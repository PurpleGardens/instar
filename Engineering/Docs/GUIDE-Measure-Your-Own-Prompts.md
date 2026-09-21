# Measure your own prompts — is a cheaper model good enough for *your* work?

**Mode:** Guide (task guide) · **For:** a technical individual. You can use a terminal and
Python, and you use an AI assistant every day. You don't need an application, a gateway or
production traffic. · **Time:** about an hour, most of it choosing prompts.
· **Cost:** usually $1–5 in API calls.

Public benchmarks tell you which model is best at somebody else's test. This guide tells
you which models are good enough at **the work you actually do**, and what they'd cost.
You bring 10–30 real prompts from your own recent work; Instar runs them through your
usual model and a few cheaper ones, then has independent judges compare the answers.

If you *do* have an application with captured AI traffic, [`RUNBOOK.md`](RUNBOOK.md) §5 is
the better path. This guide is for prompts a person writes, not traffic a system emits.

---

## What you'll end up with

- A side-by-side table: your everyday model against one or two cheaper candidates, with
  **cost per 1,000 calls** and **quality relative to your model** for each.
- The same answers scored by **two judges from different model families**, because a
  single judge's opinion isn't reliable (see *Why two judges* below).
- A **control**: your own model run twice, which shows how much of any quality gap is
  the judge's own error rather than a real difference.
- Everything stored locally. Nothing leaves your machine except the API calls you make.

---

## 1. Install (5 minutes)

```bash
git clone https://github.com/PurpleGardens/instar
cd instar
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
instar arms            # a free mock run: no key, no network, no spend
```

The mock run finishes in a second and prints a small table. Its numbers are fake. It's
only there to show the pipeline works.

