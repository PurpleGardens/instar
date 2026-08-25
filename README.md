# Instar

An open-source harness for measuring LLM workloads — cost, quality, and latency — on your own traffic.

> *"Harness" here is the classic test-rig sense — a measurement wrapper around a system-under-test — not the newer "agent harness" (Claude Code, Cursor) that turns a raw model into an agent runtime.*

> **Status:** pre-release (`0.1.0.dev0`, no tagged release yet). The harness core — CLI, provider adapters, routing policies, rubrics, reporters — is runnable today in mock mode and against live providers; the API surface is still unstable. See [`Planning/Project-Plan.md`](./Planning/Project-Plan.md) for the plan.

## Who it's for

Instar answers one question — *which model mix, which router, for which use case, on my traffic?* — for two kinds of adopter:

- **Teams with in-house engineering** — ML platform, AI enablement, or product-engineering groups — who run Instar against their own workloads directly.
- **Leaders who have the AI question but not the plumbing** — heads of marketing, operations, finance, and other AI-adopting functions — who engage a consulting partner to run it on their behalf. Instar is built by the team at Atelier, Purple Blossom AI's consulting practice, which uses it in that role; the tool is Apache 2.0 and any firm can do the same.

Either path produces the same artifact: evidence grounded in *your* workloads, not vendor benchmarks.

## What it looks like

A *workload* is a JSONL file of captured LLM calls — the trace of AI calls one real workflow makes. Replay it through a routing policy and see what a cheaper model would have cost you, and what it would have cost you in quality:

```bash
$ instar route --traffic Engineering/fixtures/marketing-content-ops.jsonl \
      --catalog Engineering/fixtures/catalogs/example-departments.json
route -> runs/route-feature_category-mock
  policy=feature_category  saved=36.9%  q_all=0.989  q_weak=0.978  weak=6/12
```

Sweep a threshold to draw the cost/quality curve, which is the artifact that actually answers "how much can we save before quality starts to hurt?":

```bash
$ instar route --traffic your-workload.jsonl --catalog your-catalog.json \
      --sweep 0.2,0.4,0.6,0.8
```

Or compare two gateways on per-call latency:

```bash
$ instar gateway --traffic your-workload.jsonl \
      --a-url http://localhost:4000 --a-name litellm \
      --b-url https://api.example.com --b-name direct
```

Every run writes `result.json`, a Markdown report, and (for sweeps) a CSV.

**Runs default to mock mode** — deterministic, no API keys, no network, no spend, green in CI. Mock numbers are placeholders, never a measurement; every report says so on its face. Add `--live` and credentials to hit real providers.

## Install

```bash
# from source (v0.1)
git clone https://github.com/PurpleBlossomAI/instar
cd instar
pip install -e .
```

The harness core is **stdlib-only** — the bare install pulls in nothing. That includes the OpenAI-compatible backend, so vLLM, Ollama, LiteLLM, OpenRouter, and OpenAI are all reachable without an extra dependency. Anthropic's SDK is an optional extra (`pip install -e ".[anthropic]"`).

PyPI publication is deferred to v0.2 (see [`Planning/Project-Plan.md`](./Planning/Project-Plan.md) §14) — `pip install instar` requires committing to a stable API surface we're not ready to promise at v0.1.

Python 3.11+.

## Where to go next

- [`Engineering/Docs/RUNBOOK.md`](./Engineering/Docs/RUNBOOK.md) — how to actually run it, including a walkthrough for measuring **your own** workload.
- [`Engineering/Docs/PROVIDERS.md`](./Engineering/Docs/PROVIDERS.md) — connecting Instar to a hosted LLM or a self-hosted small model.
- [`Engineering/Docs/GUIDE-Creating-Rubrics.md`](./Engineering/Docs/GUIDE-Creating-Rubrics.md) — a step-by-step guide to writing a rubric that decides your AI-spend question.
- [`Engineering/Docs/RUBRICS.md`](./Engineering/Docs/RUBRICS.md) — the rubric reference: every field, metric, and verdict rule.
- [`Engineering/Docs/CODE-OVERVIEW.md`](./Engineering/Docs/CODE-OVERVIEW.md) — orientation for contributors: architecture, the four core abstractions, and how to extend each.
- [`Planning/Project-Plan.md`](./Planning/Project-Plan.md) — the plan: IP boundary, roadmap, sprint, open questions.
- [`Engineering/Docs/COMPARISON-smevals.md`](./Engineering/Docs/COMPARISON-smevals.md) — how Instar composes with [smevals](https://github.com/prime-radiant-inc/smevals), and which questions each tool is the right one for.
- [`Planning/Naming.md`](./Planning/Naming.md) — why "Instar," what off-box verifications are still owed before public announcement.
- Docs site — planned: GitHub Pages under `purpleblossomai.github.io/instar/`, later `instar-dev.org`.

## What Instar is NOT

- **Not a router.** Production routing is the job of OpenRouter, LiteLLM, Portkey, Kong, and Cloudflare AI Gateway. Instar tells you which routing choices *would* have paid off on your traffic.
- **Not a general eval platform.** Promptfoo, Braintrust, and DeepEval already win on breadth.
- **Not an observability store.** Langfuse, Helicone, Arize, and LangSmith already own trace storage.
- **Not a hosted service.** No SaaS, no dashboard-as-a-product.
- **Not a container for consulting IP.** Per-department rubrics and customer-derived fixtures live in their owners' private repos, not here.

Full anti-scope in [`Planning/Project-Plan.md`](./Planning/Project-Plan.md) §1.

## License, contributing, security

- **License:** Apache 2.0. See [`LICENSE`](./LICENSE).
- **Contributing:** see [`CONTRIBUTING.md`](./CONTRIBUTING.md). Issues and PRs are welcome; merge rights are limited to Brian and Prithvi through v0.1 (see [`Planning/Project-Plan.md`](./Planning/Project-Plan.md) §6).
- **Security:** see [`SECURITY.md`](./SECURITY.md) — please report privately rather than opening a public issue.

## Related

[`github.com/PurpleBlossomAI/gateway-lab`](https://github.com/PurpleBlossomAI/gateway-lab) — Prithvi's parallel experimental space. Not the shipped artifact.
