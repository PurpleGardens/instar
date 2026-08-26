# SPDX-License-Identifier: Apache-2.0
"""Saved generations, so a run can be judged more than once.

An A/B/C run answers two questions with one set of API calls: what each arm
cost, and how good its answers were. The first is arithmetic. The second is an
*opinion*, produced by whatever judge happened to be configured — and the judge
is a measurement instrument with its own bias.

The obvious way to check a judge is to try another one. Without a transcript
that means running the whole workload again, which regenerates every answer and
confounds the thing you wanted to isolate: a score that moved could be a
different judge, or it could be a different set of answers. You cannot tell.

So :func:`instar.core.arms.run_arms` can capture what each arm actually said,
and :func:`instar.core.arms.rejudge` replays those saved answers past a new
judge. Generations are paid for once; judging is cheap and repeatable, and any
difference between two judges' numbers is attributable to the judges alone.

The transcript holds model output, so treat it like the workload it came from:
if the fixture was private, the transcript is private too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from instar.core.traffic import TrafficSample
from instar.providers.base import CompletionResult

FORMAT_VERSION = 1


@dataclass
class TranscriptEntry:
    """One prompt, and what every arm answered.

    ``completions`` is keyed by arm name. Every arm answers every prompt, so a
    missing key means that arm was not part of the run rather than that it
    declined — a failed call is present with ``ok=False``.
    """

    sample: TrafficSample
    completions: dict[str, CompletionResult]

    def to_json(self) -> dict[str, Any]:
        return {
            "sample": self.sample.to_json(),
            "completions": {
                name: {
                    "text": c.text,
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "latency_s": c.latency_s,
                    "ok": c.ok,
                    "error": c.error,
                    "cost_usd": c.cost_usd,
                }
                for name, c in self.completions.items()
            },
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> TranscriptEntry:
        return cls(
            sample=TrafficSample.from_json(d["sample"]),
            completions={
                name: CompletionResult(
                    text=c["text"],
                    model=c["model"],
                    input_tokens=c["input_tokens"],
                    output_tokens=c["output_tokens"],
                    latency_s=c["latency_s"],
                    ok=c.get("ok", True),
                    error=c.get("error"),
                    cost_usd=c.get("cost_usd"),
                )
                for name, c in d["completions"].items()
            },
        )


@dataclass
class Transcript:
    """Every generation from one arms run, in the order the arms walked it.

    Order matters: :func:`instar.core.arms.judge_arm` lines arms up positionally,
    so the entries must be replayed in the sequence they were produced.
    """

    baseline: str
    arm_models: dict[str, str]
    entries: list[TranscriptEntry] = field(default_factory=list)

    @property
    def arm_names(self) -> list[str]:
        return list(self.arm_models)

    def to_json(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "baseline": self.baseline,
            "arm_models": self.arm_models,
            "entries": [e.to_json() for e in self.entries],
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Transcript:
        version = d.get("format_version")
        if version != FORMAT_VERSION:
            raise ValueError(
                f"transcript format_version {version!r} is not supported "
                f"(this build reads {FORMAT_VERSION})"
            )
        if d["baseline"] not in d["arm_models"]:
            raise ValueError(f"baseline {d['baseline']!r} is not one of the recorded arms")
        return cls(
            baseline=d["baseline"],
            arm_models=dict(d["arm_models"]),
            entries=[TranscriptEntry.from_json(e) for e in d["entries"]],
        )

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> Transcript:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
