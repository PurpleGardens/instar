# SPDX-License-Identifier: Apache-2.0
"""The measurement corpus: every run, kept, with enough context to read it later.

A single run answers a single question. Many of the questions worth asking are
about the *measuring* rather than the models: is this judge drifting? has this
rubric dimension ever decided a verdict? is this gold label wrong? did the
cheap model get better, or did the judge get kinder? None of them can be
answered from one run, and all of them can be answered from many — provided
each run was stored with its context. This module writes that context.

**Append-only.** A run becomes a directory that is never rewritten:

    <corpus>/<tenant_id>/<YYYY>/<MM>/<run_id>/
        run.json          one run record
        calls.jsonl       one call record per (prompt, repeat, arm)
        transcript.json   every generation (arms runs only)

Re-judging saved generations writes a *new* run directory whose call records
point back at the original transcript, so a second judge's opinion is added
next to the first rather than replacing it.

**Four rules the records enforce**, each learned from a run that would have
been misread without it:

1. *The judge is part of the measurement.* Every score carries the judge's
   kind, model, family, and blinding. Scores under different judges are
   different measurements, and averaging them has to be done on purpose.
2. *A judged run should carry a same-model control.* The control's score is
   the judge's own error; without it the other scores cannot be read.
3. *Generations are kept.* Gold labels and judges both turn out to be wrong
   sometimes; with the generations stored, history can be re-scored instead of
   discarded.
4. *Age is visible.* Every record says when it was made and by which build, so
   an old number cannot pass for a current one.

**Tenants and consent.** Records are grouped by tenant, and each carries an
``upstream_consent`` flag that is false unless the tenant opted in *and* the
row did not opt out. Instar only records the flag; any pooling across tenants
is done elsewhere, by whoever holds the consent.

**Privacy.** A transcript holds raw model output for the workload's prompts.
If the workload is private, so is the corpus. Keep it out of public repos.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from instar import __version__
from instar.core.arms import ArmsResult
from instar.core.transcript import Transcript

SCHEMA_VERSION = 1

# Where a task came from. Kept because the three are not interchangeable: a
# set chosen from past failures is selected *against* the model that failed
# it, and comparing on it without accounting for that flatters any newcomer.
ORIGIN_COVERAGE = "coverage"
ORIGIN_FAILURE_MINED = "failure-mined"
ORIGIN_PRODUCTION = "production-captured"
ORIGINS = frozenset({ORIGIN_COVERAGE, ORIGIN_FAILURE_MINED, ORIGIN_PRODUCTION})

# What a real user did with the original output, when that is known. A weak
# label, but a free one: it turns captured production calls into labelled rows.
REACTIONS = frozenset({"accepted", "edited", "regenerated", "abandoned"})

# What a workload is *for*, which decides how its numbers may be used. Every
# decision taken on a fixed set of tasks spends some of its validity (Dwork et
# al., 2015); a set used to choose between candidates stops measuring them
# (Xia et al., RRSI, 2026). So a set is one of:
#   evolve    - tuning happens here; its scores are optimistic by construction
#   held_out  - checks what tuning produced; each look spends some of it
#   standard  - the frozen yardstick; never tuned against
SPLIT_EVOLVE = "evolve"
SPLIT_HELD_OUT = "held_out"
SPLIT_STANDARD = "standard"
SPLIT_ROLES = frozenset({SPLIT_EVOLVE, SPLIT_HELD_OUT, SPLIT_STANDARD})

ROLE_BASELINE = "baseline"
ROLE_CONTROL = "control"
ROLE_CANDIDATE = "candidate"

KIND_ARMS = "arms"
KIND_REJUDGE = "rejudge"

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class RecordContext:
    """What a run's records need to know that the run itself does not.

    Args:
        tenant_id: whose workload this is. Required: a record with no owner
            cannot honour a consent decision or a withdrawal.
        upstream_consent: whether the tenant allows these records to leave
            their corpus for a shared one. Off unless someone said yes.
        workload_id: which workload (fixture) was replayed.
        origin: where the tasks came from; one of :data:`ORIGINS`. A sample's
            ``meta["origin"]`` overrides it for that sample.
        rubric_version: the rubric the scores are meant to be read against.
        gold_version: the version of the gold labels, if the workload has any.
            Gold labels get corrected; scores against an old version are not
            scores against the new one.
        mock: True for hermetic runs, so they can never be mistaken for data.
        split_role: what the workload is for; one of :data:`SPLIT_ROLES`, or
            None when nobody said.
    """

    tenant_id: str
    upstream_consent: bool = False
    workload_id: str | None = None
    origin: str = ORIGIN_COVERAGE
    rubric_version: str | None = None
    gold_version: str | None = None
    mock: bool = False
    split_role: str | None = None

    def __post_init__(self) -> None:
        if not _TENANT_RE.match(self.tenant_id):
            raise ValueError(
                f"tenant_id {self.tenant_id!r} must be 1-128 characters of letters, "
                "digits, '.', '_' or '-', starting with a letter or digit"
            )
        if self.origin not in ORIGINS:
            raise ValueError(f"origin {self.origin!r} must be one of {sorted(ORIGINS)}")
        if self.split_role is not None and self.split_role not in SPLIT_ROLES:
            raise ValueError(f"split_role {self.split_role!r} must be one of {sorted(SPLIT_ROLES)}")

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "upstream_consent": self.upstream_consent,
            "workload_id": self.workload_id,
            "origin": self.origin,
            "rubric_version": self.rubric_version,
            "gold_version": self.gold_version,
            "mock": self.mock,
            "split_role": self.split_role,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> RecordContext:
        return cls(
            tenant_id=str(d["tenant_id"]),
            upstream_consent=bool(d.get("upstream_consent", False)),
            workload_id=d.get("workload_id"),
            origin=str(d.get("origin", ORIGIN_COVERAGE)),
            rubric_version=d.get("rubric_version"),
            gold_version=d.get("gold_version"),
            mock=bool(d.get("mock", False)),
            split_role=d.get("split_role"),
        )


def provenance() -> dict[str, Any]:
    """Which build of Instar made a record: package version plus git sha if known."""
    sha: str | None = None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0:
            sha = out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        sha = None
    return {"instar_version": __version__, "git_sha": sha}


def new_run_id(now: datetime) -> str:
    """Sortable by time, unique without coordination."""
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _sample_origin(meta: dict[str, Any], default: str) -> str:
    origin = meta.get("origin", default)
    if origin not in ORIGINS:
        raise ValueError(f"sample origin {origin!r} must be one of {sorted(ORIGINS)}")
    return str(origin)


def _sample_reaction(meta: dict[str, Any]) -> str | None:
    reaction = meta.get("reaction")
    if reaction is None:
        return None
    if reaction not in REACTIONS:
        raise ValueError(f"sample reaction {reaction!r} must be one of {sorted(REACTIONS)}")
    return str(reaction)


def _role(arm: str, baseline: str, control: str | None) -> str:
    if arm == baseline:
        return ROLE_BASELINE
    if arm == control:
        return ROLE_CONTROL
    return ROLE_CANDIDATE


def build_call_records(
    result: ArmsResult,
    transcript: Transcript,
    ctx: RecordContext,
    *,
    run_id: str,
    recorded_at: str,
    generation_run_id: str,
    generation_path: str,
) -> list[dict[str, Any]]:
    """One record per (transcript entry, arm), in transcript order.

    ``generation_run_id`` / ``generation_path`` locate the transcript holding
    the text: this run's own for an arms run, the source run's for a re-judge.
    """
    control = result.control
    judge = result.judge.to_json() if result.judge is not None else None
    records: list[dict[str, Any]] = []
    for i, entry in enumerate(transcript.entries):
        sample = entry.sample
        origin = _sample_origin(sample.meta, ctx.origin)
        reaction = _sample_reaction(sample.meta)
        # Eligible for a shared corpus only if the tenant opted in and this
        # row did not opt out. A row cannot opt in on the tenant's behalf.
        consent = ctx.upstream_consent and bool(sample.meta.get("upstream_consent", True))
        for arm in transcript.arm_names:
            c = entry.completions[arm]
            judged = result.judgments.get(arm)
            jr = judged[i] if judged is not None else None
            records.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_id": uuid.uuid4().hex,
                    "run_id": run_id,
                    "recorded_at": recorded_at,
                    "tenant_id": ctx.tenant_id,
                    "upstream_consent": consent,
                    "workload_id": ctx.workload_id,
                    "feature": sample.feature,
                    "sample_id": sample.id,
                    "origin": origin,
                    "sample_index": entry.repeat,
                    "entry_index": i,
                    "arm": arm,
                    "role": _role(arm, transcript.baseline, control),
                    "backend": transcript.arm_backends.get(arm),
                    "model_requested": transcript.arm_models[arm],
                    "model_served": c.model or None,
                    "ok": c.ok,
                    "error": c.error,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "latency_s": c.latency_s,
                    "cost_usd": c.cost_usd,
                    "generation": {
                        "run_id": generation_run_id,
                        "path": generation_path,
                        "entry_index": i,
                    },
                    "judge": judge if judged is not None else None,
                    "score": jr.score if jr is not None else None,
                    "rationale": jr.rationale if jr is not None else None,
                    "rubric_version": ctx.rubric_version,
                    "gold_version": ctx.gold_version,
                    "reaction": reaction,
                    "mock": ctx.mock,
                }
            )
    return records


def _run_dir(corpus_dir: Path, tenant_id: str, now: datetime, run_id: str) -> Path:
    return corpus_dir / tenant_id / f"{now:%Y}" / f"{now:%m}" / run_id


def write_run(
    corpus_dir: str | Path,
    result: ArmsResult,
    transcript: Transcript,
    ctx: RecordContext,
    *,
    source_run_dir: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Append one run to the corpus and return its directory.

    Without ``source_run_dir`` this is an arms run: its transcript is written
    beside its records. With it, this is a re-judge of the run in that
    directory: no transcript is copied, and every call record points at the
    source run's transcript.

    Refuses to write into an existing directory — a corpus is never edited.
    """
    corpus = Path(corpus_dir)
    now = now or datetime.now(UTC)
    run_id = new_run_id(now)
    recorded_at = now.isoformat(timespec="seconds")
    run_dir = _run_dir(corpus, ctx.tenant_id, now, run_id)
    if run_dir.exists():
        raise FileExistsError(f"refusing to overwrite corpus run {run_dir}")

    if source_run_dir is None:
        kind = KIND_ARMS
        source_run_id: str | None = None
        generation_run_id = run_id
        generation_path = "transcript.json"
    else:
        src = Path(source_run_dir)
        source_run_id = src.name
        kind = KIND_REJUDGE
        generation_run_id = source_run_id
        generation_path = str(src.resolve().relative_to(corpus.resolve()) / "transcript.json")

    calls = build_call_records(
        result,
        transcript,
        ctx,
        run_id=run_id,
        recorded_at=recorded_at,
        generation_run_id=generation_run_id,
        generation_path=generation_path,
    )

    run_record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "kind": kind,
        "recorded_at": recorded_at,
        "source_run_id": source_run_id,
        **provenance(),
        **ctx.to_json(),
        "n_calls_per_arm": result.n,
        "n_records": len(calls),
        "baseline": result.baseline,
        "control": result.control,
        "judge": result.judge.to_json() if result.judge is not None else None,
        "trustworthy": result.trustworthy,
        "warnings": list(result.warnings),
        "arms": result.to_json()["arms"],
        "deltas": result.deltas(),
    }

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")
    with open(run_dir / "calls.jsonl", "w", encoding="utf-8") as fh:
        for rec in calls:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if kind == KIND_ARMS:
        transcript.save(run_dir / "transcript.json")
    return run_dir


def load_run_context(run_dir: str | Path) -> RecordContext:
    """The context a corpus run was recorded under, for re-judging it."""
    d = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    return RecordContext.from_json(d)


def find_corpus_root(run_dir: str | Path) -> Path | None:
    """The corpus directory a run directory lives in, or None if it is not one.

    A corpus run sits at ``<corpus>/<tenant>/<YYYY>/<MM>/<run_id>``.
    """
    p = Path(run_dir).resolve()
    if not (p / "run.json").is_file() or len(p.parents) < 4:
        return None
    return p.parents[3]
