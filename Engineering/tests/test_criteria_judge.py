# SPDX-License-Identifier: Apache-2.0
"""The absolute criteria judge, and what absolute scoring changes downstream.

Properties that matter: a critical miss gates the score to 0.0; a criterion the
judge gave no verdict on is a miss, never a pass; a sample with no criteria is
unscored; the baseline is scored under an absolute judge and only then; and the
corpus reads an absolute judge's control against the baseline, not against 1.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from instar.cli.main import main
from instar.core.arms import Arm, rejudge, run_arms
from instar.core.corpus import RecordContext, write_run
from instar.core.corpus_read import CorpusFilter, calibration, iter_runs, judge_label
from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult
from instar.providers.mock import MockBackend
from instar.rubrics.base import JudgeKey
from instar.rubrics.criteria import (
    CriteriaJudge,
    CriteriaSet,
    Criterion,
    MockCriteriaBackend,
    criteria_score,
    parse_verdicts,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE = FIXTURES / "criteria" / "sample-traffic-example-v1.json"
STRONG = "mock-strong"
WEAK = "mock-weak"


class _ScriptedBackend(Backend):
    """Replies with a fixed verdict text and records the prompt it was given."""

    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        self.prompts.append(sample.messages[0]["content"])
        return CompletionResult(
            text=self.reply, model=model, input_tokens=1, output_tokens=1, latency_s=0.0
        )


def _ok(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="m", input_tokens=1, output_tokens=1, latency_s=0.0)


def _sample(feature: str = "f", **meta: object) -> TrafficSample:
    return TrafficSample(
        id="s1",
        feature=feature,
        system="Be brief.",
        messages=[{"role": "user", "content": "Say hi"}],
        meta=dict(meta),
    )


def _set(**features: list[object]) -> CriteriaSet:
    return CriteriaSet.from_json({"version": "t1", "features": features})


class TestLoading:
    def test_example_fixture_loads(self) -> None:
        cs = CriteriaSet.load(EXAMPLE)
        assert cs.version == "sample-traffic-example-v1"
        assert cs.default and "support.ticket_classification" in cs.features

    def test_strings_and_objects(self) -> None:
        cs = _set(f=["plain", {"id": "gate", "text": "must", "critical": True}])
        assert cs.features["f"] == (Criterion("c1", "plain"), Criterion("gate", "must", True))

    @pytest.mark.parametrize(
        "bad, match",
        [
            ({"features": {"f": "x"}}, "must be a list"),
            ({"features": {"f": [{"text": " "}]}}, "empty"),
            ({"features": {"f": [3]}}, "string or an object"),
            (
                {"features": {"f": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]}},
                "duplicate",
            ),
            ({"features": {}}, "no criteria"),
        ],
    )
    def test_rejects_bad_files(self, bad: dict[str, object], match: str) -> None:
        with pytest.raises(ValueError, match=match):
            CriteriaSet.from_json(bad)

    def test_resolution_order(self) -> None:
        cs = CriteriaSet.from_json({"default": ["d"], "features": {"f": ["feat"]}})
        assert [c.text for c in cs.for_sample(_sample("f"))] == ["feat"]
        assert [c.text for c in cs.for_sample(_sample("other"))] == ["d"]
        assert [c.text for c in cs.for_sample(_sample("f", criteria=["own"]))] == ["own"]


class TestScoring:
    def test_parse_tolerates_format_and_keeps_first(self) -> None:
        assert parse_verdicts("1: yes\n2) NO\n 3 - Yes\n1: NO\n9: YES", 3) == {
            1: True,
            2: False,
            3: True,
        }

    def test_fraction_met(self) -> None:
        cs = [Criterion("a", "a"), Criterion("b", "b"), Criterion("c", "c"), Criterion("d", "d")]
        r = criteria_score(cs, {1: True, 2: True, 3: True, 4: False})
        assert r.score == 0.75
        assert "missed: d" in r.rationale

    def test_critical_miss_gates_to_zero(self) -> None:
        cs = [Criterion("a", "a"), Criterion("gate", "g", critical=True)]
        r = criteria_score(cs, {1: True, 2: False})
        assert r.score == 0.0
        assert "critical failed: gate" in r.rationale

    def test_missing_verdict_is_a_miss(self) -> None:
        cs = [Criterion("a", "a"), Criterion("b", "b")]
        r = criteria_score(cs, {1: True})
        assert r.score == 0.5
        assert "no verdict (counted as missed): b" in r.rationale

    def test_judge_sees_task_answer_and_criteria_not_provenance(self) -> None:
        backend = _ScriptedBackend("1: YES\n2: NO")
        judge = CriteriaJudge(_set(f=["greets", "is formal"]), backend, "claude-x")
        r = judge.score(_sample(), _ok("STRONG TEXT"), _ok("hi there"))
        assert r.score == 0.5
        prompt = backend.prompts[0]
        assert "Be brief." in prompt and "hi there" in prompt and "1. greets" in prompt
        assert "STRONG TEXT" not in prompt  # absolute: the other answer is never shown

    def test_abstains_without_criteria(self) -> None:
        judge = CriteriaJudge(_set(f=["x"]), _ScriptedBackend(""), "m")
        assert judge.abstains(_sample("other"), _ok("a"), _ok("b"))
        assert not judge.abstains(_sample("f"), _ok("a"), _ok("b"))

    def test_key(self) -> None:
        k = CriteriaJudge(_set(f=["x"]), _ScriptedBackend(""), "claude-x").key()
        assert (k.kind, k.family, k.blind, k.absolute, k.version) == (
            "criteria",
            "anthropic",
            True,
            True,
            "t1",
        )

    def test_failed_judge_call(self) -> None:
        class Down(Backend):
            def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
                return CompletionResult.failure(model, "503")

        r = CriteriaJudge(_set(f=["x"]), Down(), "m").score(_sample(), _ok("a"), _ok("b"))
        assert r.score == 0.0 and "503" in r.rationale


def _samples(n: int = 4, feature: str = "f") -> list[TrafficSample]:
    return [
        TrafficSample(id=f"s{i}", feature=feature, messages=[{"role": "user", "content": f"q{i}"}])
        for i in range(n)
    ]


def _arms() -> list[Arm]:
    return [
        Arm("base", MockBackend("base", latency_s=0.001), STRONG),
        Arm("ctl", MockBackend("ctl", latency_s=0.001), STRONG, is_control=True),
        Arm("cheap", MockBackend("cheap", latency_s=0.001), WEAK),
    ]


def _mock_judge(**features: list[object]) -> CriteriaJudge:
    return CriteriaJudge(_set(**features), MockCriteriaBackend(), "mock-judge", family="mock")


class TestArms:
    def test_absolute_judge_scores_the_baseline(self) -> None:
        result = run_arms(_samples(), arms=_arms(), judge=_mock_judge(f=["a", "b", "c"]))
        base = result.by_name("base")
        assert base.quality_mean is not None and base.quality_n == 4
        assert "base" in result.judgments
        assert result.judge is not None and result.judge.absolute

    def test_relative_judge_still_skips_the_baseline(self) -> None:
        from instar.rubrics.judges import MockJudge

        result = run_arms(_samples(), arms=_arms(), judge=MockJudge())
        assert result.by_name("base").quality_mean is None
        assert "base" not in result.judgments

    def test_same_answers_same_score(self) -> None:
        # The control serves the baseline's model; mock output is identical, so an
        # absolute judge must give both arms the same score.
        result = run_arms(_samples(), arms=_arms(), judge=_mock_judge(f=["a", "b", "c"]))
        assert result.by_name("base").quality_scores == result.by_name("ctl").quality_scores

    def test_samples_without_criteria_are_unscored(self) -> None:
        samples = _samples(2, "f") + _samples(2, "g")
        for i, s in enumerate(samples):
            s.id = f"x{i}"
        result = run_arms(samples, arms=_arms(), judge=_mock_judge(f=["a"]))
        assert result.by_name("cheap").quality_n == 2

    def test_rejudge_with_criteria(self) -> None:
        live = run_arms(_samples(), arms=_arms(), capture=True)
        assert live.transcript is not None
        again = rejudge(live.transcript, _mock_judge(f=["a", "b"]))
        assert again.by_name("base").quality_n == 4


class TestCorpus:
    def test_calibration_reads_control_against_baseline(self, tmp_path: Path) -> None:
        result = run_arms(
            _samples(6), arms=_arms(), judge=_mock_judge(f=["a", "b", "c"]), capture=True
        )
        assert result.transcript is not None
        ctx = RecordContext(tenant_id="demo", workload_id="w", mock=True)
        write_run(tmp_path, result, result.transcript, ctx)
        rows = calibration(iter_runs(tmp_path), CorpusFilter(include_mock=True))
        assert len(rows) == 1
        assert rows[0].absolute
        # Identical mock answers: control minus baseline is exactly zero.
        assert rows[0].control.mean == 0.0

    def test_label_marks_absolute_and_version(self) -> None:
        key = JudgeKey("criteria", "m", "anthropic", True, True, "v2")
        assert judge_label(key) == "criteria:m [anthropic, blind, absolute, v=v2]"

    def test_old_judge_keys_still_load(self) -> None:
        k = JudgeKey.from_json({"kind": "llm", "model": "m", "family": "openai", "blind": False})
        assert (k.absolute, k.version) == (False, None)


class TestCli:
    def test_arms_with_criteria_in_mock_mode(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs = tmp_path / "runs"
        traffic = FIXTURES / "sample-traffic.jsonl"
        rc = main(
            [
                "arms",
                "--traffic",
                str(traffic),
                "--criteria",
                str(EXAMPLE),
                "--control",
                "--runs-dir",
                str(runs),
                "--label",
                "crit",
            ]
        )
        assert rc == 0
        result = json.loads((runs / "crit" / "result.json").read_text())
        assert result["judge"]["absolute"] is True
        assert result["judge"]["version"] == "sample-traffic-example-v1"
        base = next(a for a in result["arms"] if a["name"] == result["baseline"])
        assert base["quality_mean"] is not None
        report = (runs / "crit" / "report.md").read_text()
        assert "Absolute: each answer scored on its own" in report

    def test_criteria_and_blind_judge_conflict(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="drop --blind-judge"):
            main(
                [
                    "arms",
                    "--traffic",
                    str(FIXTURES / "sample-traffic.jsonl"),
                    "--criteria",
                    str(EXAMPLE),
                    "--blind-judge",
                    "--runs-dir",
                    str(tmp_path),
                ]
            )

    def test_bad_criteria_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "c.json"
        bad.write_text("{not json")
        with pytest.raises(SystemExit, match="not valid JSON"):
            main(
                [
                    "arms",
                    "--traffic",
                    str(FIXTURES / "sample-traffic.jsonl"),
                    "--criteria",
                    str(bad),
                    "--runs-dir",
                    str(tmp_path),
                ]
            )

    def test_rejudge_criteria_and_grades_conflict(self, tmp_path: Path) -> None:
        live = run_arms(_samples(), arms=_arms(), capture=True)
        assert live.transcript is not None
        t = live.transcript.save(tmp_path / "transcript.json")
        with pytest.raises(SystemExit, match="not both"):
            main(
                [
                    "rejudge",
                    str(t),
                    "--criteria",
                    str(EXAMPLE),
                    "--grades",
                    "x.csv",
                    "--grader",
                    "g1",
                ]
            )
