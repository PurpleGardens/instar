# SPDX-License-Identifier: Apache-2.0
"""What a server's tool definitions cost before anyone asks a question.

Every tool an agent can call is described to the model on every turn: its name,
its description and its input schema. That context is paid for on each model
call in the loop, whether or not the tool is used. This module lists a server's
tools and sizes each definition, split into description and schema so the
report can say *where* the weight is.

**Token counts here are estimates** (about four characters per token, the same
heuristic as mock mode). Each client serialises tool definitions its own way
and each model tokenises differently, so an exact count only exists for one
client and one model. The estimate is for comparing servers and tools with each
other, and for ordering of magnitude, not for a bill.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from instar.core.cost import PRICING
from instar.mcp.client import MCPClient, MCPError, ServerSpec
from instar.providers.base import estimate_tokens


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _undocumented_params(schema: Mapping[str, Any]) -> list[str]:
    props = schema.get("properties") or {}
    if not isinstance(props, Mapping):
        return []
    return sorted(
        str(k) for k, v in props.items() if not (isinstance(v, Mapping) and v.get("description"))
    )


@dataclass
class ToolDef:
    """One tool's definition, sized."""

    name: str
    read_only: bool
    destructive: bool
    description_tokens: int
    schema_tokens: int
    total_tokens: int
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_tool(cls, tool: Mapping[str, Any]) -> ToolDef:
        name = str(tool.get("name", ""))
        desc = str(tool.get("description") or "")
        schema = tool.get("inputSchema") or {}
        ann = tool.get("annotations") or {}
        # What a model is typically shown: name, description, input schema.
        shown = {"name": name, "description": desc, "input_schema": schema}
        notes: list[str] = []
        if not desc:
            notes.append("no description")
        undocumented = _undocumented_params(schema) if isinstance(schema, Mapping) else []
        if undocumented:
            notes.append(f"parameters without a description: {', '.join(undocumented)}")
        if "readOnlyHint" not in ann:
            notes.append("not annotated read-only or not; treated as not read-only")
        return cls(
            name=name,
            read_only=bool(ann.get("readOnlyHint", False)),
            destructive=bool(ann.get("destructiveHint", False)),
            description_tokens=estimate_tokens(desc) if desc else 0,
            schema_tokens=estimate_tokens(_compact(schema)),
            total_tokens=estimate_tokens(_compact(shown)),
            notes=notes,
        )


@dataclass
class ServerProbe:
    """One server's catalogue and what it costs in context."""

    server: str
    transport: str
    ok: bool
    error: str | None = None
    server_info: dict[str, Any] = field(default_factory=dict)
    protocol_version: str | None = None
    init_ms: float = 0.0
    list_ms: float = 0.0
    tools: list[ToolDef] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(t.total_tokens for t in self.tools)

    def cost_per_1k_turns_usd(self, model: str) -> float | None:
        """Input cost of carrying these definitions on 1,000 model calls."""
        price = PRICING.get(model)
        if price is None:
            return None
        return self.total_tokens * price[0] / 1_000_000 * 1000

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


def probe_tools(
    spec: ServerSpec,
    raw_tools: list[Any],
    *,
    init_s: float,
    list_s: float,
    server_info: dict[str, Any],
    protocol: str | None,
) -> ServerProbe:
    return ServerProbe(
        server=spec.name,
        transport=spec.transport,
        ok=True,
        server_info=server_info,
        protocol_version=protocol,
        init_ms=init_s * 1000.0,
        list_ms=list_s * 1000.0,
        tools=[ToolDef.from_tool(t) for t in raw_tools if isinstance(t, Mapping)],
    )


def probe(spec: ServerSpec) -> ServerProbe:
    """Connect, list every tool, size the definitions. Never raises for a server fault."""
    try:
        with MCPClient(spec) as client:
            listed = client.list_tools()
            return probe_tools(
                spec,
                listed.result["tools"],
                init_s=client.init_latency_s,
                list_s=listed.latency_s,
                server_info=client.server_info,
                protocol=client.protocol_version,
            )
    except MCPError as e:
        return ServerProbe(server=spec.name, transport=spec.transport, ok=False, error=str(e))
