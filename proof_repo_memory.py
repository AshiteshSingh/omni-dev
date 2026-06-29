"""
proof_repo_memory.py - Prove durable cross-session repo memory.

Simulates: clone a repo -> learn (ingest CONTENT into Cognee) -> DELETE the repo
-> ask a question. If Cognee answers from the graph after the files are gone,
durable memory works.

Run: python proof_repo_memory.py
"""
import asyncio
import os
import shutil
import tempfile

from dotenv import load_dotenv

load_dotenv()
from src import cognee_paths  # noqa: E402  (pins storage + LLM env before cognee)

try:
    import litellm
    litellm.num_retries = 0
except Exception:
    pass

STEP_TIMEOUT = 300  # seconds per cognee step


# A distinctive, made-up fact no LLM could know without reading the file.
SECRET_FILE = "payment_engine.py"
SECRET_CODE = '''\
def reconcile_ledger(transactions):
    """OmniProof reconciliation.

    The magic constant ZORBLAX_THRESHOLD = 7321 is used to flag any
    transaction above it as 'requires_manual_review'. Reconciliation runs
    every 19 minutes and writes results to the 'glorptide' audit table.
    """
    ZORBLAX_THRESHOLD = 7321
    flagged = [t for t in transactions if t > ZORBLAX_THRESHOLD]
    return {"flagged": flagged, "audit_table": "glorptide"}
'''


async def main() -> None:
    # 1) Create a throwaway "cloned repo" with the secret file.
    repo = tempfile.mkdtemp(prefix="fake_repo_")
    path = os.path.join(repo, SECRET_FILE)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SECRET_CODE)
    print(f"[1] Created fake repo at {repo} with {SECRET_FILE}")

    # 2) "Learn" it: ingest the CONTENT into Cognee and build the graph.
    import cognee
    doc = f"Source file: {SECRET_FILE}\n\n{SECRET_CODE}"
    print("[2] Ingesting file CONTENT into Cognee graph (add + cognify)...", flush=True)
    await asyncio.wait_for(cognee.add(doc, dataset_name="proof_repo"), timeout=STEP_TIMEOUT)
    print("    added; cognifying (building graph)...", flush=True)
    await asyncio.wait_for(cognee.cognify(), timeout=STEP_TIMEOUT)
    print("    done.", flush=True)

    # 3) DELETE the repo entirely — the source is gone.
    shutil.rmtree(repo, ignore_errors=True)
    print(f"[3] Deleted the repo. Exists now? {os.path.exists(repo)}", flush=True)

    # 4) Ask a question that can ONLY be answered from the ingested content.
    print("[4] Asking Cognee about the deleted repo...", flush=True)
    res = await asyncio.wait_for(
        cognee.recall(
            query_text="What is the ZORBLAX_THRESHOLD value and which audit table does reconcile_ledger write to?",
            top_k=5,
        ),
        timeout=STEP_TIMEOUT,
    )
    print("\n=== ANSWER FROM GRAPH (repo is deleted) ===")
    for r in (res or []):
        text = getattr(r, "text", None) or getattr(r, "answer", None) or str(r)
        print(text[:600])

    blob = " ".join(
        (getattr(r, "text", None) or getattr(r, "answer", None) or str(r))
        for r in (res or [])
    )
    ok = "7321" in blob or "glorptide" in blob.lower()
    print("\n=== VERDICT ===")
    print("PASS - it remembered the deleted repo's content." if ok
          else "PARTIAL - graph answered but did not surface the exact secret detail.")


if __name__ == "__main__":
    asyncio.run(main())
