# Case study: when the verdict was wrong to trust

> **What this teaches.** A rubric returns a clean, mechanical verdict — and this
> is a real run where that verdict said **FAIL** and following it would have been
> a mistake. The lesson is not "rubrics are unreliable." It's the opposite: the
> verdict did its job, and *then a human did theirs* by reading the rows behind
> it. This is the judgment no tool can automate, shown on real numbers.
>
> If [`09-LESSON-Rubrics-Hands-On.md`](09-LESSON-Rubrics-Hands-On.md) taught you how a
> verdict is produced, this teaches you when not to stop at it.

---

## The setup

A concrete, cheap question: **can a small self-hosted model do support-ticket
triage as well as a larger one?** If yes, the larger model is wasted money on
this workload.

- **Workload:** 24 synthetic support tickets, each to be labeled as one of
  `billing`, `bug_report`, `feature_request`, `how_to`, `account_access`,
  `cancellation`.
- **Baseline (the "strong" model):** Qwen2.5-7B.
- **Candidate (the "weak" model):** Qwen2.5-3B — less than half the size.
- **Both self-hosted** on an 8 GB consumer GPU via Ollama. No API, no per-token
  bill; you pay for the GPU whether it's busy or not.
- **Scoring:** exact match against a known correct label. Classification is the
  one case that needs *no* LLM judge — the answer is right or wrong against a
  gold label, which makes the result objective and cheap. (This is exactly why
  a classification workload is the best possible *first* thing to measure.)
- **Pricing:** self-hosted models have no list price, so we derived an amortized
  rate from measured throughput — about `$0.83/Mtok` for the 7B and `$0.46` for
  the 3B — and passed it in. Without a real price every call would cost `$0` and
  the savings figure would be meaningless.

## The run

```
policy=feature_category   saved=44.6%   routed-weak quality=0.917   weak=24/24
rubric=support-triage-v1  verdict=FAIL
  MARGINAL   accuracy               0.9167
  FAIL       worst_case_accuracy    0
  PASS       savings                44.58
  PASS       latency                1,022
  PASS       run_integrity          0
```

The saving is real and large: 44.6%, on a workload where the smaller model runs
for less than half the cost per token. Latency and run integrity passed clean.
Accuracy landed at 91.7% — a `MARGINAL`.

But `worst_case_accuracy` scored `0` and failed, and because the overall verdict
is the worst dimension, **the run failed.** The rubric had a dimension binding
`quality.routed_weak_min` at `pass_at: 1.0` — *no single ticket may score zero* —
and two tickets did.

Read only this block, and the decision writes itself: **don't switch.** The 3B
mangled tickets the 7B presumably wouldn't have.

That decision would have been wrong.

## Reading the rows

The verdict points at *which* dimension failed. It does not tell you *why*, and
the "why" is the only thing that actually decides anything. Two tickets scored
zero, so look at those two tickets.

**Ticket `triage-005`** — the correct label on file was `billing`. The 3B said
`feature_request`. Here is the ticket:

> *"Our purchase order number is missing from the last two invoices, so accounts
> payable will not process them. How do we get the PO field added to future
> invoices?"*

Read the last sentence. The customer is asking how to get a field **added to
future invoices**. That is a feature request. The model's answer is at least as
defensible as the label it was marked wrong against — arguably *more* so.

**Ticket `triage-022`** — the correct label was `account_access`. The 3B said
`bug_report`:

> *"Our single sign-on integration stopped accepting logins this morning. Users
> get sent back to the login page with no error message. Direct password login
> still works."*

An authentication system broke. Is that an access problem or a bug? Genuinely
both. A human triager could file it either way and defend the choice.

**Neither "failure" is a model error.** Both are tickets where the gold label is
debatable and the model picked a reasonable alternative. The measured 91.7%
*understates* the model, and the two zeros that failed the whole run were the
label set's fault, not the 3B's.

## The lesson that generalizes

Here is the rule to carry out of this, well beyond triage:

> **A classification measurement cannot exceed the consistency of its own labels.**

You cannot measure a model as more accurate than the rate at which your own
annotators agree with each other. If two reasonable people would label a ticket
differently, then *no* model can score 100% against one of their answers — and
the ceiling you hit is a fact about your labels, not about the model.

The practical consequences:

- **Before you conclude a model "isn't good enough," check whether you hit the
  model's ceiling or your label set's.** If you have never measured how often
  your own people agree on these labels, you do not yet know which one you're
  looking at.
- **This is why the per-call rows and the judge's reasoning exist.** An aggregate
  alone — "worst case: fail" — would have sent this team to buy a bigger model
  they did not need. The rows sent them to fix two labels instead. One of those
  is a procurement decision; the other is an afternoon.
- **The rubric was still right to fail the run.** Its job was to stop a `44.6%`
  saving from waving through a run with a zero in it. It did that. The failure
  was the correct trigger for an investigation — it just wasn't the correct
  *conclusion*. Verdict starts the inquiry; it doesn't end it.

## A second finding, almost missed

Look again at the passing `latency` line — `1,022`. It's easy to skate past a
dimension that passed. But there's a genuine surprise hiding in it.

In an isolated throughput test, the 3B generated **121 tokens/second against the
7B's 67** — about **1.8x faster**. You would expect the workload to feel roughly
that much snappier. It didn't:

| Model | end-to-end latency (mean) |
|---|---|
| 7B (baseline) | 1,114 ms |
| 3B (candidate) | 964 ms |

Only about **1.16x** — nothing like 1.8x. Why? Because a triage answer is *two or
three tokens long* (`billing`, `account_access`). When the output is that short,
the fixed per-request overhead — loading the prompt, the network hop, scheduling
— dwarfs the actual token generation, and the small model's speed advantage never
gets a chance to show up.

The generalizable point:

> **The small model's win here is throughput and cost, not response time.**

If you were choosing a small model specifically to make a *user-facing* feature
feel faster, this run would mislead you unless you read it carefully. On short
outputs, "faster to generate" and "faster to respond" are different claims, and
only one of them was true here. On long-generation workloads (summaries, drafts)
the 1.8x *would* show up in latency — measure the workload you actually have.

## What to take away

1. **A verdict is where an investigation starts, not where a decision ends.**
   Read the rows behind any FAIL before you act on it.
2. **A classification score is capped by label quality.** Measure your
   annotators' agreement before you blame a model for a ceiling.
3. **"Faster to generate" ≠ "faster to respond."** On short outputs, per-request
   overhead dominates; the model's raw speed may never reach the user.
4. **None of this is an argument against rubrics.** The rubric did precisely what
   it should: it refused to let a large saving hide a zero, and it forced a human
   to look. The human looking is the part that can't be automated — which is the
   whole reason the tool surfaces the rows and not just the verdict.

## Try it yourself

Every number here is reproducible on modest hardware. See
[`06-PROVIDERS.md`](06-PROVIDERS.md) for standing up the two Qwen models locally, then
run the workload with `--rubric Engineering/fixtures/rubrics/support-triage-v1.json`.
For the mechanics of the rubric itself, start with
[`09-LESSON-Rubrics-Hands-On.md`](09-LESSON-Rubrics-Hands-On.md).
