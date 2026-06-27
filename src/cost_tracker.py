"""
cost_tracker.py - Python conversion of scratch_repo/src/cost-tracker.ts

Tracks cumulative API cost and token usage across the session.
"""
import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class CallRecord:
    """Record of a single API call."""
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    timestamp: float = field(default_factory=time.time)


class CostTracker:
    """
    Tracks API costs and token usage.
    Mirrors CostTracker from scratch_repo/src/cost-tracker.ts.
    """

    # Cost per million tokens (USD)
    COSTS = {
        # Anthropic
        "claude-3-5-sonnet": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3},
        "claude-3-5-haiku": {"input": 0.8, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
        "claude-3-opus": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.5},
        # Google Gemini (approximate)
        "gemini-1.5-pro": {"input": 3.5, "output": 10.5},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.3},
        "gemini-2.0-flash": {"input": 0.1, "output": 0.4},
        # OpenAI
        "gpt-4o": {"input": 5.0, "output": 15.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
        # Default fallback
        "default": {"input": 1.0, "output": 3.0},
    }

    def __init__(self):
        self.total_cost_usd: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_duration_ms: int = 0
        self.call_history: List[CallRecord] = []

    def _get_cost_rates(self, model: str) -> dict:
        """Get cost rates for a model, using fuzzy matching."""
        model_lower = model.lower()
        for key in self.COSTS:
            if key in model_lower:
                return self.COSTS[key]
        return self.COSTS["default"]

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> float:
        """Estimate cost in USD for a single API call."""
        rates = self._get_cost_rates(model)
        cost = (
            (input_tokens / 1_000_000) * rates["input"]
            + (output_tokens / 1_000_000) * rates["output"]
            + (cache_write_tokens / 1_000_000) * rates.get("cache_write", 0)
            + (cache_read_tokens / 1_000_000) * rates.get("cache_read", 0)
        )
        return cost

    def add_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int = 0,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> float:
        """Record an API call and return the cost."""
        cost = self.estimate_cost(model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens)
        self.total_cost_usd += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_duration_ms += duration_ms
        self.call_history.append(
            CallRecord(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                duration_ms=duration_ms,
            )
        )
        return cost

    def get_summary(self) -> str:
        """Get a formatted summary of session costs."""
        total_tokens = self.total_input_tokens + self.total_output_tokens
        duration_s = self.total_duration_ms / 1000
        return (
            f"💰 Session Cost: ${self.total_cost_usd:.4f} USD\n"
            f"📊 Total Tokens: {total_tokens:,} "
            f"(↑{self.total_input_tokens:,} input / ↓{self.total_output_tokens:,} output)\n"
            f"⏱️  Total Time: {duration_s:.1f}s across {len(self.call_history)} API calls"
        )

    def reset(self):
        """Reset cost tracking (e.g., on /compact)."""
        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_duration_ms = 0
        self.call_history.clear()

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


# Global singleton
_tracker = CostTracker()


def get_tracker() -> CostTracker:
    return _tracker


def add_to_total_cost(model: str, input_tokens: int, output_tokens: int, duration_ms: int = 0) -> float:
    """Convenience function to record cost. Mirrors addToTotalCost from scratch_repo."""
    return _tracker.add_call(model, input_tokens, output_tokens, duration_ms)
