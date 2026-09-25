# Engineering

Code, tests, examples, fixtures, docs source, and engineering notes for Instar. This is where the harness gets built and where design decisions about *how* to build it get recorded.

## Contents

- **`src/instar/`** — the Python package.
  - `core/` — the traffic format, the feature catalog, cost math, and the two runners (`route`, `gateway`).
  - `providers/` — the `Backend` seam: mock, Anthropic, and any OpenAI-compatible endpoint.
  - `policies/` — routing policies (control, rules, classifier).
  - `rubrics/` — judges: objective label match, LLM-as-judge, auto-dispatch, mock.
  - `reporters/` — JSON, Markdown, and CSV output.
  - `cli/` — `instar route` and `instar gateway`.
- **`fixtures/`** — synthetic, PII-free workload fixtures plus example feature catalogs. Customer-derived fixtures stay in the private repo (see IP boundary). `tests/test_fixtures.py` asserts that no private product terms appear here.
- **`tests/`** — `pytest`, all hermetic. Mock mode is always green; live-provider tests sit behind the `live` marker and are skipped in CI.
- **`Docs/`** — contributor documentation: `13-CODE-OVERVIEW.md` for orientation, `05-RUNBOOK.md` for running things.
- **`docs/`** — MkDocs Material source for the published site. Not yet scaffolded; see `../Planning/Project-Plan.md`.

The core is **stdlib-only** on purpose: mock mode runs anywhere, `pip install instar-harness` pulls in nothing, and CI never breaks on a provider SDK release. Provider SDKs are optional extras, imported lazily by the module that needs them.

## Running it

```bash
pip install -e ".[dev]"
instar route --traffic Engineering/fixtures/sample-traffic.jsonl \
    --catalog Engineering/fixtures/catalogs/example-departments.json
pytest -m "not live"
```

See `Docs/05-RUNBOOK.md` for the full set of tasks, including measuring your own workload.

## Src layout note

The Python package lives at `Engineering/src/instar/` — not at the repo root. `pyproject.toml` (Week-1 sprint task, at the repo root) points its build backend at this path. This is deliberate; see `../CLAUDE.md` §Organization principle for why by-function organization wins over Python-convention.

## Not in this directory

- Governance/tooling files (`pyproject.toml`, `.github/`, `CONTRIBUTING.md`, etc.) live at repo root — GitHub and Python tooling expect them there.
- Strategy, plan, roadmap, naming decisions — those live in `../Planning/`.
- Positioning, announcement drafts, external comms — those live in `../Marketing/`.

## IP boundary reminder

Everything committed here is Apache 2.0 and adoptable by anyone. Per-department rubrics, customer-derived fixtures, and methodology write-ups **do not belong here** — they live in the private Purple Blossom AI repo. See `../CLAUDE.md` §IP boundary and `../Planning/Project-Plan.md` §2. Every PR reviewer holds this line.
