# Rubrics, hands-on

> **Learn by doing, in about ten minutes.** You will run four commands, change
> one number, and watch a pass/fail verdict flip in front of you. Everything here
> runs in **mock mode** — deterministic, no API key, no network, no spend — so
> the output you see will match the output printed in this lesson exactly. Nothing
> you do here costs anything or touches a real model.
>
> If you have read [`07-GUIDE-Creating-Rubrics.md`](07-GUIDE-Creating-Rubrics.md), this
> makes it concrete. If you haven't, this is a fine place to start — the ideas
> will land better after you've felt them move.

## Before you start

```bash
pip install -e ".[dev]"
```

Run everything below from the repository root. Mock mode is the default, so no
flag turns it on. Each command writes its full report to `runs/<label>/` — but in
this lesson we only read the short summary the command prints to your terminal.

---

## Step 1 — A measurement is not a decision

Run the workload with no rubric:

```bash
instar route \
  --traffic Engineering/fixtures/support-triage.jsonl \
  --catalog Engineering/fixtures/catalogs/example-departments.json
```

You'll see:

```
policy=feature_category  saved=95.0%  q_all=0.968  q_weak=0.968  weak=24/24
```

Read that out loud: *we routed all 24 support tickets to the cheap model, saved
95% of the spend, and quality on those calls was 0.968.*

Now answer the question that matters: **should we make this change?**

You can't. Not from that line. 0.968 is good if you needed 0.95 and a disaster if
you needed 0.99, and the number can't tell you which you needed. That is the
whole reason rubrics exist — a measurement gives you numbers; a rubric turns them
into a decision. Let's add one.

---

## Step 2 — Add a rubric, get a verdict

Instar ships an example rubric for exactly this workload. Add it with `--rubric`:

```bash
instar route \
  --traffic Engineering/fixtures/support-triage.jsonl \
  --catalog Engineering/fixtures/catalogs/example-departments.json \
  --rubric Engineering/fixtures/rubrics/support-triage-v1.json
```

Now the same numbers come with a verdict:

```
rubric=support-triage-v1  verdict=FAIL
  PASS       accuracy                 0.9685
  FAIL       worst_case_accuracy      0.933
  PASS       savings                  95.04
  PASS       latency                  8
  PASS       run_integrity            0
```

Four of five dimensions passed. The verdict is still **FAIL**. Sit with that for
a second, because it's the single most important behavior in the whole system:

> **The overall verdict is the *worst* dimension, never an average.**

