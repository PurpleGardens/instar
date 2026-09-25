# SPDX-License-Identifier: Apache-2.0
"""A model using MCP servers: the agent loop, measured.

Phase 1 measured the server on its own. This measures what a company actually
pays for: a model given a server's tools, answering a real task. Per task it
records every model turn (tokens, latency, why it stopped) and every tool call
(which tool, with what, whether it failed, how many tokens the result added),
and returns the final answer as an ordinary :class:`CompletionResult`.

That last part is the design. :class:`MCPAgentBackend` wraps any backend that
implements :meth:`~instar.providers.base.Backend.chat` and is itself just a
:class:`~instar.providers.base.Backend`, so everything Instar already does with
completions works unchanged on agent runs: ``instar arms`` compares models (or
server sets) on the same tasks, any judge scores the final answer, ``rejudge``
and human grading work on the saved transcript, and the corpus stores it.

**Holding tool output fixed.** Live servers return different data over time, so
two models compared live aren't answering the same question. A
:class:`ToolCassette` (the file ``instar mcp run --record`` writes, or
``--record-tools`` here) replays a recorded result whenever a call matches one
exactly (server, tool, arguments), so every arm reads the same tool output.
With ``cassette_only`` a miss is returned to the model as an error rather than
called live.

**Safety** is the same gate as phase 1: a tool the server doesn't mark
read-only is offered to the model (its definition is part of the real cost) but
never executed unless allow-listed; the model gets an error result saying so.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from instar.core.traffic import TrafficSample
from instar.mcp.client import MCPClient, MCPError, ServerSpec
from instar.mcp.toolcalls import call_permitted, response_text
from instar.providers.base import (
    Backend,
    ChatRequest,
    ChatTurn,
    CompletionResult,
    ToolSpec,
    estimate_tokens,
)

DEFAULT_MAX_TURNS = 8

# Tool-call statuses, as in phase 1, plus the two only an agent run can hit.
LIVE = "live"
CASSETTE = "cassette"
CASSETTE_MISS = "cassette_miss"
UNKNOWN_TOOL = "unknown_tool"


def _key(server: str, tool: str, arguments: Mapping[str, Any]) -> str:
    return json.dumps([server, tool, arguments], sort_keys=True, ensure_ascii=False)


class ToolCassette:
    """Recorded tool results, looked up by exact (server, tool, arguments)."""

    def __init__(self) -> None:
        self._results: dict[str, dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self._results)

    @classmethod
    def load(cls, path: str | Path) -> ToolCassette:
        """Read a JSONL cassette (``instar mcp run --record`` format).

        Rows without a ``result`` (a transport error was recorded) are skipped:
        there is nothing to replay. The first recording of a call wins.
        """
        c = cls()
        p = Path(path)
        if not p.exists():
            return c
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:{n}: not valid JSON ({e})") from e
            if isinstance(row.get("result"), dict):
                k = _key(str(row["server"]), str(row["tool"]), row.get("arguments") or {})
                c._results.setdefault(k, row["result"])
        return c

    def get(self, server: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._results.get(_key(server, tool, arguments))

    def put(
        self, server: str, tool: str, arguments: Mapping[str, Any], result: dict[str, Any]
    ) -> None:
        self._results.setdefault(_key(server, tool, arguments), result)


_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")


def _safe_name(name: str) -> str:
    """A tool name every provider accepts: [a-zA-Z0-9_-], at most 64 chars."""
    return _NAME_OK.sub("_", name)[:64] or "tool"


@dataclass
class ToolExec:
    """One tool call made on the model's behalf."""

    name: str
    server: str | None
    tool: str | None
    arguments: dict[str, Any]
    status: str
    source: str | None
    is_error: bool
    latency_s: float
    result_tokens: int
    detail: str | None = None


