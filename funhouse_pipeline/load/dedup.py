"""Player deduplication and identity resolution (Task 10.1, Req 8.1-8.4).

This module is the **player resolution layer** the rest of Load builds on. Its
job is to turn a batch of extracted *player* records into a stable mapping

    input player identity  ->  the surviving ``players.id``

so that Task 11's clean-record insert wiring can attach every session, payment,
and student_metric to the one row that represents that person (Req 8.3). It is
deterministic and issues no LLM call.

Why a single ``players`` table
------------------------------
Learners and lounge customers share ONE ``players`` table (Req 8.1); a person
who is both is a single row with one combined history. The database-level
guarantee that two rows cannot represent the same person is the ``UNIQUE``
constraint on ``players.dedup_key`` (see the schema). This module computes that
key deterministically and resolves matches against it.

The ``dedup_key`` rule (deterministic)
--------------------------------------
For a candidate player the key is::

    dedup_key = slug(first_name) | slug(last_name) | birth_date_iso

where

* ``slug(x)`` lower-cases ``x``, trims surrounding whitespace, and collapses any
  internal whitespace run to a single space (so ``"  De  Villiers "`` and
  ``"de villiers"`` slug identically). ``None``/absent becomes the empty string.
* ``birth_date_iso`` is the birth date rendered as ``YYYY-MM-DD`` when a real
  calendar date is present, and the empty string when it is absent/unparseable.
* The three parts are joined with the ``|`` separator, e.g.
  ``"john|smith|2010-04-01"`` or, with no birth date, ``"john|smith|"``.

The **name key** is the same value without the birth-date part
(``slug(first)|slug(last)``) and is used only to detect ambiguity.

Matching and merge semantics
-----------------------------
1. **Exact match -> merge.** Records that share an identical ``dedup_key`` (within
   the batch and/or against an existing ``players`` row) are the same person: no
   new row is created for a match; the surviving row is the existing DB row when
   one is present, otherwise a single new row for the batch group.
2. **Attribute fill-in favors the higher-confidence, non-null value.** When a
   batch group is merged into a *new* row, each attribute takes the value from
   the highest-confidence candidate that supplies a non-null/non-empty value.
   When merging into an *existing* row, the already-loaded value is treated as
   authoritative and only NULL gaps are filled from the highest-confidence
   incoming non-null value (existing data is never clobbered).
3. **Ambiguous merges are flagged, not auto-merged.** When one name key maps to
   more than one distinct ``dedup_key`` (same name, conflicting/absent birth
   date) the person cannot be resolved safely, so every candidate under that
   name key is flagged for Operator review and **none** of them is inserted or
   merged (Req 8: ambiguous merges go to review). Records with no usable name at
   all are likewise flagged (``MISSING_NAME``) since a row needs a ``first_name``.
4. **New rows start pending.** Every newly created ``players`` row is written
   with ``consent_status = 'pending'`` (Req 8.4).

Transaction ownership
---------------------
``resolve_players`` executes its reads/writes on the supplied connection but does
**not** commit; the caller owns the transaction so this layer composes with the
per-record transactions Task 11/12 add.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from funhouse_pipeline.extract.records import ExtractedRecord

# --------------------------------------------------------------------------- #
# Flag reasons for records that cannot be resolved automatically.
# --------------------------------------------------------------------------- #

#: One name maps to multiple distinct dedup keys (conflicting/absent birth date).
AMBIGUOUS_MERGE = "AMBIGUOUS_MERGE"
#: The candidate has no usable name, so no ``players`` row can be created.
MISSING_NAME = "MISSING_NAME"

#: Player attributes carried on the row and eligible for confidence fill-in.
#: (School / guardian *name* -> *id* FK resolution is Task 11's concern.)
_FILLABLE_ATTRS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "birth_date",
    "grade",
    "photo_consent",
)


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def slug(value: Any) -> str:
    """Lower-case, trim, and collapse internal whitespace (see module docstring)."""
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def normalize_birth_date(value: Any) -> str:
    """Render a birth date as ``YYYY-MM-DD``; empty string when absent/invalid.

    Accepts a :class:`datetime.date`/``datetime`` or an ISO date/datetime string.
    Anything that is not a real calendar date normalizes to ``""`` so it simply
    does not contribute to the key (it is treated as "no birth date").
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return ""
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso).date().isoformat()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def name_key(payload: Mapping[str, Any]) -> str:
    """The name portion of the key: ``slug(first)|slug(last)`` (no birth date)."""
    return f"{slug(payload.get('first_name'))}|{slug(payload.get('last_name'))}"


