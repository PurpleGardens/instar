# SPDX-License-Identifier: Apache-2.0
"""Deterministic mock backend — the hermetic default.

Mock mode is not a toy. It is how you read the data flow, write a fixture, wire
a policy, and get a green CI run without spending a token or holding an API key.
Output text and token counts are a pure function of ``(sample.id, model)``, so
two runs of the same fixture produce byte-identical results.

Numbers from a mock run are **deterministic placeholders, not a measurement**.
Every report Instar writes says so on its face.
"""

from __future__ import annotations

import hashlib
import json

from instar.core.traffic import TrafficSample
from instar.providers.base import (
    Backend,
    ChatRequest,
    ChatTurn,
    CompletionResult,
    ToolCallRequest,
    estimate_tokens,
    sample_text,
)


class MockBackend(Backend):
    """Reproducible synthetic completions.

    ``latency_s`` lets you give two mock arms different simulated speeds so a
    gateway comparison produces a non-degenerate spread.
    """

    def __init__(self, name: str = "mock", *, latency_s: float = 0.01) -> None:
        self.name = name
        self._latency = latency_s

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        seed = hashlib.sha256(f"{sample.id}:{model}".encode()).hexdigest()
        in_tok = estimate_tokens(sample_text(sample))
        # Deterministic pseudo-output length, capped by the sample's own limit.
        out_tok = 1 + (int(seed[:4], 16) % max(1, sample.max_tokens))
        text = f"[mock:{model}] response to {sample.feature} ({sample.id})"
        return CompletionResult(
            text=text,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_s=self._latency,
            ok=True,
        )

    def chat(self, request: ChatRequest) -> ChatTurn:
        """A scripted tool-using turn. Measures nothing.

        On the first turn, calls the tools listed in the sample's
        ``meta["mock_tool_calls"]`` (``[{"tool": name, "arguments": {...}}]``;
        a plain tool name matches a server-prefixed one). Once results are in,
        answers with a deterministic text that quotes the start of each result,
        so a criteria judge has something to read.
        """
        results = [
            r for m in request.messages if m.get("role") == "tool_results" for r in m["results"]
        ]
        in_tok = sum(estimate_tokens(json.dumps(m, default=str)) for m in request.messages)
        in_tok += sum(estimate_tokens(json.dumps(t.input_schema)) for t in request.tools)
        planned = request.meta.get("mock_tool_calls") or []
        if planned and not results and request.tools:
            names = [t.name for t in request.tools]
            calls: list[ToolCallRequest] = []
            for i, c in enumerate(planned):
                want = str(c.get("tool"))
                name = next((n for n in names if n == want or n.endswith("__" + want)), want)
                calls.append(ToolCallRequest(f"call-{i + 1}", name, dict(c.get("arguments") or {})))
            return ChatTurn(
                "", calls, request.model, in_tok, 8 * len(calls), self._latency, "tool_use"
            )
        quoted = "; ".join(str(r["content"])[:60] for r in results)
        text = f"[mock:{request.model}] answer from {len(results)} tool result(s): {quoted}"
        return ChatTurn(
            text, [], request.model, in_tok, estimate_tokens(text), self._latency, "end_turn"
        )
