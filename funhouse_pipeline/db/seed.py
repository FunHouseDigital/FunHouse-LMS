"""Idempotent reference-data seeding (Req 2).

Inserts the founding reference data the pipeline needs before historical records
can be loaded: the Smithfield location, the partner/proposed schools, the five
products, and the two seed users (Aya, Loyiso). Seeding is **idempotent**: each
row is inserted only when a row with the same natural identity is absent;
otherwise it is skipped and the existing row is left unchanged (Req 2.8). This
makes the seed step safe to re-run as part of the one re-runnable command.

Like the migration runner, this module accepts any DB-API 2.0 connection
(psycopg in production) and uses only portable, parameterized SQL so it can be
exercised without binding to a specific driver instance.

Design references:
- locations: Row 1 = Smithfield (Req 2.1).
- schools (partner): Mofulatshepe, Relebohile-Sibulele, Smithfield Primary (Req 2.2).
- schools (proposed): Thabo-Vuyo, Naledi, Rouxville Primary, JB Tyu (Req 2.3).
- products: PayPerUse-20min/1hr/2hr, Subscription, Holiday Special (Req 2.4-2.6).
- users: Aya (founder), Loyiso (manager) (Req 2.7).

Money is stored as integer cents; product rules are stored as JSONB, matching
the design's Data Models / Seed data section.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Seed data (natural identities and values) — mirrors the design's Seed table.
# --------------------------------------------------------------------------- #

# locations: Row 1 (Req 2.1). Natural identity = name.
SMITHFIELD_LOCATION = "Smithfield"

# schools with contract_status='partner' (Req 2.2). Natural identity = name.
PARTNER_SCHOOLS: tuple[str, ...] = (
    "Mofulatshepe",
    "Relebohile-Sibulele",
    "Smithfield Primary",
)

# schools with contract_status='proposed' (Req 2.3). Natural identity = name.
PROPOSED_SCHOOLS: tuple[str, ...] = (
    "Thabo-Vuyo",
    "Naledi",
    "Rouxville Primary",
    "JB Tyu",
)


@dataclass(frozen=True)
class SeedProduct:
    """A product to seed. Prices are integer cents; rules are stored as JSONB."""

    name: str
    type: str
    price_cents: int
    rules: Mapping[str, Any] = field(default_factory=dict)


# products (Req 2.4-2.6). Natural identity = name.
#
# Holiday Special price: neither the requirements (Req 2.4 lists it without a
# price) nor the design/PRD specifies a price for the Holiday Special product —
# only its rules are given (Req 2.6). price_cents is NOT NULL in the schema, so
# per the task guidance we seed it as 0 (a placeholder to be corrected once the
# business confirms the amount) rather than inventing a figure.
SEED_PRODUCTS: tuple[SeedProduct, ...] = (
    SeedProduct("PayPerUse-20min", "pay_per_use", 1000),
    SeedProduct("PayPerUse-1hr", "pay_per_use", 3000),
    SeedProduct("PayPerUse-2hr", "pay_per_use", 5000),
    SeedProduct(
        "Subscription",
        "subscription",
        35000,
        {"members": 4, "hours_per_week": 2, "min_term_months": 3},
    ),
    SeedProduct(
        "Holiday Special",
        "once_off_pass",
        0,  # unspecified in PRD/design; placeholder pending business confirmation.
        {"hours_per_week": 3, "reset": "sunday", "rollover": False, "fixed_window": True},
    ),
)


@dataclass(frozen=True)
class SeedUser:
    """A user to seed. Natural identity = name (no email is provided by the PRD)."""

    name: str
    role: str


# users (Req 2.7). Natural identity = name.
SEED_USERS: tuple[SeedUser, ...] = (
    SeedUser("Aya", "founder"),
    SeedUser("Loyiso", "manager"),
)

# Total number of rows a full seed inserts into a fresh database.
TOTAL_SEED_ROWS = (
    1  # location
    + len(PARTNER_SCHOOLS)
    + len(PROPOSED_SCHOOLS)
    + len(SEED_PRODUCTS)
    + len(SEED_USERS)
)


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SeedRowResult:
    """Outcome for a single seed row."""

    table: str
    identity: str
    status: str  # "inserted" | "skipped"


@dataclass(frozen=True)
class SeedResult:
    """Summary of a seed run."""

    rows: tuple[SeedRowResult, ...]

    def inserted(self) -> list[SeedRowResult]:
        return [r for r in self.rows if r.status == "inserted"]

    def skipped(self) -> list[SeedRowResult]:
        return [r for r in self.rows if r.status == "skipped"]

    def summary(self) -> str:
        ins = ", ".join(f"{r.table}:{r.identity}" for r in self.inserted()) or "(none)"
        skp = ", ".join(f"{r.table}:{r.identity}" for r in self.skipped()) or "(none)"
        return (
            f"Seed complete: {len(self.inserted())} inserted, "
            f"{len(self.skipped())} skipped.\n"
            f"  Inserted: {ins}\n"
            f"  Skipped: {skp}"
        )


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #


def _insert_if_absent(
    cursor: Any,
    table: str,
    natural_col: str,
    natural_val: str,
    values: Mapping[str, Any],
    *,
    json_columns: Sequence[str] = (),
) -> tuple[Any, str]:
    """Insert a row only if one with the same natural identity is absent.

    Returns the tuple ``(id, status)`` where status is ``"inserted"`` or
    ``"skipped"``. On skip, the existing row is left unchanged (Req 2.8) and its
    id is returned so callers can resolve foreign keys against it.

    ``table``/``natural_col``/``json_columns`` are internal constants (never user
    input), so composing them into the statement is safe.
    """
    cursor.execute(
        f"SELECT id FROM {table} WHERE {natural_col} = %s",  # noqa: S608 - constants only
        (natural_val,),
    )
    existing = cursor.fetchone()
    if existing is not None:
        return existing[0], "skipped"

    columns = list(values.keys())
    placeholders: list[str] = []
    params: list[Any] = []
    for col in columns:
        value = values[col]
        if col in json_columns:
            placeholders.append("%s::jsonb")
            params.append(json.dumps(value))
        else:
            placeholders.append("%s")
            params.append(value)

    cursor.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "  # noqa: S608 - constants only
        f"VALUES ({', '.join(placeholders)}) RETURNING id",
        params,
    )
    return cursor.fetchone()[0], "inserted"


def seed(conn: Any) -> SeedResult:
    """Seed reference data idempotently and return a per-row summary.

    All non-``locations`` rows carry ``location_id`` resolved to the Smithfield
    location. Each insert happens only when a row with the same natural identity
    (name, or name for users since no email is provided) is absent; otherwise it
    is skipped and left unchanged (Req 2.8). The routine is safe to re-run.

    Args:
        conn: An open DB-API connection (psycopg in production). The schema must
            already be deployed (see :mod:`funhouse_pipeline.db.migrations`).

    Returns:
        A :class:`SeedResult`. The transaction is committed on success.
    """
    rows: list[SeedRowResult] = []

    with conn.cursor() as cursor:
        # 1. locations — Smithfield (Req 2.1). All other rows FK to this.
        location_id, status = _insert_if_absent(
            cursor,
            "locations",
            "name",
            SMITHFIELD_LOCATION,
            {"name": SMITHFIELD_LOCATION},
        )
        rows.append(SeedRowResult("locations", SMITHFIELD_LOCATION, status))

        # 2. schools — partner (Req 2.2) then proposed (Req 2.3).
        for name in PARTNER_SCHOOLS:
            _, status = _insert_if_absent(
                cursor,
                "schools",
                "name",
                name,
                {"name": name, "contract_status": "partner", "location_id": location_id},
            )
            rows.append(SeedRowResult("schools", name, status))

        for name in PROPOSED_SCHOOLS:
            _, status = _insert_if_absent(
                cursor,
                "schools",
                "name",
                name,
                {"name": name, "contract_status": "proposed", "location_id": location_id},
            )
            rows.append(SeedRowResult("schools", name, status))

        # 3. products (Req 2.4-2.6). rules stored as JSONB.
        for product in SEED_PRODUCTS:
            _, status = _insert_if_absent(
                cursor,
                "products",
                "name",
                product.name,
                {
                    "name": product.name,
                    "type": product.type,
                    "price_cents": product.price_cents,
                    "rules": dict(product.rules),
                    "location_id": location_id,
                },
                json_columns=("rules",),
            )
            rows.append(SeedRowResult("products", product.name, status))

        # 4. users — Aya (founder), Loyiso (manager) (Req 2.7).
        for user in SEED_USERS:
            _, status = _insert_if_absent(
                cursor,
                "users",
                "name",
                user.name,
                {"name": user.name, "role": user.role, "location_id": location_id},
            )
            rows.append(SeedRowResult("users", user.name, status))

    conn.commit()
    return SeedResult(rows=tuple(rows))
