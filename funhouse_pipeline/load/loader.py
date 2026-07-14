"""Deterministic clean-record loading (Task 11, Req 9.1-9.5, 10.2/10.3, 13.3, 14.1).

This module is the second half of the Load stage. Task 10
(:mod:`funhouse_pipeline.load.dedup`) resolves *players*; this module imports the
remaining Clean_Records into ``players``, ``sessions``, ``payments``, ``lessons``
and ``student_metrics`` (Req 9.1) using **deterministic code with no LLM call**
(Req 9.2, Property 22). It never imports :func:`funhouse_pipeline.llm.llm_generate`
nor any provider SDK.

What it does
------------
1. **POPIA filter first (Req 14.1, Property 28).** Every record's payload is run
   through :func:`funhouse_pipeline.load.popia.filter_payload` *before* anything
   is inserted, so national identity numbers and physical addresses can never
   reach the database even if an extractor produced them.
2. **Player resolution (Req 8, via Task 10).** Player Clean_Records are resolved
   through :func:`resolve_players`, yielding the one surviving ``players.id`` per
   person that referencing rows attach to (Req 9.3).
3. **Deterministic FK resolution (Req 9.1).** ``*_name`` values are resolved to
   ``*_id`` foreign keys by normalized lookup:

   * ``player_name``  -> ``players.id``  (name index built from the resolved
     players, using the same slug normalization as dedup)
   * ``school_name``  -> ``schools.id``  (seeded)
   * ``product_name`` -> ``products.id`` (seeded)

   A ``*_name`` that is **present but resolves to no row (or is ambiguous)** is a
   FK-resolution failure: the record is **flagged for Operator review and not
   inserted** (never inserted with a null/guessed FK) -- design § Error Handling.
   An absent name on a nullable FK is fine (the column is left NULL).
4. **Amount normalization (Req 9.1).** ``payments.amount`` is converted to
   ``amount_cents`` consistently with the Validator's amount rule
   (``"R30"`` -> 3000; a bare number is matched against the known tier prices
   and otherwise treated as Rand). See :func:`amount_to_cents`.
5. **Natural-key idempotency (Req 9.5, 13.3).** Each row gets a deterministic
   :func:`compute_natural_key` (a stable hash of its identifying domain fields +
   source provenance) and is inserted with ``INSERT ... ON CONFLICT (natural_key)
   DO NOTHING``. A conflict means the row was already loaded -> the insert is a
   no-op and the skip is recorded (design § Idempotency & Re-Runnability).
6. **Per-record transactions (design § Error Handling).** Each record's insert
   runs in its own (nested) transaction, so a failure leaves no half-written row
   and does not abort the rest of the batch.
7. **Lesson tagging + provenance (Req 10.2, 10.3).** ``lessons`` rows are written
   with ``topic``, ``phenomenon`` and an ``original_file_ref`` derived from the
   source file via the shared :func:`funhouse_pipeline.archive.archive_key`
   helper -- the *same* key the Archive stage (Task 13) will store the original
   under, so provenance is consistent.

Audit trail (Task 12.1, Req 14.5)
---------------------------------
Every write this loader performs is audited: the acting identity is written to
``logged_by`` on the tables that carry that column (``sessions``, ``payments``,
``student_metrics``; ``players`` and ``lessons`` have no such column per the
schema), and a ``sync_log`` entry is appended **inside the same per-record
transaction** as the write via
:func:`funhouse_pipeline.load.audit.append_sync_log`. Inserts append an
``insert`` entry, natural-key/dedup duplicates append a ``skip`` entry (Req 9.5),
and player-dedup merges append an ``update`` entry. Because the audit insert
shares the write's transaction, the audit trail can never diverge from what was
actually committed.

Consent ledger (Task 12.2, Req 11): consent writes are append-only and live in
:mod:`funhouse_pipeline.load.consent`. Should consent records ever arrive mixed
into the record stream as ``ExtractedRecord``s (``target_table == 'consents'``),
:func:`load_clean_records` routes them to that append-only API; in Phase 0 the
extract flow produces none (consents are not among the five target tables), so
the hook is dormant but ready.

Out of scope (clean seams left for later tasks)
-----------------------------------------------
* The actual S3 upload is **Task 13**; this module only derives the key.

Transaction ownership: the caller supplies an open connection whose
``search_path`` points at the target schema. This module manages nested
transactions per record; it does not close the connection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from funhouse_pipeline.archive import archive_key
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.audit import (
    ACTION_INSERT,
    ACTION_SKIP,
    ACTION_UPDATE,
    append_sync_log,
)
from funhouse_pipeline.load.dedup import ResolutionResult, resolve_players, slug
from funhouse_pipeline.load.popia import filter_payload
from funhouse_pipeline.validate.results import ValidationResult
from funhouse_pipeline.validate.validator import (
    _amount_candidates_cents,
    _known_amount_cents,
)

# Reason codes for records withheld from load and routed to Operator review.
UNRESOLVED_PLAYER = "UNRESOLVED_PLAYER"      # player_name matched no players row
AMBIGUOUS_PLAYER = "AMBIGUOUS_PLAYER"        # player_name matched >1 players row
UNRESOLVED_SCHOOL = "UNRESOLVED_SCHOOL"      # school_name matched no schools row
UNRESOLVED_PRODUCT = "UNRESOLVED_PRODUCT"    # product_name matched no products row
BAD_AMOUNT = "BAD_AMOUNT"                    # payment amount could not be parsed
BAD_SESSION_TYPE = "BAD_SESSION_TYPE"        # session_type not an allowed value
INSERT_ERROR = "INSERT_ERROR"               # unexpected DB error during insert

#: The four non-player target tables this loader inserts into (players are
#: handled by :func:`resolve_players`).
INSERTABLE_TABLES: tuple[str, ...] = ("sessions", "payments", "lessons", "student_metrics")

#: Allowed ``sessions.session_type`` values (mirrors the schema CHECK).
ALLOWED_SESSION_TYPES: frozenset[str] = frozenset({"lesson", "kit", "esports", "lounge"})

#: Identifying domain fields per table used to build the deterministic
#: natural_key (combined with the source-file provenance). See design
#: § Idempotency & Re-Runnability.
_NATURAL_KEY_FIELDS: Mapping[str, tuple[str, ...]] = {
    "sessions": ("player_name", "session_type", "started_at", "ended_at"),
    "payments": ("player_name", "product_name", "amount", "paid_at"),
    "lessons": ("title",),
    "student_metrics": ("player_name", "metric_type", "measured_at", "value"),
}


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LoadedRow:
    """A row successfully inserted (or already present) for a Clean_Record."""

    table: str
    record_id: str
    row_id: Any
    natural_key: str


@dataclass(frozen=True)
class SkippedRow:
    """A Clean_Record whose insert was a no-op because the row already exists."""

    table: str
    record_id: str
    natural_key: str
    reason: str = "duplicate"


@dataclass(frozen=True)
class FlaggedLoad:
    """A Clean_Record withheld from load and routed to Operator review."""

    table: str
    record_id: str
    reason: str
    detail: str = ""


@dataclass
class LoadResult:
    """Outcome of loading a batch of Clean_Records.

    Attributes:
        players: The player :class:`ResolutionResult` from Task 10.
        loaded: Rows inserted (or already present) for non-player records.
        skipped: Records skipped as duplicates (natural-key conflict).
        flagged: Records withheld for review (FK failure / bad value / error).
        dropped_fields: ``record_id -> [prohibited keys dropped]`` (POPIA).
    """

    players: ResolutionResult = field(default_factory=ResolutionResult)
    loaded: list[LoadedRow] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    flagged: list[FlaggedLoad] = field(default_factory=list)
    dropped_fields: dict[str, list[str]] = field(default_factory=dict)
    #: Number of ``sync_log`` entries appended during this load (Req 14.5).
    audit_entries: int = 0
    #: Consent-ledger append outcome, when consent records were routed here
    #: (``target_table == 'consents'``). ``None`` when no consents were seen.
    consents: Any | None = None

    def summary(self) -> str:
        return (
            f"Load complete: {len(self.loaded)} loaded, {len(self.skipped)} "
            f"skipped (duplicate), {len(self.flagged)} flagged for review, "
            f"{self.audit_entries} audit entries; "
            f"{self.players.summary()}"
        )


# --------------------------------------------------------------------------- #
# Amount + natural key helpers
# --------------------------------------------------------------------------- #


def _rand_default_cents(value: Any) -> int | None:
    """Fallback amount->cents: treat the value as Rands (``R``-prefix or bare)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower()
    if text.startswith("r"):
        text = text[1:].strip()
    text = text.replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return round(number * 100)


