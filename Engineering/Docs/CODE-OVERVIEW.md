# Instar — Code Overview

> **TL;DR:** Instar replays a captured workload (a JSONL file of `TrafficSample` rows) against candidate models through a routing policy, prices every call, scores what the cheaper model gave up, and writes an auditable report. Four abstractions carry the whole design — `TrafficSample`, `Backend`, `RoutingPolicy`, `Judge` — and each is one small ABC you can subclass. The core is stdlib-only; provider SDKs are optional extras imported lazily. Everything defaults to a hermetic mock mode that costs nothing and is byte-for-byte reproducible.

Audience: a new contributor. For "how do I actually run this", see [`RUNBOOK.md`](./RUNBOOK.md).

---

## What Instar is

Instar is an open-source (Apache-2.0) measurement harness. It runs an organization's **own** workloads through candidate models, providers, and routing policies and produces defensible cost, quality, and latency numbers — so a decision about which model mix to use for which team rests on evidence from that team's traffic rather than on a vendor benchmark measured on somebody else's. The target user is measuring a department's workload: the marketing team's campaign work, the sales team's enrichment pipeline, the support team's triage queue.

Instar **measures**. It does not route production requests, it is not a general eval platform, it is not an observability or trace store, and it is not a hosted service. A `RoutingPolicy` in this codebase is a hypothesis being tested offline against recorded traffic, not a component anyone deploys in front of live users.

---

## Mental model

One fixture, one catalog, one runner, three pluggable seams, one report directory.

```
  workload fixture (.jsonl)         feature catalog (.json)
  one TrafficSample per line        feature -> foreground | background
            |                                   |
            +------------------+----------------+
                               |
                               v
                        +--------------+           +--------------+
                        |    runner    | --------> |   reporter   |
                        |  route.py    |           |  markdown.py |
                        |  gateway.py  |           +--------------+
                        +--------------+                   |
                               |                           v
        +----------------------+----------------+   runs/<label>/
        |                      |                |     result.json
        v                      v                v     report.md
  +--------------+      +-------------+   +-----------+ sweep.csv
  | RoutingPolicy|      |   Backend   |   |   Judge   |  (sweeps only)
  |  policies/   |      | providers/  |   | rubrics/  |
  +--------------+      +-------------+   +-----------+
   strong or weak?      run the call      what did the
                        (mock or live)    cheap model
                                          give up? 0.0-1.0
```

The `route` runner's per-sample loop, in order:

1. `policy.decide(sample)` returns `strong` or `weak`, with an auditable `reason`.
2. The **strong** backend always runs. Its cost is this call's all-strong baseline. This is why a run always knows what it would have cost to do nothing.
3. If the decision was `weak`, the weak backend runs too, and `judge.score(sample, strong, weak)` returns a quality in `[0, 1]`. If the decision was `strong`, quality is `1.0` by definition — nothing changed.
4. A `SampleOutcome` row is appended: id, feature, category, target, reason, baseline cost, routed cost, quality, rationale.
5. Failed calls are excluded from the aggregates but counted in `error_count`, and the CLI exits `1`.

The `gateway` runner is simpler: it replays the same workload through two backends, interleaved `A, B, A, B, ...`, and compares p50/p95/p99 wall-clock latency. Interleaving is deliberate — sequential blocks would attribute any drift in network conditions to whichever arm ran second, which is exactly the difference being measured.

---

## Module tour

All paths are relative to `Engineering/src/instar/`.

