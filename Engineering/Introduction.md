# Introducing Instar — and how to get involved

> **TL;DR:** Instar is a new Apache-2.0 harness for measuring LLM workloads on your own traffic — cost, quality, and latency across candidate models, providers, and routers. Repo is two days old but already running: harness core, CLI, provider adapters, rubric framework, and a substantial docs tree are all live on `main`, with a case study from a real self-hosted Qwen run to show the shape of the thing. Looking for a few genuine collaborators — one PR, one design conversation, or "I'll be an early user and file real bug reports" all welcome. Reach out to Brian if you're curious.

**Repo:** [github.com/PurpleGardens/instar](https://github.com/PurpleGardens/instar)
**Status:** pre-v0.1 (harness core + docs landed 2026-07-22; v0.1 tag targeted early August 2026)
**License:** Apache 2.0

---

## What Instar is

Instar runs your own LLM workloads through candidate models, providers, and routing policies, and produces a defensible comparison on cost, quality (scored via LLM-judge rubrics), and latency. The output is evidence, not opinion — a report a stakeholder can read, and that will still be reproducible in six months.

It is deliberately *not*:

- A router (production routing is decided by OpenRouter, LiteLLM, Portkey, Kong, Cloudflare AI Gateway — a crowded space).
- A general eval platform (Promptfoo, Braintrust, DeepEval win on breadth).
- An observability/trace store (Langfuse, Helicone, Arize, LangSmith own that).
- A hosted service. No SaaS. You run it.

## Why this project

Every organization deploying LLMs at any scale is making a router / model / policy decision. Most of them are guessing — reading vendor benchmarks (measured on someone else's workload), listening to Twitter, deferring to whoever built the first prototype. At millions of requests a month, a mediocre routing choice is real six-figure money and material quality regressions, and the decision-making inputs haven't scaled with the stakes.

The tools to change this exist in pieces (eval platforms, observability, routers) but the *measurement harness* — the thing that runs your real workloads through candidate configurations and produces a defensible comparison — is where the field is thinnest. Instar aims to be that harness. Small, well-scoped, opinionated.

## Why Instar specifically, not one of the eval platforms

The eval platforms are optimized for prompt-level comparison — "does prompt A beat prompt B on this benchmark." Instar is optimized for **workload-level questions across a router + model + policy space**: given your real traffic distribution, what's the cost / quality / latency Pareto frontier, and which routing policy realizes it? Different question, different shape of tool.

It's also **reproducibility-first** by design — mock mode, seeded runs, pinned model IDs, hermetic fixtures — so a report generated today can be re-run and re-verified quarters later. Most eval tooling optimizes for interactive exploration; Instar optimizes for defensible artifacts.

## Who's building it

Instar is built by folks at Atelier, Purple Blossom AI's consulting practice; Atelier uses it in engagements, which is where the roadmap pressure comes from. The tool is Apache 2.0 so any firm — including competing consultancies — can adopt the same motion.

Brian Fromme is day-to-day maintainer through v0.1. Prithvi Devraj continues to run a parallel experimental space at [github.com/PurpleBlossomAI/gateway-lab](https://github.com/PurpleBlossomAI/gateway-lab).

## Current state (2026-07-22)

Landed on `main`, CI-green:

- Governance and packaging: license, README, plan, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`, `pyproject.toml`, CI (`ruff`, `mypy --strict`, `pytest` on Python 3.11 / 3.12 / 3.13, SPDX-header check).
- The harness itself: `Engineering/src/instar/` with core (traffic, catalog, gateway, route, cost), the `instar` CLI, provider adapters (Anthropic, OpenAI-compatible, mock), routing policies (rules + cost/quality classifier), a rubric framework with spec loader and LLM-judge scaffolding, and a Markdown reporter.
- Fixtures under `Engineering/fixtures/` — synthetic traffic samples, catalogs, and an illustrative rubric spec.
- A substantial `Engineering/Docs/` knowledge tree with a stated four-mode structure (Explain / Guide / Do / Judge): a rubric reference, a step-by-step rubric-writing guide, a hands-on ten-minute lesson, a case study from a live self-hosted Qwen run, a runbook, a providers guide, and a code overview.
- `Planning/Engagement-Methodology.md` — the eleven-phase spine that places every Instar concept inside a full evaluation engagement.

Still ahead:

- Formal v0.1.0 tag, gated on off-box verifications in `Planning/Naming.md` (PyPI name, domain reservations, trademark quick-pass).
- Additional provider adapters (Google, vLLM native, OpenRouter, LiteLLM) as demand pulls them in.
- MkDocs Material scaffold if we choose to publish the docs tree as a hosted site.
- Customer-facing `Engineering/docs/How-Instar-Fits-an-Evaluation.md` walking through a canonical engagement using the phase vocabulary.

## What you'd actually do

Any of these is a reasonable place to enter — pick what sounds fun:

- **Provider adapters.** Small, well-scoped modules for Anthropic, OpenAI, Google, vLLM/self-hosted, OpenRouter, LiteLLM. High-leverage, easy to review, good first PR territory.
- **Reference routing policies.** A rules-based baseline and a cost/quality classifier. Interesting design space, real modeling work.
- **Rubric framework.** The internals of "how do we score responses with an LLM judge, reproducibly?" — a real engineering + reasoning problem.
- **Reporters.** JSON, Markdown, CSV, HTML. Some polish work, some real UX thinking about what to show an exec vs. an engineer.
- **CLI + ergonomics.** Making `instar run` feel obvious the first time you use it.
- **Docs + examples.** Getting-started, a first tutorial, an honest FAQ.
- **Early-user / reviewer role.** Run Instar against a workload you know and tell us where it fails you. This may be the most valuable thing at v0.1 — we don't have external usage yet.

## What we're not asking

This isn't a job posting. Nothing here is paid. We're not asking anyone to be full-time, exclusive, or to bring customers, and we're not asking for referrals — we want to earn attention with the tool itself.

We *are* asking for a genuine collaborator: someone who cares about the problem, who'd send a PR because they'd get something back from thinking about it. If that's the wrong shape for you right now, no hard feelings — bookmark the repo and check in when v0.2 lands.

## What you'd get out of it

- Design influence on a small, well-scoped OSS project at the moment it's shaping up.
- Attribution as a contributor on a repo that gets used in real client engagements.
- Practical experience with a slice of the LLM operations problem — routing, cost accounting, LLM-as-judge — that's underexplored in public tooling.
- Whatever we learn from the customer side of the work, aggregated and shared (never confidential).

## How to get involved

1. Skim the [README](../README.md) and the [Project Plan](../Planning/Project-Plan.md).
2. Reply to Brian's intro message so we know you're interested. Say what caught you.
3. If you want to dive in: watch the `v1 Roadmap` project board (populating this week), pick a `good-first-issue`, or open a PR that scratches something specific.
4. Or just tell Brian "here's what I'd want to work on" and we'll shape a small first thing together.

Questions, pushback, or "this isn't for me but you should talk to X" all welcome. Reach Brian at brianfromme@gmail.com.
