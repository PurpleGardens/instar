# SPDX-License-Identifier: Apache-2.0
"""MCP phase 2: a model using MCP tools, measured as an agent loop.

The providers' tool-use turns are tested against faked transports (an
SDK-shaped fake for Anthropic, a patched ``urlopen`` for chat completions),
checking the wire format each dialect expects. The loop itself runs the mock
model against the real demo MCP server.

Properties that matter: tokens and cost are summed across turns; a tool that
isn't read-only is offered but never run; a cassette holds tool output fixed
across arms; a turn that fails fails the task rather than inventing an answer;
and the loop stops at max_turns.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from instar.cli.main import main
from instar.core.arms import Arm, run_arms
from instar.core.traffic import TrafficSample
from instar.core.transcript import Transcript
from instar.mcp.agent import (
    CASSETTE,
    CASSETTE_MISS,
    MCPAgentBackend,
    MCPToolbox,
    ToolCassette,
    _safe_name,
)
from instar.mcp.client import ServerSpec
from instar.providers.anthropic import AnthropicBackend
from instar.providers.base import (
    Backend,
    ChatRequest,
    ChatTurn,
    CompletionResult,
    ToolCallRequest,
    ToolSpec,
)
from instar.providers.mock import MockBackend
from instar.providers.openai_compat import OpenAICompatBackend
from instar.rubrics.criteria import CriteriaSet, _tools_used

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mcp"
DEMO = (sys.executable, "-m", "instar.mcp.demo_server")


def _spec(name: str = "demo", **kw: Any) -> ServerSpec:
    return ServerSpec(name=name, command=DEMO, timeout_s=10, **kw)


@pytest.fixture
def toolbox() -> Iterator[MCPToolbox]:
    with MCPToolbox([_spec()]) as tb:
        yield tb


def _task(tool: str | None = "lookup_order", args: dict[str, Any] | None = None,
          sid: str = "t1") -> TrafficSample:  # fmt: skip
    meta: dict[str, Any] = {}
    if tool:
        meta["mock_tool_calls"] = [{"tool": tool, "arguments": args or {"order_id": "A-100"}}]
    return TrafficSample(
        id=sid, feature="support.order_status", system="Be brief.",
        messages=[{"role": "user", "content": "Where is A-100?"}], meta=meta,
    )  # fmt: skip


REQ = ChatRequest(
    model="m",
    messages=[
        {"role": "user", "content": "Where is A-100?"},
        {
            "role": "assistant",
            "text": "Checking.",
            "tool_calls": [ToolCallRequest("tu1", "lookup_order", {"order_id": "A-100"})],
            "raw": None,
        },
        {
            "role": "tool_results",
            "results": [{"id": "tu1", "content": "shipped", "is_error": False}],
        },
    ],
    tools=[ToolSpec("lookup_order", "Look up an order", {"type": "object"})],
    max_tokens=100,
    system="Be brief.",
)


class TestAnthropicChat:
    def _client(self, response: Any) -> tuple[Any, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []

        def create(**kw: Any) -> Any:
            calls.append(kw)
            return response

        return SimpleNamespace(messages=SimpleNamespace(create=create)), calls

    def test_wire_format_and_parsing(self) -> None:
        blocks = [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Let me check."),
            SimpleNamespace(
                type="tool_use", id="tu2", name="lookup_order", input={"order_id": "B"}
            ),
        ]
        resp = SimpleNamespace(
            content=blocks, model="claude-x", stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=50, output_tokens=7),
        )  # fmt: skip
        client, calls = self._client(resp)
        turn = AnthropicBackend(client=client).chat(REQ)
        sent = calls[0]
        tool = {"name": "lookup_order", "description": "Look up an order"}
        assert sent["tools"] == [{**tool, "input_schema": {"type": "object"}}]
        assert sent["system"] == "Be brief."
        assert sent["messages"][1]["content"][1] == {
            "type": "tool_use", "id": "tu1", "name": "lookup_order", "input": {"order_id": "A-100"},
        }  # fmt: skip
        assert sent["messages"][2] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "shipped",
                         "is_error": False}],
        }  # fmt: skip
        assert turn.text == "Let me check."
        assert turn.tool_calls == [ToolCallRequest("tu2", "lookup_order", {"order_id": "B"})]
        assert (turn.input_tokens, turn.output_tokens, turn.stop_reason) == (50, 7, "tool_use")
        assert turn.raw == blocks  # sent back unchanged next turn, thinking included

    def test_raw_blocks_round_trip(self) -> None:
        raw = [SimpleNamespace(type="thinking", thinking="")]
        req = ChatRequest(
            model="m",
            messages=[{"role": "assistant", "text": "", "tool_calls": [], "raw": raw}],
            tools=[], max_tokens=10,
        )  # fmt: skip
        resp = SimpleNamespace(content=[], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))  # fmt: skip
        client, calls = self._client(resp)
        AnthropicBackend(client=client).chat(req)
        assert calls[0]["messages"][0]["content"] is raw
        assert "tools" not in calls[0]

    def test_failure_does_not_raise(self) -> None:
        def boom(**kw: Any) -> Any:
            raise RuntimeError("overloaded")

        client = SimpleNamespace(messages=SimpleNamespace(create=boom))
        turn = AnthropicBackend(client=client).chat(REQ)
        assert not turn.ok and "overloaded" in (turn.error or "")


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *a: object) -> None:
        pass


class TestOpenAIChat:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[Any]:
        sent: list[Any] = []

        def fake(req: Any, timeout: float | None = None) -> _FakeResponse:
            sent.append(json.loads(req.data))
            return _FakeResponse(json.dumps(payload).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return sent

    def test_wire_format_and_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = self._patch(monkeypatch, {
            "model": "served",
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "c9", "type": "function",
                 "function": {"name": "lookup_order", "arguments": "{\"order_id\": \"B\"}"}},
                {"id": "c10", "type": "function",
                 "function": {"name": "lookup_order", "arguments": "not json"}},
            ]}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 9, "cost": 0.001},
        })  # fmt: skip
        turn = OpenAICompatBackend("http://x").chat(REQ)
        body = sent[0]
        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
        assert body["messages"][0] == {"role": "system", "content": "Be brief."}
        assert (
            body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"order_id": "A-100"}'
        )
        assert body["messages"][3] == {"role": "tool", "tool_call_id": "tu1", "content": "shipped"}
        assert turn.tool_calls[0] == ToolCallRequest("c9", "lookup_order", {"order_id": "B"})
        assert turn.tool_calls[1].arguments == {"_unparsed_arguments": "not json"}
        assert (turn.model, turn.cost_usd, turn.stop_reason) == ("served", 0.001, "tool_calls")


class TestToolbox:
    def test_catalogue_and_prefixing(self) -> None:
        with MCPToolbox([_spec("a"), _spec("b")]) as tb:
            names = {t.name for t in tb.specs}
        assert {"a__lookup_order", "b__lookup_order"} <= names

    def test_single_server_names_are_plain(self, toolbox: MCPToolbox) -> None:
        assert {t.name for t in toolbox.specs} == {"lookup_order", "search_docs", "refund_order"}

    def test_refused_tool_is_never_run(self, toolbox: MCPToolbox) -> None:
        text, ex = toolbox.execute("refund_order", {"order_id": "A-1"})
        assert ex.status == "refused" and ex.is_error and "not run" in text

    def test_unknown_tool(self, toolbox: MCPToolbox) -> None:
        _, ex = toolbox.execute("nope", {})
        assert ex.status == "unknown_tool"

    def test_live_call_and_record(self, tmp_path: Path) -> None:
        tape = tmp_path / "tape.jsonl"
        with MCPToolbox([_spec()], record=tape) as tb:
            text, ex = tb.execute("lookup_order", {"order_id": "A-100"})
        assert "shipped" in text and ex.source == "live" and ex.status == "ok"
        assert json.loads(tape.read_text())["result"]["structuredContent"]["status"] == "shipped"

    def test_cassette_replay_and_miss(self, tmp_path: Path) -> None:
        tape = tmp_path / "tape.jsonl"
        tape.write_text(json.dumps({"server": "demo", "tool": "lookup_order",
                                    "arguments": {"order_id": "A-100"},
                                    "result": {"content": [{"type": "text", "text": "FROZEN"}]}})
                        + "\n")  # fmt: skip
        with MCPToolbox([_spec()], cassette=ToolCassette.load(tape), cassette_only=True) as tb:
            text, ex = tb.execute("lookup_order", {"order_id": "A-100"})
            assert text == "FROZEN" and ex.source == CASSETTE
            _, miss = tb.execute("lookup_order", {"order_id": "A-101"})
            assert miss.status == CASSETTE_MISS and miss.is_error

    def test_no_server_reachable(self) -> None:
        from instar.mcp.client import MCPError

        with pytest.raises(MCPError, match="no MCP server reachable"):
            MCPToolbox([ServerSpec(name="x", url="http://127.0.0.1:9/mcp", timeout_s=2)]).open()

    def test_safe_names(self) -> None:
        assert _safe_name("my server.tool/x") == "my_server_tool_x"
        assert len(_safe_name("x" * 100)) == 64


class TestLoop:
    def test_one_tool_then_answer(self, toolbox: MCPToolbox) -> None:
        r = MCPAgentBackend(MockBackend("m", latency_s=0.0), toolbox).complete(_task(), "mock")
        traj = r.trajectory
        assert r.ok and traj is not None
        assert traj["n_turns"] == 2 and traj["tool_calls"] == 1 and traj["stop"] == "end_turn"
        assert "shipped" in r.text
        assert r.input_tokens == sum(t["input_tokens"] for t in traj["turns"])

    def test_no_tools_needed(self, toolbox: MCPToolbox) -> None:
        r = MCPAgentBackend(MockBackend("m"), toolbox).complete(_task(tool=None), "mock")
        assert r.trajectory is not None and r.trajectory["tool_calls"] == 0

    def test_max_turns(self, toolbox: MCPToolbox) -> None:
        class Loops(Backend):
            name = "loops"

            def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
                raise AssertionError

            def chat(self, request: ChatRequest) -> ChatTurn:
                call = ToolCallRequest("x", "lookup_order", {"order_id": "A-100"})
                return ChatTurn("", [call], "m", 10, 1, 0.0, "tool_use", cost_usd=0.5)

        r = MCPAgentBackend(Loops(), toolbox, max_turns=3).complete(_task(), "m")
        assert r.trajectory is not None and r.trajectory["stop"] == "max_turns"
        assert r.trajectory["n_turns"] == 3 and r.input_tokens == 30
        assert r.cost_usd == 1.5  # reported every turn, so summed

    def test_failed_turn_fails_the_task(self, toolbox: MCPToolbox) -> None:
        class Down(Backend):
            def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
                raise AssertionError

            def chat(self, request: ChatRequest) -> ChatTurn:
                return ChatTurn.failure("m", "503")

        r = MCPAgentBackend(Down(), toolbox).complete(_task(), "m")
        assert not r.ok and r.error == "503" and r.trajectory is not None

    def test_backend_without_chat(self, toolbox: MCPToolbox) -> None:
        class Plain(Backend):
            def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
                raise AssertionError

        with pytest.raises(NotImplementedError, match="does not support tool use"):
            MCPAgentBackend(Plain(), toolbox).complete(_task(), "m")

    def test_arms_aggregate_and_transcript(self, toolbox: MCPToolbox, tmp_path: Path) -> None:
        arms = [
            Arm("a", MCPAgentBackend(MockBackend("a"), toolbox), "strong"),
            Arm("b", MCPAgentBackend(MockBackend("b"), toolbox), "weak"),
        ]
        samples = [_task(sid="t1"), _task("refund_order", {"order_id": "A-1"}, sid="t2")]
        result = run_arms(samples, arms=arms, capture=True)
        use = result.by_name("b").tool_use
        assert use is not None and use["tool_calls"] == 1.0 and use["refused"] == 0.5
        assert result.transcript is not None
        saved = Transcript.load(result.transcript.save(tmp_path / "t.json"))
        assert saved.entries[1].completions["b"].trajectory == (
            result.transcript.entries[1].completions["b"].trajectory
        )


class TestJudgeSeesTools:
    def test_tools_used_summary(self) -> None:
        traj = {"turns": [{"tool_calls": [{"tool": "lookup_order", "arguments": {"order_id": "A"},
                                           "status": "ok"}]}]}  # fmt: skip
        r = CompletionResult("x", "m", 1, 1, 0.0, trajectory=traj)
        assert _tools_used(r) == 'TOOL CALLS MADE:\n- lookup_order({"order_id": "A"}) -> ok\n\n'
        assert _tools_used(CompletionResult("x", "m", 1, 1, 0.0)) == ""
        assert _tools_used(CompletionResult("x", "m", 1, 1, 0.0, trajectory={"turns": []})) == (
            "TOOL CALLS MADE: none\n\n"
        )

    def test_demo_criteria_load(self) -> None:
        assert CriteriaSet.load(FIXTURES / "demo-agent-criteria.json").version == "demo-agent-v1"


class TestCli:
    def test_agent_arms_record_then_replay(self, tmp_path: Path) -> None:
        tasks = str(FIXTURES / "demo-agent-tasks.jsonl")
        servers = str(FIXTURES / "demo-servers.json")
        criteria = str(FIXTURES / "demo-agent-criteria.json")
        tape = tmp_path / "tape.jsonl"
        runs = str(tmp_path / "runs")
        rc = main(["arms", "--traffic", tasks, "--mcp-servers", servers, "--criteria", criteria,
                   "--record-tools", str(tape), "--runs-dir", runs, "--label", "live"])  # fmt: skip
        assert rc == 0
        report = (tmp_path / "runs" / "live" / "report.md").read_text()
        assert "## Tool use (MCP)" in report
        rc = main(["arms", "--traffic", tasks, "--mcp-servers", servers, "--tool-cassette",
                   str(tape), "--cassette-only", "--save-transcript", str(tmp_path / "t.json"),
                   "--runs-dir", runs, "--label", "replay"])  # fmt: skip
        assert rc == 0
        t = Transcript.load(tmp_path / "t.json")
        hits = [c.trajectory["cassette_hits"] for e in t.entries for c in e.completions.values()
                if c.trajectory]  # fmt: skip
        assert sum(hits) == 9  # three live-callable tasks x three arms; the refund is refused

    def test_cassette_only_needs_a_cassette(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="needs --tool-cassette"):
            main(["arms", "--traffic", str(FIXTURES / "demo-agent-tasks.jsonl"), "--mcp-servers",
                  str(FIXTURES / "demo-servers.json"), "--cassette-only",
                  "--runs-dir", str(tmp_path)])  # fmt: skip
