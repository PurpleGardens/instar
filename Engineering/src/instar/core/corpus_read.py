# SPDX-License-Identifier: Apache-2.0
"""Reading the measurement corpus: many runs at once, and checks on the judges.

:mod:`instar.core.corpus` writes one run at a time. This module reads them
back together, which is where the questions about the *measuring* get answered:

- **Is the judge drifting?** :func:`calibration` reports each judge's score on
  the same-model control arm, run by run. The control serves the baseline's own
  model, so a perfect judge would score it as the baseline's equal every time;
  whatever it scores instead is the judge's own error. With runs on more than
  one date, this is a drift report.
- **Is a difference real?** :func:`scores` compares every arm with the control
  under the same judge, sample by sample, and says "no measurable difference"
  when the gap is inside the noise band.

**The noise band.** Two measurements of the same thing differ by chance. The
band is how far apart they can land before chance stops being a good
explanation: ``z`` standard errors of the difference (``z = 2`` by default,
roughly a 95% band). Standard errors are computed over **samples**, not calls:
repeats of one prompt are not independent evidence, and counting them as if
they were makes every band look narrower than it is. So a fixture with few
samples gets a wide band, which is the honest answer for a small fixture.

**Age is always shown.** Every row says how old it is, so an old number cannot
pass for a current one.

Nothing here writes to the corpus.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from instar.core.corpus import KIND_ARMS, ROLE_BASELINE, ROLE_CONTROL, SCHEMA_VERSION
from instar.rubrics.base import JudgeKey

DEFAULT_Z = 2.0


@dataclass(frozen=True)
class CorpusRun:
    """One run directory: its run record, and the calls read on demand."""

    run_dir: Path
    record: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.record["run_id"])

    @property
    def kind(self) -> str:
        return str(self.record.get("kind", KIND_ARMS))

    @property
    def recorded_at(self) -> datetime:
        return _parse_time(str(self.record["recorded_at"]))

    @property
    def tenant_id(self) -> str:
        return str(self.record["tenant_id"])

    @property
    def workload_id(self) -> str | None:
        w = self.record.get("workload_id")
        return None if w is None else str(w)

    @property
    def source_run_id(self) -> str | None:
        s = self.record.get("source_run_id")
        return None if s is None else str(s)

    @property
    def judge(self) -> JudgeKey | None:
        j = self.record.get("judge")
        return None if j is None else JudgeKey.from_json(j)

    @property
    def mock(self) -> bool:
        return bool(self.record.get("mock", False))

    @property
    def rubric_version(self) -> str | None:
        v = self.record.get("rubric_version")
        return None if v is None else str(v)

    @property
    def gold_version(self) -> str | None:
        v = self.record.get("gold_version")
        return None if v is None else str(v)

    def calls(self) -> list[dict[str, Any]]:
        path = self.run_dir / "calls.jsonl"
        if not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def _parse_time(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def age_days(recorded_at: datetime, now: datetime) -> int:
    """Whole days between a record and ``now``; never negative."""
    return max(0, (now - recorded_at).days)


def iter_runs(corpus_dir: str | Path) -> list[CorpusRun]:
    """Every run in the corpus, oldest first.

    A corpus is ``<corpus>/<tenant>/<YYYY>/<MM>/<run_id>/run.json``. Anything
    else under the directory is ignored. A run written by a newer schema than
    this build understands is refused rather than half-read.
    """
    root = Path(corpus_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"no corpus at {root}")
    runs: list[CorpusRun] = []
    for run_json in sorted(root.glob("*/*/*/*/run.json")):
        record = json.loads(run_json.read_text(encoding="utf-8"))
        version = int(record.get("schema_version", 0))
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"{run_json.parent.name} uses corpus schema {version}; this build of "
                f"Instar reads up to {SCHEMA_VERSION}. Upgrade Instar to read it"
            )
        runs.append(CorpusRun(run_json.parent, record))
    runs.sort(key=lambda r: (r.recorded_at, r.run_id))
    return runs


@dataclass(frozen=True)
class CorpusFilter:
    """Which rows to read. ``None`` means "any"; dates are inclusive.

    Mock runs are left out unless asked for: they measure nothing, and a
    report that mixed them in would be quietly wrong.
    """

    tenant: str | None = None
    workload: str | None = None
    kind: str | None = None
    since: date | None = None
    until: date | None = None
    judge_family: str | None = None
    judge_model: str | None = None
    feature: str | None = None
    model: str | None = None
    role: str | None = None
    include_mock: bool = False

    def run_matches(self, run: CorpusRun) -> bool:
        if run.mock and not self.include_mock:
            return False
        if self.tenant is not None and run.tenant_id != self.tenant:
            return False
        if self.workload is not None and run.workload_id != self.workload:
            return False
        if self.kind is not None and run.kind != self.kind:
            return False
        day = run.recorded_at.date()
        if self.since is not None and day < self.since:
            return False
        if self.until is not None and day > self.until:
            return False
        if self.judge_family is not None or self.judge_model is not None:
            j = run.judge
            if j is None:
                return False
            if self.judge_family is not None and j.family != self.judge_family:
                return False
            if self.judge_model is not None and j.model != self.judge_model:
                return False
        return True

    def call_matches(self, rec: dict[str, Any]) -> bool:
        if self.feature is not None and rec.get("feature") != self.feature:
            return False
        if self.model is not None and self.model not in (
            rec.get("model_requested"),
            rec.get("model_served"),
        ):
            return False
        return self.role is None or rec.get("role") == self.role


def select_runs(runs: Iterable[CorpusRun], flt: CorpusFilter) -> list[CorpusRun]:
    return [r for r in runs if flt.run_matches(r)]


def select_calls(runs: Iterable[CorpusRun], flt: CorpusFilter) -> Iterator[dict[str, Any]]:
    for run in runs:
        if not flt.run_matches(run):
            continue
        for rec in run.calls():
            if flt.call_matches(rec):
                yield rec


def judge_label(judge: JudgeKey | None) -> str:
    """A short, stable name for a judge key, for tables."""
    if judge is None:
        return "unjudged"
    parts = [judge.kind]
    if judge.model:
        parts.append(judge.model)
    tags = [t for t in (judge.family, "blind" if judge.blind else None) if t]
    label = ":".join(parts)
    return f"{label} [{', '.join(tags)}]" if tags else label


# ── statistics over samples ──────────────────────────────────────────────


@dataclass(frozen=True)
class SampleStats:
    """A mean over calls, with its standard error computed over samples.

    ``se`` is ``None`` when there are fewer than two samples: one sample says
    nothing about how much another would differ.
    """

    n_calls: int
    n_samples: int
    mean: float
    se: float | None


def _by_sample(recs: Iterable[dict[str, Any]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for rec in recs:
        score = rec.get("score")
        if score is None:
            continue
        out.setdefault(str(rec.get("sample_id")), []).append(float(score))
    return out


def sample_stats(recs: Iterable[dict[str, Any]]) -> SampleStats | None:
    """Mean score over the scored calls, standard error over sample means."""
    by = _by_sample(recs)
    if not by:
        return None
    scores = [s for v in by.values() for s in v]
    means = [statistics.fmean(v) for v in by.values()]
    se = statistics.stdev(means) / math.sqrt(len(means)) if len(means) >= 2 else None
    return SampleStats(len(scores), len(means), statistics.fmean(scores), se)


def paired_difference(
    a: Iterable[dict[str, Any]], b: Iterable[dict[str, Any]]
) -> SampleStats | None:
    """Mean of (a - b) over the samples both arms scored, with its standard error.

    Pairing by sample removes the variation that comes from some prompts being
    harder than others, which both arms share.
    """
    by_a, by_b = _by_sample(a), _by_sample(b)
    shared = sorted(set(by_a) & set(by_b))
    if not shared:
        return None
    diffs = [statistics.fmean(by_a[s]) - statistics.fmean(by_b[s]) for s in shared]
    se = statistics.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) >= 2 else None
    n_calls = sum(len(by_a[s]) for s in shared)
    return SampleStats(n_calls, len(diffs), statistics.fmean(diffs), se)


# ── reports ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CalibrationRow:
    """One judge's score on the control arm in one run.

    ``noise_band`` is how far apart two independent runs under this judge can
    land by chance (``z * sqrt(2) * se``). A candidate gap smaller than it is
    not evidence of anything.
    """

    judge: str
    tenant_id: str
    workload_id: str | None
    run_id: str
    kind: str
    source_run_id: str | None
    recorded_at: datetime
    age_days: int
    rubric_version: str | None
    gold_version: str | None
    control: SampleStats
    noise_band: float | None

    def to_json(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "tenant_id": self.tenant_id,
            "workload_id": self.workload_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "source_run_id": self.source_run_id,
            "recorded_at": self.recorded_at.isoformat(timespec="seconds"),
            "age_days": self.age_days,
            "rubric_version": self.rubric_version,
            "gold_version": self.gold_version,
            "control_mean": self.control.mean,
            "control_se": self.control.se,
            "n_calls": self.control.n_calls,
            "n_samples": self.control.n_samples,
            "noise_band": self.noise_band,
        }


def calibration(
    runs: Iterable[CorpusRun],
    flt: CorpusFilter,
    *,
    now: datetime | None = None,
    z: float = DEFAULT_Z,
) -> list[CalibrationRow]:
    """Each judge's control-arm score, per run, oldest first within each judge.

    Runs with no judge or no scored control are skipped: there is nothing to
    calibrate. A feature filter narrows the control's calls; a model filter is
    ignored, because the control always serves the baseline's model.
    """
    now = now or datetime.now(UTC)
    # The control serves the baseline's model, so a model filter would drop it.
    ctrl_flt = replace(flt, role=ROLE_CONTROL, model=None)
    rows: list[CalibrationRow] = []
    for run in select_runs(runs, flt):
        if run.judge is None:
            continue
        stats = sample_stats(r for r in run.calls() if ctrl_flt.call_matches(r))
        if stats is None:
            continue
        band = None if stats.se is None else z * math.sqrt(2) * stats.se
        rows.append(
            CalibrationRow(
                judge=judge_label(run.judge),
                tenant_id=run.tenant_id,
                workload_id=run.workload_id,
                run_id=run.run_id,
                kind=run.kind,
                source_run_id=run.source_run_id,
                recorded_at=run.recorded_at,
                age_days=age_days(run.recorded_at, now),
                rubric_version=run.rubric_version,
                gold_version=run.gold_version,
                control=stats,
                noise_band=band,
            )
        )
    rows.sort(key=lambda r: (r.judge, r.recorded_at, r.run_id))
    return rows


def dates_per_judge(rows: Iterable[CalibrationRow]) -> dict[str, int]:
    """How many distinct days each judge was calibrated on. Drift needs two."""
    days: dict[str, set[date]] = {}
    for r in rows:
        days.setdefault(r.judge, set()).add(r.recorded_at.date())
    return {j: len(d) for j, d in days.items()}


VERDICT_NO_DIFFERENCE = "no measurable difference"
VERDICT_BETTER = "better than control"
VERDICT_WORSE = "worse than control"
VERDICT_UNKNOWN = "too few samples to say"


@dataclass(frozen=True)
class ScoreRow:
    """One arm's score in one run, read against that run's control."""

    judge: str
    run_id: str
    recorded_at: datetime
    age_days: int
    arm: str
    model: str | None
    score: SampleStats
    control_mean: float | None
    relative_to_control: float | None
    gap: SampleStats | None
    band: float | None
    verdict: str

    def to_json(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "run_id": self.run_id,
            "recorded_at": self.recorded_at.isoformat(timespec="seconds"),
            "age_days": self.age_days,
            "arm": self.arm,
            "model": self.model,
            "mean": self.score.mean,
            "n_calls": self.score.n_calls,
            "n_samples": self.score.n_samples,
            "control_mean": self.control_mean,
            "relative_to_control": self.relative_to_control,
            "gap_to_control": None if self.gap is None else self.gap.mean,
            "band": self.band,
            "verdict": self.verdict,
        }


def _verdict(gap: SampleStats | None, z: float) -> tuple[float | None, str]:
    if gap is None or gap.se is None:
        return None, VERDICT_UNKNOWN
    band = z * gap.se
    if abs(gap.mean) <= band:
        return band, VERDICT_NO_DIFFERENCE
    return band, VERDICT_BETTER if gap.mean > 0 else VERDICT_WORSE


def scores(
    runs: Iterable[CorpusRun],
    flt: CorpusFilter,
    *,
    now: datetime | None = None,
    z: float = DEFAULT_Z,
) -> list[ScoreRow]:
    """Every judged, non-baseline arm in every matching run, against the control.

    The gap is paired by sample; its band is ``z`` standard errors of that
    paired difference. Runs without a control get :data:`VERDICT_UNKNOWN`:
    without the judge's own error there is nothing to read the score against.
    """
    now = now or datetime.now(UTC)
    rows: list[ScoreRow] = []
    for run in select_runs(runs, flt):
        if run.judge is None:
            continue
        # Only the feature filter narrows the calls: model and role choose which
        # arms are *reported*, and must never drop the control they are read against.
        calls = [r for r in run.calls() if flt.feature is None or r.get("feature") == flt.feature]
        by_arm: dict[str, list[dict[str, Any]]] = {}
        for rec in calls:
            by_arm.setdefault(str(rec["arm"]), []).append(rec)
        control_recs = [r for r in calls if r.get("role") == ROLE_CONTROL]
        control = sample_stats(control_recs)
        for arm, recs in sorted(by_arm.items()):
            role = recs[0].get("role")
            if role == ROLE_BASELINE:
                continue
            if flt.role is not None and role != flt.role:
                continue
            if flt.model is not None and flt.model not in (
                recs[0].get("model_requested"),
                recs[0].get("model_served"),
            ):
                continue
            stats = sample_stats(recs)
            if stats is None:
                continue
            is_control = role == ROLE_CONTROL
            gap = None if is_control or control is None else paired_difference(recs, control_recs)
            band, verdict = (None, "control") if is_control else _verdict(gap, z)
            rel = (
                stats.mean / control.mean
                if control is not None and control.mean > 0 and not is_control
                else None
            )
            rows.append(
                ScoreRow(
                    judge=judge_label(run.judge),
                    run_id=run.run_id,
                    recorded_at=run.recorded_at,
                    age_days=age_days(run.recorded_at, now),
                    arm=arm,
                    model=recs[0].get("model_requested"),
                    score=stats,
                    control_mean=None if control is None else control.mean,
                    relative_to_control=rel,
                    gap=gap,
                    band=band,
                    verdict=verdict,
                )
            )
    return rows
