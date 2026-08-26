# Naming — Instar

**Decision date:** 2026-07-21
**Decided by:** Brian (theme + shortlist) + Claude (candidates + rubric)
**Status:** Name locked. PyPI ✓ (reserved as `instar-harness` — bare `instar` is disallowed by PyPI name policy) and docs domain ✓ (see below). Remaining before public announcement: full Class-9 trademark clearance and a courtesy note to Prithvi. Repo being public is **not** an announcement — the hold is on PyPI push / tagged release / public announcement, not on this repo's visibility.

---

## The name

**Instar.**

In biology, an *instar* is a stage between molts in an insect's development. Two reasons it fits:

1. **What the harness does.** A measurement harness observes the *stage-by-stage progression* of an LLM workload — each provider, each rubric evaluation, each routing decision is a discrete instar in the workload's development. The word maps directly to what the tool measures.
2. **What the tool is for.** The customer story is metamorphosis — organizations *becoming* AI-productive. Instar names a stage in that transformation, which fits the sibling brand (Blossom Grove) without competing with it.

The name was chosen from a shortlist of butterfly/transformation-themed candidates. Full deliberation (Brian's four — Butterfly, Chrysalis, Papillon, Psychí — plus five additions — Instar, Eclosion, Imago, Metamorph, Fledge — scored against a 6-factor rubric) lives in MVP1 at `~/projects/PurpleBlossomAI/MVP1/Docs/Experiments/Harness-Service+Product/Project-Name-Candidates.md`. That file is archival; this one is the operational reference.

---

## Availability sanity-check

> **Note (2026-08-26):** the two `PurpleBlossomAI/...` references below are kept as written
> because this file records what was decided on 2026-07-21, and rewriting a decision record to
> match today's layout destroys the thing it exists for. The repo has since moved to
> **`github.com/PurpleGardens/instar`** (the old URL 301s). Live pointers elsewhere in the repo
> were updated; these two were not.

Done 2026-07-21:
- **GitHub org repo:** `github.com/PurpleBlossomAI/instar` — free (was 404 at check time; now claimed by this repo). ✓
- **AI/dev-tools competitive scan:** no dominant "Instar" in the LLM eval / harness / observability space. Incumbents in the category (DeepEval, Promptfoo, Langfuse, EleutherAI `lm-evaluation-harness`) don't collide. Re-confirmed 2026-07-23 — still no LLM-eval/harness tool named Instar. ✓

Done 2026-07-23:
- **PyPI:** bare `instar` is **disallowed** by PyPI name policy — upload returns `400 "The name 'instar' isn't allowed"` (reason #2 too-similar or #3 admin-prohibited; the project genuinely does not exist — `simple/instar/` → 404). Reserved instead as **`instar-harness`** (the pre-planned fallback), `0.0.0` placeholder uploaded 2026-07-23, live at `pypi.org/project/instar-harness/`. Import package and CLI stay `instar`. ✓
- **Docs domain:** `instar-dev.org` ordered on Namecheap. ✓ Note the deviation from the originally-planned `instar.io` / `instar.dev` — `instar.com` is the German IP-camera company (INSTAR Deutschland GmbH), so a `-dev` variant both avoids that space and signals "developer tool." Downstream doc references updated below.

---

## Verification status (Brian, off-box)

**Not blocking commits to this repo. Blocking public announcement / PyPI *release* (`0.1.0`+) / tagged release.** A `0.0.0` name-holding placeholder is explicitly permitted (see #1).

1. **PyPI name — ✓ RESERVED as `instar-harness` (2026-07-23).** Bare `instar` is disallowed by PyPI policy (`400 "The name 'instar' isn't allowed"`; the name does not resolve to an existing project, so it's the too-similar/admin-prohibited gate, not "taken"). Fell back to the pre-planned **`instar-harness`** distribution name and uploaded a metadata-only `0.0.0` placeholder (throwaway scaffold outside the repo, not the harness packaging). **Import package + CLI stay `instar`** — users `pip install instar-harness`, then `import instar` / run `instar` (the `scikit-learn`/`sklearn` pattern). The placeholder is **not** a release and **not** an announcement — the hold on published releases (`0.1.0`+), tags, and announcements stands until trademark clearance + the Prithvi note are done. Reversible: the project/release can be deleted from PyPI to free the name (but the `0.0.0` version slot is then burned).
   - **Open question (pre-v0.1 backlog):** whether to pursue the bare `instar` distribution name via PyPI support (confirm prohibition vs. similarity) / PEP 541, so `pip install instar` matches the CLI. Not worth blocking on; decide before v0.1.
2. **Domain reservation — ✓ DONE (2026-07-23).** `instar-dev.org` ordered on Namecheap. (Chose `-dev.org` over the earlier `instar.io`/`instar.dev` idea; `instar.com` is the German camera company.) Optional follow-on: defensively grab `instar-dev.com` / matching handles if desired — not a blocker.
3. **Trademark — knockout DONE (2026-07-23); full clearance still owed.** Preliminary knockout run (see §Trademark knockout below). "Instar" is crowded but almost all uses are unrelated fields (pharma cl.5, rail cl.39, finance cl.36, coaching cl.35/41). **Caution:** Class 9 (software) has a pending bare-word INSTAR application (Qingdao Chang Ning, appl. 79405838, filed Jul 2024) and an active class-9 user (INSTAR Deutschland / cameras). Verdict: fine to **use** as an OSS project/package/CLI name; a full clearance focused on Class 9 is owed **before any federal trademark filing or heavy brand spend**, and a composite/narrow-goods mark would clear more easily than bare "INSTAR."
4. **Notify Prithvi — ⬜ STILL OWED.** Courtesy heads-up on the name and the fork approach (which is *for* his benefit — protects his continuing `gateway-lab` work).

If a full trademark clearance later comes back ugly, stop and reopen the naming decision using the full deliberation in the MVP1 archival doc.

---

## Trademark knockout (2026-07-23)

Preliminary knockout only — **not** legal clearance; a full search/opinion is counsel's job. Built from public search snippets (individual USPTO/Justia records were not opened); confirm the two Class-9 items at [tmsearch.uspto.gov](https://tmsearch.uspto.gov/) before relying on this for a filing decision.

| Mark | Owner | Class / field | Risk to Instar |
|---|---|---|---|
| INSTAR (appl. 79405838, pending, filed Jul 2024) | Qingdao Chang Ning Imp/Exp | Cl. 9 — electrical/scientific apparatus | **Highest** — same class, bare word, live |
| INSTAR / InstarVision | INSTAR Deutschland GmbH (`instar.com`) | Cl. 9 — IP cameras + software, US sales | Elevated — class-9 software in commerce; different goods/channel |
| INSTAR | Instar Group Inc. | Cl. 36 — PE/infrastructure fund | Low |
| INSTAR | The InStar Group LLC | Cl. 39 — rail-car leasing | Low |
| INSTAR TECHNOLOGIES | InStar Technologies a.s. | Cl. 5 — pharma | Low |
| INSTAR MODE | Toda Inc. | beauty (different mark) | Low |
| Instar Performance LLC | — | Cl. 35/41 — business coaching | Low |
| INSTAR | Three Village Central School Dist. | educational program | Low |

Three-bucket read: **resolve-now** — nothing blocks using the name for the OSS project; LLM-eval competitive scan is clean. **Backlog** — full Class-9 clearance before any federal filing; prefer a composite or narrowly-described mark. **Accept-as-residual** — coexisting with unrelated INSTAR marks is normal for a near-dictionary word.

---

## Repo strategy: fork, not rename

Instar was created 2026-07-21 as a **new empty repo** at `~/projects/PurpleBlossomAI/instar` rather than renaming `github.com/PurpleBlossomAI/gateway-lab`. Rationale:

- Prithvi keeps iterating on `gateway-lab` (testing + paper writing) without coordination overhead.
- Instar gets clean Apache-2 provenance from commit 1.
- No shared-state action; nothing about Prithvi's remote or working tree changes.
- Convergence question deferred 30–60 days (see `Project-Plan.md` §Repo relationship).

Consequence: MVP1 references to `gateway-lab` **stay as-is** — they still correctly point to Prithvi's active repo. No sweep needed.

---

## Downstream naming

Once Instar is the accepted name, everything inherits from it:

- **PyPI distribution name:** `instar-harness` (reserved 2026-07-23; bare `instar` disallowed — see status #1). `pip install instar-harness`.
- **Import package:** `instar` (`import instar`; package path `Engineering/src/instar/`).
- **CLI:** `instar` (`instar run`, `instar bench`, `instar report`).
- **Package path:** `Engineering/src/instar/` (per `../CLAUDE.md` §Organization principle).
- **Docs site URL:** GitHub Pages under `purpleblossomai.github.io/instar/` initially; move to `instar-dev.org` (reserved on Namecheap) once content is up.
- **Announcement copy:** to be drafted in `../Marketing/` in Week 2 of the sprint; do not publish until the full Class-9 trademark clearance is done (PyPI + domain are already cleared).
