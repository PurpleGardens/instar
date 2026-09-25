# How to Create Rubrics for Your AI Spend

> **A guide, not a reference.** [`07-RUBRICS.md`](07-RUBRICS.md) is the reference — it
> lists every field, every metric, and the exact verdict rules. This guide
> teaches the harder part: **how to arrive at a rubric that actually decides
> your question**, and how it behaves once you run it. Read this to learn the
> method; keep `07-RUBRICS.md` open for the field list.

---

## What you'll be able to do after reading this

- State an AI-spend decision precisely enough that a rubric can settle it.
- Turn that decision into dimensions, metrics, and thresholds you can defend to
  the person who signs off on it.
- Choose threshold numbers from real sources instead of guessing.
- Run the rubric, read the verdict correctly, and know when the verdict is
  telling you about the model versus about your own test.
- Avoid the four mistakes that make a rubric lie to you.

---

## Part 1 — The one idea

**A rubric turns a measurement into a decision.**

A run gives you numbers: `saved=44.6%, quality=0.917, p95=1,022ms`. Those are
not an answer to "should we move this workload to the cheaper model." They
become an answer only once someone has said what "good enough" was going to be —
*before seeing them.*

That timing is the whole game. `91.7%` accuracy feels fine if you were hoping for
90 and feels alarming if you were hoping for 99, and the number itself cannot
tell you which you were hoping for. If you decide the bar after the run, you will
decide it — without meaning to — to match the result you already have. A rubric
is the artifact that pins the bar down in advance, in writing, signed by whoever
owns the outcome, so the run's conclusion is reproducible by someone who wasn't
in the room.

Everything else in this guide is in service of that one idea. When a choice is
unclear, ask: *does this make the decision more honest, or less?*

---

## Part 2 — Anatomy of a dimension

A rubric is a JSON file. Its heart is a list of **dimensions**. One dimension is
one thing you are holding the configuration to:

```json
{
  "id": "accuracy",
  "label": "Label accuracy on tickets moved to the cheap model",
  "metric": "quality.routed_weak_mean",
  "direction": "higher_is_better",
  "pass_at": 0.95,
  "marginal_at": 0.90,
  "rationale": "Below 0.90 a human re-checks every ticket, which erases the saving."
}
```

Take each field in turn, because each encodes a decision:

- **`metric`** — *which measured number this dimension watches.* This is where
  you bind an abstract worry ("the cheap model might mangle tickets") to
  something the run actually produces (`quality.routed_weak_mean`). The metric
  name must be one Instar knows; a typo is refused at load time rather than
  becoming a silently missing row. Part 6 is a map from intent to metric.

- **`direction`** — `higher_is_better` (the default) or `lower_is_better`.
  Accuracy and savings go up; latency and error count go down. Getting this
  wrong inverts the whole dimension, so it is worth a half-second's thought each
  time.

- **`pass_at`** — *the bar.* Meeting it exactly is a pass. This is the number
  everyone will argue about, which is exactly why writing it down in advance is
  valuable. Part 4 is entirely about where this number legitimately comes from.

- **`marginal_at`** — *optional second bar* for "usable, but clearly worse."
  Omit it and the dimension is strictly pass-or-fail. Include it when there is a
  real middle ground — an answer good enough to ship under supervision but not
  good enough to trust unattended. On a `higher_is_better` dimension it must sit
  at or below `pass_at`; put it on the wrong side and the rubric is refused,
  because a `marginal` band that can never trigger is always a typo.

- **`label`** — the human wording that appears in the report. Defaults to `id`.
  Write it for the stakeholder who will read the report, not for yourself.

- **`rationale`** — *why this threshold.* Optional to the parser, essential to
  the process. A threshold with no recorded reasoning will be re-litigated in
  three months by people who weren't there, using worse information than you have
  right now. Write the sentence while you still remember it.

The `id` is just a stable handle; keep it short and unique within the rubric.

---

## Part 3 — The method: from decision to rubric in six steps

This is the part to actually learn. The mechanics above take five minutes; this
is where a good rubric is separated from a decorative one. Work these steps in
order — the order matters, because each one constrains the next.

### Step 1 — Write the decision as one sentence

Not "evaluate the cheap model." A decision, with an action attached:

> *"Move support-ticket triage from the 7B model to the 3B model, if it stays
> accurate enough that we don't add human re-checking."*

If you cannot finish the sentence with a concrete action ("move," "keep,"
"self-host," "ship"), you are not ready to write a rubric — you are still
exploring, and exploration wants a plain run, not a verdict.

