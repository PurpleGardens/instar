# Guide: grading answers by hand

> **TL;DR:** Export a saved `instar arms` transcript to a spreadsheet with
> `instar grade-sheet`. A person marks each candidate answer PASS, MARGINAL or
> FAIL against the reference answer beside it, without seeing which model wrote
> it. `instar rejudge --grades` scores the run from those grades. Cost and
> latency replay unchanged, so the human's quality numbers sit beside every LLM
> judge's numbers on the same answers. That comparison tells you how far to
> trust the LLM judge.

**For:** anyone who has run `instar arms --save-transcript` (or `--corpus`) and
wants a person, not a model, to decide what "good enough" means. The grader
needs a spreadsheet and nothing else.

---

## Why grade by hand

Every model judge is a measurement instrument with its own error. In our own
runs the same answers scored anywhere from 0.53 to 0.87 depending on which
vendor's model did the judging. The only way to know which judge to believe is
to compare it with people who know the work. Grading 30–50 rows by hand is
usually enough to see whether a judge agrees with you.

## 1. Export the sheet

```bash
instar grade-sheet runs/my-run/transcript.json -o sheet.csv
```

The CSV has one row per (prompt, candidate answer):

| Column | What it holds |
|---|---|
| `item_id` | Opaque id. Don't edit it. |
| `feature` | The workload feature the prompt came from |
| `task` | The system prompt and messages the models were given |
| `reference_answer` | The baseline arm's answer, what you'd ship today |
| `candidate_answer` | One alternative answer |
| `grade` | **Fill in:** `PASS`, `MARGINAL` or `FAIL` |
| `note` | Optional: why. Lands in the report next to the score. |

What the grader does **not** see: arm names, model ids, prices. Rows are
shuffled (reproducibly; `--seed` changes the order) so one arm's answers don't
arrive in a block. The reference always sits in its own column, because the
question is relative and a person needs to know which answer is the reference.

The command refuses to overwrite an existing file (it may hold someone's
grades); pass `--force` if you mean it.

## 2. Grade

For each row, ask: **could the candidate ship in place of the reference?**

- **PASS**: as good as the reference for this task. Ship it.
- **MARGINAL**: usable but clearly worse. The user would probably ask again.
- **FAIL**: wrong, off-target or unusable. It would have to be redone.

These are the same rungs the LLM judges use, so the numbers are comparable.
Leave a row blank to skip it. Partial grading is fine: a skipped row is
**unscored**, not a pass, and the report counts only what was graded.

Sort, filter or hide columns in the spreadsheet as you like. Grades are matched
by `item_id`, not by row position. Save as CSV when done.

## 3. Score the run from the grades

```bash
instar rejudge runs/my-run/transcript.json --grades sheet.csv --grader grader-1
```

`--grader` is a **pseudonymous id** (`grader-1`, initials). It's recorded as the
judge in `result.json` and in any corpus, so don't use a name or an email; an
id containing `@` is refused. Use a different id for each person so their grades
can be compared with each other.

Add `--corpus DIR` (for a transcript from a corpus run) to store the grades
beside the model judges' scores. `instar corpus runs` then lists the run with
judge `human:grader-1 [human, blind]`, and `--judge-family human` filters to
human-graded runs.

The sheet has to match the transcript: a graded `item_id` the transcript can't
produce (a sheet from a different run, or an edited id) stops the command
rather than silently scoring nothing.

## Things to know

- **Identical answers are graded once.** If two arms gave the same text for
  the same prompt (common for a same-model control arm or a router arm serving
  the baseline's model), they share one row and one grade. For a person that is
  the right call: nobody should grade the same text twice. It also means a
  human-graded control arm doesn't measure the grader's noise the way it
  measures a model judge's. For grader noise, have two people grade the same
  sheet and compare.
- **Failed calls aren't offered for grading.** A pair where either side errored
  is skipped, the same as for every other judge.
- **Position isn't blinded.** The reference is always in its own column. The
  judge key records `blind: true` meaning *provenance* is hidden, not position.
