# Instar documentation — start here

Instar is two things at once: a tool for measuring AI spend, and a **knowledge
tree** for learning how to reason about it — for companies and individuals,
students and instructors alike. These docs are built to be taught from, not just
read. If you're not sure where to begin, pick the row that sounds like you.

| If you want to… | Read, in order |
|---|---|
| **Understand what Instar is and why it's worth doing** (manager, no code) | [`01-OVERVIEW-For-Managers.md`](01-OVERVIEW-For-Managers.md) |
| **Decide whether a model is good enough** (you own the outcome, not the tool) | [`03-GUIDE-Setting-the-Bar.md`](03-GUIDE-Setting-the-Bar.md) |
| **Learn rubrics by running something** (10 minutes, no spend) | [`09-LESSON-Rubrics-Hands-On.md`](09-LESSON-Rubrics-Hands-On.md) |
| **Find out if a cheaper model is good enough for *your own* prompts** (no app needed, ~1 hour) | [`02-GUIDE-Measure-Your-Own-Prompts.md`](02-GUIDE-Measure-Your-Own-Prompts.md) |
| **Measure an MCP server** (no model needed) | [`12-GUIDE-MCP-Measurement.md`](12-GUIDE-MCP-Measurement.md) |
| **Score answers against a written checklist** (absolute judge, baseline included) | [`11-GUIDE-Criteria-Judge.md`](11-GUIDE-Criteria-Judge.md) |
| **Run it and measure a workload** | [`05-RUNBOOK.md`](05-RUNBOOK.md) → [`06-PROVIDERS.md`](06-PROVIDERS.md) |
| **Turn a decision into a pass/fail check** | [`08-GUIDE-Creating-Rubrics.md`](08-GUIDE-Creating-Rubrics.md) → [`07-RUBRICS.md`](07-RUBRICS.md) |
| **Learn to read a result with judgment** | [`04-CASE-STUDY-Qwen-Triage.md`](04-CASE-STUDY-Qwen-Triage.md) |
| **Work on the code** | [`13-CODE-OVERVIEW.md`](13-CODE-OVERVIEW.md) → [`05-RUNBOOK.md`](05-RUNBOOK.md) |
| **Figure out whether Instar is even the right tool** | [`14-COMPARISON-smevals.md`](14-COMPARISON-smevals.md) |

---

## The docs, and who each is for

| Doc | It's a… | For | In one line |
|---|---|---|---|
| [`01-OVERVIEW-For-Managers.md`](01-OVERVIEW-For-Managers.md) | overview (explain) | manager, decision owner | What Instar does and why it's worth doing, in business terms: the four ideas, what the report tells you, MCP measurement, benefits and limits. No code. |
| [`03-GUIDE-Setting-the-Bar.md`](03-GUIDE-Setting-the-Bar.md) | one-pager | decision owner | The bar is a business judgment, not a technical one. How to set it, in plain terms — no tool, no jargon. |
| [`09-LESSON-Rubrics-Hands-On.md`](09-LESSON-Rubrics-Hands-On.md) | lesson (do it) | student | Run four commands, change one number, watch a verdict flip. Learn rubrics by feeling them move. |
| [`02-GUIDE-Measure-Your-Own-Prompts.md`](02-GUIDE-Measure-Your-Own-Prompts.md) | task guide | individual | Bring 10–30 of your own prompts; compare your everyday model with cheaper ones under two judges and a control. One key, about an hour. |
| [`12-GUIDE-MCP-Measurement.md`](12-GUIDE-MCP-Measurement.md) | task guide | operator | Size an MCP server's tool definitions and replay tool calls against it with no model: latency, errors, context cost per result, checks. Read-only by default. |
| [`11-GUIDE-Criteria-Judge.md`](11-GUIDE-Criteria-Judge.md) | task guide | operator + decision owner | Write down what a good answer must do as a checklist; a model judge checks every arm against it, baseline included. For when "as good as the baseline" is the wrong question. |
| [`10-GUIDE-Human-Grading.md`](10-GUIDE-Human-Grading.md) | task guide | grader + operator | Export a blind spreadsheet from a saved run, grade PASS / MARGINAL / FAIL by hand, and score the run from it, beside the LLM judges. |
| [`05-RUNBOOK.md`](05-RUNBOOK.md) | task guide | operator | Every command, with a walkthrough for measuring your own workload. |
| [`06-PROVIDERS.md`](06-PROVIDERS.md) | task guide | operator | Connecting a hosted LLM (account → key → URL) or a self-hosted SLM (server, sizing, licensing). |
| [`08-GUIDE-Creating-Rubrics.md`](08-GUIDE-Creating-Rubrics.md) | tutorial | operator + decision owner | How to *arrive at* a rubric that decides your question — the method, not just the syntax. |
| [`07-RUBRICS.md`](07-RUBRICS.md) | reference | operator | Every rubric field, metric, and verdict rule. Keep it open while writing one. |
| [`04-CASE-STUDY-Qwen-Triage.md`](04-CASE-STUDY-Qwen-Triage.md) | case study (judge it) | student + operator | A real run where the verdict said FAIL and was wrong to trust. Teaches the judgment no tool can automate. |
| [`13-CODE-OVERVIEW.md`](13-CODE-OVERVIEW.md) | reference | contributor | Architecture, the four core abstractions, and how to extend each. |
| [`14-COMPARISON-smevals.md`](14-COMPARISON-smevals.md) | orientation | evaluator | Instar answers the workload-economics question; smevals answers the capability question. Which to reach for, and how they chain. |