class MCPToolbox:
    """The tools from a set of MCP servers, as offered to a model.

    Connects once and is shared by every arm, so arms see the same catalogue
    and, with a cassette, the same tool output. With one server tool names are
    left as they are; with several they become ``<server>__<tool>`` so two
    servers' tools can't collide.
    """

    def __init__(
        self,
        servers: Sequence[ServerSpec],
        *,
        cassette: ToolCassette | None = None,
        cassette_only: bool = False,
        record: str | Path | None = None,
    ) -> None:
        if not servers:
            raise ValueError("an MCP toolbox needs at least one server")
        self.servers = list(servers)
        self.cassette = cassette
        self.cassette_only = cassette_only
        self.record = Path(record) if record else None
        self._clients: dict[str, MCPClient] = {}
        self._routes: dict[str, tuple[ServerSpec, Mapping[str, Any]]] = {}
        self.specs: list[ToolSpec] = []
        self.unreachable: dict[str, str] = {}

    def open(self) -> None:
        prefix = len(self.servers) > 1
        for spec in self.servers:
            client = MCPClient(spec)
            try:
                client.open()
                tools = client.list_tools().result["tools"]
            except MCPError as e:
                client.close()
                self.unreachable[spec.name] = str(e)
                continue
            self._clients[spec.name] = client
            for t in tools:
                if not isinstance(t, Mapping):
                    continue
                raw = str(t.get("name"))
                name = _safe_name(f"{spec.name}__{raw}" if prefix else raw)
                while name in self._routes:
                    name = _safe_name(name[:60] + "_x")
                self._routes[name] = (spec, t)
                schema = t.get("inputSchema") or {"type": "object", "properties": {}}
                self.specs.append(
                    ToolSpec(
                        name=name,
                        description=str(t.get("description") or ""),
                        input_schema=dict(schema),
                    )
                )
        if not self._clients:
            raise MCPError(
                "no MCP server reachable: "
                + "; ".join(f"{k}: {v}" for k, v in self.unreachable.items())
            )

    def close(self) -> None:
        for c in self._clients.values():
            c.close()
        self._clients.clear()

    def __enter__(self) -> MCPToolbox:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, ToolExec]:
        """Run one tool call; return (text for the model, what happened)."""
        route = self._routes.get(name)
        if route is None:
            return f"Error: no tool named {name!r}.", ToolExec(
                name, None, None, arguments, UNKNOWN_TOOL, None, True, 0.0, 0
            )
        spec, tool = route
        tool_name = str(tool.get("name"))

        def done(
            text: str,
            status: str,
            source: str | None,
            is_error: bool,
            latency: float,
            detail: str | None = None,
        ) -> tuple[str, ToolExec]:
            return text, ToolExec(
                name, spec.name, tool_name, arguments, status, source, is_error, latency,
                estimate_tokens(text) if text else 0, detail,
            )  # fmt: skip

        permitted, why = call_permitted(tool, spec.allow)
        if not permitted:
            return done(f"Error: this tool was not run by the measurement harness ({why}).",
                        "refused", None, True, 0.0, why)  # fmt: skip

        recorded = self.cassette.get(spec.name, tool_name, arguments) if self.cassette else None
        if recorded is not None:
            text = response_text(recorded)
            is_err = bool(recorded.get("isError"))
            return done(text, "tool_error" if is_err else "ok", CASSETTE, is_err, 0.0)
        if self.cassette_only:
            return done("Error: no recorded result for this call.", CASSETTE_MISS, None, True, 0.0)

        client = self._clients.get(spec.name)
        if client is None:
            return done(f"Error: server {spec.name} is unreachable.", "skipped", None, True, 0.0)
        t0 = time.perf_counter()
        try:
            timed = client.call_tool(tool_name, arguments)
        except MCPError as e:
            return done(
                f"Error: {e}", "transport_error", LIVE, True, time.perf_counter() - t0, str(e)
            )
        result = timed.result
        text = response_text(result)
        is_err = bool(result.get("isError"))
        if self.cassette is not None:
            self.cassette.put(spec.name, tool_name, arguments, result)
        if self.record is not None:
            with self.record.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"server": spec.name, "tool": tool_name, "arguments": arguments,
                                    "latency_s": timed.latency_s, "result": result},
                                   ensure_ascii=False) + "\n")  # fmt: skip
        return done(text, "tool_error" if is_err else "ok", LIVE, is_err, timed.latency_s)


