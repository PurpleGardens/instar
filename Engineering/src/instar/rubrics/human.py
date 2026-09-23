# SPDX-License-Identifier: Apache-2.0
"""Human grading: a person as the judge, recorded like any other judge.

Every model-based judge in Instar carries the same warning — validate it
against hand-graded examples before trusting a number from it — and until now
there was nowhere to put the hand grades. This module closes that loop in two
steps, both offline:

1. :func:`write_grading_sheet` turns a saved ``instar arms`` transcript into a
   CSV a person can fill in with a spreadsheet: one row per (prompt, candidate
   answer), the reference answer beside it, and a ``grade`` column taking
   ``PASS`` / ``MARGINAL`` / ``FAIL`` — the same three rungs as
   :class:`~instar.rubrics.judges.LLMJudge`, so human and model scores are on
   one scale.
2. :class:`HumanJudge` reads the filled sheet back and scores the transcript
   through :func:`instar.core.arms.rejudge`. Cost and latency replay unchanged,
   so the human's numbers sit beside every LLM judge's numbers on the *same
   answers* — which is what makes judge-vs-human agreement measurable.

**What the grader sees.** The task, the reference answer (the baseline arm's),
and one candidate answer. Never an arm name or a model id: provenance is a
reason to prefer an answer that has nothing to do with its text. Rows are
shuffled so one arm's answers don't arrive in a block. The reference always
sits in the same column, because the question is relative ("good enough to
ship *in place of* the reference?") and a person needs to know which is which.
Position is therefore not blinded, and :class:`HumanJudge` records itself as
blind to provenance only.

**Item ids are content-addressed.** An id is a hash of the prompt and the two
answers, not of a row number, so a sheet stays valid however it was sorted or
filtered, and a pair that appears twice (two repeats that produced the same
text) is graded once. A sheet from a different transcript is rejected rather
than silently matching nothing.

**Partial grading is allowed.** A person may grade a sample of the rows. An
ungraded pair is *unscored*, never a pass: :meth:`HumanJudge.abstains` tells
the runner to skip it, and the report counts only what was graded.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

from instar.core.traffic import TrafficSample
from instar.core.transcript import Transcript
from instar.providers.base import CompletionResult, sample_text
from instar.rubrics.base import Judge, JudgeKey, JudgeResult

HUMAN_FAMILY = "human"

# Same rungs as LLMJudge, so a human score and a model score mean the same thing.
GRADES = {"PASS": 1.0, "MARGINAL": 0.5, "FAIL": 0.0}

SHEET_COLUMNS = (
    "item_id",
    "feature",
    "task",
    "reference_answer",
    "candidate_answer",
    "grade",
    "note",
)

# utf-8-sig writes a byte-order mark so spreadsheet apps open the file as UTF-8,
# and reads a file with or without one.
_ENCODING = "utf-8-sig"


def pair_id(sample: TrafficSample, reference: CompletionResult, candidate: CompletionResult) -> str:
    """Stable, opaque id for one (prompt, reference, candidate) pair.

    Hashed from content so it survives sorting and filtering in a spreadsheet,
    and reveals nothing about which arm produced the candidate.
    """
    payload = json.dumps(
        [sample.id, sample_text(sample), reference.text, candidate.text], ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SheetRow:
    item_id: str
    feature: str
    task: str
    reference_answer: str
    candidate_answer: str


def grading_rows(transcript: Transcript, *, seed: int = 0) -> list[SheetRow]:
    """Every gradable pair in ``transcript``, de-duplicated and shuffled.

    A pair is gradable when both the baseline and the candidate call succeeded,
    matching :func:`instar.core.arms.judge_calls`, which skips failed pairs
    rather than scoring them. The shuffle is seeded so an export reproduces.
    """
    base = transcript.baseline
    rows: dict[str, SheetRow] = {}
    for entry in transcript.entries:
        ref = entry.completions[base]
        if not ref.ok:
            continue
        for arm in transcript.arm_names:
            if arm == base:
                continue
            cand = entry.completions[arm]
            if not cand.ok:
                continue
            pid = pair_id(entry.sample, ref, cand)
            rows.setdefault(
                pid,
                SheetRow(
                    item_id=pid,
                    feature=entry.sample.feature,
                    task=sample_text(entry.sample).strip(),
                    reference_answer=ref.text,
                    candidate_answer=cand.text,
                ),
            )
    ordered = [rows[k] for k in sorted(rows)]
    random.Random(seed).shuffle(ordered)
    return ordered


def write_grading_sheet(
    transcript: Transcript, path: str | Path, *, seed: int = 0, overwrite: bool = False
) -> int:
    """Write the grading sheet CSV; return the number of rows.

    Refuses to replace an existing file unless ``overwrite`` is set: the file
    at that path may be a sheet someone has spent an afternoon grading.
    """
    p = Path(path)
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} already exists; refusing to overwrite a grading sheet")
    rows = grading_rows(transcript, seed=seed)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding=_ENCODING, newline="") as f:
        w = csv.writer(f)
        w.writerow(SHEET_COLUMNS)
        for r in rows:
            w.writerow(
                [r.item_id, r.feature, r.task, r.reference_answer, r.candidate_answer, "", ""]
            )
    return len(rows)


@dataclass(frozen=True)
class Grade:
    score: float
    verdict: str
    note: str


def load_grades(path: str | Path) -> dict[str, Grade]:
    """Read a filled grading sheet. Blank grades are skipped, not failed.

    Raises ``ValueError`` naming the line for an unknown grade, a missing
    column, or one item graded two different ways.
    """
    p = Path(path)
    grades: dict[str, Grade] = {}
    with p.open(encoding=_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        missing = {"item_id", "grade"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{p}: missing column(s) {sorted(missing)}")
        for row in reader:
            line = reader.line_num
            item = (row.get("item_id") or "").strip()
            raw = (row.get("grade") or "").strip().upper()
            if not item or not raw:
                continue
            if raw not in GRADES:
                raise ValueError(f"{p}:{line}: grade {raw!r} is not one of {', '.join(GRADES)}")
            grade = Grade(GRADES[raw], raw, (row.get("note") or "").strip())
            prior = grades.get(item)
            if prior is not None and prior.verdict != grade.verdict:
                raise ValueError(
                    f"{p}:{line}: item {item} graded both {prior.verdict} and {grade.verdict}"
                )
            grades[item] = grade
    return grades


class HumanJudge(Judge):
    """Scores pairs from a person's filled grading sheet.

    ``grader`` is a pseudonymous id (``grader-1``, initials) and is written
    into ``result.json`` and any corpus the run is recorded in, so it should
    not be a name or an email address. It is stored as the judge key's
    ``model``, with family ``"human"``, so the corpus can tell two graders
    apart and compare each of them with the model judges.
    """

    name = "human"

    def __init__(self, grades: dict[str, Grade], grader: str) -> None:
        grader = grader.strip()
        if not grader:
            raise ValueError("a human judge needs a grader id")
        if "@" in grader:
            raise ValueError(
                "grader id looks like an email address; use a pseudonym such as grader-1"
            )
        self.grades = grades
        self.grader = grader

    @classmethod
    def for_transcript(cls, transcript: Transcript, path: str | Path, grader: str) -> HumanJudge:
        """Load ``path`` and check it was exported from ``transcript``.

        Any graded id the transcript cannot produce means the wrong sheet (or a
        hand-edited id), and scoring it would silently grade nothing.
        """
        grades = load_grades(path)
        known = {r.item_id for r in grading_rows(transcript)}
        stray = sorted(set(grades) - known)
        if stray:
            raise ValueError(
                f"{path}: {len(stray)} graded item(s) are not in this transcript "
                f"(first: {stray[0]}); was the sheet exported from a different run?"
            )
        return cls(grades, grader)

    def key(self) -> JudgeKey:
        return JudgeKey(kind=self.name, model=self.grader, family=HUMAN_FAMILY, blind=True)

    def abstains(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> bool:
        return pair_id(sample, strong, weak) not in self.grades

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        grade = self.grades.get(pair_id(sample, strong, weak))
        if grade is None:
            # judge_calls consults abstains() first, so this is only reachable
            # by calling score() directly. Refuse rather than invent a score.
            raise KeyError(f"no human grade for sample {sample.id!r}")
        rationale = f"human ({self.grader}): {grade.verdict}"
        if grade.note:
            rationale += f" - {grade.note}"
        return JudgeResult(grade.score, rationale)
