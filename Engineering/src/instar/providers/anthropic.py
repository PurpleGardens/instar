# SPDX-License-Identifier: Apache-2.0
"""Anthropic backend.

Requires the optional ``anthropic`` SDK: ``pip install 'instar-harness[anthropic]'``.
The import is lazy, so the stdlib-only mock path never pays for it.

Credentials resolve from the environment the way the SDK expects
(``ANTHROPIC_API_KEY``). Tests inject a fake via ``client=`` to stay offline.
"""

from __future__ import annotations

import time
from typing import Any

from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult


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
                    "optional extra. Install it with:  pip install 'instar-harness[anthropic]'"
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
