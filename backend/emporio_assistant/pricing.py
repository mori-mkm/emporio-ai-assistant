"""Provider price table (EMP-7): exact USD costs in integer micro-dollars.

A model is priceable only when its entry names a source, a check date and a price for every
billable category. Anything else is an unknown price and the paid path refuses it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

UNIT = "usd_per_million_tokens"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "pricing.json"


class PricingError(Exception):
    """Unknown or invalid price: the paid path must not dispatch."""


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    model: str
    # category -> USD per million units; categories reported but already counted elsewhere
    # (e.g. reasoning tokens billed as output) are in `included_in` and cost nothing extra.
    prices: dict[str, Decimal]
    included_in: dict[str, str]
    # Categories the provider always reports for this model. A settled usage must contain each
    # one explicitly (0 is a valid reported value); a missing one makes the cost unknown.
    required_usage: frozenset[str]
    # Price tier limit: the entry's prices are valid only up to this prompt size.
    max_prompt_tokens: int | None = None
    prompt_categories: frozenset[str] = frozenset()

    def settlement_cost_micro_usd(self, usage: dict[str, int]) -> int:
        """Cost of reported usage. Refuses empty/incomplete usage and prompts above the tier."""
        missing = self.required_usage - set(usage)
        if not usage or missing:
            raise PricingError(f"incomplete usage: missing {sorted(missing) or 'all categories'}")
        if self.max_prompt_tokens is not None:
            prompt = sum(usage.get(c, 0) for c in self.prompt_categories)
            if prompt > self.max_prompt_tokens:
                raise PricingError("prompt exceeded the priced tier; higher prices not configured")
        return self.cost_micro_usd(usage)

    def cost_micro_usd(self, units: dict[str, int]) -> int:
        """Exact cost rounded UP to the micro-dollar (may overstate a call by < 1 µUSD).

        Raises PricingError on unknown categories."""
        total = Decimal(0)
        for category, count in units.items():
            if type(count) is not int or count < 0:
                raise PricingError(f"usage for '{category}' must be a non-negative integer")
            if category in self.included_in:
                continue
            if category not in self.prices:
                raise PricingError(f"no price for category '{category}' of {self.model}")
            total += self.prices[category] * count  # USD/M * units = micro-USD
        return math.ceil(total)


@dataclass(frozen=True)
class PriceTable:
    version: str
    fictitious: bool
    models: dict[tuple[str, str], ModelPrice]

    def get(self, provider: str, model: str) -> ModelPrice:
        price = self.models.get((provider, model))
        if price is None:
            raise PricingError(f"no verified price for {provider}/{model}")
        return price


def load_price_table(path: Path = DEFAULT_PATH) -> PriceTable:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PricingError(f"cannot read price table: {type(error).__name__}") from None
    if not isinstance(data, dict) or not isinstance(data.get("pricing_version"), str):
        raise PricingError("price table needs a pricing_version")
    if type(data.get("fictitious")) is not bool:
        raise PricingError("price table must state fictitious: true/false")
    models: dict[tuple[str, str], ModelPrice] = {}
    for entry in data.get("models", []):
        price = _parse_entry(entry)
        if price is not None:  # unverified entries stay unpriceable
            models[(price.provider, price.model)] = price
    return PriceTable(data["pricing_version"], data["fictitious"], models)


def _parse_entry(entry: dict) -> ModelPrice | None:
    if not isinstance(entry, dict) or entry.get("unit") != UNIT:
        raise PricingError(f"each model entry needs unit '{UNIT}'")
    if not entry.get("source") or not entry.get("checked_on"):
        return None
    prices: dict[str, Decimal] = {}
    included: dict[str, str] = {}
    for category, spec in (entry.get("categories") or {}).items():
        if isinstance(spec, dict) and set(spec) == {"included_in"}:
            included[category] = spec["included_in"]
            continue
        try:
            value = Decimal(spec["usd_per_million"])
        except (TypeError, KeyError, InvalidOperation):
            raise PricingError(f"invalid price for category '{category}'") from None
        if not value.is_finite() or value < 0:
            raise PricingError(f"invalid price for category '{category}'")
        prices[category] = value
    if not prices or not set(included.values()) <= set(prices):
        return None
    required = entry.get("required_usage", sorted(prices))
    if not isinstance(required, list) or not required or not set(required) <= set(prices):
        raise PricingError("required_usage must list priced categories")
    tier = entry.get("max_prompt_tokens")
    prompt_categories = entry.get("prompt_categories", [])
    if tier is not None and (type(tier) is not int or tier <= 0 or not prompt_categories):
        raise PricingError("max_prompt_tokens needs a positive integer and prompt_categories")
    if not set(prompt_categories) <= set(prices):
        raise PricingError("prompt_categories must be priced categories")
    return ModelPrice(
        entry["provider"],
        entry["model"],
        prices,
        included,
        frozenset(required),
        tier,
        frozenset(prompt_categories),
    )
