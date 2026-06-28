"""
clear.py - Python conversion of scratch_repo/src/commands/clear.ts

The /clear command resets the current conversation history, freeing up context
while preserving the system instruction (and long-term Cognee memory). It mirrors
``agent.compact_session`` — the difference from /compact is that /clear discards
the conversation outright instead of summarizing it first (Req 16.6).
"""
from __future__ import annotations

from typing import Any, Dict, List


def clear_command(agent: Any) -> List[Dict[str, Any]]:
    """
    Reset the agent's conversation history to just the system message (Req 16.6).

    Args:
        agent: The active OmniDevAgent (or any object exposing ``messages`` and,
            optionally, ``compact_session``).
    Returns:
        The new ``messages`` list (system message only), for the interface to use.
    """
    # Prefer the agent's own reset so any associated state (context cache, etc.)
    # is cleared consistently.
    compact = getattr(agent, "compact_session", None)
    if callable(compact):
        compact()
        return getattr(agent, "messages", [])

    # Fallback: reset messages to just the system message.
    messages = getattr(agent, "messages", None)
    if isinstance(messages, list) and messages and messages[0].get("role") == "system":
        system_msg = messages[0]
    else:
        system_instruction = getattr(agent, "system_instruction", "")
        system_msg = {"role": "system", "content": system_instruction}

    agent.messages = [system_msg]
    return agent.messages
