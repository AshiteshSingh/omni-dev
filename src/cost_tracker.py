"""
cost_tracker.py - Python conversion of scratch_repo/src/cost-tracker.ts

Tracks cumulative API cost and token usage across the session.
"""
import time
from dataclasses import dataclass, field
from typing import List, Optional

# Fallback thresholds used when the global config cannot be read. These mirror
# the Config_Defaults in src/config_store.py (costThreshold / tokenWarningThreshold).
DEFAULT_COST_THRESHOLD: float = 5.0
DEFAULT_TOKEN_WARNING_THRESHOLD: int = 1_000_000


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
        # Session-level acknowledgement of the cost threshold (Req 15.4). Once set,
        # check_cost_warning() returns None for the remainder of the session even
        # as cost continues to rise. Initialized from persisted config defensively.
        self._cost_acknowledged: bool = self._load_persisted_acknowledgement()

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

    def get_cost_threshold(self) -> float:
        """Read the configured Cost_Threshold from global config, defensively.

        Falls back to DEFAULT_COST_THRESHOLD if the config is unavailable or the
        stored value is missing/invalid (Req 15.2).
        """
        try:
            from src.config_store import get_global_config

            value = get_global_config().get("costThreshold")
            if value is None:
                return DEFAULT_COST_THRESHOLD
            return float(value)
        except Exception:
            return DEFAULT_COST_THRESHOLD

    def get_token_warning_threshold(self) -> int:
        """Read the configured Token_Warning threshold, defensively.

        Falls back to DEFAULT_TOKEN_WARNING_THRESHOLD if the config is unavailable
        or the stored value is missing/invalid (Req 15.3).
        """
        try:
            from src.config_store import get_global_config

            value = get_global_config().get("tokenWarningThreshold")
            if value is None:
                return DEFAULT_TOKEN_WARNING_THRESHOLD
            return int(value)
        except Exception:
            return DEFAULT_TOKEN_WARNING_THRESHOLD

    def _load_persisted_acknowledgement(self) -> bool:
        """Read the persisted costThresholdAcknowledged flag, defensively (Req 15.4)."""
        try:
            from src.config_store import get_global_config

            return bool(get_global_config().get("costThresholdAcknowledged", False))
        except Exception:
            return False

    def check_cost_warning(self) -> Optional[str]:
        """Return a cost warning when cumulative cost exceeds the Cost_Threshold.

        Returns a warning string when cumulative session cost exceeds the configured
        Cost_Threshold AND the threshold has not been acknowledged; otherwise None
        (Req 15.2, 15.4 / Property 33).
        """
        if self._cost_acknowledged:
            return None
        threshold = self.get_cost_threshold()
        if self.total_cost_usd > threshold:
            return (
                f"⚠️  Session cost ${self.total_cost_usd:.4f} USD has exceeded the "
                f"configured threshold of ${threshold:.2f} USD."
            )
        return None

    def check_token_warning(self) -> Optional[str]:
        """Return a token-usage warning when cumulative tokens exceed the threshold.

        Returns a warning string when cumulative session tokens exceed the configured
        Token_Warning threshold; otherwise None (Req 15.3 / Property 33).
        """
        threshold = self.get_token_warning_threshold()
        if self.total_tokens > threshold:
            return (
                f"⚠️  Session token usage {self.total_tokens:,} has exceeded the "
                f"configured threshold of {threshold:,} tokens."
            )
        return None

    def acknowledge_cost(self) -> None:
        """Acknowledge the Cost_Threshold warning for the session (Req 15.4).

        Marks the threshold acknowledged so check_cost_warning() returns None for the
        rest of the session even as cost rises, and persists costThresholdAcknowledged
        to the global config defensively (persistence failure is non-fatal).
        """
        self._cost_acknowledged = True
        try:
            from src.config_store import get_global_config, save_global_config

            cfg = get_global_config()
            cfg["costThresholdAcknowledged"] = True
            save_global_config(cfg)
        except Exception:
            # Persistence is best-effort; the session-level flag still suppresses warnings.
            pass

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
