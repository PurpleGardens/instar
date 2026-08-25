# SPDX-License-Identifier: Apache-2.0
"""Render results to ``<runs_dir>/<label>/`` as JSON, Markdown, and CSV.

Every report carries its own caveats. A mock run says on its face that its
numbers are placeholders; a run with failed calls or unpriced models says so
above the results table, not in a footnote. This is deliberate: these files get
pasted into decks and forwarded to people who never ran the tool, and a number
that travels without its caveat becomes a fact it was never entitled to be.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from instar.core.arms import COST_UNAVAILABLE, ArmsResult
from instar.core.gateway import GatewayResult
from instar.core.route import RouteResult, SweepPoint
from instar.rubrics.spec import RubricVerdict

DEFAULT_RUNS_DIR = "runs"

MOCK_BANNER = (
    "> **MOCK RUN — not a measurement.** Outputs are deterministic synthetic text and "
    "costs come from placeholder pricing. Use this to verify the pipeline, never to "
    "make a decision."
)

PLACEHOLDER_PRICING_CAVEAT = (
    "> **Costs use Instar's built-in placeholder pricing table.** Those rates are "
    "illustrative and go stale the moment a provider changes a price. Supply your own "
    "with `--pricing` before quoting any dollar figure."
)

CUSTOM_PRICING_NOTE = (
    "_Costs use the pricing table you supplied with `--pricing`. Token counts are in "
    "the per-call table below, so the arithmetic can be checked._"
)


def _pricing_caveat(custom_pricing: bool) -> str:
    return CUSTOM_PRICING_NOTE if custom_pricing else PLACEHOLDER_PRICING_CAVEAT


def _run_dir(label: str, runs_dir: str | Path = DEFAULT_RUNS_DIR) -> Path:
    d = Path(runs_dir) / label
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(
    label: str,
    payload: dict[str, Any],
    markdown: str,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
) -> Path:
    d = _run_dir(label, runs_dir)
    (d / "result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (d / "report.md").write_text(markdown, encoding="utf-8")
    return d


def _warning_block(warnings: list[str], error_count: int = 0, n: int = 0) -> list[str]:
    lines: list[str] = []
    if error_count:
        lines.append(
            f"> **{error_count}/{n} calls FAILED.** They are excluded from every figure "
            f"below. Re-run clean before trusting these numbers."
        )
        lines.append("")
    for w in warnings:
        lines.append(f"> **Warning:** {w}")
        lines.append("")
    return lines


VERDICT_MARK = {"pass": "PASS", "marginal": "MARGINAL", "fail": "FAIL", "unmeasured": "UNMEASURED"}


def _rubric_block(verdict: RubricVerdict) -> list[str]:
    """Render the rubric verdict. It goes at the TOP of the report: the decision
    is what the reader came for, and burying it under the raw numbers invites
    them to find their own story in the data instead."""
    lines = [
        f"## Verdict: {VERDICT_MARK.get(verdict.verdict, verdict.verdict.upper())}",
        "",
        f"Rubric: **{verdict.rubric}**",
        "",
        "| dimension | measured | bar | result |",
        "|---|---|---|---|",
    ]
    for d in verdict.dimensions:
        value = "not measured" if d.value is None else f"{d.value:,.4g}"
        comparator = ">=" if d.direction == "higher_is_better" else "<="
        bar = f"{comparator} {d.pass_at:,.4g}"
        if d.marginal_at is not None:
            bar += f" (marginal {comparator} {d.marginal_at:,.4g})"
        lines.append(
            f"| {d.label} | {value} | {bar} | **{VERDICT_MARK.get(d.verdict, d.verdict)}** |"
        )
    lines += [
        "",
        "_Overall verdict is the **worst** dimension, never an average — a large saving "
        "must not be able to hide a failing quality score. A dimension that could not be "
        "measured is not a pass._",
        "",
    ]
    for note in verdict.notes:
        lines += [f"> **Note:** {note}", ""]
    return lines


def report_route(
    result: RouteResult,
    label: str,
    *,
    mock: bool,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
    rubric_verdict: RubricVerdict | None = None,
    custom_pricing: bool = False,
) -> Path:
    """Write the routing report. Returns the run directory."""
    c = result.cost
    mq_weak = (
        "n/a (nothing routed weak)"
        if result.mean_quality_routed_weak is None
        else f"{result.mean_quality_routed_weak:.3f}"
    )
    lines = [
        f"# Routing run — {'MOCK' if mock else 'LIVE'}",
        "",
        f"- policy: **{result.policy}**",
        f"- strong: `{result.strong_model}` · weak: `{result.weak_model}`",
        f"- samples: {result.n} · routed to weak: {result.weak_count}",
        "",
    ]
    if mock:
        lines += [MOCK_BANNER, ""]
    lines += _warning_block(result.warnings, result.error_count, result.n)
    if rubric_verdict is not None:
        lines += _rubric_block(rubric_verdict)
    lines += [
        "| metric | value |",
        "|---|---|",
        f"| baseline (all-strong) cost | ${c.baseline_usd:.6f} |",
        f"| routed cost | ${c.routed_usd:.6f} |",
        f"| **spend saved** | **${c.saved_usd:.6f} ({c.saved_pct:.1f}%)** |",
        f"| mean quality (all calls) | {result.mean_quality_all:.3f} |",
        f"| **mean quality (routed-weak only)** | **{mq_weak}** |",
        "",
        "_Mean quality over all calls is diluted by every call that stayed strong and "
        "scored 1.0 by definition. The routed-weak figure is where quality risk actually "
        "lives — read that one._",
        "",
        _pricing_caveat(custom_pricing),
        "",
        "## Per-call decisions",
        "",
        "| id | feature | category | target | tokens (in/out) | cost | latency | quality | why |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for o in result.outcomes:
        cost = "—" if not o.ok else f"${o.routed_usd:.6f}"
        latency = "—" if not o.ok else f"{o.routed_latency_s * 1000.0:.0f} ms"
        quality = "—" if not o.ok else f"{o.quality:.2f}"
        why = o.error if not o.ok else (o.rationale or o.reason)
        tokens = "—" if not o.ok else f"{o.routed_input_tokens}/{o.routed_output_tokens}"
        lines.append(
            f"| `{o.id}` | {o.feature} | {o.category} | {o.target} | {tokens} | {cost} | "
            f"{latency} | {quality} | {why} |"
        )
    lines.append("")

    payload = result.to_json()
    if rubric_verdict is not None:
        payload["rubric"] = rubric_verdict.to_json()
    return _write(label, payload, "\n".join(lines), runs_dir)


def report_sweep(
    points: list[SweepPoint],
    label: str,
    *,
    mock: bool,
    strong_model: str,
    weak_model: str,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
    custom_pricing: bool = False,
) -> Path:
    """Write the cost/quality sweep, including a CSV for charting."""
    lines = [
        f"# Cost/quality sweep — {'MOCK' if mock else 'LIVE'}",
        "",
        f"strong: `{strong_model}` · weak: `{weak_model}`",
        "",
    ]
    if mock:
        lines += [MOCK_BANNER, ""]
    lines += [
        "| threshold | spend saved | quality (all) | quality (routed-weak) | routed weak |",
        "|---|---|---|---|---|",
    ]
    for p in points:
        mq_weak = (
            "n/a" if p.mean_quality_routed_weak is None else f"{p.mean_quality_routed_weak:.3f}"
        )
        lines.append(
            f"| {p.threshold:.2f} | {p.saved_pct:.1f}% | {p.mean_quality_all:.3f} | "
            f"{mq_weak} | {p.weak_count} |"
        )
    lines += [
        "",
        "_Raising the threshold sends more calls to the weak model: more savings, lower "
        "quality. The useful reading is where the routed-weak quality column starts to "
        "fall away — that is your workload's actual budget for cheapness, and it is not "
        "transferable to anyone else's workload._",
        "",
        _pricing_caveat(custom_pricing),
        "",
    ]

    payload: dict[str, Any] = {
        "strong_model": strong_model,
        "weak_model": weak_model,
        "mock": mock,
        "points": [vars(p) for p in points],
    }
    d = _write(label, payload, "\n".join(lines), runs_dir)

    with (d / "sweep.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["threshold", "saved_pct", "mean_quality_all", "mean_quality_routed_weak", "weak_count"]
        )
        for p in points:
            writer.writerow(
                [
                    p.threshold,
                    p.saved_pct,
                    p.mean_quality_all,
                    "" if p.mean_quality_routed_weak is None else p.mean_quality_routed_weak,
                    p.weak_count,
                ]
            )
    return d


def report_arms(
    result: ArmsResult,
    label: str,
    *,
    mock: bool,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
) -> Path:
    """Write the N-way arms comparison: latency and cost, per arm."""
    lines = [
        f"# Arms comparison — {'MOCK' if mock else 'LIVE'}",
        "",
        f"calls per arm: {result.n} · baseline: `{result.baseline}`",
        "",
    ]
    if mock:
        lines += [MOCK_BANNER, ""]
    lines += _warning_block(result.warnings)
    lines += [
        "## Latency",
        "",
        "| arm | model | ok | failed | p50 ms | p95 ms | p99 ms | mean ms | out tok | ms/out tok |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in result.arms:
        lines.append(
            f"| {s.name} | `{s.model}` | {s.n_ok} | {s.n_err} | {s.p50_ms:.1f} | "
            f"{s.p95_ms:.1f} | {s.p99_ms:.1f} | {s.mean_ms:.1f} | {s.output_tokens} | "
            f"{s.ms_per_output_token:.1f} |"
        )
    lines += [
        "",
        "_Most of a generative call is spent emitting tokens, so raw percentiles "
        "compare arms only when their output lengths do. When `out tok` differs "
        "materially, `ms/out tok` is the length-independent view._",
    ]
    lines += [
        "",
        "## Cost",
        "",
        "| arm | total USD | USD / 1k calls | source |",
        "|---|---|---|---|",
    ]
    for s in result.arms:
        if s.cost_source == COST_UNAVAILABLE:
            lines.append(f"| {s.name} | unknown | unknown | {s.cost_source} |")
        else:
            lines.append(
                f"| {s.name} | ${s.cost_usd:.6f} | ${s.cost_per_1k_calls_usd:.4f} | "
                f"{s.cost_source} |"
            )
    lines += [
        "",
        "_`provider_reported` is what the endpoint charged; `computed` is our "
        "pricing table's estimate; `unavailable` means the cost columns are "
        "missing, not zero._",
        "",
        f"## Against `{result.baseline}`",
        "",
        "| arm | p50 Δ ms | p95 Δ ms | ms/out tok Δ | out tok ratio | cost Δ |",
        "|---|---|---|---|---|---|",
    ]
    for d in result.deltas():
        pct = d["cost_delta_pct"]
        cost_cell = "n/a" if pct is None else f"{pct:+.1f}%"
        lines.append(
            f"| {d['arm']} | {d['latency_p50_delta_ms']:+.1f} | "
            f"{d['latency_p95_delta_ms']:+.1f} | "
            f"{d['ms_per_output_token_delta']:+.2f} | "
            f"{d['output_token_ratio']:.2f}x | {cost_cell} |"
        )
    lines += [
        "",
        "_Wall-clock from the client, so this includes the network path to each "
        "endpoint. If the arms are not on comparable footing (one local, one "
        "across the internet), this measures geography, not software._",
        "",
    ]
    return _write(label, result.to_json(), "\n".join(lines), runs_dir)


def report_gateway(
    result: GatewayResult,
    label: str,
    *,
    mock: bool,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
) -> Path:
    """Write the gateway A/B latency comparison."""
    lines = [
        f"# Gateway comparison — {'MOCK' if mock else 'LIVE'}",
        "",
        f"model: `{result.model}` · calls per arm: {result.n}",
        "",
    ]
    if mock:
        lines += [MOCK_BANNER, ""]
    lines += _warning_block(result.warnings)
    lines += [
        "## Latency",
        "",
        "| arm | ok | failed | p50 ms | p95 ms | p99 ms | mean ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in (result.a, result.b):
        lines.append(
            f"| {s.backend} | {s.n_ok} | {s.n_err} | {s.p50_ms:.1f} | {s.p95_ms:.1f} | "
            f"{s.p99_ms:.1f} | {s.mean_ms:.1f} |"
        )
    lines += [
        "",
        f"**Overhead ({result.a.backend} minus {result.b.backend}):** "
        f"p50 {result.overhead_p50_ms:+.1f} ms · "
        f"p95 {result.overhead_p95_ms:+.1f} ms · "
        f"p99 {result.overhead_p99_ms:+.1f} ms",
        "",
        "_Wall-clock from the client, so this includes the network path to each endpoint. "
        "If the two arms are not on comparable footing (one local, one across the "
        "internet), this measures geography, not software._",
        "",
    ]
    return _write(label, result.to_json(), "\n".join(lines), runs_dir)
