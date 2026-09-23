# SPDX-License-Identifier: Apache-2.0
"""A tiny synthetic MCP server, so ``instar mcp`` can be tried with nothing installed.

Three tools over made-up data, chosen to show what a measurement finds:

- ``lookup_order`` (read-only): a short, structured answer. The well-behaved case.
- ``search_docs`` (read-only): answers correctly but at length. The response
  bloat case: every token of it lands in the model's context.
- ``refund_order`` (marked destructive): exists to show that ``instar mcp run``
  refuses to call a tool that isn't read-only unless you allow it by name.

Run over stdio (the default) or streamable HTTP::

    python -m instar.mcp.demo_server
    python -m instar.mcp.demo_server --http 8765 [--sse]

Everything here is fictional and deterministic. It is a fixture, not a sample
of how to write a production server.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SERVER_INFO = {"name": "instar-demo", "version": "1"}

_ORDERS = {
    "A-100": {"status": "shipped", "carrier": "Northwind Freight", "eta_days": 2},
    "A-101": {"status": "processing", "carrier": None, "eta_days": 5},
    "A-102": {"status": "delivered", "carrier": "Northwind Freight", "eta_days": 0},
}

_DOC = (
    "Returns are accepted within 30 days of delivery for unused items in original "
    "packaging. Refunds are issued to the original payment method within 5 business "
    "days of the return being received. "
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "title": "Look up an order",
        "description": "Return the status, carrier and estimated days to delivery for one order.",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "Order id, e.g. A-100"}},
            "required": ["order_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_docs",
        "title": "Search help documents",
        "description": (
            "Search the help centre and return matching articles. Use this whenever the "
            "user asks about policies, returns, refunds, shipping, accounts, billing or "
            "anything else covered by the help centre. Results include the full article "
            "text so the answer can be quoted directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of articles to return",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 10,
                },
                "locale": {
                    "type": "string",
                    "description": "Help-centre locale",
                    "enum": ["en-US", "en-GB", "de-DE", "fr-FR", "es-ES"],
                    "default": "en-US",
                },
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "refund_order",
        "title": "Refund an order",
        "description": "Issue a full refund for an order. This moves money.",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
]


def _text(text: str, *, error: bool = False, structured: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if structured is not None:
        out["structuredContent"] = structured
    return out


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "lookup_order":
        order = _ORDERS.get(str(args.get("order_id", "")))
        if order is None:
            return _text(f"No order {args.get('order_id')!r}.", error=True)
        return _text(json.dumps(order), structured=order)
    if name == "search_docs":
        limit = max(1, min(10, int(args.get("limit", 3))))
        articles = [
            f"Article {i + 1}: Returns and refunds (matched {args.get('query')!r}).\n" + _DOC * 6
            for i in range(limit)
        ]
        return _text("\n\n".join(articles))
    if name == "refund_order":
        return _text(f"Refunded order {args.get('order_id')}.")
    raise KeyError(name)


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, the response out (None for notifications)."""
    if "id" not in msg:
        return None
    method, mid = msg.get("method"), msg["id"]
    params = msg.get("params") or {}
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            result = call(str(params.get("name")), dict(params.get("arguments") or {}))
        except KeyError:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"},
            }
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        reply = handle(json.loads(line))
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


def make_http_server(port: int, *, sse: bool = False) -> ThreadingHTTPServer:
    """A streamable-HTTP server on ``127.0.0.1:port`` (0 picks a free port)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # keep test output clean
            pass

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            msg = json.loads(body)
            reply = handle(msg)
            if reply is None:
                self.send_response(202)
                self.end_headers()
                return
            self.send_response(200)
            if msg.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", uuid.uuid4().hex)
            if sse:
                payload = f"event: message\ndata: {json.dumps(reply)}\n\n".encode()
                self.send_header("Content-Type", "text/event-stream")
            else:
                payload = json.dumps(reply).encode()
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_DELETE(self) -> None:
            self.send_response(200)
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on PORT")
    p.add_argument("--sse", action="store_true", help="answer HTTP requests as event streams")
    args = p.parse_args(argv)
    if args.http is None:
        serve_stdio()
        return 0
    server = make_http_server(args.http, sse=args.sse)
    print(f"instar demo MCP server on http://127.0.0.1:{server.server_address[1]}/mcp")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
