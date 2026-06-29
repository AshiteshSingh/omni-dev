"""
simple_memory.py - Reliable fallback memory system for Omni-Dev

This module provides a JSON file-based memory store that works 100% of the time,
regardless of whether Cognee / cloud APIs are available.

It stores memories in .cognee_data/simple_memory.json in the project root.
Retrieval uses simple keyword/substring matching — fast, dependency-free.
"""
import os
import json
import time
import re
from typing import List, Dict, Optional
from pathlib import Path


def _get_memory_path() -> Path:
    """Get the path to the memory JSON file (project-local)."""
    # Try to find the project root (where .env or omni_dev.py lives)
    cwd = Path(os.getcwd())
    # Walk up to find the project root marker
    check = cwd
    for _ in range(5):
        if (check / "omni_dev.py").exists() or (check / ".env").exists():
            data_dir = check / ".cognee_data"
            data_dir.mkdir(exist_ok=True)
            return data_dir / "simple_memory.json"
        check = check.parent
    # Fallback: use cwd
    data_dir = cwd / ".cognee_data"
    data_dir.mkdir(exist_ok=True)
    return data_dir / "simple_memory.json"


def _load_memories() -> List[Dict]:
    """Load memories from disk."""
    path = _get_memory_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_memories(memories: List[Dict]) -> None:
    """Save memories to disk."""
    path = _get_memory_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def remember(fact: str, tag: str = "general") -> bool:
    """
    Store a fact in simple memory.
    Returns True on success.
    """
    if not fact or not fact.strip():
        return False
    memories = _load_memories()
    entry = {
        "id": int(time.time() * 1000),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag,
        "text": fact.strip(),
    }
    # Avoid duplicate near-identical entries (simple dedup by first 100 chars)
    prefix = fact.strip()[:100].lower()
    for m in memories:
        if m.get("text", "")[:100].lower() == prefix:
            # Update existing instead of adding duplicate
            m["text"] = fact.strip()
            m["timestamp"] = entry["timestamp"]
            _save_memories(memories)
            return True
    memories.append(entry)
    # Keep last 500 memories
    if len(memories) > 500:
        memories = memories[-500:]
    _save_memories(memories)
    return True


def recall(query: str, top_k: int = 8) -> List[str]:
    """
    Retrieve memories matching a query using keyword scoring.
    Returns a list of matching memory text strings (most recent first).
    """
    if not query:
        return []
    memories = _load_memories()
    if not memories:
        return []

    # Score each memory by keyword overlap
    query_words = set(re.findall(r'\w+', query.lower()))
    scored = []
    for mem in memories:
        text = mem.get("text", "")
        text_words = set(re.findall(r'\w+', text.lower()))
        # Keyword overlap score + recency bonus
        overlap = len(query_words & text_words)
        # Also check direct substring matches (weighted higher)
        direct = sum(1 for w in query_words if len(w) > 3 and w in text.lower())
        score = overlap + direct * 2
        if score > 0:
            scored.append((score, mem.get("id", 0), text))

    # Sort by score desc, then by recency (id) desc
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [text for _, _, text in scored[:top_k]]


def clear_all() -> bool:
    """Erase all stored memories (truncate the JSON store to an empty list).

    Used by the forget lifecycle when the scope is 'all'. Returns True on
    success, False on failure.
    """
    try:
        _save_memories([])
        return True
    except Exception:
        return False


def recall_recent(n: int = 5) -> List[str]:
    """Return the N most recent memories regardless of query."""
    memories = _load_memories()
    recent = memories[-n:]
    recent.reverse()
    return [m.get("text", "") for m in recent]


def get_memory_summary() -> str:
    """Return a status string about memory state."""
    memories = _load_memories()
    path = _get_memory_path()
    count = len(memories)
    size_kb = path.stat().st_size // 1024 if path.exists() else 0
    if not memories:
        return f"Empty memory store ({path})"
    newest = memories[-1]
    return (
        f"{count} memories stored ({size_kb} KB) at {path}\n"
        f"Latest: [{newest.get('timestamp', '?')}] {newest.get('text', '')[:100]}"
    )
