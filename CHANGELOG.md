# Changelog

All notable changes to Instar are documented here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Instar is in **v0.x** — the API surface is unstable until v1.0.0. Breaking changes may land in any 0.y release; we will call them out in the corresponding `### Changed` or `### Removed` section.

## [Unreleased]

### Added

- `Engineering/Docs/COMPARISON-smevals.md` — where Instar sits relative to [smevals](https://github.com/prime-radiant-inc/smevals). smevals answers the capability question ("how good is model X at task Y?"); Instar answers the workload-economics question ("does this routing change pay off on my traffic?"). Documents the two-phase composition (candidate discovery in smevals, economic decision in Instar), an honest list of what smevals does better, and why cost tracking is an input-contract change rather than a feature. Three borrow items are named there and tracked for v0.2: a static HTML report, an `instar docs` command for coding agents, and an `--exec` runner backend.
- **The harness core.** Clean-room port of the internal gateway experiment code into `Engineering/src/instar/`, restructured to the package layout in `Planning/Project-Plan.md` §4:
  - `core/` — `TrafficSample` and JSONL workload fixtures; `FeatureCatalog`; cost math (per-token pricing, prompt-caching model, self-hosting break-even); the routing runner (`run_route`, `run_sweep`); the gateway A/B latency runner (`run_gateway`).
  - `providers/` — `Backend` interface and `CompletionResult`; `MockBackend` (deterministic), `AnthropicBackend` (optional SDK, lazily imported), and `OpenAICompatBackend`.
  - `policies/` — `AllStrongPolicy` (control), `FeatureCategoryPolicy` (rules baseline), `ClassifierPolicy` with a placeholder difficulty scorer and a documented seam for a trained router.
  - `rubrics/` — `LabelMatchJudge` (objective, gold-label), `LLMJudge` (PASS/MARGINAL/FAIL), `AutoJudge` (dispatches per sample), `MockJudge` (deterministic).
  - `reporters/` — `result.json`, Markdown report with per-call decision rows, and `sweep.csv` for charting.
  - `cli/` — `instar route` and `instar gateway`, both defaulting to hermetic mock mode.
- `OpenAICompatBackend` is implemented over `urllib` rather than an SDK, so vLLM, Ollama, LiteLLM, OpenRouter, and OpenAI are all reachable from a stdlib-only install.
- Synthetic, PII-free workload fixtures under `Engineering/fixtures/`: support triage (24 calls, gold labels, objectively scorable), marketing content ops (12, mixed), sales pipeline (10, including a prompt-caching refinement series), and a small mixed smoke sample. Example feature catalogs under `Engineering/fixtures/catalogs/`.
- 203 tests across 10 files, all hermetic. Includes a check that no private product terms leak into the shipped fixtures, enforcing the IP boundary in CI rather than in a reviewer's memory.
- `instar` console-script entry point and an `anthropic` optional-dependency extra.
- `Engineering/Docs/CODE-OVERVIEW.md` (architecture and extension points) and `Engineering/Docs/RUNBOOK.md` (task-oriented, including a walkthrough for measuring your own workload).
- `--strong-url` / `--weak-url` (with matching `--*-key-env`) on `instar route`, so either arm can be any OpenAI-compatible endpoint rather than Anthropic. This is what makes the CLI able to evaluate a self-hosted small model against a frontier baseline. The LLM judge runs on the strong arm.
- **Rubrics** (`instar.rubrics.spec`) — the framework for turning a measurement into a decision. A rubric is JSON: named dimensions, each binding a measured metric to a `pass_at` threshold and optional `marginal_at` band, agreed before the run and executed with `--rubric`. Two deliberate refusals: the overall verdict is the **worst** dimension rather than an average, so a large cost saving cannot cancel out a failing quality score; and a dimension the run could not measure returns `unmeasured`, which is never a pass. A failing verdict exits 1 so CI can refuse to publish a report its own rubric rejected. Closes the gap where `Planning/Engagement-Methodology.md` §B and `Marketing/Measuring-Your-AI-Costs.md` both promised per-dimension rubric scoring that the code did not implement.
- `Engineering/Docs/RUBRICS.md` (reference) and `Engineering/Docs/GUIDE-Creating-Rubrics.md` (a step-by-step guide to deriving dimensions and thresholds from a decision), plus an example rubric under `Engineering/fixtures/rubrics/`. The framework ships; the rubrics do not — per-department thresholds stay with whoever owns the outcome.
- `Engineering/Docs/GUIDE-Setting-the-Bar.md` — a one-page, jargon-free guide for the *decision owner* (not the operator): why the quality bar is a business judgment, how to set it, and the traps. Closes the audience gap where every other doc taught the person who runs Instar and nothing taught the person who signs off on the threshold.
- `Engineering/Docs/README.md` — a documentation index and reading map, so the docs are discoverable instead of scattered. A `00-README.md` symlink points to it so the index also floats to the top of a plain directory listing, while GitHub still renders the real `README.md` as the folder landing page. The index states the organizing principle: the docs are a **knowledge tree** with four teaching modes — Explain (reference), Guide (tutorial/task-guide), Do (lesson), Judge (case study) — and a new doc is placed by choosing its mode.
- `Engineering/Docs/LESSON-Rubrics-Hands-On.md` — a ten-minute, learn-by-doing lesson: run four commands in mock mode, change one threshold, watch the verdict flip from FAIL to PASS, then confront the trap of having moved the bar to match the result. Every quoted output is real and deterministic, so a learner sees exactly what the lesson shows.
- `Engineering/Docs/CASE-STUDY-Qwen-Triage.md` — a narrative case study built from the real Qwen 7B-vs-3B run: the rubric returned FAIL, and reading the two failing rows showed the labels were wrong, not the model. Teaches the judgment no tool automates — a verdict starts an investigation, it doesn't end one — plus the "faster to generate is not faster to respond" finding (1.8x throughput, 1.16x latency, because triage outputs are 2-3 tokens).
- `Engineering/Docs/PROVIDERS.md` — how to connect Instar to a hosted LLM (account, payment, key, base URL) and to a self-hosted SLM (Ollama/vLLM/llama.cpp, sizing, licensing posture), plus the small-model failure modes that look like poor quality but are really prompt or plumbing bugs.
- The route runner now records per-call latency and token counts, so cost arithmetic in a report can be audited rather than taken on faith, and rubrics can bind latency dimensions.
- Repository scaffolding: `Planning/`, `Engineering/`, `Marketing/`, `CLAUDE.md`, initial README stub.
- Governance files: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1), `SECURITY.md`, `CHANGELOG.md`.
- `pyproject.toml` with setuptools build backend, PEP 639 SPDX license fields, and configuration for `ruff` (lint + format), `mypy --strict`, and `pytest`. Package located at `Engineering/src/instar/` per `CLAUDE.md` §Organization principle.
- Smoke test at `Engineering/tests/test_smoke.py` — verifies the package installs and imports.
- SPDX license headers on all Python source files.
- CI baseline at `.github/workflows/ci.yml`: four parallel jobs — `ruff check` + `ruff format --check`, `mypy --strict`, `pytest -m 'not live'` on Python 3.11/3.12/3.13, and an SPDX-header check. Runs on push to `main` and on pull requests.
- `Planning/Engagement-Methodology.md` — first-draft spine for how Instar fits into a complete LLM evaluation engagement. Names eleven phases (A–K), annotates each with its IP-boundary side (OSS / mixed / private methodology), and frames the process as a loop rather than a line. Companion artifact (`Engineering/docs/How-Instar-Fits-an-Evaluation.md`) planned after the Week-2 code lift; Atelier playbook is a separate, private artifact.
- `Engineering/Introduction.md` — first-pass contributor-facing introduction, written for people Brian is inviting to the project.
- `Marketing/Measuring-Your-AI-Costs.md` — first-pass positioning + evaluation-process primer for leaders. Draft; not for publication until v0.1 gate clears (see `Planning/Naming.md`).

