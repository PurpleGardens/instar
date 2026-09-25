# Providers — connecting Instar to the models you want to measure

> **TL;DR:** Instar reaches every model the same way — an OpenAI-compatible
> `POST {base_url}/v1/chat/completions`. A hosted LLM is *sign up, add a payment
> method, get a key, point Instar at the URL*: about five minutes and no
> infrastructure. A self-hosted SLM is *install a server, pull weights, start it,
> point Instar at localhost*: about thirty minutes on a machine with a GPU, and
> from then on it is yours to operate. Everything after that — fixtures,
> policies, judges, reports — is identical for both.

Instar ships **no provider accounts, no keys, and no opinion about which vendor
you should use.** It is a measurement tool; the providers are yours.

---

## 1. The one interface

Every backend Instar talks to implements a single method:

```
complete(sample, model) -> CompletionResult(text, model, input_tokens, output_tokens, latency_s, ok, error)
```

Two implementations cover almost everything:

| Backend | Reaches | Needs |
|---|---|---|
| `OpenAICompatBackend` | vLLM, Ollama, llama.cpp, TGI, LM Studio, LiteLLM proxy, OpenRouter, OpenAI, and most hosted providers | nothing — stdlib `urllib` |
| `AnthropicBackend` | Anthropic | `pip install 'instar-harness[anthropic]'` |

Because the OpenAI-compatible dialect is near-universal, **the harness core
needs no SDK at all.** That is deliberate: `pip install instar-harness` pulls in nothing,
mock mode runs anywhere, and a provider SDK release cannot break your CI.

On the command line, an arm becomes OpenAI-compatible the moment you give it a
URL:

```bash
--strong-url http://localhost:11434   --strong-model qwen2.5:7b
--weak-url   https://openrouter.ai/api --weak-model  qwen/qwen-2.5-7b-instruct --weak-key-env OPENROUTER_API_KEY
```

Omit the URL and that arm uses Anthropic. Omit the key env var and no
`Authorization` header is sent, which is what local servers want.

---

## 2. Hosted LLMs — the five-minute path

The setup is the same shape at every vendor.

1. **Create an account** with the provider.
2. **Add a payment method.** Nearly all of them meter per token; a few offer
   trial credit. Instar will spend real money the moment you pass `--live`.
3. **Create an API key.** Scope it to the minimum the vendor allows, and give it
   a spending cap if the vendor supports one. A runaway sweep is the realistic
   failure mode, not a breach.
4. **Export the key** into your shell. Instar never reads a key from a file or a
   flag — only from an environment variable you name — so keys stay out of shell
   history, out of run artifacts, and out of git.
5. **Point Instar at the base URL.**

```bash
export OPENROUTER_API_KEY=sk-or-...

instar route --live \
  --traffic your-workload.jsonl \
  --catalog your-catalog.json \
  --strong-url https://openrouter.ai/api --strong-model anthropic/claude-sonnet-4-6 \
  --weak-url   https://openrouter.ai/api --weak-model  qwen/qwen-2.5-7b-instruct \
  --strong-key-env OPENROUTER_API_KEY \
  --weak-key-env   OPENROUTER_API_KEY
```

Anthropic is the one provider with a dedicated backend, because it does not
speak the OpenAI dialect natively:

```bash
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-...

instar route --live --traffic your-workload.jsonl --strong-model claude-sonnet-4-6
```

### An aggregator is the cheapest way to compare many models

If the question is *which* model, a router/aggregator such as OpenRouter gets you
one account, one key, one base URL, and dozens of models behind it — including
open-weight SLMs you would otherwise have to host. That makes the first pass
cheap: measure six candidates, then self-host only the one that wins.

Two cautions when measuring through an aggregator:

- **Pin the upstream provider if you can.** The same open-weight model served by
  two different hosts can differ in quantization, context limit, and latency.
  Unpinned, you are measuring an aggregate of hosts rather than a model.
- **Latency includes the aggregator's own hop.** Fine for comparing models
  against each other; not a fair number to compare against a `localhost`
  deployment. See §5.

### Model ids

Use **undated** model ids. Dated snapshot ids are a recurring source of 404s as
snapshots are retired, and a run that dies halfway through is worse than a run
pinned slightly loosely. Instar's defaults are undated for this reason.

Every model in a run needs a row in the pricing table or its calls cost `$0` and
the totals silently understate spend. Instar warns when this happens, but supply
your own table with `--pricing` before quoting any figure.

---

## 3. Self-hosted SLMs — the thirty-minute path

Self-hosting trades a per-token bill for a fixed one. That trade only pays above
some steady volume, which is exactly what
`instar.core.cost.breakeven_requests_per_month()` computes — run it before you
buy a GPU, not after.

The setup effort is real but bounded, and it is a one-time cost per model.

### Choosing a server

| Server | Use it for | Endpoint |
|---|---|---|
| **Ollama** | fastest start; local development, demos, first measurements | `http://localhost:11434` |
| **vLLM** | production throughput, batching, concurrency | `http://localhost:8000` |
| **llama.cpp** | CPU-only hosts, laptops, edge, ARM | `http://localhost:8080` |

All three speak the OpenAI dialect, so **Instar's flags do not change between
them** — only the port does. Start on Ollama to get a number; move to vLLM when
throughput matters.

### Ollama quickstart

