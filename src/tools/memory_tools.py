"""
memory_tools.py - Memory lifecycle tools for Omni-Dev

PRIMARY:   SimpleMemory (JSON file) — always works, zero cloud dependencies.
LIFECYCLE: Cognee memory lifecycle APIs (remember / recall / forget) — the
           primary Cognee memory path; best effort, silent-fail so the agent
           NEVER loses memory due to cloud API failures.

This module exposes the full remember/recall/forget lifecycle to the agent as
tools, leaning on Cognee v1.2.2's lifecycle APIs while keeping SimpleMemory as a
guaranteed offline fallback.
"""
import asyncio
from typing import Any, Dict, List

from src.simple_memory import (
    remember as sm_remember,
    recall as sm_recall,
    clear_all as sm_clear_all,
)
from .base_tool import BaseTool

# Pin Cognee's durable storage roots (project .cognee_data) before any cognee
# operation. Side-effecting import — safe/no-op if cognee is unavailable.
try:
    from src import cognee_paths  # noqa: F401
except Exception:
    pass


# Attributes probed (in order) to pull readable text out of a Cognee
# RecallResponse entry (discriminated union: QA / GraphContext / SessionContext /
# Graph / AgentTrace). Falls back to str(entry).
_RECALL_TEXT_ATTRS = (
    "answer", "text", "content", "context", "summary",
    "payload", "result", "graph_context", "value",
)


def _extract_recall_text(entry: Any) -> str:
    """Defensively extract readable text from a Cognee recall/search entry.

    Cognee 1.2.2 returns results in several shapes:
      * dict with a ``search_result`` list (CHUNKS/INSIGHTS search),
      * dict with ``answer``/``text``/``content`` keys,
      * discriminated-union objects (QA / GraphContext / ...).
    Handle all of them, falling back to ``str(entry)`` only as a last resort.
    """
    # dict-shaped results (the common case in cognee 1.2.2 search()).
    if isinstance(entry, dict):
        sr = entry.get("search_result")
        if isinstance(sr, (list, tuple)):
            parts = [str(s).strip() for s in sr if str(s).strip()]
            if parts:
                return "\n\n".join(parts)
        if isinstance(sr, str) and sr.strip():
            return sr.strip()
        for key in ("answer", "text", "content", "context", "summary", "value", "result"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (list, tuple)):
                parts = [str(s).strip() for s in v if str(s).strip()]
                if parts:
                    return "\n\n".join(parts)
        return ""  # don't return the raw dict repr (noisy)

    for attr in _RECALL_TEXT_ATTRS:
        try:
            val = getattr(entry, attr, None)
        except Exception:
            val = None
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    try:
        text = str(entry)
    except Exception:
        return ""
    return text.strip()


def _describe_remember_result(result: Any) -> str:
    """Turn a Cognee ``RememberResult`` into a short, real status string.

    We report something the graph layer actually returned (status / data id /
    dataset) instead of a hardcoded "success", so the user can trust that the
    Cognee engine — not just the offline mirror — accepted the write.
    """
    if result is None:
        return "accepted"
    # Object-shaped result (RememberResult): probe common fields.
    for attr in ("status", "state"):
        val = getattr(result, attr, None)
        if val:
            return str(val)
    for attr in ("data_id", "id", "dataset", "dataset_id"):
        val = getattr(result, attr, None)
        if val:
            return f"{attr}={val}"
    # dict-shaped result.
    if isinstance(result, dict):
        for key in ("status", "state", "dataset", "data_id", "id"):
            if result.get(key):
                return f"{key}={result[key]}"
    return "accepted"


