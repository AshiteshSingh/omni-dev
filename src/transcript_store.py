"""Conversation transcript store for Omni-Dev.

Persists Conversation_Transcripts as individual JSON files under the same
``.omni-dev`` global directory used by :mod:`src.config_store`, in a
``transcripts`` subdirectory:

    <global>/transcripts/<id>.json

This supports the ``resume`` command's save / list / restore / fork semantics,
mirroring the reference TypeScript implementation
(``scratch_repo/src/commands/resume.tsx`` + ``scratch_repo/src/history.ts``).

Behavior contract (Requirement 12):
- ``save_transcript`` writes atomically (temp file + ``os.replace``), generating an
  id when absent and maintaining ``createdAt`` / ``updatedAt`` timestamps (12.1).
- ``load_transcript`` reproduces the saved messages in their original order (12.2,
  Property 28).
- ``list_transcripts`` lists persisted transcripts, most-recent first, tolerating a
  missing directory (returns ``[]``) and skipping corrupt files (12.3).
- ``fork_transcript`` creates a NEW transcript (new id) containing the messages up to
  and including a selected index, leaving the original file unchanged (12.5,
  Property 29).

The global directory is resolved lazily (via ``USERPROFILE`` / ``HOME``) so tests can
redirect the home directory after import, mirroring ``config_store``'s ``_resolve``
approach.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

#: Preview length (characters) for the first user/assistant message in metadata.
_PREVIEW_MAX_CHARS = 80


def _resolve_global_dir() -> Path:
    """Resolve the ``.omni-dev`` global directory at call time.

    Resolving lazily (rather than caching a module-level constant) lets tests
    redirect the home directory via ``USERPROFILE`` / ``HOME`` after import. This
    mirrors :func:`src.config_store._resolve_global_file`.
    """
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".omni-dev"


def _resolve_transcript_dir() -> Path:
    """Resolve the transcripts directory: ``<global>/transcripts``."""
    return _resolve_global_dir() / "transcripts"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_id() -> str:
    """Generate a transcript id: ``<timestamp>_<short-random-hex>``.

    The timestamp prefix keeps ids roughly sortable; the random suffix avoids
    collisions when transcripts are created within the same second.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    return f"{stamp}_{secrets.token_hex(3)}"


def _atomic_write(file: Path, data: Dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``file`` atomically (temp file + ``os.replace``)."""
    file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    # Write to a temp file in the same directory so os.replace is atomic on all
    # platforms (rename across filesystems is not).
    fd, tmp_path = tempfile.mkstemp(
        prefix=".transcript.", suffix=".tmp", dir=str(file.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, str(file))
    except BaseException:
        # Clean up the temp file on any failure; do not touch the existing file.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _transcript_path(transcript_id: str) -> Path:
    """Resolve the on-disk path for a transcript id."""
    return _resolve_transcript_dir() / f"{transcript_id}.json"


def _preview_of(messages: List[Dict[str, Any]]) -> str:
    """Build a short preview from the first user/assistant message with text.

    System messages are skipped so the preview reflects the actual conversation;
    if no user/assistant text is found, any text content is used as a fallback.
    """
    def _text_preview(content: str) -> str:
        preview = content.strip().replace("\n", " ")
        if len(preview) > _PREVIEW_MAX_CHARS:
            return preview[:_PREVIEW_MAX_CHARS].rstrip() + "..."
        return preview

    fallback = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not (isinstance(content, str) and content.strip()):
            continue
        if msg.get("role") in ("user", "assistant"):
            return _text_preview(content)
        if not fallback:
            fallback = _text_preview(content)
    return fallback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_transcript(transcript: Dict[str, Any]) -> str:
    """Persist a Conversation_Transcript to disk and return its id (12.1).

    A deep copy of ``transcript`` is written so the caller's object is never
    mutated. An id is generated when absent; ``createdAt`` is set on first save and
    ``updatedAt`` is refreshed on every save. The transcripts directory is created
    as needed. The write is atomic.
    """
    data = copy.deepcopy(transcript) if isinstance(transcript, dict) else {}

    transcript_id = data.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        transcript_id = _generate_id()
        data["id"] = transcript_id

    now = time.time()
    if not isinstance(data.get("createdAt"), (int, float)):
        data["createdAt"] = now
    data["updatedAt"] = now

    if "messages" not in data or not isinstance(data.get("messages"), list):
        data["messages"] = []

    _atomic_write(_transcript_path(transcript_id), data)
    return transcript_id


def load_transcript(transcript_id: str) -> Dict[str, Any]:
    """Load and return the transcript for ``transcript_id`` (12.2, 12.4).

    Raises ``FileNotFoundError`` if no transcript with that id exists. The returned
    ``messages`` reproduce the saved messages in their original order.
    """
    path = _transcript_path(transcript_id)
    if not path.exists():
        raise FileNotFoundError(f"No transcript found with id: {transcript_id}")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_transcripts() -> List[Dict[str, Any]]:
    """Return metadata for all stored transcripts, most-recent first (12.3).

    Each entry contains ``id``, ``createdAt``, ``updatedAt``, ``projectPath``,
    ``model``, ``messageCount`` and a short ``preview``. A missing directory yields
    an empty list; corrupt or unreadable files are skipped rather than raising.
    """
    directory = _resolve_transcript_dir()
    if not directory.exists():
        return []

    metas: List[Dict[str, Any]] = []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return []

    for path in entries:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # Skip corrupt / unreadable files.
            continue
        if not isinstance(data, dict):
            continue

        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []

        metas.append(
            {
                "id": data.get("id") or path.stem,
                "createdAt": data.get("createdAt"),
                "updatedAt": data.get("updatedAt"),
                "projectPath": data.get("projectPath"),
                "model": data.get("model"),
                "messageCount": len(messages),
                "preview": _preview_of(messages),
            }
        )

    # Most-recent first: sort by updatedAt (fallback createdAt), descending.
    def _sort_key(meta: Dict[str, Any]) -> float:
        for field in ("updatedAt", "createdAt"):
            value = meta.get(field)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    metas.sort(key=_sort_key, reverse=True)
    return metas


def fork_transcript(transcript_id: str, upto_index: int) -> Dict[str, Any]:
    """Fork ``transcript_id`` at ``upto_index`` into a NEW transcript (12.5).

    Creates a new transcript (new id) containing exactly ``messages[:upto_index+1]``
    while leaving the original transcript file unchanged on disk. The new transcript
    is saved and returned (Property 29).
    """
    original = load_transcript(transcript_id)

    messages = original.get("messages")
    if not isinstance(messages, list):
        messages = []

    # Clamp the index into the valid range so we never raise on a stale index.
    end = upto_index + 1
    if end < 0:
        end = 0
    prefix = copy.deepcopy(messages[:end])

    forked: Dict[str, Any] = {
        "projectPath": original.get("projectPath"),
        "model": original.get("model"),
        "messages": prefix,
    }

    new_id = save_transcript(forked)
    return load_transcript(new_id)
