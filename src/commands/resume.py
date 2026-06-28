"""
resume.py - Python conversion of scratch_repo/src/commands/resume.tsx

The /resume command lets the user browse previously saved conversation
transcripts and either resume one or fork it at a chosen message index.

These are thin, import-safe wrappers over :mod:`src.transcript_store`; they return
plain data the interface layer can render and act on (Req 16). Lookups that fail
(unknown id) return ``None`` rather than raising, so the dispatcher can show a
friendly message.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src import transcript_store


def list_resumable() -> List[Dict[str, Any]]:
    """
    Return metadata for all resumable transcripts, most-recent first.

    Tolerates a missing transcript directory (returns ``[]``).
    """
    return transcript_store.list_transcripts()


def resume_command(transcript_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a transcript by id so the session can be resumed.

    Args:
        transcript_id: The id of the transcript to load.
    Returns:
        The transcript dict, or ``None`` if no transcript with that id exists.
    """
    try:
        return transcript_store.load_transcript(transcript_id)
    except FileNotFoundError:
        return None


def fork_command(transcript_id: str, upto_index: int) -> Optional[Dict[str, Any]]:
    """
    Fork a transcript at ``upto_index`` into a new transcript.

    Args:
        transcript_id: The id of the transcript to fork.
        upto_index: Inclusive index of the last message to keep in the fork.
    Returns:
        The newly created (forked) transcript dict, or ``None`` if the source
        transcript does not exist.
    """
    try:
        return transcript_store.fork_transcript(transcript_id, upto_index)
    except FileNotFoundError:
        return None
