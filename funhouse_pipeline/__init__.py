"""FunHouse Phase 0 data-foundation pipeline.

A document-intelligence pipeline that converts paper-era records into a clean,
structured founding dataset inside PostgreSQL. Runs as five stages end to end:
Collect -> Extract -> Validate -> Load -> Archive.

This package is organised into stage/submodule packages; see the design document
(.kiro/specs/phase0-data-foundation/design.md) for the full architecture.
"""

__version__ = "0.1.0"