### Step 2 — List what would make you say no

Force yourself to name the failures you are actually afraid of. For triage:

- *"It quietly mislabels a whole category of ticket."*
- *"It's accurate on average but tanks on the tickets that matter most."*
- *"The saving is real but so small it's not worth running a second model."*
- *"Half the calls fail and the numbers only describe the ones that worked."*

Each fear is a candidate dimension. A rubric is, precisely, the list of ways you
have agreed the thing could fail. If a dimension does not correspond to a fear
that would change your action, it is noise — cut it.

### Step 3 — Bind each fear to a metric

Now translate. This is a mechanical lookup once the fears are named:

| Fear | Metric |
|---|---|
| mislabels a whole category | `quality.routed_weak_min` (the single worst call) |
| accurate on average, bad where it counts | `quality.routed_weak_mean` **and** `..._min` together |
| saving too small to bother | `cost.saved_pct` |
| numbers describe only the calls that worked | `run.error_count` |

Notice two fears map to two metrics on the *same* underlying quality — a mean and
a minimum. That pairing is deliberate and Part 7 explains why a mean alone will
lie to you.

### Step 4 — Set the pass bar for each

The hard step, and it has its own section (Part 4). The short version: a
threshold should trace back to a *consequence* — the cost of being wrong, an
existing SLA, the human baseline you're replacing, or a break-even calculation —
not to a number that felt round.

### Step 5 — Decide where `marginal` sits, if anywhere

Ask, for each dimension: *is there a band where the answer is "usable but I'd
want a human watching"?* If yes, that's `marginal_at`. If the dimension is
genuinely binary — a call failed or it didn't; the saving cleared break-even or
it didn't — leave `marginal_at` out and let it be pass/fail.

### Step 6 — Add the integrity guard

Always include this dimension, in every rubric you ever write:

```json
{
  "id": "run_integrity",
  "label": "No failed calls",
  "metric": "run.error_count",
  "direction": "lower_is_better",
  "pass_at": 0,
  "rationale": "Failed calls are excluded from every other number here; a run with any is describing only the calls that happened to work."
}
```

Failed calls are dropped from every aggregate in the report. A run where a third
of the calls errored can post excellent quality and cost numbers that describe
only the two-thirds that succeeded. This dimension makes that impossible to miss.
It costs one paragraph and it has saved people from shipping a decision built on
a broken run.

---

## Part 4 — Where a threshold number actually comes from

"What do I put for `pass_at`?" is the question that stalls everyone. The wrong
answer is a round number that feels safe. Here are the legitimate sources, best
first.

> **Often the number isn't yours to invent.** The threshold is a business
> judgment owned by whoever owns the outcome — and that is frequently a different
> person from the one writing the rubric. If that's you, or if you need to brief
> them, [`03-GUIDE-Setting-the-Bar.md`](03-GUIDE-Setting-the-Bar.md) is the one-page,
> jargon-free version to hand them. Your job here is to turn the numbers they
> give you into the dimensions below.

### 1. The downstream cost of being wrong

The strongest source. Trace the error to money:

> A misfiled ticket costs a support agent ~10 minutes to catch and re-route. At
> 10,000 tickets/day, each **1%** of misclassification is 100 tickets ≈ **16.7
> agent-hours/day**. If the cheap-model saving is worth ~8 agent-hours/day, then
> anything worse than ~99.5% accuracy spends more than it saves.

Now `pass_at` is not a feeling; it's the break-even between the saving and the
cost of the errors the saving causes. Even a rough version of this calculation
beats a confident guess.

### 2. The baseline you are actually replacing

You are not competing with perfection. You are competing with *what happens
today.* If human triagers agree with each other only ~94% of the time, demanding
99% of a model is incoherent — and worse, **you cannot even measure above your
own label consistency** (Part 5 shows exactly this happening). Set the bar
relative to the incumbent, and if the incumbent is human, measure the humans'
agreement before you set it.

### 3. An SLA that already exists

Sometimes the number is inherited, not chosen. If a user-facing feature already
promises p95 < 2 seconds, that *is* your latency bar — it is not up for
negotiation, and the rubric's job is simply to enforce a commitment already
made:

```json
{
  "id": "latency_sla",
  "metric": "latency.p95_ms",
  "direction": "lower_is_better",
  "pass_at": 2000,
  "rationale": "Inherited from the existing product SLA for this surface; not negotiable here."
}
```

