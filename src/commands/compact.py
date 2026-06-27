"""
compact.py - Python conversion of scratch_repo/src/commands/compact.ts

AI-powered session compaction: summarizes the conversation before clearing.
The original implementation just reset messages. This version uses LLM to create
a summary, then clears the chat (preserving Cognee long-term memory).
"""
import os
from typing import List, Dict, Any

import litellm


async def compact_command(messages: List[Dict[str, Any]], model: str) -> tuple[str, List[Dict[str, Any]]]:
    """
    AI-powered compact: summarize conversation then clear it.
    Mirrors compact.ts from scratch_repo — uses LLM to generate summary.
    
    Returns:
        (summary_text, new_messages_list)
    """
    if not messages or len(messages) <= 1:
        return "Nothing to compact (conversation is empty).", messages

    try:
        # Build summary request (mirrors compact.ts logic)
        summary_prompt = (
            "Provide a detailed but concise summary of our conversation above. "
            "Focus on:\n"
            "1. What we did and accomplished\n"
            "2. Which files we worked on and what changes were made\n"
            "3. What we are currently doing\n"
            "4. What we need to do next\n"
            "5. Any important decisions, errors, or context\n\n"
            "This summary will be used to continue the conversation efficiently."
        )

        summary_messages = [
            *messages,
            {"role": "user", "content": summary_prompt},
        ]

        response = litellm.completion(
            model=model,
            messages=summary_messages,
            max_tokens=2048,
        )
        summary = response.choices[0].message.content or ""

        if not summary:
            return "Failed to generate summary. Session not compacted.", messages

        # Store summary to Cognee memory so it persists permanently
        try:
            import cognee
            await cognee.add(
                f"Compacted Session Summary:\n{summary}",
                dataset_name="session_summaries",
            )
            await cognee.cognify()
        except Exception:
            pass

        # New messages: just the system message + a user message explaining the compact
        from src.agent.core import OmniDevAgent
        system_msg = messages[0] if messages[0].get("role") == "system" else None
        new_messages = []
        if system_msg:
            new_messages.append(system_msg)
        new_messages.append({
            "role": "user",
            "content": f"[Session compacted. Here is a summary of what we did:\n\n{summary}]"
        })
        new_messages.append({
            "role": "assistant",
            "content": "Understood. I've reviewed the session summary and am ready to continue where we left off."
        })

        return summary, new_messages

    except Exception as e:
        return f"Error during compact: {e}", messages
