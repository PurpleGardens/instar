# Rubrics — turning a measurement into a decision

> **TL;DR:** A judge scores one answer. A **rubric** decides whether the whole
> configuration met the bar. It is a JSON file listing **dimensions** — each
> binding one measured metric to a `pass_at` threshold and an optional
> `marginal_at` band — agreed **before** the run and passed with `--rubric`. The
> overall verdict is the **worst** dimension, never an average, so a large cost
> saving cannot hide a failing quality score. A dimension with nothing to measure
> returns `unmeasured`, which is never a pass. A failing verdict exits `1`.
>
> **New to rubrics?** Learn by doing in
> [`08-LESSON-Rubrics-Hands-On.md`](08-LESSON-Rubrics-Hands-On.md) (ten minutes, no
> spend), or learn the method in
> [`07-GUIDE-Creating-Rubrics.md`](07-GUIDE-Creating-Rubrics.md). This page is the
> reference you keep open while writing one.

---

## 1. Why rubrics exist

A measurement does not make a decision. `saved=44.6%, quality=0.917` is not an
answer to "should we switch?" — it becomes one only once somebody says what
"good enough" was going to be.

The problem is *when* they say it. A threshold agreed in advance is a standard.
A threshold chosen after seeing the numbers is a rationalization, and it is
almost impossible to avoid: 91.7% accuracy feels acceptable if you were hoping
for 90% and feels alarming if you were hoping for 99%, and the number itself
cannot tell you which you were.

A rubric is the artifact that stops that slide. It is written before the run,
signed off by whoever owns the outcome, and executed mechanically. It moves the
argument to where it belongs — *before* the evidence, where it is about the
business — and it makes the run's conclusion reproducible by someone who was not
in the room.

### Judges and rubrics are different jobs

| | Judge | Rubric |
|---|---|---|
| **Question** | how good was *this answer*? | does *this configuration* meet the bar? |
| **Scope** | one call | one run |
| **Owner** | whoever defines quality for the task | whoever owns the business outcome |
| **In code** | `instar.rubrics.judges` | `instar.rubrics.spec` |

They are usually different people, which is exactly why they are different
artifacts. Do not let the person who tunes the judge also set the thresholds.

---

## 2. The format

A rubric is JSON. Pass it with `--rubric path/to/rubric.json`.

