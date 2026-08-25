# SPDX-License-Identifier: Apache-2.0
"""The N-arm runner: interleaving, per-arm models, and honest cost provenance."""

import pytest

from instar.core.arms import (
    COST_COMPUTED,
    COST_REPORTED,
    COST_UNAVAILABLE,
    Arm,
    resolve_arm_cost,
    run_arms,
    summarize_arm,
)
from instar.core.traffic import TrafficSample
from instar.providers.base import Backend, CompletionResult
from instar.providers.mock import MockBackend

PRICING = {"strong": (3.0, 15.0), "cheap": (0.1, 0.5)}


def _samples(n: int = 4) -> list[TrafficSample]:
    return [
        TrafficSample(id=f"s{i}", feature="a.b", messages=[{"role": "user", "content": "x"}])
        for i in range(n)
    ]


def _result(**kw: object) -> CompletionResult:
    base = {
        "text": "ok",
        "model": "strong",
        "input_tokens": 1000,
        "output_tokens": 100,
        "latency_s": 0.01,
    }
    base.update(kw)
    return CompletionResult(**base)  # type: ignore[arg-type]


class _RecordingBackend(Backend):
    """Logs (name, model) per call so ordering and per-arm models can be asserted."""

    def __init__(self, name: str, log: list[tuple[str, str]]) -> None:
        self.name = name
        self.log = log

    def complete(self, sample: TrafficSample, model: str) -> CompletionResult:
        self.log.append((self.name, model))
        return _result(model=model)


class TestInterleaving:
    def test_arms_are_interleaved_not_blocked(self):
        """Blocked runs charge network drift to whichever arm ran last."""
        log: list[tuple[str, str]] = []
        arms = [
            Arm("a", _RecordingBackend("a", log), "strong"),
            Arm("b", _RecordingBackend("b", log), "strong"),
            Arm("c", _RecordingBackend("c", log), "cheap"),
        ]
        run_arms(_samples(3), arms=arms, pricing=PRICING)
        assert [n for n, _ in log] == ["a", "b", "c"] * 3

    def test_each_arm_uses_its_own_model(self):
        """The reason this runner exists: arm C must vary the model."""
        log: list[tuple[str, str]] = []
        arms = [
            Arm("a", _RecordingBackend("a", log), "strong"),
            Arm("c", _RecordingBackend("c", log), "cheap"),
        ]
        run_arms(_samples(2), arms=arms, pricing=PRICING)
        assert set(log) == {("a", "strong"), ("c", "cheap")}

    def test_repeats_replay_the_whole_workload(self):
        log: list[tuple[str, str]] = []
        arms = [
            Arm("a", _RecordingBackend("a", log), "strong"),
            Arm("b", _RecordingBackend("b", log), "strong"),
        ]
        r = run_arms(_samples(3), arms=arms, repeats=4, pricing=PRICING)
        assert r.n == 12
        assert len(log) == 24


class TestValidation:
    def test_one_arm_is_not_a_comparison(self):
        with pytest.raises(ValueError, match="at least two arms"):
            run_arms(_samples(), arms=[Arm("a", MockBackend("a"), "strong")])

    def test_duplicate_arm_names_are_rejected(self):
        arms = [
            Arm("a", MockBackend("a"), "strong"),
            Arm("a", MockBackend("a2"), "cheap"),
        ]
        with pytest.raises(ValueError, match="unique"):
            run_arms(_samples(), arms=arms)

    def test_unknown_baseline_is_rejected(self):
        arms = [Arm("a", MockBackend("a"), "strong"), Arm("b", MockBackend("b"), "cheap")]
        with pytest.raises(ValueError, match="baseline"):
            run_arms(_samples(), arms=arms, baseline="nope")

    def test_zero_repeats_is_rejected(self):
        arms = [Arm("a", MockBackend("a"), "strong"), Arm("b", MockBackend("b"), "cheap")]
        with pytest.raises(ValueError, match="repeats"):
            run_arms(_samples(), arms=arms, repeats=0)