`accuracy` passed at 0.9685. `savings` passed at 95%. None of that rescues the
run, because `worst_case_accuracy` failed — and this rubric was told that mattered.
A great average must not be allowed to hide a failure on a dimension you said you
cared about. (In a moment you'll see exactly why that rule protects you.)

**Open the rubric and see why it failed.** It's plain JSON:

```bash
cat Engineering/fixtures/rubrics/support-triage-v1.json
```

Find the `worst_case_accuracy` dimension. It binds the metric
`quality.routed_weak_min` — the single worst call in the run — with
`"pass_at": 1.0`. It's saying: *no individual ticket may score zero.* The run's
worst call scored 0.933, so the dimension failed, so the run failed.

---

## Step 3 — Change one number, watch the verdict move

This is the part to actually feel. Make your own copy of the rubric so you don't
disturb the shipped one:

```bash
cp Engineering/fixtures/rubrics/support-triage-v1.json my-rubric.json
```

Open `my-rubric.json` and find `worst_case_accuracy`. Change its `pass_at` from
`1.0` to `0.9`:

```json
{
  "id": "worst_case_accuracy",
  "metric": "quality.routed_weak_min",
  "direction": "higher_is_better",
  "pass_at": 0.9
}
```

Run it again, pointing at your edited copy:

```bash
instar route \
  --traffic Engineering/fixtures/support-triage.jsonl \
  --catalog Engineering/fixtures/catalogs/example-departments.json \
  --rubric my-rubric.json
```

```
rubric=support-triage-v1  verdict=PASS
  PASS       accuracy                 0.9685
  PASS       worst_case_accuracy      0.933
  PASS       savings                  95.04
  PASS       latency                  8
  PASS       run_integrity            0
```

The verdict flipped from FAIL to PASS. **The numbers from the run did not change
at all** — the same 0.933 that failed a moment ago now passes. All you moved was
the bar.

---

## Step 4 — The trap you just walked into

Stop and notice what happened. You had a failing result. You changed a threshold.
Now it passes. If your goal was to ship this change, you just "succeeded" — and
you learned nothing, because *you moved the target to wherever the arrow already
landed.*

This is the one mistake that corrupts the whole exercise, and it is disturbingly
easy to make by accident. A rubric only means something if the bar was set
**before** you saw the result. Lowering it afterward doesn't make the model
better; it makes your standard dishonest.

So which of the two runs was right — FAIL at 1.0, or PASS at 0.9?

**Neither this lesson nor the tool can tell you.** That's a business judgment: is
one ticket in twenty-four scoring badly acceptable for this workload, or not?
Whoever owns support triage decides that — see
[`02-GUIDE-Setting-the-Bar.md`](02-GUIDE-Setting-the-Bar.md) — and they decide it
*before* the run. If, having seen this result, they genuinely conclude 0.9 was
always the right bar, the honest move is to save it as a **new version**
(`support-triage-v2`) with a written reason, not to quietly edit v1 until it
passes. The version number is how the change stays honest.

---

## Step 5 — When a dimension has nothing to measure

One more behavior worth seeing. Run the shipped rubric again, but this time force
the *control* policy — `all_strong` keeps every call on the expensive model and
routes nothing to the cheap one:

```bash
instar route \
  --traffic Engineering/fixtures/support-triage.jsonl \
  --catalog Engineering/fixtures/catalogs/example-departments.json \
  --rubric Engineering/fixtures/rubrics/support-triage-v1.json \
  --policy all_strong
```

```
rubric=support-triage-v1  verdict=FAIL
  UNMEASURED accuracy                 not measured
  UNMEASURED worst_case_accuracy      not measured
  FAIL       savings                  0
  PASS       latency                  20
  PASS       run_integrity            0
```

Two dimensions came back **UNMEASURED**. That's not a bug — it's the point.
Nothing was routed to the cheap model, so there is no cheap-model quality to
score. A weaker tool would silently skip those dimensions and might even report a
pass. Instar refuses:

> **A dimension it could not measure is never a pass.** Silence is not evidence.

`unmeasured` drags the overall verdict down exactly like a failure would, so a run
that tested nothing can never masquerade as a run that passed.

---

## What you now know, by having done it

- A **measurement** is numbers; a **rubric** turns them into a decision (Step 1–2).
- The overall verdict is the **worst** dimension, never an average, so a big
  saving can't hide a real quality failure (Step 2).
- Thresholds are the whole game, and moving one moves the verdict (Step 3).
- Moving a threshold **after** seeing the result is the cardinal sin; version it
  instead (Step 4).
- A dimension with **nothing to measure** is never a pass (Step 5).

Delete your practice file when you're done:

```bash
rm my-rubric.json
```

## Where to go next

- [`07-GUIDE-Creating-Rubrics.md`](07-GUIDE-Creating-Rubrics.md) — write a rubric for
  your own workload, from scratch.
- [`03-CASE-STUDY-Qwen-Triage.md`](03-CASE-STUDY-Qwen-Triage.md) — a real run where the
  verdict said FAIL and was *wrong to trust* — and how reading the rows revealed
  why. Everything here was mock; that was live, and it teaches the judgment this
  lesson can't.
- [`02-GUIDE-Setting-the-Bar.md`](02-GUIDE-Setting-the-Bar.md) — for the person who
  owns the number you kept changing in Step 3.