def compute_dedup_key(payload: Mapping[str, Any]) -> str:
    """Deterministic ``dedup_key`` for a player payload (see module docstring)."""
    return f"{name_key(payload)}|{normalize_birth_date(payload.get('birth_date'))}"


def _has_name(payload: Mapping[str, Any]) -> bool:
    """True when the payload has at least one non-empty name part."""
    return bool(slug(payload.get("first_name")) or slug(payload.get("last_name")))


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FlaggedPlayer:
    """A candidate that could not be resolved automatically (needs review)."""

    identity: str
    dedup_key: str
    reason: str


@dataclass
class ResolutionResult:
    """Outcome of resolving a batch of player candidates.

    Attributes:
        resolved: Mapping ``input identity -> surviving players.id`` for every
            candidate that was resolved (created or merged). Downstream inserts
            use this to attach sessions/payments/metrics to the surviving row.
        created: ``players.id`` values for rows newly inserted by this run.
        merged_into_existing: identities that matched a pre-existing ``players``
            row (no new row created), mapped to that row's id.
        flagged: candidates withheld for Operator review (ambiguous / no name).
    """

    resolved: dict[str, Any] = field(default_factory=dict)
    created: list[Any] = field(default_factory=list)
    merged_into_existing: dict[str, Any] = field(default_factory=dict)
    flagged: list[FlaggedPlayer] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Player resolution: {len(self.resolved)} resolved "
            f"({len(self.created)} new, {len(self.merged_into_existing)} merged "
            f"into existing), {len(self.flagged)} flagged for review."
        )


# --------------------------------------------------------------------------- #
# Merge helper
# --------------------------------------------------------------------------- #


