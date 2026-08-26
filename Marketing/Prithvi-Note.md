# Courtesy note to Prithvi — draft

**Status:** draft, unsent. Clears gate item 4 in [`README.md`](README.md) once sent
and acknowledged.
**Owed per:** [`../Planning/Naming.md`](../Planning/Naming.md) §Verification status #4 ·
[`../CLAUDE.md`](../CLAUDE.md) §Status

---

## What this note is actually for

`Planning/Naming.md` describes this as *"a courtesy heads-up on the name and the fork
approach."* That undersells it. The name is trivia. **The substantive news is the
convergence decision** (`Planning/Project-Plan.md` §13, made 2026-07-22 and moved up from
the original 30–60 day deferral): Instar is now the durable home for the Measurement
Harness, new harness work goes here, and `gateway-lab` winds down once Prithvi's paper
and in-flight work are finished.

Prithvi hasn't been told that. It is recorded in this repo as a decision already taken.
So the note leads with it rather than burying it under the naming heads-up.

The forcing function for sending now: a possible outreach to Simon Willison about
[`../Engineering/Docs/COMPARISON-smevals.md`](../Engineering/Docs/COMPARISON-smevals.md).
If that lands anywhere with reach, Prithvi hearing "Instar" from a Willison post before
hearing it from Brian is the bad version.

### Three judgment calls baked into the draft

1. **No wind-down date for `gateway-lab`.** §13 says "a few more weeks, then consider
   archiving." A date is better agreed than announced, so the draft deliberately omits
   one. If a date is needed, that's a conversation.
2. **His paper and SLM hosting work are named as his.** Given the convergence, absorption
   is the reasonable thing for him to worry about. Saying otherwise plainly costs nothing
   if true.
3. **The Simon outreach is disclosed**, because it's the reason for the timing.

**Recommended delivery: call first, then send version B as the written record.** The
wind-down of someone's repo lands better in a voice than in text. Version A exists for
the case where a call isn't practical soon.

---

## Version A — full email (if sending cold)

> **Subject:** Instar — heads-up before anything goes public
>
> Prithvi,
>
> Wanted to give you this directly before it shows up anywhere public.
>
> The measurement harness we've been building now lives in its own repo —
> `github.com/PurpleGardens/instar`, Apache 2.0, still pre-release. The name comes from
> biology: an *instar* is the stage between molts, which fit both what the tool measures
> and the larger theme of organizations changing shape around AI.
>
> Two things I want to be straight with you about.
>
> First, I made Instar a **new repo rather than renaming gateway-lab**, specifically so
> nothing about your remote or your working tree changed underneath you. That was the
> point of doing it that way.
>
> Second, the real news: I ported the harness core into Instar as a clean-room
> re-implementation on July 22, and I've since decided **Instar is the durable home for
> it.** I'd originally planned to leave that question open for another month or two, but a
> big push landed the harness plus a full docs tree there, and keeping three repos as
> candidates for the same code was creating duplication risk. New harness work goes to
> Instar now.
>
> What that means for you, concretely: **keep gateway-lab for as long as your paper and
> your in-flight work need it.** There's no deadline from me. When you're done with it
> we'd archive rather than delete, so the provenance stays intact. Your SLM hosting tests
> and the paper are yours — I'd rather support them than absorb them, and if any of that
> work wants a home in Instar, that's your call to make, not mine.
>
> You have merge rights on Instar and I'd like you involved in it, but I don't want that
> to read as an obligation on top of the paper.
>
> One more thing: I'm considering reaching out to Simon Willison about how Instar composes
> with his smevals tool. If that goes anywhere it could bring some attention, which is
> exactly why I wanted you to hear all of this from me first.
>
> Any concerns about the name, the repo split, or how I've handled the port? Genuinely —
> say so, and happy to talk it through rather than settle it over email.
>
> Brian

---

## Version B — short confirmation (after a call)

> **Subject:** Recap — Instar, and where gateway-lab goes from here
>
> Prithvi,
>
> Good talking. Writing down what we covered so we both have it.
>
> - The harness now lives at `github.com/PurpleGardens/instar` — Apache 2.0, pre-release.
>   New harness work goes there. It's a new repo rather than a rename of gateway-lab, so
>   nothing changed under your remote.
> - The core was ported over on July 22 as a clean-room re-implementation, and Instar is
>   the durable home for it going forward.
> - **gateway-lab stays yours** for the paper and anything in flight — no deadline from
>   me. When you're done we archive rather than delete, so the provenance survives.
> - Your SLM hosting work and the paper are yours. If any of it should land in Instar,
>   that's your call.
> - You have merge rights on Instar whenever you want them — no obligation.
> - Heads-up that I may reach out to Simon Willison about how Instar and his smevals tool
>   compose. Wanted you to know before anything public.
>
> [Anything we agreed on the call that isn't above — add it here.]
>
> Brian

---

## After sending

- Mark gate item 4 in [`README.md`](README.md) and item #4 in
  [`../Planning/Naming.md`](../Planning/Naming.md) §Verification status.
- Record his response — particularly any objection to the name, since the naming decision
  is reversible in principle and much cheaper to revisit before a tagged release.
- Note that the *other* hold on public announcement (full Class-9 trademark clearance)
  is independent and does not clear with this one.
