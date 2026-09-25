# SPDX-License-Identifier: Apache-2.0
"""The judging interface: what did we give up by using the cheaper model?

A :class:`Judge` scores a weak completion **relative to the strong one** on the
same input, in ``[0, 1]``, where ``1.0`` means "the weak output is as good as
the strong output for this call."

Relative scoring is deliberate. The question a cost study has to answer is not
"is this output good?" in the abstract — it is "what do we lose by routing this
call to the cheap model?" Only a paired comparison answers that, and only a
paired comparison stays meaningful when your workload has no ground truth.

Where ground truth *does* exist — classification with known labels — use an
objective scorer instead. It is cheaper, faster, and not itself a model whose
judgment you would then have to validate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from instar.core.traffic import TrafficSample
from instar.providers.base import CompletionResult


@dataclass(frozen=True)
class JudgeResult:
    """A quality score in ``[0, 1]`` plus the reasoning behind it.

    ``rationale`` lands in the per-sample report rows. A score nobody can audit
    is a number nobody should act on.
    """

    score: float
    rationale: str


# Vendor prefixes and name fragments -> the family whose house style a judge
# may share. Family is vendor-level on purpose: the bias a cross-family check
# guards against is a judge preferring text shaped like its own maker's output.
_FAMILY_BY_VENDOR = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "meta-llama": "meta",
    "meta": "meta",
    "mistralai": "mistral",
    "mistral": "mistral",
    "qwen": "alibaba",
    "alibaba": "alibaba",
    "deepseek": "deepseek",
    "x-ai": "xai",
}
_FAMILY_BY_FRAGMENT = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini", "google"),
    ("gemma", "google"),
    ("llama", "meta"),
    ("mistral", "mistral"),
    ("mixtral", "mistral"),
    ("qwen", "alibaba"),
    ("deepseek", "deepseek"),
    ("grok", "xai"),
)
UNKNOWN_FAMILY = "unknown"


def model_family(model: str) -> str:
    """Best-effort vendor family for a model id, or ``"unknown"``.

    Router-style ids (``openai/gpt-4o``) are read by their vendor prefix; bare
    ids by a known name fragment. A guess is recorded rather than required
    because the family is what makes two judges' numbers comparable or not —
    but a wrong guess is worse than ``unknown``, so anything unrecognized says
    so. Pass the family explicitly to a judge when you know better.
    """
    m = model.strip().lower()
    if "/" in m:
        vendor = m.split("/", 1)[0]
        if vendor in _FAMILY_BY_VENDOR:
            return _FAMILY_BY_VENDOR[vendor]
        m = m.split("/", 1)[1]
    for fragment, family in _FAMILY_BY_FRAGMENT:
        if m.startswith(fragment) or f"-{fragment}" in m:
            return family
    return UNKNOWN_FAMILY


@dataclass(frozen=True)
class JudgeKey:
    """Which instrument produced a score.

    A quality number is a property of the judge as much as of the models it
    compared: one set of answers can score very differently under judges from
    different families. So every stored score carries this key, and two scores
    with different keys are not the same measurement.

    ``model`` and ``family`` are ``None`` for judges that consult no model
    (objective label matching, the mock).

    ``absolute`` marks a judge that scores each answer on its own against the
    task (a criteria checklist) rather than against the baseline's answer. The
    two scales mean different things: under a relative judge the control arm's
    true score is 1.0, under an absolute one it is whatever the baseline
    scored. Readers of a corpus need to know which they are looking at.
    """

    kind: str
    model: str | None = None
    family: str | None = None
    blind: bool = False
    absolute: bool = False
    # Version of the judge's own instructions when they are an input (a
    # criteria file). The same judge model reading a different checklist is a
    # different instrument.
    version: str | None = None

    def to_json(self) -> dict[str, str | bool | None]:
        return {
            "kind": self.kind,
            "model": self.model,
            "family": self.family,
            "blind": self.blind,
            "absolute": self.absolute,
            "version": self.version,
        }

    @classmethod
    def from_json(cls, d: dict[str, object]) -> JudgeKey:
        model = d.get("model")
        family = d.get("family")
        return cls(
            kind=str(d["kind"]),
            model=None if model is None else str(model),
            family=None if family is None else str(family),
            blind=bool(d.get("blind", False)),
            absolute=bool(d.get("absolute", False)),
            version=None if d.get("version") is None else str(d.get("version")),
        )


class Judge(ABC):
    """Scores a weak completion against the strong baseline.

    An *absolute* judge (``absolute = True``) ignores ``strong`` and scores
    ``weak`` against the task alone. The arms runner then scores the baseline
    arm as well, by passing its own answer as ``weak``, because an absolute
    score for the baseline is a measurement rather than a tautology.
    """

    name: str = "abstract"
    absolute: bool = False

    def key(self) -> JudgeKey:
        """Identify this judge. Model-based judges override to add model and family."""
        return JudgeKey(kind=self.name)

    def abstains(
        self,
        sample: TrafficSample,
        strong: CompletionResult,
        weak: CompletionResult,
    ) -> bool:
        """True when this judge has no opinion on the pair and it should go unscored.

        A model judge always has an opinion. A human grader may have graded
        only some rows; an ungraded pair is *unscored*, which is not a pass and
        not a fail, so the runner skips it the way it skips a failed call.
        """
        return False

    @abstractmethod
    def score(
        self,
        sample: TrafficSample,
        strong: CompletionResult,
        weak: CompletionResult,
    ) -> JudgeResult: ...
