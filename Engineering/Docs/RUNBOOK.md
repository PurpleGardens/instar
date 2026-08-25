# Instar — Runbook

> **TL;DR:** `pip install -e ".[dev]"`, then `instar route` — that is a complete hermetic run against a shipped fixture, no API key, no network, no spend. To measure your own workload: capture it into a JSONL fixture, write a feature catalog, run `--policy all_strong` for the control, run `--policy feature_category` against it, and read the **routed-weak** quality column. Only go `--live` once the mock run is green, and start with fifty calls, not five thousand.

Task-oriented. For the architecture and how to extend it, see [`CODE-OVERVIEW.md`](./CODE-OVERVIEW.md).

Every command below was run against this repository. Output is real, trimmed only where noted.

---

## Contents

1. [Install](#1-install)
2. [Your first run](#2-your-first-run-mock)
3. [Run the control, then a policy](#3-run-the-control-then-a-policy)
4. [Sweep a threshold and read the curve](#4-sweep-a-threshold-and-read-the-curve)
5. [Measure your own workload](#5-measure-your-own-workload)
6. [Score a classification workload with gold labels](#6-score-a-classification-workload-with-gold-labels)
7. [Going live](#7-going-live)
8. [Compare two gateways](#8-compare-two-gateways)
8b. [Compare three or more arms](#8b-compare-three-or-more-arms-instar-arms)
9. [Supply your own pricing table](#9-supply-your-own-pricing-table)
10. [Read a report](#10-read-a-report)
11. [Interpreting results honestly](#11-interpreting-results-honestly)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Install

Python 3.11 or newer. From a clone of the repository:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The package source lives at `Engineering/src/instar/`, not the more usual `./src/`. `pyproject.toml` points setuptools there via `[tool.setuptools.package-dir]`, so the editable install works from the repo root and nothing else needs to know.

The harness core has **no runtime dependencies**. `pip install -e .` (without `[dev]`) is enough to run everything in mock mode and everything that talks to an OpenAI-compatible endpoint. The `dev` extra adds pytest, ruff, mypy, pre-commit, and detect-secrets; `anthropic` adds the one optional provider SDK.

Verify:

```bash
$ instar --help
usage: instar [-h] {route,gateway} ...

Measure LLM cost, quality, and latency on your own workloads.

positional arguments:
  {route,gateway}
    route          replay a workload through a routing policy; measure cost vs
                   quality
    gateway        compare two gateways or endpoints on per-call latency
```

Run the test suite (hermetic, no network, no keys):

```bash
$ pytest -m "not live"
198 passed in 0.14s
```

The same three gates CI runs:

```bash
ruff check . && ruff format --check .
mypy
pytest -m "not live"
```

---

## 2. Your first run (mock)

**Mock mode is the default.** There is no `--mock` flag; `--live` is the opt-in. From the repo root, with no arguments at all, `instar route` finds a shipped fixture and runs end to end:

```bash
$ instar route
  note: 7 feature(s) not in the catalog, defaulting to 'foreground': assistant.ask, campaign.copy_generation, content.insight_summary, content.seo_metadata, ops.status_rollup, sales.lead_enrichment, support.ticket_classification
route -> runs/route-feature_category-mock
  policy=feature_category  saved=0.0%  q_all=1.000  q_weak=n/a  weak=0/10
```

Three things happened, and all three are the point:

- It found `Engineering/fixtures/sample-traffic.jsonl` without being told (it searches `sample-traffic.jsonl`, `fixtures/sample-traffic.jsonl`, then `Engineering/fixtures/sample-traffic.jsonl`).
- **It saved nothing**, because no catalog was supplied. Every feature fell through to the `foreground` default, so `feature_category` had nothing to route. That is the safe failure: forgetting to catalog a feature costs money, never quality.
- **It told you exactly which features fell through.** That note is the difference between a mis-costed workload and a visible gap.

Give it the catalog and the same fixture routes:

```bash
$ instar route \
    --traffic Engineering/fixtures/sample-traffic.jsonl \
    --catalog Engineering/fixtures/catalogs/example-departments.json
route -> runs/route-feature_category-mock
  policy=feature_category  saved=34.6%  q_all=0.983  q_weak=0.971  weak=6/10
```

Output lands in `runs/<label>/` (`runs/` is gitignored). Change the location with `--runs-dir`, and the directory name with `--label`.

Get a green mock run before you spend a live token. Mock mode is hermetic and byte-for-byte deterministic, which makes it the right place to find a broken fixture, a typo'd feature key, or a policy that does not do what you thought. It is also, emphatically, **not a measurement** — the numbers above are placeholders from synthetic completions and a placeholder pricing table, and the generated report says so on its face.

---

## 3. Run the control, then a policy

`all_strong` is the control: every call goes to the strong model, so it saves nothing by construction. **Always run it first.** It is the baseline every saving is measured against, and running it proves your fixture, catalog, models, and pricing are all wired up before any number means anything.

```bash
$ instar route --traffic Engineering/fixtures/sample-traffic.jsonl \
    --catalog Engineering/fixtures/catalogs/example-departments.json \
    --policy all_strong
route -> runs/route-all_strong-mock
  policy=all_strong  saved=0.0%  q_all=1.000  q_weak=n/a  weak=0/10
```

`saved=0.0%` and `q_weak=n/a` are the correct answers here. Anything else means something is wrong.

Now the rules policy:

```bash
$ instar route --traffic Engineering/fixtures/sample-traffic.jsonl \
    --catalog Engineering/fixtures/catalogs/example-departments.json \
    --policy feature_category
route -> runs/route-feature_category-mock
  policy=feature_category  saved=34.6%  q_all=0.983  q_weak=0.971  weak=6/10
```

Reading the console line:

| Field | Meaning |
|---|---|
| `saved` | Percent of spend saved versus the all-strong baseline. |
| `q_all` | Mean quality over every call. **Diluted** — every call that stayed strong scored 1.0 for free. |
| `q_weak` | Mean quality over only the calls that were actually routed to the weak model. **This is the number to read.** |
| `weak=6/10` | How many calls were moved. Six moved calls is a small sample; treat the quality figure accordingly. |

Note the gap: `q_all=0.983` looks almost untouched, while `q_weak=0.971` is the real figure for the calls you changed. On a workload that routes 10% of traffic, `q_all` will look reassuring no matter how badly those calls went. Quality risk lives entirely in the calls you moved.

The three shipped policies:

| `--policy` | Behavior |
|---|---|
| `all_strong` | Control. Everything strong. Saves nothing. |
| `feature_category` | Background features to the weak model, foreground to the strong model, per your catalog. Needs a catalog to do anything. |
| `classifier` | Scores each prompt for difficulty and routes below `--threshold` to the weak model. |

`feature_category` is worth taking seriously before anything cleverer. It needs no classifier, it is trivially explainable ("we only spend premium tokens where a person is waiting"), and on a workload with a large scheduled-job floor it captures most of the available saving.

---

## 4. Sweep a threshold and read the curve

A single point estimate tells you nothing about the trade. `--sweep` runs the classifier policy across several thresholds and draws the cost/quality curve:

```bash
$ instar route --traffic Engineering/fixtures/sample-traffic.jsonl \
    --catalog Engineering/fixtures/catalogs/example-departments.json \
    --sweep 0.1,0.2,0.3,0.4,0.5
sweep -> runs/route-sweep-mock
  t=0.10  saved= 31.2%  q_all=0.988  q_weak=0.970  weak=4
  t=0.20  saved= 34.6%  q_all=0.983  q_weak=0.971  weak=6
  t=0.30  saved= 94.5%  q_all=0.893  q_weak=0.893  weak=10
  t=0.40  saved= 94.5%  q_all=0.893  q_weak=0.893  weak=10
  t=0.50  saved= 94.5%  q_all=0.893  q_weak=0.893  weak=10
```

Raising the threshold sends more calls to the weak model: more savings, lower quality. The useful reading is **where the routed-weak quality column starts to fall away** — that is your workload's actual budget for cheapness, and it is not transferable to anyone else's workload.

Two things to notice in this particular output, both typical:

- The curve **saturates** at `t=0.30`: everything is already routed weak, so higher thresholds change nothing. Your interesting range is below the saturation point. If every row is identical, sweep lower.
- The step between `0.20` and `0.30` is large because the fixture only has ten samples. On a real workload, sweep more thresholds over a wider fixture.

`--sweep` always sweeps `ClassifierPolicy` (carrying your catalog), regardless of `--policy`. To sweep a policy of your own, use `run_sweep(..., policy_factory=...)` from Python.

The sweep run writes an extra file for charting:

```bash
$ cat runs/route-sweep-mock/sweep.csv
threshold,saved_pct,mean_quality_all,mean_quality_routed_weak,weak_count
0.1,31.153529895454007,0.9878,0.9695,4
0.2,34.602960178550454,0.9827,0.9711666666666666,6
0.3,94.47727005755902,0.8933,0.8933,10
0.4,94.47727005755902,0.8933,0.8933,10
0.5,94.47727005755902,0.8933,0.8933,10
```

`mean_quality_routed_weak` is empty in the CSV when nothing was routed weak at that threshold.

---

## 5. Measure your own workload

This is what Instar is for. The shipped fixtures exist to prove the pipeline works; they say nothing about your traffic. Four steps.

### 5.1 Capture

Instar does not capture traffic for you — it replays it. Get a list of the LLM calls one real workflow makes: a support triage pass, a campaign build, a nightly enrichment job. Pull them from your application logs, your gateway's request log, or your observability store.

Capture a **workload**, not a prompt. The point of measuring a workload is that it includes the background jobs and refinement loops that a "run it once and read the meter" estimate misses, and those are usually where the money is.

**Redact before you write the file.** The format is PII-free by construction — there is no tenant id, user id, or customer-content field — but nothing stops you pasting a real customer's message into `messages`. Replace real content with synthetic stand-ins that preserve the *shape* (length, structure, difficulty) of the original. Length matters: token estimates in mock mode come from character count, and a fixture of uniform prompts produces a flat, meaningless cost curve.

### 5.2 Write the fixture

One JSON object per line, in a `.jsonl` file. Only `id`, `feature`, and `messages` are required.

```bash
$ mkdir -p my-workload
$ cat > my-workload/my-workload.jsonl <<'EOF'
{"id": "sup-001", "feature": "support.ticket_classification", "system": "Classify the ticket into exactly one of: billing, bug_report, how_to. Reply with only the label.", "messages": [{"role": "user", "content": "The nightly export produced a zero-byte file three days running."}], "max_tokens": 8, "temperature": 0.0, "meta": {"synthetic": true, "gold": "bug_report"}}
{"id": "sup-002", "feature": "support.ticket_classification", "system": "Classify the ticket into exactly one of: billing, bug_report, how_to. Reply with only the label.", "messages": [{"role": "user", "content": "We were charged twice for the June renewal. Can you reverse the duplicate?"}], "max_tokens": 8, "temperature": 0.0, "meta": {"synthetic": true, "gold": "billing"}}
{"id": "sup-003", "feature": "support.macro_draft", "messages": [{"role": "user", "content": "Draft a reply to a customer whose order shipped four days late, offering a partial refund and explaining the warehouse backlog without making excuses."}], "max_tokens": 400, "temperature": 0.7, "meta": {"synthetic": true}}
{"id": "sup-004", "feature": "support.queue_digest", "system": "You write terse hourly queue digests. No speculation.", "messages": [{"role": "user", "content": "Digest the last hour: 38 new tickets, 12 escalations, median first response 22 minutes against a 15 minute target, backlog up 9 percent."}], "max_tokens": 200, "temperature": 0.0, "meta": {"synthetic": true, "cadence": "hourly"}}
EOF
```

Field notes:

| Field | Guidance |
|---|---|
| `id` | Unique within the fixture. It identifies the row in the report and seeds mock determinism. |
| `feature` | Your own dotted key naming the AI touchpoint. Instar attaches no meaning to it beyond what your catalog assigns, so pick names your stakeholders recognize — they appear in the report. |
| `max_tokens` | Copy the real value from your application. It caps mock output length and is sent verbatim on a live run. |
| `meta.gold` | The correct label, when the call is a classification. This is what unlocks objective scoring — see §6. |
| `meta.cadence` | How often a scheduled job fires. Not used by the runner; useful when you extrapolate a monthly figure by hand. |
| `category` | A per-sample override of the catalog. Leave it out unless one call in a feature is genuinely special. |

### 5.3 Write the catalog

The catalog is the one thing the harness cannot guess: only you know which of your features a person is actually waiting on. Foreground means a human is watching and will judge the output; background means it runs on a schedule or behind the scenes.

```bash
$ cat > my-workload/my-catalog.json <<'EOF'
{
  "default": "foreground",
  "categories": {
    "support.ticket_classification": "background",
    "support.queue_digest": "background",
    "support.macro_draft": "foreground"
  }
}
EOF
```

Uncatalogued features fall to `default`, which is `foreground` unless you change it. That is the safe direction — a forgotten feature costs money, never quality — and the CLI names every feature that fell through so the gap stays visible.

### 5.4 Run the control, then the policy

```bash
$ instar route --traffic my-workload/my-workload.jsonl \
    --catalog my-workload/my-catalog.json \
    --policy all_strong --label support-control
route -> runs/support-control
  policy=all_strong  saved=0.0%  q_all=1.000  q_weak=n/a  weak=0/4
```

Control looks right: nothing saved, nothing routed. Now the hypothesis:

```bash
$ instar route --traffic my-workload/my-workload.jsonl \
    --catalog my-workload/my-catalog.json \
    --policy feature_category --label support-rules
route -> runs/support-rules
  policy=feature_category  saved=14.1%  q_all=0.955  q_weak=0.940  weak=3/4
```

Three of four calls moved to the weak model for a 14.1% saving — a small percentage because the single foreground call (`support.macro_draft`, 400 max tokens) dominates the spend. That is a genuinely useful finding and a common one: **routing only pays where the volume is.** If your expensive calls are the ones a person is watching, routing is the wrong lever and prompt caching (`cached_call_cost_usd` in `core/cost.py`) is the right one.

Then find your workload's cheapness budget:

```bash
$ instar route --traffic my-workload/my-workload.jsonl \
    --catalog my-workload/my-catalog.json \
    --sweep 0.1,0.2,0.3,0.4,0.5 --label support-sweep
sweep -> runs/support-sweep
  t=0.10  saved= 14.1%  q_all=0.955  q_weak=0.940  weak=3
  t=0.20  saved= 14.1%  q_all=0.955  q_weak=0.940  weak=3
  t=0.30  saved= 96.6%  q_all=0.895  q_weak=0.895  weak=4
  t=0.40  saved= 96.6%  q_all=0.895  q_weak=0.895  weak=4
  t=0.50  saved= 96.6%  q_all=0.895  q_weak=0.895  weak=4
```

Everything above is still mock. These numbers verify the pipeline and nothing else. Once the shape looks right and the per-call table in `report.md` shows every call being routed the way you intended, go to §7.

---

## 6. Score a classification workload with gold labels

If your workload has ground truth, use it. `LabelMatchJudge` is exact, free, instant, and — unlike an LLM judge — is not itself a model whose judgment you would then have to validate. A classification batch is the best first candidate for evaluating a cheap or self-hosted model precisely because you can score it with a string compare and defend the result to anyone.

Add `meta.gold` to each sample:

```json
{"id": "sup-002", "feature": "support.ticket_classification", "system": "Classify the ticket into exactly one of: billing, bug_report, how_to. Reply with only the label.", "messages": [{"role": "user", "content": "We were charged twice for the June renewal. Can you reverse the duplicate?"}], "max_tokens": 8, "temperature": 0.0, "meta": {"synthetic": true, "gold": "billing"}}
```

**Objective scoring only takes effect on a live run.** In mock mode the synthetic backends emit no labels, so `MockJudge` is used regardless. On `--live`, the CLI collects every distinct `meta.gold` in the fixture, builds a `LabelMatchJudge` over that label set, and wraps it in an `AutoJudge`: samples with a gold label are scored objectively, everything else falls through to the `LLMJudge`. One run therefore scores a mixed workload correctly, with no flag to set.

Scoring rules worth knowing before you read the numbers:

- Matching is case-insensitive substring matching against the label set, longest label first, so `account_access` is never shadowed by `access`.
- A weak output with **no recognizable label scores 0.0**. A refusal or an invented category is a real routing failure, not a tie.
- With no `meta.gold`, the judge falls back to whatever label it can find in the *strong* model's output. That is a convenience, not a ground truth — supply real labels if the result will be quoted.
- A short `max_tokens` (8 in the example) both keeps the classification honest and keeps the live bill small.

The shipped `Engineering/fixtures/support-triage.jsonl` is a fully labelled 24-sample classification workload if you want a worked example.

---

## 7. Going live

Live runs cost real money and take real time. Everything below assumes your mock run is already green.

```bash
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-...
```

By default `instar route --live` uses the Anthropic backend for both arms and for the LLM judge. The SDK import is lazy, so it is only needed when an arm actually resolves to Anthropic — a run whose arms are both OpenAI-compatible URLs needs no SDK at all.

```bash
instar route --live \
  --traffic my-workload/my-workload.jsonl \
  --catalog my-workload/my-catalog.json \
  --policy feature_category \
  --strong-model claude-sonnet-4-6 \
  --weak-model claude-haiku-4-5 \
  --label support-live-01
```

**Cost warning.** A live route run makes up to **three** API calls per routed sample: the strong call (always), the weak call (if the policy routes it), and the judge call (if the sample has no gold label). A `--sweep` multiplies that by the number of thresholds. A 500-sample fixture swept across five thresholds is thousands of calls, and the strong-model calls dominate the bill.

Start small. Take a 20-to-50-sample slice of your fixture, run one policy at one setting, confirm the report reads sensibly and the token counts look like your production traffic, and only then scale up. If the fixture is labelled classification work, that first slice is nearly free to score.

Model ids:

| Flag | Default | Env override |
|---|---|---|
| `--strong-model` | `claude-sonnet-4-6` | `INSTAR_STRONG_MODEL` |
| `--weak-model` | `claude-haiku-4-5` | `INSTAR_WEAK_MODEL` |
| `--model` (gateway) | `claude-haiku-4-5` | `INSTAR_GATEWAY_MODEL` |

Use **undated** model ids. Dated snapshot ids are a recurring source of 404s as snapshots are retired, and a run that dies halfway through is worse than a run pinned slightly loosely. In mock mode these flags are ignored — models are forced to `mock-strong` and `mock-weak`.

A live run has no mock banner on its report, which means its numbers will be read as real. Before you circulate one, confirm `--pricing` is your own table (§9) and that the run exited `0`.

### Measuring a self-hosted or third-party model

Either arm can be pointed at any OpenAI-compatible endpoint instead of Anthropic. This is the comparison most cost questions actually turn on: keep a frontier model as the quality baseline, and put the candidate — a self-hosted small model, a cheaper hosted one, anything behind a proxy — on the weak arm.

```bash
instar route --live \
  --traffic my-workload/my-workload.jsonl \
  --catalog my-workload/my-catalog.json \
  --policy feature_category \
  --strong-model claude-sonnet-4-6 \
  --weak-url http://localhost:8000 \
  --weak-model Qwen/Qwen2.5-7B-Instruct \
  --label self-hosted-weak-01
```

| Flag | Effect |
|---|---|
| `--strong-url` / `--weak-url` | Point that arm at an OpenAI-compatible base url. Omit for Anthropic. |
| `--strong-key-env` / `--weak-key-env` | Env var holding that arm's API key. Omit for endpoints that need none, which is usual for local vLLM and Ollama. |

The LLM judge runs on the **strong** arm — whatever you trust as the quality baseline is what grades the cheaper model's work. Give that arm a real pricing row (§9) or its calls will cost $0 in the report.

`OpenAICompatBackend` needs no SDK: it speaks `POST {base_url}/chat/completions` over stdlib `urllib`, and covers vLLM, TGI, Ollama, LiteLLM proxy, OpenRouter, and OpenAI itself.

For anything the flags do not cover — a custom backend, a trained router, a bespoke judge — drive the runner from Python directly:

```python
from instar.core.catalog import FeatureCatalog
from instar.core.route import run_route
from instar.core.traffic import load_traffic
from instar.policies import FeatureCategoryPolicy
from instar.providers.anthropic import AnthropicBackend
from instar.providers.openai_compat import OpenAICompatBackend
from instar.reporters import report_route
from instar.rubrics.judges import AutoJudge, LabelMatchJudge, LLMJudge

samples = load_traffic("my-workload/my-workload.jsonl")
catalog = FeatureCatalog.from_json("my-workload/my-catalog.json")
labels = sorted({str(s.meta["gold"]) for s in samples if s.meta.get("gold")})

strong = AnthropicBackend("anthropic")
weak = OpenAICompatBackend("http://localhost:8000", name="vllm")

result = run_route(
    samples,
    policy=FeatureCategoryPolicy(catalog),
    strong_backend=strong,
    weak_backend=weak,
    judge=AutoJudge(LabelMatchJudge(labels), LLMJudge(strong, "claude-sonnet-4-6")),
    strong_model="claude-sonnet-4-6",
    weak_model="Qwen/Qwen2.5-7B-Instruct",
    catalog=catalog,
)
report_route(result, "self-hosted-weak", mock=False, runs_dir="runs")
print(f"saved {result.cost.saved_pct:.1f}%  routed-weak quality {result.mean_quality_routed_weak}")
```

---

## 8. Compare two gateways

`instar gateway` replays the same workload through two arms and compares per-call latency. Both arms are just backends, so this compares a hosted router against a self-hosted proxy, a gateway against calling the provider directly, or two configurations of the same gateway.

Calls are **interleaved** (A, B, A, B, ...) rather than run in blocks, so drift in network conditions or provider load is spread across both arms instead of being attributed to whichever ran second.

Mock first, to see the report shape:

```bash
$ instar gateway --traffic Engineering/fixtures/sample-traffic.jsonl \
    --a-name litellm --b-name direct --repeats 20
gateway -> runs/gateway-mock
  overhead (litellm - direct): p50 +2.0ms  p95 +2.0ms  p99 +2.0ms
```

(The mock arms have fixed simulated latencies of 12 ms and 10 ms, which is why the delta is exactly 2 ms at every percentile.)

Live, comparing a LiteLLM proxy against a vLLM server directly:

```bash
instar gateway --live \
  --traffic my-workload/my-workload.jsonl \
  --a-url http://localhost:4000 --a-name litellm --a-key-env LITELLM_API_KEY \
  --b-url http://localhost:8000 --b-name vllm-direct \
  --model Qwen/Qwen2.5-7B-Instruct \
  --repeats 20 \
  --label gw-litellm-vs-direct
```

Notes:

- `--a-url` and `--b-url` are **both required** with `--live`; the run refuses to start otherwise.
- Base URLs work with or without a trailing `/v1`: `http://localhost:8000` and `http://localhost:8000/v1` both resolve to `http://localhost:8000/v1/chat/completions`.
- `--a-key-env` / `--b-key-env` name an *environment variable* holding a bearer token, not the token itself. Omit them for endpoints that do not authenticate — local vLLM and Ollama usually need none.
- `--model` is sent to both arms, so the model id has to be valid on both.
- `--repeats N` replays the whole workload N times. Latency is noisy; a single pass over a short fixture is an anecdote.

**Below 30 calls per arm, the run warns and marks itself untrustworthy:**

```
  warning: only 4 calls per arm - tail percentiles are indicative at best; raise --repeats or use a larger fixture before quoting p95/p99
```

p99 of ten calls is just the slowest call. Treat tail figures as indicative until n is well into the hundreds.

Latency is wall-clock from the client, so it includes your network path to each endpoint. Comparing a hosted service across the internet against one on localhost measures geography as much as software — put the arms on comparable footing before drawing a conclusion.

Instar takes no view on which gateway you should use and ships no vendor capability matrix. Feature checklists go stale and a vendor's own comparison table is marketing. What a harness can honestly contribute is the number nobody publishes about your traffic: the latency your chosen layer actually adds.

---

## 8b. Compare three or more arms (`instar arms`)

`instar gateway` holds the model fixed across two arms. That cannot express the
comparison most people actually want, which has three arms and *varies* the
model on the third:

| arm | what it isolates |
|---|---|
| **A** direct to the provider | the baseline you have today |
| **B** through a router, same model | what the extra hop costs |
| **C** through a router, cheaper model | what routing saves |

`instar arms` gives every arm its own backend **and its own model**, and reports
latency and cost per arm plus deltas against a baseline.

```bash
instar arms --live \
  --traffic my-workload/my-workload.jsonl \
  --repeats 5 \
  --pricing my-pricing.json \
  --extra-body '{"provider":{"data_collection":"deny","zdr":true}}' \
  --arm 'name=A-direct,url=direct,model=claude-sonnet-4-5' \
  --arm 'name=B-router-same,url=https://openrouter.ai/api/v1,key_env=OPENROUTER_API_KEY,model=anthropic/claude-sonnet-4.5' \
  --arm 'name=C-router-cheap,url=https://openrouter.ai/api/v1,key_env=OPENROUTER_API_KEY,model=deepseek/deepseek-chat'
```

Notes:

- Each `--arm` is a flat `key=value` list: `name=`, `model=`, and either `url=`
  (any OpenAI-compatible endpoint) or `url=direct` for the native Anthropic
  backend. `key_env=` names an *environment variable*, never the token.
- `--baseline` picks which arm the deltas are measured against. Default: the
  first, which is the natural reading of "A/B/C".
- `--extra-body` is merged into every OpenAI-compatible request. It is how
  endpoint-specific controls reach the wire without the harness growing a
  vendor-shaped API. Keys the harness owns (`model`, `messages`, `max_tokens`)
  always win, so a stray override cannot change what is being measured.
- Arms are interleaved (A, B, C, A, B, C, ...) for the same reason the two-arm
  runner interleaves.

### Where the cost number comes from

Each arm's cost carries a `cost_source`, and it matters more than the dollar
figure:

| source | meaning |
|---|---|
| `provider_reported` | the endpoint told us what the call cost. A measurement. |
| `computed` | derived from tokens and your pricing table. An estimate. |
| `unavailable` | neither was possible. **Not `$0` — missing.** |

Aggregators report real per-call cost (OpenRouter's `usage.cost`) because only
the router knows which upstream host served the request and at what rate. A
frontier API does not; it returns tokens and leaves the arithmetic to you.

Three deliberate refusals, all in service of not publishing a confident wrong
number:

1. An arm that is **partially** reported falls back to the pricing table
   entirely, rather than summing measured dollars with estimated ones and
   presenting the total as a measurement.
2. An arm that is neither reported nor priced is `unavailable`, and the run
   warns. A silent `$0` is how a cost report ends up flattering whichever arm
   happens to be unpriced.
3. A cost delta is **omitted** when either side is unknown, rather than printed
   as a percentage derived from a zero.

If arms disagree on `cost_source`, the run says so — a comparison that mixes
measured and estimated cost is not like-for-like, and the totals alone will not
tell you.

### Model substitution is a finding, not noise

A router may serve a model other than the one requested — a fallback chain
firing, or an auto-router substituting. When an arm's responses name a different
model, the run records the served ids and warns. For a router comparison this is
often the most important thing the run has to tell you, so it is surfaced rather
than averaged away.

---

## 9. Supply your own pricing table

**The shipped pricing table is a placeholder.** Its values are sized to be directionally sane so mock runs produce believable curves. They are not authoritative and they go stale the moment a provider changes a price. Verify them, or supply your own, before quoting any figure publicly.

A pricing table is JSON mapping a model id to `[input_usd_per_mtok, output_usd_per_mtok]`:

```bash
$ cat > my-workload/my-pricing.json <<'EOF'
{
  "claude-sonnet-4-6": [3.0, 15.0],
  "claude-haiku-4-5": [1.0, 5.0],
  "mock-strong": [12.0, 60.0],
  "mock-weak": [0.8, 4.0]
}
EOF

$ instar route --traffic my-workload/my-workload.jsonl \
    --catalog my-workload/my-catalog.json \
    --pricing my-workload/my-pricing.json --label support-priced
route -> runs/support-priced
  policy=feature_category  saved=13.5%  q_all=0.955  q_weak=0.940  weak=3/4
```

`--pricing` **replaces** the built-in table rather than merging with it, so it must cover every model in the run. A model with no row costs `$0.00`, which would silently understate a run — so every run checks and warns:

```
  warning: no pricing for mock-weak - those calls were costed at $0, so the totals understate real spend
```

The warning also lands in `report.md`, above the results table.

Two special cases, both handled in `instar.core.cost`:

- **Self-hosted models.** Either price them at `[0.0, 0.0]` (marginal cost against a fixed GPU bill) and use `breakeven_requests_per_month(...)` to find the monthly volume at which self-hosting beats a per-token API, or precompute `$/Mtok` from `$/hr ÷ throughput` and put that in the row directly.
- **Prompt caching.** `cached_call_cost_usd(...)` prices a call that reuses a cached static prefix, with a surcharge on a cold write and a large discount on a warm read. This is the lever for work that routing cannot cheapen: a quality-sensitive foreground call that must stay on the strong model but re-sends the same large prefix every turn. It is a pure function and does not touch the routing runner, which stays the no-caching baseline.

---

## 10. Read a report

Every run writes `<runs-dir>/<label>/`:

| File | Contents |
|---|---|
| `result.json` | The full result, including every per-call outcome and a `trustworthy` boolean. This is the machine-readable artifact. |
| `report.md` | The human-readable report, with its caveats. |
| `sweep.csv` | Sweeps only. One row per threshold, ready to chart. |

A real (mock) `report.md`, complete:

```markdown
# Routing run — MOCK

- policy: **feature_category**
- strong: `mock-strong` · weak: `mock-weak`
- samples: 4 · routed to weak: 3

> **MOCK RUN — not a measurement.** Outputs are deterministic synthetic text and costs come from placeholder pricing. Use this to verify the pipeline, never to make a decision.

| metric | value |
|---|---|
| baseline (all-strong) cost | $0.019960 |
| routed cost | $0.017154 |
| **spend saved** | **$0.002805 (14.1%)** |
| mean quality (all calls) | 0.955 |
| **mean quality (routed-weak only)** | **0.940** |

_Mean quality over all calls is diluted by every call that stayed strong and scored 1.0 by definition. The routed-weak figure is where quality risk actually lives — read that one._

_Costs use Instar's placeholder pricing table unless you supplied your own with `--pricing`. Verify against current provider pricing before quoting any figure._

## Per-call decisions

| id | feature | category | target | cost | quality | why |
|---|---|---|---|---|---|---|
| `sup-001` | support.ticket_classification | background | weak | $0.000038 | 0.93 | background feature → weak model |
| `sup-002` | support.ticket_classification | background | weak | $0.000041 | 0.97 | background feature → weak model |
| `sup-003` | support.macro_draft | foreground | strong | $0.016820 | 1.00 | foreground feature → strong model |
| `sup-004` | support.queue_digest | background | weak | $0.000256 | 0.92 | background feature → weak model |
```

How to read it:

1. **Check the banner first.** A `MOCK RUN` banner means the numbers below are placeholders. Failure counts and warnings also appear above the results table, never in a footnote — because these files get pasted into decks and forwarded to people who never ran the tool.
2. **Read the bolded routed-weak quality**, not the overall mean.
3. **Scan the per-call table.** Every routing decision carries a `why`. If a call went somewhere you did not expect, this is where you find out — usually a feature key that is missing from the catalog or spelled differently in the fixture.
4. **Check `trustworthy` in `result.json`.** It is `false` if anything happened that should stop you quoting the numbers: any failed call, or any warning.

The per-call table costs `$0.016820` for one foreground call versus `$0.000038` for a routed one. Numbers at that resolution are how you spot that a single expensive call is carrying the whole workload — which changes the recommendation from "route more" to "cache the prefix on the expensive one".

---

## 11. Interpreting results honestly

The project's credibility rests on not overclaiming. What a clean run does and does not support:

**You can say:**

- "On this captured workload, this policy would have cost X% less than sending everything to the strong model."
- "Of the N calls it moved to the cheaper model, mean quality against the strong model's answer was Q, scored by [name the judge]."
- "Here is the per-call table; you can audit any individual decision."
- "Latency for arm A was p50 X ms against arm B's Y ms, over N interleaved calls per arm from this client."

**You cannot say:**

- **Anything, from a mock run.** Mock output is synthetic text and placeholder pricing. It proves the pipeline runs. It measures nothing.
- **"We will save X%."** You measured a replay of past traffic against a fixed strong baseline. Production has retries, caching, traffic mix shifts, and prompt changes.
- **"Quality is unaffected"**, from the overall mean. Quote the routed-weak figure or quote nothing.
- **Anything from an unvalidated LLM judge.** It is a measurement instrument with its own error rate. Hand-grade a sample and check the judge agrees before you rest a decision on it.
- **A dollar figure from the placeholder pricing table.** Supply `--pricing` first.
- **A p95 or p99 from a small gateway run.** The tool warns below 30 calls per arm for a reason.
- **"This generalizes."** A cheapness budget measured on your support queue does not transfer to your marketing team, let alone to another company.

Three specific traps:

- **The diluted mean.** `q_all` includes every call that stayed strong and scored 1.0 for free. Route 10% of traffic and `q_all` will read above 0.97 even if every moved call was garbage.
- **The silent $0.** An unpriced or typo'd model id costs nothing and makes a run look brilliant. The warning exists because this is the easiest way to publish a wrong number.
- **The MARGINAL rung.** A cheap answer that triggers a user retry is not a saving — the user pays again, in a second call and in their own patience. `LLMJudge` scores that 0.5 rather than folding it into PASS, and a policy whose routed-weak quality sits near 0.5 is likely producing retries, not savings.

Small `n` deserves a caveat everywhere. Six routed calls is an anecdote. Report the count alongside the mean; the CLI and the report both give it to you.

---

## 12. Troubleshooting

**`saved=0.0%` and `weak=0/N` with `--policy feature_category`.**
No catalog, or no feature in the catalog is marked `background`. Check the note the CLI prints: `note: N feature(s) not in the catalog, defaulting to 'foreground'`. Feature keys must match the fixture exactly. `FeatureCategoryPolicy` with an empty catalog behaves exactly like the control — that is a useful sanity check and a signal that you have not catalogued your features yet.

**`instar: no --traffic given and no default fixture found`.**
You are not in the repo root. Pass `--traffic path/to/workload.jsonl` explicitly.

**`instar: <path>:2: bad traffic sample: Expecting value: ...`.**
A malformed line in your JSONL fixture; the message names the file and the line number. Common causes: a pretty-printed JSON object spanning multiple lines (each sample must be on exactly one line), a trailing comma, or a missing `id`/`feature`/`messages` key.

**`instar: sample 'x': category 'urgent' must be one of ['background', 'foreground'] or None`.**
The only valid categories are `foreground` and `background`. The same applies to a catalog's `default` and to every value in its `categories` map.

**`warning: no pricing for <model>`.**
That model has no row in the active pricing table, so its calls were costed at `$0` and the totals understate real spend. Add the row. Remember `--pricing` replaces the built-in table rather than merging with it.

**`ModuleNotFoundError: the Anthropic backend needs the 'anthropic' SDK ...`.**
Install the optional extra: `pip install -e ".[anthropic]"`. The import is lazy, so this only surfaces when an arm actually resolves to Anthropic — give both arms a `--strong-url`/`--weak-url` and no SDK is needed.

**`instar: --live gateway runs need both --a-url and --b-url`.**
A live gateway comparison needs both arms. Exit code 1.

**All gateway calls failed / `HTTP 401` / `HTTP 404`.**
The report names the failure per arm. Check the endpoint resolves (`{base_url}/v1/chat/completions`), that `--model` is valid on **both** arms, and that `--a-key-env`/`--b-key-env` name an environment variable that is actually exported — they take a variable *name*, not a token.

**Exit code 1 on a run that printed a report.**
Some calls failed. They are excluded from every figure and counted in `error_count`; `trustworthy` in `result.json` is `false`. Re-run clean before trusting the numbers. This exit code is what lets CI refuse to publish a report built on partial data.

**Two runs overwrote each other.**
Without `--label`, the directory name is derived from the policy and mode (`route-feature_category-mock`), so re-running the same policy overwrites the previous run. Pass `--label` for anything you want to keep.

**Quality is 1.000 for every call.**
Nothing was routed to the weak model, so every call scored 1.0 by definition. `q_weak=n/a` confirms it. Either you ran `--policy all_strong`, or the catalog issue above applies.

**The sweep shows an identical row at every threshold.**
The curve has saturated — everything is already routed weak (or nothing is). Sweep a lower range; §4 shows an example that saturates at 0.30.

**Mock numbers changed between runs.**
They should not; mock output is a pure function of `(sample.id, model)`. If they did, your fixture changed — most often a duplicated or edited `id`.
