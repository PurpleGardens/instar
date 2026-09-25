# Guide: judging answers against written criteria

> **TL;DR:** Write down what a good answer must do, per feature, as a short
> checklist. `instar arms --criteria criteria.json` has a model judge check
> every arm's answers against it (YES or NO per criterion), **baseline
> included**. Each answer's score is the share of criteria it met. Missing a
> criterion marked `critical` scores 0.0. Use it when "is the cheap answer as
> good as the expensive one?" is the wrong question.

**For:** operators and decision owners who can say what a good answer looks
like, but don't want the definition of "good" to be "whatever the baseline
model wrote."

---

## Relative or absolute?

Instar's other model judges are **relative**. They ask whether a candidate
answer could ship in place of the baseline's. That's the right question for a
routing study where the baseline is what you already trust.

It's the wrong question when:

- **nobody trusts the baseline either.** A relative judge scores a
  cheap model 1.0 for matching an answer that was wrong.
- **the thing under test isn't a model swap.** A prompt change, a new tool or
  MCP server, a retrieval step: you want to know whether the answer is *good*,
  not whether it resembles the old one.
- **you need the baseline's own score.** A relative judge can't give one; the
  baseline is 1.0 by definition. An absolute judge measures it.

`CriteriaJudge` is **absolute**: it reads the task and one answer, never the
other arm's answer and never which model wrote it.

## 1. Write the criteria

```json
{
  "version": "support-v1",
  "default": [
    "Answers the task that was asked, not a different one"
  ],
  "features": {
    "support.macro_draft": [
      "Names the refund window",
      "Tells the customer what happens next",
      {"id": "no-invented-policy",
       "text": "Does not state any policy the task does not give",
       "critical": true}
    ]
  }
}
```

- **`features`**: a checklist per feature key (the `feature` field in your
  workload).
- **`default`**: used for any feature without its own list. Optional.
- **Per-sample override**: a sample's `meta.criteria` (same list format) wins
  over both. Use it when one prompt needs its own checks.
- A criterion is a string, or an object with `text`, an optional `id` (defaults
  to `c1`, `c2`, …) and optional `"critical": true`.
- **`version`** is recorded with every score. Change it whenever you change
  the criteria; the same judge reading a different checklist is a different
  instrument.

A sample whose feature has no criteria and no default is **unscored**, not
passed. An empty checklist doesn't describe a good answer.

**Writing criteria that work:**

- One checkable fact per line. "Names the refund window" can be checked;
  "is helpful and accurate" can't.
- Say what the task *gives*. "Does not state figures the task does not give"
  is checkable; "is factually correct" asks the judge to know your business.
- Mark as `critical` only what makes an answer unusable on its own: wrong
  format for a parser, invented policy, a leaked secret. If everything is
  critical, the score collapses to pass/fail.
- 3–6 criteria per feature is plenty. Longer lists get sloppier verdicts.

A worked example for the shipped sample workload is in
`Engineering/fixtures/criteria/sample-traffic-example-v1.json`.

## 2. Run it

```bash
instar arms --traffic your-workload.jsonl --criteria criteria.json --control \
    --judge-model <judge> --live ...
```

`--criteria` turns judging on (no `--judge` needed). The judge model is chosen
with the same flags as the other judges (`--judge-model`, `--judge-url`,
`--judge-key-env`, `--judge-family`). `--blind-judge` is refused, because the
criteria judge already sees one answer with no provenance.

Or re-score a saved transcript without regenerating anything:

```bash
instar rejudge runs/my-run/transcript.json --criteria criteria.json
```

Without `--live` (or with `rejudge --mock-judge`), verdicts come from a
deterministic mock that **measures nothing** but exercises the whole path.

## 3. Read the result

Every arm, the baseline included, gets a quality number on one scale: the
share of your criteria its answers met. The report's quality table counts
answers that met **all** criteria, **partial**, and **none or a critical
miss**. Each call's rationale names the criteria it missed, for example
`criteria: 2/3 met; missed: c2`, so a low score points at a specific
criterion.

A criterion the judge gave no readable verdict on counts as **missed**, and
the rationale says so. An unreadable reply must never read as a pass.

**The control arm under an absolute judge.** With a relative judge, a
same-model control's true score is 1.0, so anything less is judge error. With
an absolute judge, its true score is *the baseline's*. `instar corpus
calibration` therefore shows an absolute judge's control as **control minus
baseline** on the same prompts, where `+0.000` means the judge scored the two
same-model arms alike. Corpus labels mark these judges `absolute` and carry
`v=<version>`.

## Validate it before you quote it

A criteria judge is still a model. Before quoting its numbers:

1. Grade a sample of the same answers by hand
   ([`GUIDE-Human-Grading.md`](GUIDE-Human-Grading.md)).
2. Re-score with a judge from a different vendor family (`rejudge --criteria
   ... --judge-model <other>`).

Checklists make disagreements easier to diagnose than a single
PASS/MARGINAL/FAIL: the rationale shows which criterion two judges read
differently.

*Note:* human grading sheets are relative today (reference answer beside
candidate). A human checklist sheet is a natural follow-up.
