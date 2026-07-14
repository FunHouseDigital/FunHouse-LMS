"""Load stage: deterministic import of Clean_Records into PostgreSQL.

Task 10 implements the **player deduplication / resolution layer** in
:mod:`funhouse_pipeline.load.dedup`; Task 11 adds the clean-record insert,
natural-key idempotency, deterministic FK resolution, the POPIA field filter,
and lesson tagging + ``original_file_ref`` in
:mod:`funhouse_pipeline.load.loader` and :mod:`funhouse_pipeline.load.popia`.
Task 12 adds the audit trail (``logged_by`` + ``sync_log``) in
:mod:`funhouse_pipeline.load.audit` -- wired into every loader insert and skip --
and the append-only consent ledger writes in
:mod:`funhouse_pipeline.load.consent`.
"""

from funhouse_pipeline.load.audit import (
    ACTION_DELETE,
    ACTION_INSERT,
    ACTION_SKIP,
    ACTION_UPDATE,
    ALLOWED_ACTIONS,
    append_sync_log,
)
from funhouse_pipeline.load.consent import (
    AppendedConsent,
    ConsentLoadResult,
    FlaggedConsent,
    append_consent,
    load_consent_records,
    revoke_consent,
)
from funhouse_pipeline.load.dedup import (
    AMBIGUOUS_MERGE,
    MISSING_NAME,
    FlaggedPlayer,
    ResolutionResult,
    compute_dedup_key,
    merge_attributes,
    name_key,
    normalize_birth_date,
    resolve_players,
    slug,
)
from funhouse_pipeline.load.loader import (
    ALLOWED_SESSION_TYPES,
    AMBIGUOUS_PLAYER,
    BAD_AMOUNT,
    BAD_SESSION_TYPE,
    INSERT_ERROR,
    INSERTABLE_TABLES,
    UNRESOLVED_PLAYER,
    UNRESOLVED_PRODUCT,
    UNRESOLVED_SCHOOL,
    FlaggedLoad,
    LoadedRow,
    LoadResult,
    SkippedRow,
    amount_to_cents,
    compute_natural_key,
    load_clean_records,
)
from funhouse_pipeline.load.popia import (
    PROHIBITED_KEYS,
    filter_payload,
    is_prohibited_key,
)

__all__ = [
    # audit (Task 12)
    "ACTION_INSERT",
    "ACTION_UPDATE",
    "ACTION_DELETE",
    "ACTION_SKIP",
    "ALLOWED_ACTIONS",
    "append_sync_log",
    # consent ledger (Task 12)
    "AppendedConsent",
    "ConsentLoadResult",
    "FlaggedConsent",
    "append_consent",
    "revoke_consent",
    "load_consent_records",
    # dedup (Task 10)
    "AMBIGUOUS_MERGE",
    "MISSING_NAME",
    "FlaggedPlayer",
    "ResolutionResult",
    "compute_dedup_key",
    "merge_attributes",
    "name_key",
    "normalize_birth_date",
    "resolve_players",
    "slug",
    # loader (Task 11)
    "ALLOWED_SESSION_TYPES",
    "INSERTABLE_TABLES",
    "UNRESOLVED_PLAYER",
    "AMBIGUOUS_PLAYER",
    "UNRESOLVED_SCHOOL",
    "UNRESOLVED_PRODUCT",
    "BAD_AMOUNT",
    "BAD_SESSION_TYPE",
    "INSERT_ERROR",
    "FlaggedLoad",
    "LoadedRow",
    "LoadResult",
    "SkippedRow",
    "amount_to_cents",
    "compute_natural_key",
    "load_clean_records",
    # POPIA (Task 11)
    "PROHIBITED_KEYS",
    "filter_payload",
    "is_prohibited_key",
]
