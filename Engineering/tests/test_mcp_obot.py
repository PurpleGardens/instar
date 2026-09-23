# SPDX-License-Identifier: Apache-2.0
"""Obot audit-log export to Instar tool-call fixture.

The shipped export is synthetic but follows Obot's normalised ``AuditLogEvent``
shape field for field (``apiclient/types/auditlogevent.go``): gateway calls with
the JSON-RPC request in ``details.request.body``, a webhook-rewritten request,
an event-stream response stored as a string, a redacted payload, a JSON-RPC
error, and two Obot Sentry events (one MCP tool, one local shell tool).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from instar.cli.main import main
from instar.mcp.client import load_servers
from instar.mcp.obot import convert, convert_event, read_export
from instar.mcp.toolcalls import OK, TOOL_ERROR, load_calls, run_toolcalls

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mcp"
EXPORT = FIXTURES / "obot-export-example.jsonl"


def _events() -> list[dict[str, object]]:
    return read_export(EXPORT)


class TestConvert:
    def test_keeps_tool_calls_and_counts_the_rest(self) -> None:
        r = convert(_events())
        assert r.read == 10
        assert [c["tool"] for c in r.calls] == [
            "lookup_order",  # A-100 (a second identical call merged)
            "lookup_order",  # Z-999
            "search_docs",  # webhook-rewritten
            "lookup_order",  # A-101, JSON-RPC error
            "lookup_order",  # Sentry, server "orders"
        ]
        assert r.merged == 1
        assert r.skipped == {
            "not a tool call (initialize)": 1,
            "not a tool call (tools/list)": 1,
            "payload redacted or absent": 1,
            "local tool, not an MCP tool": 1,
        }

    def test_duplicates_are_counted(self) -> None:
        first = convert(_events()).calls[0]
        assert first["meta"]["seen"] == 2
        assert len(convert(_events(), dedupe=False).calls) == 6

    def test_mutated_body_is_what_the_server_received(self) -> None:
        docs = next(c for c in convert(_events()).calls if c["tool"] == "search_docs")
        assert docs["arguments"] == {"query": "returns"}  # the webhook dropped a field

    def test_observed_outcomes(self) -> None:
        by_id = {c["id"]: c for c in convert(_events(), expect_observed=True).calls}
        assert by_id["obot-3"]["expect"] == {"is_error": False}
        assert by_id["obot-5"]["expect"] == {"is_error": True}
        assert by_id["obot-6"]["expect"] == {"is_error": False}  # read from the event stream
        assert "expect" not in by_id["obot-8"]  # JSON-RPC error: the call failed, not the tool
        assert by_id["obot-9"]["expect"] == {"is_error": False}  # Sentry: outcome.status

    def test_no_expectations_unless_asked(self) -> None:
        assert all("expect" not in c for c in convert(_events()).calls)

    def test_server_map(self) -> None:
        calls = convert(_events(), server_map={"Demo Orders": "demo"}).calls
        mapped = [c.get("server") for c in calls]
        assert mapped == ["demo", "demo", "demo", "demo", None]
        assert calls[-1]["meta"]["obot_server"] == "orders"

    def test_tool_filter(self) -> None:
        r = convert(_events(), tools={"search_docs"})
        assert [c["tool"] for c in r.calls] == ["search_docs"]
        assert r.skipped["tool not selected"] == 5

    def test_meta_carries_provenance(self) -> None:
        c = convert(_events()).calls[0]
        assert c["id"] == "obot-3"
        assert c["meta"]["observed_duration_ms"] == 12
        assert c["meta"]["client"] == "claude-ai"
        assert c["meta"]["source"] == "obot"

    def test_unknown_event_type(self) -> None:
        call, reason = convert_event({"eventType": "llm_call"}, {})
        assert call is None and reason == "event type llm_call"

    def test_reads_a_json_list_response(self, tmp_path: Path) -> None:
        p = tmp_path / "list.json"
        p.write_text(json.dumps({"items": _events(), "total": 10}))
        assert len(read_export(p)) == 10


class TestRoundTrip:
    def test_converted_calls_replay_against_the_demo_server(self, tmp_path: Path) -> None:
        out = tmp_path / "calls.jsonl"
        rc = main(
            [
                "mcp",
                "from-obot",
                str(EXPORT),
                "-o",
                str(out),
                "--server-map",
                "Demo Orders=demo",
                "--expect-observed",
            ]
        )
        assert rc == 0
        calls = [c for c in load_calls(out) if c.server == "demo"]
        result = run_toolcalls(calls, load_servers(str(FIXTURES / "demo-servers.json")))
        by = {o.call_id: o for o in result.outcomes}
        assert by["obot-3"].status == OK and not by["obot-3"].failed
        assert by["obot-5"].status == TOOL_ERROR and not by["obot-5"].failed
        assert by["obot-6"].status == OK


class TestCli:
    def test_refuses_to_overwrite(self, tmp_path: Path) -> None:
        out = tmp_path / "calls.jsonl"
        out.write_text("keep")
        with pytest.raises(SystemExit, match="--force"):
            main(["mcp", "from-obot", str(EXPORT), "-o", str(out)])

    def test_bad_server_map(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="OBOT_NAME=INSTAR_NAME"):
            main(["mcp", "from-obot", str(EXPORT), "-o", str(tmp_path / "x"), "--server-map", "x"])

    def test_nothing_replayable(self, tmp_path: Path) -> None:
        p = tmp_path / "e.jsonl"
        p.write_text(
            json.dumps({"id": 1, "eventType": "mcp_call", "action": {"operation": "ping"}})
        )
        with pytest.raises(SystemExit, match="no replayable"):
            main(["mcp", "from-obot", str(p), "-o", str(tmp_path / "out.jsonl")])

    def test_summary(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        main(["mcp", "from-obot", str(EXPORT), "-o", str(tmp_path / "c.jsonl")])
        out = capsys.readouterr().out
        assert "10 events read, 5 calls written, 1 duplicates merged" in out
        assert "unmapped Obot server(s) ['Demo Orders', 'orders']" in out
