# Guide: measuring MCP servers

> **TL;DR:** `instar mcp probe` connects to your MCP servers and sizes every
> tool definition, which the model pays for on every turn whether or not the
> tool is used. `instar mcp run` replays a file of recorded tool calls straight
> at the servers, with no model involved, and reports per tool: latency,
> errors, how many tokens each result adds to context, and whether results met
> the checks you wrote. Tools that aren't marked read-only are refused unless
> you allow them by name.

**For:** anyone whose agents use MCP servers they didn't write, or wrote and
never measured. No model and no API key are needed for anything in this guide.

This is phase 1 of MCP measurement: the server on its own. Measuring a model
*using* the server (task success, turns, total cost) is phase 2.

---

## Try it with nothing installed

Instar ships a small synthetic server for exactly this:

```bash
instar mcp probe --servers Engineering/fixtures/mcp/demo-servers.json --price-model claude-sonnet-4-6
instar mcp run   --servers Engineering/fixtures/mcp/demo-servers.json \
                 --calls   Engineering/fixtures/mcp/demo-calls.jsonl --repeats 5
```

The demo's three tools show what a measurement finds: `lookup_order` is small
and correct; `search_docs` is correct but returns about 1,000 tokens by default,
so it fails its size budget; `refund_order` is marked destructive and gets
refused.

## 1. Describe your servers

```json
{
  "servers": {
    "github-direct": {
      "command": ["npx", "-y", "@example/github-mcp"],
      "env": {"GITHUB_READ_ONLY": "1"},
      "allow": ["search_issues", "get_file"]
    },
    "github-via-gateway": {
      "url": "https://gateway.example.com/mcp/github",
      "headers_env": {"Authorization": "GATEWAY_BEARER"}
    }
  }
}
```

- **stdio servers** use `command` (a list of arguments; `"{python}"` stands for
  the interpreter running Instar), with optional `env` and `cwd`.
- **HTTP servers** use `url` (streamable HTTP; JSON and event-stream replies are
  both handled). `headers_env` maps a header to the **environment variable**
  holding its value, so tokens never go in the file.
- `allow`: tools that may be called even though the server doesn't mark them
  read-only. See *Safety* below.
- `timeout_s`: per request, default 30.

If a stdio server exits on startup, the error includes the last lines it wrote
to stderr, which is usually the reason (a missing or malformed credential).

## 2. Probe: what the definitions cost

```bash
instar mcp probe --servers servers.json --price-model claude-sonnet-4-6
```

Per server: tool count, estimated tokens for all definitions, startup and
list latency, and optionally the input cost of carrying those definitions on
1,000 model calls. Per tool: tokens split into description and schema, whether
it's marked read-only or destructive, and notes such as missing descriptions,
undocumented parameters, or no read-only annotation at all.

**Token counts are estimates** (about four characters per token). Each client
serialises tools differently and each model tokenises differently. Use them to
compare servers and tools, and for order of magnitude, not as a bill.

## 3. Write the calls to replay

One JSON object per line:

```json
{"id": "issue-search", "tool": "search_issues", "arguments": {"q": "is:open label:bug"},
 "expect": {"is_error": false, "contains": ["bug"], "max_tokens": 2000}}
```

- `server` is optional. **Leave it out to run the call against every server**,
  which is how you compare two servers doing the same job, or one server direct
  versus through a gateway. Calls are interleaved across servers so network
  drift doesn't favour one of them.
- `expect` (all optional):

| Check | Passes when |
|---|---|
| `is_error` | the tool's `isError` equals this |
| `contains` | every listed string appears in the result text |
| `not_contains` | none of the listed strings appear |
| `max_tokens` | the result adds at most this many (estimated) tokens to context |
| `structured` | `structuredContent` contains these keys and values (nested subset) |

A call with no `expect` is still measured for latency and size; the run warns
that its correctness wasn't checked.

Good sources of calls: an agent's own transcripts, a gateway's audit log, or the
questions your team actually asks. Include a few that *should* fail (an unknown
id) and check for `is_error: true`.

## 4. Run

```bash
instar mcp run --servers servers.json --calls calls.jsonl --repeats 10 \
               --record results.jsonl
```

- `--dry-run` connects and checks every call (tool exists, permitted, required
  arguments present) but calls nothing. Run it first against a server you
  don't know.
- `--record` appends every raw result to a JSONL file. It holds whatever the
  server returned, so treat it like the data behind it.
- `--repeats` replays the file N times. Latency percentiles need at least 30
  answered calls per server; the run warns below that.

Each call ends in one of five states:

| Status | Meaning |
|---|---|
| `ok` | the tool answered |
| `tool_error` | the tool answered that it failed (`isError`). Often correct; check with `expect.is_error` |
| `transport_error` | the call never completed (timeout, crash, protocol error) |
| `refused` | not permitted; see *Safety* |
| `skipped` | no such tool, a required argument missing, server unreachable, or a dry run |

The command exits non-zero only for transport errors or skips outside a dry
run. Refusals and tool errors are findings, not failures.

## Safety

A measurement must never move money or delete data. A tool is called only if:

1. the server marks it `readOnlyHint: true` and not `destructiveHint`, **or**
2. its name is in that server's `allow` list.

Everything else is refused and listed as refused. Many servers don't annotate
their tools at all, so expect `probe` to flag them and plan to allow-list read
tools by hand **after reading what they do**. Prefer pointing measurements at a
test account or a read-only credential either way.

## What this doesn't tell you (yet)

- Whether a model *chooses* the right tool or arguments. That's phase 2 (an
  agent loop over the same servers), which will also replay recorded results
  so models can be compared on identical tool output.
- Exact token counts for a specific client and model.
- Anything about write tools, beyond refusing to call them.