```json
{
  "name": "support-triage-v1",
  "description": "What good looks like for ticket triage.",
  "dimensions": [
    {
      "id": "accuracy",
      "label": "Label accuracy on tickets moved to the cheap model",
      "metric": "quality.routed_weak_mean",
      "direction": "higher_is_better",
      "pass_at": 0.95,
      "marginal_at": 0.90,
      "rationale": "Below 0.90 a human re-checks every ticket, which erases the saving."
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | stable identifier; must be unique within the rubric |
| `metric` | yes | which measured value this dimension binds to (§3) |
| `pass_at` | yes | the bar. Meeting it exactly is a pass |
| `direction` | no | `higher_is_better` (default) or `lower_is_better` |
| `marginal_at` | no | the "usable but not good" band. Omit for pass/fail only |
| `label` | no | human wording for the report; defaults to `id` |
| `rationale` | no | **why** this threshold. Omit it and nobody can defend it later |

Write the `rationale`. A threshold whose reasoning is not recorded will be
argued about again in three months, by people who were not there, using worse
information.

### Validation is strict on purpose

The loader refuses, at load time rather than in a report:

- an unknown `metric` (a typo would otherwise become a silently missing row);
- a `direction` that is not one of the two;
- a `marginal_at` on the wrong side of `pass_at`, which would make `marginal`
  unreachable and is almost always a typo;
- duplicate dimension `id`s;
- a rubric with no dimensions.

---

## 3. Metrics a dimension can bind to

| Metric | Meaning |
|---|---|
| `cost.baseline_usd` | spend with everything on the strong model |
| `cost.routed_usd` | spend under the policy |
| `cost.saved_usd` | absolute saving |
| `cost.saved_pct` | percentage saved vs baseline |
| `quality.mean_all` | mean quality across every call |
| `quality.routed_weak_mean` | mean quality across only the calls moved to the weak model |
| `quality.routed_weak_min` | the single worst routed call |
| `latency.p50_ms` / `p95_ms` / `p99_ms` / `mean_ms` | latency of the call a user actually waited on |
| `run.error_count` | failed calls |
| `run.weak_share_pct` | percentage of traffic the policy moved |

**For `instar arms` runs**, including ones already stored in a corpus, bind the
`arm.*` metrics instead. Each is computed for one candidate arm:

| Metric | Meaning |
|---|---|
| `arm.quality_mean` | the arm's mean judged quality |
| `arm.quality_min` | its single worst judged answer |
| `arm.quality_vs_control` | its quality divided by the same-model control's; unmeasured without a control |
| `arm.cost_saved_pct` | cost per call saved vs the baseline arm; unmeasured if either cost is unknown |
| `arm.p50_ms` / `arm.p95_ms` | latency percentiles |
| `arm.ms_per_output_token` | latency normalized by output length |
| `arm.error_count` | failed calls on this arm |

`instar corpus rubric` applies such a rubric to history (`04-RUNBOOK.md` §8c). A
routing metric asked of an arms run is `unmeasured`, never a pass.

Three notes that matter more than they look:

- **Prefer `quality.routed_weak_mean` over `quality.mean_all`.** Calls kept on
  the strong model score 1.0 by definition. On a workload that routes 10% of
  traffic, `mean_all` stays above 0.9 no matter how badly those calls went.
- **Pair a mean with `quality.routed_weak_min`.** A healthy mean can hide one
  category the small model cannot recognize at all. §5 is a live example of
  exactly this.
- **Always include `run.error_count` with `pass_at: 0`.** Failed calls are
  excluded from every other number in the report, so a run with failures can
  post excellent aggregates that describe only the calls that happened to work.

---

## 4. How the verdict is computed

Each dimension resolves to one of four values:

| Verdict | When |
|---|---|
| `pass` | the metric met `pass_at` |
| `marginal` | it missed `pass_at` but met `marginal_at` |
| `fail` | it missed both |
| `unmeasured` | the run produced no value for this metric |

The overall verdict is the **worst** of them, ordered
`fail < unmeasured < marginal < pass`.

**Two deliberate refusals:**

1. **Never an average.** Averaging dimensions lets a spectacular cost saving
   cancel out a failing quality score, which is precisely the error this tool
   exists to prevent. If a configuration fails on any dimension you said
   mattered, it failed.
2. **`unmeasured` is not a pass.** If the policy routed nothing to the weak
   model, `quality.routed_weak_mean` has no value — and a rubric that quietly
   skipped it would report a clean pass for a run that tested nothing. Silence is
   not evidence.

`instar route` exits `1` on a `fail`, so CI can refuse to publish a report whose
own rubric rejected it.

---

## 5. A worked example, with real numbers

A real run: 24 synthetic support tickets, classified by a self-hosted
Qwen2.5-3B against a Qwen2.5-7B baseline, scored by exact match against gold
labels — no LLM judge involved. Priced with an amortized self-hosted rate
derived from measured throughput.

```
policy=feature_category  saved=44.6%  q_weak=0.917  weak=24/24
rubric=support-triage-v1  verdict=FAIL
  MARGINAL   accuracy                 0.9167
  FAIL       worst_case_accuracy      0
  PASS       savings                  44.58
  PASS       latency                  1,115
  PASS       run_integrity            0
