"""
Per-request cost tracking via ContextVar.

Each service calls start_tracking() at the top of a fresh run.
All subsequent _create_json_completion() and _embed() calls in the
same async context accumulate into the tracker automatically.
At the end of the run, get_summary() returns the totals.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

# Pricing per 1M tokens (input_usd, output_usd)
# Rates as of April 2026 — update as needed.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":                 (2.50,  10.00),
    "gpt-4o-mini":            (0.15,   0.60),
    "text-embedding-3-small": (0.02,   0.00),
}
_EMBEDDING_DEFAULT = (0.02, 0.00)
_LLM_DEFAULT       = (0.15,  0.60)  # fall back to mini pricing for unknown models


@dataclass
class _Accumulator:
    cost_usd:           float = 0.0
    llm_calls:          int   = 0
    prompt_tokens:      int   = 0
    completion_tokens:  int   = 0
    embedding_tokens:   int   = 0


_ctx: ContextVar[_Accumulator | None] = ContextVar("cost_ctx", default=None)


def start_tracking() -> None:
    """Start a fresh cost accumulator for the current async context."""
    _ctx.set(_Accumulator())


def record_llm(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Record one LLM completion call."""
    acc = _ctx.get()
    if acc is None:
        return
    inp, out = _PRICING.get(model, _LLM_DEFAULT)
    acc.cost_usd          += (prompt_tokens / 1_000_000) * inp + (completion_tokens / 1_000_000) * out
    acc.prompt_tokens     += prompt_tokens
    acc.completion_tokens += completion_tokens
    acc.llm_calls         += 1


def record_embedding(model: str, tokens: int) -> None:
    """Record one embedding call."""
    acc = _ctx.get()
    if acc is None:
        return
    inp, _ = _PRICING.get(model, _EMBEDDING_DEFAULT)
    acc.cost_usd         += (tokens / 1_000_000) * inp
    acc.embedding_tokens += tokens


def get_summary() -> dict:
    """Return a serialisable cost summary dict, or an empty dict if not tracking."""
    acc = _ctx.get()
    if acc is None:
        return {}
    total = acc.prompt_tokens + acc.completion_tokens + acc.embedding_tokens
    return {
        "cost_usd":          round(acc.cost_usd, 6),
        "llm_calls":         acc.llm_calls,
        "prompt_tokens":     acc.prompt_tokens,
        "completion_tokens": acc.completion_tokens,
        "embedding_tokens":  acc.embedding_tokens,
        "total_tokens":      total,
    }
