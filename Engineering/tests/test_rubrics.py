# SPDX-License-Identifier: Apache-2.0
"""Judges: deterministic, objective, model-based, and dispatching."""

from instar.core.catalog import BACKGROUND, FOREGROUND, FeatureCatalog
from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult
from instar.rubrics.judges import (
    AutoJudge,
    LabelMatchJudge,
    LLMJudge,
    MockJudge,
    _is_transient,
    judge_complete,
)

CATALOG = FeatureCatalog({"bg.job": BACKGROUND, "fg.chat": FOREGROUND})
LABELS = ["billing", "bug_report", "account_access", "access"]


def _sample(feature: str = "bg.job", gold: str | None = None) -> TrafficSample:
    meta = {"gold": gold} if gold else {}
    return TrafficSample(
        id=f"s-{feature}-{gold}",
        feature=feature,
        messages=[{"role": "user", "content": "classify this"}],
        meta=meta,
    )


def _result(text: str, model: str = "m") -> CompletionResult:
    return CompletionResult(text=text, model=model, input_tokens=1, output_tokens=1, latency_s=0.0)


# ── mock judge ──────────────────────────────────────────────────────────


def test_mock_judge_is_deterministic() -> None:
    s, strong, weak = _sample(), _result("a", "strong"), _result("b", "weak")
    assert MockJudge(CATALOG).score(s, strong, weak) == MockJudge(CATALOG).score(s, strong, weak)


def test_mock_judge_scores_background_above_foreground() -> None:
    """Gives the mock curve a realistic shape without measuring anything."""
    strong, weak = _result("a", "strong"), _result("b", "weak")
    bg = MockJudge(CATALOG).score(_sample("bg.job"), strong, weak).score
    fg = MockJudge(CATALOG).score(_sample("fg.chat"), strong, weak).score
    assert bg > fg


def test_mock_judge_stays_in_range() -> None:
    for i in range(50):
        s = TrafficSample(id=f"s{i}", feature="bg.job", messages=[])
        assert 0.0 <= MockJudge(CATALOG).score(s, _result("a"), _result("b")).score <= 1.0


# ── label match ─────────────────────────────────────────────────────────


def test_label_match_scores_a_hit() -> None:
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(gold="billing"), _result("billing"), _result("billing"))
    assert r.score == 1.0


def test_label_match_scores_a_miss() -> None:
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(gold="billing"), _result("billing"), _result("bug_report"))
    assert r.score == 0.0


def test_label_match_is_case_insensitive() -> None:
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(gold="billing"), _result("BILLING"), _result("Billing"))
    assert r.score == 1.0


def test_label_match_prefers_the_longest_label() -> None:
    """'account_access' must not lose to the substring 'access'."""
    judge = LabelMatchJudge(LABELS)
    r = judge.score(
        _sample(gold="account_access"), _result("account_access"), _result("account_access")
    )
    assert r.score == 1.0


def test_label_match_treats_an_unrecognizable_answer_as_wrong() -> None:
    """A refusal or an invented category is a routing failure, not a tie."""
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(gold="billing"), _result("billing"), _result("I cannot help"))
    assert r.score == 0.0
    assert "no recognizable label" in r.rationale


def test_label_match_falls_back_to_the_strong_output() -> None:
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(), _result("billing"), _result("billing"))
    assert r.score == 1.0


def test_label_match_scores_zero_with_no_gold_and_no_strong_label() -> None:
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(), _result("nothing useful"), _result("billing"))
    assert r.score == 0.0


def test_label_match_rationale_names_both_sides() -> None:
    judge = LabelMatchJudge(LABELS)
    r = judge.score(_sample(gold="billing"), _result("billing"), _result("bug_report"))
    assert "bug_report" in r.rationale
    assert "billing" in r.rationale


# ── llm judge ───────────────────────────────────────────────────────────


class _VerdictBackend(Backend):
    """A judge model that always returns the verdict it was constructed with."""

    name = "verdict"

    def __init__(self, verdict: str, ok: bool = True) -> None:
        self.verdict = verdict
        self.ok = ok
        self.seen: list[TrafficSample] = []

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        self.seen.append(sample)
        if not self.ok:
            return CompletionResult.failure(model, "judge exploded")
        return CompletionResult(
            text=self.verdict, model=model, input_tokens=1, output_tokens=1, latency_s=0.0
        )


def test_llm_judge_maps_pass_to_one() -> None:
    judge = LLMJudge(_VerdictBackend("PASS"), "m")
    assert judge.score(_sample(), _result("a"), _result("b")).score == 1.0


def test_llm_judge_maps_marginal_to_a_half() -> None:
    """The rung that matters: a retry is not a saving."""
    judge = LLMJudge(_VerdictBackend("MARGINAL"), "m")
    assert judge.score(_sample(), _result("a"), _result("b")).score == 0.5


