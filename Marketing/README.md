# Marketing

Positioning, announcement drafts, and external communications for Instar. Currently a placeholder — first artifacts land in Week 2 of the sprint (see `../Planning/Project-Plan.md` §10).

## Contents (target)

- **Positioning** — one-page "what Instar is, who it's for, why now." Source for README hero copy, LinkedIn posts, conference blurbs.
- **Announcement drafts** — v0.1.0 blog post, short LinkedIn announcement, HN Show post draft (if we decide to submit). Drafts only; publishing is a separate decision.
- **FAQ** — external-facing version of the anti-scope in `../Planning/Project-Plan.md` §1: "why isn't this a SaaS," "how does it compare to Promptfoo / Braintrust / Langfuse," "how does the private methodology relate to the OSS."

## Do-not-publish gate

Nothing here goes public until the off-box verifications in `../Planning/Naming.md` §Still-to-verify come back clean:

1. ~~PyPI name availability~~ ✓ 2026-07-23 — reserved as `instar-harness` (bare `instar` is disallowed by PyPI policy); import package and CLI stay `instar`
2. ~~Domain reservation~~ ✓ 2026-07-23 — `instar-dev.org` (not `instar.io`/`instar.dev`; `instar.com` is a German camera company)
3. Formal trademark clearance — knockout ✓ 2026-07-23; **full Class-9 clearance still owed**, and it is the binding hold
4. Prithvi ack on the name and fork approach — draft ready in [`Prithvi-Note.md`](Prithvi-Note.md) (unsent)

Drafts can and should exist before that gate clears. Publishing cannot.

## Not in this directory

- Strategy, roadmap, IP boundary — those live in `../Planning/`.
- Code, examples, docs source — those live in `../Engineering/`.
- Customer-specific pitch materials, SOWs, engagement playbooks — those are consulting IP and belong in the private Purple Blossom AI repo, not here.