def _assistant(turn: ChatTurn) -> dict[str, Any]:
    return {"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls, "raw": turn.raw}


class MCPAgentBackend(Backend):
    """Any tool-capable backend, given MCP tools and run to an answer.

    ``complete()`` runs the loop: ask the model; if it called tools, run them
    and send the results back; stop when it answers without calling a tool,
    when a turn fails, or at ``max_turns``. Tokens and cost are summed over
    every turn (each turn re-sends the conversation, and is billed for it);
    latency is wall-clock for the whole task, model and tools together.
    """

    def __init__(
        self, inner: Backend, toolbox: MCPToolbox, *, max_turns: int = DEFAULT_MAX_TURNS
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self.inner = inner
        self.toolbox = toolbox
        self.max_turns = max_turns
        self.name = inner.name

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        conversation: list[dict[str, Any]] = [dict(m) for m in sample.messages]
        turns: list[dict[str, Any]] = []
        execs: list[ToolExec] = []
        t0 = time.perf_counter()
        in_tok = out_tok = 0
        costs: list[float | None] = []
        served = model
        text = ""
        stop = "max_turns"
        for _ in range(self.max_turns):
            turn = self.inner.chat(
                ChatRequest(
                    model=model,
                    messages=conversation,
                    tools=self.toolbox.specs,
                    max_tokens=sample.max_tokens,
                    system=sample.system,
                    temperature=sample.temperature,
                    meta=dict(sample.meta),
                )
            )
            record: dict[str, Any] = {
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "latency_s": turn.latency_s,
                "stop_reason": turn.stop_reason,
                "tool_calls": [],
            }
            turns.append(record)
            if not turn.ok:
                stop = "error"
                return self._result(sample, model, served, "", in_tok, out_tok, costs, t0, turns,
                                    execs, stop, error=turn.error)  # fmt: skip
            in_tok += turn.input_tokens
            out_tok += turn.output_tokens
            costs.append(turn.cost_usd)
            served = turn.model or served
            text = turn.text
            conversation.append(_assistant(turn))
            if not turn.tool_calls:
                stop = turn.stop_reason
                break
            results = []
            for call in turn.tool_calls:
                content, ex = self.toolbox.execute(call.name, call.arguments)
                execs.append(ex)
                record["tool_calls"].append(asdict(ex))
                results.append({"id": call.id, "content": content, "is_error": ex.is_error})
            conversation.append({"role": "tool_results", "results": results})
        return self._result(
            sample, model, served, text, in_tok, out_tok, costs, t0, turns, execs, stop
        )

    def _result(
        self,
        sample: TrafficSample,
        model: str,
        served: str,
        text: str,
        in_tok: int,
        out_tok: int,
        costs: list[float | None],
        t0: float,
        turns: list[dict[str, Any]],
        execs: list[ToolExec],
        stop: str,
        *,
        error: str | None = None,
    ) -> CompletionResult:
        trajectory = {
            "stop": stop,
            "turns": turns,
            "n_turns": len(turns),
            "tool_calls": len(execs),
            "tool_errors": sum(1 for e in execs if e.is_error and e.status != "refused"),
            "refused": sum(1 for e in execs if e.status == "refused"),
            "cassette_hits": sum(1 for e in execs if e.source == CASSETTE),
            "tool_result_tokens": sum(e.result_tokens for e in execs),
            "tool_latency_s": sum(e.latency_s for e in execs),
            "model_latency_s": sum(float(t["latency_s"]) for t in turns),
        }
        reported = costs and all(c is not None for c in costs)
        return CompletionResult(
            text=text,
            model=served,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_s=time.perf_counter() - t0,
            ok=error is None,
            error=error,
            cost_usd=sum(c for c in costs if c is not None) if reported else None,
            trajectory=trajectory,
        )
