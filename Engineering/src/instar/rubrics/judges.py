# SPDX-License-Identifier: Apache-2.0
"""The shipped judges: deterministic, objective, model-based, and dispatching.

Pick by what your workload actually gives you:

- :class:`LabelMatchJudge` — you have ground-truth labels. Use this. It is exact,
  free, instant, and needs no second model whose judgment you would then have to
  trust.
- :class:`LLMJudge` — open-ended generation with no ground truth. A model grades
  the trade. Validate it on a sample you have graded by hand before believing it.
- :class:`AutoJudge` — a mixed workload. Routes each sample to whichever of the
  above applies.
- :class:`MockJudge` — hermetic runs. Produces a plausible curve shape and
  measures nothing.

If your workload permits objective scoring, prefer it. An LLM judge is a
measurement instrument with its own error, and a cost study that rests on an
unvalidated one has just moved the uncertainty rather than removed it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from instar.core.catalog import BACKGROUND, FeatureCatalog
from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult
from instar.rubrics.base import Judge, JudgeResult


class MockJudge(Judge):
    """Deterministic judge for hermetic runs.

    Produces a stable score per ``(sample, model pair)`` so a sweep yields a
    smooth, reproducible curve. Background samples score higher than foreground
    ones, which makes the mock curve *shaped* like a real one.

    It is not a measurement. It never looks at the completion text.
    """

    name = "mock"

    def __init__(self, catalog: FeatureCatalog | None = None) -> None:
        self.catalog = catalog

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        seed = hashlib.sha256(f"{sample.id}:{strong.model}:{weak.model}".encode()).hexdigest()
        jitter = (int(seed[:4], 16) % 100) / 1000.0  # 0.000 .. 0.099
        category = (
            self.catalog.category_for(sample) if self.catalog is not None else sample.category
        )
        base = 0.92 if category == BACKGROUND else 0.75
        return JudgeResult(min(1.0, base + jitter), "mock judge (deterministic, not a measurement)")


class LabelMatchJudge(Judge):
    """Objective scorer for classification: did the weak model land the label?

    Classification is the one case that needs no LLM judge — the answer is right
    or wrong against a known label, so quality is an exact match rather than an
    opinion. That objectivity is exactly why a classification workload is the
    best first candidate when evaluating a cheap or self-hosted model: you can
    score it with a string compare and defend the result to anyone.

    Scores against ``sample.meta["gold"]`` when present, falling back to the
    label found in the strong model's own output. A weak output with no
    recognizable label scores ``0.0`` — a refusal or an invented category is a
    real routing failure, not a tie.

    In mock mode the synthetic backends emit no labels, so use
    :class:`MockJudge` there; this judge is for live runs.
    """

    name = "label_match"

    def __init__(self, labels: Iterable[str]) -> None:
        # Longest-first so a multi-word label wins over a substring collision
        # (e.g. "account_access" must not be shadowed by "access").
        self.labels = sorted({label.lower() for label in labels}, key=len, reverse=True)

    def _extract(self, text: str) -> str | None:
        lowered = (text or "").lower()
        for label in self.labels:
            if label in lowered:
                return label
        return None

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        gold = str(sample.meta.get("gold") or "").lower() or self._extract(strong.text)
        pred = self._extract(weak.text)
        if not gold:
            return JudgeResult(0.0, "no gold label, and strong output had no recognizable label")
        if pred is None:
            return JudgeResult(0.0, f"weak output had no recognizable label (gold={gold})")
        match = pred == gold
        return JudgeResult(
            1.0 if match else 0.0,
            f"weak={pred} vs gold={gold} -> {'match' if match else 'miss'}",
        )


class LLMJudge(Judge):
    """LLM-as-judge for open-ended generation.

    Three rungs, because two is not enough and five is false precision:

    - ``PASS`` (1.0) — as good as the strong answer; ship it.
    - ``MARGINAL`` (0.5) — usable but clearly worse; the user would likely
      re-prompt.
    - ``FAIL`` (0.0) — wrong or unusable; it would have to be redone.

    The ``MARGINAL`` rung is the one that earns its keep. A cheap answer that
    triggers a retry is not a saving — the user pays again, in a second call and
    in their own patience. Collapsing it into PASS is how a routing change looks
    free on a spreadsheet and costs money in production.

    Validate this judge against hand-graded examples before you trust a number
    from it.
    """

    name = "llm"

    SYSTEM_PROMPT = (
        "You are a strict evaluator. You are shown a TASK and two AI answers to it: "
        "a STRONG answer (premium model) and a WEAK answer (cheaper model). Decide "
        "whether the WEAK answer is good enough to ship to the user in place of the "
        "STRONG one. Reply with EXACTLY ONE word:\n"
        "PASS - as good as the strong answer; ship it.\n"
        "MARGINAL - usable but clearly worse; the user would likely refine it.\n"
        "FAIL - wrong, off-target, or unusable; would have to be redone.\n"
        "Reply with only that one word."
    )

    def __init__(self, judge_backend: Backend, judge_model: str) -> None:
        self.judge_backend = judge_backend
        self.judge_model = judge_model

    @staticmethod
    def _task_text(sample: TrafficSample) -> str:
        return "\n".join(
            m.get("content", "") for m in sample.messages if isinstance(m.get("content"), str)
        )

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        prompt = (
            f"TASK (feature={sample.feature}):\n{self._task_text(sample)}\n\n"
            f"STRONG answer:\n{strong.text}\n\n"
            f"WEAK answer:\n{weak.text}\n\nVerdict:"
        )
        probe = TrafficSample(
            id=f"judge-{sample.id}",
            feature="judge",
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8,
        )
        result = self.judge_backend.complete(probe, self.judge_model)
        if not result.ok:
            return JudgeResult(0.0, f"judge call failed: {result.error}")
        verdict = (result.text or "").strip().upper()
        if "FAIL" in verdict:
            return JudgeResult(0.0, "llm judge: FAIL")
        if "MARGINAL" in verdict:
            return JudgeResult(0.5, "llm judge: MARGINAL (would likely induce a retry)")
        if "PASS" in verdict:
            return JudgeResult(1.0, "llm judge: PASS")
        return JudgeResult(0.5, f"llm judge: unparsed verdict {verdict[:20]!r}")


class BlindPairwiseJudge(Judge):
    """:class:`LLMJudge` with the labels removed and the positions shuffled.

    :class:`LLMJudge` tells the judge outright which answer came from the
    premium model. That is a hint about the answer it is being asked to
    evaluate, and a model that has been told "this one is the expensive one"
    has a reason to prefer it that has nothing to do with the text. Position
    also matters: LLM judges are known to favour whichever candidate they read
    first.

    This judge removes both cues. The two answers are presented as "Answer 1"
    and "Answer 2" with no provenance, and which one goes first is decided by a
    hash of the sample id — deterministic, so a run is reproducible, but
    uncorrelated with which arm produced it.

    It is not a replacement for :class:`LLMJudge` so much as a **control on
    it**. Run both: if they agree, the quality number is robust to labelling
    bias; if they diverge, you have learned something about your judge rather
    than about your models, and neither number should be quoted until you know
    which.

    The same caveat applies as to any LLM judge — validate against hand-graded
    examples before trusting it — plus one more: a judge from the same model
    family as one of the arms may favour its own house style, and blinding does
    not fix that. Only a judge from a different family does.
    """

    name = "blind-pairwise"

    SYSTEM_PROMPT = (
        "You are a strict evaluator. You are shown a TASK and two AI answers to "
        "it. You do not know which model produced either answer, and they are "
        "not in any meaningful order. Judge only the text.\n"
        "Decide whether ANSWER B is good enough to ship to the user in place of "
        "ANSWER A. Reply with EXACTLY ONE word:\n"
        "PASS - as good as answer A; ship it.\n"
        "MARGINAL - usable but clearly worse than A; the user would likely refine it.\n"
        "FAIL - wrong, off-target, or unusable; would have to be redone.\n"
        "Reply with only that one word."
    )

    def __init__(self, judge_backend: Backend, judge_model: str) -> None:
        self.judge_backend = judge_backend
        self.judge_model = judge_model

    @staticmethod
    def _first_is_baseline(sample_id: str) -> bool:
        """Deterministic coin flip from the sample id.

        Deterministic so the same run reproduces; hashed so the result is
        uncorrelated with anything about the arms. ``hash()`` is salted per
        process and would break reproducibility, hence sha256.
        """
        digest = hashlib.sha256(sample_id.encode("utf-8")).digest()
        return digest[0] % 2 == 0

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        baseline_first = self._first_is_baseline(sample.id)
        first, second = (strong, weak) if baseline_first else (weak, strong)
        # A and B name POSITIONS, and the system prompt asks about "B in place
        # of A". So the labels have to follow the shuffle: whichever position
        # holds the baseline is A.
        label_first, label_second = ("A", "B") if baseline_first else ("B", "A")
        prompt = (
            f"TASK (feature={sample.feature}):\n{LLMJudge._task_text(sample)}\n\n"
            f"ANSWER {label_first}:\n{first.text}\n\n"
            f"ANSWER {label_second}:\n{second.text}\n\nVerdict:"
        )
        probe = TrafficSample(
            id=f"blindjudge-{sample.id}",
            feature="judge",
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8,
        )
        result = self.judge_backend.complete(probe, self.judge_model)
        if not result.ok:
            return JudgeResult(0.0, f"judge call failed: {result.error}")
        verdict = (result.text or "").strip().upper()
        if "FAIL" in verdict:
            return JudgeResult(0.0, "blind judge: FAIL")
        if "MARGINAL" in verdict:
            return JudgeResult(0.5, "blind judge: MARGINAL (would likely induce a retry)")
        if "PASS" in verdict:
            return JudgeResult(1.0, "blind judge: PASS")
        return JudgeResult(0.5, f"blind judge: unparsed verdict {verdict[:20]!r}")


class AutoJudge(Judge):
    """Dispatch each sample to the judge that fits it.

    Samples carrying a gold label are scored objectively; everything else goes
    to the LLM judge. This is what lets one run score a realistic mixed
    workload, where a classification batch sits alongside generative work.
    """

    name = "auto"

    def __init__(self, label_judge: Judge | None, llm_judge: Judge) -> None:
        self.label_judge = label_judge
        self.llm_judge = llm_judge

    def score(
        self, sample: TrafficSample, strong: CompletionResult, weak: CompletionResult
    ) -> JudgeResult:
        if sample.meta.get("gold") and self.label_judge is not None:
            return self.label_judge.score(sample, strong, weak)
        return self.llm_judge.score(sample, strong, weak)
