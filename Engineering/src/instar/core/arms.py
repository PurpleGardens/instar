# SPDX-License-Identifier: Apache-2.0
"""The arms runner — compare N ways of serving the same workload.

:mod:`instar.core.gateway` answers "what does the layer in front of my model
cost me in latency?" for two arms sharing one model. Real comparisons are
usually wider than that and rarely hold the model fixed:

- **direct to the provider** — the baseline you have today;
- **through a router, same model** — isolates what the extra hop costs;
- **through a router, cheaper model** — isolates what routing saves.

Those three only mean something read together, and the third *must* vary the
model, so each arm carries its own. Each arm is a
:class:`~instar.providers.base.Backend` plus a model id, which makes the same
runner serve "two gateways", "four models", or "one model at three
quantizations" without new code.

**Calls are interleaved** across arms (A, B, C, A, B, C, …) for the reason
:mod:`instar.core.gateway` interleaves two: sequential blocks charge any drift
in network conditions or provider load to whichever arm ran last, which is
precisely the difference under measurement.

Latency is wall-clock from the client and includes your network path, so arms
must be on comparable footing before a delta means anything — a hosted API
against localhost measures geography.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from instar.core.cost import PRICING, call_cost_usd
from instar.core.gateway import percentile
from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult
from instar.rubrics.base import Judge

# How a cost figure was arrived at. Worth recording per arm because a run that
# mixes measured and estimated costs is not comparing like with like, and a
# reader cannot tell from the dollar figure alone.
COST_REPORTED = "provider_reported"
COST_COMPUTED = "computed"
COST_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Arm:
    """One way of serving the workload: a backend, a model, a label."""

    name: str
    backend: Backend
    model: str


@dataclass
class ArmStats:
    """Latency and cost for one arm over one workload."""

    name: str
    model: str
    n_ok: int
    n_err: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    cost_usd: float
    cost_source: str
    input_tokens: int
    output_tokens: int
    # Quality relative to the baseline arm, in [0, 1], when a judge was run.
    # None on the baseline itself (nothing to compare against) and on every arm
    # when no judge was supplied. None means UNSCORED, which is not a pass —
    # the distinction a cost report most often loses.
    quality_mean: float | None = None
    quality_n: int = 0
    quality_scores: list[float] = field(default_factory=list)
    # Models that actually served, when the arm's backend substituted. Empty
    # for a well-behaved single-model arm; non-empty is a finding, not noise.
    served_models: list[str] = field(default_factory=list)

    @property
    def cost_per_1k_calls_usd(self) -> float:
        """The figure people actually budget with. 0.0 when cost is unknown."""
        return (self.cost_usd / self.n_ok * 1000.0) if self.n_ok else 0.0

    @property
    def ms_per_output_token(self) -> float:
        """Latency normalized by work done.

        Raw wall-clock across arms is confounded on any generative workload:
        most of a call's duration is spent emitting tokens, so an arm that
        happened to write shorter answers looks faster whether or not it is.
        Two arms are only comparable on p50 if they produced comparable output
        lengths — check ``output_tokens`` before reading a latency delta as a
        property of the *endpoint*. This ratio is the length-independent view.
        """
        return (self.mean_ms / self.output_tokens) if self.output_tokens else 0.0


def resolve_arm_cost(
    results: list[CompletionResult],
    *,
    model: str,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, str]:
    """Total USD for an arm, plus how we know it.

    Precedence, and the reasoning behind it:

    1. **Provider-reported** wins whenever every successful call carried a
       figure. It is what you were charged, not what a table predicted.
    2. **Computed** from ``pricing`` otherwise — an estimate, and wrong the
       moment our table drifts from real list prices.
    3. **Unavailable** when neither is possible. Deliberately not $0: an
       unpriced model silently scoring zero is how a cost report ends up
       flattering the cheapest-looking arm, and
       :func:`instar.core.cost.unpriced_models` exists because of that trap.

    A *partially* reported arm is treated as unreported and falls through to
    the table, so a run never sums measured dollars with estimated ones and
    presents the total as a measurement.
    """
    ok = [r for r in results if r.ok]
    if not ok:
        return (0.0, COST_UNAVAILABLE)

    reported = [r.cost_usd for r in ok if r.cost_usd is not None]
    if len(reported) == len(ok):
        return (sum(reported), COST_REPORTED)

    table = PRICING if pricing is None else pricing
    if model in table:
        total = sum(
            call_cost_usd(model, r.input_tokens, r.output_tokens, pricing=table) for r in ok
        )
        return (total, COST_COMPUTED)

    return (0.0, COST_UNAVAILABLE)


def summarize_arm(
    arm: Arm,
    results: list[CompletionResult],
    *,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> ArmStats:
    """Latency percentiles and cost over the successful calls in ``results``."""
    ok = [r for r in results if r.ok]
    lat_ms = [r.latency_s * 1000.0 for r in ok]
    mean = (sum(lat_ms) / len(lat_ms)) if lat_ms else 0.0
    cost, source = resolve_arm_cost(results, model=arm.model, pricing=pricing)
    served = sorted({r.model for r in ok if r.model and r.model != arm.model})
    return ArmStats(
        name=arm.name,
        model=arm.model,
        n_ok=len(ok),
        n_err=len(results) - len(ok),
        p50_ms=percentile(lat_ms, 50),
        p95_ms=percentile(lat_ms, 95),
        p99_ms=percentile(lat_ms, 99),
        mean_ms=mean,
        cost_usd=cost,
        cost_source=source,
        input_tokens=sum(r.input_tokens for r in ok),
        output_tokens=sum(r.output_tokens for r in ok),
        served_models=served,
    )


def judge_arm(
    judge: Judge,
    samples: list[TrafficSample],
    baseline_results: list[CompletionResult],
    arm_results: list[CompletionResult],
) -> tuple[float | None, list[float]]:
    """Score one arm's outputs against the baseline's, call for call.

    The three lists are positionally aligned by construction — :func:`run_arms`
    drives every arm through the same (repeat, sample) sequence — so index ``i``
    is the same prompt answered by each arm.

    A pair where either side failed is skipped rather than scored 0.0: a network
    error is not a quality signal, and folding it in would let an unreliable arm
    look like a *bad* arm instead of a broken one. Those two need different fixes.
    """
    scores: list[float] = []
    for sample, base, arm in zip(samples, baseline_results, arm_results, strict=True):
        if not base.ok or not arm.ok:
            continue
        scores.append(judge.score(sample, base, arm).score)
    mean = (sum(scores) / len(scores)) if scores else None
    return mean, scores


@dataclass
class ArmsResult:
    """An N-way comparison, read against whichever arm is the baseline."""

    n: int
    baseline: str
    arms: list[ArmStats]
    warnings: list[str] = field(default_factory=list)

    @property
    def trustworthy(self) -> bool:
        return not self.warnings and all(a.n_err == 0 for a in self.arms)

    def by_name(self, name: str) -> ArmStats:
        for a in self.arms:
            if a.name == name:
                return a
        raise KeyError(name)

    def deltas(self) -> list[dict[str, Any]]:
        """Each non-baseline arm against the baseline.

        Latency is reported as an absolute millisecond delta (a router's added
        hop is a fixed tax, not a percentage). Cost is reported as a percentage
        because the interesting question is what share of the bill routing
        saves — and is omitted entirely when either side's cost is unknown,
        rather than printing a confident number derived from a zero.
        """
        base = self.by_name(self.baseline)
        out: list[dict[str, Any]] = []
        for a in self.arms:
            if a.name == self.baseline:
                continue
            row: dict[str, Any] = {
                "arm": a.name,
                "vs": self.baseline,
                "latency_p50_delta_ms": a.p50_ms - base.p50_ms,
                "latency_p95_delta_ms": a.p95_ms - base.p95_ms,
                "ms_per_output_token_delta": (a.ms_per_output_token - base.ms_per_output_token),
                "output_token_ratio": (
                    (a.output_tokens / base.output_tokens) if base.output_tokens else 0.0
                ),
                "cost_delta_pct": None,
                "quality_mean": a.quality_mean,
            }
            if (
                base.cost_source != COST_UNAVAILABLE
                and a.cost_source != COST_UNAVAILABLE
                and base.cost_per_1k_calls_usd > 0
            ):
                row["cost_delta_pct"] = (
                    (a.cost_per_1k_calls_usd - base.cost_per_1k_calls_usd)
                    / base.cost_per_1k_calls_usd
                    * 100.0
                )
            out.append(row)
        return out

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["trustworthy"] = self.trustworthy
        d["deltas"] = self.deltas()
        for arm_d, arm in zip(d["arms"], self.arms, strict=True):
            arm_d["cost_per_1k_calls_usd"] = arm.cost_per_1k_calls_usd
            arm_d["ms_per_output_token"] = arm.ms_per_output_token
        return d


def run_arms(
    samples: list[TrafficSample],
    *,
    arms: list[Arm],
    repeats: int = 1,
    pricing: dict[str, tuple[float, float]] | None = None,
    baseline: str | None = None,
    judge: Judge | None = None,
) -> ArmsResult:
    """Replay ``samples`` through every arm, interleaved, and compare.

    Args:
        samples: the workload. Yours, ideally — a benchmark measures the
            benchmark.
        arms: two or more ways of serving it.
        repeats: replay the whole workload this many times. Latency is noisy;
            one pass over a short fixture is an anecdote.
        pricing: model -> (input, output) USD per 1M tokens, for arms whose
            backend does not report cost.
        baseline: which arm the deltas are measured against. Defaults to the
            first, which is the natural reading of "A/B/C".
        judge: optional. Scores every other arm's output against the baseline's
            on the same call. Without one the run reports cost and latency and
            says nothing about quality — which is half an answer, and the half
            that makes a cheap arm look unambiguously good.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if len(arms) < 2:
        raise ValueError("need at least two arms to compare")
    names = [a.name for a in arms]
    if len(set(names)) != len(names):
        raise ValueError(f"arm names must be unique, got {names}")
    base_name = baseline or names[0]
    if base_name not in names:
        raise ValueError(f"baseline {base_name!r} is not one of {names}")

    collected: dict[str, list[CompletionResult]] = {a.name: [] for a in arms}
    # The exact (repeat, sample) sequence every arm walked, kept so a judge can
    # line up call i across arms without re-deriving the ordering.
    sequence: list[TrafficSample] = []
    for _ in range(repeats):
        for sample in samples:
            sequence.append(sample)
            for arm in arms:
                collected[arm.name].append(arm.backend.complete(sample, arm.model))

    stats = [summarize_arm(a, collected[a.name], pricing=pricing) for a in arms]

    if judge is not None:
        base_results = collected[base_name]
        for s in stats:
            if s.name == base_name:
                continue
            mean, scores = judge_arm(judge, sequence, base_results, collected[s.name])
            s.quality_mean = mean
            s.quality_n = len(scores)
            s.quality_scores = scores

    warnings: list[str] = []
    total_calls = len(samples) * repeats
    if total_calls < 30:
        warnings.append(
            f"only {total_calls} calls per arm - tail percentiles are indicative at best; "
            f"raise --repeats or use a larger fixture before quoting p95/p99"
        )
    for s in stats:
        if s.n_err:
            warnings.append(f"{s.name}: {s.n_err}/{total_calls} calls failed")
        if s.cost_source == COST_UNAVAILABLE:
            warnings.append(
                f"{s.name}: cost unknown - the backend reported none and "
                f"{s.model!r} has no pricing row. Cost columns for this arm "
                f"are not $0, they are missing"
            )
        if s.served_models:
            warnings.append(
                f"{s.name}: asked for {s.model!r} but was served "
                f"{', '.join(repr(m) for m in s.served_models)}"
            )
    base_stats = next(s for s in stats if s.name == base_name)
    for s in stats:
        if s.name == base_name or not base_stats.output_tokens or not s.output_tokens:
            continue
        ratio = s.output_tokens / base_stats.output_tokens
        if ratio < 0.75 or ratio > 1.33:
            warnings.append(
                f"{s.name}: produced {ratio:.2f}x the output tokens of "
                f"{base_name} - raw latency percentiles are not comparable; "
                f"read ms_per_output_token instead"
            )

    if judge is None and len(stats) > 1:
        warnings.append(
            "no judge supplied - this run measures cost and latency only. A "
            "cheaper arm is not a better arm until its output has been scored"
        )

    sources = {s.cost_source for s in stats if s.cost_source != COST_UNAVAILABLE}
    if len(sources) > 1:
        warnings.append(
            "arms mix measured and estimated costs "
            f"({', '.join(sorted(sources))}) - the cost comparison is not like-for-like"
        )

    return ArmsResult(n=total_calls, baseline=base_name, arms=stats, warnings=warnings)
