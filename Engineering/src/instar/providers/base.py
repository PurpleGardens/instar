# SPDX-License-Identifier: Apache-2.0
"""The provider interface: turn a :class:`TrafficSample` into a completion.

A :class:`Backend` is the seam between the harness and the outside world. The
harness never imports a vendor SDK directly — it calls ``backend.complete()``
and reads token counts and latency off the result. That is what lets the same
workload run against a frontier API, a cheap hosted model, a self-hosted small
model, or a gateway, without the measurement code changing.

To add a provider, subclass :class:`Backend` and implement one method. A
failed call must return ``ok=False`` with an ``error`` string rather than
raising, so one dead call cannot abort a long run — the runner records the
failure loudly and excludes it from the aggregates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from instar.core.traffic import TrafficSample


@dataclass(frozen=True)
class CompletionResult:
    """One completion, with everything the harness needs to price and score it."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    ok: bool = True
    error: str | None = None
    # What the provider says this call actually cost, in USD, when it says so
    # at all. Most endpoints don't: a frontier API returns tokens and leaves
    # the arithmetic to you, so this stays None and the harness prices the
    # call from a pricing table. Aggregators are the exception — OpenRouter
    # returns ``usage.cost`` because it alone knows which upstream host served
    # the request and at what rate.
    #
    # Where it IS reported it should win, because a pricing table cannot
    # honestly cover a router: 400+ models across 70+ hosts at prices that
    # move weekly. None means "unknown", never "free" — see
    # :func:`instar.core.arms.resolve_arm_cost`.
    cost_usd: float | None = None

    @classmethod
    def failure(cls, model: str, error: str, latency_s: float = 0.0) -> CompletionResult:
        """A failed call. Zero tokens, ``ok=False``, and the reason preserved."""
        return cls(
            text="",
            model=model,
            input_tokens=0,
            output_tokens=0,
            latency_s=latency_s,
            ok=False,
            error=error,
            cost_usd=None,
        )


def estimate_tokens(text: str) -> int:
    """Crude, deterministic token estimate (~4 characters per token).

    Mock mode only. Live backends report the provider's real token counts, which
    is the only number you should ever put in a report.
    """
    return max(1, len(text) // 4)


def sample_text(sample: TrafficSample) -> str:
    """Flatten a sample's system prompt and messages into one string."""
    parts = [sample.system or ""]
    for m in sample.messages:
        content = m.get("content", "")
        parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


class Backend(ABC):
    """Something that can produce a completion for a sample."""

    name: str = "abstract"

    @abstractmethod
    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        """Run ``sample`` against ``model``. Never raises for a provider error —
        returns :meth:`CompletionResult.failure` instead."""