def _cognee_background_ingest(fact: str) -> None:
    """Fallback: run cognee add + cognify in the background (silent-fail).

    Used only if the lifecycle ``cognee.remember`` call fails. cognify is slow
    (minutes), so it must never block the agent — scheduled as a fire-and-forget
    asyncio task when a loop is running, otherwise in a throwaway thread.
    """
    async def _ingest():
        try:
            import cognee
            try:
                from src import cognee_paths
                cognee_paths.configure_cognee_storage()
            except Exception:
                pass
            await cognee.add(fact, dataset_name="user_memory")
            await cognee.cognify()
        except Exception:
            pass

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_ingest())
        return
    except RuntimeError:
        pass

    import threading

    def _runner():
        try:
            asyncio.run(_ingest())
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()


class MemoryWriteTool(BaseTool):
    """
    Store a fact or context into long-term memory.
    SimpleMemory (JSON file) primary + Cognee remember() lifecycle store.
    """

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact, user preference, or project context into long-term memory. "
            "Writes into the Cognee memory graph (the durable, self-improving engine) "
            "via the remember lifecycle API, and mirrors to a local offline store for resilience. "
            "ALWAYS call this after building something, completing a task, or learning a user preference. "
            "The information can be retrieved later using 'recall'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "fact": {
                "type": "string",
                "description": "The fact, preference, or context to remember permanently.",
            },
        }

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, fact: str, **kwargs) -> str:
        """Store the fact (SimpleMemory primary + Cognee remember lifecycle)."""
        if not fact or not fact.strip():
            return "Error: fact parameter is required."

        fact = fact.strip()

        # PRIMARY MEMORY ENGINE: Cognee remember() lifecycle. This is the source
        # of truth — it writes the fact into the durable knowledge graph (vector +
        # relational + graph stores) and self-enriches via self_improvement.
        # run_in_background=True returns a RememberResult quickly; we capture it so
        # we can report the graph's REAL status rather than assuming success.
        cognee_status = ""
        try:
            import cognee
            try:
                from src import cognee_paths
                cognee_paths.configure_cognee_storage()
            except Exception:
                pass
            result = await cognee.remember(
                fact,
                dataset_name="user_memory",
                run_in_background=True,
                self_improvement=True,
            )
            cognee_status = _describe_remember_result(result)
        except Exception:
            # Engine unreachable: fall back to the legacy background add+cognify so
            # the graph still catches up when the backend recovers.
            try:
                _cognee_background_ingest(fact)
                cognee_status = "queued (background add+cognify)"
            except Exception:
                cognee_status = ""

        # RESILIENCE LAYER: SimpleMemory (JSON) — synchronous, offline mirror so a
        # transient cloud failure can never lose the fact. NOT the source of truth.
        ok = sm_remember(fact)

        if cognee_status:
            return (
                f"✅ Stored in the Cognee memory graph ({cognee_status})"
                + (" · mirrored to offline store." if ok else ".")
            )
        if ok:
            return (
                "✅ Saved to the offline memory store. "
                "Cognee graph is unreachable right now — it will sync on the next reachable write."
            )
        return "❌ Error: could not save to memory."


