<!-- SPDX-License-Identifier: Apache-2.0 -->
# Instar and smevals: two questions, two tools

**TL;DR** — [smevals](https://github.com/prime-radiant-inc/smevals) answers *"how good is
model X at abstract task Y?"* Instar answers *"which model mix, which router, for which
use case, **on my traffic**?"* Those are different questions, they gate different
decisions, and they want different inputs — a hand-authored task versus a JSONL capture
of production calls. If you're choosing models, start with smevals. If you're deciding
whether a routing change pays for itself, that's Instar. Most real migrations want both,
in that order.

---

## The two questions

**smevals — the capability question.** *Can this model do this kind of thing at all, and
which candidate does it best?* You write a task, point it at several models, and get a
comparison. The answer is about the **model**.

**Instar — the workload-economics question.** *If I move this workload to a cheaper or
smaller model, what happens to my cost, my quality, and my p95 — and is that trade worth
making?* You replay traffic you actually served, through a routing policy, and get a
verdict. The answer is about the **workload**.

You can't derive the second from the first. What you'd save depends on your traffic mix
— how many calls are easy, how long the prompts run, which ones you'd never route away
— and a hand-picked task set doesn't contain that distribution. That's not a gap in
smevals; it's a different measurement with a different input.

---

## What each one is

**smevals** (Simon Willison and Jesse Vincent, Prime Radiant, MIT). A framework for
running evals against small and large models. The vocabulary is precise and worth
learning: an **Eval** is a set of **Tasks**; a **Config** is a model plus its settings; a
**Run** is one Task executed against one Config by a **Runner**; a **Grader** applies a
sequence of **Checks** to a Run to produce a **Grade**. Runners and Checkers are *any
executable*, driven by environment variables, which makes the extension story unusually
open. YAML throughout, a local viewer, and a static HTML build. `smevals docs` prints the
whole manual to stdout so a coding agent can author an eval suite for you — a genuinely
good idea, and the one we most want to borrow.

**Instar** (Purple Blossom AI, Apache 2.0, pre-release). A harness for measuring LLM
*workloads*. A **workload** is a JSONL file of captured LLM calls — the trace of what one
real workflow actually asked for. Replay it through a **routing policy** (send the easy
calls to the weak model, keep the rest on the strong one) and Instar reports what you'd
have spent, what quality you'd have given up, and what happened to latency at p50/p95/p99.
A **rubric** — bar set in advance — turns those numbers into a verdict: pass, marginal,
fail, or **unmeasured**. `--sweep` draws the cost/quality curve so you can see where the
trade stops being worth it.

---

## Where they genuinely overlap

The shared grammar of anything that measures LLMs, and it's not a competitive surface:
separate execution from grading, allow an LLM to judge when a deterministic check can't,
stay provider-neutral, ship as Python OSS under a permissive license. Both tools do all
four. Neither invented them.

---

## Side by side

| | **smevals** | **Instar** |
|---|---|---|
| The question | how good is this model at this task? | does this routing change pay off on my traffic? |
| Input | hand-authored YAML Tasks | JSONL of captured production calls |
| Unit of judgment | one Run (one Task × one Config) | one workload, across dimensions |
| Comparison | Config vs Config | strong vs weak under a routing policy |
| Runner contract | any executable, env-var driven | Python adapter (`Backend`); OpenAI-compatible + Anthropic |
| Grading | Checks in order; any failed Check fails the Grade, else score vs `pass_threshold` | multi-dimension rubric; **worst dimension wins**; bar set before the run |
| Harness errors | failed Runs are never graded and are excluded from reports | `run.error_count` is a mandatory rubric dimension at `pass_at: 0` |
| Cost | not tracked | baseline vs routed USD, saved %, break-even request count |
| Latency | not tracked | p50 / p95 / p99 / mean |
| Reporting | terminal, JSON, local viewer, static HTML | Markdown + JSON + CSV sweep |
| Agent onboarding | `smevals docs` | not yet — on the roadmap, borrowed from smevals |
| Config format | YAML | JSON rubrics + Python policies + JSONL workloads |
| License | MIT | Apache 2.0 |
| Status | released, on PyPI | pre-release, no tagged release yet |

Two rows deserve a note, because the one-word summaries flatten something real.

**On harness errors.** Both tools take the position that a network failure is not
evidence about a model — they just act on it differently. smevals *excludes* failed Runs
so they can't contaminate a capability judgment, which is right when you're asking what a
model can do. Instar *counts* them, because when you're deciding whether to put a
provider in front of production traffic, a backend that errors 4% of the time has told
you something decision-relevant. Different questions, defensibly different answers.

**On grading strictness.** It would be wrong to say smevals averages away failures — it
doesn't. Within a Run, any failed Check fails the Grade outright, which is strict. The
difference is the *unit*: smevals judges a Run, Instar judges a whole workload across
several dimensions at once (cost, quality on the routed subset, quality overall, latency
tail, error rate). Instar's anti-averaging stance is about that aggregate step — the
place where a healthy mean can hide a subset that broke badly.

---

## What smevals does better

Stated plainly, because a comparison that only flatters its author isn't worth reading.

1. **The static HTML report.** A shareable, self-contained artifact reads far better to a
   stakeholder than a Markdown file in a repo. Instar ships Markdown and CSV. This is on
   our roadmap explicitly because of smevals.
2. **Agent-authored evals.** `smevals docs` plus "point your coding agent at this" is a
   real UX innovation, not a convenience. Instar assumes a human author today.
3. **The executable Runner/Checker contract.** Any program, driven by env vars. For a
   one-off — a bash pipeline, an internal HTTP service, a non-Python stack — that beats
   writing a Python adapter. We intend to add an `--exec` backend for exactly this.
4. **It's released.** smevals is on PyPI with a real user base. Instar is pre-release.

---

## Composing them

The two tools chain, and the order matters because each stage is more expensive than the
one before it.

```
  Phase 1 — CANDIDATE DISCOVERY  (smevals)
    Author ~10 tasks shaped like your call site.
    Run them against 5–8 candidates: small local models, plus API baselines.
    Cheap: minutes, and a few dollars of inference.
    OUT: a shortlist of 2–3 models that can do the job at all.
                          │
                          ▼
  Phase 2 — ECONOMIC DECISION  (Instar)
    Replay 200–500 captured production calls through a routing policy,
    using the shortlist as the weak-model candidates.
    Cost, quality, and latency-tail numbers; rubric verdict with the bar set first.
    OUT: pass / marginal / fail, with a break-even request count.
```

The argument for the order: you don't want to replay five hundred real calls against
eight candidate models. Use the cheap capability filter to get to three, then spend the
expensive measurement once, on the finalists.

**When you only need one of them.** If the candidate set is already decided ("Opus versus
Haiku, that's the question"), skip Phase 1 — smevals adds nothing you don't know. If you
have no captured traffic yet, skip Phase 2 — Instar has nothing to replay, and a
capability answer is the honest stopping point. If the question is purely "which model is
best at this," Phase 1 *is* the whole answer.

---

## "Why not just add cost tracking to smevals?"

The fair question, and worth answering directly: because cost isn't a column you add —
it changes what the input has to be.

To say *"you would have saved 37%,"* you need the calls you actually served: their token
counts, their length distribution, the mix of easy and hard, and the ones you'd never
route away regardless of price. Twenty-four hand-authored tasks can tell you a model is
capable; they can't tell you what your traffic costs, because they aren't your traffic.
The moment you make a tool consume production captures, you've changed its input
contract, its privacy posture, and its unit of judgment — which is a different tool, not
a feature flag.

The same is true in reverse. Instar is deliberately **not a general eval platform** —
Promptfoo, Braintrust, DeepEval, and now smevals all win on breadth, and we'd rather
compose with them than lose slowly to all four.

---

## Pointers

- smevals — <https://github.com/prime-radiant-inc/smevals> (MIT)
- Instar — [`README.md`](../../README.md) · [`RUNBOOK.md`](RUNBOOK.md) (measure your own
  workload) · [`RUBRICS.md`](RUBRICS.md) (the verdict model) ·
  [`GUIDE-Setting-the-Bar.md`](GUIDE-Setting-the-Bar.md) (why the bar goes up front)
- A worked example of reading a verdict *wrongly* —
  [`CASE-STUDY-Qwen-Triage.md`](CASE-STUDY-Qwen-Triage.md)

*Written against smevals as of 2026-08-06. If something here has gone stale or reads
unfairly, please open an issue — we'd rather fix it than leave it wrong.*
