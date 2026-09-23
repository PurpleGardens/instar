# SPDX-License-Identifier: Apache-2.0
"""Reading the corpus across runs: filters, judge calibration, noise-band verdicts.

What these tests protect, in order of how badly a silent failure would hurt:

- a report never mixes in mock runs unless asked;
- standard errors are computed over samples, not calls, so repeats of one
  prompt cannot make a band look narrower than it is;
- a model filter never drops the control an arm is read against;
- a gap inside the band is reported as "no measurable difference";
- every row carries its age.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from instar.cli.main import main
from instar.core.arms import Arm, run_arms
from instar.core.corpus import SCHEMA_VERSION, RecordContext, write_run
from instar.core.corpus_read import (
    VERDICT_NO_DIFFERENCE,
    VERDICT_UNKNOWN,
    VERDICT_WORSE,
    CorpusFilter,
    age_days,
    calibration,
    dates_per_judge,
    iter_runs,
    judge_label,
    paired_difference,
    sample_stats,
    scores,
    select_calls,
)
from instar.core.traffic import TrafficSample
from instar.providers.mock import MockBackend
from instar.rubrics.base import JudgeKey
from instar.rubrics.judges import MockJudge

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
OPENAI = {"kind": "blind-pairwise", "model": "gpt-x", "family": "openai", "blind": True}
ANTHROPIC = {"kind": "blind-pairwise", "model": "claude-x", "family": "anthropic", "blind": True}


def _rec(sample: str, arm: str, role: str, model: str, score: float | None) -> dict[str, Any]:
    return {
        "sample_id": sample,
        "feature": "f." + sample,
        "arm": arm,
        "role": role,
        "model_requested": model,
        "model_served": model,
        "score": score,
    }


def _write(
    root: Path,
    run_id: str,
    when: str,
    calls: list[dict[str, Any]],
    *,
    judge: dict[str, Any] | None = OPENAI,
    tenant: str = "t1",
    kind: str = "arms",
    mock: bool = False,
    schema: int = SCHEMA_VERSION,
) -> Path:
    d = root / tenant / when[:4] / when[5:7] / run_id
    d.mkdir(parents=True)
    record = {
        "schema_version": schema,
        "run_id": run_id,
        "kind": kind,
        "recorded_at": when,
        "source_run_id": None,
        "tenant_id": tenant,
        "workload_id": "w1",
        "judge": judge,
        "mock": mock,
        "n_records": len(calls),
    }
    (d / "run.json").write_text(json.dumps(record))
    (d / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    return d


def _typical_calls(cand: dict[str, float], ctl: dict[str, float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in sorted(ctl):
        out.append(_rec(s, "base", "baseline", "strong", None))
        out.append(_rec(s, "ctl", "control", "strong", ctl[s]))
        out.append(_rec(s, "cheap", "candidate", "weak", cand[s]))
    return out


class TestLoading:
    def test_runs_come_back_oldest_first(self, tmp_path: Path) -> None:
        _write(tmp_path, "r2", "2026-10-02T00:00:00+00:00", [])
        _write(tmp_path, "r1", "2026-09-01T00:00:00+00:00", [])
        assert [r.run_id for r in iter_runs(tmp_path)] == ["r1", "r2"]

    def test_stray_files_are_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, "r1", "2026-09-01T00:00:00+00:00", [])
        (tmp_path / "notes.txt").write_text("hi")
        (tmp_path / "t1" / "README.json").write_text("{}")
        assert len(iter_runs(tmp_path)) == 1

    def test_a_newer_schema_is_refused(self, tmp_path: Path) -> None:
        _write(tmp_path, "r1", "2026-09-01T00:00:00+00:00", [], schema=SCHEMA_VERSION + 1)
        with pytest.raises(ValueError, match="Upgrade Instar"):
            iter_runs(tmp_path)

    def test_missing_corpus(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            iter_runs(tmp_path / "nope")

    def test_age_is_whole_days_and_never_negative(self) -> None:
        assert age_days(datetime(2026, 10, 1, tzinfo=UTC), NOW) == 20
        assert age_days(datetime(2026, 11, 1, tzinfo=UTC), NOW) == 0


class TestFilters:
    def _corpus(self, root: Path) -> None:
        calls = _typical_calls({"a": 1.0, "b": 0.5}, {"a": 1.0, "b": 1.0})
        _write(root, "r1", "2026-09-01T00:00:00+00:00", calls)
        _write(root, "r2", "2026-10-01T00:00:00+00:00", calls, judge=ANTHROPIC, kind="rejudge")
        _write(root, "r3", "2026-10-02T00:00:00+00:00", calls, tenant="t2")
        _write(root, "m1", "2026-10-03T00:00:00+00:00", calls, mock=True)

    def _ids(self, root: Path, **kw: Any) -> list[str]:
        from instar.core.corpus_read import select_runs

        return [r.run_id for r in select_runs(iter_runs(root), CorpusFilter(**kw))]

    def test_mock_runs_are_left_out_by_default(self, tmp_path: Path) -> None:
        self._corpus(tmp_path)
        assert "m1" not in self._ids(tmp_path)
        assert "m1" in self._ids(tmp_path, include_mock=True)

    def test_run_level_filters(self, tmp_path: Path) -> None:
        self._corpus(tmp_path)
        assert self._ids(tmp_path, tenant="t2") == ["r3"]
        assert self._ids(tmp_path, kind="rejudge") == ["r2"]
        assert self._ids(tmp_path, judge_family="anthropic") == ["r2"]
        assert self._ids(tmp_path, judge_model="gpt-x") == ["r1", "r3"]
        assert self._ids(tmp_path, since=date(2026, 10, 1), until=date(2026, 10, 1)) == ["r2"]

    def test_call_level_filters(self, tmp_path: Path) -> None:
        self._corpus(tmp_path)
        runs = iter_runs(tmp_path)
        assert len(list(select_calls(runs, CorpusFilter(tenant="t1", role="control")))) == 4
        assert len(list(select_calls(runs, CorpusFilter(tenant="t1", model="weak")))) == 4
        assert len(list(select_calls(runs, CorpusFilter(tenant="t1", feature="f.a")))) == 6


class TestStatistics:
    def test_se_is_over_samples_not_calls(self) -> None:
        # Ten identical repeats of one sample and ten of another: two samples,
        # not twenty independent calls.
        recs = [_rec("a", "x", "control", "m", 1.0)] * 10 + [
            _rec("b", "x", "control", "m", 0.0)
        ] * 10
        st = sample_stats(recs)
        assert st is not None
        assert (st.n_calls, st.n_samples, st.mean) == (20, 2, 0.5)
        assert st.se == pytest.approx(math.sqrt(0.5) / math.sqrt(2))

    def test_one_sample_has_no_se(self) -> None:
        st = sample_stats([_rec("a", "x", "control", "m", 1.0)] * 3)
        assert st is not None and st.se is None

    def test_unscored_calls_are_skipped(self) -> None:
        assert sample_stats([_rec("a", "x", "baseline", "m", None)]) is None

    def test_paired_difference_uses_shared_samples_only(self) -> None:
        a = [_rec("a", "x", "candidate", "m", 1.0), _rec("b", "x", "candidate", "m", 0.0)]
        b = [_rec("a", "y", "control", "m", 1.0), _rec("c", "y", "control", "m", 1.0)]
        d = paired_difference(a, b)
        assert d is not None and (d.n_samples, d.mean, d.se) == (1, 0.0, None)

    def test_judge_label(self) -> None:
        assert judge_label(None) == "unjudged"
        assert judge_label(JudgeKey("mock")) == "mock"
        assert judge_label(JudgeKey.from_json(OPENAI)) == "blind-pairwise:gpt-x [openai, blind]"


class TestCalibration:
    def test_one_row_per_judged_run_with_age_and_band(self, tmp_path: Path) -> None:
        ctl = {"a": 1.0, "b": 0.5, "c": 1.0, "d": 0.5}
        calls = _typical_calls(dict.fromkeys(ctl, 0.0), ctl)
        _write(tmp_path, "r1", "2026-09-21T00:00:00+00:00", calls)
        _write(tmp_path, "r2", "2026-10-21T00:00:00+00:00", calls)
        _write(tmp_path, "u1", "2026-10-21T00:00:00+00:00", calls, judge=None)
        rows = calibration(iter_runs(tmp_path), CorpusFilter(), now=NOW)
        assert [r.run_id for r in rows] == ["r1", "r2"]
        assert [r.age_days for r in rows] == [30, 0]
        r = rows[0]
        assert r.control.mean == 0.75 and r.control.n_samples == 4
        assert r.control.se is not None
        assert r.noise_band == pytest.approx(2 * math.sqrt(2) * r.control.se)
        assert dates_per_judge(rows) == {judge_label(JudgeKey.from_json(OPENAI)): 2}

    def test_a_model_filter_does_not_drop_the_control(self, tmp_path: Path) -> None:
        calls = _typical_calls({"a": 0.0, "b": 0.0}, {"a": 1.0, "b": 0.5})
        _write(tmp_path, "r1", "2026-10-01T00:00:00+00:00", calls)
        assert len(calibration(iter_runs(tmp_path), CorpusFilter(model="weak"), now=NOW)) == 1

    def test_runs_without_a_control_are_skipped(self, tmp_path: Path) -> None:
        calls = [c for c in _typical_calls({"a": 1.0}, {"a": 1.0}) if c["role"] != "control"]
        _write(tmp_path, "r1", "2026-10-01T00:00:00+00:00", calls)
        assert calibration(iter_runs(tmp_path), CorpusFilter(), now=NOW) == []


class TestScores:
    def test_a_gap_inside_the_band_is_no_difference(self, tmp_path: Path) -> None:
        ctl = {"a": 1.0, "b": 0.5, "c": 1.0, "d": 0.5}
        cand = {"a": 0.5, "b": 1.0, "c": 1.0, "d": 0.5}  # noisy, mean gap 0
        _write(tmp_path, "r1", "2026-10-01T00:00:00+00:00", _typical_calls(cand, ctl))
        rows = {r.arm: r for r in scores(iter_runs(tmp_path), CorpusFilter(), now=NOW)}
        assert set(rows) == {"cheap", "ctl"}  # the baseline is not judged
        assert rows["cheap"].verdict == VERDICT_NO_DIFFERENCE
        assert rows["ctl"].verdict == "control"
        assert rows["cheap"].age_days == 20

    def test_a_consistent_loss_is_worse(self, tmp_path: Path) -> None:
        ctl = {"a": 1.0, "b": 1.0, "c": 0.9, "d": 1.0}
        cand = {"a": 0.1, "b": 0.0, "c": 0.1, "d": 0.2}
        _write(tmp_path, "r1", "2026-10-01T00:00:00+00:00", _typical_calls(cand, ctl))
        (row,) = scores(iter_runs(tmp_path), CorpusFilter(role="candidate"), now=NOW)
        assert row.verdict == VERDICT_WORSE
        assert row.gap is not None and row.gap.mean == pytest.approx(-0.875)
        assert row.relative_to_control == pytest.approx(0.1 / 0.975)

    def test_a_model_filter_keeps_the_control_to_read_against(self, tmp_path: Path) -> None:
        ctl = {"a": 1.0, "b": 1.0, "c": 0.9, "d": 1.0}
        cand = {"a": 0.1, "b": 0.0, "c": 0.1, "d": 0.2}
        _write(tmp_path, "r1", "2026-10-01T00:00:00+00:00", _typical_calls(cand, ctl))
        (row,) = scores(iter_runs(tmp_path), CorpusFilter(model="weak"), now=NOW)
        assert row.arm == "cheap" and row.verdict == VERDICT_WORSE

    def test_no_control_means_no_verdict(self, tmp_path: Path) -> None:
        calls = _typical_calls({"a": 1.0, "b": 0.0}, {"a": 1.0, "b": 1.0})
        calls = [c for c in calls if c["role"] != "control"]
        _write(tmp_path, "r1", "2026-10-01T00:00:00+00:00", calls)
        (row,) = scores(iter_runs(tmp_path), CorpusFilter(), now=NOW)
        assert row.verdict == VERDICT_UNKNOWN and row.relative_to_control is None


class TestEndToEnd:
    def _corpus(self, root: Path) -> None:
        samples = [
            TrafficSample(id=f"s{i}", feature="demo", messages=[{"role": "user", "content": "q"}])
            for i in range(4)
        ]
        arms = [
            Arm("base", MockBackend("base", latency_s=0.001), "mock-strong"),
            Arm("cheap", MockBackend("cheap", latency_s=0.001), "mock-weak"),
            Arm("ctl", MockBackend("ctl", latency_s=0.001), "mock-strong", is_control=True),
        ]
        result = run_arms(samples, arms=arms, repeats=2, judge=MockJudge(), capture=True)
        assert result.transcript is not None
        ctx = RecordContext(tenant_id="t1", workload_id="demo", mock=True)
        for when in (datetime(2026, 9, 21, tzinfo=UTC), datetime(2026, 10, 21, tzinfo=UTC)):
            write_run(root, result, result.transcript, ctx, now=when)

    def test_written_runs_read_back(self, tmp_path: Path) -> None:
        self._corpus(tmp_path)
        runs = iter_runs(tmp_path)
        assert len(runs) == 2
        assert calibration(runs, CorpusFilter(), now=NOW) == []  # mock left out
        rows = calibration(runs, CorpusFilter(include_mock=True), now=NOW)
        assert [r.age_days for r in rows] == [30, 0]
        assert all(r.control.n_samples == 4 and r.control.n_calls == 8 for r in rows)

    def test_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._corpus(tmp_path)
        assert main(["corpus", "runs", str(tmp_path)]) == 0
        assert "no runs match" in capsys.readouterr().out

        assert main(["corpus", "runs", str(tmp_path), "--include-mock"]) == 0
        out = capsys.readouterr().out
        assert "2 runs" in out and "days old" in out

        assert main(["corpus", "calibration", str(tmp_path), "--include-mock"]) == 0
        out = capsys.readouterr().out
        assert "band (z=2)" in out and "drift:" not in out

        assert main(["corpus", "scores", str(tmp_path), "--include-mock", "--json"]) == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert {r["arm"] for r in rows} == {"cheap", "ctl"}
        assert all("age_days" in r and "verdict" in r for r in rows)

        assert main(["corpus", "calls", str(tmp_path), "--include-mock", "--count"]) == 0
        assert capsys.readouterr().out.strip() == str(2 * 4 * 2 * 3)

    def test_bad_date_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            main(["corpus", "runs", str(tmp_path), "--since", "last week"])

    def test_missing_corpus_is_a_clean_error(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="no corpus"):
            main(["corpus", "runs", str(tmp_path / "nope")])
