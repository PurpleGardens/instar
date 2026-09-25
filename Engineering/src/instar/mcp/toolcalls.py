# SPDX-License-Identifier: Apache-2.0
"""Replay recorded tool calls straight at MCP servers, no model involved.

A tool-call fixture is JSONL, one call per line::

    {"id": "t1", "tool": "lookup_order", "arguments": {"order_id": "A-100"},
     "expect": {"is_error": false, "contains": ["shipped"], "max_tokens": 200}}

``server`` is optional. A call that names one runs against that server only;
a call that doesn't runs against **every** configured server, which is how two
servers doing the same job are compared (or one server reached directly and
through a gateway). Calls are interleaved across servers, for the reason the
LLM runners interleave arms: sequential blocks charge any drift to whichever
server went last.

Per call it records wall-clock latency, whether the transport failed, whether
the tool reported an error (``isError``), how big the response is (what lands
in the model's context), and which ``expect`` checks passed.

**Safety.** A measurement must not move money or delete data. A tool is called
only if the server marks it read-only (``readOnlyHint: true``, and not
``destructiveHint``) or the server's config lists it under ``allow``. Anything
else is *refused* and reported as refused, never silently skipped. Many servers
don't annotate their tools at all, so expect to allow-list read tools by hand
after reading what they do.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from instar.core.gateway import percentile
from instar.mcp.client import MCPClient, MCPError, ServerSpec
from instar.mcp.probe import ServerProbe, probe_tools
from instar.providers.base import estimate_tokens

_EXPECT_KEYS = {"is_error", "contains", "not_contains", "max_tokens", "structured"}


@dataclass(frozen=True)
class ToolCall:
    """One recorded tool call, and what a good result looks like."""

    id: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    server: str | None = None
    expect: Mapping[str, Any] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: Mapping[str, Any], where: str = "call") -> ToolCall:
        for key in ("id", "tool"):
            if not isinstance(d.get(key), str) or not d[key]:
                raise ValueError(f"{where}: '{key}' is required")
        expect = d.get("expect") or {}
        unknown = set(expect) - _EXPECT_KEYS
        if unknown:
            raise ValueError(
                f"{where}: unknown expect key(s) {sorted(unknown)}; known: {sorted(_EXPECT_KEYS)}"
            )
        args = d.get("arguments") or {}
        if not isinstance(args, Mapping):
            raise ValueError(f"{where}: 'arguments' must be an object")
        return cls(
            id=str(d["id"]),
            tool=str(d["tool"]),
            arguments=dict(args),
            server=None if d.get("server") is None else str(d["server"]),
            expect=dict(expect),
            meta=dict(d.get("meta") or {}),
        )


def load_calls(path: str | Path) -> list[ToolCall]:
    p = Path(path)
    calls: list[ToolCall] = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{n}: not valid JSON ({e})") from e
        calls.append(ToolCall.from_json(d, where=f"{p}:{n}"))
    if not calls:
        raise ValueError(f"{p}: no calls")
    ids = [c.id for c in calls]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"{p}: duplicate call id(s) {dupes}")
    return calls


def call_permitted(tool: Mapping[str, Any], allow: frozenset[str]) -> tuple[bool, str]:
    """May this tool be called during a measurement? And why (not)."""
    name = str(tool.get("name"))
    ann = tool.get("annotations") or {}
    if name in allow:
        return True, "allowed by name in the server config"
    if ann.get("destructiveHint"):
        return False, "marked destructive; add it to the server's 'allow' list to call it"
    if ann.get("readOnlyHint") is True:
        return True, "marked read-only"
    return False, "not marked read-only; add it to the server's 'allow' list to call it"


def response_text(result: Mapping[str, Any]) -> str:
    """The text a model would be shown from a tool result's content blocks."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "resource":
            res = block.get("resource") or {}
            parts.append(str(res.get("text", "")))
        else:
            # Images, audio, links: size them by their serialised form.
            parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(parts)


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            k in actual and _subset(v, actual[k]) for k, v in expected.items()
        )
    return bool(expected == actual)


def check_expectations(
    expect: Mapping[str, Any], result: Mapping[str, Any], text: str, tokens: int
) -> tuple[list[str], list[str]]:
    """(passed, failed) check descriptions. An empty ``expect`` checks nothing."""
    passed: list[str] = []
    failed: list[str] = []

    def record(ok: bool, label: str) -> None:
        (passed if ok else failed).append(label)

    if "is_error" in expect:
        want = bool(expect["is_error"])
        record(bool(result.get("isError", False)) == want, f"is_error={want}")
    for s in expect.get("contains", []):
        record(str(s) in text, f"contains {s!r}")
    for s in expect.get("not_contains", []):
        record(str(s) not in text, f"not contains {s!r}")
    if "max_tokens" in expect:
        record(tokens <= int(expect["max_tokens"]), f"<= {int(expect['max_tokens'])} tokens")
    if "structured" in expect:
        record(
            _subset(expect["structured"], result.get("structuredContent")),
            "structuredContent matches",
        )
    return passed, failed