def amount_to_cents(value: Any, *, known_cents: frozenset[int] = frozenset()) -> int | None:
    """Convert a raw payment amount to integer cents (Req 9.1).

    Consistent with the Validator's amount rule: a value is reduced to candidate
    cent interpretations (``"R30"`` -> 3000; a bare ``30`` -> both 30 and 3000).
    When any candidate matches a known tier/product price the matching value is
    returned (this is the interpretation the record was validated under);
    otherwise the amount is treated as Rands (the documented default).

    Returns ``None`` when the amount cannot be parsed at all.
    """
    candidates = _amount_candidates_cents(value)
    if not candidates:
        return None
    for cents in candidates:
        if cents in known_cents:
            return cents
    return _rand_default_cents(value)


def _norm_field(value: Any) -> str:
    """Normalize a value for natural-key composition (stable across runs)."""
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def compute_natural_key(table: str, payload: Mapping[str, Any], source_file: str) -> str:
    """Deterministic ``natural_key`` for a row (Req 9.5).

    A stable SHA-256 over the table name, the table's identifying domain fields
    (in a fixed order), and the source-file provenance. The same logical record
    from the same source always hashes to the same key, so re-loading it is a
    no-op via ``ON CONFLICT (natural_key) DO NOTHING``.
    """
    fields = _NATURAL_KEY_FIELDS.get(table, ())
    parts = [table]
    parts.extend(_norm_field((payload or {}).get(f)) for f in fields)
    parts.append(_norm_field(source_file))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{table}:{digest}"


