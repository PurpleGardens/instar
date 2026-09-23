# SPDX-License-Identifier: Apache-2.0
"""The ``instar`` command line.

The subcommands:

- ``instar route`` — replay a workload through a routing policy; measure spend
  saved and quality given up. Sweep a threshold to draw the cost/quality curve.
- ``instar gateway`` — replay a workload through two gateways or endpoints;
  compare per-call latency.
- ``instar arms`` — replay a workload through N arms, each with its own
  endpoint and model; compare latency and cost. The A/B/C shape: direct,
  through a router at the same model, through a router at a cheaper one.
- ``instar rejudge`` — score a saved arms transcript again with another judge.
- ``instar corpus`` — read a measurement corpus across runs: list runs and
  calls, check each judge against its control, and read scores against the
  noise band.

``arms`` and ``rejudge`` can also append each run to a measurement corpus
(``--corpus``), so runs can be read together later. See
:mod:`instar.core.corpus`.

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

from instar.cli.corpus import add_corpus_parser
from instar.cli.mcp import add_mcp_parser
from instar.core.arms import Arm, rejudge, run_arms
from instar.core.catalog import FeatureCatalog
from instar.core.corpus import (
    ORIGINS,
    SPLIT_ROLES,
    RecordContext,
    find_corpus_root,
    load_run_context,
    write_run,
)
from instar.core.cost import load_pricing
from instar.core.gateway import run_gateway
from instar.core.route import run_route, run_sweep
from instar.core.traffic import TrafficSample, load_traffic
from instar.core.transcript import Transcript
from instar.mcp.agent import MCPAgentBackend, MCPToolbox, ToolCassette
from instar.mcp.client import MCPError, load_servers
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
from instar.rubrics.criteria import CriteriaJudge, CriteriaSet, MockCriteriaBackend
from instar.rubrics.human import HumanJudge, write_grading_sheet
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
    specs: list[dict[str, str]] = []
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
        specs = [_parse_arm_spec(a) for a in args.arm]
        arms = [_build_arm(spec, extra_body=extra_body) for spec in specs]

    if args.control:
        if any(a.name == CONTROL_ARM_NAME for a in arms):
            raise SystemExit(
                f"instar: --control adds an arm named {CONTROL_ARM_NAME!r}; rename yours"
            )
        base_name = args.baseline or arms[0].name
        base_arm = next((a for a in arms if a.name == base_name), None)
        if base_arm is None:
            raise SystemExit(f"instar: baseline {base_name!r} is not one of the arms")
        base_spec = next((sp for sp in specs if sp["name"] == base_name), None)
        arms.append(_control_arm(base_arm, mock=mock, spec=base_spec, extra_body=extra_body))

    ctx = (
        _record_context(args, mock=mock, traffic=str(_resolve_traffic(args.traffic)))
        if args.corpus
        else None
    )

    pricing = load_pricing(args.pricing) if args.pricing else None

    judge: Judge | None = None
    if args.criteria:
        judge = _criteria_judge(args, mock=mock)
    elif args.judge:
        if mock:
            judge = MockJudge()
        else:
            judge_backend: Backend = (
                OpenAICompatBackend(args.judge_url, name="judge", api_key_env=args.judge_key_env)
                if args.judge_url
                else AnthropicBackend(name="judge")
            )
            judge = (
                BlindPairwiseJudge(judge_backend, args.judge_model, family=args.judge_family)
                if args.blind_judge
                else LLMJudge(judge_backend, args.judge_model, family=args.judge_family)
            )

    toolbox = _mcp_toolbox(args) if args.mcp_servers else None
    if toolbox is not None:
        arms = [
            Arm(a.name, MCPAgentBackend(a.backend, toolbox, max_turns=args.max_turns), a.model,
                is_control=a.is_control)
            for a in arms
        ]  # fmt: skip
    try:
        result = run_arms(
            samples,
            arms=arms,
            repeats=args.repeats,
            pricing=pricing,
            baseline=args.baseline,
            judge=judge,
            capture=bool(args.save_transcript) or ctx is not None,
        )
    finally:
        if toolbox is not None:
            toolbox.close()
    if toolbox is not None:
        for server, why in toolbox.unreachable.items():
            result.warnings.append(f"MCP server {server} unreachable, its tools not offered: {why}")
        if args.tool_cassette:
            result.warnings.append(
                f"tool results replayed from {args.tool_cassette} where recorded"
                + ("; unrecorded calls returned an error" if args.cassette_only else "")
            )
    if args.save_transcript and result.transcript is not None:
        saved = result.transcript.save(args.save_transcript)
        print(f"transcript -> {saved}")
    if ctx is not None and result.transcript is not None:
        run_dir = write_run(args.corpus, result, result.transcript, ctx)
        print(f"corpus -> {run_dir}")
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


def _cmd_rejudge(args: argparse.Namespace) -> int:
    transcript = Transcript.load(args.transcript)
    source_dir = Path(args.transcript).resolve().parent
    ctx: RecordContext | None = None
    if args.corpus:
        root = find_corpus_root(source_dir)
        if root is None or root != Path(args.corpus).resolve():
            raise SystemExit(
                "instar: rejudge --corpus needs a transcript.json from a run inside "
                f"{args.corpus} (written by `instar arms --corpus`), so the new "
                "scores can point at the generations they judged"
            )
        ctx = load_run_context(source_dir)
    human = args.grades is not None
    if human and args.criteria:
        raise SystemExit("instar: use --grades or --criteria, not both")
    if human and (args.mock_judge or args.blind_judge or args.judge_url):
        raise SystemExit(
            "instar: --grades scores with a person's grades; it cannot be combined "
            "with --mock-judge, --blind-judge or --judge-url"
        )
    if human and not args.grader:
        raise SystemExit("instar: --grades needs --grader (a pseudonymous id such as grader-1)")
    if human:
        try:
            judge: Judge = HumanJudge.for_transcript(transcript, args.grades, args.grader)
        except (OSError, ValueError) as e:
            raise SystemExit(f"instar: {e}") from e
    elif args.criteria:
        judge = _criteria_judge(args, mock=args.mock_judge)
    elif args.mock_judge:
        judge = MockJudge()
    else:
        judge_backend: Backend = (
            OpenAICompatBackend(args.judge_url, name="judge", api_key_env=args.judge_key_env)
            if args.judge_url
            else AnthropicBackend(name="judge")
        )
        judge = (
            BlindPairwiseJudge(judge_backend, args.judge_model, family=args.judge_family)
            if args.blind_judge
            else LLMJudge(judge_backend, args.judge_model, family=args.judge_family)
        )
    pricing = load_pricing(args.pricing) if args.pricing else None
    result = rejudge(transcript, judge, pricing=pricing)
    if ctx is not None:
        run_dir = write_run(args.corpus, result, transcript, ctx, source_run_dir=source_dir)
        print(f"corpus -> {run_dir}")
    if human:
        default_label = f"rejudge-human-{_slug(args.grader)}"
    elif args.criteria:
        default_label = f"rejudge-criteria-{'mock' if args.mock_judge else _slug(args.judge_model)}"
    else:
        default_label = f"rejudge-{args.judge_model.replace('/', '-')}"
    label = args.label or default_label
    d = report_arms(result, label, mock=args.mock_judge, runs_dir=args.runs_dir)
    print(f"rejudge -> {d}")
    base = result.by_name(result.baseline)
    if human:
        assert isinstance(judge, HumanJudge)
        print(f"  judge: human ({judge.grader}), {len(judge.grades)} graded item(s)")
    else:
        named = "mock" if args.mock_judge else args.judge_model
        if args.criteria:
            named = f"criteria ({named}), absolute"
        print(f"  judge: {named}{' (blind)' if args.blind_judge and not args.mock_judge else ''}")
    for w in result.warnings:
        if "not scored by this judge" in w:
            print(f"  note: {w}")
    if base.quality_mean is not None:
        print(f"  {base.name:<16} baseline, quality {base.quality_mean:.3f} (n={base.quality_n})")
    else:
        print(f"  {base.name:<16} baseline")
    for s in result.arms:
        if s.name == result.baseline:
            continue
        q = "unscored" if s.quality_mean is None else f"{s.quality_mean:.3f} (n={s.quality_n})"
        print(f"  {s.name:<16} quality {q}")
    return 0


def _mcp_toolbox(args: argparse.Namespace) -> MCPToolbox:
    """Connect to the --mcp-servers for an agent run; one toolbox for every arm."""
    try:
        servers = load_servers(args.mcp_servers)
        cassette = ToolCassette.load(args.tool_cassette) if args.tool_cassette else None
    except (OSError, ValueError) as e:
        raise SystemExit(f"instar: {e}") from e
    if args.cassette_only and cassette is None:
        raise SystemExit("instar: --cassette-only needs --tool-cassette")
    toolbox = MCPToolbox(
        servers, cassette=cassette, cassette_only=args.cassette_only, record=args.record_tools
    )
    try:
        toolbox.open()
    except MCPError as e:
        raise SystemExit(f"instar: {e}") from e
    return toolbox


def _criteria_judge(args: argparse.Namespace, *, mock: bool) -> CriteriaJudge:
    """Build the absolute criteria judge from --criteria and the judge flags."""
    try:
        criteria = CriteriaSet.load(args.criteria)
    except (OSError, ValueError) as e:
        raise SystemExit(f"instar: {e}") from e
    if args.blind_judge:
        raise SystemExit(
            "instar: --criteria already hides provenance (the judge sees one answer); "
            "drop --blind-judge"
        )
    if mock:
        return CriteriaJudge(criteria, MockCriteriaBackend(), "mock-judge", family="mock")
    backend: Backend = (
        OpenAICompatBackend(args.judge_url, name="judge", api_key_env=args.judge_key_env)
        if args.judge_url
        else AnthropicBackend(name="judge")
    )
    return CriteriaJudge(criteria, backend, args.judge_model, family=args.judge_family)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text).strip("-") or "grader"


def _cmd_grade_sheet(args: argparse.Namespace) -> int:
    transcript = Transcript.load(args.transcript)
    try:
        n = write_grading_sheet(transcript, args.out, seed=args.seed, overwrite=args.force)
    except FileExistsError as e:
        raise SystemExit(f"instar: {e} (pass --force to replace it)") from e
    print(f"grade-sheet -> {args.out}")
    print(f"  {n} item(s) to grade; arm names and models are not shown to the grader")
    print("  fill the grade column with PASS, MARGINAL or FAIL (blank = not graded), then:")
    print(f"  instar rejudge {args.transcript} --grades {args.out} --grader <id>")
    return 0


CONTROL_ARM_NAME = "control"


def _control_arm(
    baseline: Arm, *, mock: bool, spec: dict[str, str] | None, extra_body: dict[str, Any] | None
) -> Arm:
    """A same-model control: the baseline's endpoint and model under another name.

    Built as a fresh backend rather than sharing the baseline's object, so the
    two arms are two independent calls that happen to ask the same model.
    """
    if mock or spec is None:
        backend: Backend = MockBackend(CONTROL_ARM_NAME, latency_s=0.010)
        return Arm(CONTROL_ARM_NAME, backend, baseline.model, is_control=True)
    built = _build_arm({**spec, "name": CONTROL_ARM_NAME}, extra_body=extra_body)
    return Arm(CONTROL_ARM_NAME, built.backend, built.model, is_control=True)


def _record_context(args: argparse.Namespace, *, mock: bool, traffic: str | None) -> RecordContext:
    if not args.tenant:
        raise SystemExit("instar: --corpus needs --tenant (whose workload this is)")
    workload = args.workload or (Path(traffic).stem if traffic else None)
    try:
        return RecordContext(
            tenant_id=args.tenant,
            upstream_consent=bool(args.upstream_consent),
            workload_id=workload,
            origin=args.origin,
            rubric_version=args.rubric_version,
            gold_version=args.gold_version,
            mock=mock,
            split_role=args.split_role,
        )
    except ValueError as e:
        raise SystemExit(f"instar: {e}") from e


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--traffic", help="workload fixture (.jsonl); defaults to a sample if present")
    p.add_argument("--live", action="store_true", help="use real endpoints instead of mocks")
    p.add_argument("--label", help="run label; output goes to <runs-dir>/<label>/")
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="where to write run output")


def _add_corpus_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--corpus",
        metavar="DIR",
        help="append this run to a measurement corpus at DIR (implies capturing "
        "generations). Holds raw model output: as private as the workload",
    )
    p.add_argument("--tenant", help="whose workload this is; required with --corpus")
    p.add_argument(
        "--upstream-consent",
        action="store_true",
        help="the tenant allows these records to be pooled into a shared corpus (default: no)",
    )
    p.add_argument("--workload", help="workload id (default: the traffic file's name)")
    p.add_argument(
        "--origin",
        default="coverage",
        choices=sorted(ORIGINS),
        help="where the tasks came from; a sample's meta.origin overrides it",
    )
    p.add_argument("--rubric-version", help="version of the rubric these scores are read against")
    p.add_argument("--gold-version", help="version of the workload's gold labels, if any")
    p.add_argument(
        "--split-role",
        choices=sorted(SPLIT_ROLES),
        help="what this workload is for: evolve (tuned on), held_out (checks tuning), "
        "or standard (the frozen yardstick, never tuned against)",
    )


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
        "--mcp-servers",
        metavar="JSON",
        help="give every arm the tools of these MCP servers and run each task as an "
        "agent loop (see Engineering/Docs/GUIDE-MCP-Measurement.md)",
    )
    arms.add_argument(
        "--max-turns", type=int, default=8, help="agent loop: model turns per task, at most"
    )
    arms.add_argument(
        "--tool-cassette",
        metavar="JSONL",
        help="agent loop: replay recorded tool results (from `mcp run --record` or "
        "--record-tools) so every arm reads the same tool output",
    )
    arms.add_argument(
        "--cassette-only",
        action="store_true",
        help="agent loop: never call a tool live; an unrecorded call returns an error",
    )
    arms.add_argument(
        "--record-tools",
        metavar="JSONL",
        help="agent loop: append every live tool result to this file (a cassette)",
    )
    arms.add_argument(
        "--criteria",
        metavar="JSON",
        help="score every arm, baseline included, against a criteria checklist "
        "(absolute; implies --judge). See Engineering/Docs/GUIDE-Criteria-Judge.md",
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
    arms.add_argument(
        "--control",
        action="store_true",
        help="add a same-model control arm (the baseline's endpoint and model). Its "
        "score is the judge's own error, which is what makes the other scores readable",
    )
    arms.add_argument(
        "--judge-family",
        help="the judge model's vendor family, when the automatic guess is wrong or unknown",
    )
    _add_corpus_args(arms)
    arms.add_argument(
        "--save-transcript",
        metavar="PATH",
        help="write every generation to PATH so the run can be re-judged later "
        "without paying to regenerate it (see the rejudge command). Holds raw "
        "model output: as private as the workload it came from.",
    )

    rej = sub.add_parser(
        "rejudge",
        help="re-score a saved arms transcript with a different judge (no new generations)",
        description="Score saved generations again with another judge. Cost and "
        "latency are replayed unchanged from the original run, so any movement "
        "in quality is the judge's doing and nothing else's — which is the only "
        "way to tell a claim about your models from a quirk of your judge.",
    )
    rej.add_argument("transcript", help="transcript written by `instar arms --save-transcript`")
    rej.add_argument("--label", help="run label; output goes to <runs-dir>/<label>/")
    rej.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="where to write run output")
    rej.add_argument("--judge-model", default=DEFAULT_STRONG, help="model that judges")
    rej.add_argument("--judge-url", help="OpenAI-compatible endpoint for the judge")
    rej.add_argument("--judge-key-env", help="env var holding the judge's API key")
    rej.add_argument(
        "--blind-judge",
        action="store_true",
        help="hide which answer came from which arm and shuffle their order",
    )
    rej.add_argument("--pricing", help="pricing table JSON for arms that report no cost")
    rej.add_argument(
        "--judge-family",
        help="the judge model's vendor family, when the automatic guess is wrong or unknown",
    )
    rej.add_argument(
        "--corpus",
        metavar="DIR",
        help="append the new scores to the corpus the transcript came from; the "
        "tenant and other context are read from the original run",
    )
    rej.add_argument(
        "--mock-judge",
        action="store_true",
        help="score with the deterministic mock judge; measures nothing, exercises the path",
    )
    rej.add_argument(
        "--criteria",
        metavar="JSON",
        help="re-score against a criteria checklist (absolute; the baseline is scored too)",
    )
    rej.add_argument(
        "--grades",
        metavar="CSV",
        help="score with a person's filled grading sheet (from `instar grade-sheet`) "
        "instead of a model judge",
    )
    rej.add_argument(
        "--grader",
        help="pseudonymous id for the person who graded (e.g. grader-1); recorded as "
        "the judge, so not a name or email",
    )
    rej.set_defaults(func=_cmd_rejudge)

    gs = sub.add_parser(
        "grade-sheet",
        help="export a blind grading sheet (CSV) from a saved arms transcript",
        description="Write one row per (prompt, candidate answer) with the reference "
        "answer beside it, shuffled and with no arm names or models, for a person to "
        "grade PASS / MARGINAL / FAIL in a spreadsheet. Score it back with "
        "`instar rejudge --grades`.",
    )
    gs.add_argument("transcript", help="transcript written by `instar arms --save-transcript`")
    gs.add_argument("-o", "--out", default="grading-sheet.csv", help="where to write the CSV")
    gs.add_argument("--seed", type=int, default=0, help="shuffle seed (the order reproduces)")
    gs.add_argument(
        "--force", action="store_true", help="replace an existing file (it may hold grades)"
    )
    gs.set_defaults(func=_cmd_grade_sheet)

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

    add_corpus_parser(sub)
    add_mcp_parser(sub)

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