### 4. Break-even economics for the savings bar

The savings threshold is not arbitrary either. Below the volume where a second
model's operational overhead pays for itself, any saving is illusory. Instar
ships `instar.core.cost.breakeven_requests_per_month()` for exactly this; use its
output to justify the number you put in `cost.saved_pct`.

### 5. Stakeholder risk tolerance — made explicit and signed

Sometimes the number is a genuine judgment call with no formula behind it. That
is fine — but then the rubric's job is to *force the owner to name it and put
their name on it*, so it becomes a decision on record rather than a drift nobody
chose. Write the person and the reasoning into `rationale`.

### When you honestly don't know

Start with a defensible placeholder, name the rubric `-v1`, run it, and use the
*distribution you observe* to calibrate `-v2`. This is legitimate. What is not
legitimate is moving the bar *within a version* to make a run pass — that is the
after-the-fact rationalization the whole exercise exists to prevent. Bump the
version number; never quietly edit the number.

---

## Part 5 — How a rubric is utilized

You've defined it. Here is its whole life.

### Attaching it to a run

```bash
instar route --live \
  --traffic support-triage.jsonl \
  --catalog catalog.json \
  --pricing pricing.json \
  --rubric support-triage-v1.json \
  --strong-url http://localhost:11434 --strong-model qwen2.5:7b-instruct-q4_K_M \
  --weak-url   http://localhost:11434 --weak-model  qwen2.5:3b-instruct-q4_K_M \
  --policy feature_category
```

The rubric runs *after* the measurement, against the completed result. It never
touches what the models do — it only reads what they produced. That separation is
why you can apply a new rubric to an old run's data without re-spending a token.

### How the verdict is computed

Each dimension resolves to one of four values:

| Verdict | When |
|---|---|
| `pass` | met `pass_at` |
| `marginal` | missed `pass_at` but met `marginal_at` |
| `fail` | missed both |
| `unmeasured` | the run produced no value for this metric |

The overall verdict is the **worst** of them, on the ordering
`fail < unmeasured < marginal < pass`. It is a *minimum*, never an average — see
Part 7 for why that is non-negotiable.

The `unmeasured` case is subtle and important. If your policy routed nothing to
the weak model, then `quality.routed_weak_mean` has no value to check. A rubric
that quietly skipped it would report a clean pass for a run that tested nothing.
Instead the dimension returns `unmeasured`, which drags the overall verdict down
and prints a note. Silence is not evidence.

### What you get back

On the terminal, the verdict and each dimension:

```
rubric=support-triage-v1  verdict=FAIL
  MARGINAL   accuracy                 0.9167
  FAIL       worst_case_accuracy      0
  PASS       savings                  44.58
  PASS       latency                  1,022
  PASS       run_integrity            0
```

In the report (`report.md`), the verdict table sits at the **top** — the
decision is what the reader came for, and burying it under the raw numbers just
invites them to find their own story in the data. The full per-call table sits
below it.

And in the **exit code**: a `fail` exits `1`. This is what lets a rubric gate a
pipeline — CI can refuse to publish a report that its own rubric rejected. A
`marginal` or `pass` exits `0`.

### The habit that matters most: read the rows before the verdict

The verdict is a summary, and summaries can mislead about *why*. Before you act
on a `fail`, read the per-call rows — especially the ones that scored badly. Part
5's real example is entirely about a case where the rows told a completely
different story than the verdict did.

### Versioning and the loop

Name rubrics with a version: `support-triage-v1`, `-v2`. When analysis shows the
dimensions didn't capture what actually mattered — a routine and expected outcome
— you revise the rubric and re-run. You do **not** reinterpret the old verdict.
The version number in the report always says which bar a run was judged against,
which keeps the history honest as your understanding improves.

---

## Part 6 — Which metric for which intent

The full list is in `07-RUBRICS.md`. This is the part that matters when you're
choosing: *what do you actually mean, and which metric means that?*

| You want to know... | Use | Not |
|---|---|---|
| how good the cheap model was on the calls you moved | `quality.routed_weak_mean` | `quality.mean_all` |
| whether any single moved call was a disaster | `quality.routed_weak_min` | any mean |
| whether the whole run was good enough to ship as-is | both of the above, together | either alone |
| how much you saved | `cost.saved_pct` | `cost.saved_usd` (unless the absolute dollar figure is the point) |
| whether a user waits too long | `latency.p95_ms` or `p99_ms` | `latency.mean_ms` (a mean hides the slow tail users remember) |
| whether the run is even trustworthy | `run.error_count` | — always include this |
| whether enough traffic was actually tested on the weak model | `run.weak_share_pct` | — |

