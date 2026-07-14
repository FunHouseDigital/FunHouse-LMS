"""Property-based tests for the Auth_Service (Tasks 3.2, 3.3).

Implements design Properties 2 and 3. These exercise the real bcrypt hashing
and real PyJWT signing/verification (no mocking), so they run in every
environment (no database needed). Each property runs a minimum of 100 Hypothesis
iterations per the design's Testing Strategy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from funhouse_api.auth.service import (
    AuthError,
    AuthUser,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)

pytestmark = [pytest.mark.property]

# bcrypt is intentionally slow; disable the per-example deadline.
_SETTINGS = settings(max_examples=100, deadline=None)

_SECRET = "unit-test-secret"
_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Keep passwords under bcrypt's 72-byte input limit so a distinct plaintext is
# guaranteed to hash differently (bytes beyond 72 are ignored by bcrypt).
_passwords = st.text(min_size=1, max_size=60)


# Feature: funhouse-api, Property 2: Password verification round-trip — for any
# plaintext, verify_password(p, hash_password(p)) is true, a different plaintext
# is false, and the stored hash never equals the plaintext.
# Validates: Requirements 1.2, 1.4, 1.6
@_SETTINGS
@given(password=_passwords, other=_passwords)
def test_property_2_password_verification_round_trip(password: str, other: str) -> None:
    stored = hash_password(password)

    # The stored value is a bcrypt hash, never the plaintext (Req 1.6).
    assert stored != password
    # Correct plaintext verifies (Req 1.2).
    assert verify_password(password, stored) is True
    # Any different plaintext does not verify (Req 1.4).
    if other != password:
        assert verify_password(other, stored) is False


# Feature: funhouse-api, Property 3: Issued tokens carry a correct expiry and
# expired tokens are rejected — exp == iat + lifetime; any token whose exp is
# earlier than the current server time is rejected.
# Validates: Requirements 1.5, 2.4, 14.5
@_SETTINGS
@given(
    ttl_seconds=st.integers(min_value=1, max_value=1_000_000),
    issue_offset=st.integers(min_value=-1_000_000, max_value=1_000_000),
)
def test_property_3_token_expiry_issuance_and_rejection(
    ttl_seconds: int, issue_offset: int
) -> None:
    now = _EPOCH + timedelta(seconds=issue_offset)
    user = AuthUser(id="00000000-0000-0000-0000-000000000001", role="manager")

    token = issue_token(user, now=now, secret=_SECRET, ttl_seconds=ttl_seconds)

    # Decoded at issue time the token is valid and exp == iat + lifetime.
    claims = decode_token(token, now=now, secret=_SECRET)
    assert claims.exp == claims.iat + ttl_seconds

    # One second past expiry the token is rejected (Req 2.4).
    expired_moment = now + timedelta(seconds=ttl_seconds + 1)
    with pytest.raises(AuthError):
        decode_token(token, now=expired_moment, secret=_SECRET)