class MemoryReadTool(BaseTool):
    """
    Search long-term memory for past context.
    SimpleMemory (JSON file) primary + Cognee recall() lifecycle retrieval.
    """

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory for past context, facts, or user preferences. "
            "Retrieves from the local memory store AND the Cognee memory graph via "
            "the recall lifecycle API. "
            "Use this at the START of every session to load past work context. "
            "Use this when the user references past work or when you need project history."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "What you want to search for in long-term memory.",
            },
        }

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, query: str, **kwargs) -> str:
        """Search memory: SimpleMemory primary + Cognee recall lifecycle."""
        if not query or not query.strip():
            return "Error: query parameter is required."

        query = query.strip()
        results: List[str] = []
        graph_results: List[str] = []

        # PRIMARY: SimpleMemory — always works (offline keyword fallback).
        sm_results = sm_recall(query, top_k=8)
        results.extend(sm_results)

        # LIFECYCLE: Cognee retrieval. We run TWO complementary queries and merge:
        #   1) CHUNKS search  — fast, no LLM, returns the ACTUAL stored text
        #      (e.g. the code of an indexed-then-deleted file). Most reliable for
        #      "what's in file X" questions.
        #   2) recall()       — GRAPH_COMPLETION, an LLM-synthesized answer over
        #      the graph. Nice prose but can hedge, so it's secondary.
        # The whole block is bounded by a soft timeout so a slow LLM can never
        # block the agent — the SimpleMemory results above are returned regardless.
        async def _cognee_recall() -> List[str]:
            out: List[str] = []
            import cognee

            # Pin durable storage roots FIRST so recall reads the same project
            # .cognee_data graph that writes target (never site-packages).
            try:
                from src import cognee_paths
                cognee_paths.configure_cognee_storage()
            except Exception:
                pass

            # 1) Raw factual retrieval via CHUNKS / INSIGHTS (no LLM = fast + exact).
            #    If this yields anything, return it immediately — it contains the
            #    actual stored text (e.g. an indexed-then-deleted file's code) and
            #    we must not risk losing it to a slow GRAPH_COMPLETION call below.
            try:
                from cognee.modules.search.types.SearchType import SearchType

                for stype in (
                    getattr(SearchType, "CHUNKS", None),
                    getattr(SearchType, "INSIGHTS", None),
                ):
                    if stype is None:
                        continue
                    try:
                        res = await asyncio.wait_for(
                            cognee.search(query_text=query, query_type=stype, top_k=8),
                            timeout=25,
                        )
                    except Exception:
                        continue
                    for entry in (res or []):
                        text = _extract_recall_text(entry)
                        if text and text not in out:
                            out.append(text)
                    if out:
                        return out  # fast, factual hit — done.
            except Exception:
                pass

            # 2) GRAPH_COMPLETION synthesized answer (only if CHUNKS found nothing).
            try:
                cog_results = await asyncio.wait_for(
                    cognee.recall(query_text=query, top_k=8), timeout=30
                )
                for entry in (cog_results or []):
                    text = _extract_recall_text(entry)
                    if text and text not in out:
                        out.append(text)
            except Exception:
                pass

            return out

        try:
            cog_texts = await asyncio.wait_for(_cognee_recall(), timeout=60)
            for text in cog_texts:
                if text and text not in results:
                    graph_results.append(text)
                    results.append(text)
        except Exception:
            pass

        if results:
            # Surface Cognee graph memory first and label it, so graph-based
            # retrieval is clearly driving recall (with the offline store as a
            # transparent fallback).
            sections: List[str] = []
            if graph_results:
                sections.append(
                    "🧠 **Cognee Graph Memory:**\n\n"
                    + "\n\n---\n\n".join(graph_results[:8])
                )
            local_only = [r for r in sm_results if r not in graph_results]
            if local_only:
                sections.append(
                    "🗂️ **Local Memory Store:**\n\n"
                    + "\n\n---\n\n".join(local_only[:8])
                )
            return "📚 **Memory Retrieved:**\n\n" + "\n\n".join(sections)
        return "No relevant memories found. (Memory is fresh — start working and memories will be stored automatically.)"