# --------------------------------------------------------------------------- #
# FK indexes
# --------------------------------------------------------------------------- #


class _NameIndex:
    """Normalized name -> id lookup with ambiguity detection.

    A name that maps to exactly one id resolves to it; a name mapping to more
    than one id is *ambiguous* and resolution returns a sentinel so the caller
    can flag the record rather than guess.
    """

    AMBIGUOUS = object()

    def __init__(self) -> None:
        self._by_name: dict[str, Any] = {}

    def add(self, name: str, row_id: Any) -> None:
        key = slug(name)
        if not key:
            return
        existing = self._by_name.get(key, None)
        if existing is None:
            self._by_name[key] = row_id
        elif existing is self.AMBIGUOUS or existing == row_id:
            self._by_name[key] = existing if existing == row_id else self.AMBIGUOUS
        else:
            self._by_name[key] = self.AMBIGUOUS

    def resolve(self, name: Any) -> Any:
        """Return the id, ``AMBIGUOUS``, or ``None`` when unknown/empty."""
        key = slug(name)
        if not key:
            return None
        return self._by_name.get(key, None)


def _build_players_index(conn: Any) -> _NameIndex:
    """Build a ``"first last"`` (and bare ``first``) -> players.id index."""
    index = _NameIndex()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id, first_name, last_name FROM players")
        for row_id, first, last in cursor.fetchall():
            full = f"{first or ''} {last or ''}".strip()
            index.add(full, row_id)
            # Also index the bare first name so single-name references resolve
            # when unambiguous (ambiguity is handled by the index).
            if first:
                index.add(first, row_id)
    return index


def _build_reference_index(conn: Any, table: str) -> _NameIndex:
    """Build a ``name -> id`` index for a seeded reference table (schools/products)."""
    index = _NameIndex()
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT id, name FROM {table}")  # table is an internal constant
        for row_id, name in cursor.fetchall():
            index.add(name, row_id)
    return index


# --------------------------------------------------------------------------- #
# Record intake
# --------------------------------------------------------------------------- #


def _as_record(item: Any) -> ExtractedRecord:
    """Accept either an ExtractedRecord or a ValidationResult (use its record)."""
    if isinstance(item, ValidationResult):
        return item.record
    return item


def _popia_filter_records(
    records: Iterable[Any],
) -> tuple[list[ExtractedRecord], dict[str, list[str]]]:
    """Strip prohibited fields from every record up front (Req 14.1)."""
    filtered: list[ExtractedRecord] = []
    dropped_by_record: dict[str, list[str]] = {}
    for item in records:
        record = _as_record(item)
        clean_payload, dropped = filter_payload(record.payload or {})
        if dropped:
            dropped_by_record[record.record_id] = dropped
        filtered.append(replace(record, payload=clean_payload))
    return filtered, dropped_by_record


# --------------------------------------------------------------------------- #
# Per-table column building
# --------------------------------------------------------------------------- #