### Changed

- `README.md` rewritten from mission stub to v0.1 shape: two-audience "Who it's for," status callout, install-from-source (PyPI deferred to v0.2), anti-scope, and pointers to `Planning/`. Subsequently corrected to document the CLI that actually exists (`instar route` / `instar gateway` over JSONL workloads) in place of an illustrative YAML interface that was never built.
- The feature-to-category map is now **user-supplied configuration** (`FeatureCatalog`, loaded from JSON) rather than a table baked into the source. An adopter measuring their own marketing or support workload declares their own features; the shipped catalogs are examples. Uncatalogued features default to foreground, so an omission costs money rather than quality, and the CLI names them on stderr.
- Gateway comparison arms are generic and user-specified (`--a-url` / `--b-url`), and calls are interleaved between arms rather than run in blocks, so drift in network or provider conditions cannot be attributed to whichever arm ran second.
- Reports carry their caveats inline — mock banner, failed-call count, unpriced-model warning, and small-sample warnings on tail latency percentiles — because these files get forwarded to people who never ran the tool.

### Removed

- The internal-gateway backend and the curated vendor feature-parity matrix from the source experiment. Both encoded private architecture and neither is runnable or useful outside it. Gateway comparison now measures latency only, on endpoints the user names.

### Deprecated

- *nothing yet*

### Fixed

- Bad input now exits with a message rather than a traceback: a malformed fixture line, an invalid catalog, a missing file, and an uninstalled provider SDK all report cleanly and exit 1.
- Reports stated that costs came from the placeholder pricing table "unless you supplied your own", which was misleading in both directions. A report now says which table it actually used, and the placeholder case is a prominent warning rather than a hedge in a footnote.
- The shipped support-triage fixture did not pin its output language. Against an exact-match scorer a multilingual model answering in another language scores 0%, which reads as a model-quality failure rather than the prompt bug it is.

### Security

- *nothing yet*

<!--
Release template — copy this block above [Unreleased] when tagging a release:

## [X.Y.Z] — YYYY-MM-DD

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
-->
