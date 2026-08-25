# SPDX-License-Identifier: Apache-2.0
"""The ``instar`` command line.

Two subcommands:

- ``instar route`` — replay a workload through a routing policy; measure spend
  saved and quality given up. Sweep a threshold to draw the cost/quality curve.
- ``instar gateway`` — replay a workload through two gateways or endpoints;
  compare per-call latency.
- ``instar arms`` — replay a workload through N arms, each with its own
  endpoint and model; compare latency and cost. The A/B/C shape: direct,
  through a router at the same model, through a router at a cheaper one.

Both default to **mock mode**, which is hermetic: no API keys, no network, no
spend. Pass ``--live`` to use real endpoints.

Exit status is ``1`` when a run produced failed calls, so CI can refuse to
publish a report built on partial data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from instar.core.arms import Arm, run_arms
from instar.core.catalog import FeatureCatalog
from instar.core.cost import load_pricing
from instar.core.gateway import run_gateway
from instar.core.route import run_route, run_sweep
from instar.core.traffic import TrafficSample, load_traffic
from instar.policies import POLICY_NAMES, ClassifierPolicy, build_policy
from instar.providers.anthropic import AnthropicBackend
from instar.providers.base import Backend
from instar.providers.mock import MockBackend
from instar.providers.openai_compat import OpenAICompatBackend
from instar.reporters import (
    DEFAULT_RUNS_DIR,
    report_arms,
    report_gateway,
    report_route,
    report_sweep,
)
from instar.rubrics.base import Judge
from instar.rubrics.judges import (
    AutoJudge,
    BlindPairwiseJudge,
    LabelMatchJudge,
    LLMJudge,
    MockJudge,
)
from instar.rubrics.spec import FAIL, MARGINAL, Rubric

# Undated model ids on purpose — dated snapshot ids are a recurring 404 source.
DEFAULT_STRONG = os.getenv("INSTAR_STRONG_MODEL", "claude-sonnet-4-6")
DEFAULT_WEAK = os.getenv("INSTAR_WEAK_MODEL", "claude-haiku-4-5")
DEFAULT_GATEWAY_MODEL = os.getenv("INSTAR_GATEWAY_MODEL", "claude-haiku-4-5")

MOCK_STRONG_MODEL = "mock-strong"
MOCK_WEAK_MODEL = "mock-weak"

# Where to look for the default fixture when --traffic is omitted.
_TRAFFIC_SEARCH = (
    Path("sample-traffic.jsonl"),
    Path("fixtures/sample-traffic.jsonl"),
    Path("Engineering/fixtures/sample-traffic.jsonl"),
)


def _resolve_traffic(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for candidate in _TRAFFIC_SEARCH:
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(p) for p in _TRAFFIC_SEARCH)
    raise SystemExit(
        f"instar: no --traffic given and no default fixture found (looked for: {tried}).\n"
        f"Pass --traffic path/to/workload.jsonl"
    )


def _load_catalog(path: str | None) -> FeatureCatalog:
    if not path:
        return FeatureCatalog.empty()
    return FeatureCatalog.from_json(path)


def _load_inputs(
    traffic: str | None, catalog_path: str | None, pricing_path: str | None
) -> tuple[list[TrafficSample], FeatureCatalog, dict[str, tuple[float, float]] | None]:
    """Load everything the run needs, reporting bad input as a message rather
    than a traceback. A malformed fixture is a typo, not a crash."""
    path = _resolve_traffic(traffic)
    try:
        samples = load_traffic(path)
        catalog = _load_catalog(catalog_path)
        pricing = load_pricing(pricing_path) if pricing_path else None
    except FileNotFoundError as e:
        raise SystemExit(f"instar: file not found: {e.filename}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise SystemExit(f"instar: {e}") from e
    return samples, catalog, pricing


def _warn_uncatalogued(catalog: FeatureCatalog, samples: list[TrafficSample]) -> None:
    """Tell the user which features fell through to the default category.

    Silence here is how a workload gets mis-costed: an uncatalogued feature is
    treated as foreground and quietly never routed to the cheap model, so the
    savings look worse than they are and nobody can see why.
    """
    features = {s.feature for s in samples if s.category is None}
    unknown = catalog.unknown_features(features)
    if unknown:
        print(
            f"  note: {len(unknown)} feature(s) not in the catalog, defaulting to "
            f"'{catalog.default}': {', '.join(unknown)}",
            file=sys.stderr,
        )


def _live_backend(name: str, url: str | None, key_env: str | None) -> Backend:
    """An arm of a live run: an OpenAI-compatible endpoint if a URL was given,
    otherwise Anthropic.

    The URL form is what lets an arm be a self-hosted small model, a proxy, or
    any third-party provider — which is the comparison most cost questions
    actually turn on.
    """
    if url:
        return OpenAICompatBackend(url, name=name, api_key_env=key_env)
    return AnthropicBackend(name)


def _cmd_route(args: argparse.Namespace) -> int:
    samples, catalog, pricing = _load_inputs(args.traffic, args.catalog, args.pricing)
    try:
        rubric = Rubric.from_json(args.rubric) if args.rubric else None
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit(f"instar: {e}") from e
    mock = not args.live

    strong_model = MOCK_STRONG_MODEL if mock else args.strong_model
    weak_model = MOCK_WEAK_MODEL if mock else args.weak_model

    strong_backend: Backend
    weak_backend: Backend
    judge: Judge
    if mock:
        strong_backend = MockBackend("strong", latency_s=0.020)
        weak_backend = MockBackend("weak", latency_s=0.008)
        judge = MockJudge(catalog)
    else:
        strong_backend = _live_backend("strong", args.strong_url, args.strong_key_env)
        weak_backend = _live_backend("weak", args.weak_url, args.weak_key_env)
        # Objective label-match wherever a gold label exists; LLM-as-judge for
        # everything else. One judge therefore handles a mixed workload.
        labels = sorted({str(s.meta["gold"]) for s in samples if s.meta.get("gold")})
        label_judge = LabelMatchJudge(labels) if labels else None
        # The judge runs on the strong arm: whatever you trust as the quality
        # baseline is what should be grading the cheaper model's work.
        judge = AutoJudge(label_judge, LLMJudge(strong_backend, judge_model=strong_model))

    _warn_uncatalogued(catalog, samples)

    if args.sweep:
        try:
            thresholds = [float(x) for x in args.sweep.split(",") if x.strip()]
        except ValueError as e:
            raise SystemExit(f"instar: --sweep must be comma-separated numbers: {e}") from e
        if not thresholds:
            raise SystemExit("instar: --sweep needs at least one threshold")
        points = run_sweep(
            samples,
            thresholds=thresholds,
            strong_backend=strong_backend,
            weak_backend=weak_backend,
            judge=judge,
            strong_model=strong_model,
            weak_model=weak_model,
            policy_factory=lambda t: ClassifierPolicy(threshold=t, catalog=catalog),
            pricing=pricing,
            catalog=catalog,
        )
        label = args.label or f"route-sweep-{'mock' if mock else 'live'}"
        d = report_sweep(
            points,
            label,
            mock=mock,
            strong_model=strong_model,
            weak_model=weak_model,
            runs_dir=args.runs_dir,
            custom_pricing=pricing is not None,
        )
        print(f"sweep -> {d}")
        for p in points:
            mq_weak = (
                "  n/a"
                if p.mean_quality_routed_weak is None
                else f"{p.mean_quality_routed_weak:.3f}"
            )
            print(
                f"  t={p.threshold:.2f}  saved={p.saved_pct:5.1f}%  "
                f"q_all={p.mean_quality_all:.3f}  q_weak={mq_weak}  weak={p.weak_count}"
            )
        return 0

    policy = build_policy(args.policy, threshold=args.threshold, catalog=catalog)
    result = run_route(
        samples,
        policy=policy,
        strong_backend=strong_backend,
        weak_backend=weak_backend,
        judge=judge,
        strong_model=strong_model,
        weak_model=weak_model,
        pricing=pricing,
        catalog=catalog,
    )
    label = args.label or f"route-{policy.name}-{'mock' if mock else 'live'}"
    rubric_verdict = rubric.evaluate(result) if rubric else None
    d = report_route(
        result,
        label,
        mock=mock,
        runs_dir=args.runs_dir,
        rubric_verdict=rubric_verdict,
        custom_pricing=pricing is not None,
    )

    mq_weak = (
        "n/a"
        if result.mean_quality_routed_weak is None
        else f"{result.mean_quality_routed_weak:.3f}"
    )
    print(f"route -> {d}")
    print(
        f"  policy={result.policy}  saved={result.cost.saved_pct:.1f}%  "
        f"q_all={result.mean_quality_all:.3f}  q_weak={mq_weak}  "
        f"weak={result.weak_count}/{result.n}"
    )
    if rubric_verdict is not None:
        print(f"  rubric={rubric_verdict.rubric}  verdict={rubric_verdict.verdict.upper()}")
        for dv in rubric_verdict.dimensions:
            value = "not measured" if dv.value is None else f"{dv.value:,.4g}"
            print(f"    {dv.verdict.upper():10s} {dv.id:24s} {value}")
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if rubric_verdict is not None and rubric_verdict.verdict in (FAIL, MARGINAL):
        # A rubric exists to gate a decision. If the bar was not met, saying so
        # in the exit status is the whole point.
        for dv in rubric_verdict.failed + rubric_verdict.unmeasured:
            print(f"  rubric {dv.verdict}: {dv.id} ({dv.metric})", file=sys.stderr)
        if rubric_verdict.verdict == FAIL:
            return 1
    if result.error_count:
        print(
            f"  {result.error_count}/{result.n} calls FAILED — figures exclude them and "
            f"are NOT trustworthy until you re-run clean",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_gateway(args: argparse.Namespace) -> int:
    samples, _, _ = _load_inputs(args.traffic, None, None)
    mock = not args.live
    model = MOCK_WEAK_MODEL if mock else args.model

    a_backend: Backend
    b_backend: Backend
    if mock:
        # Two arms with different simulated latency, so the mock comparison is
        # non-degenerate and you can see the report take shape.
        a_backend = MockBackend(args.a_name, latency_s=0.012)
        b_backend = MockBackend(args.b_name, latency_s=0.010)
    else:
        if not args.a_url or not args.b_url:
            raise SystemExit("instar: --live gateway runs need both --a-url and --b-url")
        a_backend = OpenAICompatBackend(args.a_url, name=args.a_name, api_key_env=args.a_key_env)
        b_backend = OpenAICompatBackend(args.b_url, name=args.b_name, api_key_env=args.b_key_env)

    result = run_gateway(
        samples,
        a_backend=a_backend,
        b_backend=b_backend,
        model=model,
        repeats=args.repeats,
    )
    label = args.label or f"gateway-{'mock' if mock else 'live'}"
    d = report_gateway(result, label, mock=mock, runs_dir=args.runs_dir)
    print(f"gateway -> {d}")
    print(
        f"  overhead ({result.a.backend} - {result.b.backend}): "
        f"p50 {result.overhead_p50_ms:+.1f}ms  "
        f"p95 {result.overhead_p95_ms:+.1f}ms  "
        f"p99 {result.overhead_p99_ms:+.1f}ms"
    )
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return 1 if (result.a.n_err or result.b.n_err) else 0


def _parse_arm_spec(spec: str) -> dict[str, str]:
    """Parse ``name=A,url=...,model=...,key_env=...`` into a dict.

    A flat key=value list rather than JSON because these go on a command line
    by hand. ``url`` may be omitted for the special value ``direct``, which
    selects the native Anthropic backend instead of an OpenAI-compatible one.
    """
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"instar: bad --arm segment {part!r}; expected key=value")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    if "name" not in out:
        raise SystemExit(f"instar: --arm {spec!r} is missing name=")
    if "model" not in out:
        raise SystemExit(f"instar: --arm {spec!r} is missing model=")
    return out


def _build_arm(spec: dict[str, str], *, extra_body: dict[str, Any] | None) -> Arm:
    url = spec.get("url", "")
    backend: Backend
    if url in ("", "direct", "anthropic"):
        backend = AnthropicBackend(name=spec["name"])
    else:
        backend = OpenAICompatBackend(
            url,
            name=spec["name"],
            api_key_env=spec.get("key_env"),
            extra_body=extra_body,
        )
    return Arm(name=spec["name"], backend=backend, model=spec["model"])


def _cmd_arms(args: argparse.Namespace) -> int:
    samples, _, _ = _load_inputs(args.traffic, None, None)
    mock = not args.live

    extra_body: dict[str, Any] | None = None
    if args.extra_body:
        try:
            parsed = json.loads(args.extra_body)
        except json.JSONDecodeError as e:
            raise SystemExit(f"instar: --extra-body is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise SystemExit("instar: --extra-body must be a JSON object")
        extra_body = parsed

    arms: list[Arm]
    if mock:
        # Three arms with different simulated latency so the report shape is
        # visible without spending anything.
        arms = [
            Arm("arm-a", MockBackend("arm-a", latency_s=0.010), MOCK_STRONG_MODEL),
            Arm("arm-b", MockBackend("arm-b", latency_s=0.013), MOCK_STRONG_MODEL),
            Arm("arm-c", MockBackend("arm-c", latency_s=0.011), MOCK_WEAK_MODEL),
        ]
    else:
        if len(args.arm) < 2:
            raise SystemExit("instar: --live arms runs need at least two --arm specs")
        arms = [_build_arm(_parse_arm_spec(a), extra_body=extra_body) for a in args.arm]

    pricing = load_pricing(args.pricing) if args.pricing else None

    judge: Judge | None = None
    if args.judge:
        if mock:
            judge = MockJudge()
        else:
            judge_backend: Backend = (
                OpenAICompatBackend(args.judge_url, name="judge", api_key_env=args.judge_key_env)
                if args.judge_url
                else AnthropicBackend(name="judge")
            )
            judge = (
                BlindPairwiseJudge(judge_backend, args.judge_model)
                if args.blind_judge
                else LLMJudge(judge_backend, args.judge_model)
            )

    result = run_arms(
        samples,
        arms=arms,
        repeats=args.repeats,
        pricing=pricing,
        baseline=args.baseline,
        judge=judge,
    )
    label = args.label or f"arms-{'mock' if mock else 'live'}"
    d = report_arms(result, label, mock=mock, runs_dir=args.runs_dir)
    print(f"arms -> {d}")
    for s in result.arms:
        cost = (
            "cost unknown"
            if s.cost_source == "unavailable"
            else f"${s.cost_per_1k_calls_usd:.4f}/1k ({s.cost_source})"
        )
        qual = (
            "  quality unscored"
            if s.quality_mean is None
            else f"  quality {s.quality_mean:.3f} (n={s.quality_n})"
        )
        print(f"  {s.name:<14} p50 {s.p50_ms:7.1f}ms  {cost}{qual}")
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return 1 if any(s.n_err for s in result.arms) else 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--traffic", help="workload fixture (.jsonl); defaults to a sample if present")
    p.add_argument("--live", action="store_true", help="use real endpoints instead of mocks")
    p.add_argument("--label", help="run label; output goes to <runs-dir>/<label>/")
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="where to write run output")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="instar",
        description="Measure LLM cost, quality, and latency on your own workloads.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    route = sub.add_parser(
        "route", help="replay a workload through a routing policy; measure cost vs quality"
    )
    _add_common(route)
    route.add_argument(
        "--policy",
        default="feature_category",
        choices=list(POLICY_NAMES),
        help="routing policy to test (default: feature_category)",
    )
    route.add_argument("--threshold", type=float, default=0.5, help="classifier policy threshold")
    route.add_argument(
        "--sweep",
        help="comma-separated thresholds to sweep, e.g. 0.2,0.4,0.6,0.8; always sweeps "
        "the classifier policy, ignoring --policy",
    )
    route.add_argument("--catalog", help="feature catalog JSON mapping features to categories")
    route.add_argument(
        "--rubric",
        help="rubric JSON: the dimensions and thresholds this run must meet. "
        "A failing verdict exits 1.",
    )
    route.add_argument(
        "--pricing",
        help="pricing table JSON; REPLACES the built-in table rather than merging, so "
        "include every model your run uses",
    )
    route.add_argument("--strong-model", default=DEFAULT_STRONG)
    route.add_argument("--weak-model", default=DEFAULT_WEAK)
    route.add_argument(
        "--strong-url", help="OpenAI-compatible base url for the strong arm (default: Anthropic)"
    )
    route.add_argument("--strong-key-env", help="env var holding the strong arm's API key")
    route.add_argument(
        "--weak-url",
        help="OpenAI-compatible base url for the weak arm, e.g. a self-hosted small model "
        "(default: Anthropic)",
    )
    route.add_argument("--weak-key-env", help="env var holding the weak arm's API key")
    route.set_defaults(func=_cmd_route)

    arms = sub.add_parser(
        "arms",
        help="replay a workload through N arms; compare latency and cost",
    )
    _add_common(arms)
    arms.add_argument(
        "--arm",
        action="append",
        default=[],
        metavar="name=A,url=...,model=...,key_env=...",
        help="one arm; repeat for each. url=direct uses the native Anthropic "
        "backend. Ignored in mock mode.",
    )
    arms.add_argument(
        "--baseline",
        help="arm name the deltas are measured against (default: the first arm)",
    )
    arms.add_argument(
        "--extra-body",
        help="JSON merged into every OpenAI-compatible request, e.g. "
        '\'{"provider":{"sort":"price","zdr":true}}\'',
    )
    arms.add_argument("--pricing", help="pricing table JSON for arms that report no cost")
    arms.add_argument(
        "--judge",
        action="store_true",
        help="score every non-baseline arm's output against the baseline's",
    )
    arms.add_argument(
        "--blind-judge",
        action="store_true",
        help="hide which answer came from which arm and shuffle their order; "
        "run alongside the default judge as a control on labelling bias",
    )
    arms.add_argument("--judge-model", default=DEFAULT_STRONG, help="model that judges")
    arms.add_argument("--judge-url", help="OpenAI-compatible endpoint for the judge")
    arms.add_argument("--judge-key-env", help="env var holding the judge's API key")
    arms.add_argument("--repeats", type=int, default=1, help="replay the workload N times per arm")

    gateway = sub.add_parser(
        "gateway", help="compare two gateways or endpoints on per-call latency"
    )
    _add_common(gateway)
    gateway.add_argument("--model", default=DEFAULT_GATEWAY_MODEL)
    gateway.add_argument("--a-url", help="arm A base url (OpenAI-compatible)")
    gateway.add_argument("--a-name", default="arm-a", help="label for arm A in the report")
    gateway.add_argument("--a-key-env", help="env var holding arm A's API key")
    gateway.add_argument("--b-url", help="arm B base url (OpenAI-compatible)")
    gateway.add_argument("--b-name", default="arm-b", help="label for arm B in the report")
    gateway.add_argument("--b-key-env", help="env var holding arm B's API key")
    gateway.add_argument(
        "--repeats", type=int, default=1, help="replay the workload N times (latency is noisy)"
    )
    gateway.set_defaults(func=_cmd_gateway)
    arms.set_defaults(func=_cmd_arms)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        exit_code: int = args.func(args)
    except ModuleNotFoundError as e:
        # An uninstalled optional provider SDK is a setup problem, not a crash.
        raise SystemExit(f"instar: {e}") from e
    except KeyboardInterrupt:
        raise SystemExit("instar: interrupted") from None
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