| Module | What it holds | Why it exists |
|---|---|---|
| `core/traffic.py` | `TrafficSample`, `load_traffic`, `save_traffic` | The replay format. A JSONL fixture is one *workload* — the trace of AI calls a real workflow makes, not a single prompt. PII-free by construction: no tenant id, no user id, no raw-customer-content field. |
| `core/catalog.py` | `FeatureCatalog`, `FOREGROUND`, `BACKGROUND` | Your feature-key to category map. The one thing a harness cannot guess: only you know which features a person is actually waiting on. |
| `core/cost.py` | `PRICING`, `call_cost_usd`, `cached_call_cost_usd`, `unpriced_models`, `CostSummary`, `breakeven_requests_per_month` | Per-token cost math. The shipped pricing table is a **placeholder**. Prompt-caching and self-hosting break-even math live here as pure functions. |
| `core/route.py` | `run_route`, `run_sweep`, `RouteResult`, `SampleOutcome`, `SweepPoint` | The routing runner. Produces percent of spend saved versus the all-strong baseline, plus quality reported both overall and over the routed-weak subset. |
| `core/gateway.py` | `run_gateway`, `GatewayResult`, `LatencyStats`, `percentile`, `summarize` | The A/B latency runner. Compares whatever two `Backend`s you point at it. |
| `providers/base.py` | `Backend` ABC, `CompletionResult`, `estimate_tokens`, `sample_text` | The seam between the harness and the outside world. The measurement code never imports a vendor SDK. |
| `providers/mock.py` | `MockBackend` | Deterministic synthetic completions, a pure function of `(sample.id, model)`. |
| `providers/anthropic.py` | `AnthropicBackend` | Live Anthropic calls. Needs the optional `anthropic` SDK, imported lazily inside `_get_client`. |
| `providers/openai_compat.py` | `OpenAICompatBackend` | `POST {base_url}/chat/completions` over stdlib `urllib` — no SDK at all. One class covers vLLM, TGI, Ollama, LiteLLM proxy, OpenRouter, and OpenAI. |
| `policies/base.py` | `RoutingPolicy` ABC, `RoutingDecision`, `STRONG`, `WEAK` | Strong-vs-weak per call. `STRONG`/`WEAK` are relative roles, not tiers. |
| `policies/rules.py` | `AllStrongPolicy`, `FeatureCategoryPolicy` | The control, and the cheapest defensible real policy. |
| `policies/classifier.py` | `ClassifierPolicy`, `heuristic_difficulty`, `HARD_CUES` | Per-prompt difficulty routing and the threshold sweep. `heuristic_difficulty` is a **stand-in**, not a trained router. |
| `policies/__init__.py` | `build_policy`, `POLICY_NAMES` | Name-to-policy construction, used by the CLI. |
| `rubrics/base.py` | `Judge` ABC, `JudgeResult` | Relative scoring: not "is this output good?" but "what did we lose by routing this call to the cheap model?" |
| `rubrics/judges.py` | `MockJudge`, `LabelMatchJudge`, `LLMJudge`, `AutoJudge` | Deterministic, objective, model-based, and dispatching. |
| `rubrics/criteria.py` | `CriteriaJudge`, `CriteriaSet`, `MockCriteriaBackend` | Absolute scoring: one answer against a per-feature checklist, fraction met, critical criteria gate to 0. |
| `rubrics/human.py` | `HumanJudge`, `write_grading_sheet`, `load_grades` | A person as the judge: blind CSV export from a transcript, grades read back and scored through `rejudge`. |
| `reporters/markdown.py` | `report_route`, `report_sweep`, `report_gateway` | Writes `<runs-dir>/<label>/`. Every report carries its own caveats on its face. |
| `cli/main.py` | `build_parser`, `main`, `_cmd_route`, `_cmd_gateway` | `instar route` and `instar gateway`. Mock is the default; `--live` opts in. |

---

## The four core abstractions

### 1. `TrafficSample` — one captured LLM call

```python
@dataclass
class TrafficSample:
    id: str
    feature: str                       # your own dotted key; Instar attaches no meaning
    messages: list[dict[str, Any]]
    max_tokens: int = 1024
    system: str | None = None
    temperature: float | None = None
    category: str | None = None        # optional per-sample override
    meta: dict[str, Any] = field(default_factory=dict)
```

