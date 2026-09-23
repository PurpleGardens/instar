# SPDX-License-Identifier: Apache-2.0
"""Anthropic backend.

Requires the optional ``anthropic`` SDK: ``pip install 'instar[anthropic]'``.
The import is lazy, so the stdlib-only mock path never pays for it.

Credentials resolve from the environment the way the SDK expects
(``ANTHROPIC_API_KEY``). Tests inject a fake via ``client=`` to stay offline.
"""

from __future__ import annotations

import time
from typing import Any

from instar.core.traffic import TrafficSample
from instar.providers.base import (
    Backend,
    ChatRequest,
    ChatTurn,
    CompletionResult,
    ToolCallRequest,
)


class AnthropicBackend(Backend):
    """Live Anthropic completions via ``messages.create``.

    Pin an **undated** model id. Dated ids are a recurring source of 404s as
    snapshots are retired, and a run that dies halfway through is worse than a
    run pinned slightly loosely.

    No extended thinking is requested. Replay traffic is ordinary generation and
    classification, so leaving thinking off is both cheaper and more stable
    across runs.
    """

    def __init__(self, name: str = "anthropic", *, client: Any = None) -> None:
        self.name = name
        self._client = client  # None → construct lazily from the environment

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic  # optional dependency, imported only on a live run
            except ModuleNotFoundError as e:  # a traceback here helps nobody
                raise ModuleNotFoundError(
                    "the Anthropic backend needs the 'anthropic' SDK, which is an "
                    "optional extra. Install it with:  pip install 'instar[anthropic]'"
                ) from e
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": sample.max_tokens,
            "messages": sample.messages,
        }
        if sample.system:
            kwargs["system"] = sample.system
        if sample.temperature is not None:
            kwargs["temperature"] = sample.temperature

        t0 = time.perf_counter()
        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:  # keep the batch alive; flag this call as failed
            return CompletionResult.failure(
                model, f"{type(e).__name__}: {e}", latency_s=time.perf_counter() - t0
            )
        dt = time.perf_counter() - t0

        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        usage = resp.usage
        return CompletionResult(
            text=text,
            model=model,
            input_tokens=int(getattr(usage, "input_tokens", 0)),
            output_tokens=int(getattr(usage, "output_tokens", 0)),
            latency_s=dt,
            ok=True,
        )

    def chat(self, request: ChatRequest) -> ChatTurn:
        """One turn of a manual tool-use loop.

        Tools go out as ``{name, description, input_schema}``. A previous
        assistant turn is sent back as the raw content blocks it arrived as, so
        anything the model returned besides text and tool calls (thinking
        blocks) round-trips unchanged. Tool results go back as ``tool_result``
        blocks, all results for a turn in one user message, with ``is_error``
        set for a failed or refused call.

        No refusal fallback is requested: a fallback serves the turn from a
        different model, which would silently change what is being measured.
        """
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [_to_anthropic(m) for m in request.messages],
        }
        if request.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
        if request.system:
            kwargs["system"] = request.system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        t0 = time.perf_counter()
        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:  # keep the run alive; flag this turn as failed
            return ChatTurn.failure(
                request.model, f"{type(e).__name__}: {e}", time.perf_counter() - t0
            )
        dt = time.perf_counter() - t0

        blocks = list(resp.content)
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text")
        calls = [
            ToolCallRequest(
                id=str(b.id), name=str(b.name), arguments=dict(getattr(b, "input", None) or {})
            )
            for b in blocks
            if getattr(b, "type", None) == "tool_use"
        ]
        usage = resp.usage
        return ChatTurn(
            text=text,
            tool_calls=calls,
            model=str(getattr(resp, "model", None) or request.model),
            input_tokens=int(getattr(usage, "input_tokens", 0)),
            output_tokens=int(getattr(usage, "output_tokens", 0)),
            latency_s=dt,
            stop_reason=str(getattr(resp, "stop_reason", None) or "unknown"),
            raw=blocks,
        )


def _to_anthropic(m: dict[str, Any]) -> dict[str, Any]:
    """Instar's conversation form to a Messages API message."""
    role = m.get("role")
    if role == "assistant":
        if m.get("raw") is not None:
            return {"role": "assistant", "content": m["raw"]}
        content: list[dict[str, Any]] = []
        if m.get("text"):
            content.append({"type": "text", "text": m["text"]})
        for c in m.get("tool_calls", []):
            content.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
        return {"role": "assistant", "content": content}
    if role == "tool_results":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r["id"],
                    "content": r["content"],
                    "is_error": bool(r.get("is_error", False)),
                }
                for r in m["results"]
            ],
        }
    return {"role": str(role), "content": m.get("content", "")}
