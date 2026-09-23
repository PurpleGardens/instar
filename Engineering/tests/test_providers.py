# SPDX-License-Identifier: Apache-2.0
"""Provider backends: determinism, failure handling, and protocol shape.

No test here touches the network. The Anthropic backend takes an injected fake
client; the OpenAI-compatible backend has its transport monkeypatched.
"""

import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any

import pytest

from instar.core.traffic import TrafficSample
from instar.providers.anthropic import AnthropicBackend
from instar.providers.base import CompletionResult, estimate_tokens, sample_text
from instar.providers.mock import MockBackend
from instar.providers.openai_compat import OpenAICompatBackend

SAMPLE = TrafficSample(
    id="s1",
    feature="a.b",
    messages=[{"role": "user", "content": "hello there"}],
    system="be terse",
    max_tokens=100,
)


# ── base ────────────────────────────────────────────────────────────────


def test_estimate_tokens_is_never_zero() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_sample_text_includes_system_and_messages() -> None:
    text = sample_text(SAMPLE)
    assert "be terse" in text
    assert "hello there" in text


def test_failure_result_is_zero_token_and_not_ok() -> None:
    r = CompletionResult.failure("m", "boom")
    assert not r.ok
    assert r.error == "boom"
    assert r.input_tokens == 0
    assert r.output_tokens == 0


# ── mock ────────────────────────────────────────────────────────────────


def test_mock_backend_is_deterministic() -> None:
    """Reproducibility is the whole point of mock mode."""
    a = MockBackend().complete(SAMPLE, "mock-weak")
    b = MockBackend().complete(SAMPLE, "mock-weak")
    assert a == b


def test_mock_backend_varies_by_model() -> None:
    a = MockBackend().complete(SAMPLE, "mock-weak")
    b = MockBackend().complete(SAMPLE, "mock-strong")
    assert a.text != b.text


def test_mock_backend_respects_the_token_cap() -> None:
    small = TrafficSample(
        id="s", feature="f", messages=[{"role": "user", "content": "x"}], max_tokens=4
    )
    assert MockBackend().complete(small, "mock-weak").output_tokens <= 4


def test_mock_backend_reports_its_simulated_latency() -> None:
    assert MockBackend(latency_s=0.5).complete(SAMPLE, "m").latency_s == 0.5


# ── anthropic ───────────────────────────────────────────────────────────


class _FakeMessages:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._response


def _fake_client(response: Any = None, raises: Exception | None = None) -> Any:
    return SimpleNamespace(messages=_FakeMessages(response, raises))


def _text_response(text: str, in_tok: int = 11, out_tok: int = 7) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def test_anthropic_maps_a_successful_response() -> None:
    client = _fake_client(_text_response("hi"))
    r = AnthropicBackend(client=client).complete(SAMPLE, "claude-haiku-4-5")
    assert r.ok
    assert r.text == "hi"
    assert (r.input_tokens, r.output_tokens) == (11, 7)


def test_anthropic_passes_system_and_temperature_through() -> None:
    client = _fake_client(_text_response("hi"))
    sample = TrafficSample(
        id="s",
        feature="f",
        messages=[{"role": "user", "content": "x"}],
        system="sys",
        temperature=0.2,
        max_tokens=64,
    )
    AnthropicBackend(client=client).complete(sample, "m")
    kwargs = client.messages.calls[0]
    assert kwargs["system"] == "sys"
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 64


def test_anthropic_omits_temperature_when_unset() -> None:
    client = _fake_client(_text_response("hi"))
    AnthropicBackend(client=client).complete(SAMPLE, "m")
    assert "temperature" not in client.messages.calls[0]


def test_anthropic_turns_an_exception_into_a_failed_result() -> None:
    """One dead call must not abort a long run."""
    client = _fake_client(raises=RuntimeError("rate limited"))
    r = AnthropicBackend(client=client).complete(SAMPLE, "m")
    assert not r.ok
    assert "rate limited" in (r.error or "")