`category`, if set, must be `"foreground"` or `"background"`; anything else raises at construction. `meta` is free-form and non-PII; the recognized keys are `gold` (correct label, for objective scoring), `cadence`, `static_prefix_tokens`, and `warm`.

**Extending it:** don't subclass. Put workload-specific information in `meta` and read it in your own policy, judge, or scorer. Adding a top-level field means changing `from_json`/`to_json` and every fixture in the repo, and risks reintroducing the PII surface the format deliberately does not have.

### 2. `Backend` — turn a sample into a completion

```python
class Backend(ABC):
    name: str = "abstract"

    @abstractmethod
    def complete(self, sample: TrafficSample, model: str) -> CompletionResult: ...
```

`CompletionResult` carries `text`, `model`, `input_tokens`, `output_tokens`, `latency_s`, `ok`, `error`.

**Extending it:** subclass `Backend`, implement `complete`, set `name` (it labels your arm in reports). The one hard rule: **never raise for a provider error.** Return `CompletionResult.failure(model, "<reason>")` instead, so one dead call cannot abort a long run. Report the provider's own token counts; do not estimate. `estimate_tokens` exists for mock mode only.

Before writing a new backend, check whether `OpenAICompatBackend` already covers your target — most self-hosted servers and gateways speak the chat-completions dialect, and pointing that class at a different URL costs nothing.

### 3. `RoutingPolicy` — strong model or weak model?

```python
class RoutingPolicy(ABC):
    name: str = "abstract"

    @abstractmethod
    def decide(self, sample: TrafficSample) -> RoutingDecision: ...
```

`RoutingDecision(target, reason, score=None)`. The `reason` string lands in the per-sample report row, so a stakeholder can audit an individual decision instead of trusting an aggregate. Write reasons a non-engineer can read.

**Extending it:** subclass and implement `decide`. If your policy is threshold-shaped, it can be swept — pass a `policy_factory` (a callable taking a threshold and returning a policy) to `run_sweep`. To expose it on the CLI you also need to add it to `POLICY_NAMES` and `build_policy` in `policies/__init__.py`.

The cheaper extension point, if you only want smarter scoring rather than different mechanics, is `ClassifierPolicy(scorer=...)`: any `Callable[[TrafficSample], float]` returning `[0, 1]`. Replacing `heuristic_difficulty` with a trained classifier is the honest place to spend effort — the shipped heuristic (prompt length + a handful of cue words + category) exists so the harness runs end to end out of the box, and it is not a measurement of difficulty.

### 4. `Judge` — what did the cheap model give up?

```python
class Judge(ABC):
    name: str = "abstract"

    @abstractmethod
    def score(self, sample: TrafficSample,
              strong: CompletionResult,
              weak: CompletionResult) -> JudgeResult: ...
```

`JudgeResult(score, rationale)`, score in `[0, 1]` where `1.0` means the weak output is as good as the strong one for this call. Scoring is **relative** on purpose: a cost study has to answer "what do we lose by routing this call to the cheap model?", and only a paired comparison answers that when the workload has no ground truth.

Shipped judges:

| Judge | Use when | Notes |
|---|---|---|
| `LabelMatchJudge` | You have gold labels (classification, extraction with a known answer) | Exact string match, longest-label-first so `account_access` is not shadowed by `access`. Falls back to the label found in the strong output when `meta["gold"]` is absent. An unrecognizable weak answer scores `0.0` — a refusal or an invented category is a real routing failure, not a tie. |
| `LLMJudge` | Open-ended generation with no ground truth | Three rungs: `PASS` 1.0, `MARGINAL` 0.5, `FAIL` 0.0. |
| `AutoJudge` | Mixed workloads | Samples with `meta["gold"]` go to the label judge, everything else to the LLM judge. |
| `MockJudge` | Hermetic runs | Deterministic; never reads the completion text; measures nothing. |
| `CriteriaJudge` | No trustworthy baseline, or the thing under test isn't a model swap | **Absolute**: scores each answer against written criteria (YES/NO each); score = fraction met, 0.0 on a critical miss. The baseline is scored too. A sample with no criteria is unscored. See [`GUIDE-Criteria-Judge.md`](GUIDE-Criteria-Judge.md). |
| `HumanJudge` | Validating a model judge; any time a person should decide | Reads a filled `instar grade-sheet` CSV. Same PASS/MARGINAL/FAIL rungs. Ungraded rows **abstain** (`Judge.abstains`), so they are unscored rather than passed. See [`GUIDE-Human-Grading.md`](GUIDE-Human-Grading.md). |

