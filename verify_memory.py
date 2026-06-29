"""
verify_memory.py - End-to-end Cognee memory diagnostic for Omni-Dev.

Run:  python verify_memory.py

Unlike the agent tools (which silent-fail by design), this script SURFACES every
error so you can see exactly what the Cognee graph layer is doing. It walks the
full lifecycle:

  1. Pin durable storage roots and print them.
  2. Print the LLM/embedding config Cognee will actually use.
  3. SimpleMemory round-trip (offline fallback) — must always pass.
  4. Cognee remember()  (lifecycle write).
  5. Cognee cognify()   (graph build).
  6. Cognee recall()    (graph read) + legacy search() fallback.
  7. Inspect on-disk store sizes.
  8. Print a PASS/FAIL summary table.
"""
import asyncio
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Fail fast instead of retrying dead credentials for minutes.
try:
    import litellm
    litellm.num_retries = 0
    litellm.request_timeout = 120
except Exception:
    pass

# Hard ceiling (seconds) on each Cognee network step so the script can never hang.
STEP_TIMEOUT = 240

# Pin storage BEFORE importing cognee.
from src import cognee_paths  # noqa: E402

RESULTS = {}


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS[name] = (ok, detail)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def show_exc(name: str) -> None:
    detail = traceback.format_exc().strip().splitlines()[-1]
    record(name, False, detail)
    print("    full traceback:")
    print("    " + "\n    ".join(traceback.format_exc().strip().splitlines()))


async def main() -> None:
    section("1. STORAGE ROOTS (should all be under project .cognee_data)")
    sys_root = cognee_paths.configure_cognee_storage()
    print(f"  configured system root: {sys_root}")
    try:
        import cognee
        from cognee.base_config import get_base_config
        print(f"  cognee version: {getattr(cognee, '__version__', '?')}")
        print(f"  data_root_directory: {get_base_config().data_root_directory}")
        try:
            from cognee.infrastructure.databases.relational.config import get_relational_config
            from cognee.infrastructure.databases.vector.config import get_vectordb_config
            print(f"  relational db: {get_relational_config().db_path}")
            print(f"  vector db:     {get_vectordb_config().vector_db_url}")
        except Exception:
            print("  (could not read relational/vector config paths)")
        in_project = ".cognee_data" in str(get_base_config().data_root_directory)
        record("storage_pinned_to_project", in_project,
               "data root is under .cognee_data" if in_project else "LEAKING to site-packages")
    except Exception:
        show_exc("storage_pinned_to_project")

    section("2. COGNEE LLM / EMBEDDING CONFIG (what the graph layer will use)")
    # These are the env vars Cognee reads. If unset, Cognee defaults to OpenAI
    # and will fail without OPENAI_API_KEY.
    cfg_keys = [
        "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY",
        "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "OPENAI_API_KEY",
    ]
    for k in cfg_keys:
        print(f"  {k:32} = {'<set>' if os.environ.get(k) else '<NOT SET>'}")
    try:
        from cognee.infrastructure.llm.config import get_llm_config
        from cognee.infrastructure.databases.vector.embeddings.config import get_embedding_config
        lc = get_llm_config()
        ec = get_embedding_config()
        print(f"\n  cognee LLM provider/model:       {lc.llm_provider} / {lc.llm_model}")
        print(f"  cognee embedding provider/model: {ec.embedding_provider} / {ec.embedding_model}")
        has_llm_key = bool(getattr(lc, "llm_api_key", None))
        record("cognee_llm_configured", has_llm_key,
               "LLM api key present" if has_llm_key else "no LLM api key — cognify/recall will FAIL")
    except Exception:
        show_exc("cognee_llm_configured")

    section("3. SIMPLEMEMORY ROUND-TRIP (offline fallback — must always pass)")
    try:
        from src.simple_memory import remember as sm_remember, recall as sm_recall
        token = "OMNI_VERIFY_TOKEN_42 the project uses pytest and hypothesis"
        sm_remember(token, tag="verify")
        hits = sm_recall("pytest hypothesis verify", top_k=5)
        found = any("OMNI_VERIFY_TOKEN_42" in h for h in hits)
        record("simplememory_roundtrip", found,
               f"{len(hits)} hit(s), token found={found}")
    except Exception:
        show_exc("simplememory_roundtrip")

    section("4. COGNEE remember()  (lifecycle write)")
    fact = "Omni-Dev verify: the memory layer is Cognee 1.2.2 with LanceDB vectors."
    try:
        import cognee
        await asyncio.wait_for(
            cognee.remember(
                fact,
                dataset_name="verify_memory",
                run_in_background=False,  # block so we can see real errors
                self_improvement=True,
            ),
            timeout=STEP_TIMEOUT,
        )
        record("cognee_remember", True, "remember() returned without error")
    except Exception:
        show_exc("cognee_remember")
        # Legacy fallback path used by the tools:
        section("4b. FALLBACK cognee.add() + cognify()")
        try:
            import cognee
            await asyncio.wait_for(cognee.add(fact, dataset_name="verify_memory"), timeout=STEP_TIMEOUT)
            await asyncio.wait_for(cognee.cognify(), timeout=STEP_TIMEOUT)
            record("cognee_add_cognify", True, "add+cognify returned without error")
        except Exception:
            show_exc("cognee_add_cognify")

    section("5. COGNEE recall()  (graph read)")
    try:
        import cognee
        res = await asyncio.wait_for(
            cognee.recall(query_text="What is the memory layer?", top_k=5),
            timeout=STEP_TIMEOUT,
        )
        n = len(res) if res else 0
        record("cognee_recall", n > 0, f"recall() returned {n} result(s)")
        for i, r in enumerate((res or [])[:3]):
            print(f"    result[{i}]: {str(r)[:160]}")
    except Exception:
        show_exc("cognee_recall")
        section("5b. FALLBACK cognee.search()")
        try:
            import cognee
            from cognee.modules.search.types.SearchType import SearchType
            res = await asyncio.wait_for(
                cognee.search(
                    query_text="memory layer",
                    query_type=getattr(SearchType, "CHUNKS", None),
                ),
                timeout=STEP_TIMEOUT,
            )
            n = len(res) if res else 0
            record("cognee_search", n > 0, f"search() returned {n} result(s)")
            for i, r in enumerate((res or [])[:3]):
                print(f"    result[{i}]: {str(r)[:160]}")
        except Exception:
            show_exc("cognee_search")

    section("6. ON-DISK STORE SIZES")
    data_dir = Path(cognee_paths.COGNEE_DATA_DIR)
    for label, p in [
        ("simple_memory.json", data_dir / "simple_memory.json"),
        ("knowledge_graph.json", data_dir / "knowledge_graph.json"),
        ("system/databases", data_dir / "system" / "databases"),
    ]:
        if p.exists():
            if p.is_dir():
                total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                files = [f.name for f in p.iterdir()]
                print(f"  {label}: {total // 1024} KB across {len(files)} entries -> {files}")
            else:
                print(f"  {label}: {p.stat().st_size // 1024} KB")
        else:
            print(f"  {label}: <missing>")

    section("SUMMARY")
    total = len(RESULTS)
    passed = sum(1 for ok, _ in RESULTS.values() if ok)
    for name, (ok, detail) in RESULTS.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"\n  {passed}/{total} checks passed.")
    if passed < total:
        print("  -> See FAILs above. If only Cognee graph checks fail, your")
        print("     offline memory works but the GRAPH layer needs LLM/embedding config.")


if __name__ == "__main__":
    asyncio.run(main())
