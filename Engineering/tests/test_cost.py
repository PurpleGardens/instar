# SPDX-License-Identifier: Apache-2.0
"""Cost math: pricing, prompt caching, break-even, and the $0 trap."""

import json

import pytest

from instar.core.cost import (
    CACHE_READ_MULTIPLIER,
    PRICING,
    CostSummary,
    breakeven_requests_per_month,
    cached_call_cost_usd,
    call_cost_usd,
    load_pricing,
    unpriced_models,
)

TABLE = {"cheap": (1.0, 5.0), "dear": (15.0, 75.0)}


def test_call_cost_prices_input_and_output_separately() -> None:
    # 1M input at $1 + 1M output at $5
    assert call_cost_usd("cheap", 1_000_000, 1_000_000, pricing=TABLE) == pytest.approx(6.0)


def test_call_cost_scales_linearly() -> None:
    assert call_cost_usd("cheap", 1_000, 2_000, pricing=TABLE) == pytest.approx(
        1_000 / 1e6 * 1.0 + 2_000 / 1e6 * 5.0
    )


def test_an_unpriced_model_costs_zero() -> None:
    """The dangerous default: a typo'd model id silently zeroes a run's cost."""
    assert call_cost_usd("typo-model", 1_000_000, 1_000_000, pricing=TABLE) == 0.0


def test_unpriced_models_surfaces_the_gap() -> None:
    """...which is why every run checks for it and warns."""
    assert unpriced_models(["cheap", "typo-model"], pricing=TABLE) == ["typo-model"]
    assert unpriced_models(["cheap", "dear"], pricing=TABLE) == []


def test_shipped_pricing_covers_the_mock_models() -> None:
    assert "mock-strong" in PRICING
    assert "mock-weak" in PRICING
    assert PRICING["mock-weak"] < PRICING["mock-strong"]


def test_load_pricing_round_trips(tmp_path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"m": [2.0, 8.0]}), encoding="utf-8")
    assert load_pricing(path) == {"m": (2.0, 8.0)}


def test_load_pricing_rejects_a_malformed_row(tmp_path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"m": [2.0]}), encoding="utf-8")
    with pytest.raises(ValueError, match="input_rate"):
        load_pricing(path)


def test_warm_cache_is_cheaper_than_cold() -> None:
    kwargs = {"cached_input_tokens": 100_000, "fresh_input_tokens": 500, "output_tokens": 500}
    warm = cached_call_cost_usd("dear", warm=True, pricing=TABLE, **kwargs)
    cold = cached_call_cost_usd("dear", warm=False, pricing=TABLE, **kwargs)
    assert warm < cold


def test_warm_cache_discounts_only_the_prefix() -> None:
    warm = cached_call_cost_usd(
        "cheap",
        cached_input_tokens=1_000_000,
        fresh_input_tokens=0,
        output_tokens=0,
        warm=True,
        pricing=TABLE,
    )
    assert warm == pytest.approx(1.0 * CACHE_READ_MULTIPLIER)


def test_caching_beats_no_caching_on_a_repeated_prefix() -> None:
    """The lever for work that routing cannot cheapen: a foreground builder that
    must stay strong but re-sends the same big prefix every turn."""
    prefix, fresh, out = 100_000, 500, 800
    uncached = call_cost_usd("dear", prefix + fresh, out, pricing=TABLE)
    warm = cached_call_cost_usd(
        "dear",
        cached_input_tokens=prefix,
        fresh_input_tokens=fresh,
        output_tokens=out,
        warm=True,
        pricing=TABLE,
    )
    assert warm < uncached


def test_cost_summary_computes_savings() -> None:
    s = CostSummary.of(100.0, 40.0)
    assert s.saved_usd == pytest.approx(60.0)
    assert s.saved_pct == pytest.approx(60.0)


def test_cost_summary_handles_a_zero_baseline() -> None:
    s = CostSummary.of(0.0, 0.0)
    assert s.saved_pct == 0.0


def test_breakeven_finds_the_crossover() -> None:
    # $1/hr * 720 hrs = $720/mo fixed; saving $0.01/request → 72,000 requests.
    n = breakeven_requests_per_month(
        hourly_usd=1.0,
        hours_per_month=720.0,
        api_cost_per_request=0.011,
        self_hosted_cost_per_request=0.001,
    )
    assert n == pytest.approx(72_000.0)


def test_breakeven_returns_none_when_self_hosting_never_wins() -> None:
    assert (
        breakeven_requests_per_month(
            hourly_usd=1.0,
            hours_per_month=720.0,
            api_cost_per_request=0.001,
            self_hosted_cost_per_request=0.002,
        )
        is None
    )


def test_sonnet_4_5_is_priced_like_sonnet_4_6() -> None:
    # A live arms run on claude-sonnet-4-5 reported cost "unavailable" for want
    # of this row; OpenRouter's provider-reported cost for the same model matched
    # this rate to within token-count noise.
    assert PRICING["claude-sonnet-4-5"] == PRICING["claude-sonnet-4-6"] == (3.0, 15.0)