Two traps encoded in that table:

- **`quality.mean_all` almost never belongs in a rubric.** Calls kept on the
  strong model score 1.0 by definition. On a workload that routes 10% of traffic,
  `mean_all` stays above 0.9 no matter how badly those 10% went. Read
  `routed_weak_mean` — the calls you actually changed are where the risk is.
- **A latency *mean* hides the tail.** Users remember the slowest requests, not
  the average one. Bind p95 or p99, not the mean, whenever a human is waiting.

---

## Part 7 — The four mistakes that make a rubric lie

Each of these produces a rubric that passes runs it should fail. They are worth
memorizing as a checklist.

**1. Averaging the dimensions.** If you compute an overall score by averaging,
a spectacular cost saving cancels out a failing quality score, and the exact
disaster this tool exists to prevent sails through. Instar refuses to do this —
the overall verdict is the worst dimension — but the same error creeps in
manually when someone eyeballs a report and thinks "well, four of five passed."
Four of five passing is a *fail* if the fifth was one you said mattered.

**2. Trusting a mean without a minimum.** A mean of 0.97 can be twenty-three
perfect calls and one catastrophe. On a classification workload, that one
catastrophe is often an entire category the small model cannot recognize.
Always pair `quality.routed_weak_mean` with `quality.routed_weak_min`.

**3. Forgetting the integrity guard.** Without `run.error_count` at `pass_at: 0`,
a run where half the calls failed can post beautiful aggregates describing only
the surviving half. This is the most dangerous omission because the report looks
*better*, not worse.