# Outcomes, in the order a reader should worry about them.
REFUSED = "refused"
SKIPPED = "skipped"
TRANSPORT_ERROR = "transport_error"
TOOL_ERROR = "tool_error"
OK = "ok"


@dataclass
class CallOutcome:
    call_id: str
    server: str
    tool: str
    repeat: int
    status: str
    detail: str | None = None
    latency_ms: float | None = None
    response_chars: int = 0
    response_tokens: int = 0
    structured_tokens: int = 0
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def checked(self) -> bool:
        return bool(self.passed or self.failed)


@dataclass
class ToolStats:
    server: str
    tool: str
    n: int
    ok: int
    tool_errors: int
    transport_errors: int
    refused: int
    skipped: int
    p50_ms: float | None
    p95_ms: float | None
    mean_tokens: float | None
    max_tokens: int | None
    checks_passed: int
    checks_total: int


@dataclass
class ToolCallsResult:
    servers: list[ServerProbe]
    outcomes: list[CallOutcome]
    repeats: int
    warnings: list[str] = field(default_factory=list)

    def tool_stats(self) -> list[ToolStats]:
        groups: dict[tuple[str, str], list[CallOutcome]] = {}
        for o in self.outcomes:
            groups.setdefault((o.server, o.tool), []).append(o)
        out: list[ToolStats] = []
        for (server, tool), rows in groups.items():
            answered = [o for o in rows if o.status in (OK, TOOL_ERROR)]
            lat = [o.latency_ms for o in answered if o.latency_ms is not None]
            toks = [o.response_tokens for o in answered]
            out.append(
                ToolStats(
                    server=server,
                    tool=tool,
                    n=len(rows),
                    ok=sum(1 for o in rows if o.status == OK),
                    tool_errors=sum(1 for o in rows if o.status == TOOL_ERROR),
                    transport_errors=sum(1 for o in rows if o.status == TRANSPORT_ERROR),
                    refused=sum(1 for o in rows if o.status == REFUSED),
                    skipped=sum(1 for o in rows if o.status == SKIPPED),
                    p50_ms=percentile(lat, 50) if lat else None,
                    p95_ms=percentile(lat, 95) if lat else None,
                    mean_tokens=(sum(toks) / len(toks)) if toks else None,
                    max_tokens=max(toks) if toks else None,
                    checks_passed=sum(len(o.passed) for o in rows),
                    checks_total=sum(len(o.passed) + len(o.failed) for o in rows),
                )
            )
        out.sort(key=lambda s: (s.server, s.tool))
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "repeats": self.repeats,
            "servers": [s.to_json() for s in self.servers],
            "tools": [asdict(t) for t in self.tool_stats()],
            "calls": [asdict(o) for o in self.outcomes],
            "warnings": self.warnings,
            "token_counts": "estimated (~4 characters per token)",
        }


def _missing_required(tool: Mapping[str, Any], args: Mapping[str, Any]) -> list[str]:
    schema = tool.get("inputSchema") or {}
    if not isinstance(schema, Mapping):
        return []
    required = schema.get("required") or []
    return [str(r) for r in required if r not in args]


