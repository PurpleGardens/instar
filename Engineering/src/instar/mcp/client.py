# SPDX-License-Identifier: Apache-2.0
"""A minimal, stdlib-only MCP client for measurement.

Speaks JSON-RPC 2.0 over the two standard MCP transports:

- **stdio**: the server is a subprocess; messages are newline-delimited JSON
  on its stdin/stdout. Its stderr is drained and discarded (servers log there).
- **streamable HTTP**: each message is a POST to one endpoint; the reply is
  either a JSON body or a ``text/event-stream`` carrying the response. The
  session id the server returns on ``initialize`` is sent on every later call.

It implements only what measurement needs: ``initialize``, ``tools/list``
(following pagination) and ``tools/call``, plus answering a server's ``ping``.
Anything else the server asks of the client is refused with "method not
found", which is the honest answer from a client that offers no capabilities.

Timing is wall-clock around each request, taken here rather than reported by
the server, for the same reason the LLM runners time from the client: it is
what the caller actually waits.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "instar", "version": "0"}
DEFAULT_TIMEOUT_S = 30.0


class MCPError(RuntimeError):
    """A transport failure, or a JSON-RPC error response from the server."""


@dataclass(frozen=True)
class ServerSpec:
    """How to reach one MCP server.

    Exactly one of ``command`` (stdio) or ``url`` (streamable HTTP) is set.
    ``headers_env`` maps an HTTP header name to the *environment variable*
    holding its value, so secrets never live in a config file.

    ``allow`` names tools that may be called even though the server does not
    mark them read-only. See :func:`instar.mcp.toolcalls.call_permitted`.
    """

    name: str
    command: tuple[str, ...] = ()
    url: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None
    headers_env: Mapping[str, str] = field(default_factory=dict)
    allow: frozenset[str] = frozenset()
    timeout_s: float = DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        if bool(self.command) == bool(self.url):
            raise ValueError(f"server {self.name!r}: give exactly one of 'command' or 'url'")

    @property
    def transport(self) -> str:
        return "stdio" if self.command else "http"

    @classmethod
    def from_json(cls, name: str, d: Mapping[str, Any]) -> ServerSpec:
        command = d.get("command", ())
        if isinstance(command, str):
            raise ValueError(f"server {name!r}: 'command' is a list of arguments, not a string")
        # "{python}" stands for the interpreter running Instar, so a config can
        # start a Python server without knowing whether it is python or python3.
        return cls(
            name=name,
            command=tuple(sys.executable if c == "{python}" else str(c) for c in command),
            url=None if d.get("url") is None else str(d["url"]),
            env={str(k): str(v) for k, v in dict(d.get("env", {})).items()},
            cwd=None if d.get("cwd") is None else str(d["cwd"]),
            headers_env={str(k): str(v) for k, v in dict(d.get("headers_env", {})).items()},
            allow=frozenset(str(t) for t in d.get("allow", [])),
            timeout_s=float(d.get("timeout_s", DEFAULT_TIMEOUT_S)),
        )


def load_servers(path: str) -> list[ServerSpec]:
    """Read ``{"servers": {name: spec, ...}}`` from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f'{path}: expected {{"servers": {{name: {{...}}}}}}')
    return [ServerSpec.from_json(str(k), v) for k, v in servers.items()]


