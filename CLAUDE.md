# Instar — Claude Context Guide

**Last Updated:** 2026-07-22
**Repo:** `~/projects/PurpleGardens/instar` · GitHub: `PurpleGardens/instar`
**Status:** Harness core, CLI, provider adapters, routing policies, rubric framework, reporters, tests, and a substantial `Engineering/Docs/` knowledge tree are all landed on `main`. CI (ruff, mypy --strict, pytest matrix, SPDX check) green. Pre-v0.1 tag. Off-box verifications in `Planning/Naming.md`: PyPI ✓ (reserved as `instar-harness` — bare `instar` disallowed by PyPI policy; import + CLI stay `instar`), docs domain ✓ (`instar-dev.org`), trademark knockout ✓ (Class-9 crowded — full clearance owed before any federal filing). Still owed before public announcement: full Class-9 trademark clearance + a courtesy note to Prithvi. Repo being public is not an announcement.

---

## What Instar is

Instar is an **open-source LLM measurement harness** (Apache 2.0). It runs a user's own workloads through candidate models, providers, and routing policies, and produces defensible cost/quality/latency numbers — so an organization can decide *which router, which model mix, for which team* on evidence rather than vendor claims.

The name comes from biology — an *instar* is a stage between molts in an insect's development. That fits both the tool's job (measuring the stage-by-stage progression of a workload) and the wider theme (organizations metamorphosing into AI-productive shapes). It's a sibling of the Blossom Grove brand, not a rival to it.

**One-line mission (from README.md):** *"an open-source project to help companies shed their old habits in order to grow and assume a new form of work with AI."*

## What Instar is NOT

- **Not a router.** We do not decide production requests. That's OpenRouter / LiteLLM / Portkey / Kong / Cloudflare AI Gateway territory — a crowded, well-funded space.
- **Not a general eval platform.** Promptfoo / Braintrust / DeepEval already win on breadth.
- **Not an observability/trace store.** Langfuse / Helicone / Arize / LangSmith own that.
- **Not a hosted service.** No SaaS, no dashboard-as-a-product.
- **Not a container for private consulting IP.** Per-department rubrics, workload fixtures derived from customer data, and the methodology write-ups stay in a private Purple Blossom AI repo (see *IP boundary* below).

**Read `Planning/Project-Plan.md` for the full rationale, license reasoning, roadmap model, contribution model, and two-week sprint plan.** This file is orientation; that file is the plan.

---

## Repo state right now

```
instar/
├── CLAUDE.md              # this file
├── LICENSE                # Apache 2.0
├── README.md              # public front page (v0.1-shaped)
├── CONTRIBUTING.md        # contribution flow, IP boundary, DCO sign-off
├── CODE_OF_CONDUCT.md     # Contributor Covenant v2.1
├── SECURITY.md            # 90-day coordinated disclosure
├── CHANGELOG.md           # keep-a-changelog format; [Unreleased] tracks pre-v0.1
├── pyproject.toml         # setuptools; package rooted at Engineering/src/instar/
├── .gitignore
├── .github/workflows/ci.yml  # ruff + mypy --strict + pytest matrix + SPDX header check
├── Planning/              # strategy, plan, naming, methodology
│   ├── README.md
│   ├── Project-Plan.md    # THE plan; read this second
│   ├── Naming.md          # why "Instar" + off-box verifications still owed
│   └── Engagement-Methodology.md   # 11-phase spine, IP-boundary annotated per phase
├── Engineering/           # code, tests, fixtures, docs
│   ├── README.md
│   ├── Introduction.md    # contributor-facing recruiting doc
│   ├── Docs/              # knowledge tree — four teaching modes (Explain/Guide/Do/Judge)
│   │   ├── README.md      # start-here index; see for the full doc map
│   │   ├── RUBRICS.md, GUIDE-Creating-Rubrics.md, GUIDE-Setting-the-Bar.md
│   │   ├── LESSON-Rubrics-Hands-On.md, CASE-STUDY-Qwen-Triage.md
│   │   ├── CODE-OVERVIEW.md, PROVIDERS.md, RUNBOOK.md
│   │   └── 00-README.md   # symlink to README.md so index sorts first
│   ├── src/instar/        # harness: core/, cli/, policies/, providers/, reporters/, rubrics/
│   ├── fixtures/          # synthetic traffic, catalogs, illustrative rubric
│   └── tests/             # pytest; mock-mode default; `live` marker for real providers
└── Marketing/             # positioning, announcement drafts
    ├── README.md
    └── Measuring-Your-AI-Costs.md  # draft; not for publication yet
```