def run_toolcalls(
    calls: Sequence[ToolCall],
    servers: Sequence[ServerSpec],
    *,
    repeats: int = 1,
    dry_run: bool = False,
    record: str | Path | None = None,
) -> ToolCallsResult:
    """Replay ``calls`` against ``servers``, interleaved, ``repeats`` times.

    ``dry_run`` connects and lists tools but calls nothing: every call is
    reported as it *would* be treated (refused, skipped for a missing tool or
    argument, or ready). ``record`` appends every raw tool result to a JSONL
    cassette, so later phases can replay tool output without the server.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    names = {s.name for s in servers}
    stray = sorted({c.server for c in calls if c.server and c.server not in names})
    if stray:
        raise ValueError(f"calls name unknown server(s) {stray}; configured: {sorted(names)}")

    clients: dict[str, MCPClient] = {}
    catalogs: dict[str, dict[str, Mapping[str, Any]]] = {}
    probes: list[ServerProbe] = []
    warnings: list[str] = []
    outcomes: list[CallOutcome] = []
    with contextlib.ExitStack() as stack:
        tape = stack.enter_context(Path(record).open("a", encoding="utf-8")) if record else None

        # Clients close before the tape does, and even if a call raises.
        def close_clients() -> None:
            for c in clients.values():
                c.close()

        stack.callback(close_clients)
        for spec in servers:
            client = MCPClient(spec)
            try:
                client.open()
                listed = client.list_tools()
            except MCPError as e:
                client.close()
                probes.append(
                    ServerProbe(server=spec.name, transport=spec.transport, ok=False, error=str(e))
                )
                warnings.append(f"{spec.name}: could not connect ({e}); its calls are skipped")
                continue
            clients[spec.name] = client
            tools = [t for t in listed.result["tools"] if isinstance(t, Mapping)]
            catalogs[spec.name] = {str(t.get("name")): t for t in tools}
            probes.append(
                probe_tools(
                    spec,
                    tools,
                    init_s=client.init_latency_s,
                    list_s=listed.latency_s,
                    server_info=client.server_info,
                    protocol=client.protocol_version,
                )
            )

        spec_by_name = {s.name: s for s in servers}
        for repeat in range(repeats):
            for call in calls:
                targets = [call.server] if call.server else [s.name for s in servers]
                for server in targets:
                    outcomes.append(
                        _one_call(
                            call,
                            server,
                            repeat,
                            clients.get(server),
                            catalogs.get(server, {}),
                            spec_by_name[server].allow,
                            dry_run=dry_run,
                            tape=tape,
                        )
                    )

    refused = sorted({f"{o.server}:{o.tool}" for o in outcomes if o.status == REFUSED})
    if refused:
        warnings.append(
            f"refused to call {len(refused)} tool(s) not marked read-only: " + ", ".join(refused)
        )
    unchecked = sum(1 for o in outcomes if o.status in (OK, TOOL_ERROR) and not o.checked)
    if unchecked:
        warnings.append(
            f"{unchecked} call(s) had no 'expect' checks - their results were measured "
            "for size and latency but not for correctness"
        )
    answered = sum(1 for o in outcomes if o.status in (OK, TOOL_ERROR))
    if answered and answered < 30 * max(1, len(clients)):
        warnings.append(
            "fewer than 30 answered calls per server - latency percentiles are indicative "
            "at best; raise --repeats before quoting p95"
        )
    return ToolCallsResult(servers=probes, outcomes=outcomes, repeats=repeats, warnings=warnings)


def _one_call(
    call: ToolCall,
    server: str,
    repeat: int,
    client: MCPClient | None,
    catalog: Mapping[str, Mapping[str, Any]],
    allow: frozenset[str],
    *,
    dry_run: bool,
    tape: Any,
) -> CallOutcome:
    def outcome(status: str, **kw: Any) -> CallOutcome:
        return CallOutcome(
            call_id=call.id, server=server, tool=call.tool, repeat=repeat, status=status, **kw
        )

    base = {"call_id": call.id, "server": server, "tool": call.tool, "repeat": repeat}
    if client is None:
        return outcome(SKIPPED, detail="server unreachable")
    tool = catalog.get(call.tool)
    if tool is None:
        return outcome(SKIPPED, detail="server has no such tool")
    permitted, why = call_permitted(tool, allow)
    if not permitted:
        return outcome(REFUSED, detail=why)
    missing = _missing_required(tool, call.arguments)
    if missing:
        return outcome(SKIPPED, detail=f"missing required argument(s) {missing}")
    if dry_run:
        return outcome(SKIPPED, detail=f"dry run ({why})")
    try:
        timed = client.call_tool(call.tool, call.arguments)
    except MCPError as e:
        if tape is not None:
            tape.write(
                json.dumps({**base, "arguments": dict(call.arguments), "error": str(e)}) + "\n"
            )
        return outcome(TRANSPORT_ERROR, detail=str(e))
    result = timed.result
    text = response_text(result)
    tokens = estimate_tokens(text) if text else 0
    structured = result.get("structuredContent")
    passed, failed = check_expectations(call.expect, result, text, tokens)
    if tape is not None:
        tape.write(
            json.dumps(
                {
                    **base,
                    "arguments": dict(call.arguments),
                    "latency_s": timed.latency_s,
                    "result": result,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return outcome(
        TOOL_ERROR if result.get("isError") else OK,
        latency_ms=timed.latency_s * 1000.0,
        response_chars=len(text),
        response_tokens=tokens,
        structured_tokens=(
            estimate_tokens(json.dumps(structured, ensure_ascii=False))
            if structured is not None
            else 0
        ),
        passed=passed,
        failed=failed,
    )