**Prefer objective scoring.** If your workload permits a label match, use it: it is exact, free, instant, and — unlike an LLM judge — is not itself a model whose judgment you would then have to validate. An LLM judge is a measurement instrument with its own error; a cost study resting on an unvalidated one has moved the uncertainty rather than removed it. Validate `LLMJudge` against hand-graded samples before trusting a number from it.

The `MARGINAL` rung is the one that earns its keep. A cheap answer that triggers a user retry is not a saving — the user pays again, in a second call and in their own patience. Collapsing `MARGINAL` into `PASS` is how a routing change looks free on a spreadsheet and costs money in production.

**Extending it:** subclass and implement `score`. Always populate `rationale`; it is written into the report, and a score nobody can audit is a number nobody should act on.

---

## The fixture format

A workload fixture is a `.jsonl` file: one `TrafficSample` per line, blank lines ignored. `load_traffic` raises with the file and line number on a malformed row, and rejects an empty file.

```json
{"id": "sup-001", "feature": "support.ticket_classification", "system": "Classify the ticket into exactly one of: billing, bug_report, how_to. Reply with only the label.", "messages": [{"role": "user", "content": "The nightly export produced a zero-byte file three days running."}], "max_tokens": 8, "temperature": 0.0, "meta": {"synthetic": true, "gold": "bug_report"}}
```

Only `id`, `feature`, and `messages` are required; everything else has a default. `id` must be unique within a fixture — it seeds mock determinism and identifies the row in the report.

Fixtures committed to this repo must be synthetic and PII-free. `Engineering/tests/test_fixtures.py` enforces that in CI: every sample carries `meta.synthetic == true`, no email addresses appear, and a forbidden-terms list catches private product names. When capturing from a real service, redact prompts to synthetic stand-ins **before** writing the fixture.

## The catalog format

A *feature* is one named AI touchpoint. A *category* says how much quality it can afford to lose: `foreground` means a person is waiting on the output and will judge it; `background` means it runs on a schedule or behind the scenes.

```json
{
  "default": "foreground",
  "categories": {
    "support.ticket_classification": "background",
    "support.queue_digest": "background",
    "support.macro_draft": "foreground"
  }
}
```

Resolution order for a sample's category:

1. the sample's own `category` field, if the fixture declares one;
2. the catalog's `categories` map, keyed by `feature`;
3. the catalog's `default` (`foreground` unless you say otherwise).

Defaulting to `foreground` is deliberate: an uncatalogued feature is treated as quality-sensitive, so forgetting to catalog something costs you money, never quality. Because that failure is silent by nature, the CLI prints a note naming every feature that fell through to the default.

The catalog is **your config, not ours**. Instar ships two examples under `Engineering/fixtures/catalogs/`, but it will never guess which of your features a user watches.

---

## Mock mode versus live mode

Mock is the default. `--live` opts in.

| | Mock (default) | Live (`--live`) |
|---|---|---|
| Backends | `MockBackend` for both arms | `AnthropicBackend` for `route`; `OpenAICompatBackend` for `gateway` arms |
| Judge | `MockJudge` | `AutoJudge` = `LabelMatchJudge` where a gold label exists, `LLMJudge` otherwise |
| Models | Forced to `mock-strong` / `mock-weak`; `--strong-model` and `--weak-model` are ignored | `--strong-model` / `--weak-model`, defaulting to undated ids |
| Token counts | `estimate_tokens`, ~4 chars per token | The provider's own reported counts |
| Network, keys, spend | None | Real |
| Reproducibility | Byte-identical across runs | Not reproducible; sampling and provider drift |
| Report banner | `MOCK RUN - not a measurement` | No banner |

