# SPDX-License-Identifier: Apache-2.0
"""Per-token pricing and cost math.

:data:`PRICING` maps a model id to ``(input_usd_per_mtok, output_usd_per_mtok)``.

.. warning::
   **The shipped table is a placeholder.** The values are sized to be
   *directionally* sane so mock runs produce believable curves. They are not
   authoritative and they go stale the moment a provider changes a price.
   Verify against current published pricing — or better, load your own table
   with :func:`load_pricing` — before quoting any number publicly.

Self-hosted models can be priced two ways:

- **marginal** — ~$0/token against a fixed GPU bill. Give the model a
  ``(0.0, 0.0)`` row and use :func:`breakeven_requests_per_month` to find the
  volume where the fixed cost beats a per-token API.
- **amortized** — precompute $/Mtok from $/hr ÷ throughput and put that in the
  row directly.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Model id -> (input $/Mtok, output $/Mtok).
# Undated ids on purpose: dated ids are a known source of 404s against live APIs.
# PLACEHOLDER VALUES — verify before publishing any figure derived from them.
PRICING: dict[str, tuple[float, float]] = {
    # strong tier
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    # Still served and still a sensible baseline; without a row, a direct arm on
    # it reports cost "unavailable" and every cost delta against it is blank.
    "claude-sonnet-4-5": (3.0, 15.0),
    # weak / cheap tier
    "claude-haiku-4-5": (1.0, 5.0),
    "gemini-2.5-flash": (0.30, 2.5),
    # self-hosted open model: marginal cost ~0; fixed GPU cost is handled by
    # breakeven_requests_per_month(). Amortize here instead if you prefer.
    "self-hosted-open": (0.0, 0.0),
    # mock-mode synthetic models
    "mock-strong": (10.0, 50.0),
    "mock-weak": (0.50, 2.5),
}


def load_pricing(path: str | Path) -> dict[str, tuple[float, float]]:
    """Load a pricing table from JSON: ``{"model-id": [input_rate, output_rate]}``.

    Rates are USD per 1,000,000 tokens. Use this instead of trusting the
    shipped placeholders whenever a number will leave your laptop.
    """
    with open(path, encoding="utf-8") as fh:
        data: Any = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: pricing table must be a JSON object")
    table: dict[str, tuple[float, float]] = {}
    for model, rate in data.items():
        if not isinstance(rate, (list, tuple)) or len(rate) != 2:
            raise ValueError(f"{path}: {model!r} must map to [input_rate, output_rate]")
        table[str(model)] = (float(rate[0]), float(rate[1]))
    return table


def call_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> float:
    """USD cost of a single call.

    An unpriced model costs 0.0 — which will silently understate a run, so
    check :func:`unpriced_models` before trusting a total.
    """
    table = PRICING if pricing is None else pricing
    rate = table.get(model)
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    return (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate


def unpriced_models(
    models: Iterable[str], *, pricing: dict[str, tuple[float, float]] | None = None
) -> list[str]:
    """Which of ``models`` have no pricing row (and would therefore cost $0)."""
    if isinstance(models, str):
        raise TypeError("models must be an iterable of model ids, not a single string")
    table = PRICING if pricing is None else pricing
    return sorted(set(models) - set(table))


# Prompt-caching multipliers applied to the *cached* portion of the input:
#   - a cold call (cache write) pays a surcharge on that prefix;
#   - a warm call (cache read) pays a large discount.
# Fresh (uncached) input and all output always price at the normal rate.
# These match the widely published Anthropic prompt-caching ratios; confirm
# against your provider's current terms before publishing a figure.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def cached_call_cost_usd(
    model: str,
    *,
    cached_input_tokens: int,
    fresh_input_tokens: int,
    output_tokens: int,
    warm: bool,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> float:
    """USD cost of one call that reuses a cached static prefix.

    ``cached_input_tokens`` is the size of the reusable prefix (a long system
    brief, a style guide, a schema); ``fresh_input_tokens`` is the per-turn
    remainder. On a warm read the prefix is charged at
    :data:`CACHE_READ_MULTIPLIER`; on a cold write it pays
    :data:`CACHE_WRITE_MULTIPLIER`.

    This is the lever for workloads that routing *cannot* cheapen — a
    quality-sensitive foreground builder that must stay on the strong model,
    but re-sends the same large prefix on every turn. It is a pure function and
    does not touch the routing runner, which stays the no-caching baseline.
    """
    table = PRICING if pricing is None else pricing
    rate = table.get(model)
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    prefix_mult = CACHE_READ_MULTIPLIER if warm else CACHE_WRITE_MULTIPLIER
    cached = (cached_input_tokens / 1_000_000.0) * in_rate * prefix_mult
    fresh = (fresh_input_tokens / 1_000_000.0) * in_rate
    out = (output_tokens / 1_000_000.0) * out_rate
    return cached + fresh + out


@dataclass(frozen=True)
class CostSummary:
    """Baseline-vs-routed spend for a run."""

    baseline_usd: float  # cost with everything on the strong model
    routed_usd: float  # cost under the routing policy
    saved_usd: float
    saved_pct: float

    @classmethod
    def of(cls, baseline_usd: float, routed_usd: float) -> CostSummary:
        saved = baseline_usd - routed_usd
        pct = (saved / baseline_usd * 100.0) if baseline_usd > 0 else 0.0
        return cls(baseline_usd, routed_usd, saved, pct)


def breakeven_requests_per_month(
    *,
    hourly_usd: float,
    hours_per_month: float,
    api_cost_per_request: float,
    self_hosted_cost_per_request: float,
) -> float | None:
    """Monthly request volume at which self-hosting beats a per-token API.

    Returns the break-even request count, or ``None`` if self-hosting never
    wins (its per-request cost is already at or above the API's). This is the
    question behind every "should we run our own small model?" conversation:
    fixed infrastructure only pays off above some steady volume.
    """
    fixed_monthly = hourly_usd * hours_per_month
    margin = api_cost_per_request - self_hosted_cost_per_request
    if margin <= 0:
        return None
    return fixed_monthly / margin
