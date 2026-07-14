# FunHouse Pipeline — Phase 0 (Data Foundation)

A document-intelligence pipeline that converts FunHouse Digital's paper-era
records into a clean, structured founding dataset inside PostgreSQL. It runs as
five stages end to end: **Collect → Extract → Validate → Load → Archive**.

> This repository is being built task-by-task from the spec in
> `.kiro/specs/phase0-data-foundation/`. So far the project **skeleton +
> configuration + test harness** (Task 1) and the **PostgreSQL schema +
> migration runner** (Task 2) are implemented. Later stages are placeholders.

## Layout

```
funhouse_pipeline/
  config/        # YAML + env configuration loading (implemented)
  db/            # connection helpers + migration runner (implemented)
  sql/           # .sql migration files (001 schema, 002 append-only consents)
  llm/           # provider-agnostic LLM abstraction (later task)
  collect/       # source routing (later task)
  extract/       # image + .docx extraction (later task)
  validate/      # deterministic validation (later task)
  load/          # dedup + idempotent load (later task)
  archive/       # S3 archival (later task)
  orchestrator/  # the documented command (later task)
tests/           # pytest suite (+ Hypothesis property tests)
```

## The one command

The whole pipeline runs from a single documented, re-runnable command:

```bash
funhouse-pipeline run --source-folder <path> --config config.yaml
```

It executes **Collect → Extract → Validate → Load → Archive** end to end over a
`Source_Folder`, threads a resumable run manifest through `.pipeline-state/`, and
prints a summary (collected / extracted / flagged / loaded / skipped / archived).
Re-running over the same folder is idempotent (duplicates are skipped and
recorded). Single stages can be run with `--stage`, and a prior run resumed with
`--resume <run_id>`. Full documentation of the command, its flags, offline
behavior, and provider/config environment variables is in
[`docs/pipeline-command.md`](docs/pipeline-command.md).

## Setup

Requires Python 3.11+. Using [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
```

## Configuration

Settings load from an optional YAML file overlaid with environment variables
(env wins). See `config.example.yaml`. Region defaults to `af-south-1`;
`LLM_PROVIDER` selects the model provider; `confidence_threshold` controls
validation flagging.

## Running the tests

```bash
uv run pytest            # runs the full suite
```

### Database-backed tests

Some tests exercise the real PostgreSQL schema (schema columns, idempotent
deploy, `metric_type` domain enforcement). They need a reachable PostgreSQL
server and are **skipped automatically** when none is available — the suite
never hard-fails just because there is no database.

To enable them, point the suite at a database via a libpq connection string:

```bash
FUNHOUSE_TEST_DSN="host=localhost port=5432 dbname=funhouse_test user=postgres" \
    uv run pytest -m db
```

Each DB-backed test runs inside a disposable schema (created up front, dropped
`CASCADE` afterwards) and a transaction that is rolled back, so tests never
interfere with each other or leave residue.

## Applying the schema manually

The migration runner applies `funhouse_pipeline/sql/*.sql` in order and reports
each of the 14 tables as *created* or *already present* (idempotent, Req 1.6):

```python
from funhouse_pipeline.config import load_config
from funhouse_pipeline.db import connect, run_migrations

cfg = load_config("config.yaml")
with connect(cfg) as conn:
    result = run_migrations(conn)
    print(result.summary())
```
