# Instar: an overview for managers

> **TL;DR:** Instar answers a question most teams using AI can't answer today:
> *for this piece of our work, which AI model (or tool, or setup) is good
> enough, and what does it cost?* It takes a sample of your team's real AI
> work, runs it through the options you're considering, prices every call, has
> the answers scored against a standard you set, and hands back a report you
> can check. It does the same for the MCP tool servers your AI assistants use.
> It's free, open source, and runs on your own machines; nothing is sent to us.

**For:** the manager, director or executive who owns a decision about AI, not
the person running the tool. You don't need to read code to use this page. The
companion for engineers is [`CODE-OVERVIEW.md`](CODE-OVERVIEW.md); this page
covers the same ground in business terms.

---

## The problem it solves

Most teams chose their AI model once, early, and haven't revisited the choice.
Since then:

- **The bill grew.** AI calls are now a real operating cost, and most of it
  goes to whatever model was picked first, whether or not the work needs it.
- **The options multiplied.** Cheaper and faster models ship every month, each
  claiming to be as good as the expensive one.
- **The evidence didn't.** Vendor benchmarks are measured on someone else's
  work. A demo is measured on a handful of examples someone picked. Neither
  tells you what will happen to *your* support tickets, *your* sales emails,
  *your* reports.

So the decision to switch (or not) usually rests on a guess, and nobody wants to
be the person whose guess broke something. Instar replaces the guess with a
measurement.

## What it does, in one picture

```
   A sample of your real AI work          The options you're weighing
   (e.g. 50 support tickets, the          (today's model, a cheaper one,
    prompts your team actually sends)      a router, a self-hosted model)
                    \                      /
                     \                    /
                      v                  v
                   +------------------------+
                   |         Instar         |
                   |  runs every option on  |
                   |  the same work, side   |
                   |  by side               |
                   +------------------------+
                               |
                               v
          For each option: what it cost, how fast it was,
          and how good its answers were, against YOUR standard
                               |
                               v
             A report you can read, check, and rerun later
```

## The four ideas behind it

Instar is built from four ideas. Each one is a choice you or your team make;
the tool keeps the choices honest.

**1. Your work, not a benchmark.** A measurement starts from a sample of what
your team actually asks AI to do: real prompts, with anything private removed
first. A cheaper model that's excellent on a public benchmark can still be
poor at *your* invoices. The only way to know is to test it on them.

**2. The options, run side by side.** Each option (today's model, a cheaper one,
a model reached through a router, one you host yourself) answers the same
questions in the same run. Instar alternates between them call by call so none
gets an unfair advantage from a slow network moment. It prices every call, using
the provider's own reported cost wherever the provider gives one.

**3. A standard you set, before you look.** Someone has to decide what "good
enough" means: *correct 95% of the time*, *never invents a refund policy*,
*answers in under two seconds*. That's a business judgment about the cost of
being wrong, and it belongs to the person who owns the outcome, which is you.
Setting it **in advance** matters: a bar chosen after you see the numbers
tends to match whatever the numbers were. [`GUIDE-Setting-the-Bar.md`](GUIDE-Setting-the-Bar.md)
is a one-page guide to doing this well.

**4. A judge for quality, and a check on the judge.** Someone or something has to
score the answers. Depending on the work, that's:

- **an answer key**, for work with a right answer (sorting tickets into
  categories). Exact, free, and nobody to argue with;
- **a checklist you write**, for work without one right answer ("names the
  refund window", "doesn't promise a date it can't support"). An AI model
  checks each item, and each answer is scored by how much of your checklist it
  meets;
- **an AI grader**, comparing each cheaper answer to what today's model said;
- **your own people**, grading a spreadsheet of answers without being told
  which model wrote which.

AI graders make mistakes too, so Instar builds in checks on the grader
itself. It hides which model wrote which answer. It can have graders from
different AI companies score the same answers. And it lets your people grade a
sample, so you can see how far to trust the AI grader before relying on it.

## What you get

A report for each measurement, in plain Markdown, with:

- **Cost**: per 1,000 calls for each option, and the saving or increase against
  today's setup. Where a cost can't be known, the report says so instead of
  printing $0.
- **Quality**: each option's score against your standard, with how many answers
  passed, were borderline, or failed. Every individual score has a written
  reason you can read.
- **Speed**: typical and worst-case response times.
- **A verdict, if you set a bar**: pass or fail against the standard you wrote
  down in advance, and which requirement decided it.
- **Warnings, in plain words**: too few examples to trust the worst-case speed;
  a cost estimated rather than reported; a router that quietly substituted a
  different model; a quality score with no control run to show how much of it
  is the grader's own error.

Every run can be repeated later on the same work to check it, or re-scored with
a different grader without paying for the AI answers again.

## AI tools and MCP servers

AI assistants increasingly reach into company systems through **MCP servers**:
small connectors that let an assistant look up an order, search documents or
query a database. Teams install them quickly and rarely measure them. Instar
measures them at two levels:

- **The connector on its own.** What it costs before anyone asks a question (every
  connector's description is sent to the AI on every step, whether it's used or
  not), how fast it answers, how often it fails, and how much text each answer
  adds to the AI's workload.
- **An AI assistant using it.** Give a model the connector's tools and a set of
  real tasks, and measure how many steps each task takes, what the whole task
  costs, and whether the final answer met your checklist. Different models can be
  compared on exactly the same tool results, so the comparison is fair.

**Safety is built in.** Instar will not call any tool that could change
something (issue a refund, delete a record) unless you explicitly allow it by
name. By default it only reads.

Instar can also take the record of tool calls your people and assistants
actually made, from an MCP gateway's audit log (for example Obot's), and replay
them as a test. That way you measure real usage, not a guess at it.