The Week-1 scaffolding described in `Planning/Project-Plan.md` §10 has completed; Week-2 items are largely done too (harness core is real, a live provider adapter exists, an illustrative rubric ships, a case study on a real self-hosted Qwen run is written up). Remaining before v0.1 tag: full Class-9 trademark clearance and a courtesy note to Prithvi (PyPI name, docs domain, and trademark knockout are all cleared — see `Planning/Naming.md` §Verification status).

---

## Organization principle: by function, with root-level tooling exceptions

Brian's preference is to organize the repo *by function* — `Engineering/`, `Marketing/`, `Planning/`, and additional functional dirs as needs emerge. Two honest exceptions:

1. **Governance/tooling files must live at the repo root**, because GitHub and Python tooling look for them there:
   - `README.md`, `LICENSE`, `.gitignore` (GitHub display + git)
   - `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md` (GitHub renders these as special files with links from the repo sidebar; moving them under `Planning/` would break that)
   - `.github/` for Actions workflows and issue/PR templates (required at root)
   - `pyproject.toml` (Python packaging convention; can technically be elsewhere with configuration, but root is what every tool expects)
   - `CLAUDE.md` (this file, at root by convention for Claude Code)
2. **The Python `src/instar/` tree lives under `Engineering/`.** The `pyproject.toml` at root will reference `Engineering/src/instar` as the package location via `[tool.setuptools.packages.find]` (or the equivalent for whichever build backend we pick). This is slightly non-standard but supported by every modern Python packager; the by-function organization wins.

When something new comes up that doesn't obviously fit, ask: is it *governance/tooling* (root) or *work* (by-function dir)?

---

## Where to look first

1. `README.md` — the public face.
2. `Planning/Project-Plan.md` — the plan. IP boundary, license, sprint, roadmap, contribution model.
3. `Planning/Engagement-Methodology.md` — the 11-phase spine for how Instar fits into a full evaluation engagement. Read to understand where each Instar concept sits in the larger consulting arc.
4. `Engineering/Docs/README.md` — start-here index for the tool docs; a four-mode teaching structure (Explain / Guide / Do / Judge) organizes everything under it.
5. `Planning/Naming.md` — why Instar, what verifications are still owed off-box.
6. `Engineering/README.md` — what lives in Engineering and the src layout note.

---

## Repo relationship: Instar and gateway-lab

There is a **sibling repo** at `github.com/PurpleBlossomAI/gateway-lab` — **Prithvi's** experimental space (testing, paper writing). Instar was created 2026-07-21 as an empty repo rather than renaming gateway-lab, so Prithvi's work wasn't disturbed.

**Posture (decided 2026-07-22):** Instar is the durable home for the Measurement Harness. The harness core was ported here from the gateway experiment on 2026-07-22 (commit `1b250bf`) as a clean-room implementation. MVP1's harness code is to be archived (preserve provenance, don't delete). Prithvi keeps `gateway-lab` for a few more weeks (paper work, in-flight items); after that the repo winds down. See `Planning/Project-Plan.md` §13 for the fuller framing.

**Rules:**
- New harness work goes to Instar. Not MVP1, not gateway-lab.
- Instar does not fork gateway-lab's git history. Any further code lift is clean-room re-implementation.
- Do **not** push commits to gateway-lab from this repo. Prithvi owns it.
- Do **not** conflate them in issues, PRs, or docs. They serve different audiences during the overlap window.
- If code lands in gateway-lab during the overlap window that belongs in Instar, flag it and port here rather than duplicating.

---

## IP boundary — what belongs here vs. what stays private

Instar is Apache 2.0 and adoptable. The *methodology* that makes Instar useful in a consulting engagement is private. Rough split:

| Lives here (Instar OSS) | Lives in private Purple Blossom AI repo (MVP1) |
|---|---|
| Harness core, provider adapters, routing policy interface, rubric framework, reporters | Per-department rubrics (Marketing brand voice, Finance extraction, Ops p95 latency, etc.) |
| Public synthetic fixtures + docs on building your own | Customer-derived workload fixtures |
| 2–3 reference routing policies (rules + cost/quality classifier) | Customer-specific routing recommendation logic |
| Reproducibility scaffolding (mock mode, seeded runs, model-ID pinning, CI) | Cost-experiment methodology write-ups, SOW templates, engagement playbooks |

Enforcement:
- Every PR reviewer holds this line. If a PR would leak methodology or customer-derived fixtures, it doesn't merge.
- CONTRIBUTING.md (when written) will spell this out in words a contributor will read.
- If in doubt: default to *not* in Instar; propose it in MVP1's `Docs/Experiments/Harness-Service+Product/` instead.

Details: `Planning/Project-Plan.md` §IP boundary.

---

## Git workflow

Standard feature-branch flow:

1. **Never commit directly to `main`. Never make code changes while on `main`.** If you land here on `main`, create a branch first.
2. Branch naming: `<short-topic>-NN` (e.g., `readme-rewrite-01`, `governance-files-01`).
3. Commit → push → merge to main → delete branch is the normal default on a dev box. Only pause for a PR review if a change is risky (cross-cutting, security-sensitive, infra).
4. Every commit message ends with the standard `Co-Authored-By: Claude ...` trailer.

---

## How Brian likes to work

Compact context so you don't relearn it. Detail lives in MVP1's memory system (`/home/fromme/.claude/projects/-home-fromme-projects-PurpleBlossomAI-MVP1/memory/`); this is a portable subset.

- **Distillation first, depth second.** Lead every non-trivial doc with a one-paragraph or card-shaped TL;DR. Layers below it are fine.
- **Surface gaps in the framing.** On planning/scope tasks, name what's missing from Brian's list, not just sort what he gave you.
- **Pair-author, don't assign homework.** Co-work on artifacts in one session. Don't structure plans as "your part / my part" that reads as homework.
- **Offload cognitive load by proposing scope cuts.** Solo founder is plate-full — actively propose deferrals, don't just execute faster.
- **Push back on naming/framing that contradicts his own point.** Placeholder names or framings that encode what he just said the product *isn't* should be challenged with alternatives.
- **Security/audit and review findings — three buckets:** resolve-now / file-as-backlog / accept-as-residual. Bounded certainty is the goal.
- **Careful actions.** For hard-to-reverse or shared-state actions (force pushes, repo renames, deleting branches, publishing to PyPI, public announcements), pause and confirm. Never assume prior authorization carries.
- **Brian is a creator/dreamer.** Creative breakouts mid-tactical work are fuel, not distraction — honor them, then return cleanly.

---

## What NOT to do here

- Do not push to `PurpleBlossomAI/gateway-lab` (Prithvi's repo).
- Do not create files in this repo that belong in MVP1 (customer engagements, private rubrics, MVP1 backlog tickets, etc.).
- Do not publish a *released* version to PyPI (`0.1.0`+), tag a release, or make public announcements until the remaining items in `Planning/Naming.md` §Verification status are complete (full Class-9 trademark clearance + Prithvi notified). A `0.0.0` name-holding placeholder is explicitly allowed and expected (see `Planning/Naming.md` §Verification status #1). The repo being public does not lift this hold.
- Do not delete or restructure `Engineering/src/instar/` without checking `Planning/Project-Plan.md` first — the layout is deliberate.
- Do not add a docs domain, blog, Discord, or telemetry before v0.2. Explicitly deferred (see Project-Plan §Documentation surface).
- Do not add trailing summaries to end-of-turn responses. State what changed and what's next, briefly.

---

## Provenance and full historical context

The design conversation that led to this repo lives in the MVP1 sibling repo at `~/projects/PurpleBlossomAI/MVP1/Docs/Experiments/Harness-Service+Product/`. Read those docs (Harness-Service+Product-Discussion.md, OSS-Project-Plan.md, Project-Name-Candidates.md) if you need the full "why" beyond what's captured here and in `Planning/`.

That MVP1 tree is authoritative for *strategy and market context*; this repo is authoritative for *the OSS project itself*.
