"""RBAC_Enforcer: role/scope derivation and enforcement (Req 3, 15, 7.6).

Authorization layers on top of authentication. Once ``require_auth`` has
produced a :class:`~funhouse_api.auth.dependencies.Principal`, the
``require_scope`` dependency derives a :class:`Scope` describing exactly which
records the caller may read, write, and create:

* **founder** → unrestricted: sees every location and school (Req 3.1).
* **manager** → constrained to the manager's ``location_id`` (Req 3.2).
* **facilitator** → constrained to both ``location_id`` **and** ``school_id``
  (Req 3.3).

The :class:`Scope` is the single object every resource/sync path consults:

* :meth:`Scope.read_filter` yields a SQL fragment + params appended to every
  collection/record query so out-of-scope rows are never returned
  (Req 3.4, 3.6, 3.7, 15.1, 15.2).
* :meth:`Scope.assert_can_write` rejects a cross-scope write **before** any
  persistence with ``403`` (Req 3.5).
* :meth:`Scope.stamp` sets ``location_id`` (and ``school_id`` where applicable)
  on a new row to the caller's scope (Req 15.3).

Scope derivation is **fail-closed**: if a scope cannot be derived (an unknown
role, or a manager/facilitator missing the location/school its role requires)
the request is rejected rather than served (Req 7.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping

from fastapi import Depends
from fastapi.exceptions import HTTPException

from funhouse_api.auth.dependencies import Principal, require_auth

ROLE_FOUNDER = "founder"
ROLE_MANAGER = "manager"
ROLE_FACILITATOR = "facilitator"


class AuthzError(Exception):
    """Raised when authorization fails (cross-scope access or underivable scope).

    Mapped to ``403 Forbidden`` by ``require_scope`` and the resource layer.
    """


@dataclass(frozen=True)
class Scope:
    """The set of records a caller may access (Req 3, 15).

    ``location_id`` / ``school_id`` are ``None`` for an unrestricted founder. A
    manager carries only ``location_id``; a facilitator carries both.
    """

    role: str
    location_id: str | None
    school_id: str | None

    @property
    def unrestricted(self) -> bool:
        """True for a founder: no scope filter is applied (Req 3.1)."""
        return self.role == ROLE_FOUNDER

    @classmethod
    def derive(cls, principal: Principal) -> "Scope":
        """Derive a :class:`Scope` from a :class:`Principal`, fail-closed (Req 7.6).

        Raises:
            AuthzError: For an unknown role, a manager with no ``location_id``,
                or a facilitator missing ``location_id`` or ``school_id``.
        """
        role = principal.role
        if role == ROLE_FOUNDER:
            return cls(role=role, location_id=None, school_id=None)

        if role == ROLE_MANAGER:
            if not principal.location_id:
                raise AuthzError("manager principal is missing a location scope")
            return cls(role=role, location_id=principal.location_id, school_id=None)

        if role == ROLE_FACILITATOR:
            if not principal.location_id or not principal.school_id:
                raise AuthzError(
                    "facilitator principal is missing a location or school scope"
                )
            return cls(
                role=role,
                location_id=principal.location_id,
                school_id=principal.school_id,
            )

        # Unknown/unsupported role → fail closed.
        raise AuthzError(f"unsupported role {role!r}")

    def read_filter(self, *, alias: str | None = None) -> tuple[str, list[Any]]:
        """Return a ``(sql_fragment, params)`` for use in a ``WHERE`` clause.

        A founder gets an always-true fragment (no restriction, Req 3.1). A
        manager is constrained by ``location_id`` (Req 3.2, 15.1); a facilitator
        additionally by ``school_id`` (Req 3.3, 15.2). ``alias`` optionally
        qualifies the column (e.g. ``"p"`` → ``p.location_id``).
        """
        prefix = f"{alias}." if alias else ""
        if self.unrestricted:
            return "TRUE", []

        if self.role == ROLE_MANAGER:
            return f"{prefix}location_id = %s", [self.location_id]

        # facilitator
        return (
            f"{prefix}location_id = %s AND {prefix}school_id = %s",
            [self.location_id, self.school_id],
        )

    def assert_can_write(
        self,
        row_location_id: str | None,
        row_school_id: str | None = None,
    ) -> None:
        """Reject a cross-scope write before persistence (Req 3.5).

        A founder may write anywhere. A manager may only write rows whose
        ``location_id`` matches its scope; a facilitator additionally requires a
        matching ``school_id``.

        Raises:
            AuthzError: If the target row is outside the caller's scope.
        """
        if self.unrestricted:
            return

        if _as_str(row_location_id) != _as_str(self.location_id):
            raise AuthzError("write target is outside the caller's location scope")

        if self.role == ROLE_FACILITATOR:
            if _as_str(row_school_id) != _as_str(self.school_id):
                raise AuthzError("write target is outside the caller's school scope")

    def stamp(self, new_row: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        """Stamp ``location_id`` (and ``school_id``) onto a new row (Req 15.3).

        For a manager/facilitator the row's ``location_id`` is set to the
        caller's location; a facilitator's ``school_id`` is set to the caller's
        school. For an unrestricted founder the row is left as supplied (the
        founder must provide any location/school explicitly). Mutates and returns
        ``new_row`` for convenience.
        """
        if self.location_id is not None:
            new_row["location_id"] = self.location_id
        if self.role == ROLE_FACILITATOR and self.school_id is not None:
            new_row["school_id"] = self.school_id
        return new_row


def _as_str(value: Any) -> str | None:
    """Normalize a scope/row id to a comparable string (or ``None``)."""
    return None if value is None else str(value)


def require_scope(principal: Principal = Depends(require_auth)) -> Scope:
    """FastAPI dependency: derive the caller's :class:`Scope`, fail-closed.

    Layered after ``require_auth``. If the scope cannot be derived the request
    is rejected with ``403`` rather than served (Req 7.6).
    """
    try:
        return Scope.derive(principal)
    except AuthzError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
