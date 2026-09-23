# SPDX-License-Identifier: Apache-2.0
"""Rubrics — turning a measurement into a decision.

A **judge** answers "how good was this one answer?". A **rubric** answers the
question the judge cannot: *"given everything we measured, does this
configuration meet the bar we set?"*

The distinction matters because those are different jobs owned by different
people. Dimensions and thresholds are a business decision — what "good enough"
means, and who has to sign off on it. A rubric writes that decision down
**before** the run, so the run cannot be reinterpreted afterwards to say what
someone hoped it would say. That is the entire point: a threshold agreed in
advance is a standard; a threshold chosen after seeing the numbers is a
rationalization.

A rubric is therefore:

- a list of **dimensions**, each binding one measured metric,
- each with a **direction** (higher or lower is better),
- each with **pass** and **marginal** thresholds,
- evaluated to a per-dimension verdict and one overall verdict.

Two deliberate design choices, both about not fooling yourself:

1. **The overall verdict is the worst dimension, never an average.** Averaging
   lets a spectacular cost saving hide a failing quality score, which is exactly
   the mistake this tool exists to prevent. A configuration that fails on any
   dimension you said mattered has failed.
2. **An unmeasurable dimension is never a pass.** If a run routed nothing to the
   weak model, the routed-weak quality dimension has no value to check — it
   returns ``UNMEASURED``, which taints the overall verdict rather than being
   quietly skipped. Silence is not evidence.

Instar ships the rubric *framework*, not a library of rubrics. What "good" means
for your marketing copy or your support triage is yours to define, and the
example under ``Engineering/fixtures/rubrics/`` is an illustration, not a
recommendation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from instar.core.route import RouteResult

# Verdicts, ordered worst to best. The order is load-bearing: the overall
# verdict is the minimum, so this tuple defines what "worst" means.
FAIL = "fail"
UNMEASURED = "unmeasured"
MARGINAL = "marginal"
PASS = "pass"

VERDICT_ORDER = (FAIL, UNMEASURED, MARGINAL, PASS)

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"
DIRECTIONS = frozenset({HIGHER_IS_BETTER, LOWER_IS_BETTER})


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = max(0, min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[k]


def _routed_latencies_ms(result: RouteResult) -> list[float]:
    return [o.routed_latency_s * 1000.0 for o in result.outcomes if o.ok]


def _weak_qualities(result: RouteResult) -> list[float]:
    return [o.quality for o in result.outcomes if o.ok and o.target == "weak"]


# The metrics a rubric dimension may bind to. Each returns None when the run
# provides no value for it, which becomes an UNMEASURED verdict rather than a
# silent pass. Keeping this an explicit registry means a typo in a rubric is
# caught at load time, not discovered as a missing row in a report.
METRICS: dict[str, Callable[[RouteResult], float | None]] = {
    # cost
    "cost.baseline_usd": lambda r: r.cost.baseline_usd,
    "cost.routed_usd": lambda r: r.cost.routed_usd,
    "cost.saved_usd": lambda r: r.cost.saved_usd,
    "cost.saved_pct": lambda r: r.cost.saved_pct,
    # quality
    "quality.mean_all": lambda r: r.mean_quality_all,
    "quality.routed_weak_mean": lambda r: r.mean_quality_routed_weak,
    "quality.routed_weak_min": lambda r: min(_weak_qualities(r)) if _weak_qualities(r) else None,
    # latency of the call a user would actually have waited on
    "latency.p50_ms": lambda r: _percentile(_routed_latencies_ms(r), 50),
    "latency.p95_ms": lambda r: _percentile(_routed_latencies_ms(r), 95),
    "latency.p99_ms": lambda r: _percentile(_routed_latencies_ms(r), 99),
    "latency.mean_ms": lambda r: (
        sum(_routed_latencies_ms(r)) / len(_routed_latencies_ms(r))
        if _routed_latencies_ms(r)
        else None
    ),
    # run integrity
    "run.error_count": lambda r: float(r.error_count),
    "run.weak_share_pct": lambda r: (100.0 * r.weak_count / r.n) if r.n else None,
}


@dataclass(frozen=True)
class ArmView:
    """One arm of a stored ``instar arms`` run, as a rubric sees it.

    Built from the ``arms`` block of a corpus ``run.json``, so a rubric can be
    applied to history at read time: agree a new rubric, then ask how every run
    already on disk would have fared under it, without re-running anything.
    ``baseline`` and ``control`` are the same run's baseline and control arms.
    """

    arm: Mapping[str, Any]
    baseline: Mapping[str, Any]
    control: Mapping[str, Any] | None = None


def _num(d: Mapping[str, Any] | None, key: str) -> float | None:
    if d is None:
        return None
    v = d.get(key)
    return None if v is None else float(v)


def _arm_quality_min(v: ArmView) -> float | None:
    scores = [float(x) for x in v.arm.get("quality_scores") or []]
    return min(scores) if scores else None


def _arm_quality_vs_control(v: ArmView) -> float | None:
    q, c = _num(v.arm, "quality_mean"), _num(v.control, "quality_mean")
    return None if q is None or not c else q / c


def _arm_cost_saved_pct(v: ArmView) -> float | None:
    # A cost that nobody reported and no table priced is unknown, not zero.
    if "unavailable" in (v.arm.get("cost_source"), v.baseline.get("cost_source")):
        return None
    a, b = _num(v.arm, "cost_per_1k_calls_usd"), _num(v.baseline, "cost_per_1k_calls_usd")
    return None if a is None or not b else 100.0 * (1.0 - a / b)


# Metrics for a stored arms run, one arm at a time. Same rules as METRICS:
# None means "not measured", which is never a pass.
ARM_METRICS: dict[str, Callable[[ArmView], float | None]] = {
    "arm.quality_mean": lambda v: _num(v.arm, "quality_mean"),
    "arm.quality_min": _arm_quality_min,
    "arm.quality_vs_control": _arm_quality_vs_control,
    "arm.cost_saved_pct": _arm_cost_saved_pct,
    "arm.p50_ms": lambda v: _num(v.arm, "p50_ms"),
    "arm.p95_ms": lambda v: _num(v.arm, "p95_ms"),
    "arm.ms_per_output_token": lambda v: _num(v.arm, "ms_per_output_token"),
    "arm.error_count": lambda v: _num(v.arm, "n_err"),
}


@dataclass(frozen=True)
class Dimension:
    """One thing you decided to hold the configuration to."""

    id: str
    metric: str
    direction: str
    pass_at: float
    marginal_at: float | None = None
    label: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.metric not in METRICS and self.metric not in ARM_METRICS:
            raise ValueError(
                f"dimension {self.id!r}: unknown metric {self.metric!r}. "
                f"Available: {', '.join(sorted({*METRICS, *ARM_METRICS}))}"
            )
        if self.direction not in DIRECTIONS:
            raise ValueError(
                f"dimension {self.id!r}: direction must be one of {sorted(DIRECTIONS)}"
            )
        if self.marginal_at is None:
            return
        # A marginal bar on the wrong side of the pass bar is a rubric that can
        # never return MARGINAL — almost certainly a typo, so refuse it.
        if self.direction == HIGHER_IS_BETTER and self.marginal_at > self.pass_at:
            raise ValueError(
                f"dimension {self.id!r}: marginal_at ({self.marginal_at}) must be <= "
                f"pass_at ({self.pass_at}) when higher is better"
            )
        if self.direction == LOWER_IS_BETTER and self.marginal_at < self.pass_at:
            raise ValueError(
                f"dimension {self.id!r}: marginal_at ({self.marginal_at}) must be >= "
                f"pass_at ({self.pass_at}) when lower is better"
            )

    def verdict_for(self, value: float | None) -> str:
        if value is None:
            return UNMEASURED
        meets = (
            value >= self.pass_at if self.direction == HIGHER_IS_BETTER else value <= self.pass_at
        )
        if meets:
            return PASS
        if self.marginal_at is None:
            return FAIL
        near = (
            value >= self.marginal_at
            if self.direction == HIGHER_IS_BETTER
            else value <= self.marginal_at
        )
        return MARGINAL if near else FAIL


@dataclass(frozen=True)
class DimensionVerdict:
    """How one dimension came out, with the number that decided it."""

    id: str
    label: str
    metric: str
    value: float | None
    verdict: str
    pass_at: float
    marginal_at: float | None
    direction: str
    rationale: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RubricVerdict:
    """The whole rubric applied to one run."""

    rubric: str
    verdict: str
    dimensions: list[DimensionVerdict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[DimensionVerdict]:
        return [d for d in self.dimensions if d.verdict == FAIL]

    @property
    def unmeasured(self) -> list[DimensionVerdict]:
        return [d for d in self.dimensions if d.verdict == UNMEASURED]

    def to_json(self) -> dict[str, Any]:
        return {
            "rubric": self.rubric,
            "verdict": self.verdict,
            "dimensions": [d.to_json() for d in self.dimensions],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class Rubric:
    """A named set of dimensions, agreed before the run.

    Load from JSON shaped like::

        {
          "name": "support-triage-v1",
          "description": "what good looks like for ticket triage",
          "dimensions": [
            {
              "id": "accuracy",
              "label": "Label accuracy on calls we moved to the cheap model",
              "metric": "quality.routed_weak_mean",
              "direction": "higher_is_better",
              "pass_at": 0.95,
              "marginal_at": 0.90,
              "rationale": "below 90% a human has to re-check every ticket"
            }
          ]
        }
    """

    name: str
    dimensions: list[Dimension]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.dimensions:
            raise ValueError(f"rubric {self.name!r} has no dimensions")
        seen = [d.id for d in self.dimensions]
        dupes = sorted({i for i in seen if seen.count(i) > 1})
        if dupes:
            raise ValueError(f"rubric {self.name!r} has duplicate dimension ids: {dupes}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Rubric:
        raw = data.get("dimensions")
        if not isinstance(raw, list):
            raise ValueError("rubric 'dimensions' must be a list")
        dims = [
            Dimension(
                id=str(d["id"]),
                metric=str(d["metric"]),
                direction=str(d.get("direction", HIGHER_IS_BETTER)),
                pass_at=float(d["pass_at"]),
                marginal_at=None if d.get("marginal_at") is None else float(d["marginal_at"]),
                label=str(d.get("label", "")),
                rationale=str(d.get("rationale", "")),
            )
            for d in raw
        ]
        return cls(
            name=str(data.get("name", "unnamed")),
            description=str(data.get("description", "")),
            dimensions=dims,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> Rubric:
        with open(path, encoding="utf-8") as fh:
            data: Any = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: rubric must be a JSON object")
        try:
            return cls.from_dict(data)
        except KeyError as e:
            raise ValueError(f"{path}: dimension missing required field {e}") from e

    def evaluate(self, result: RouteResult) -> RubricVerdict:
        """Apply this rubric to a completed routing run."""
        values = {
            d.metric: METRICS[d.metric](result) for d in self.dimensions if d.metric in METRICS
        }
        notes: list[str] = []
        if result.error_count:
            notes.append(
                f"{result.error_count}/{result.n} calls failed; this verdict rests on "
                f"partial data and should not be acted on until the run is clean"
            )
        notes.extend(result.warnings)
        return self.evaluate_values(values, notes)

    def evaluate_arm(self, view: ArmView) -> RubricVerdict:
        """Apply this rubric to one arm of a stored ``instar arms`` run."""
        values = {
            d.metric: ARM_METRICS[d.metric](view)
            for d in self.dimensions
            if d.metric in ARM_METRICS
        }
        notes: list[str] = []
        if view.arm.get("n_err"):
            notes.append(
                f"{view.arm.get('n_err')} calls failed on this arm; this verdict rests on "
                "partial data"
            )
        return self.evaluate_values(values, notes)

    def evaluate_values(
        self, values: Mapping[str, float | None], notes: list[str] | None = None
    ) -> RubricVerdict:
        """Apply this rubric to metric values already computed.

        A dimension whose metric is missing from ``values`` (a routing metric
        asked of an arms run, say) is UNMEASURED, like any metric without a value.
        """
        dimension_verdicts: list[DimensionVerdict] = []
        for dim in self.dimensions:
            value = values.get(dim.metric)
            dimension_verdicts.append(
                DimensionVerdict(
                    id=dim.id,
                    label=dim.label or dim.id,
                    metric=dim.metric,
                    value=value,
                    verdict=dim.verdict_for(value),
                    pass_at=dim.pass_at,
                    marginal_at=dim.marginal_at,
                    direction=dim.direction,
                    rationale=dim.rationale,
                )
            )

        # Worst dimension wins. Never an average — see the module docstring.
        overall = min(
            (d.verdict for d in dimension_verdicts), key=VERDICT_ORDER.index, default=UNMEASURED
        )

        notes = list(notes or [])
        for d in dimension_verdicts:
            if d.verdict == UNMEASURED:
                notes.append(
                    f"dimension '{d.id}' could not be measured on this run "
                    f"(metric {d.metric} had no value) — it is not a pass"
                )
        return RubricVerdict(
            rubric=self.name, verdict=overall, dimensions=dimension_verdicts, notes=notes
        )


def binding_dimensions(verdict: RubricVerdict) -> list[str]:
    """The dimensions that set this verdict: those at the worst level, unless it passed.

    A dimension that is never binding, run after run, never changed anyone's
    action and is a candidate for deletion (RUBRICS.md §8).
    """
    if verdict.verdict == PASS:
        return []
    return [d.id for d in verdict.dimensions if d.verdict == verdict.verdict]


def sole_deciders(verdict: RubricVerdict) -> list[str]:
    """The dimensions that decided this verdict on their own.

    A sole decider is one whose passing would have lifted the overall verdict.
    When two dimensions fail together, both are binding and neither is a sole
    decider: fixing either alone would change nothing.
    """
    overall = VERDICT_ORDER.index(verdict.verdict)
    out: list[str] = []
    for d in verdict.dimensions:
        if d.verdict == PASS:
            continue
        others = [VERDICT_ORDER.index(x.verdict) for x in verdict.dimensions if x is not d]
        if min(others, default=VERDICT_ORDER.index(PASS)) > overall:
            out.append(d.id)
    return out


def evaluate_rubric(result: RouteResult, rubric: Rubric) -> RubricVerdict:
    """Convenience wrapper: apply ``rubric`` to ``result``."""
    return rubric.evaluate(result)
