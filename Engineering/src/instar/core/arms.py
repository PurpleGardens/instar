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
from instar.core.transcript import Transcript, TranscriptEntry
from instar.providers.base import Backend, CompletionResult
from instar.rubrics.base import Judge, JudgeKey, JudgeResult

# How a cost figure was arrived at. Worth recording per arm because a run that
# mixes measured and estimated costs is not comparing like with like, and a
# reader cannot tell from the dollar figure alone.
COST_REPORTED = "provider_reported"
COST_COMPUTED = "computed"
COST_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Arm:
    """One way of serving the workload: a backend, a model, a label.

    ``is_control`` marks a **same-model control**: an arm serving exactly the
    baseline's model. Its true quality relative to the baseline is ~1.0, so any
    score a judge gives it below that is the judge's error, not the model's —
    which is what makes the other arms' quality numbers readable.
    """

    name: str
    backend: Backend
    model: str
    is_control: bool = False


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
    is_control: bool = False

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
        is_control=arm.is_control,
    )


def judge_calls(
    judge: Judge,
    samples: list[TrafficSample],
    baseline_results: list[CompletionResult],
    arm_results: list[CompletionResult],
) -> list[JudgeResult | None]:
    """Score one arm's outputs against the baseline's, call for call.

    The three lists are positionally aligned by construction — :func:`run_arms`
    drives every arm through the same (repeat, sample) sequence — so index ``i``
    is the same prompt answered by each arm. The result is aligned the same way,
    with ``None`` where the pair was skipped.

    A pair where either side failed is skipped rather than scored 0.0: a network
    error is not a quality signal, and folding it in would let an unreliable arm
    look like a *bad* arm instead of a broken one. Those two need different fixes.

    A pair the judge :meth:`~instar.rubrics.base.Judge.abstains` on (a human
    grader who left the row blank) is skipped the same way: unscored, not 0.0.
    """
    out: list[JudgeResult | None] = []
    abstains = getattr(judge, "abstains", None)
    for sample, base, arm in zip(samples, baseline_results, arm_results, strict=True):
        if not base.ok or not arm.ok:
            out.append(None)
            continue
        if callable(abstains) and abstains(sample, base, arm):
            out.append(None)
            continue
        out.append(judge.score(sample, base, arm))
    return out


def judge_arm(
    judge: Judge,
    samples: list[TrafficSample],
    baseline_results: list[CompletionResult],
    arm_results: list[CompletionResult],
) -> tuple[float | None, list[float]]:
    """Mean and list of scores for one arm; see :func:`judge_calls`."""
    scores = [
        r.score for r in judge_calls(judge, samples, baseline_results, arm_results) if r is not None
    ]
    mean = (sum(scores) / len(scores)) if scores else None
    return mean, scores


def judge_key_of(judge: Judge) -> JudgeKey:
    """The judge's identity, tolerating judges that predate :meth:`Judge.key`.

    A judge only has to provide ``score``; one written without subclassing
    :class:`Judge` still gets recorded, by its ``name``, rather than failing the
    run after every generation has been paid for.
    """
    key = getattr(judge, "key", None)
    if callable(key):
        result = key()
        if isinstance(result, JudgeKey):
            return result
    return JudgeKey(kind=str(getattr(judge, "name", type(judge).__name__)))


def _apply_judge(
    judge: Judge,
    stats: list[ArmStats],
    base_name: str,
    sequence: list[TrafficSample],
    collected: dict[str, list[CompletionResult]],
) -> dict[str, list[JudgeResult | None]]:
    """Judge every non-baseline arm; fill its quality fields; return per-call results."""
    judgments: dict[str, list[JudgeResult | None]] = {}
    base_results = collected[base_name]
    for s in stats:
        if s.name == base_name:
            continue
        calls = judge_calls(judge, sequence, base_results, collected[s.name])
        scores = [r.score for r in calls if r is not None]
        s.quality_mean = (sum(scores) / len(scores)) if scores else None
        s.quality_n = len(scores)
        s.quality_scores = scores
        judgments[s.name] = calls
    return judgments