def _resolve_player(index: _NameIndex, name: Any) -> tuple[Any, str | None]:
    """Resolve a required player_name -> (id, error_reason)."""
    resolved = index.resolve(name)
    if resolved is None:
        return None, UNRESOLVED_PLAYER
    if resolved is _NameIndex.AMBIGUOUS:
        return None, AMBIGUOUS_PLAYER
    return resolved, None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


# --------------------------------------------------------------------------- #
# Core load
# --------------------------------------------------------------------------- #


def load_clean_records(
    records: Iterable[Any],
    conn: Any,
    *,
    location_id: Any,
    rules: Any | None = None,
    logged_by: Any | None = None,
    device_id: Any | None = None,
) -> LoadResult:
    """Load Clean_Records into their target tables deterministically (Req 9).

    Args:
        records: Clean_Records to load, as :class:`ExtractedRecord`s or
            :class:`~funhouse_pipeline.validate.results.ValidationResult`s (the
            record of each is used). Records for all five target tables may be
            mixed; players are resolved via Task 10, the rest are inserted here.
        conn: An open DB-API connection with ``search_path`` set to the target
            schema. The caller owns the connection; this function manages nested
            per-record transactions and does not close it.
        location_id: ``location_id`` (NOT NULL FK) for newly created rows.
        rules: Optional :class:`~funhouse_pipeline.extract.context.BusinessRules`
            used to normalize payment amounts to the validated cent value.
        logged_by: Optional acting-user id (a ``users.id``) written to
            ``logged_by`` where the column exists and recorded as the acting
            identity on every appended ``sync_log`` entry (Req 14.5).
        device_id: Optional originating device id recorded on ``sync_log`` rows.

    Returns:
        A :class:`LoadResult` describing players resolution, loaded rows,
        duplicate skips, review flags, POPIA fields dropped, and the count of
        ``sync_log`` audit entries appended.
    """
    known_cents = _known_amount_cents(rules) if rules is not None else frozenset()

    filtered, dropped_fields = _popia_filter_records(records)
    result = LoadResult(dropped_fields=dropped_fields)

    player_records = [r for r in filtered if r.target_table == "players"]
    other_records = [r for r in filtered if r.target_table in INSERTABLE_TABLES]
    consent_records = [r for r in filtered if r.target_table == "consents"]

    # 1. Resolve players (Task 10) and audit those writes inside one
    #    transaction, so the player rows and their sync_log entries are atomic.
    with conn.transaction():
        result.players = resolve_players(
            player_records, conn, location_id=location_id
        )
        with conn.cursor() as cursor:
            for player_id in result.players.created:
                append_sync_log(
                    cursor,
                    entity="players",
                    record_id=player_id,
                    action=ACTION_INSERT,
                    location_id=location_id,
                    user_id=logged_by,
                    device_id=device_id,
                )
                result.audit_entries += 1
            # A merge fills gaps on an existing row -> audited as an update.
            for player_id in dict.fromkeys(result.players.merged_into_existing.values()):
                append_sync_log(
                    cursor,
                    entity="players",
                    record_id=player_id,
                    action=ACTION_UPDATE,
                    location_id=location_id,
                    user_id=logged_by,
                    device_id=device_id,
                )
                result.audit_entries += 1

    # 2. Build FK indexes from the now-resolved reference/player data.
    players_index = _build_players_index(conn)
    schools_index = _build_reference_index(conn, "schools")
    products_index = _build_reference_index(conn, "products")

    # 3. Resolve each player's school_name FK (Req 9.1). The player row already
    #    exists (a person must be represented for history); a school_name that
    #    is present but unresolvable is flagged for review and the FK left NULL
    #    rather than guessed.
    _resolve_player_schools(
        conn, player_records, result, schools_index, location_id, logged_by, device_id
    )

    # 4. Insert each non-player record in its own transaction.
    for record in other_records:
        _load_one(
            record,
            conn,
            result,
            location_id=location_id,
            players_index=players_index,
            schools_index=schools_index,
            products_index=products_index,
            known_cents=known_cents,
            logged_by=logged_by,
            device_id=device_id,
        )

    # 5. Route any consent records to the append-only consent ledger (Task 12.2).
    #    Phase 0's extract flow produces none, so this is normally dormant.
    if consent_records:
        from funhouse_pipeline.load.consent import load_consent_records

        result.consents = load_consent_records(
            consent_records,
            conn,
            location_id=location_id,
            players_index=players_index,
            captured_by_user_id=logged_by,
            device_id=device_id,
        )
        result.audit_entries += len(result.consents.appended)

    return result


