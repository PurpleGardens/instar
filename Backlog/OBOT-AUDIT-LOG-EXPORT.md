# Obot Audit Log Export — punted 2026-09-25

**Status:** Deferred. Not blocking day-to-day Instar work; may be blocking part of Instar's MCP-server tooling story (accepted for now).

**Cross-repo context.** This ticket sits in the Instar repo but the work spans:
- **Instar** (`~/projects/PurpleGardens/instar/`) — the MCP-server tooling that would consume Obot audit exports.
- **MVP1** (`~/projects/PurpleBlossomAI/MVP1/`) — where the Blossom Grove MCP servers are configured in Claude Code sessions that call into Obot.
- **Obot** (external, self-hosted) — the audit-log source; UI is where the export is configured.

## Where I got stuck

1. Blossom Grove MCP servers were wired into Claude Code sessions as intended.
2. Went to Obot expecting audit rows for those MCP-tool calls → **audit log view was empty**. Root cause not diagnosed; unclear whether events aren't being emitted, aren't being retained, or aren't being surfaced in the UI I was looking at.
3. Tried the **`+ Create Export`** button as a workaround → it requires an **S3-compatible object store** as the export sink. No sink provisioned; not willing to stand one up today.

## Why punt

- Standing up an S3-compatible bucket (real S3, R2, MinIO, etc.) + wiring Obot's export config to it is a half-day of yak-shaving that doesn't advance today's Instar goals.
- The empty audit view is the more interesting problem anyway — export is downstream of that.
- Accepted cost: whatever Instar MCP tooling depends on Obot audit exports stays blocked until this is picked back up.

## Pick-up checklist (when this comes off the shelf)

- [ ] Diagnose the empty audit view first. Confirm whether MCP calls are producing audit events at all (Obot server logs, DB, whatever the storage backend is) before touching export config.
- [ ] Decide the S3 sink: cheapest reasonable options are Cloudflare R2 (no egress, we already use CF) or a small MinIO on an existing OCI box. Skip real AWS S3 unless there's a reason.
- [ ] Once a sink exists, re-try `+ Create Export` and capture the exact config Obot wants (bucket, region string, credential shape) in this doc for the next person.
- [ ] Re-check which piece of Instar MCP tooling actually needs the export vs. can read audit rows directly — the export path may not be required if direct read works.

## Not doing (explicitly)

- Not standing up S3/R2/MinIO today.
- Not filing this against MVP1's `Docs/Backlog/` — the consuming code lives in Instar, so the ticket lives here.