Mock mode is not a toy. It is how you read the data flow, write a fixture, wire a policy, and get a green CI run without holding an API key. But its numbers are deterministic placeholders. Every report says so on its face, and it is worth repeating in review: **a mock number is never a measurement.**

---

## Conventions and safety rules

- **Stdlib-only core.** `pyproject.toml` declares no runtime dependencies. Mock mode runs anywhere, and CI never breaks on a provider SDK release. `anthropic` is an optional extra imported lazily inside the module that needs it. Do not add a runtime dependency without a very good reason, and never at import time in `core/`.
- **Undated model ids, on purpose.** Dated snapshot ids are a recurring source of 404s as snapshots are retired. A run pinned slightly loosely beats a run that dies halfway through.
- **Failures are loud, never a silent $0.** Backends return `CompletionResult.failure(...)` rather than raising. The runner excludes failed calls from aggregates, counts them in `error_count`, sets `trustworthy` to `False`, prints the count on stderr, and exits `1`.
- **Unpriced models warn.** `call_cost_usd` returns `0.0` for a model with no pricing row, which would silently understate a run — so every run calls `unpriced_models` and attaches a warning that reaches both the console and the report.
- **Pricing is placeholder.** `PRICING` in `core/cost.py` is sized to be directionally sane so mock runs produce believable curves. Verify it, or supply `--pricing`, before any figure leaves your laptop.
- **Caveats travel with the numbers.** Reports get pasted into decks and forwarded to people who never ran the tool. Mock banners, failure counts, and pricing caveats go *above* the results table, never in a footnote.
- **Every decision is auditable.** Policies carry a `reason`, judges carry a `rationale`, and both land in the per-call table in `report.md`.
- **Small-n honesty.** `run_gateway` warns below 30 calls per arm: p99 of ten calls is just the slowest call. Latency is wall-clock from the client, so an arm across the internet versus one on localhost measures geography, not software.
- **Tests are hermetic.** No test touches the network. The Anthropic backend takes an injected fake client; `OpenAICompatBackend` has `urllib.request.urlopen` monkeypatched. Live-provider tests sit behind the `live` pytest marker and are skipped in CI.
- **Every `.py` file carries `# SPDX-License-Identifier: Apache-2.0`.** CI fails without it.
- **CI gates:** `ruff check .`, `ruff format --check .`, `mypy` (strict, on `Engineering/src/instar`), and `pytest -m "not live"` on Python 3.11, 3.12, and 3.13.
- **Fixtures stay public-safe.** Synthetic, no PII, no private product names. `test_fixtures.py` enforces this so the IP boundary lives in CI rather than in a reviewer's memory.

---

## Where to start reading

In this order, with the module docstrings — they carry the reasoning, not just the API:

1. **`core/traffic.py`** — the data everything else operates on. Small file, sets the tone.
2. **`core/catalog.py`** — the foreground/background split and its resolution order. This is the idea the simplest real policy is built on.
3. **`core/route.py`** — the runner. Read `run_route` top to bottom; it is the spine of the project and shows exactly where the three seams plug in.
4. **`policies/rules.py`** — two policies in fifty lines, including the control you measure everything against.
5. **`rubrics/judges.py`** — the four judges and, in the class docstrings, the argument for objective scoring over an LLM judge.
6. **`cli/main.py`** — how the pieces are wired for a real invocation, including the mock/live split.
7. **`Engineering/tests/`** — the test names are written as sentences and double as a specification. `test_route.py` and `test_cli.py` are the best two to read.

Then run everything in [`RUNBOOK.md`](./RUNBOOK.md) against the shipped fixtures. Mock mode costs nothing, so there is no reason to read without running.
