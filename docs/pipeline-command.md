# The `funhouse-pipeline` command

Phase 0 is driven by **one documented, re-runnable command** that runs the full
pipeline end to end over a `Source_Folder`:

**Collect → Extract → Validate → Load → Archive**

The command is registered as a console script (`funhouse-pipeline`) and can also
be invoked as a module (`python -m funhouse_pipeline.orchestrator.cli`).

```bash
funhouse-pipeline run \
  --source-folder <path> \
  --config <path/to/config.yaml> \
  [--stage collect|extract|validate|load|archive] \
  [--resume <run_id>] \
  [--run-id <run_id>] \
  [--state-dir <path>] \
  [--reference-date YYYY-MM-DD] \
  [--no-migrate] [--no-seed]
```

Running it processes a **new** `Source_Folder` fully, and re-running it over the
**same** folder is safe — duplicates are skipped and recorded rather than
re-inserted (idempotent, Req 13.3/13.4).

## What a run does

1. **Collect** — walks `cards/`, `sheets/`, `lessons/`, `photos/`, `whatsapp/`
   inside the `Source_Folder` and routes each file by source type. Missing
   subfolders are recorded as absent and do not halt the run; unsupported files
   are skipped with a recorded reason. *No network I/O.*
2. **Extract** — image files go through the LLM abstraction (AWS Bedrock Batch by
   default); `.docx` lessons are parsed as text (no OCR, no LLM). Every record
   carries a confidence score and source-file provenance.
3. **Validate** — deterministic, LLM-free classification into clean vs flagged,
   with a reason per flag. Flagged records are written to a review artifact
   (`flagged-<run_id>.csv`) and are never auto-loaded. *No network I/O.*
4. **Load** — deduplicates people into the single `players` table, resolves
   foreign keys, and inserts clean records idempotently (natural-key
   `ON CONFLICT DO NOTHING`). Every write is audited (`logged_by` + `sync_log`).
5. **Archive** — uploads each original byte-for-byte to S3 under `raw/` in
   `af-south-1`, with a SHA-256 content hash; re-archival of an unchanged file is
   a no-op.

At the end the command prints a **run summary** with counts:

```
Run <run_id> summary (stages: collect, extract, validate, load, archive):
  collected: 42
  extracted: 118
  flagged:   7
  loaded:    103
  skipped:   8
  archived:  42
```

## Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--source-folder <path>` | *(required)* | The input `Source_Folder`. |
| `--config <path>` | *(none)* | YAML config file; environment variables override it. |
| `--stage <name>` | *(all five)* | Run a single stage plus its cheap prerequisites (see below). |
| `--resume <run_id>` | *(off)* | Resume a prior run from its manifest, skipping work already completed. |
| `--run-id <id>` | *(generated)* | Use an explicit run id. |
| `--state-dir <path>` | `.pipeline-state/` | Where the run manifest is persisted. |
| `--reference-date YYYY-MM-DD` | today | The "today" used for date-plausibility checks (set it for reproducible validation). |
| `--migrate` / `--no-migrate` | `--migrate` | Apply schema migrations idempotently before Load, or assume the schema exists. |
| `--seed` / `--no-seed` | `--seed` | Apply the reference-data seed idempotently before Load, or assume it is seeded. |

### `--stage` semantics

Stages depend on each other, so a single-stage run also executes the cheap,
offline prerequisites its inputs need:

- `--stage collect` → collect
- `--stage extract` → collect, extract
- `--stage validate` → collect, extract, validate
- `--stage load` → collect, extract, validate, load
- `--stage archive` → collect, archive (archive only needs the collected originals)

Omitting `--stage` runs all five.

## The run manifest (resumability)

Each run persists a JSON manifest to `--state-dir` (default `.pipeline-state/`)
named `<run_id>.json`. It records per-stage lifecycle, per-file status per stage,
per-record disposition (loaded/skipped/flagged), and an append-only list of every
skip and failure — so nothing disappears silently. `--resume <run_id>` reloads it
and re-attempts only work not yet completed. Because the database enforces
idempotency independently (natural keys, `dedup_key`, archive content hashes),
correctness holds even if a file is reprocessed.