def _resolve_player_schools(
    conn: Any,
    player_records: Sequence[ExtractedRecord],
    result: LoadResult,
    schools_index: _NameIndex,
    location_id: Any,
    logged_by: Any | None,
    device_id: Any | None,
) -> None:
    for record in player_records:
        school_name = _blank_to_none((record.payload or {}).get("school_name"))
        if school_name is None:
            continue
        player_id = result.players.resolved.get(record.record_id)
        if player_id is None:
            continue  # player itself was flagged/ambiguous; nothing to attach to
        resolved = schools_index.resolve(school_name)
        if resolved is None or resolved is _NameIndex.AMBIGUOUS:
            result.flagged.append(
                FlaggedLoad("players", record.record_id, UNRESOLVED_SCHOOL, str(school_name))
            )
            continue
        try:
            # Attach the school FK and audit the update atomically (Req 14.5).
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE players SET school_id = COALESCE(school_id, %s) "
                        "WHERE id = %s",
                        (resolved, player_id),
                    )
                    append_sync_log(
                        cursor,
                        entity="players",
                        record_id=player_id,
                        action=ACTION_UPDATE,
                        location_id=location_id,
                        user_id=logged_by,
                        device_id=device_id,
                    )
            result.audit_entries += 1
        except Exception as exc:  # pragma: no cover - defensive
            result.flagged.append(
                FlaggedLoad("players", record.record_id, INSERT_ERROR, str(exc))
            )


def _load_one(
    record: ExtractedRecord,
    conn: Any,
    result: LoadResult,
    *,
    location_id: Any,
    players_index: _NameIndex,
    schools_index: _NameIndex,
    products_index: _NameIndex,
    known_cents: frozenset[int],
    logged_by: Any | None,
    device_id: Any | None,
) -> None:
    """Build columns, resolve FKs, insert one record, and audit the write.

    The INSERT and its ``sync_log`` entry run in the same nested transaction so
    the audit trail is atomic with the write (Req 14.5): a successful insert
    appends an ``insert`` entry keyed to the new row id; a natural-key duplicate
    (ON CONFLICT DO NOTHING no-op) appends a ``skip`` entry keyed to the existing
    row id (Req 9.5); a failed insert commits neither row nor audit entry.
    """
    table = record.target_table
    payload = record.payload or {}

    try:
        columns = _build_columns(
            table,
            record,
            location_id=location_id,
            players_index=players_index,
            schools_index=schools_index,
            products_index=products_index,
            known_cents=known_cents,
            logged_by=logged_by,
            conn=conn,
        )
    except _FKResolutionError as err:
        result.flagged.append(FlaggedLoad(table, record.record_id, err.reason, err.detail))
        return

    natural_key = compute_natural_key(table, payload, record.source_file)
    columns["natural_key"] = natural_key

    col_names = list(columns.keys())
    placeholders = ", ".join(["%s"] * len(col_names))
    values = [columns[c] for c in col_names]

    try:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {table} ({', '.join(col_names)}) "
                    f"VALUES ({placeholders}) "
                    "ON CONFLICT (natural_key) DO NOTHING RETURNING id",
                    values,
                )
                row = cursor.fetchone()

                if row is None:
                    # Duplicate: resolve the existing row's id so the skip can be
                    # audited against the row it collided with (Req 9.5, 14.5).
                    cursor.execute(
                        f"SELECT id FROM {table} WHERE natural_key = %s",
                        (natural_key,),
                    )
                    existing = cursor.fetchone()
                    audited_id = existing[0] if existing else None
                    action = ACTION_SKIP
                else:
                    audited_id = row[0]
                    action = ACTION_INSERT

                audited = audited_id is not None
                if audited:
                    append_sync_log(
                        cursor,
                        entity=table,
                        record_id=audited_id,
                        action=action,
                        location_id=location_id,
                        user_id=logged_by,
                        device_id=device_id,
                    )
    except Exception as exc:  # noqa: BLE001 - per-record isolation (design)
        result.flagged.append(
            FlaggedLoad(table, record.record_id, INSERT_ERROR, str(exc))
        )
        return

    if row is None:
        # ON CONFLICT DO NOTHING matched an existing row -> duplicate skip (Req 9.5).
        result.skipped.append(SkippedRow(table, record.record_id, natural_key))
    else:
        result.loaded.append(LoadedRow(table, record.record_id, row[0], natural_key))
    if audited:
        result.audit_entries += 1


