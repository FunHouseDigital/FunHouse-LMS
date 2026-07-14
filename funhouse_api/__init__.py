"""FunHouse Container API (Spec 2).

A single portable HTTP API packaged as one Docker container, built on top of the
Phase 0 Data Foundation in ``funhouse_pipeline``. The guiding principle is
*reuse, not reimplementation*: every write path funnels through the reused
Phase 0 Load logic, DB layer, and configuration module.

This package depends only on a PostgreSQL DSN (rendered by the reused
``funhouse_pipeline.config.DatabaseConfig.dsn()``); it never contacts AWS
Cognito, DynamoDB, Pinpoint, or Lambda-as-architecture (Req 13.2, 13.3).
"""

from funhouse_api.app import create_app

__all__ = ["create_app"]