def test_llm_judge_maps_fail_to_zero() -> None:
    judge = LLMJudge(_VerdictBackend("FAIL"), "m")
    assert judge.score(_sample(), _result("a"), _result("b")).score == 0.0


def test_llm_judge_tolerates_surrounding_prose() -> None:
    judge = LLMJudge(_VerdictBackend("  pass  "), "m")
    assert judge.score(_sample(), _result("a"), _result("b")).score == 1.0


def test_llm_judge_hedges_on_an_unparsed_verdict() -> None:
    judge = LLMJudge(_VerdictBackend("maybe?"), "m")
    r = judge.score(_sample(), _result("a"), _result("b"))
    assert r.score == 0.5
    assert "unparsed" in r.rationale


def test_llm_judge_reports_its_own_failure() -> None:
    judge = LLMJudge(_VerdictBackend("PASS", ok=False), "m")
    r = judge.score(_sample(), _result("a"), _result("b"))
    assert r.score == 0.0
    assert "judge call failed" in r.rationale


def test_llm_judge_shows_both_answers_to_the_model() -> None:
    backend = _VerdictBackend("PASS")
    LLMJudge(backend, "m").score(_sample(), _result("STRONG-TEXT"), _result("WEAK-TEXT"))
    prompt = backend.seen[0].messages[0]["content"]
    assert "STRONG-TEXT" in prompt
    assert "WEAK-TEXT" in prompt


# ── auto judge ──────────────────────────────────────────────────────────


def test_auto_judge_uses_labels_when_gold_is_present() -> None:
    llm = LLMJudge(_VerdictBackend("FAIL"), "m")
    judge = AutoJudge(LabelMatchJudge(LABELS), llm)
    r = judge.score(_sample(gold="billing"), _result("billing"), _result("billing"))
    assert r.score == 1.0  # objective judge won, not the LLM's FAIL


def test_auto_judge_falls_back_to_the_llm_without_gold() -> None:
    llm = LLMJudge(_VerdictBackend("MARGINAL"), "m")
    judge = AutoJudge(LabelMatchJudge(LABELS), llm)
    assert judge.score(_sample(), _result("a"), _result("b")).score == 0.5


def test_auto_judge_works_without_a_label_judge() -> None:
    llm = LLMJudge(_VerdictBackend("PASS"), "m")
    judge = AutoJudge(None, llm)
    assert judge.score(_sample(gold="billing"), _result("a"), _result("b")).score == 1.0


# ── judge retry ─────────────────────────────────────────────────────────


class _SequenceBackend(Backend):
    """Returns each queued CompletionResult in turn."""

    name = "seq"

    def __init__(self, results: list[CompletionResult]) -> None:
        self._results = list(results)
        self.calls = 0

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        self.calls += 1
        return self._results.pop(0)


def _fail(err: str) -> CompletionResult:
    return CompletionResult.failure("m", err)


def _ok() -> CompletionResult:
    return CompletionResult(text="PASS", model="m", input_tokens=1, output_tokens=1, latency_s=0.0)


def test_is_transient_classifies_http_codes() -> None:
    assert _is_transient("HTTP 429: rate limit")
    assert _is_transient("HTTP 503: bad gateway")
    assert _is_transient("HTTP 500: oops")
    assert not _is_transient("HTTP 400: bad request")
    assert not _is_transient("HTTP 401: unauthorized")
    assert not _is_transient(None)
    assert not _is_transient("")


def test_is_transient_classifies_network_errors() -> None:
    assert _is_transient("TimeoutError: read timed out")
    assert _is_transient("URLError: <urlopen error [Errno 111]>")
    assert _is_transient("ConnectionResetError: peer reset")
    assert not _is_transient("ValueError: bad json")
    assert not _is_transient("judge exploded")


def test_judge_complete_retries_on_transient_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("instar.rubrics.judges.time.sleep", lambda _: None)
    backend = _SequenceBackend([_fail("HTTP 429: slow down"), _ok()])
    result = judge_complete(backend, _sample(), "m")
    assert result.ok
    assert backend.calls == 2


def test_judge_complete_gives_up_after_retries_exhausted(monkeypatch) -> None:
    monkeypatch.setattr("instar.rubrics.judges.time.sleep", lambda _: None)
    fail = _fail("HTTP 503: gateway")
    backend = _SequenceBackend([fail, fail, fail])
    result = judge_complete(backend, _sample(), "m")
    assert not result.ok
    assert backend.calls == 3  # initial + 2 retries


def test_judge_complete_does_not_retry_non_transient(monkeypatch) -> None:
    monkeypatch.setattr("instar.rubrics.judges.time.sleep", lambda _: None)
    backend = _SequenceBackend([_fail("HTTP 400: bad request")])
    result = judge_complete(backend, _sample(), "m")
    assert not result.ok
    assert backend.calls == 1
