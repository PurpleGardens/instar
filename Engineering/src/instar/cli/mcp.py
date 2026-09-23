# SPDX-License-Identifier: Apache-2.0
"""``instar mcp``: measure MCP servers without a model.

- ``instar mcp probe``  size every tool definition a server exposes
- ``instar mcp run``    replay recorded tool calls; latency, errors, size, checks
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from instar.mcp.client import load_servers
from instar.mcp.probe import probe
from instar.mcp.toolcalls import OK, REFUSED, TOOL_ERROR, load_calls, run_toolcalls
from instar.reporters import DEFAULT_RUNS_DIR
from instar.reporters.mcp import report_probe, report_toolcalls


def _servers(args: argparse.Namespace) -> Any:
    try:
        servers = load_servers(args.servers)
    except (OSError, ValueError) as e:
        raise SystemExit(f"instar: {e}") from e
    if args.server:
        wanted = set(args.server)
        missing = sorted(wanted - {s.name for s in servers})
        if missing:
            raise SystemExit(f"instar: no server(s) named {missing} in {args.servers}")
        servers = [s for s in servers if s.name in wanted]
    return servers


def _cmd_probe(args: argparse.Namespace) -> int:
    probes = [probe(s) for s in _servers(args)]
    d = report_probe(probes, args.label, price_model=args.price_model, runs_dir=args.runs_dir)
    print(f"mcp probe -> {d}")
    for p in probes:
        if not p.ok:
            print(f"  {p.server:<16} unreachable: {p.error}")
            continue
        extra = ""
        if args.price_model:
            cost = p.cost_per_1k_turns_usd(args.price_model)
            extra = "  unpriced" if cost is None else f"  ${cost:.4f} per 1k turns"
        print(f"  {p.server:<16} {len(p.tools)} tools  ~{p.total_tokens} tokens{extra}")
    print("  token counts are estimates (~4 chars/token)")
    return 0 if all(p.ok for p in probes) else 1


def _cmd_run(args: argparse.Namespace) -> int:
    servers = _servers(args)
    try:
        calls = load_calls(args.calls)
        result = run_toolcalls(
            calls, servers, repeats=args.repeats, dry_run=args.dry_run, record=args.record
        )
    except (OSError, ValueError) as e:
        raise SystemExit(f"instar: {e}") from e
    d = report_toolcalls(result, args.label, price_model=args.price_model, runs_dir=args.runs_dir)
    print(f"mcp run -> {d}")
    for s in result.tool_stats():
        checks = f"checks {s.checks_passed}/{s.checks_total}" if s.checks_total else "no checks"
        p50 = "-" if s.p50_ms is None else f"{s.p50_ms:.1f}ms"
        tok = "-" if s.mean_tokens is None else f"~{s.mean_tokens:.0f} tok"
        state = f"ok {s.ok}/{s.n}"
        if s.refused:
            state += f", refused {s.refused}"
        if s.tool_errors:
            state += f", tool err {s.tool_errors}"
        print(f"  {s.server:<14} {s.tool:<22} {state:<18} p50 {p50:<9} {tok:<9} {checks}")
    if args.record and not args.dry_run:
        print(f"  results recorded -> {args.record}")
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    broken = any(o.status not in (OK, TOOL_ERROR, REFUSED) for o in result.outcomes)
    return 1 if (broken and not args.dry_run) else 0


def _common(p: argparse.ArgumentParser, label: str) -> None:
    p.add_argument("--servers", required=True, metavar="JSON", help="server config file")
    p.add_argument("--server", action="append", help="only this server (repeatable); default: all")
    p.add_argument(
        "--price-model",
        help="also price the definitions' input tokens per 1k turns at this model's rate",
    )
    p.add_argument("--label", default=label, help="run label; output goes to <runs-dir>/<label>/")
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="where to write run output")


def add_mcp_parser(sub: Any) -> None:
    """Register ``instar mcp`` and its subcommands."""
    mcp = sub.add_parser(
        "mcp",
        help="measure MCP servers: definition size, and tool calls replayed without a model",
        description="Measure MCP servers directly, with no model in the loop. See "
        "Engineering/Docs/GUIDE-MCP-Measurement.md.",
    )
    msub = mcp.add_subparsers(dest="mcp_cmd", required=True)

    pr = msub.add_parser(
        "probe",
        help="list every tool and size its definition (what it costs the model per turn)",
    )
    _common(pr, "mcp-probe")
    pr.set_defaults(func=_cmd_probe)

    run = msub.add_parser(
        "run",
        help="replay a tool-call fixture against the servers; latency, errors, size, checks",
    )
    _common(run, "mcp-run")
    run.add_argument("--calls", required=True, metavar="JSONL", help="tool-call fixture")
    run.add_argument("--repeats", type=int, default=1, help="replay the fixture N times")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="connect and check every call (tool exists, permitted, arguments) but call nothing",
    )
    run.add_argument(
        "--record",
        metavar="JSONL",
        help="append every raw tool result to this file, for replay without the server",
    )
    run.set_defaults(func=_cmd_run)