class _FKResolutionError(Exception):
    """Internal signal that a required FK/value could not be resolved."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _build_columns(
    table: str,
    record: ExtractedRecord,
    *,
    location_id: Any,
    players_index: _NameIndex,
    schools_index: _NameIndex,
    products_index: _NameIndex,
    known_cents: frozenset[int],
    logged_by: Any | None,
    conn: Any,
) -> dict[str, Any]:
    payload = record.payload or {}
    columns: dict[str, Any] = {"location_id": location_id}

    if table == "sessions":
        player_id, err = _resolve_player(players_index, payload.get("player_name"))
        if err:
            raise _FKResolutionError(err, str(payload.get("player_name")))
        session_type = _blank_to_none(payload.get("session_type"))
        if session_type is None or str(session_type).strip().lower() not in ALLOWED_SESSION_TYPES:
            raise _FKResolutionError(BAD_SESSION_TYPE, str(session_type))
        columns["player_id"] = player_id
        columns["session_type"] = str(session_type).strip().lower()
        columns["started_at"] = _blank_to_none(payload.get("started_at"))
        columns["ended_at"] = _blank_to_none(payload.get("ended_at"))
        columns["duration_minutes"] = _to_int(payload.get("duration_minutes"))
        columns["school_id"] = _resolve_optional_fk(
            schools_index, payload.get("school_name"), UNRESOLVED_SCHOOL
        )
        if logged_by is not None:
            columns["logged_by"] = logged_by

    elif table == "payments":
        player_id, err = _resolve_player(players_index, payload.get("player_name"))
        if err:
            raise _FKResolutionError(err, str(payload.get("player_name")))
        amount_cents = amount_to_cents(payload.get("amount"), known_cents=known_cents)
        if amount_cents is None:
            raise _FKResolutionError(BAD_AMOUNT, str(payload.get("amount")))
        columns["player_id"] = player_id
        columns["product_id"] = _resolve_optional_fk(
            products_index, payload.get("product_name"), UNRESOLVED_PRODUCT
        )
        columns["amount_cents"] = amount_cents
        columns["method"] = _blank_to_none(payload.get("method"))
        columns["paid_at"] = _blank_to_none(payload.get("paid_at"))
        if logged_by is not None:
            columns["logged_by"] = logged_by

    elif table == "lessons":
        # Lessons tagging + provenance (Req 10.2, 10.3). Title is NOT NULL.
        title = _blank_to_none(payload.get("title")) or record.source_file
        columns["title"] = title
        columns["topic"] = _blank_to_none(payload.get("topic"))
        columns["phenomenon"] = _blank_to_none(payload.get("phenomenon"))
        columns["content"] = _blank_to_none(payload.get("content"))
        columns["original_file_ref"] = archive_key(record.source_file)

    elif table == "student_metrics":
        player_id, err = _resolve_player(players_index, payload.get("player_name"))
        if err:
            raise _FKResolutionError(err, str(payload.get("player_name")))
        columns["player_id"] = player_id
        columns["lesson_id"] = _resolve_lesson_id(conn, payload.get("lesson_title"))
        columns["metric_type"] = _blank_to_none(payload.get("metric_type"))
        columns["value"] = payload.get("value")
        columns["measured_at"] = _blank_to_none(payload.get("measured_at"))
        if logged_by is not None:
            columns["logged_by"] = logged_by

    return columns


def _resolve_optional_fk(index: _NameIndex, name: Any, reason: str) -> Any:
    """Resolve a nullable FK: NULL when absent, flag (raise) when unresolvable."""
    if _blank_to_none(name) is None:
        return None
    resolved = index.resolve(name)
    if resolved is None or resolved is _NameIndex.AMBIGUOUS:
        raise _FKResolutionError(reason, str(name))
    return resolved


def _resolve_lesson_id(conn: Any, lesson_title: Any) -> Any:
    """Best-effort ``lesson_title`` -> ``lessons.id`` (nullable; never flags).

    ``student_metrics.lesson_id`` is nullable, so an unresolved lesson title
    simply leaves the link NULL rather than withholding the metric.
    """
    title = _blank_to_none(lesson_title)
    if title is None:
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM lessons WHERE lower(btrim(title)) = %s LIMIT 1",
            (str(title).strip().lower(),),
        )
        row = cursor.fetchone()
    return row[0] if row else None