class ForgetMemoryTool(BaseTool):
    """
    Remove memories via the Cognee forget() lifecycle API.
    Scope can target the memory layer, everything, or a named dataset.
    """

    @property
    def name(self) -> str:
        return "forget"

    @property
    def description(self) -> str:
        return (
            "Remove stored memories via the Cognee forget lifecycle API. "
            "Use 'memory' to clear the memory layer only (default), 'all' to wipe "
            "everything, or pass a dataset name to forget a specific dataset. "
            "Use this when the user asks to forget, reset, or clear remembered context."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "scope": {
                "type": "string",
                "description": (
                    "What to forget: 'memory' (memory layer only, default), "
                    "'all' (everything), or a dataset name."
                ),
            },
            "dataset": {
                "type": "string",
                "description": "Optional dataset name to forget (overrides scope when set).",
            },
        }

    @property
    def required_params(self):
        return []

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, scope: str = "memory", dataset: str = "", **kwargs) -> str:
        """Map scope/dataset onto cognee.forget() and confirm what was removed."""
        scope = (scope or "memory").strip()
        dataset = (dataset or "").strip()

        # An explicit dataset arg wins; otherwise a non memory/all scope IS a
        # dataset name.
        target_dataset = dataset
        if not target_dataset and scope not in ("memory", "all"):
            target_dataset = scope

        try:
            import cognee
            try:
                from src import cognee_paths
                cognee_paths.configure_cognee_storage()
            except Exception:
                pass

            if target_dataset:
                result = await cognee.forget(dataset=target_dataset)
                what = f"dataset '{target_dataset}'"
            elif scope == "all":
                result = await cognee.forget(everything=True)
                # Also clear the SimpleMemory store so the offline layer matches.
                try:
                    sm_clear_all()
                except Exception:
                    pass
                what = "all memories"
            else:
                # memory_only requires a dataset in Cognee v1.2.2; target the
                # CLI's user_memory dataset's memory layer.
                result = await cognee.forget(memory_only=True, dataset="user_memory")
                what = "the memory layer"

            detail = ""
            if isinstance(result, dict):
                # Summarize counts/status from the returned dict defensively.
                bits = []
                for k, v in result.items():
                    if isinstance(v, (int, str, bool)) and str(v).strip():
                        bits.append(f"{k}={v}")
                if bits:
                    detail = " (" + ", ".join(bits[:5]) + ")"

            return f"🧹 Forgot {what}{detail}."
        except Exception as e:
            # Even if Cognee fails, honor 'all' by clearing the offline store.
            if scope == "all":
                try:
                    sm_clear_all()
                    return "🧹 Forgot all memories (local store cleared; Cognee unavailable)."
                except Exception:
                    pass
            return f"⚠️ Could not complete forget for {locals().get('what', scope)}: {e}"


class ImproveMemoryTool(BaseTool):
    """
    Run the Cognee improve()/memify lifecycle to self-enrich the memory graph.

    This is the fourth Cognee memory lifecycle verb (remember / recall / forget /
    improve). It consolidates everything remembered so far by building a global
    context index and a truth subspace across the dataset, which sharpens future
    recall and de-duplicates/links related facts.
    """

    @property
    def name(self) -> str:
        return "improve_memory"

    @property
    def description(self) -> str:
        return (
            "Consolidate and enrich long-term memory via Cognee's improve/memify "
            "lifecycle. It builds a global context index and a truth subspace across "
            "everything remembered so far, strengthening future recall and linking "
            "related facts. Call this after a burst of 'remember' calls, at the end of "
            "a work session, or when the user asks to consolidate/organize/clean up memory."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "dataset": {
                "type": "string",
                "description": "Dataset to improve (default 'user_memory').",
            },
        }

    @property
    def required_params(self):
        return []

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, dataset: str = "user_memory", **kwargs) -> str:
        """Run cognee.improve() in the background (global index + truth subspace)."""
        dataset = (dataset or "user_memory").strip() or "user_memory"
        try:
            import cognee
            try:
                from src import cognee_paths
                cognee_paths.configure_cognee_storage()
            except Exception:
                pass
            # improve() is the lifecycle verb; memify is its underlying primitive.
            # build_global_context_index + build_truth_subspace are what make recall
            # sharper over time. run_in_background so the agent never blocks on it.
            await cognee.improve(
                dataset=dataset,
                run_in_background=True,
                build_global_context_index=True,
                build_truth_subspace=True,
            )
            return (
                f"🧠 Memory consolidation scheduled for '{dataset}' "
                "(global context index + truth subspace). Recall will sharpen as it completes."
            )
        except Exception as e:
            # Fall back to a plain memify pass if improve() is unavailable.
            try:
                import cognee
                await cognee.memify(dataset=dataset, run_in_background=True)
                return f"🧠 Memory enrichment (memify) scheduled for '{dataset}'."
            except Exception:
                return f"⚠️ Could not run memory improvement for '{dataset}': {e}"