@dataclass
class ArmsResult:
    """An N-way comparison, read against whichever arm is the baseline."""

    n: int
    baseline: str
    arms: list[ArmStats]
    warnings: list[str] = field(default_factory=list)
    # Present only when the run was asked to capture. Deliberately kept out of
    # to_json(): result.json is a summary people read and diff, and raw model
    # output would swamp it. Save it beside the report with Transcript.save().
    transcript: Transcript | None = None
    # Which judge scored this run, if any. Part of the result's identity: the
    # same answers under a different judge are a different measurement.
    judge: JudgeKey | None = None
    # Per-call judge results per judged arm, aligned with the run's (repeat,
    # sample) sequence; None where a pair was skipped. Kept out of to_json()
    # for the same reason as the transcript — it is per-call detail.
    judgments: dict[str, list[JudgeResult | None]] = field(default_factory=dict)

    @property
    def control(self) -> str | None:
        return next((a.name for a in self.arms if a.is_control), None)

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
        d.pop("transcript", None)
        d.pop("judgments", None)
        d["judge"] = self.judge.to_json() if self.judge is not None else None
        d["control"] = self.control
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
    capture: bool = False,
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
        capture: keep every generation on the result as a
            :class:`~instar.core.transcript.Transcript`, so the same answers can
            be re-scored later by a different judge. Generations are the
            expensive half of a run and judging is the disputable half; capturing
            lets you redo the second without paying for the first again. Off by
            default because a transcript holds raw model output.
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
    controls = [a for a in arms if a.is_control]
    if len(controls) > 1:
        raise ValueError(f"at most one control arm, got {[a.name for a in controls]}")
    base_arm = next(a for a in arms if a.name == base_name)
    if controls:
        if controls[0].name == base_name:
            raise ValueError("the baseline cannot also be the control arm")
        if controls[0].model != base_arm.model:
            raise ValueError(
                f"control arm {controls[0].name!r} must serve the baseline's model "
                f"{base_arm.model!r}, not {controls[0].model!r} - otherwise it is not a control"
            )

    collected: dict[str, list[CompletionResult]] = {a.name: [] for a in arms}
    # The exact (repeat, sample) sequence every arm walked, kept so a judge can
    # line up call i across arms without re-deriving the ordering.
    sequence: list[TrafficSample] = []
    repeat_of: list[int] = []
    for rep in range(repeats):
        for sample in samples:
            sequence.append(sample)
            repeat_of.append(rep)
            for arm in arms:
                collected[arm.name].append(arm.backend.complete(sample, arm.model))

    transcript: Transcript | None = None
    if capture:
        transcript = Transcript(
            baseline=base_name,
            arm_models={a.name: a.model for a in arms},
            entries=[
                TranscriptEntry(
                    sample=sample,
                    completions={a.name: collected[a.name][i] for a in arms},
                    repeat=repeat_of[i],
                )
                for i, sample in enumerate(sequence)
            ],
            arm_backends={a.name: type(a.backend).__name__ for a in arms},
            control=controls[0].name if controls else None,
        )

    stats = [summarize_arm(a, collected[a.name], pricing=pricing) for a in arms]

    judgments: dict[str, list[JudgeResult | None]] = {}
    if judge is not None:
        judgments = _apply_judge(judge, stats, base_name, sequence, collected)

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
    if judge is not None and not controls:
        warnings.append(
            "no control arm - a judged run needs a same-model control (--control) "
            "to show how much of each quality score is the judge's own error"
        )

    sources = {s.cost_source for s in stats if s.cost_source != COST_UNAVAILABLE}
    if len(sources) > 1:
        warnings.append(
            "arms mix measured and estimated costs "
            f"({', '.join(sorted(sources))}) - the cost comparison is not like-for-like"
        )

    return ArmsResult(
        n=total_calls,
        baseline=base_name,
        arms=stats,
        warnings=warnings,
        transcript=transcript,
        judge=judge_key_of(judge) if judge is not None else None,
        judgments=judgments,
    )


class _ReplayBackend(Backend):
    """Placeholder backend for arms rebuilt from a transcript.

    :func:`summarize_arm` needs an :class:`Arm`, and an ``Arm`` needs a backend —
    but re-judging never generates anything, so there is nothing for it to call.
    Raising rather than quietly returning a failure means a future edit that
    tries to *complete* through a replayed arm fails loudly instead of silently
    scoring an empty string.
    """

    name = "replay"

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        raise RuntimeError("a replayed arm cannot generate; it only carries saved completions")


_REPLAY_BACKEND = _ReplayBackend()


def rejudge(
    transcript: Transcript,
    judge: Judge,
    *,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> ArmsResult:
    """Re-score saved generations with a different judge. No new generations.

    This is the control on the judge. :func:`run_arms` reports one judge's
    opinion; run this over the same transcript with a judge from another model
    family and any movement in the quality numbers is the judge's doing, because
    nothing else changed. Two judges that agree make a quality claim defensible.
    Two that disagree have told you the claim was never about the models.

    Cost and latency come back identical to the original run — they are read off
    the saved completions, not re-measured — so the report is directly
    comparable to the one the live run produced.
    """
    if not transcript.entries:
        raise ValueError("transcript has no entries to judge")
    names = transcript.arm_names
    base_name = transcript.baseline
    sequence = [e.sample for e in transcript.entries]
    collected: dict[str, list[CompletionResult]] = {
        name: [e.completions[name] for e in transcript.entries] for name in names
    }
    arms = [
        Arm(
            name=name,
            backend=_REPLAY_BACKEND,
            model=transcript.arm_models[name],
            is_control=name == transcript.control,
        )
        for name in names
    ]
    stats = [summarize_arm(a, collected[a.name], pricing=pricing) for a in arms]
    judgments = _apply_judge(judge, stats, base_name, sequence, collected)

    warnings = [
        "re-judged from a saved transcript: cost and latency are replayed from "
        "the original run, only the quality scores are new"
    ]
    abstained = sum(
        1
        for name, calls in judgments.items()
        for base, arm, jr in zip(collected[base_name], collected[name], calls, strict=True)
        if base.ok and arm.ok and jr is None
    )
    if abstained:
        warnings.append(
            f"{abstained} pair(s) were not scored by this judge (for a human judge, "
            "rows left ungraded); quality is over the scored pairs only"
        )
    if transcript.control is None:
        warnings.append(
            "no control arm in this transcript - the judge's own error cannot be "
            "separated from the difference between models"
        )
    return ArmsResult(
        n=len(transcript.entries),
        baseline=base_name,
        arms=stats,
        warnings=warnings,
        judge=judge_key_of(judge),
        judgments=judgments,
    )
