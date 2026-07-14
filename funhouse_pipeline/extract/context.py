"""Business-rules context / system prompt for image extraction (Task 6.1, Req 4.2).

The Extractor supplies the business rules -- **pricing tiers, product rules,
school names, and known player names** -- to the LLM as the extraction system
prompt so the model extracts against known ground truth (Req 4.2). Everything
except the known player names is *derived from the seed data* so the prompt can
never drift from what the database actually contains:

- pricing tiers / product rules  <- :data:`funhouse_pipeline.db.seed.SEED_PRODUCTS`
- school names                    <- ``PARTNER_SCHOOLS`` + ``PROPOSED_SCHOOLS``

Known player names are **injected** by the caller: players are not seeded (they
come from the historical records / the ``players`` master), so the caller passes
whatever set it has (e.g. names already loaded into the DB). An empty set is
allowed on a cold start.

This module performs no I/O and never imports a provider SDK.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from funhouse_pipeline.db.seed import (
    PARTNER_SCHOOLS,
    PROPOSED_SCHOOLS,
    SEED_PRODUCTS,
    SeedProduct,
)

# Pay-per-use products map to the design's Pricing_Tier durations. The durations
# are not stored on the product row, so they are attached here for the prompt.
_TIER_DURATIONS: Mapping[str, str] = {
    "PayPerUse-20min": "20 minutes",
    "PayPerUse-1hr": "1 hour",
    "PayPerUse-2hr": "2 hours",
    "Subscription": "per month (group of 4)",
}


def _rand(price_cents: int) -> str:
    """Format integer cents as a Rand string (e.g. 1000 -> ``R10``)."""
    rand = price_cents / 100
    if rand == int(rand):
        return f"R{int(rand)}"
    return f"R{rand:.2f}"


@dataclass(frozen=True)
class PricingTier:
    """A known valid amount-and-product combination (design: Pricing_Tier)."""

    product_name: str
    price_cents: int
    duration: str | None = None

    @property
    def amount_rand(self) -> str:
        return _rand(self.price_cents)


@dataclass(frozen=True)
class BusinessRules:
    """The ground-truth business rules injected into the extraction prompt.

    Attributes:
        pricing_tiers: Valid amount/product combinations derived from seeded
            products (Req 4.2).
        products: The seeded products with their JSONB rules.
        school_names: Partner + proposed school names (seed data).
        known_player_names: Caller-injected known player names (Req 4.2). May be
            empty on a cold start.
    """

    pricing_tiers: tuple[PricingTier, ...]
    products: tuple[SeedProduct, ...]
    school_names: tuple[str, ...]
    known_player_names: tuple[str, ...] = field(default_factory=tuple)


def build_business_rules(known_player_names: Iterable[str] = ()) -> BusinessRules:
    """Assemble the :class:`BusinessRules` from seed data + injected names.

    Args:
        known_player_names: Known player names supplied by the caller (from the
            ``players`` master / DB or a provided set). Players are not seeded,
            so this is the injection point for that ground truth (Req 4.2).

    Returns:
        A :class:`BusinessRules` capturing pricing tiers, product rules, school
        names, and known player names.
    """
    pricing_tiers = tuple(
        PricingTier(
            product_name=p.name,
            price_cents=p.price_cents,
            duration=_TIER_DURATIONS.get(p.name),
        )
        for p in SEED_PRODUCTS
    )
    school_names = tuple(PARTNER_SCHOOLS) + tuple(PROPOSED_SCHOOLS)
    # De-duplicate while preserving order; drop blanks defensively.
    seen: set[str] = set()
    players: list[str] = []
    for name in known_player_names:
        cleaned = str(name).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            players.append(cleaned)

    return BusinessRules(
        pricing_tiers=pricing_tiers,
        products=tuple(SEED_PRODUCTS),
        school_names=school_names,
        known_player_names=tuple(players),
    )


def build_system_prompt(rules: BusinessRules) -> str:
    """Render the business rules into the extraction system prompt (Req 4.2).

    The returned text always contains four labelled sections -- pricing tiers,
    product rules, school names, and known player names -- so downstream checks
    (design Property 7) can verify the rules are present. It also states the
    output contract the model must follow (see :mod:`funhouse_pipeline.extract.records`).

    Args:
        rules: The business rules to render.

    Returns:
        The system prompt string.
    """
    lines: list[str] = []
    lines.append(
        "You extract structured records from historical FunHouse Digital paper "
        "records (membership cards, attendance/payment sheets, photos). Extract "
        "ONLY against the known business rules below. Do NOT extract national "
        "identity numbers or physical addresses."
    )

    # --- Pricing tiers -----------------------------------------------------
    lines.append("")
    lines.append("PRICING TIERS (valid amount/product combinations):")
    for tier in rules.pricing_tiers:
        duration = f" for {tier.duration}" if tier.duration else ""
        lines.append(f"- {tier.amount_rand} = {tier.product_name}{duration}")

    # --- Product rules -----------------------------------------------------
    lines.append("")
    lines.append("PRODUCT RULES:")
    for product in rules.products:
        rules_json = json.dumps(dict(product.rules), sort_keys=True)
        lines.append(
            f"- {product.name} ({product.type}, {_rand(product.price_cents)}): "
            f"{rules_json}"
        )

    # --- School names ------------------------------------------------------
    lines.append("")
    lines.append("SCHOOL NAMES (only these are valid):")
    for school in rules.school_names:
        lines.append(f"- {school}")

    # --- Known player names ------------------------------------------------
    lines.append("")
    lines.append("KNOWN PLAYER NAMES (match people against these):")
    if rules.known_player_names:
        for name in rules.known_player_names:
            lines.append(f"- {name}")
    else:
        lines.append("- (none provided yet)")

    # --- Output contract ---------------------------------------------------
    lines.append("")
    lines.append(
        "OUTPUT CONTRACT: Return a JSON object {\"records\": [...]}. Each record "
        "MUST be {\"target_table\": one of "
        "[players|sessions|payments|lessons|student_metrics], "
        "\"confidence\": a number in [0,1], \"payload\": {domain columns}}. "
        "Set confidence to your extraction certainty. Return {\"records\": []} "
        "when nothing can be extracted."
    )

    return "\n".join(lines)


def build_extraction_prompt_context(
    known_player_names: Iterable[str] = (),
    *,
    rules: BusinessRules | None = None,
) -> dict[str, Any]:
    """Convenience: build ``{system_prompt, business_rules}`` for a request.

    Returns the system prompt plus the :class:`BusinessRules` used to build it,
    so callers can both send the prompt to the LLM and reuse the rules (e.g. for
    validation later).
    """
    resolved = rules if rules is not None else build_business_rules(known_player_names)
    return {
        "system_prompt": build_system_prompt(resolved),
        "business_rules": resolved,
    }
