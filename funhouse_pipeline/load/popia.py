"""POPIA field filter: drop prohibited personal data before load (Req 14.1).

The pipeline must **exclude national identity numbers and physical addresses**
from loaded records (Req 14.1). The extraction system prompt already instructs
the model not to produce them (see :mod:`funhouse_pipeline.extract.context`),
but Load applies this filter *defensively* as a second line of defence: even if
an extractor payload contains such a field, it is stripped here **before** any
value reaches the database, so prohibited data can never be inserted (design
§ Load "POPIA filter"; Property 28).

Recognized prohibited fields
----------------------------
Matching is by a *canonical* form of the key -- lower-cased with every non
alphanumeric character removed -- so ``"ID Number"``, ``"id_number"`` and
``"id-number"`` all collapse to ``idnumber`` and are recognized identically.

National identity numbers (canonical forms)::

    id, idno, idnumber, nationalid, nationalidnumber, said, saidnumber,
    identity, identitynumber, identitydocument, passport, passportno,
    passportnumber

Physical addresses (canonical forms)::

    address, physicaladdress, homeaddress, residentialaddress, postaladdress,
    streetaddress, street, streetname, addressline1, addressline2, suburb,
    postalcode, zipcode, zip

This set is intentionally broad but explicit; it is documented here so the
recognized keys are auditable. Non-prohibited fields pass through untouched.
"""

from __future__ import annotations

from typing import Any, Mapping

# Canonical (alphanumeric-only, lower-cased) forms of prohibited keys.
_NATIONAL_ID_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "idno",
        "idnumber",
        "nationalid",
        "nationalidnumber",
        "said",
        "saidnumber",
        "identity",
        "identitynumber",
        "identitydocument",
        "passport",
        "passportno",
        "passportnumber",
    }
)

_ADDRESS_KEYS: frozenset[str] = frozenset(
    {
        "address",
        "physicaladdress",
        "homeaddress",
        "residentialaddress",
        "postaladdress",
        "streetaddress",
        "street",
        "streetname",
        "addressline1",
        "addressline2",
        "suburb",
        "postalcode",
        "zipcode",
        "zip",
    }
)

#: The full set of canonical prohibited keys (national IDs + addresses).
PROHIBITED_KEYS: frozenset[str] = _NATIONAL_ID_KEYS | _ADDRESS_KEYS


def _canonical(key: str) -> str:
    """Canonicalize a payload key: lower-case, keep only alphanumerics."""
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def is_prohibited_key(key: str) -> bool:
    """Return True when ``key`` names a national identity number or address."""
    return _canonical(key) in PROHIBITED_KEYS


def filter_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Strip prohibited fields from ``payload`` before it is loaded (Req 14.1).

    Args:
        payload: The domain payload of a record about to be loaded.

    Returns:
        ``(clean_payload, dropped_keys)`` where ``clean_payload`` is a new dict
        with every prohibited field removed and ``dropped_keys`` lists the
        original keys that were dropped (for logging/audit). The input mapping is
        never mutated.
    """
    clean: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in (payload or {}).items():
        if is_prohibited_key(key):
            dropped.append(key)
        else:
            clean[key] = value
    return clean, dropped
