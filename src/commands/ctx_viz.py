"""
ctx_viz.py - Python conversion of scratch_repo/src/commands/ctx_viz.ts

Visualizes the current conversation context: messages, token counts,
tool calls, and the injected system context.
"""
import json
import os
from typing import List, Dict, Any


def count_tokens_approx(text: str) -> int:
    """Rough token count estimate (~4 chars per token)."""
    return len(text) // 4


async def ctx_viz_command(messages: List[Dict[str, Any]], context: Dict[str, str] = None) -> str:
    """
    Visualize the current context state.
    Mirrors ctx_viz.ts from scratch_repo.
    """
    lines = ["## 🔭 Context Visualization\n"]

    # Message summary
    total_messages = len(messages)
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    total_tokens_approx = count_tokens_approx(str(messages))

    lines.append(f"### 📨 Messages ({total_messages} total)")
    lines.append(f"  Estimated tokens: ~{total_tokens_approx:,}")
    lines.append(f"  Total characters: {total_chars:,}\n")

    # Per-message breakdown
    for i, msg in enumerate(messages[:20]):  # Show first 20
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        if isinstance(content, list):
            content_str = json.dumps(content)
        else:
            content_str = str(content)

        tokens = count_tokens_approx(content_str)
        preview = content_str[:80].replace("\n", " ") + ("..." if len(content_str) > 80 else "")
        tool_calls = msg.get("tool_calls", [])
        tc_str = f" [{len(tool_calls)} tool calls]" if tool_calls else ""
        lines.append(f"  [{i}] {role}{tc_str} (~{tokens} tokens): {preview}")

    if total_messages > 20:
        lines.append(f"  ... and {total_messages - 20} more messages")

    # Injected context
    if context:
        lines.append(f"\n### 🌍 Injected Context ({len(context)} items)")
        for key, value in context.items():
            tokens = count_tokens_approx(value)
            lines.append(f"  📎 {key}: ~{tokens} tokens ({len(value)} chars)")

    # Environment
    model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro")
    lines.append(f"\n### ⚙️ Environment")
    lines.append(f"  Model: {model}")
    lines.append(f"  CWD: {os.getcwd()}")

    return "\n".join(lines)