class TestCostProvenance:
    def test_reported_cost_wins_over_the_table(self):
        results = [_result(cost_usd=0.001) for _ in range(3)]
        total, source = resolve_arm_cost(results, model="strong", pricing=PRICING)
        assert source == COST_REPORTED
        assert total == pytest.approx(0.003)

    def test_falls_back_to_the_table_when_nothing_is_reported(self):
        results = [_result() for _ in range(3)]
        total, source = resolve_arm_cost(results, model="strong", pricing=PRICING)
        assert source == COST_COMPUTED
        assert total > 0

    def test_partially_reported_arm_does_not_mix_measured_and_estimated(self):
        """Summing real dollars with guessed ones would look like a measurement."""
        results = [_result(cost_usd=0.001), _result(cost_usd=None), _result(cost_usd=0.001)]
        _, source = resolve_arm_cost(results, model="strong", pricing=PRICING)
        assert source == COST_COMPUTED

    def test_unpriced_and_unreported_is_unknown_not_zero(self):
        results = [_result(model="mystery")]
        total, source = resolve_arm_cost(results, model="mystery", pricing=PRICING)
        assert source == COST_UNAVAILABLE
        assert total == 0.0

    def test_failed_calls_are_excluded_from_cost(self):
        results = [_result(cost_usd=0.001), CompletionResult.failure("strong", "boom")]
        total, source = resolve_arm_cost(results, model="strong", pricing=PRICING)
        assert source == COST_REPORTED
        assert total == pytest.approx(0.001)

    def test_all_failed_is_unavailable(self):
        results = [CompletionResult.failure("strong", "boom")]
        _, source = resolve_arm_cost(results, model="strong", pricing=PRICING)
        assert source == COST_UNAVAILABLE


class TestWarnings:
    def _arms(self):
        return [
            Arm("a", MockBackend("a", latency_s=0.01), "strong"),
            Arm("b", MockBackend("b", latency_s=0.01), "cheap"),
        ]

    def test_small_n_is_flagged(self):
        r = run_arms(_samples(2), arms=self._arms(), pricing=PRICING)
        assert any("tail percentiles" in w for w in r.warnings)
        assert not r.trustworthy

    def test_unknown_cost_is_flagged_loudly(self):
        arms = [
            Arm("a", MockBackend("a"), "strong"),
            Arm("b", MockBackend("b"), "unpriced-model"),
        ]
        r = run_arms(_samples(2), arms=arms, pricing=PRICING)
        assert any("cost unknown" in w and "not $0" in w for w in r.warnings)

    def test_model_substitution_is_a_finding(self):
        """A router serving something else is the thing you most want to know."""

        class _Substituting(Backend):
            name = "router"

            def complete(self, sample, model):
                return _result(model="someone/else")

        arms = [
            Arm("a", MockBackend("a"), "strong"),
            Arm("router", _Substituting(), "strong"),
        ]
        r = run_arms(_samples(2), arms=arms, pricing=PRICING)
        assert any("was served" in w and "someone/else" in w for w in r.warnings)
        assert r.by_name("router").served_models == ["someone/else"]

    def test_mixed_cost_sources_are_flagged_as_not_like_for_like(self):
        class _Reporting(Backend):
            name = "reporting"

            def complete(self, sample, model):
                return _result(model=model, cost_usd=0.001)

        arms = [
            Arm("table", MockBackend("table"), "strong"),
            Arm("reported", _Reporting(), "cheap"),
        ]
        r = run_arms(_samples(2), arms=arms, pricing=PRICING)
        assert any("not like-for-like" in w for w in r.warnings)


class TestDeltas:
    def _run(self):
        class _Slow(Backend):
            name = "slow"

            def complete(self, sample, model):
                return _result(model=model, latency_s=0.05, cost_usd=0.002)

        class _Fast(Backend):
            name = "fast"

            def complete(self, sample, model):
                return _result(model=model, latency_s=0.01, cost_usd=0.001)

        arms = [
            Arm("base", _Fast(), "strong"),
            Arm("other", _Slow(), "cheap"),
        ]
        return run_arms(_samples(4), arms=arms, pricing=PRICING)

    def test_baseline_defaults_to_the_first_arm(self):
        assert self._run().baseline == "base"

    def test_latency_delta_is_absolute_ms(self):
        d = self._run().deltas()[0]
        assert d["latency_p50_delta_ms"] == pytest.approx(40.0, abs=1.0)

    def test_cost_delta_is_a_percentage(self):
        d = self._run().deltas()[0]
        assert d["cost_delta_pct"] == pytest.approx(100.0)

    def test_cost_delta_is_omitted_when_a_cost_is_unknown(self):
        """Better a blank than a confident number derived from a zero."""
        arms = [
            Arm("base", MockBackend("base"), "strong"),
            Arm("other", MockBackend("other"), "unpriced-model"),
        ]
        r = run_arms(_samples(2), arms=arms, pricing=PRICING)
        assert r.deltas()[0]["cost_delta_pct"] is None

    def test_to_json_carries_deltas_and_per_1k(self):
        d = self._run().to_json()
        assert d["deltas"] and "cost_per_1k_calls_usd" in d["arms"][0]
        assert d["trustworthy"] is False  # small n


