"""Players resource package (Req 6, 8.7, 15).

Roster read, registration with append-only consents, and per-player history,
all scoped by the RBAC_Enforcer and reusing the Phase 0 Load logic (player
dedup, append-only consent ledger, POPIA filter, audit ledger).
"""
