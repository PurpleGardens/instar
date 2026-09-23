# SPDX-License-Identifier: Apache-2.0
"""Render MCP measurements to ``<runs_dir>/<label>/`` as JSON and Markdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from instar.mcp.probe import ServerProbe
from instar.mcp.toolcalls import ToolCallsResult
from instar.reporters.markdown import DEFAULT_RUNS_DIR, _write

TOKEN_NOTE = (
    "_Token counts are **estimates** (about four characters per token). Each "
    "client serialises tool definitions differently and each model tokenises "
    "differently; use these to compare servers and tools, not as a bill._"
)


def _ms(x: float | None) -> str:
    return "-" if x is None else f"{x:.1f}"


def _probe_lines(probes: list[ServerProbe], price_model: str | None) -> list[str]:
    lines = ["## Tool definitions: the cost before any question", ""]
    header = "| server | transport | tools | est. tokens | init ms | list ms |"
    sep = "|---|---|---|---|---|---|"
    if price_model:
        header += f" $ per 1k turns ({price_model}) |"
        sep += "---|"
    lines += [header, sep]
    for p in probes:
        if not p.ok:
            lines.append(f"| {p.server} | {p.transport} | **unreachable**: {p.error} | | | |")
            continue
        row = (
            f"| {p.server} | {p.transport} | {len(p.tools)} | {p.total_tokens} | "
            f"{p.init_ms:.1f} | {p.list_ms:.1f} |"
        )
        if price_model:
            cost = p.cost_per_1k_turns_usd(price_model)
            row += " unpriced |" if cost is None else f" ${cost:.4f} |"
        lines.append(row)
    lines += [
        "",
        "Every definition is sent to the model on every turn of an agent loop, "
        "whether or not the tool is used.",
    ]
    for p in probes:
        if not p.ok or not p.tools:
            continue
        lines += [
            "",
            f"### `{p.server}`",
            "",
            "| tool | est. tokens | description | schema | read-only | notes |",
            "|---|---|---|---|---|---|",
        ]
        for t in sorted(p.tools, key=lambda t: -t.total_tokens):
            ro = "destructive" if t.destructive else ("yes" if t.read_only else "no")
            lines.append(
                f"| `{t.name}` | {t.total_tokens} | {t.description_tokens} | "
                f"{t.schema_tokens} | {ro} | {'; '.join(t.notes)} |"
            )
    return lines


def report_probe(
    probes: list[ServerProbe],
    label: str,
    *,
    price_model: str | None = None,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
) -> Path:
    payload: dict[str, Any] = {
        "servers": [p.to_json() for p in probes],
        "price_model": price_model,
        "token_counts": "estimated (~4 characters per token)",
    }
    md = [f"# MCP probe: {label}", "", *_probe_lines(probes, price_model), "", TOKEN_NOTE, ""]
    return _write(label, payload, "\n".join(md), runs_dir)


def report_toolcalls(
    result: ToolCallsResult,
    label: str,
    *,
    price_model: str | None = None,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
) -> Path:
    md = [f"# MCP tool calls: {label}", "", f"repeats: {result.repeats}", ""]
    if result.warnings:
        md += ["## Warnings", ""] + [f"- {w}" for w in result.warnings] + [""]
    md += [
        "## Per tool",
        "",
        "| server | tool | calls | ok | tool err | transport err | refused | skipped | "
        "p50 ms | p95 ms | mean resp tokens | max resp tokens | checks |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in result.tool_stats():
        checks = f"{s.checks_passed}/{s.checks_total}" if s.checks_total else "none"
        mean = "-" if s.mean_tokens is None else f"{s.mean_tokens:.0f}"
        md.append(
            f"| {s.server} | `{s.tool}` | {s.n} | {s.ok} | {s.tool_errors} | "
            f"{s.transport_errors} | {s.refused} | {s.skipped} | {_ms(s.p50_ms)} | "
            f"{_ms(s.p95_ms)} | {mean} | {s.max_tokens if s.max_tokens is not None else '-'} | "
            f"{checks} |"
        )
    md += [
        "",
        "Response tokens are what each result adds to the model's context. A "
        "`tool err` is the tool answering that it failed (`isError`); a "
        "`transport err` is the call not completing at all.",
        "",
        "## Calls that failed a check, errored, or were refused",
        "",
        "| call | server | repeat | status | detail |",
        "|---|---|---|---|---|",
    ]
    flagged = [o for o in result.outcomes if o.failed or o.status not in ("ok",)]
    for o in flagged:
        detail = o.detail or ("failed: " + "; ".join(o.failed) if o.failed else "")
        md.append(f"| {o.call_id} | {o.server} | {o.repeat} | {o.status} | {detail} |")
    if not flagged:
        md.append("| - | | | | none |")
    md += ["", *_probe_lines(result.servers, price_model), "", TOKEN_NOTE, ""]
    payload = {**result.to_json(), "price_model": price_model}
    return _write(label, payload, "\n".join(md), runs_dir)