class TestSummarize:
    def test_per_1k_scaling(self):
        arm = Arm("a", MockBackend("a"), "strong")
        results = [_result(cost_usd=0.002) for _ in range(4)]
        s = summarize_arm(arm, results, pricing=PRICING)
        assert s.cost_per_1k_calls_usd == pytest.approx(2.0)

    def test_no_successful_calls_gives_zero_not_a_crash(self):
        arm = Arm("a", MockBackend("a"), "strong")
        s = summarize_arm(arm, [CompletionResult.failure("strong", "boom")], pricing=PRICING)
        assert s.n_ok == 0 and s.cost_per_1k_calls_usd == 0.0


class TestLatencyNormalization:
    """Raw wall-clock across arms is confounded by output length."""

    def _arm_with(self, name: str, *, out_tokens: int, latency_s: float) -> Arm:
        class _B(Backend):
            def __init__(self, n: str) -> None:
                self.name = n

            def complete(self, sample, model):
                return _result(
                    model=model,
                    output_tokens=out_tokens,
                    latency_s=latency_s,
                    cost_usd=0.001,
                )

        return Arm(name, _B(name), "strong")

    def test_tokens_are_summed_per_arm(self):
        arm = self._arm_with("a", out_tokens=50, latency_s=0.1)
        s = summarize_arm(arm, [_result(output_tokens=50) for _ in range(4)])
        assert s.output_tokens == 200

    def test_ms_per_output_token_is_length_independent(self):
        """Same speed per token, different answer lengths -> equal ratio."""
        short = summarize_arm(
            self._arm_with("s", out_tokens=10, latency_s=0.1),
            [_result(output_tokens=10, latency_s=0.1)],
        )
        long = summarize_arm(
            self._arm_with("l", out_tokens=100, latency_s=1.0),
            [_result(output_tokens=100, latency_s=1.0)],
        )
        assert short.ms_per_output_token == pytest.approx(long.ms_per_output_token)
        # ...even though raw latency differs by 10x.
        assert long.mean_ms == pytest.approx(short.mean_ms * 10)

    def test_zero_output_tokens_does_not_divide_by_zero(self):
        s = summarize_arm(
            self._arm_with("a", out_tokens=0, latency_s=0.1),
            [_result(output_tokens=0)],
        )
        assert s.ms_per_output_token == 0.0

    def test_lopsided_output_lengths_are_flagged(self):
        """A 'faster' arm that just wrote less is the classic false finding."""
        arms = [
            self._arm_with("base", out_tokens=100, latency_s=1.0),
            self._arm_with("terse", out_tokens=20, latency_s=0.3),
        ]
        r = run_arms(_samples(4), arms=arms, pricing=PRICING)
        assert any("not comparable" in w and "ms_per_output_token" in w for w in r.warnings)

    def test_similar_output_lengths_are_not_flagged(self):
        arms = [
            self._arm_with("base", out_tokens=100, latency_s=1.0),
            self._arm_with("other", out_tokens=95, latency_s=1.0),
        ]
        r = run_arms(_samples(4), arms=arms, pricing=PRICING)
        assert not any("not comparable" in w for w in r.warnings)

    def test_deltas_carry_the_ratio_and_normalized_latency(self):
        arms = [
            self._arm_with("base", out_tokens=100, latency_s=1.0),
            self._arm_with("terse", out_tokens=50, latency_s=0.5),
        ]
        d = run_arms(_samples(4), arms=arms, pricing=PRICING).deltas()[0]
        assert d["output_token_ratio"] == pytest.approx(0.5)
        # Half the wall-clock, half the tokens -> identical per-token speed.
        assert d["ms_per_output_token_delta"] == pytest.approx(0.0, abs=1e-6)
        assert d["latency_p50_delta_ms"] < 0  # the misleading raw number


