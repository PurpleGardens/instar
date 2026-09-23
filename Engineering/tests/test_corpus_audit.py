# SPDX-License-Identifier: Apache-2.0
"""Auditing the instrument: rubric archaeology, the label-ceiling detector, looks.

What these tests protect, in order of how badly a silent failure would hurt:

- an unknown cost is never scored as a saving;
- a dimension that fails alongside another still counts as binding, so it is
  never reported as a deletion candidate just because it had company;
- a control arm is not counted as a second model agreeing against gold;
- a split role is validated and survives into the corpus.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from instar.cli.main import main
from instar.core.corpus import RecordContext, write_run
from instar.core.corpus_read import (
    CorpusFilter,
    iter_runs,
    label_ceiling,
    looks,
    rubric_archaeology,
)
from instar.core.traffic import TrafficSample
from instar.core.transcript import Transcript, TranscriptEntry
from instar.providers.base import CompletionResult
from instar.rubrics.judges import extract_label, normalize_labels
from instar.rubrics.spec import (
    FAIL,
    MARGINAL,
    PASS,
    UNMEASURED,
    ArmView,
    Dimension,
    Rubric,
    binding_dimensions,
    sole_deciders,
)

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
LABELS = ["admin", "personal"]
JUDGE = {"kind": "blind-pairwise", "model": "gpt-x", "family": "openai", "blind": True}


def _arm(name: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "model": name + "-model",
        "n_err": 0,
        "p95_ms": 1000.0,
        "cost_per_1k_calls_usd": 10.0,
        "cost_source": "provider_reported",
        "quality_mean": None,
        "quality_scores": [],
    }
    return {**base, **kw}


def _rubric(*dims: dict[str, Any]) -> Rubric:
    return Rubric.from_dict({"name": "r", "dimensions": list(dims)})


QUALITY = {
    "id": "q",
    "metric": "arm.quality_vs_control",
    "pass_at": 0.95,
    "marginal_at": 0.85,
}
SAVINGS = {"id": "s", "metric": "arm.cost_saved_pct", "pass_at": 50.0}
ERRORS = {"id": "e", "metric": "arm.error_count", "direction": "lower_is_better", "pass_at": 0}


class TestArmMetrics:
    def test_rubrics_accept_arm_metrics_and_still_refuse_typos(self) -> None:
        Dimension(id="x", metric="arm.quality_min", direction="higher_is_better", pass_at=1)
        with pytest.raises(ValueError, match="unknown metric"):
            Dimension(id="x", metric="arm.qualty_min", direction="higher_is_better", pass_at=1)

    def test_quality_is_read_against_the_control(self) -> None:
        view = ArmView(
            _arm("cheap", quality_mean=0.8, quality_scores=[1.0, 0.5, 0.9]),
            _arm("base"),
            _arm("ctl", quality_mean=0.8),
        )
        v = _rubric(QUALITY, {"id": "m", "metric": "arm.quality_min", "pass_at": 0.5}).evaluate_arm(
            view
        )
        assert [(d.value, d.verdict) for d in v.dimensions] == [(1.0, PASS), (0.5, PASS)]

    def test_no_control_means_quality_is_unmeasured(self) -> None:
        v = _rubric(QUALITY).evaluate_arm(ArmView(_arm("cheap", quality_mean=0.9), _arm("base")))
        assert v.dimensions[0].verdict == UNMEASURED

    def test_savings(self) -> None:
        view = ArmView(_arm("cheap", cost_per_1k_calls_usd=2.0), _arm("base"))
        (d,) = _rubric(SAVINGS).evaluate_arm(view).dimensions
        assert d.value == pytest.approx(80.0) and d.verdict == PASS

    def test_an_unknown_cost_is_never_a_saving(self) -> None:
        unknown = _arm("base", cost_source="unavailable", cost_per_1k_calls_usd=0.0)
        view = ArmView(_arm("cheap", cost_per_1k_calls_usd=2.0), unknown)
        assert _rubric(SAVINGS).evaluate_arm(view).dimensions[0].verdict == UNMEASURED

    def test_a_routing_metric_on_an_arm_is_unmeasured(self) -> None:
        rub = _rubric({"id": "r", "metric": "quality.routed_weak_mean", "pass_at": 0.9})
        assert rub.evaluate_arm(ArmView(_arm("c"), _arm("b"))).verdict == UNMEASURED

    def test_failed_calls_are_noted(self) -> None:
        v = _rubric(ERRORS).evaluate_arm(ArmView(_arm("c", n_err=2), _arm("b")))
        assert v.verdict == FAIL and "2 calls failed" in v.notes[0]


class TestBinding:
    def _verdict(self, q: float, cost: float, errs: int) -> Any:
        view = ArmView(
            _arm("c", quality_mean=q, cost_per_1k_calls_usd=cost, n_err=errs),
            _arm("b"),
            _arm("ctl", quality_mean=1.0),
        )
        return _rubric(QUALITY, SAVINGS, ERRORS).evaluate_arm(view)

    def test_a_pass_has_no_binding_dimension(self) -> None:
        v = self._verdict(1.0, 1.0, 0)
        assert v.verdict == PASS and binding_dimensions(v) == [] and sole_deciders(v) == []

    def test_a_lone_failure_is_binding_and_the_sole_decider(self) -> None:
        v = self._verdict(1.0, 9.0, 0)
        assert binding_dimensions(v) == ["s"] and sole_deciders(v) == ["s"]

    def test_two_failures_both_bind_neither_decides_alone(self) -> None:
        v = self._verdict(1.0, 9.0, 3)
        assert binding_dimensions(v) == ["s", "e"] and sole_deciders(v) == []

    def test_a_marginal_under_a_fail_is_not_binding(self) -> None:
        v = self._verdict(0.9, 9.0, 0)
        assert v.dimensions[0].verdict == MARGINAL
        assert binding_dimensions(v) == ["s"] and sole_deciders(v) == ["s"]


def _write_arms_run(
    root: Path, run_id: str, when: str, arms: list[dict[str, Any]], **extra: Any
) -> Path:
    d = root / "t1" / when[:4] / when[5:7] / run_id
    d.mkdir(parents=True)
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "kind": "arms",
        "recorded_at": when,
        "tenant_id": "t1",
        "workload_id": "w1",
        "judge": JUDGE,
        "mock": False,
        "baseline": "b",
        "control": "ctl",
        "arms": arms,
        **extra,
    }
    (d / "run.json").write_text(json.dumps(record))
    (d / "calls.jsonl").write_text("")
    return d


class TestArchaeology:
    def test_counts_binding_ranges_and_never_binding(self, tmp_path: Path) -> None:
        ctl = _arm("ctl", quality_mean=1.0)
        # run 1: savings fails alone; run 2: everything passes
        _write_arms_run(
            tmp_path,
            "r1",
            "2026-10-01T00:00:00+00:00",
            [_arm("b"), ctl, _arm("c", quality_mean=1.0, cost_per_1k_calls_usd=9.0)],
        )
        _write_arms_run(
            tmp_path,
            "r2",
            "2026-10-11T00:00:00+00:00",
            [_arm("b"), ctl, _arm("c", quality_mean=0.97, cost_per_1k_calls_usd=1.0)],
        )
        rows, overall = rubric_archaeology(
            iter_runs(tmp_path), CorpusFilter(), _rubric(QUALITY, SAVINGS, ERRORS), now=NOW
        )
        by = {h.dimension: h for h in rows}
        assert overall == {PASS: 1, MARGINAL: 0, FAIL: 1, UNMEASURED: 0}
        assert (by["s"].binding, by["s"].sole, by["s"].never_binding) == (1, 1, False)
        assert by["q"].never_binding and by["e"].never_binding
        assert (by["q"].lo, by["q"].hi) == (pytest.approx(0.97), 1.0)
        assert (by["s"].newest_days, by["s"].oldest_days) == (10, 20)
        assert all(h.n == 2 for h in rows)  # baseline and control are not candidates

    def test_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ctl = _arm("ctl", quality_mean=1.0)
        arms = [_arm("b"), ctl, _arm("c", quality_mean=1.0, cost_per_1k_calls_usd=1.0)]
        _write_arms_run(tmp_path, "r1", "2026-10-01T00:00:00+00:00", arms)
        rubric = tmp_path / "r.json"
        rubric.write_text(json.dumps({"name": "r", "dimensions": [QUALITY, SAVINGS]}))
        assert main(["corpus", "rubric", str(tmp_path / "t1"), "--rubric", str(rubric)]) == 0
        # a tenant directory is not a corpus root: nothing there
        assert "no candidate arms" in capsys.readouterr().out
        assert main(["corpus", "rubric", str(tmp_path), "--rubric", str(rubric)]) == 0
        out = capsys.readouterr().out
        assert "never binding under any judge so far: q, s" in out

    def test_a_bad_rubric_is_a_clean_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "r.json"
        bad.write_text(json.dumps({"name": "r", "dimensions": [{"id": "x", "metric": "nope"}]}))
        with pytest.raises(SystemExit):
            main(["corpus", "rubric", str(tmp_path), "--rubric", str(bad)])


def _entry(sid: str, gold: str, answers: dict[str, str], repeat: int = 0) -> TranscriptEntry:
    sample = TrafficSample(
        id=sid, feature="cal", messages=[{"role": "user", "content": "q"}], meta={"gold": gold}
    )
    completions = {
        arm: CompletionResult(text=t, model=arm, input_tokens=1, output_tokens=1, latency_s=0.1)
        for arm, t in answers.items()
    }
    return TranscriptEntry(sample=sample, completions=completions, repeat=repeat)


def _labels_run(root: Path, entries: list[TranscriptEntry]) -> None:
    tr = Transcript(
        baseline="a",
        arm_models={"a": "model-a", "b": "model-b", "c": "model-c", "ctl": "model-a"},
        entries=entries,
        control="ctl",
    )
    d = _write_arms_run(root, "r1", "2026-10-01T00:00:00+00:00", [])
    tr.save(d / "transcript.json")


class TestLabelCeiling:
    def test_extract_label_prefers_the_longest(self) -> None:
        labels = normalize_labels(["access", "account_access"])
        assert extract_label("It's ACCOUNT_ACCESS.", labels) == "account_access"
        assert extract_label("no idea", labels) is None

    def test_models_agreeing_against_gold_are_flagged(self, tmp_path: Path) -> None:
        _labels_run(
            tmp_path,
            [
                _entry(
                    "s1",
                    "admin",
                    {"a": "personal", "b": "personal", "c": "admin", "ctl": "personal"},
                ),
                _entry("s2", "admin", {"a": "admin", "b": "admin", "c": "admin", "ctl": "admin"}),
            ],
        )
        rep = label_ceiling(iter_runs(tmp_path), CorpusFilter(), labels=LABELS, now=NOW)
        (flag,) = rep.flags
        assert (flag.sample_id, flag.gold, flag.consensus) == ("s1", "admin", "personal")
        assert flag.agreeing == ("model-a", "model-b") and flag.n_models == 3
        assert rep.examined == 2
        assert rep.accuracy == {"model-a": (1, 2), "model-b": (1, 2), "model-c": (2, 2)}

    def test_a_control_is_not_a_second_opinion(self, tmp_path: Path) -> None:
        # Only model-a (via its arm and its control) says "personal".
        _labels_run(
            tmp_path,
            [
                _entry(
                    "s1", "admin", {"a": "personal", "b": "admin", "c": "admin", "ctl": "personal"}
                )
            ],
        )
        assert (
            label_ceiling(iter_runs(tmp_path), CorpusFilter(), labels=LABELS, now=NOW).flags == []
        )

    def test_majority_over_repeats_and_consistency(self, tmp_path: Path) -> None:
        answers = [("personal", "personal"), ("personal", "personal"), ("admin", "personal")]
        _labels_run(
            tmp_path,
            [
                _entry("s1", "admin", {"a": a, "b": b, "c": "admin", "ctl": a}, repeat=i)
                for i, (a, b) in enumerate(answers)
            ],
        )
        rep = label_ceiling(iter_runs(tmp_path), CorpusFilter(), labels=LABELS, now=NOW)
        (flag,) = rep.flags
        # model-a answers through its arm and its control: personal on 4 of 6
        assert flag.agreeing == ("model-a", "model-b")
        assert flag.consistency == pytest.approx(4 / 6)

    def test_labels_default_to_the_gold_values(self, tmp_path: Path) -> None:
        # "personal" is never a gold label here, so without --labels it can't be read.
        _labels_run(
            tmp_path,
            [_entry("s1", "admin", {"a": "personal", "b": "personal", "c": "admin", "ctl": "x"})],
        )
        assert label_ceiling(iter_runs(tmp_path), CorpusFilter(), now=NOW).flags == []

    def test_min_models(self, tmp_path: Path) -> None:
        _labels_run(
            tmp_path,
            [_entry("s1", "admin", {"a": "personal", "b": "personal", "c": "admin", "ctl": "x"})],
        )
        rep = label_ceiling(
            iter_runs(tmp_path), CorpusFilter(), labels=LABELS, min_models=3, now=NOW
        )
        assert rep.flags == []

    def test_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _labels_run(
            tmp_path,
            [_entry("s1", "admin", {"a": "personal", "b": "personal", "c": "admin", "ctl": "x"})],
        )
        assert main(["corpus", "labels", str(tmp_path), "--labels", "admin,personal"]) == 0
        out = capsys.readouterr().out
        assert "1 flag(s) across 1 gold-labelled sample(s)" in out and "model-c" in out


class TestSplitRoleAndLooks:
    def test_split_role_is_validated(self) -> None:
        with pytest.raises(ValueError, match="split_role"):
            RecordContext(tenant_id="t", split_role="training")
        ctx = RecordContext(tenant_id="t", split_role="standard")
        assert RecordContext.from_json(ctx.to_json()).split_role == "standard"

    def test_looks_counts_arms_and_rejudges_per_gold_version(self, tmp_path: Path) -> None:
        _write_arms_run(tmp_path, "r1", "2026-10-01T00:00:00+00:00", [], gold_version="v1")
        _write_arms_run(
            tmp_path, "r2", "2026-10-02T00:00:00+00:00", [], gold_version="v1",
            split_role="standard", kind="rejudge",
        )  # fmt: skip
        _write_arms_run(tmp_path, "r3", "2026-10-11T00:00:00+00:00", [], gold_version="v2")
        rows = looks(iter_runs(tmp_path), CorpusFilter(), now=NOW)
        assert [(r.gold_version, r.arms_runs, r.rejudge_runs) for r in rows] == [
            ("v1", 1, 1),
            ("v2", 1, 0),
        ]
        assert rows[0].split_roles == ("standard", "unset") and rows[0].age_days == 19

    def test_arms_records_the_split_role(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        rc = main(
            [
                "arms", "--corpus", str(corpus), "--tenant", "t1", "--split-role", "standard",
                "--runs-dir", str(tmp_path / "runs"), "--label", "x",
            ]
        )  # fmt: skip
        assert rc == 0
        (run,) = iter_runs(corpus)
        assert run.split_role == "standard"

    def test_looks_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write_arms_run(tmp_path, "r1", "2026-10-01T00:00:00+00:00", [])
        assert main(["corpus", "looks", str(tmp_path)]) == 0
        assert "no --split-role recorded" in capsys.readouterr().out


def test_written_corpus_round_trip(tmp_path: Path) -> None:
    """A real write_run output is readable by every audit reader."""
    from instar.core.arms import Arm, run_arms
    from instar.providers.mock import MockBackend
    from instar.rubrics.judges import MockJudge

    samples = [
        TrafficSample(id=f"s{i}", feature="d", messages=[{"role": "user", "content": "q"}])
        for i in range(3)
    ]
    arms = [
        Arm("base", MockBackend("base", latency_s=0.001), "mock-strong"),
        Arm("cheap", MockBackend("cheap", latency_s=0.001), "mock-weak"),
        Arm("ctl", MockBackend("ctl", latency_s=0.001), "mock-strong", is_control=True),
    ]
    result = run_arms(samples, arms=arms, judge=MockJudge(), capture=True)
    assert result.transcript is not None
    ctx = RecordContext(tenant_id="t1", mock=True, split_role="evolve")
    write_run(tmp_path, result, result.transcript, ctx, now=NOW)
    flt = CorpusFilter(include_mock=True)
    rows, overall = rubric_archaeology(iter_runs(tmp_path), flt, _rubric(QUALITY, ERRORS), now=NOW)
    assert sum(overall.values()) == 1 and {h.dimension for h in rows} == {"q", "e"}
    assert label_ceiling(iter_runs(tmp_path), flt, now=NOW).examined == 0  # no gold
    assert looks(iter_runs(tmp_path), flt, now=NOW)[0].split_roles == ("evolve",)
