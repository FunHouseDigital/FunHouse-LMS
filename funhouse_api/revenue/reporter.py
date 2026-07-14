"""Revenue_Reporter: three-stream revenue summary (Req 11).

:func:`summary` joins ``payments -> products`` and sums ``amount_cents`` grouped
by ``products.type`` into the three revenue streams, all in integer cents
consistent with the stored amounts (Req 11.1, 11.4):

* ``pay_per_use_cents``  -- sum of payments for ``type = 'pay_per_use'`` products.
* ``subscription_cents`` -- sum of payments for ``type = 'subscription'`` products.
* ``school_contracts_cents`` -- sum of payments for school-contract products.
  The Phase 0 catalog has no school-contract product type, so while no such
  payment exists this stream is ``0`` (R0) by construction (Req 11.3).

For a manager or facilitator the sum is restricted to the caller's scope
(Req 11.5, 11.2): payments carry ``location_id`` (a manager's constraint) and,
for a facilitator, the school constraint is applied by joining to the payment's
player (``players.school_id``). A payment whose product is unknown (no
``product_id``) contributes to no stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Product types that map onto the three revenue streams. The Phase 0 products
# CHECK permits 'pay_per_use', 'subscription', 'once_off_pass'; there is no
# 'school_contracts' product type, so that stream sums an empty set (Req 11.3).
_STREAM_TYPES: dict[str, tuple[str, ...]] = {
    "pay_per_use": ("pay_per_use",),
    "subscription": ("subscription",),
    "school_contracts": ("school_contracts",),
}


@dataclass(frozen=True)
class RevenueSummary:
    """Three-stream revenue totals in integer cents (Req 11.1, 11.4)."""

    pay_per_use_cents: int
    subscription_cents: int
    school_contracts_cents: int


def summary(conn: Any, scope: Any) -> RevenueSummary:
    """Return the scoped three-stream revenue summary (Req 11)."""
    conditions: list[str] = []
    params: list[Any] = []
    if not getattr(scope, "unrestricted", False):
        conditions.append("pay.location_id = %s")
        params.append(scope.location_id)
        if getattr(scope, "role", None) == "facilitator":
            conditions.append("pl.school_id = %s")
            params.append(scope.school_id)
    where = " AND ".join(conditions) if conditions else "TRUE"

    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT pr.type, COALESCE(SUM(pay.amount_cents), 0)
            FROM payments pay
            JOIN products pr ON pr.id = pay.product_id
            JOIN players pl ON pl.id = pay.player_id
            WHERE {where}
            GROUP BY pr.type
            """,
            params,
        )
        by_type = {row[0]: int(row[1]) for row in cursor.fetchall()}

    def _stream_total(stream: str) -> int:
        return sum(by_type.get(t, 0) for t in _STREAM_TYPES[stream])

    return RevenueSummary(
        pay_per_use_cents=_stream_total("pay_per_use"),
        subscription_cents=_stream_total("subscription"),
        school_contracts_cents=_stream_total("school_contracts"),
    )