```bash
# install (no root needed: extract the release tarball into ~/.local)
ollama serve &                        # starts the OpenAI-compatible server

ollama pull qwen2.5:7b-instruct-q4_K_M     # ~4.7 GB
ollama pull qwen2.5:3b-instruct-q4_K_M     # ~2.0 GB

instar route --live \
  --traffic Engineering/fixtures/support-triage.jsonl \
  --catalog Engineering/fixtures/catalogs/example-departments.json \
  --strong-url http://localhost:11434 --strong-model qwen2.5:7b-instruct-q4_K_M \
  --weak-url   http://localhost:11434 --weak-model  qwen2.5:3b-instruct-q4_K_M \
  --policy feature_category
```

No API key, no account, no payment method. This is the cheapest possible real
measurement: two open models, scored objectively against gold labels, for the
cost of the electricity.

### vLLM quickstart

```bash
pip install vllm
vllm serve Qwen/Qwen2.5-7B-Instruct --dtype bfloat16 --max-model-len 32768

instar route --live --traffic your-workload.jsonl \
  --weak-url http://localhost:8000 --weak-model Qwen/Qwen2.5-7B-Instruct
```

Some weights are license-gated on HuggingFace: you must accept the license on the
model page **and** export `HF_TOKEN` before the server can download them. This
catches people out constantly, and the error appears at model-download time, not
at install time.

### Sizing

Approximate VRAM at 4-bit quantization, which is where most small models still
behave well:

| Model size | VRAM (4-bit) | Fits on |
|---|---|---|
| 1–2 B | ~1.5 GB | CPU, or anything |
| 3–4 B | ~2.5 GB | any modern GPU |
| 7–9 B | ~5–6 GB | 8 GB card, with headroom for short context |
| 14 B | ~9 GB | 12 GB card |
| 30–32 B | ~20 GB | 24 GB card |

Long context is the hidden cost: the KV cache, not the weights, dominates VRAM at
high context lengths. Set the server's max context to what your workload actually
needs rather than leaving it at the model maximum.

### Licensing is a procurement question, not a technical one

Open weights are not all equally open, and the difference surfaces in a
customer's legal review rather than in your terminal:

- **Apache-2.0 / MIT** — no practical restrictions. The cleanest posture, and the
  one that raises no questions.
- **Vendor community licenses** — commercial use is permitted, but they are not
  OSI-approved and typically add attribution requirements, an acceptable-use
  policy you must pass through to downstream users, and sometimes a
  monthly-active-user threshold above which you need a separate agreement.

None of this blocks a measurement. It matters when the model stops being an
experiment and starts being something you productize, so establish the license
posture before you build on a model rather than after.

---

## 4. Getting a small model to behave

Small models fail differently from large ones, and several of their failure modes
look like *low quality* in a report when they are really *prompt or plumbing
bugs*. Rule out the following before concluding a model is not good enough — a
misdiagnosis here is expensive, because it sends you back to a costlier model
that you did not need.

- **Pin the output language.** Multilingual models sometimes answer in another
  language when the system prompt does not specify one. Against an exact-match
  scorer, that reads as 0% accuracy.
- **Pin the output format, and leave room for it.** If you ask for a bare label,
  say so — and make sure `max_tokens` is large enough for the answer plus any
  preamble the model insists on adding.
- **Turn off "thinking" modes for short-answer work.** Reasoning modes emit long
  chains before the answer. With a small `max_tokens`, the output is truncated
  before the answer ever appears, and the model scores zero for a reason that has
  nothing to do with its ability.
- **Let the server apply the chat template.** Every family has its own control
  tokens. Hand-formatting prompts breaks system-prompt adherence and tool calling
  in ways that are subtle rather than obvious.
- **Quantization is a variable, not a detail.** A model at 4-bit is not the same
  measurement as the same model at 8-bit or full precision. Record which you
  used; some families degrade noticeably more than others.
- **Small variants are not miniature large ones.** A 3 B model from the same
  family as an 8 B is a genuinely different model, not a scaled-down one — more
  hallucination, weaker multi-step reasoning, less reliable tool use.

If a small model scores badly, check this list before you believe the number.

---

## 5. Comparing fairly

The point of measuring is to make a defensible claim, and most unfair
comparisons come from the setup rather than the models.

- **Latency is wall-clock from the client**, so it includes your network path.
  A hosted API measured across the internet against a model on `localhost` is
  measuring geography as much as software. Put the arms on comparable footing, or
  say plainly that you did not.
- **A self-hosted model's marginal token cost is near zero**, but its fixed cost
  is not. Price it either as `(0.0, 0.0)` and reason about break-even volume, or
  amortize the hourly infrastructure cost into a $/Mtok rate — but do not compare
  an un-amortized self-hosted model against a metered API and call the difference
  savings.
- **Warm up before you measure.** A first call after model load pays a startup
  cost that is not representative. Discard it, or use `--repeats` so it is
  diluted.
- **Concurrency changes throughput conclusions.** Ollama serves one request at a
  time by default; vLLM batches. A single-threaded replay understates what a
  production deployment would achieve.

---

## 6. Where to go next

- [`04-RUNBOOK.md`](04-RUNBOOK.md) — running Instar, including measuring your own
  workload end to end.
- [`06-RUBRICS.md`](06-RUBRICS.md) — turning measurements into a decision.
- [`10-CODE-OVERVIEW.md`](10-CODE-OVERVIEW.md) — the `Backend` interface, if you need
  to write a provider Instar does not ship.