**One key covers everything.** The recipe below sends every call through
[OpenRouter](https://openrouter.ai), which reaches Anthropic, OpenAI, Google, DeepSeek and
others with a single key, and reports what each call actually cost. Create a key, set a
spending limit on it (a few dollars is plenty), then:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

Any OpenAI-compatible endpoint works instead (a local Ollama or vLLM server, OpenAI
directly). See [`PROVIDERS.md`](PROVIDERS.md).

---

## 2. Choose your prompts (the part that matters most, 30–40 minutes)

A benchmark measures the benchmark. Twenty well-chosen prompts from your real work tell
you more than two hundred generic ones.

**Pick 10–30 prompts from the last month or two**, spread over **3–6 kinds of work** you do
regularly: say drafting emails, summarizing documents, analysing a spreadsheet you paste
in, writing code, planning. Include:

- **Your everyday work**, the things you'd happily hand to a cheaper model if it could
  do them.
- **A few prompts where your model let you down**, the ones that needed lots of
  back-and-forth or that you gave up on. These are where a better model has room to
  show it's better. Mark them `"origin": "failure-mined"` (below).

**Make each prompt stand on its own.**

- If the real conversation took several turns, keep the one request that mattered and
  paste the context it needed into it.
- Attachments, web browsing and tool use don't replay. Paste the relevant text in
  instead, or leave that prompt out.
- If you use custom instructions or a system prompt, put it in `system`.

**Privacy.** Your prompts go only to the models you choose, and your results stay on your
machine. Still: **remove anything you wouldn't send to those providers**, such as other
people's names, contact details, client data or credentials. Replace it with realistic
stand-ins of the same length and shape. The measurement depends on the *shape* of the
task, not the real names.

### Write them down, one file per prompt

The easiest way is a folder per kind of work and a plain-text file per prompt:

```
my-prompts/
├── email/            01.txt  02.txt  03.txt
├── summarize/        01.txt  02.txt
├── analysis/         01.txt  02.txt  03.txt
└── planning/         01.txt  02-failed.txt
```

Name a file `*-failed.txt` if your model let you down on it. Then turn the folder into an
Instar workload:

```bash
python3 - <<'EOF'
import json, pathlib
root = pathlib.Path("my-prompts")
with open("my-workload.jsonl", "w", encoding="utf-8") as out:
    for f in sorted(root.glob("*/*.txt")):
        kind = f.parent.name
        row = {
            "id": f"{kind}-{f.stem}",
            "feature": f"my.{kind}",
            "messages": [{"role": "user", "content": f.read_text(encoding="utf-8").strip()}],
            "max_tokens": 1500,
            "meta": {"origin": "failure-mined" if f.stem.endswith("failed") else "coverage"},
        }
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
print("wrote", sum(1 for _ in open("my-workload.jsonl")), "prompts")
EOF
```

Add `"system": "..."` to a row if you use a system prompt. Raise `max_tokens` for tasks
with long answers. Keep `id` and `feature` names neutral: they appear in results you
might share, so `my.email`, not `my.email-to-acme-lawyer`.

Check it with a free mock run:

```bash
instar arms --traffic my-workload.jsonl
```

---

## 3. Choose the models

Three roles:

| Role | What to pick | Example (OpenRouter id) |
|---|---|---|
| **Baseline** | The model you use every day. Everything is compared against it. | `anthropic/claude-sonnet-4.6`, `openai/gpt-4.1` |
| **Candidates** | One or two cheaper models you'd consider switching to | `anthropic/claude-haiku-4.5`, `google/gemini-2.5-flash`, `deepseek/deepseek-chat` |
| **Control** | Added for you by `--control`: your baseline model again, under the name `control` | — |

Model ids change. Check the current ones on openrouter.ai/models before you run.

---

## 4. Run it

```bash
OR=https://openrouter.ai/api/v1
instar arms --live --traffic my-workload.jsonl --repeats 3 \
  --arm "name=mine,url=$OR,model=anthropic/claude-sonnet-4.6,key_env=OPENROUTER_API_KEY" \
  --arm "name=cheap-1,url=$OR,model=anthropic/claude-haiku-4.5,key_env=OPENROUTER_API_KEY" \
  --arm "name=cheap-2,url=$OR,model=google/gemini-2.5-flash,key_env=OPENROUTER_API_KEY" \
  --control \
  --extra-body '{"provider":{"data_collection":"deny"}}' \
  --judge --blind-judge --judge-model openai/gpt-4.1-mini \
  --judge-url $OR --judge-key-env OPENROUTER_API_KEY \
  --corpus ~/instar-corpus --tenant me --workload my-work-v1 \
  --label my-work-v1
```

What the pieces do:

- `--repeats 3` runs each prompt three times, because one run of anything is an
  anecdote.
- `--control` adds your baseline again, so the judge's own error shows up.
- `--blind-judge` hides which answer came from which model and shuffles their order.
- `--judge-model …` should come from a **different family** than your baseline where
  possible.
- `--extra-body` asks OpenRouter to route only to providers that don't keep or train on
  your data. Drop it if it makes a model unavailable, and decide for yourself whether
  that's acceptable.
- `--corpus ~/instar-corpus --tenant me` keeps every answer and score on disk, so the
  second judge below costs only judging.

**Cost:** about (prompts × repeats × arms) generations plus one judge call for each
non-baseline answer. For 20 prompts × 3 repeats × 4 arms that's 240 generations: usually
a dollar or two, more if your baseline is a top-tier model and your prompts are long.
**Start with 5 prompts and `--repeats 1`** to see real numbers before the full run.

### The second judge (important, cheap)

Rescore the **same saved answers** with a judge from another family. No new generations,
so it costs pennies:

```bash
instar rejudge ~/instar-corpus/me/*/*/*/transcript.json \
  --blind-judge --judge-model google/gemini-2.5-flash \
  --judge-url $OR --judge-key-env OPENROUTER_API_KEY \
  --corpus ~/instar-corpus --label my-work-v1-judge2
```

If your glob matches more than one run, pass the one you want.

---

## 5. Read the result

Each run prints a summary and writes a report to `runs/<label>/report.md`. Read it in this
order:

1. **Did every call succeed?** Rate limits (HTTP 429) and timeouts happen. Failed pairs
   are skipped, not scored as zero, so check the `scored` count before trusting a number.
2. **The control's score.** Its true quality is about 1.0. If a judge scores it 0.85,
   that judge takes about 0.15 off an *identical* model, and you can't read any gap
   smaller than that as a real difference.
3. **Candidate quality relative to the control.** Divide a candidate's score by the
   control's score under the same judge.
4. **Do the two judges agree?** If both say a candidate is close to your model, that's a
   finding. If one says 0.8 and the other 0.5, you've learned about the judges, not the
   models. Say so rather than picking the number you like.
5. **Per kind of work, not just overall.** A cheap model is often fine for summaries and
   poor at planning. The overall average hides that. The per-call records in
   `calls.jsonl` let you group by `feature`.
6. **Cost per 1,000 calls.** The saving only matters for the kinds of work where quality
   held up.

### Why two judges

A judge is itself a model, and models from the same family tend to prefer each other's
style. In one measured case, the same 35 answers were scored anywhere from 0.53 to 0.87
depending on which family did the judging, and judges from different families agreed on
individual answers as little as a third of the time. Two families that agree give you
something you can act on. One judge on its own doesn't.

---

## 6. What's in your corpus, and what's safe to share

```
~/instar-corpus/me/<YYYY>/<MM>/<run_id>/
  run.json          models, costs, quality per arm, judge, warnings. No prompt text.
  calls.jsonl       one row per answer: feature, id, tokens, cost, score, judge. No prompt text.
  transcript.json   YOUR PROMPTS AND EVERY ANSWER. Private.
```

- `run.json` and `calls.jsonl` contain **no prompt or answer text**, only the `id` and
  `feature` names you chose, token counts, costs, scores and judge verdicts. They're the
  files to share if someone asks for your results.
- **`transcript.json` holds your prompts and every model's answers.** Don't share it
  unless you mean to share your prompts.
- `--upstream-consent` marks your records as OK to pool with other people's. It's off by
  default. Only set it if you've agreed to that with whoever you're sharing with.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `HTTP 429` on some calls | The provider is rate-limited upstream. Rerun later, or lower `--repeats`. |
| `HTTP 404` / model not found | The model id changed. Check openrouter.ai/models. |
| A model is unavailable with `--extra-body` | No provider for that model meets the privacy preference. Pick another model or drop the preference, knowingly. |
| `cost unknown` for an arm | The endpoint reported no cost and the model has no pricing row. Pass `--pricing` (see [`RUNBOOK.md`](RUNBOOK.md) §9). |
| A candidate's answers are much shorter | Check `max_tokens`. A low cap truncates long answers and the judge scores the truncation. |
| `--corpus needs --tenant` | Add `--tenant <any-short-name>`. |

See also: [`RUNBOOK.md`](RUNBOOK.md) §8b–8c (arms and the corpus in depth) ·
[`PROVIDERS.md`](PROVIDERS.md) · [`GUIDE-Setting-the-Bar.md`](GUIDE-Setting-the-Bar.md)
(deciding what "good enough" means before you look at the numbers).