class TestJudging:
    """Cost without quality is the half of the answer that flatters cheap arms."""

    class _FixedJudge:
        name = "fixed"

        def __init__(self, score: float) -> None:
            self._score = score
            self.calls: list[str] = []

        def score(self, sample, strong, weak):
            from instar.rubrics.base import JudgeResult

            self.calls.append(sample.id)
            return JudgeResult(self._score, "fixed")

    def _arms(self):
        return [
            Arm("base", MockBackend("base"), "strong"),
            Arm("other", MockBackend("other"), "cheap"),
        ]

    def test_no_judge_means_unscored_not_perfect(self):
        r = run_arms(_samples(4), arms=self._arms(), pricing=PRICING)
        assert r.by_name("other").quality_mean is None

    def test_missing_judge_is_warned_about(self):
        r = run_arms(_samples(4), arms=self._arms(), pricing=PRICING)
        assert any("not a better arm until" in w for w in r.warnings)

    def test_judge_scores_every_non_baseline_arm(self):
        j = self._FixedJudge(0.5)
        r = run_arms(_samples(4), arms=self._arms(), pricing=PRICING, judge=j)
        assert r.by_name("other").quality_mean == pytest.approx(0.5)
        assert r.by_name("other").quality_n == 4

    def test_baseline_is_never_scored_against_itself(self):
        j = self._FixedJudge(1.0)
        r = run_arms(_samples(4), arms=self._arms(), pricing=PRICING, judge=j)
        assert r.by_name("base").quality_mean is None

    def test_judge_sees_every_repeat_not_just_the_first(self):
        j = self._FixedJudge(1.0)
        run_arms(_samples(3), arms=self._arms(), repeats=4, pricing=PRICING, judge=j)
        assert len(j.calls) == 12

    def test_failed_calls_are_skipped_not_scored_zero(self):
        """A network error is not a quality signal; they need different fixes."""

        class _Flaky(Backend):
            name = "flaky"

            def __init__(self) -> None:
                self.n = 0

            def complete(self, sample, model):
                self.n += 1
                if self.n == 1:
                    return CompletionResult.failure(model, "boom")
                return _result(model=model)

        arms = [Arm("base", MockBackend("base"), "strong"), Arm("other", _Flaky(), "cheap")]
        j = self._FixedJudge(1.0)
        r = run_arms(_samples(4), arms=arms, pricing=PRICING, judge=j)
        assert r.by_name("other").quality_n == 3  # not 4, and not a 0.0 dragging the mean
        assert r.by_name("other").quality_mean == pytest.approx(1.0)

    def test_quality_reaches_the_deltas(self):
        j = self._FixedJudge(0.75)
        r = run_arms(_samples(4), arms=self._arms(), pricing=PRICING, judge=j)
        assert r.deltas()[0]["quality_mean"] == pytest.approx(0.75)


class TestBlindJudge:
    def test_position_is_deterministic_for_a_given_sample_id(self):
        from instar.rubrics.judges import BlindPairwiseJudge

        first = BlindPairwiseJudge._first_is_baseline("sample-42")
        assert first == BlindPairwiseJudge._first_is_baseline("sample-42")

    def test_position_varies_across_sample_ids(self):
        from instar.rubrics.judges import BlindPairwiseJudge

        flips = {BlindPairwiseJudge._first_is_baseline(f"s{i}") for i in range(20)}
        assert flips == {True, False}

    def test_prompt_carries_no_provenance(self):
        from instar.rubrics.judges import BlindPairwiseJudge

        assert "STRONG" not in BlindPairwiseJudge.SYSTEM_PROMPT
        assert "WEAK" not in BlindPairwiseJudge.SYSTEM_PROMPT
        assert "premium" not in BlindPairwiseJudge.SYSTEM_PROMPT

    def test_labels_follow_the_shuffle(self):
        """A names the baseline wherever it landed, or the verdict inverts."""
        from instar.providers.base import Backend
        from instar.rubrics.judges import BlindPairwiseJudge

        seen = {}

        class _Capture(Backend):
            name = "cap"

            def complete(self, sample, model):
                seen["prompt"] = sample.messages[0]["content"]
                return _result(text="PASS", model=model)

        j = BlindPairwiseJudge(_Capture(), "judge-model")
        for sid in ("s0", "s1", "s2", "s3"):
            sample = TrafficSample(id=sid, feature="f", messages=[{"role": "user", "content": "q"}])
            j.score(sample, _result(text="BASELINE_TEXT"), _result(text="ARM_TEXT"))
            p = seen["prompt"]
            # Whichever position the baseline occupies, it must be labelled A.
            a_idx, b_idx = p.index("ANSWER A:"), p.index("ANSWER B:")
            base_idx, arm_idx = p.index("BASELINE_TEXT"), p.index("ARM_TEXT")
            assert (base_idx > a_idx) == (base_idx - a_idx < 40)
            assert (arm_idx > b_idx) == (arm_idx - b_idx < 40)