## Error handling & retries

- **Recoverable per-item errors** (a bad `.docx`, an unresolved foreign key, a
  duplicate row) are recorded in the manifest/summary and the run continues.
- **Unrecoverable run errors** (database connection/auth failure with Load in
  scope, persistent S3 archival failure, a missing S3 bucket) halt the run with
  an actionable message and a non-zero exit code.
- **Bedrock Batch and S3 calls** are retried with **exponential backoff**; every
  retry and every persistent failure is recorded in the manifest.

## Offline behavior (Req 15.3)

Collect and Validate require **no internet**. Network access is needed only for:

- **Bedrock** (Extract, when `LLM_PROVIDER=bedrock`),
- **S3** (Archive),
- **PostgreSQL/RDS** (Load).

You can therefore run `--stage collect` or `--stage validate` fully offline.

## Configuration & environment variables

Settings load from the optional YAML `--config` file and are then overridden by
environment variables (env wins). See `config.example.yaml`.

| Setting | YAML key | Env var | Default |
|---------|----------|---------|---------|
| LLM provider | `llm_provider` | `LLM_PROVIDER` | `bedrock` (`bedrock` \| `anthropic`) |
| Confidence threshold | `confidence_threshold` | `CONFIDENCE_THRESHOLD` | `0.7` |
| AWS region | `region` | `AWS_REGION` | `af-south-1` |
| S3 bucket | `s3_bucket` | `S3_BUCKET` | *(none)* |
| DB host | `database.host` | `DB_HOST` | `localhost` |
| DB port | `database.port` | `DB_PORT` | `5432` |
| DB name | `database.dbname` | `DB_NAME` | `funhouse` |
| DB user | `database.user` | `DB_USER` | `funhouse` |
| DB password | `database.password` | `DB_PASSWORD` | *(none)* |
| DB sslmode | `database.sslmode` | `DB_SSLMODE` | `prefer` (`require` against RDS) |

Switching model providers is a **single environment variable** — no code change:

```bash
LLM_PROVIDER=anthropic funhouse-pipeline run --source-folder ./intake --config config.yaml
```

## Examples

```bash
# Full end-to-end run over a new intake folder.
funhouse-pipeline run --source-folder ./intake --config config.yaml

# Re-run the same folder later (idempotent; duplicates skipped).
funhouse-pipeline run --source-folder ./intake --config config.yaml

# Resume a prior run after fixing a transient outage.
funhouse-pipeline run --source-folder ./intake --config config.yaml --resume 7f3c...

# Collect only, fully offline (no AWS, no DB).
funhouse-pipeline run --source-folder ./intake --stage collect

# Load into an already-deployed, already-seeded database.
funhouse-pipeline run --source-folder ./intake --config config.yaml --no-migrate --no-seed
```


## Data protection & residency (POPIA)

The subjects of much of this data are minors, so data protection is a first-class
part of the deployment (Req 14):

- **Encryption at rest (Req 14.2).** The PostgreSQL database is deployed on AWS
  RDS with **storage encryption enabled** at the instance level, so all data —
  including automated backups, read replicas, and snapshots — is encrypted at
  rest. A local development PostgreSQL cannot emulate RDS storage encryption;
  this control is provisioned as part of the RDS instance configuration.
- **Encryption in transit (Req 14.3).** All database connections use TLS
  (`sslmode=require` against RDS; the default `prefer` locally), and all S3 and
  Bedrock traffic uses HTTPS/TLS — boto3's default transport.
- **Region residency (Req 14.4, 1.5, 12.2).** Both the database and the S3
  archive bucket live in region **`af-south-1`**, which is the default region in
  configuration.
- **No prohibited fields (Req 14.1).** National identity numbers and physical
  addresses are never loaded — the Loader drops them defensively even if an
  extractor produced them.
- **Audit trail (Req 14.5).** Every write records the acting identity in
  `logged_by` and appends a `sync_log` entry, and the `consents` ledger is
  append-only.