## How these docs are organized: teach by mode, not just by reader

This is a knowledge tree, and every doc has a **teaching mode** as well as a
target reader. Placing a new doc means choosing its mode, not just dropping it in
a folder:

| Mode | It answers | Genres here |
|---|---|---|
| **Explain** | *What is this?* | reference (`RUBRICS`, `CODE-OVERVIEW`), overview (`01-OVERVIEW-For-Managers`) |
| **Guide** | *How do I do it, step by step?* | tutorial + task guide (`GUIDE-*`, `RUNBOOK`, `PROVIDERS`) |
| **Do** | *Let me try it and see what happens.* | lesson (`LESSON-*`) |
| **Judge** | *How do I read a result wisely — and when do I distrust it?* | case study (`CASE-STUDY-*`) |

The same concept — rubrics — appears in all four modes on purpose: explained in
`RUBRICS`, guided in `GUIDE-Creating-Rubrics`, *done* in `LESSON-Rubrics-Hands-On`,
and *judged* in `CASE-STUDY-Qwen-Triage`. A student can enter at any mode and
climb; an instructor can teach from any rung. **When you add a doc, decide which
mode it serves** — a gap in the tree is usually a missing mode, not a missing
topic.

---

## The shortest path to a real result

1. `pip install -e ".[dev]"`
2. `instar route` — a complete hermetic run against a shipped fixture. No key,
   no network, no spend. ([`05-RUNBOOK.md`](05-RUNBOOK.md) explains what you're seeing.)
3. Add `--rubric Engineering/fixtures/rubrics/support-triage-v1.json` and watch
   the verdict appear. ([`08-GUIDE-Creating-Rubrics.md`](08-GUIDE-Creating-Rubrics.md)
   explains how to write your own.)

Everything above runs in **mock mode**, which is deterministic and free. Going
live — real models, real spend — is one `--live` flag and a provider, covered in
[`06-PROVIDERS.md`](06-PROVIDERS.md).

---

## Elsewhere in the repo

These docs cover *the tool*. The wider context lives by function:

- **`../Introduction.md`** — a contributor-facing introduction to the project.
- **`../../Planning/`** — strategy: the project plan, the naming rationale, and
  `Engagement-Methodology.md`, which places rubrics (phase B) inside a full
  evaluation engagement.
- **`../../Marketing/Measuring-Your-AI-Costs.md`** — the leader-facing case for
  why measuring your own workload beats trusting a vendor benchmark.
- **`../../README.md`** (repo root) — the public front page.
- **`../../CLAUDE.md`** — the boundary between what's public (Instar) and what's
  private methodology.

---

## A note on keeping this current

When you add a doc here, add a row to the tables above **and name its teaching
mode** (Explain / Guide / Do / Judge). An unindexed doc is one nobody knows
exists — which is the exact problem this file was written to solve — and a doc
with no clear mode is usually one that's trying to do two jobs at once.
