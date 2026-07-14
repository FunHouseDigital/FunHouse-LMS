"""Entitlement_Engine package (Spec 2, Req 8, 9).

Deterministic creation, drawdown (with digital signature), recurring reset, and
balance queries for player entitlements. No AI/model calls are ever made here;
every value is derived from ``products.rules`` and the current time.
"""
