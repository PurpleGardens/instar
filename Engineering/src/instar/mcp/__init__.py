# SPDX-License-Identifier: Apache-2.0
"""Measuring MCP servers: what their tools cost before and after they are called.

Phase 1 needs no model at all. :mod:`instar.mcp.probe` measures what a server's
tool definitions cost in context before anyone asks a question, and
:mod:`instar.mcp.toolcalls` replays recorded tool calls straight at the server
and measures what comes back: latency, errors, response size, and whether the
result met the expectations written for it.

The client in :mod:`instar.mcp.client` is stdlib-only, like the rest of the
harness core, and speaks the two standard transports (stdio and streamable
HTTP). It is a measuring client, not a general-purpose one: it implements the
handful of methods a measurement needs and nothing else.
"""
