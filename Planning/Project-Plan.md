# Instar — Project Plan

**Date:** 2026-07-21 (seed; adapted from MVP1's `Docs/Experiments/Harness-Service+Product/OSS-Project-Plan.md`)
**Author:** Brian + Claude (pair-drafted in MVP1; ported here as this repo's authoritative plan)
**Status:** Plan draft — guides Brian's two-week push on Instar while Prithvi continues on `gateway-lab`.

**Companions in this repo:**
- [`Naming.md`](./Naming.md) — decision + off-box verifications still owed
- [`../CLAUDE.md`](../CLAUDE.md) — context for Claude sessions, organization principle, git workflow, IP boundary summary
- [`../README.md`](../README.md) — public-facing (currently mission stub; Week-1 sprint rewrites it)

**Provenance (in MVP1):** `~/projects/PurpleBlossomAI/MVP1/Docs/Experiments/Harness-Service+Product/` — the discussion doc, the original OSS project plan, the naming candidates with full deliberation. Read those for the market/strategy context that led here.

---

## TL;DR

Instar is an **open-source LLM measurement harness** (Apache 2.0). We're not building an OSS product; we're building an OSS *harness* that our service depends on and any org can adopt. This plan defines: what belongs in this repo vs. what stays in a private methodology repo, how the repo is governed and released, and what to do in the next two weeks to move from an empty repo to a credible v0.1.0.

**Two-week goal:** Ship v0.1.0 with a real README, governance files, CI green on main, `pyproject.toml` configured, a first runnable example in `--mock` mode, and a public issue backlog reflecting the actual roadmap. Everything else is post-sprint.

**Currently blocking public announcement (not blocking commits):** off-box verifications in `Naming.md` — PyPI name availability, domain reservations, formal trademark quick-pass, Prithvi ack.

---

## 1. What Instar is (and isn't)

**Is:**
- An open-source harness for measuring LLM workloads: cost, quality (via LLM-judge rubrics), latency, and router/model tradeoffs — on the user's own traffic.
- Router-agnostic and provider-agnostic: adapters for the common backends (Anthropic, OpenAI, Google, vLLM/self-hosted, OpenRouter, LiteLLM) conform to a small, stable interface.
- A *framework* for rubrics — not a library of rubrics. Ships with a couple of illustrative rubrics for the examples; real per-department rubrics stay in the private repo.
- Designed for reproducibility: mock-mode, hermetic tests, pinned model IDs, published fixtures that are synthetic and PII-free.

**Is not:**
- A router (we do not decide requests in production).
- A general eval platform (Promptfoo, Braintrust, DeepEval already win on breadth).
- An observability/trace store (Langfuse, Helicone, Arize already own that).
- A hosted service (no SaaS, no dashboard-as-a-product).
- A container for consulting IP. Methodology, per-department rubrics, and customer-derived fixtures stay in the private Purple Blossom AI repo.

---

## 2. IP boundary — what belongs where

Load-bearing decision. Once wrong, hard to reverse.

| Lives in this repo (Instar OSS) | Lives in private (Purple Blossom AI / MVP1) |
|---|---|
| Harness core (run definitions, workflow replay, cost accounting) | Cost-experiment methodology write-ups |
| Provider adapters (Anthropic, OpenAI, Google, vLLM, OpenRouter, LiteLLM) | Per-department rubrics (Marketing brand voice, Finance extraction, Ops p95 latency, etc.) |
| Routing policy *interface* + 2–3 reference policies (rules, cost/quality classifier) | Customer-specific routing recommendation logic |
| Rubric *framework* (how to define + score with an LLM judge) | Workload fixtures derived from customer data |
| Reporters (JSON, Markdown, CSV, simple HTML) | Client engagement deliverables, SOW templates, playbooks |
| Public synthetic fixtures + docs on how to build your own | Anything covered by NDA or that identifies customer traffic |
| Reproducibility harness (mock mode, seeded runs, model-ID pinning, CI) | |

**Enforcement:** every PR reviewer holds this line. `CONTRIBUTING.md` (Week-1 task) will have an *"What does not belong in this repo"* section in words a contributor will read.

---

## 3. License — Apache 2.0 (applied)

`LICENSE` is already in place. Rationale (recorded here so we don't relitigate):

- **Apache 2.0** — permissive, includes patent grant (important for anything AI-adjacent), universally trusted by enterprises, no adoption friction. ✓
- MIT — no patent grant; not worth the exposure in an active-patent field.
- BSL 1.1 / SSPL / Elastic License — depresses adoption; the point of a harness is that people run it.
- AGPL 3.0 — many enterprises are AGPL-allergic; hurts the "customers adopt this after we leave" motion.

The moat is the methodology, not the code.

---

## 4. Repository layout

Current + target. Note the Engineering-scoped src tree per `../CLAUDE.md` §Organization principle.

```
instar/
├── CLAUDE.md                    # context for Claude sessions
├── README.md                    # what this is, why, quick start (§7 Surface 1)
├── LICENSE                      # Apache-2.0
├── CONTRIBUTING.md              # Week-1: contribution flow, anti-scope, IP boundary
├── CODE_OF_CONDUCT.md           # Week-1: Contributor Covenant v2.1
├── SECURITY.md                  # Week-1: disclosure policy
├── CHANGELOG.md                 # Week-1: keep-a-changelog format
├── pyproject.toml               # Week-1: package metadata; package path = Engineering/src/instar
├── .gitignore
├── .github/
│   ├── workflows/               # Week-1: CI (lint, tests, coverage)
│   ├── ISSUE_TEMPLATE/          # Week-1
│   └── PULL_REQUEST_TEMPLATE.md # Week-1
├── Planning/
│   ├── README.md
│   ├── Project-Plan.md          # this file
│   └── Naming.md
├── Engineering/
│   ├── README.md
│   ├── src/
│   │   └── instar/              # Python package
│   │       ├── __init__.py
│   │       ├── core/            # Week-1+: run harness, workflow replay
│   │       ├── providers/       # Week-1+: anthropic, openai, google, vllm, openrouter, litellm
│   │       ├── policies/        # Week-1+: rules, cost/quality classifier
│   │       ├── rubrics/         # Week-1+: judge framework + illustrative rubric
│   │       ├── reporters/       # Week-1+: JSON, Markdown, CSV, HTML
│   │       └── cli/             # Week-1+: `instar run|report|bench`
│   ├── fixtures/                # Week-1+: synthetic, PII-free
│   ├── examples/                # Week-1+: runnable end-to-end walkthroughs
│   ├── tests/                   # Week-1+: pytest; mock-mode + live-seam markers
│   └── docs/                    # Week-2+: MkDocs Material source
└── Marketing/
    └── README.md                # placeholder; Week-2+: positioning, announcement drafts
```

### Branching + versioning

- Trunk-based on `main`. Short-lived feature branches, PR review, squash-merge.
- **Semantic versioning** — v0 while API is unstable, v1 when we're ready to promise it.
- Release notes in `CHANGELOG.md` (keep-a-changelog format).
- Tagged releases via GitHub Releases.

### Release cadence

- **v0.1.0 within two weeks** (the sprint goal). Purpose: mark the OSS baseline.
- After v0.1: release when there's something worth releasing. Don't schedule.

---

## 5. Roadmap & feature tracking

**Where the roadmap lives:** GitHub Issues + one GitHub Project (`v1 Roadmap`). Not a wiki, not a Google Doc, not a Notion. If it isn't in Issues it doesn't exist.

**Labeling scheme (small, opinionated):**
- `type:` — `feature`, `bug`, `docs`, `provider-adapter`, `policy`, `rubric-framework`, `chore`
- `priority:` — `now`, `soon`, `later`
- `good-first-issue` — bounded, well-specified, no repo-wide context required
- `help-wanted` — anything we'd accept a contribution on
- `blocked-by-methodology` — anything that would require exposing private IP; kept visible so the boundary is *transparent* to contributors

**RFCs:** for anything touching the harness API surface, providers interface, or routing-policy interface — a short RFC in `Engineering/docs/rfcs/NNNN-title.md`, opened as a PR, merged when accepted. Everything else: an Issue is enough.

**Milestones:** `v0.1`, `v0.2`, then stop planning until we've shipped both.

---

## 6. Contribution model

**Initially: closed by default, opening gradually.**
- Only Brian and Prithvi have merge rights at v0.1.
- All external contributions welcome as Issues + PRs, but merge cadence is deliberate.
- Contribution ladder implicit: consistent quality PRs → triage rights → merge rights on specific areas.

**Code of Conduct:** Contributor Covenant v2.1 verbatim.

**DCO or CLA?** Recommend **DCO** (sign-off on commits). Light, standard, no lawyers needed. CLA only if a real reason surfaces (dual-licensing, etc.).

---

## 7. Documentation surface

Two audiences, two surfaces.

**Surface 1 — the README.** Answers, in this order:
1. What is this (one sentence).
2. Who is it for (one sentence).
3. What it looks like running (one code block, `instar run examples/hello.yaml`, expected output).
4. Install (`pip install instar` — subject to PyPI availability, see `Naming.md`).
5. Where to go next (link to docs site).
6. What it is NOT (the anti-scope from §1, short).
7. License, contributing, security pointers.

**Surface 2 — the docs site.** MkDocs Material, source in `Engineering/docs/`, published to GitHub Pages. Sections:
- Getting started (install → first run → first rubric → first report)
- Concepts (workflow, policy, rubric, reporter, fixture)
- How-to guides (add a provider, define a rubric, replay traffic, compare two routers, run in CI)
- Reference (CLI, YAML schema, Python API)
- Architecture (one page — how the pieces fit)
- FAQ ("why isn't this a SaaS", "how does this compare to Promptfoo/Braintrust", "how does the private methodology relate")

**Explicitly deferred:** dedicated docs domain, blog, tutorials-as-videos, community server (Discord/Slack), mailing list. Not until v0.2.

---

## 8. Quality gates & CI

Every PR must pass:
- **Tests** — `pytest`, mock-mode green. Live-provider tests behind a marker + skipped without secrets.
- **Lint** — `ruff` (fast, opinionated). No `black` on top; ruff-format is enough.
- **Type check** — `mypy` in `--strict` on `Engineering/src/`. Loose on tests.
- **Coverage** — reported but not enforced at v0.1. Enforce at v0.2 once we know the right floor.
- **License header check** — every source file has SPDX header.
- **CHANGELOG update** — required for `type:feature` or breaking PRs.

Release workflow: tag → build sdist + wheel → PyPI publish (once name is confirmed) → GitHub Release with auto-generated notes from CHANGELOG.

---

## 9. Security & disclosure

- `SECURITY.md` names a contact address (dedicated alias, not a personal inbox) and a 90-day coordinated disclosure window.
- No secrets in the repo, ever. `pre-commit` hook with `detect-secrets` on the initial sweep, then on every PR.
- GitHub secret scanning + Dependabot alerts on.

Low ceremony at v0.1; formalize if/when a real report arrives.

---

## 10. The two-week sprint — seed the empty repo

Repo state at sprint start: `LICENSE`, `.gitignore`, `README.md` (mission stub), `CLAUDE.md`, `Planning/`, `Engineering/src/instar/__init__.py`, `Marketing/README.md`. **No code, no governance files, no CI, no pyproject.**

Off-box verifications Brian owes before publicizing (see `Naming.md`): PyPI availability, domain reservations, formal trademark quick-pass, Prithvi ack. Not blocking commits; blocking public announcement.

### Week 1 — scaffolding

- **README rewrite** per §7 (Surface 1). Most important artifact at v0.1.
- **Governance files** — `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `CHANGELOG.md` (empty but present).
- **`pyproject.toml`** — package metadata, deps, `instar` CLI entry point. Package path = `Engineering/src/instar`.
- **CI baseline** — `.github/workflows/` for tests + lint (ruff) + typecheck (mypy strict). Green on an empty test suite is fine.
- **Issue templates + PR template** — `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`.
- **Repo layout** — create empty modules under `Engineering/src/instar/` (core/, providers/, policies/, rubrics/, reporters/, cli/) with docstrings.
- **First 10–15 issues** — carve remaining scope (provider adapters, routing policies, rubric framework, real fixtures) into concrete labeled issues on a `v1 Roadmap` project board.

### Week 2 — proof of life + docs

- **First code lift** — port the smallest useful slice (or reimplement) so `instar run examples/hello.yaml` produces a real result in `--mock` mode. This is the "does it actually work" gate for v0.1.
- **One provider adapter** — real (Anthropic or OpenAI), audited for interface consistency.
- **One illustrative rubric** — small generic judge (e.g., "does the response answer the question") + docs. NOT a Marketing/Finance/Ops rubric (those stay private).
- **Docs site scaffold** — MkDocs Material in `Engineering/docs/`, first three "Getting started" pages, deploy to GitHub Pages.
- **v0.1.0 release** — tag, GitHub Release, PyPI push *if* the off-box PyPI verification passed.
- **Announcement — draft only, do not publish.** Blog post + short LinkedIn draft in `Marketing/`. Publishing is a separate decision after v0.1 lands.

### What this sprint is NOT

Explicitly deferred:
- No new providers beyond the first one, no new policies, no new rubrics beyond the illustrative one.
- No SaaS scaffolding, no hosted anything.
- No public announcement (drafts only).
- No community server, Discord, mailing list, telemetry.

The sprint is *packaging*, not *feature-building*.

---

## 11. First 90 days after the sprint

Loose, revisable:
- **Weeks 3–4:** first external eyeballs — share with 3–5 trusted technical friends for feedback, not adoption. Iterate on friction. Cut a v0.1.1 or v0.2 based on what breaks.
- **Weeks 5–8:** stabilize the API surface enough to promise it at v1. Add the 1–2 provider adapters most-asked-for.
- **Weeks 9–12:** first real external contributor (if it happens). First reference from a service engagement (the harness config we hand a customer *becomes* their v0.1 config). If a paid pilot lands, its harness needs go straight into the roadmap.

Success at day 90 is *not* stars or downloads. It's: one external contributor, one paying customer using it as part of an engagement, and Brian + Prithvi able to release v0.2 in an afternoon.

---

## 12. Success metrics — real vs. vanity

**Real:**
- Number of external orgs running Instar in earnest (proxy: PyPI installs, once we opt-in to telemetry).
- Number of external PRs merged (not just opened).
- Median time-to-first-run for a new user (docs tutorial — target under 15 minutes).
- Adoption inside Purple Blossom AI consulting engagements — every SOW referencing Instar by name counts.

**Ignore:**
- GitHub stars.
- Reddit/HN traction.
- Twitter/X mentions.

If we optimize for stars we build for the demo. If we optimize for "one customer built on it," we build the right thing.

---

## 13. Repo relationship — Instar and gateway-lab

Two repos, deliberately parallel — for a bounded window.

- **`github.com/PurpleBlossomAI/gateway-lab`** — Prithvi's experimental space. Testing, paper-writing. Not under Brian's stewardship day-to-day.
- **`github.com/PurpleGardens/instar`** — the curated, packaged, adoptable OSS artifact. This repo.

They share design DNA — both are internal Purple Blossom AI work, so patterns can be ported from `gateway-lab` into Instar as clean-room re-implementations (no license issue because we own both). The harness core was ported this way at commit `1b250bf` on 2026-07-22.

**Convergence decision (2026-07-22): Instar is the durable home for the Measurement Harness.** Under the original plan this call was deferred 30–60 days from repo creation. Brian moved it up because the StBarths session shipped a substantive harness port plus a full docs tree here, and continuing to treat three repos (Instar, gateway-lab, MVP1) as parallel candidates for the same code was creating duplication risk and scope ambiguity.

**What this means concretely:**

- New harness work goes to Instar. Do not port to MVP1 or gateway-lab.
- MVP1's harness code is to be archived (not deleted — preserve provenance for any later legal or IP questions).
- Prithvi keeps `gateway-lab` for a few more weeks (paper writing, in-flight items). After that the repo winds down; consider archiving it on GitHub (Settings → Archive) so it's read-only and clearly signals "reference, not active."
- If code lands in `gateway-lab` during the overlap window that belongs in Instar, flag it and port here rather than letting the duplication persist.

---

## 14. Open questions

- **PyPI at v0.1, or wait for v0.2?** Publishing on PyPI is a real commitment (semver, backward-compat expectations). Recommend: skip PyPI at v0.1, install-from-source only; publish at v0.2 when the API is stable. Reconsider if a Week-1 user wants it.
- **Telemetry — opt-in usage pings, or none?** Would help know if anyone's running it. Recommend: none until v0.2, and even then opt-in with a clear README paragraph. Trust > data.
- **Governance long-term.** Formal steering structure, or founder-led indefinitely? Recommend: founder-led (Brian + Prithvi) through v1; revisit only if a real second maintainer emerges or a corporate sponsor appears.
- **When (if ever) do we publish an anonymized BG-derived fixture set?** Would be strong credibility. Also tells competitors what we're measuring. Recommend: hold until 2+ paying customers whose engagements would benefit from the public reference.
- **Contributor licensing (DCO vs CLA).** Recommend DCO for v0.1; revisit if we ever want dual-licensing.

---

## 15. Roles

- **Brian:** naming, IP boundary, positioning, external announcements, engagement-to-repo feedback loop.
- **Prithvi:** most of the code work if he chooses to contribute; provider adapters; CI; docs technical scaffolding. Continues to own `gateway-lab` independently.
- **Claude (fresh sessions in this repo):** pair-authoring on plans, RFCs, docs, code scaffolding. Not a committer independently.
- **Future maintainers:** unnamed; ladder up per §6.

Weekly check-in cadence during the sprint: one short sync at end of Week 1 to unblock and course-correct, one at end of Week 2 to confirm v0.1 ships and to write the (unpublished) announcement draft.

---

*This plan is a living document. When something turns out to be wrong, edit it — don't leave stale guidance behind.*
