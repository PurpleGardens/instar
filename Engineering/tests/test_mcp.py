# SPDX-License-Identifier: Apache-2.0
"""MCP measurement: the client over both transports, probing, and tool-call replay.

Everything runs against the shipped demo server (stdio as a subprocess, HTTP on
a local thread), so the suite needs no network and no external server.

Properties that matter: a tool that isn't read-only is never called unless
allowed by name; a server that won't start says why; calls without a server run
against every server, interleaved; a tool reporting an error is distinguished
from a call that never completed; and response size is what lands in context.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from instar.cli.main import main
from instar.mcp.client import MCPClient, MCPError, ServerSpec, _sse_messages, load_servers
from instar.mcp.demo_server import TOOLS, make_http_server
from instar.mcp.probe import ToolDef, probe
from instar.mcp.toolcalls import (
    OK,
    REFUSED,
    SKIPPED,
    TOOL_ERROR,
    TRANSPORT_ERROR,
    ToolCall,
    call_permitted,
    check_expectations,
    load_calls,
    response_text,
    run_toolcalls,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mcp"
DEMO_CMD = (sys.executable, "-m", "instar.mcp.demo_server")


def _stdio(name: str = "demo", **kw: object) -> ServerSpec:
    return ServerSpec(name=name, command=DEMO_CMD, timeout_s=10, **kw)  # type: ignore[arg-type]


@pytest.fixture(params=[False, True], ids=["json", "sse"])
def http_spec(request: pytest.FixtureRequest) -> Iterator[ServerSpec]:
    server = make_http_server(0, sse=request.param)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield ServerSpec(name="http", url=f"http://127.0.0.1:{server.server_address[1]}/mcp")
    finally:
        server.shutdown()
        server.server_close()


def _calls(*rows: dict[str, object]) -> list[ToolCall]:
    return [ToolCall.from_json(r) for r in rows]


class TestSpec:
    def test_exactly_one_transport(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ServerSpec(name="x")
        with pytest.raises(ValueError, match="exactly one"):
            ServerSpec(name="x", command=("a",), url="http://x")

    def test_command_must_be_a_list(self) -> None:
        with pytest.raises(ValueError, match="list of arguments"):
            ServerSpec.from_json("x", {"command": "node server.js"})

    def test_python_placeholder_and_demo_config(self) -> None:
        (spec,) = load_servers(str(FIXTURES / "demo-servers.json"))
        assert spec.command[0] == sys.executable

    def test_unset_header_env_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INSTAR_TEST_TOKEN", raising=False)
        spec = ServerSpec(
            name="x",
            url="http://127.0.0.1:9/mcp",
            headers_env={"Authorization": "INSTAR_TEST_TOKEN"},
        )
        with pytest.raises(MCPError, match="INSTAR_TEST_TOKEN"):
            MCPClient(spec).open()


class TestClient:
    def test_stdio_session(self) -> None:
        with MCPClient(_stdio()) as c:
            assert c.server_info["name"] == "instar-demo"
            names = [t["name"] for t in c.list_tools().result["tools"]]
            assert names == [t["name"] for t in TOOLS]
            r = c.call_tool("lookup_order", {"order_id": "A-100"})
            assert r.result["structuredContent"]["status"] == "shipped"
            assert r.latency_s >= 0

    def test_http_session(self, http_spec: ServerSpec) -> None:
        with MCPClient(http_spec) as c:
            assert c.protocol_version
            r = c.call_tool("lookup_order", {"order_id": "A-101"})
            assert r.result["structuredContent"]["status"] == "processing"

    def test_jsonrpc_error_raises(self) -> None:
        with MCPClient(_stdio()) as c, pytest.raises(MCPError, match="unknown tool"):
            c.call_tool("nope", {})

    def test_server_that_exits_says_why(self) -> None:
        spec = ServerSpec(
            name="dies",
            command=(sys.executable, "-c", "import sys; sys.stderr.write('bad config\\n')"),
            timeout_s=5,
        )
        with pytest.raises(MCPError, match="bad config"):
            MCPClient(spec).open()

    def test_missing_executable(self) -> None:
        with pytest.raises(MCPError, match="could not start"):
            MCPClient(ServerSpec(name="x", command=("/no/such/binary",))).open()

    def test_sse_parsing(self) -> None:
        body = 'event: message\ndata: {"id": 1}\n\n: comment\n\ndata: {"id":\ndata: 2}\n\n'
        assert _sse_messages(body) == [{"id": 1}, {"id": 2}]


class TestProbe:
    def test_sizes_and_notes(self) -> None:
        p = probe(_stdio())
        assert p.ok and len(p.tools) == 3
        by = {t.name: t for t in p.tools}
        assert by["search_docs"].total_tokens > by["lookup_order"].total_tokens
        assert by["refund_order"].destructive and not by["refund_order"].read_only
        assert any("without a description: order_id" in n for n in by["refund_order"].notes)
        assert p.total_tokens == sum(t.total_tokens for t in p.tools)

    def test_unannotated_tool_is_flagged(self) -> None:
        t = ToolDef.from_tool({"name": "x", "inputSchema": {"type": "object"}})
        assert "no description" in t.notes
        assert any("not annotated" in n for n in t.notes)
        assert not t.read_only

    def test_pricing(self) -> None:
        p = probe(_stdio())
        assert p.cost_per_1k_turns_usd("no-such-model") is None
        assert (p.cost_per_1k_turns_usd("claude-sonnet-4-6") or 0) > 0

    def test_unreachable_is_reported_not_raised(self) -> None:
        p = probe(ServerSpec(name="x", url="http://127.0.0.1:9/mcp", timeout_s=2))
        assert not p.ok and p.error


class TestSafety:
    def test_permission_rules(self) -> None:
        ro = {"name": "a", "annotations": {"readOnlyHint": True}}
        bare = {"name": "b"}
        destructive = {"name": "c", "annotations": {"readOnlyHint": True, "destructiveHint": True}}
        assert call_permitted(ro, frozenset())[0]
        assert not call_permitted(bare, frozenset())[0]
        assert not call_permitted(destructive, frozenset())[0]
        assert call_permitted(bare, frozenset({"b"}))[0]
        assert call_permitted(destructive, frozenset({"c"}))[0]

    def test_destructive_tool_is_refused_and_never_called(self, tmp_path: Path) -> None:
        tape = tmp_path / "tape.jsonl"
        result = run_toolcalls(
            _calls({"id": "r", "tool": "refund_order", "arguments": {"order_id": "A-1"}}),
            [_stdio()],
            record=tape,
        )
        (o,) = result.outcomes
        assert o.status == REFUSED and "destructive" in (o.detail or "")
        assert not tape.exists() or tape.read_text() == ""
        assert any("refused" in w for w in result.warnings)

    def test_allow_list_permits(self) -> None:
        result = run_toolcalls(
            _calls({"id": "r", "tool": "refund_order", "arguments": {"order_id": "A-1"}}),
            [_stdio(allow=frozenset({"refund_order"}))],
        )
        assert result.outcomes[0].status == OK


class TestExpectations:
    def test_checks(self) -> None:
        result = {
            "content": [{"type": "text", "text": "status shipped"}],
            "isError": False,
            "structuredContent": {"status": "shipped", "eta": {"days": 2}},
        }
        passed, failed = check_expectations(
            {
                "is_error": False,
                "contains": ["shipped"],
                "not_contains": ["refund"],
                "max_tokens": 1,
                "structured": {"eta": {"days": 2}},
            },
            result,
            "status shipped",
            tokens=3,
        )
        assert failed == ["<= 1 tokens"]
        assert len(passed) == 4

    def test_response_text_blocks(self) -> None:
        text = response_text(
            {
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "resource", "resource": {"text": "b"}},
                    {"type": "image", "data": "xx"},
                ]
            }
        )
        assert text.startswith("a\nb\n") and '"image"' in text

    def test_unknown_expect_key(self) -> None:
        with pytest.raises(ValueError, match="unknown expect"):
            ToolCall.from_json({"id": "x", "tool": "t", "expect": {"equals": 1}})


class TestRun:
    def test_demo_fixture(self) -> None:
        calls = load_calls(FIXTURES / "demo-calls.jsonl")
        result = run_toolcalls(calls, load_servers(str(FIXTURES / "demo-servers.json")))
        by = {o.call_id: o for o in result.outcomes}
        assert by["order-shipped"].status == OK and not by["order-shipped"].failed
        assert by["order-unknown"].status == TOOL_ERROR and not by["order-unknown"].failed
        assert by["docs-returns"].failed == ["<= 400 tokens"]  # the bloat case
        assert by["docs-one"].failed == []
        assert by["refund-attempt"].status == REFUSED
        assert by["docs-returns"].response_tokens > 500

    def test_skips_missing_tool_and_arguments(self) -> None:
        result = run_toolcalls(
            _calls({"id": "a", "tool": "nope"}, {"id": "b", "tool": "lookup_order"}),
            [_stdio()],
        )
        details = [o.detail for o in result.outcomes]
        assert all(o.status == SKIPPED for o in result.outcomes)
        assert details == ["server has no such tool", "missing required argument(s) ['order_id']"]

    def test_unnamed_calls_hit_every_server_interleaved(self, http_spec: ServerSpec) -> None:
        calls = _calls(
            {"id": "a", "tool": "lookup_order", "arguments": {"order_id": "A-100"}},
            {
                "id": "b",
                "tool": "lookup_order",
                "arguments": {"order_id": "A-101"},
                "server": "http",
            },
        )
        result = run_toolcalls(calls, [_stdio(), http_spec], repeats=2)
        order = [(o.call_id, o.server, o.repeat) for o in result.outcomes]
        assert order == [
            ("a", "demo", 0), ("a", "http", 0), ("b", "http", 0),
            ("a", "demo", 1), ("a", "http", 1), ("b", "http", 1),
        ]  # fmt: skip
        stats = {(s.server, s.tool): s for s in result.tool_stats()}
        assert stats[("http", "lookup_order")].n == 4

    def test_unknown_server_name(self) -> None:
        with pytest.raises(ValueError, match="unknown server"):
            run_toolcalls(_calls({"id": "a", "tool": "t", "server": "zzz"}), [_stdio()])

    def test_unreachable_server_skips_its_calls(self) -> None:
        dead = ServerSpec(name="dead", url="http://127.0.0.1:9/mcp", timeout_s=2)
        result = run_toolcalls(
            _calls({"id": "a", "tool": "lookup_order", "arguments": {"order_id": "A-100"}}),
            [_stdio(), dead],
        )
        by = {o.server: o for o in result.outcomes}
        assert by["demo"].status == OK
        assert by["dead"].status == SKIPPED
        assert not result.servers[1].ok

    def test_dry_run_calls_nothing(self, tmp_path: Path) -> None:
        tape = tmp_path / "t.jsonl"
        result = run_toolcalls(
            _calls({"id": "a", "tool": "lookup_order", "arguments": {"order_id": "A-100"}}),
            [_stdio()],
            dry_run=True,
            record=tape,
        )
        assert result.outcomes[0].status == SKIPPED
        assert "dry run" in (result.outcomes[0].detail or "")
        assert tape.read_text() == ""

    def test_record_writes_raw_results(self, tmp_path: Path) -> None:
        tape = tmp_path / "t.jsonl"
        run_toolcalls(
            _calls({"id": "a", "tool": "lookup_order", "arguments": {"order_id": "A-100"}}),
            [_stdio()],
            repeats=2,
            record=tape,
        )
        rows = [json.loads(line) for line in tape.read_text().splitlines()]
        assert [r["repeat"] for r in rows] == [0, 1]
        assert rows[0]["result"]["structuredContent"]["status"] == "shipped"

    def test_transport_error_is_not_a_tool_error(self) -> None:
        # A server that answers initialize and tools/list, then dies on the call.
        script = (
            "import json,sys\n"
            "tools=[{'name':'t','inputSchema':{},'annotations':{'readOnlyHint':True}}]\n"
            "for line in sys.stdin:\n"
            "    m=json.loads(line)\n"
            "    if 'id' not in m: continue\n"
            "    if m['method']=='tools/call': sys.exit(1)\n"
            "    r={'protocolVersion':'x','serverInfo':{}} if m['method']=='initialize' "
            "else {'tools':tools}\n"
            "    print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)\n"
        )
        spec = ServerSpec(name="flaky", command=(sys.executable, "-c", script), timeout_s=5)
        result = run_toolcalls(_calls({"id": "a", "tool": "t"}), [spec])
        assert result.outcomes[0].status == TRANSPORT_ERROR


class TestLoadCalls:
    def test_errors_name_the_line(self, tmp_path: Path) -> None:
        p = tmp_path / "c.jsonl"
        p.write_text('{"id": "a", "tool": "t"}\n{"id": "b"}\n')
        with pytest.raises(ValueError, match=r"c\.jsonl:2: 'tool' is required"):
            load_calls(p)

    def test_duplicate_ids(self, tmp_path: Path) -> None:
        p = tmp_path / "c.jsonl"
        p.write_text('{"id": "a", "tool": "t"}\n{"id": "a", "tool": "u"}\n')
        with pytest.raises(ValueError, match="duplicate"):
            load_calls(p)


class TestCli:
    def test_probe_and_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        servers = str(FIXTURES / "demo-servers.json")
        runs = str(tmp_path / "runs")
        assert main(["mcp", "probe", "--servers", servers, "--runs-dir", runs,
                     "--price-model", "claude-sonnet-4-6"]) == 0  # fmt: skip
        probe_json = json.loads((tmp_path / "runs" / "mcp-probe" / "result.json").read_text())
        assert probe_json["servers"][0]["total_tokens"] > 0
        rc = main(
            ["mcp", "run", "--servers", servers, "--calls", str(FIXTURES / "demo-calls.jsonl"),
             "--runs-dir", runs, "--record", str(tmp_path / "tape.jsonl")]
        )  # fmt: skip
        assert rc == 0  # refused and tool-error calls are findings, not failures
        report = (tmp_path / "runs" / "mcp-run" / "report.md").read_text()
        assert "refund_order" in report and "Tool definitions" in report
        out = capsys.readouterr().out
        assert "refused 1" in out

    def test_unknown_server_filter(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="no server"):
            main(["mcp", "probe", "--servers", str(FIXTURES / "demo-servers.json"),
                  "--server", "zzz", "--runs-dir", str(tmp_path)])  # fmt: skip