## The benefits

- **Decisions you can defend.** "We tested it on 200 of our own tickets and it
  met the bar we set in advance" survives a budget review, a vendor's sales
  pitch and a new hire's second-guessing. "It seemed fine" doesn't.
- **Savings you can prove, or a switch you can safely skip.** Often part of the
  work (sorting, tagging, summaries nobody reads word for word) can move to a
  much cheaper model, and part should stay where it is. The measurement tells
  you which part is which. Sometimes the answer is "don't switch", which is worth
  knowing before you've switched.
- **The standard stays yours.** Your checklist and your graded examples say
  what good work looks like at your company. They belong to you, sit in your own
  files, and work with any AI vendor. Changing vendors doesn't mean starting over.
- **Proof you could leave.** The same report that compares models also shows what
  moving to a different vendor, or to a self-hosted model, would cost you in
  money and quality. That's leverage in any renewal conversation.
- **No new dependency.** Instar is open source (Apache 2.0), runs on your own
  machines, has no telemetry, and sends nothing to us. You can read every line,
  and anyone can rerun your measurement to check it.
- **Cheap to try.** Out of the box it runs in a practice mode that uses no AI
  service and costs nothing, so your team can see the whole process before
  spending anything. A real first measurement of 10 to 30 prompts typically
  costs a few dollars in AI usage.

## What it is not

Being clear about this saves disappointment later:

- **Not a router.** It doesn't sit in front of your live traffic or decide
  anything in production. It tells you which choices *would* have paid off, so
  you can configure whatever you use in production with confidence.
- **Not a dashboard or a hosted service.** It's a tool your team (or a partner)
  runs, which produces reports.
- **Not magic about quality.** It can only measure against the standard you
  give it. A vague standard gives a vague answer, and an AI grader is only as
  trustworthy as the checks you run on it.
- **Not a one-time verdict.** Models and prices change. A measurement is a
  snapshot; the value is that it's cheap to repeat.

## How a measurement usually goes

| Step | Who | What |
|---|---|---|
| 1. Frame the decision | You | "Can the support team's ticket sorting move to a cheaper model?" |
| 2. Set the bar | You | Written down before any test runs |
| 3. Pick the sample | You + your team | 30 to a few hundred real examples, private details removed |
| 4. Run and score | Whoever runs Instar | Your engineers, or a partner |
| 5. Grade a sample by hand | Someone who knows the work | Checks the AI grader before anyone relies on it |
| 6. Read the report and decide | You | Including the individual answers behind any surprising number |

A first measurement of one piece of work usually takes days, not weeks. Most of
the time goes on steps 1 to 3, which are business decisions, not technical ones.

Instar is built for two kinds of teams: those with engineers who run it
themselves, and those who have the AI question but not the in-house plumbing
and bring in a partner to run it. It was built by Atelier, the consulting
practice of Purple Blossom AI, which uses it in that partner role; because it's
open source, any firm can do the same.

## Plain-language glossary

| Term | Means |
|---|---|
| **Workload** | A sample of the AI requests one piece of your work makes |
| **Option** (engineers say *arm*) | One way of doing the work: a model, reached a particular way |
| **Baseline** | What you do today; everything is compared against it |
| **Judge / grader** | Whatever scores the answers: an answer key, a checklist, an AI, or a person |
| **Bar** (engineers say *rubric*) | Your written standard for "good enough", set in advance |
| **Control** | A second run of today's model, used to see how much the grader wobbles on identical work |
| **Practice mode** (*mock mode*) | A free, offline run with stand-in answers that exercises the whole process and measures nothing |
| **MCP server** | A connector that gives an AI assistant tools, such as looking up an order |

## Where to go next

- [`GUIDE-Setting-the-Bar.md`](GUIDE-Setting-the-Bar.md): the one decision only
  you can make, on one page.
- [`CASE-STUDY-Qwen-Triage.md`](CASE-STUDY-Qwen-Triage.md): a real measurement
  where the headline verdict was misleading and a person reading the details got
  it right. A good example of reading a report wisely.
- [`GUIDE-Measure-Your-Own-Prompts.md`](GUIDE-Measure-Your-Own-Prompts.md): for
  the person on your team who'll run a first measurement.
- [`CODE-OVERVIEW.md`](CODE-OVERVIEW.md): the same material for engineers.