class _Transport(ABC):
    @abstractmethod
    def send(self, msg: dict[str, Any]) -> None: ...

    @abstractmethod
    def request(self, msg: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        """Send a request and return the JSON-RPC response with the same id."""

    @abstractmethod
    def close(self) -> None: ...


class _StdioTransport(_Transport):
    def __init__(self, spec: ServerSpec) -> None:
        env = {**os.environ, **spec.env}
        try:
            self.proc = subprocess.Popen(
                list(spec.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=spec.cwd,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as e:
            raise MCPError(f"could not start {spec.command[0]!r}: {e}") from e
        self.inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        # The last few stderr lines, so a server that exits on startup can say why.
        self.stderr_tail: deque[str] = deque(maxlen=5)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # a server printing junk to stdout; not a message
            if isinstance(msg, dict):
                self.inbox.put(msg)
        self.inbox.put(None)  # EOF

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            if line.strip():
                self.stderr_tail.append(line.strip()[:300])

    def send(self, msg: dict[str, Any]) -> None:
        if self.proc.stdin is None or self.proc.poll() is not None:
            raise MCPError("server process is not running")
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPError(f"write to server failed: {e}") from e

    def request(self, msg: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        self.send(msg)
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"timed out after {timeout_s:g}s waiting for {msg['method']}")
            try:
                reply = self.inbox.get(timeout=remaining)
            except queue.Empty:
                continue
            if reply is None:
                time.sleep(0.05)  # let the stderr reader catch the last lines
                tail = " | ".join(self.stderr_tail)
                raise MCPError(
                    "server closed its output" + (f"; its stderr said: {tail}" if tail else "")
                )
            if _answer_server(reply, self.send):
                continue
            if reply.get("id") == msg["id"]:
                return reply

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()


class _HttpTransport(_Transport):
    def __init__(self, spec: ServerSpec) -> None:
        assert spec.url is not None
        self.url = spec.url
        self.headers: dict[str, str] = {}
        for header, var in spec.headers_env.items():
            value = os.environ.get(var)
            if value is None:
                raise MCPError(
                    f"server {spec.name!r}: header {header} needs ${var}, which is unset"
                )
            self.headers[header] = value
        self.session: str | None = None
        self.protocol: str | None = None

    def _post(self, msg: dict[str, Any], timeout_s: float) -> tuple[int, str, str, dict[str, str]]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(
            self.url, data=json.dumps(msg).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, resp.headers.get("Content-Type", ""), body, dict(resp.headers)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise MCPError(f"HTTP {e.code} from server: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(f"request to {self.url} failed: {e}") from e

    def send(self, msg: dict[str, Any]) -> None:
        self._post(msg, DEFAULT_TIMEOUT_S)

    def request(self, msg: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        _status, ctype, body, headers = self._post(msg, timeout_s)
        sid = next((v for k, v in headers.items() if k.lower() == "mcp-session-id"), None)
        if sid:
            self.session = sid
        if "text/event-stream" in ctype:
            candidates = _sse_messages(body)
        else:
            parsed = json.loads(body) if body.strip() else None
            candidates = parsed if isinstance(parsed, list) else [parsed]
        for m in candidates:
            if isinstance(m, dict) and m.get("id") == msg["id"] and "method" not in m:
                return m
        raise MCPError(f"no response to {msg['method']} in the server's reply")

    def close(self) -> None:
        if not self.session:
            return
        headers = {"Mcp-Session-Id": self.session, **self.headers}
        req = urllib.request.Request(self.url, headers=headers, method="DELETE")
        # A server that doesn't support session teardown is fine.
        with contextlib.suppress(urllib.error.URLError, OSError):
            urllib.request.urlopen(req, timeout=5).close()


def _sse_messages(body: str) -> list[Any]:
    """JSON payloads from the ``data:`` lines of an event stream, one per event."""
    out: list[Any] = []
    for event in body.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in event.split("\n") if line.startswith("data:")
        )
        if data:
            try:
                out.append(json.loads(data))
            except json.JSONDecodeError:
                continue
    return out


def _answer_server(msg: dict[str, Any], send: Any) -> bool:
    """Handle a message the *server* initiated. True if it was one.

    Notifications are ignored. A ``ping`` gets an empty result; any other
    request gets "method not found", since this client offers no capabilities.
    """
    if "method" not in msg:
        return False
    if "id" in msg:
        if msg["method"] == "ping":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "error": {"code": -32601, "message": "method not found"},
                }
            )
    return True


@dataclass(frozen=True)
class Timed:
    """A JSON-RPC result and how long the request took, client side."""

    result: dict[str, Any]
    latency_s: float


class MCPClient:
    """One session with one server. Use as a context manager."""

    def __init__(self, spec: ServerSpec) -> None:
        self.spec = spec
        self._transport: _Transport | None = None
        self._next_id = 0
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None
        self.init_latency_s: float = 0.0

    def __enter__(self) -> MCPClient:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        self._transport = (
            _StdioTransport(self.spec) if self.spec.command else _HttpTransport(self.spec)
        )
        init = self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.init_latency_s = init.latency_s
        self.server_info = dict(init.result.get("serverInfo") or {})
        self.protocol_version = init.result.get("protocolVersion")
        if isinstance(self._transport, _HttpTransport):
            self._transport.protocol = self.protocol_version
        self._transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _request(self, method: str, params: dict[str, Any]) -> Timed:
        if self._transport is None:
            raise MCPError("client is not open")
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        t0 = time.perf_counter()
        reply = self._transport.request(msg, self.spec.timeout_s)
        latency = time.perf_counter() - t0
        if "error" in reply:
            err = reply["error"] or {}
            raise MCPError(f"{method}: {err.get('message', 'error')} (code {err.get('code')})")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise MCPError(f"{method}: response has no result object")
        return Timed(result, latency)

    def list_tools(self) -> Timed:
        """Every tool, following ``nextCursor``; latency is the sum over pages."""
        tools: list[Any] = []
        total = 0.0
        cursor: str | None = None
        for _ in range(100):  # a server paginating forever is a bug, not a catalog
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            page = self._request("tools/list", params)
            total += page.latency_s
            tools.extend(page.result.get("tools") or [])
            cursor = page.result.get("nextCursor")
            if not cursor:
                break
        return Timed({"tools": tools}, total)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Timed:
        return self._request("tools/call", {"name": name, "arguments": dict(arguments)})


def tool_names(tools: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(t.get("name")) for t in tools]