def test_a_missing_sdk_explains_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raw ModuleNotFoundError traceback helps nobody."""
    import builtins

    real_import = builtins.__import__

    def no_anthropic(name: str, *a: object, **kw: object) -> Any:
        if name == "anthropic":
            raise ModuleNotFoundError("No module named 'anthropic'")
        return real_import(name, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    with pytest.raises(ModuleNotFoundError, match=r"instar-harness\[anthropic\]"):
        AnthropicBackend().complete(SAMPLE, "m")


def test_anthropic_ignores_non_text_blocks() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", text="ignore me"),
            SimpleNamespace(type="text", text="keep me"),
        ],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    r = AnthropicBackend(client=_fake_client(response)).complete(SAMPLE, "m")
    assert r.text == "keep me"


# ── openai-compatible ───────────────────────────────────────────────────


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[Any]:
    captured: list[Any] = []

    def fake_urlopen(req: Any, timeout: float | None = None) -> _FakeResponse:
        captured.append(req)
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


CHAT_OK = {
    "model": "served-model",
    "choices": [{"message": {"role": "assistant", "content": "hello back"}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5},
}


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:8000", "http://localhost:8000/v1/chat/completions"),
        ("http://localhost:8000/", "http://localhost:8000/v1/chat/completions"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1/chat/completions"),
    ],
)
def test_endpoint_tolerates_v1_and_trailing_slash(base_url: str, expected: str) -> None:
    assert OpenAICompatBackend(base_url).endpoint == expected


def test_openai_compat_maps_a_successful_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, CHAT_OK)
    r = OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    assert r.ok
    assert r.text == "hello back"
    assert (r.input_tokens, r.output_tokens) == (12, 5)
    assert r.model == "served-model"


def test_openai_compat_folds_system_into_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat dialect has no separate system field — it must become message[0]."""
    captured = _patch_urlopen(monkeypatch, CHAT_OK)
    OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    body = json.loads(captured[0].data)
    assert body["messages"][0] == {"role": "system", "content": "be terse"}
    assert body["messages"][1]["content"] == "hello there"


def test_openai_compat_sends_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(monkeypatch, CHAT_OK)
    OpenAICompatBackend("http://x", api_key="sk-test").complete(SAMPLE, "m")
    assert captured[0].get_header("Authorization") == "Bearer sk-test"


def test_openai_compat_reads_a_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_KEY", "sk-env")
    captured = _patch_urlopen(monkeypatch, CHAT_OK)
    OpenAICompatBackend("http://x", api_key_env="MY_KEY").complete(SAMPLE, "m")
    assert captured[0].get_header("Authorization") == "Bearer sk-env"


def test_openai_compat_omits_auth_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local vLLM and Ollama usually want no Authorization header at all."""
    captured = _patch_urlopen(monkeypatch, CHAT_OK)
    OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    assert captured[0].get_header("Authorization") is None


def test_openai_compat_sends_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(monkeypatch, CHAT_OK)
    OpenAICompatBackend("http://x", extra_headers={"X-Title": "instar"}).complete(SAMPLE, "m")
    assert captured[0].get_header("X-title") == "instar"


def test_openai_compat_defaults_missing_usage_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some local servers omit usage. A visible zero beats a silent estimate."""
    _patch_urlopen(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
    r = OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    assert r.ok
    assert (r.input_tokens, r.output_tokens) == (0, 0)


def test_openai_compat_reports_an_empty_choices_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, {"choices": []})
    r = OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    assert not r.ok
    assert "no choices" in (r.error or "")


def test_openai_compat_maps_an_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float | None = None) -> None:
        raise urllib.error.HTTPError(
            "http://x",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b"slow down"),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    assert not r.ok
    assert "429" in (r.error or "")


def test_openai_compat_maps_a_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float | None = None) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = OpenAICompatBackend("http://x").complete(SAMPLE, "m")
    assert not r.ok
    assert "timed out" in (r.error or "")
