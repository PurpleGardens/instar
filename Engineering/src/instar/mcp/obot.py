# SPDX-License-Identifier: Apache-2.0
"""Turn an Obot audit-log export into an Instar tool-call fixture.

Obot (an open-source MCP gateway) records every call that passes through it,
and exports its audit log as JSONL in a normalised event format
(``AuditLogEvent`` in Obot's ``apiclient/types``). That makes a gateway the
natural capture point for real MCP traffic: this module reads such an export
and writes the ``instar mcp run`` fixture format, so a company can replay the
tool calls its people and agents actually made.

Two event kinds carry MCP tool calls:

- ``mcp_call`` with ``action.operation == "tools/call"``: a call through the
  gateway. ``details.request.body`` is the JSON-RPC request, so the arguments
  are ``params.arguments``. When a webhook rewrote the request, the rewritten
  body (``mutatedBody``) is what the server received, and is used instead.
- ``local_agent_tool_call`` with ``target.targetType == "mcp_tool"``: a call
  made by a local agent (Claude Code, Codex, Cursor, VS Code) and reported by
  Obot Sentry. ``details.request.body`` is the tool input itself.

Everything else (initialize, tools/list, resources, prompts, local non-MCP
tools like a shell) is skipped and counted. So is any call whose payload the
export withheld (``details.payloadRedacted``): without arguments there is
nothing to replay.

**The output holds real arguments from real users.** Treat it like the export
it came from: redact before sharing, and never commit it to a public repo.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from instar.mcp.client import _sse_messages

MCP_CALL = "mcp_call"
LOCAL_AGENT_TOOL_CALL = "local_agent_tool_call"


@dataclass
class Conversion:
    """The calls produced, and why every other event was left out."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    read: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    merged: int = 0


def _json(raw: Any) -> Any:
    """A payload body: already JSON, or a JSON-encoded string (Obot stores
    non-JSON bodies, such as an event stream, as a string)."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            events = _sse_messages(raw)
            return events[-1] if events else raw
    return raw


def _mcp_call_arguments(details: Mapping[str, Any]) -> Any:
    req = details.get("request") or {}
    body = _json(req.get("mutatedBody")) if req.get("mutated") else None
    if not isinstance(body, Mapping):
        body = _json(req.get("body"))
    if not isinstance(body, Mapping):
        return None
    params = body.get("params") or {}
    return params.get("arguments", {}) if isinstance(params, Mapping) else None


def _observed_error(event: Mapping[str, Any]) -> bool | None:
    """Did the tool report an error, as far as the export shows? None if unknown."""
    details = event.get("details") or {}
    resp = _json((details.get("response") or {}).get("body"))
    if event.get("eventType") == MCP_CALL and isinstance(resp, Mapping):
        if "error" in resp:
            return None  # a JSON-RPC error: the call failed, not the tool
        result = resp.get("result")
        if isinstance(result, Mapping):
            return bool(result.get("isError", False))
    status = (event.get("outcome") or {}).get("status")
    if status == "success":
        return False
    if status == "failure":
        return True
    return None


def convert_event(
    event: Mapping[str, Any], server_map: Mapping[str, str]
) -> tuple[dict[str, Any] | None, str | None]:
    """One audit event to one tool call, or ``(None, reason it was skipped)``."""
    kind = event.get("eventType")
    action = event.get("action") or {}
    target = event.get("target") or {}
    parent = target.get("parent") or {}
    details = event.get("details") or {}

    if kind == MCP_CALL:
        if action.get("operation") != "tools/call":
            return None, f"not a tool call ({action.get('operation') or 'unknown'})"
        tool = action.get("name") or target.get("name")
    elif kind == LOCAL_AGENT_TOOL_CALL:
        if target.get("targetType") != "mcp_tool":
            return None, "local tool, not an MCP tool"
        tool = target.get("name")
    else:
        return None, f"event type {kind or 'unknown'}"

    if not tool:
        return None, "no tool name"
    if details.get("payloadRedacted") or not details.get("request"):
        return None, "payload redacted or absent"

    if kind == MCP_CALL:
        args = _mcp_call_arguments(details)
    else:
        args = _json((details.get("request") or {}).get("body"))
    if not isinstance(args, Mapping):
        return None, "arguments unreadable"

    obot_server = str(parent.get("name") or parent.get("id") or "")
    call: dict[str, Any] = {
        "id": f"obot-{event.get('id')}",
        "tool": str(tool),
        "arguments": dict(args),
        "meta": {
            "source": "obot",
            "obot_event_id": event.get("id"),
            "obot_event_type": kind,
            "obot_server": obot_server or None,
            "occurred_at": (event.get("timestamp") or {}).get("occurredAt"),
            "observed_status": (event.get("outcome") or {}).get("status"),
            "observed_duration_ms": (event.get("outcome") or {}).get("durationMs"),
            "client": event.get("client") or None,
        },
    }
    if obot_server and obot_server in server_map:
        call["server"] = server_map[obot_server]
    observed = _observed_error(event)
    if observed is not None:
        call["meta"]["observed_is_error"] = observed
    return call, None


def convert(
    events: Iterable[Mapping[str, Any]],
    *,
    server_map: Mapping[str, str] | None = None,
    expect_observed: bool = False,
    dedupe: bool = True,
    tools: set[str] | None = None,
) -> Conversion:
    """Convert audit events to tool calls.

    ``server_map`` maps Obot server names to the names in your Instar server
    config; a call whose server is mapped runs against that server only, and an
    unmapped one runs against every configured server. ``expect_observed`` adds
    ``expect.is_error`` from what the export shows happened, so a replay checks
    that the server still behaves the same. ``dedupe`` keeps one call per
    distinct (server, tool, arguments), counting the rest in ``meta.seen``.
    """
    out = Conversion()
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        out.read += 1
        call, reason = convert_event(event, server_map or {})
        if call is None:
            out.skipped[reason or "unknown"] += 1
            continue
        if tools is not None and call["tool"] not in tools:
            out.skipped["tool not selected"] += 1
            continue
        if expect_observed and "observed_is_error" in call["meta"]:
            call["expect"] = {"is_error": call["meta"]["observed_is_error"]}
        key = json.dumps(
            [call["meta"]["obot_server"], call["tool"], call["arguments"]], sort_keys=True
        )
        if dedupe and key in seen:
            seen[key]["meta"]["seen"] += 1
            out.merged += 1
            continue
        call["meta"]["seen"] = 1
        seen[key] = call
        out.calls.append(call)
    return out


def read_export(path: str | Path) -> list[dict[str, Any]]:
    """Events from an Obot JSONL export. Also accepts a JSON list response."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith('{"items"'):
        data = json.loads(text)
        items = data.get("items", []) if isinstance(data, dict) else data
        return [e for e in items if isinstance(e, dict)]
    events: list[dict[str, Any]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{n}: not valid JSON ({e})") from e
        if isinstance(obj, dict):
            events.append(obj)
    return events


def write_calls(calls: list[dict[str, Any]], path: str | Path, *, overwrite: bool = False) -> None:
    p = Path(path)
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
