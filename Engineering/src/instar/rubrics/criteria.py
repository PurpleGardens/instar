# SPDX-License-Identifier: Apache-2.0
"""An absolute judge: does this answer meet the task's written criteria?

Every other model judge in Instar is *relative*: it asks whether a cheaper
answer could ship in place of the baseline's. That is the right question for a
cost study and the wrong one when nobody trusts the baseline either, when the
job is to say whether an answer is good at all, or when the thing under test is
not a model swap (an MCP server, a prompt change). For those, the definition of
"good" has to be written down independently of any one model's output.

:class:`CriteriaJudge` takes that definition as a checklist per feature: short,
checkable statements an answer either meets or doesn't ("names the refund
window", "does not invent a policy"). A model judge answers YES or NO for each
one, and the score is the fraction met. A criterion marked ``critical`` is a
gate: miss it and the answer scores 0.0 however many others it met, because an
answer that invents a refund policy is not 80% good.

It is absolute, so under ``instar arms`` the baseline is scored too, and every
arm's number is on the same scale: the share of your criteria it met.

Where criteria come from, most specific first:

1. ``meta["criteria"]`` on the sample itself;
2. the criteria file's ``features`` entry for the sample's feature;
3. the file's ``default`` list.

A sample with no criteria from any of these is **unscored** (the judge
abstains), not passed: an empty checklist does not describe a good answer.

Criteria file::

    {
      "version": "support-v1",
      "default": ["Answers the question that was asked"],
      "features": {
        "support.macro_draft": [
          "Names the refund window",
          {"id": "no-invented-policy",
           "text": "Does not state any policy the task does not give",
           "critical": true}
        ]
      }
    }

The same caveat applies as to every model judge: validate it against human
grades before trusting its numbers. Checklists make that easier, because a
disagreement points at one criterion rather than at a whole answer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult, sample_text
from instar.rubrics.base import Judge, JudgeKey, JudgeResult, model_family


@dataclass(frozen=True)
class Criterion:
    """One checkable statement about a good answer."""

    id: str
    text: str
    critical: bool = False


def _parse_criteria(raw: Any, where: str) -> list[Criterion]:
    """A list of strings and/or ``{id?, text, critical?}`` objects."""
    if not isinstance(raw, list):
        raise ValueError(f"{where}: criteria must be a list")
    out: list[Criterion] = []
    for i, item in enumerate(raw, start=1):
        text: Any
        if isinstance(item, str):
            text, cid, critical = item, f"c{i}", False
        elif isinstance(item, Mapping):
            text = item.get("text")
            cid = str(item.get("id") or f"c{i}")
            critical = bool(item.get("critical", False))
        else:
            raise ValueError(f"{where}[{i - 1}]: a criterion is a string or an object")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}[{i - 1}]: criterion text is empty")
        out.append(Criterion(cid, text.strip(), critical))
    ids = [c.id for c in out]
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        raise ValueError(f"{where}: duplicate criterion id(s) {dupes}")
    return out


@dataclass(frozen=True)
class CriteriaSet:
    """Criteria per feature, with a default, loaded from a JSON file."""

    version: str | None
    default: tuple[Criterion, ...]
    features: Mapping[str, tuple[Criterion, ...]]

    @classmethod
    def from_json(cls, d: Mapping[str, Any], where: str = "criteria") -> CriteriaSet:
        features_raw = d.get("features", {})
        if not isinstance(features_raw, Mapping):
            raise ValueError(f"{where}: 'features' must be an object keyed by feature")
        default = tuple(_parse_criteria(d.get("default", []), f"{where}.default"))
        features = {
            str(k): tuple(_parse_criteria(v, f"{where}.features[{k!r}]"))
            for k, v in features_raw.items()
        }
        if not default and not any(features.values()):
            raise ValueError(f"{where}: no criteria defined")
        version = d.get("version")
        return cls(None if version is None else str(version), default, features)

    @classmethod
    def load(cls, path: str | Path) -> CriteriaSet:
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}: not valid JSON ({e})") from e
        if not isinstance(data, Mapping):
            raise ValueError(f"{p}: expected a JSON object")
        return cls.from_json(data, where=str(p))

    def for_sample(self, sample: TrafficSample) -> tuple[Criterion, ...]:
        own = sample.meta.get("criteria")
        if own:
            return tuple(_parse_criteria(own, f"sample {sample.id} meta.criteria"))
        return self.features.get(sample.feature) or self.default


_VERDICT_LINE = re.compile(r"^\s*(\d+)\s*[:.)\-]\s*(YES|NO)\b", re.IGNORECASE | re.MULTILINE)


def parse_verdicts(text: str, n: int) -> dict[int, bool]:
    """``{criterion number: met}`` from a judge reply, first answer per number wins."""
    out: dict[int, bool] = {}
    for m in _VERDICT_LINE.finditer(text or ""):
        k = int(m.group(1))
        if 1 <= k <= n and k not in out:
            out[k] = m.group(2).upper() == "YES"
    return out


def criteria_score(criteria: Sequence[Criterion], met: Mapping[int, bool]) -> JudgeResult:
    """Fraction of criteria met, gated by critical ones.

    A criterion the judge gave no verdict on counts as **not met**: an
    unreadable reply must not read as a pass. The rationale says so.
    """
    failed = [c for i, c in enumerate(criteria, start=1) if not met.get(i, False)]
    unread = [c.id for i, c in enumerate(criteria, start=1) if i not in met]
    critical = [c.id for c in failed if c.critical]
    score = 0.0 if critical else (len(criteria) - len(failed)) / len(criteria)
    parts = [f"criteria: {len(criteria) - len(failed)}/{len(criteria)} met"]
    if critical:
        parts.append(f"critical failed: {', '.join(critical)}")
    missed = [c.id for c in failed if not c.critical]
    if missed:
        parts.append(f"missed: {', '.join(missed)}")
    if unread:
        parts.append(f"no verdict (counted as missed): {', '.join(unread)}")
    return JudgeResult(score, "; ".join(parts))


class CriteriaJudge(Judge):
    """An LLM checks one answer against the task's criteria, one YES/NO each.

    Absolute: ``strong`` is ignored and only ``weak`` (the answer under test)
    is scored. The judge sees the task (system prompt and messages), the answer
    and the numbered criteria, never which model wrote the answer.
    """

    name = "criteria"
    absolute = True

    SYSTEM_PROMPT = (
        "You are a strict evaluator. You are shown a TASK, an ANSWER to it, and "
        "numbered CRITERIA. For each criterion, decide whether the ANSWER meets it. "
        "Judge only the answer's text against the task; do not reward length or "
        "style the criteria do not ask for. Reply with exactly one line per "
        "criterion, in order, in the form '<number>: YES' or '<number>: NO', and "
        "nothing else."
    )

    def __init__(
        self,
        criteria: CriteriaSet,
        judge_backend: Backend,
        judge_model: str,
        *,
        family: str | None = None,
    ) -> None:
        self.criteria = criteria
        self.judge_backend = judge_backend
        self.judge_model = judge_model
        self.family = family or model_family(judge_model)

    def key(self) -> JudgeKey:
        return JudgeKey(
            kind=self.name,
            model=self.judge_model,
            family=self.family,
            blind=True,
            absolute=True,
            version=self.criteria.version,
        )

    def abstains(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> bool:
        return not self.criteria.for_sample(sample)

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        criteria = self.criteria.for_sample(sample)
        if not criteria:
            raise KeyError(f"no criteria for sample {sample.id!r} (feature {sample.feature!r})")
        listed = "\n".join(f"{i}. {c.text}" for i, c in enumerate(criteria, start=1))
        prompt = (
            f"TASK (feature={sample.feature}):\n{sample_text(sample).strip()}\n\n"
            f"ANSWER:\n{weak.text}\n\nCRITERIA:\n{listed}\n\nVerdicts:"
        )
        probe = TrafficSample(
            id=f"criteria-{sample.id}",
            feature="judge",
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8 * len(criteria) + 16,
            temperature=0.0,
        )
        result = self.judge_backend.complete(probe, self.judge_model)
        if not result.ok:
            return JudgeResult(0.0, f"judge call failed: {result.error}")
        return criteria_score(criteria, parse_verdicts(result.text, len(criteria)))


class MockCriteriaBackend(Backend):
    """Deterministic YES/NO verdicts for mock runs. Measures nothing.

    Each verdict is a hash of the answer text and the criterion number, so a
    mock run exercises parsing, gating and reporting reproducibly without a
    model or a network.
    """

    name = "mock-criteria"

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        text = sample_text(sample)
        n = text.count("\n") + 1
        listed = text.split("CRITERIA:\n", 1)[-1].split("\n\nVerdicts:", 1)[0]
        count = sum(1 for line in listed.splitlines() if re.match(r"^\d+\. ", line))
        answer = text.split("ANSWER:\n", 1)[-1].split("\n\nCRITERIA:", 1)[0]
        lines = []
        for k in range(1, count + 1):
            h = hashlib.sha256(f"{answer}|{k}".encode()).digest()[0]
            lines.append(f"{k}: {'NO' if h % 4 == 0 else 'YES'}")
        return CompletionResult(
            text="\n".join(lines),
            model=model,
            input_tokens=n,
            output_tokens=len(lines),
            latency_s=0.0,
        )