def _is_present(value: Any) -> bool:
    """True when a value is a usable, non-empty attribute value."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def merge_attributes(records: Sequence[ExtractedRecord]) -> dict[str, Any]:
    """Merge a same-person group, favoring the higher-confidence non-null value.

    For each fillable attribute the value is taken from the highest-confidence
    candidate that supplies a present (non-null/non-empty) value. Ties keep the
    earliest candidate in the (already confidence-sorted) sequence.
    """
    ordered = sorted(records, key=lambda r: r.confidence_score, reverse=True)
    merged: dict[str, Any] = {}
    for attr in _FILLABLE_ATTRS:
        for rec in ordered:
            candidate = (rec.payload or {}).get(attr)
            if _is_present(candidate):
                merged[attr] = candidate
                break
    return merged


# --------------------------------------------------------------------------- #
# Core resolution
# --------------------------------------------------------------------------- #


def _existing_by_dedup_key(cursor: Any, dedup_key: str) -> Any | None:
    cursor.execute("SELECT id FROM players WHERE dedup_key = %s", (dedup_key,))
    row = cursor.fetchone()
    return row[0] if row else None


def _existing_dedup_keys_for_name(cursor: Any, nk: str) -> set[str]:
    """Distinct existing ``dedup_key`` values sharing the name key ``nk``.

    Uses the prefix operator ``^@`` (starts-with) so name parts containing LIKE
    metacharacters are matched literally.
    """
    cursor.execute(
        "SELECT DISTINCT dedup_key FROM players WHERE dedup_key ^@ %s",
        (nk + "|",),
    )
    return {r[0] for r in cursor.fetchall() if r[0] is not None}


def resolve_players(
    records: Iterable[ExtractedRecord],
    conn: Any,
    *,
    location_id: Any,
    identity_of=lambda r: r.record_id,
) -> ResolutionResult:
    """Resolve player candidates to surviving ``players.id`` values.

    Args:
        records: Extracted *player* records (``target_table == 'players'``). Any
            record whose target table is not ``players`` is ignored.
        conn: An open DB-API connection whose ``search_path`` already points at
            the target schema. The caller owns the transaction; this function
            does not commit.
        location_id: ``location_id`` for any newly created ``players`` row
            (NOT NULL FK to ``locations``).
        identity_of: Extracts the stable input identity from a record (defaults
            to ``record_id``); used as the key of the returned mapping.

    Returns:
        A :class:`ResolutionResult` with the identity->id mapping, the ids of
        newly created rows, the identities merged into pre-existing rows, and the
        candidates flagged for Operator review.
    """
    players = [r for r in records if r.target_table == "players"]
    result = ResolutionResult()

    # Group the batch by name key, then by dedup key within each name.
    by_name: dict[str, dict[str, list[ExtractedRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    nameless: list[ExtractedRecord] = []
    for rec in players:
        payload = rec.payload or {}
        if not _has_name(payload):
            nameless.append(rec)
            continue
        by_name[name_key(payload)][compute_dedup_key(payload)].append(rec)

    # Records with no usable name cannot become a row -> review.
    for rec in nameless:
        result.flagged.append(
            FlaggedPlayer(identity_of(rec), compute_dedup_key(rec.payload or {}), MISSING_NAME)
        )

    with conn.cursor() as cursor:
        for nk, groups in by_name.items():
            existing_name_keys = _existing_dedup_keys_for_name(cursor, nk)
            distinct_keys = set(groups.keys()) | existing_name_keys

            # Ambiguous: one name resolves to more than one identity.
            if len(distinct_keys) > 1:
                for dk, group in groups.items():
                    for rec in group:
                        result.flagged.append(
                            FlaggedPlayer(identity_of(rec), dk, AMBIGUOUS_MERGE)
                        )
                continue

            # Unambiguous: exactly one dedup key for this name.
            (dedup_key,) = tuple(groups.keys())
            group = groups[dedup_key]
            existing_id = _existing_by_dedup_key(cursor, dedup_key)

            if existing_id is not None:
                # Merge into the surviving existing row: fill NULL gaps only.
                _fill_existing_gaps(cursor, existing_id, merge_attributes(group))
                for rec in group:
                    ident = identity_of(rec)
                    result.resolved[ident] = existing_id
                    result.merged_into_existing[ident] = existing_id
            else:
                new_id = _insert_player(
                    cursor, merge_attributes(group), dedup_key, location_id
                )
                result.created.append(new_id)
                for rec in group:
                    result.resolved[identity_of(rec)] = new_id

    return result


def _insert_player(
    cursor: Any, attrs: Mapping[str, Any], dedup_key: str, location_id: Any
) -> Any:
    """Insert one new ``players`` row (consent pending) and return its id.

    ``ON CONFLICT (dedup_key) DO NOTHING`` makes the insert a no-op if a row with
    the same key already exists (the unique constraint is the person-uniqueness
    guarantee); in that case the existing id is read back.
    """
    columns = ["first_name", "last_name", "birth_date", "grade", "photo_consent"]
    values: list[Any] = [
        attrs.get("first_name"),
        attrs.get("last_name"),
        attrs.get("birth_date"),
        attrs.get("grade"),
        attrs.get("photo_consent") if _is_present(attrs.get("photo_consent")) else False,
    ]
    columns += ["consent_status", "dedup_key", "location_id"]
    values += ["pending", dedup_key, location_id]  # new rows start pending (Req 8.4)

    placeholders = ", ".join(["%s"] * len(values))
    cursor.execute(
        f"INSERT INTO players ({', '.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT (dedup_key) DO NOTHING RETURNING id",
        values,
    )
    row = cursor.fetchone()
    if row is not None:
        return row[0]
    # Conflict: a row with this dedup_key already exists -> use it.
    return _existing_by_dedup_key(cursor, dedup_key)


def _fill_existing_gaps(cursor: Any, player_id: Any, attrs: Mapping[str, Any]) -> None:
    """Fill only NULL columns on an existing row (never clobber loaded data)."""
    sets: list[str] = []
    params: list[Any] = []
    for attr in ("first_name", "last_name", "birth_date", "grade"):
        if _is_present(attrs.get(attr)):
            sets.append(f"{attr} = COALESCE({attr}, %s)")
            params.append(attrs[attr])
    if not sets:
        return
    params.append(player_id)
    cursor.execute(
        f"UPDATE players SET {', '.join(sets)} WHERE id = %s",
        params,
    )
