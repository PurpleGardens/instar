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
from dataclasses import dataclass, field
from typing import Any

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
    # For an agent run (a model calling tools over several turns), what
    # happened on the way to ``text``: per-turn tokens and latency, every tool
    # call and its outcome. None for an ordinary single completion.
    trajectory: dict[str, Any] | None = None

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


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model: what the model is told, nothing more."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallRequest:
    """A tool call the model asked for."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    """One model turn in a tool-using conversation, provider-neutral.

    ``messages`` is Instar's own conversation form, which each backend
    translates to its dialect:

    - ``{"role": "user", "content": ...}``: as in a :class:`TrafficSample`;
    - ``{"role": "assistant", "text": str, "tool_calls": [ToolCallRequest],
      "raw": ...}``: a previous :class:`ChatTurn`. ``raw`` is the provider's
      own form of that turn; a backend sends it back unchanged when it can,
      so provider-specific content (such as thinking blocks) round-trips;
    - ``{"role": "tool_results", "results": [{"id", "content", "is_error"}]}``.

    ``meta`` is the sample's meta, for backends (the mock) that script
    behaviour from it.
    """

    model: str
    messages: list[dict[str, Any]]
    tools: list[ToolSpec]
    max_tokens: int
    system: str | None = None
    temperature: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatTurn:
    """What the model did in one turn: text, tool calls, and what it cost."""

    text: str
    tool_calls: list[ToolCallRequest]
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    stop_reason: str
    ok: bool = True
    error: str | None = None
    cost_usd: float | None = None
    raw: Any = None

    @classmethod
    def failure(cls, model: str, error: str, latency_s: float = 0.0) -> ChatTurn:
        return cls("", [], model, 0, 0, latency_s, "error", ok=False, error=error)


class Backend(ABC):
    """Something that can produce a completion for a sample."""

    name: str = "abstract"

    @abstractmethod
    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        """Run ``sample`` against ``model``. Never raises for a provider error —
        returns :meth:`CompletionResult.failure` instead."""

    def chat(self, request: ChatRequest) -> ChatTurn:
        """One tool-capable model turn. Optional: backends that implement it
        can drive an agent loop (:mod:`instar.mcp.agent`). Never raises for a
        provider error — returns :meth:`ChatTurn.failure` instead."""
        raise NotImplementedError(f"backend {self.name!r} does not support tool use")