**4. Setting the bar after the run.** Everything else is mechanics; this is the
one that corrupts the whole enterprise. If you find yourself adjusting `pass_at`
after seeing the result, stop. Either you've learned something real about the
decision (then bump the version and write down what you learned) or you're
rationalizing (then don't). The rubric only has value because it was fixed before
the evidence.

---

## Part 8 — A live example where the verdict was wrong to trust

This is the case that teaches more than any rule. Real run: 24 support tickets,
a self-hosted 3B classifier against a 7B baseline, scored by exact label match.

```
saved=44.6%  routed-weak quality=0.917  verdict=FAIL
  MARGINAL   accuracy                 0.9167
  FAIL       worst_case_accuracy      0      <- two tickets scored zero
  PASS       savings                  44.58
  PASS       latency                  1,022
  PASS       run_integrity            0
```

The rubric did its job: three dimensions passed, but two tickets scored zero and
the rubric said none should, so the verdict is `FAIL`. Read only the aggregates
and this is a clear "don't switch."

Then you read the two failing rows — the habit from Part 5 — and the story
inverts:

| Ticket | Gold label | Model said | Reality |
|---|---|---|---|
| `triage-005` | `billing` | `feature_request` | The ticket asks *"how do we get the PO field **added** to future invoices?"* That **is** a feature request. The model is arguably more right than the label. |
| `triage-022` | `account_access` | `bug_report` | SSO stopped accepting logins. Genuinely both; a human triager could defend either. |

Neither "failure" is a model error. The measured 91.7% *understates* the model,
and the real ceiling was set by the quality of the labels, not the capability of
the model.

**The lesson generalizes into a rule:** a classification measurement cannot
exceed the consistency of its own labels. You cannot measure a model above the
rate at which your own annotators agree with each other. If you have never
measured inter-annotator agreement on your gold set, you do not yet know whether
a `fail` is telling you about the model or about your labels — and the per-call
rows are the only place you'll find out. An aggregate alone would have sent this
team to buy a bigger model they didn't need.

This is *why* rubrics report the deciding number and the judge's reasoning on
every row. The verdict starts the investigation; it does not end it.

---

## Part 9 — Three rubrics across three workload shapes

The pattern generalizes. Here is the shape of a rubric for three different kinds
of workload, so you can see what changes and what doesn't.

### A — Classification, background (the triage shape)

Objective scoring against gold labels; nobody's watching a spinner, so latency is
generous and the whole game is accuracy-without-a-disaster.

```json
{
  "name": "triage-v1",
  "dimensions": [
    {"id": "accuracy", "metric": "quality.routed_weak_mean", "direction": "higher_is_better", "pass_at": 0.95, "marginal_at": 0.90},
    {"id": "worst_case", "metric": "quality.routed_weak_min", "direction": "higher_is_better", "pass_at": 1.0},
    {"id": "savings", "metric": "cost.saved_pct", "direction": "higher_is_better", "pass_at": 40, "marginal_at": 20},
    {"id": "integrity", "metric": "run.error_count", "direction": "lower_is_better", "pass_at": 0}
  ]
}
```

### B — Generation, foreground-with-review (marketing copy)

A human sees and edits the output, so a `marginal` answer that triggers a re-edit
is a real (hidden) cost — the `marginal` band earns its keep here. There are no
gold labels, so quality comes from an LLM judge. Latency matters but a person is
already in the loop, so it's a soft bar.

```json
{
  "name": "campaign-copy-v1",
  "dimensions": [
    {"id": "shippable", "metric": "quality.routed_weak_mean", "direction": "higher_is_better", "pass_at": 0.90, "marginal_at": 0.75,
     "rationale": "PASS=ship as-is, MARGINAL=likely a re-edit (a hidden cost), FAIL=redo."},
    {"id": "no_disaster", "metric": "quality.routed_weak_min", "direction": "higher_is_better", "pass_at": 0.5,
     "rationale": "No routed piece may be off-brand enough to need a full redo."},
    {"id": "savings", "metric": "cost.saved_pct", "direction": "higher_is_better", "pass_at": 30, "marginal_at": 15},
    {"id": "tested_enough", "metric": "run.weak_share_pct", "direction": "higher_is_better", "pass_at": 20,
     "rationale": "If the policy barely routed anything weak, this run didn't really test the cheap model."},
    {"id": "integrity", "metric": "run.error_count", "direction": "lower_is_better", "pass_at": 0}
  ]
}
```

### C — Latency-sensitive, foreground (a live assistant)

A person is waiting on every token. Latency is now a hard bar and the tail is
what they feel; savings are secondary to not making the feature sluggish.

```json
{
  "name": "assistant-v1",
  "dimensions": [
    {"id": "quality", "metric": "quality.routed_weak_mean", "direction": "higher_is_better", "pass_at": 0.95, "marginal_at": 0.85},
    {"id": "tail_latency", "metric": "latency.p99_ms", "direction": "lower_is_better", "pass_at": 1500, "marginal_at": 2500,
     "rationale": "Users feel the slowest requests, not the average; inherited from the surface's SLA."},
    {"id": "typical_latency", "metric": "latency.p50_ms", "direction": "lower_is_better", "pass_at": 800},
    {"id": "savings", "metric": "cost.saved_pct", "direction": "higher_is_better", "pass_at": 25},
    {"id": "integrity", "metric": "run.error_count", "direction": "lower_is_better", "pass_at": 0}
  ]
}
```

What stayed constant across all three: an integrity guard, a quality mean, and a
saving floor. What changed: the presence of a worst-case guard, the tightness of
latency, whether a `marginal` band exists, and every threshold number — because
every one traces to a different consequence.

---

## Part 10 — A worksheet

Copy this, fill it in *before* you run anything.

```
Decision (one sentence, with an action): ________________________________

If it succeeds I will:  ____________   If it fails I will:  ____________

Failures I'm afraid of        ->  Metric                    ->  pass_at  ->  why (source)
1. ________________________   ->  ______________________    ->  _______  ->  __________
2. ________________________   ->  ______________________    ->  _______  ->  __________
3. ________________________   ->  ______________________    ->  _______  ->  __________

Integrity guard: run.error_count, pass_at 0                    [ ] included
Quality: is there a worst-case guard (routed_weak_min)?        [ ] yes  [ ] N/A
Latency: bound p95/p99, not mean, if a human waits?            [ ] yes  [ ] N/A
Every threshold traces to a consequence, not a round number?   [ ] yes
Signed off by the outcome owner?                               [ ] yes
Version number in the name?                                    [ ] yes

Then: run in mock first (proves it loads) -> run live -> read the failing rows
BEFORE trusting the verdict.
```

---

## See also

- [`07-RUBRICS.md`](07-RUBRICS.md) — the field-and-metric reference.
- [`05-RUNBOOK.md`](05-RUNBOOK.md) — running Instar end to end.
- [`06-PROVIDERS.md`](06-PROVIDERS.md) — connecting the models you're deciding between.
- `Planning/Engagement-Methodology.md` §B — where rubric definition sits in a
  full evaluation, and why dimensions and thresholds usually come from different
  people.