```

Three of five dimensions passed comfortably. The headline saving — 44.6% — is
real and substantial. **The rubric still returned FAIL**, because two tickets out
of 24 scored zero, and the rubric said no routed call should.

That is the machinery working. Read only the aggregates and this looks like a
clear win. The rubric's job was to hold the line that a 45% saving does not buy
you the right to misfile tickets.

### The caveat that outranks the verdict

Both failures deserve inspection, and inspecting them changes the conclusion:

| Ticket | Gold label | Model said | Assessment |
|---|---|---|---|
| `triage-005` | `billing` | `feature_request` | The ticket asks *"How do we get the PO field added to future invoices?"* That is a feature request. **The model is arguably more right than the label.** |
| `triage-022` | `account_access` | `bug_report` | SSO stopped accepting logins. Genuinely both. A human triager could defend either. |

So the measured 91.7% **understates the model**, and the honest conclusion is
not "the 3B failed" but "this fixture cannot distinguish a 3B from a 7B above
roughly 92%, because its labels are not that precise."

**A classification measurement is capped by the consistency of its labels.** You
cannot measure a model above the rate at which your own annotators agree with
each other. Before concluding a model is not good enough, check whether the
ceiling you hit was the model's or the label set's — and if you have never
measured inter-annotator agreement on your gold labels, you do not yet know
which one you are looking at.

This is why the per-call table exists, and why the judge's reasoning is recorded
on every row. An aggregate would have sent you to a bigger model. The rows send
you to the label set.

### A second finding worth generalizing

Latency barely moved: p95 of 1,115 ms for the 3B against 1,190 ms mean for the
7B, about 1.12x. But raw generation throughput measured 121 tok/s against
67 tok/s, about 1.8x.

Both are true. Classification emits two or three tokens, so per-request overhead
dominates and the smaller model's speed advantage never shows up in latency. The
win is in **throughput and cost**, not response time. If you are choosing a
small model to make a user-facing feature feel faster, measure that specifically
— on short outputs it may not.

---

## 6. Where rubrics fit the engagement

`Planning/Engagement-Methodology.md` phase **B** produces *"a rubric spec Instar
can execute, plus a threshold table signed off by the phase-A stakeholder."*
This is that spec.

The split it describes holds here:

- **Dimensions** — what to hold the configuration to. Partly technical.
- **Thresholds** — where the bar sits. A business decision, and usually a
  different stakeholder's.

Phase B is also where the loop most often starts. If analysis shows the
dimensions do not capture what actually matters, the answer is to revise the
rubric and re-run — not to reinterpret the verdict. Version the rubric name
(`support-triage-v1`, `-v2`) so a report always says which bar it was judged
against.

---

## 7. What Instar ships, and what is yours

Instar ships the rubric **framework** — the format, the validation, the metric
bindings, the verdict rules. It does **not** ship a library of rubrics.

`Engineering/fixtures/rubrics/support-triage-v1.json` is an illustration of the
format. **Its thresholds are not a recommendation.** What "good enough" means
depends entirely on what happens downstream of a wrong answer, which only you
know: a misfiled support ticket costs a re-route, a wrong extraction on a
financial document costs something else entirely.

Per-department rubrics — what good means for marketing copy, for finance
extraction, for operations latency — are the property of whoever develops them
and do not belong in this repository. See `../../CLAUDE.md` §IP boundary.

---

## 8. Writing your first rubric

1. **Start from the decision, not the metrics.** What will you do differently
   depending on the outcome? A dimension that would not change anyone's action
   is noise.
2. **Name the failure you are afraid of**, and bind a dimension to it. "The
   cheap model quietly mangles one category" becomes
   `quality.routed_weak_min`.
3. **Set thresholds with the person who owns the consequence**, and record the
   reasoning in `rationale` while you still remember it.
4. **Always include `run.error_count` at `pass_at: 0`.**
5. **Run it in mock mode first.** The verdict is meaningless — mock numbers are
   placeholders — but it proves the rubric loads, binds, and renders before you
   spend anything.
6. **Then run it live, and read the per-call rows before the verdict.** As §5
   shows, the rows are where you find out whether you measured the model or your
   own label set.

## See also

- [`04-RUNBOOK.md`](04-RUNBOOK.md) — running Instar end to end.
- [`05-PROVIDERS.md`](05-PROVIDERS.md) — connecting the models you want to measure.
- [`10-CODE-OVERVIEW.md`](10-CODE-OVERVIEW.md) — `instar.rubrics.spec` internals.
