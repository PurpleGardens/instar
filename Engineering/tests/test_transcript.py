# SPDX-License-Identifier: Apache-2.0
"""Transcript capture and offline re-judging.

The property that matters is the round trip: re-judging a transcript with the
same judge must reproduce the live run's numbers exactly. If it does not, a
cross-judge comparison is measuring the replay path instead of the judges.
"""

from __future__ import annotations

import json

import pytest

from instar.core.arms import Arm, rejudge, run_arms
from instar.core.traffic import TrafficSample
from instar.core.transcript import FORMAT_VERSION, Transcript
from instar.providers.base import CompletionResult
from instar.providers.mock import MockBackend
from instar.rubrics.base import Judge, JudgeResult
from instar.rubrics.judges import MockJudge

STRONG = "mock-strong"
WEAK = "mock-weak"


def _samples(n: int = 4) -> list[TrafficSample]:
    return [
        TrafficSample(
            id=f"s{i}",
            feature="demo.feature",
            messages=[{"role": "user", "content": f"question {i}"}],
        )
        for i in range(n)
    ]


def _arms() -> list[Arm]:
    return [
        Arm("a", MockBackend("a", latency_s=0.001), STRONG),
        Arm("b", MockBackend("b", latency_s=0.001), STRONG),
        Arm("c", MockBackend("c", latency_s=0.001), WEAK),
    ]


class _CountingJudge(Judge):
    """Scores everything 0.5 and counts how often it was asked."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        self.calls += 1
        return JudgeResult(0.5, "counting")


def test_capture_off_by_default() -> None:
    result = run_arms(_samples(), arms=_arms())
    assert result.transcript is None


def test_capture_records_every_arm_for_every_sample() -> None:
    samples = _samples(4)
    result = run_arms(samples, arms=_arms(), capture=True)
    t = result.transcript
    assert t is not None
    assert t.baseline == "a"
    assert t.arm_models == {"a": STRONG, "b": STRONG, "c": WEAK}
    assert len(t.entries) == len(samples)
    for entry in t.entries:
        assert set(entry.completions) == {"a", "b", "c"}


def test_capture_follows_repeats() -> None:
    result = run_arms(_samples(3), arms=_arms(), repeats=2, capture=True)
    assert result.transcript is not None
    assert len(result.transcript.entries) == 6


def test_transcript_stays_out_of_result_json() -> None:
    """result.json is a summary people diff; raw model output would swamp it."""
    result = run_arms(_samples(), arms=_arms(), capture=True)
    assert "transcript" not in result.to_json()


def test_round_trip_through_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = run_arms(_samples(), arms=_arms(), capture=True)
    assert result.transcript is not None
    path = result.transcript.save(tmp_path / "t.json")
    loaded = Transcript.load(path)
    assert loaded.baseline == result.transcript.baseline
    assert loaded.arm_models == result.transcript.arm_models
    assert len(loaded.entries) == len(result.transcript.entries)
    first = loaded.entries[0]
    original = result.transcript.entries[0]
    assert first.sample.id == original.sample.id
    assert first.completions["c"].text == original.completions["c"].text


def test_rejudge_reproduces_the_live_scores(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The whole point: same judge, same answers, same numbers."""
    samples = _samples(5)
    live = run_arms(samples, arms=_arms(), judge=MockJudge(), capture=True)
    assert live.transcript is not None
    replayed = rejudge(Transcript.load(live.transcript.save(tmp_path / "t.json")), MockJudge())
    for name in ("b", "c"):
        assert replayed.by_name(name).quality_scores == live.by_name(name).quality_scores
        assert replayed.by_name(name).quality_mean == live.by_name(name).quality_mean


def test_rejudge_replays_cost_and_latency_unchanged(tmp_path) -> None:  # type: ignore[no-untyped-def]
    live = run_arms(_samples(5), arms=_arms(), capture=True)
    assert live.transcript is not None
    replayed = rejudge(live.transcript, MockJudge())
    for name in ("a", "b", "c"):
        assert replayed.by_name(name).cost_usd == live.by_name(name).cost_usd
        assert replayed.by_name(name).output_tokens == live.by_name(name).output_tokens
        assert replayed.by_name(name).p50_ms == live.by_name(name).p50_ms


def test_rejudge_does_not_score_the_baseline_against_itself() -> None:
    live = run_arms(_samples(4), arms=_arms(), capture=True)
    assert live.transcript is not None
    judge = _CountingJudge()
    replayed = rejudge(live.transcript, judge)
    assert replayed.by_name("a").quality_mean is None
    assert judge.calls == 8  # two non-baseline arms x four samples


def test_rejudge_says_the_numbers_were_replayed() -> None:
    live = run_arms(_samples(), arms=_arms(), capture=True)
    assert live.transcript is not None
    replayed = rejudge(live.transcript, MockJudge())
    assert any("re-judged" in w for w in replayed.warnings)


def test_rejudge_rejects_an_empty_transcript() -> None:
    empty = Transcript(baseline="a", arm_models={"a": STRONG})
    with pytest.raises(ValueError, match="no entries"):
        rejudge(empty, MockJudge())


def test_load_rejects_an_unknown_format_version(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "format_version": FORMAT_VERSION + 1,
                "baseline": "a",
                "arm_models": {"a": STRONG},
                "entries": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="format_version"):
        Transcript.load(p)


def test_load_rejects_a_baseline_that_is_not_an_arm(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "format_version": FORMAT_VERSION,
                "baseline": "zz",
                "arm_models": {"a": STRONG},
                "entries": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="baseline"):
        Transcript.load(p)


def test_a_replayed_arm_cannot_generate() -> None:
    """A future edit that tries to complete through a replayed arm must fail loudly."""
    from instar.core.arms import _REPLAY_BACKEND

    with pytest.raises(RuntimeError, match="cannot generate"):
        _REPLAY_BACKEND.complete(_samples(1)[0], STRONG)
