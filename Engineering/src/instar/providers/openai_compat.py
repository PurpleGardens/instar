# SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible chat-completions backend.

Speaks the ``POST {base_url}/chat/completions`` dialect that most of the
ecosystem has settled on, using only the standard library — no SDK, no extra
dependency. One class therefore covers a lot of ground:

- a **self-hosted small model** behind vLLM, TGI, or Ollama;
- a **gateway or router** — LiteLLM proxy, OpenRouter, or your own;
- **OpenAI** itself.

That breadth is the point. Comparing two gateways, or a hosted frontier model
against a small model on your own hardware, is just two instances of this class
pointed at different URLs.

Auth, if the endpoint needs it, is a bearer token from ``api_key`` or the
``api_key_env`` environment variable. Local vLLM and Ollama usually need none.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult

DEFAULT_TIMEOUT_S = 120.0


class OpenAICompatBackend(Backend):
    """Chat completions against any OpenAI-compatible endpoint.

    Args:
        base_url: Root of the API, with or without a trailing ``/v1``. Both
            ``http://localhost:8000`` and ``http://localhost:8000/v1`` work.
        name: Label for this arm in reports (e.g. ``"litellm"``, ``"vllm"``).
        api_key: Bearer token. Omit for endpoints that do not authenticate.
        api_key_env: Environment variable to read the token from when
            ``api_key`` is not given.
        timeout_s: Per-request timeout.
        extra_headers: Additional headers, e.g. OpenRouter's attribution pair.
        extra_body: Extra top-level request fields, merged into every payload.
            This is how endpoint-specific controls reach the wire without the
            harness growing a vendor-shaped API: OpenRouter's routing and
            privacy preferences go here as
            ``{"provider": {"sort": "price", "zdr": True}}``. Keys the harness
            sets itself (``model``, ``messages``, ``max_tokens``) win, so a
            stray override cannot silently change what is being measured.
        request_usage_accounting: Ask the endpoint to return cost alongside
            tokens (OpenRouter's ``usage: {include: true}``). Harmless on
            endpoints that ignore unknown fields, and the only way to get a
            router's real per-call charge. Turn it off if a strict endpoint
            rejects the field.
    """

    def __init__(
        self,
        base_url: str,
        *,
        name: str = "openai-compat",
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        request_usage_accounting: bool = True,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.extra_headers = dict(extra_headers or {})
        self.extra_body = dict(extra_body or {})
        self.request_usage_accounting = request_usage_accounting

    @property
    def endpoint(self) -> str:
        """Full chat-completions URL, tolerating a base_url with or without /v1."""
        base = self.base_url
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    def _resolve_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None

    def _build_payload(self, sample: TrafficSample, model: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if sample.system:
            messages.append({"role": "system", "content": sample.system})
        messages.extend(sample.messages)
        payload: dict[str, Any] = dict(self.extra_body)
        if self.request_usage_accounting:
            payload.setdefault("usage", {"include": True})
        payload.update(
            {
                "model": model,
                "messages": messages,
                "max_tokens": sample.max_tokens,
            }
        )
        if sample.temperature is not None:
            payload["temperature"] = sample.temperature
        return payload

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.extra_headers}
        key = self._resolve_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            decoded: Any = json.loads(resp.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("response was not a JSON object")
        return decoded

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        t0 = time.perf_counter()
        try:
            data = self._post(self._build_payload(sample, model))
        except urllib.error.HTTPError as e:
            detail = ""
            # Best-effort: a provider's error body is useful but never required.
            with contextlib.suppress(Exception):
                detail = e.read().decode("utf-8", errors="replace")[:200]
            return CompletionResult.failure(
                model, f"HTTP {e.code}: {detail or e.reason}", time.perf_counter() - t0
            )
        except Exception as e:  # network, timeout, malformed JSON
            return CompletionResult.failure(
                model, f"{type(e).__name__}: {e}", time.perf_counter() - t0
            )
        dt = time.perf_counter() - t0

        choices = data.get("choices") or []
        if not choices:
            return CompletionResult.failure(model, "response contained no choices", dt)
        message = choices[0].get("message") or {}
        text = message.get("content") or ""

        # Usage is optional in the spec; some local servers omit it. A missing
        # count becomes 0 rather than a guess — a zero in a report is a visible
        # gap, an estimate silently masquerades as a measurement.
        usage = data.get("usage") or {}
        return CompletionResult(
            text=text if isinstance(text, str) else str(text),
            # The model that ANSWERED, which for a router is not always the one
            # asked for — a fallback chain or an auto-router may substitute.
            # Reporting the request would hide the substitution being measured.
            model=str(data.get("model") or model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_s=dt,
            ok=True,
            cost_usd=_reported_cost(usage),
        )


def _reported_cost(usage: dict[str, Any]) -> float | None:
    """The endpoint's own USD figure for a call, if it gave a usable one.

    Absent, non-numeric, or negative all mean "unknown" and yield None, so a
    malformed field degrades to the pricing-table path rather than poisoning a
    cost report. ``bool`` is excluded explicitly because it is an ``int``
    subclass in Python, and ``"cost": true`` must never become $1.00.
    """
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return None
    return float(cost) if cost >= 0 else None
