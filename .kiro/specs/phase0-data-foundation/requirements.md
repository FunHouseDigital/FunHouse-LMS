# Requirements Document

## Introduction

Phase 0 (Data Foundation) of the FunHouse Operating System converts FunHouse Digital's paper-era records into a clean, structured founding dataset and stands up the PostgreSQL database that dataset lands in. The work is delivered as a document-intelligence pipeline run from a single operator laptop: a folder of source images and documents goes in, structured CSVs come out, and those CSVs are loaded into PostgreSQL. The pipeline must be re-runnable end to end via one documented command.

This spec covers ONLY Phase 0. The Revenue PWA, Lesson Engine, and SMS features (Phases 1–3) are explicitly out of scope. The scope here is: deploying the PostgreSQL schema with seed data, extracting structured records from historical source material, deterministically validating and flagging suspect records for human review, deduplicating people into a single player master list, loading clean records into PostgreSQL, archiving originals to S3, and wrapping the whole flow in one re-runnable command.

The subjects of much of this data are minors, so POPIA-aligned data protection is a first-class requirement rather than an afterthought.

## Glossary

- **Pipeline**: The document-intelligence system that collects, extracts, validates, loads, and archives historical records. Runs locally on the operator laptop.
- **Operator**: The person running the Pipeline from a laptop (business role: Aya, founder). Reviews flagged records.
- **Extractor**: The Pipeline component that produces structured records from source material, using the LLM_Abstraction for image/document intelligence.
- **Validator**: The deterministic Pipeline component that checks extracted records against business rules and marks records as flagged or clean.
- **Loader**: The deterministic Pipeline component that imports clean CSV records into the Database.
- **Archiver**: The Pipeline component that stores original source files in Object_Storage.
- **LLM_Abstraction**: A provider-agnostic interface, `llm.generate(task, context)`, through which all large-language-model calls are made. Supports pluggable providers (AWS Bedrock now, Anthropic API later).
- **Database**: The PostgreSQL database, deployed on AWS RDS in region `af-south-1`.
- **Object_Storage**: AWS S3 storage in region `af-south-1`, where original source files are archived under a `raw/` prefix.
- **Source_Folder**: The input directory containing one subfolder per source type: `cards/`, `sheets/`, `lessons/`, `photos/`, `whatsapp/`.
- **Extracted_Record**: A single structured row produced by the Extractor, carrying a confidence score and provenance to its source file.
- **Confidence_Score**: A numeric value between 0 and 1 attached to each Extracted_Record by the Extractor, representing extraction certainty.
- **Pricing_Tier**: A known valid amount-and-product combination: R10 for 20 minutes, R30 for 1 hour, R50 for 2 hours, R350 per month for a subscription (group of 4), or R250 for a Holiday Special pass.
- **Player**: A person who is a learner, a lounge customer, or both. Students and lounge customers share ONE table; a person who is both is a single row with one combined history.
- **Consent_Ledger**: The `consents` table, an append-only record where revocations are added as new rows and no row is ever deleted or overwritten.
- **Student_Metric**: A measured learning outcome with `metric_type` restricted to: `typing_wpm`, `typing_accuracy`, `homework_done`, `quiz_score`, `observation`.
- **Documented_Command**: The single, documented command that runs the full Pipeline end to end over a specified Source_Folder.
- **Flagged_Record**: An Extracted_Record that the Validator has marked for Operator review because it failed one or more validation rules.
- **Clean_Record**: An Extracted_Record that passed all validation rules or was approved by the Operator during review.

## Requirements

### Requirement 1: Deploy PostgreSQL Schema

**User Story:** As the Operator, I want the full database schema deployed on RDS, so that extracted records have a well-defined destination.

#### Acceptance Criteria

1. WHEN the schema deployment step runs, THE Pipeline SHALL create the following tables in the Database: `locations`, `schools`, `users`, `players`, `guardians`, `consents`, `products`, `entitlements`, `sessions`, `attendance`, `payments`, `lessons`, `student_metrics`, `sync_log`.
2. THE Pipeline SHALL define each created table with `id`, `created_at`, and `updated_at` columns.
3. THE Pipeline SHALL define a `location_id` column on every created table.
4. WHERE a table represents school-associated data, THE Pipeline SHALL define a `school_id` column on that table.
5. THE Pipeline SHALL deploy the Database on AWS RDS in region `af-south-1`.
6. IF a table to be created already exists in the Database, THEN THE Pipeline SHALL leave the existing table and its data intact and report the table as already present.
7. THE Pipeline SHALL restrict the `student_metrics.metric_type` column to the values `typing_wpm`, `typing_accuracy`, `homework_done`, `quiz_score`, and `observation`.

### Requirement 2: Seed Reference Data

**User Story:** As the Operator, I want locations, schools, products, and users seeded, so that historical records can reference valid entities on load.

#### Acceptance Criteria

1. WHEN the seed step runs, THE Pipeline SHALL insert a `locations` row for Row 1 (Smithfield).
2. WHEN the seed step runs, THE Pipeline SHALL insert `schools` rows for Mofulatshepe, Relebohile-Sibulele, and Smithfield Primary with `contract_status` set to `partner`.
3. WHEN the seed step runs, THE Pipeline SHALL insert `schools` rows for Thabo-Vuyo, Naledi, Rouxville Primary, and JB Tyu with `contract_status` set to `proposed`.
4. WHEN the seed step runs, THE Pipeline SHALL insert `products` rows for: PayPerUse-20min at R10, PayPerUse-1hr at R30, PayPerUse-2hr at R50, Subscription at R350, and Holiday Special at R250.
5. THE Pipeline SHALL record the Subscription product rules as 4 members, 2 hours per week, and a 3-month minimum term.
6. THE Pipeline SHALL record the Holiday Special product rules as 3 hours per week, reset on Sunday, no rollover, and a fixed window.
7. WHEN the seed step runs, THE Pipeline SHALL insert a `users` row for Aya with role `founder` and a `users` row for Loyiso with role `manager`.
8. IF a seed record with the same natural identity already exists in the Database, THEN THE Pipeline SHALL skip insertion of that record and leave the existing record unchanged.

### Requirement 3: Collect Source Material

**User Story:** As the Operator, I want the Pipeline to read a structured input folder, so that each source type is processed with the right handling.

#### Acceptance Criteria

1. WHEN the Pipeline starts, THE Pipeline SHALL read source files from the subfolders `cards/`, `sheets/`, `lessons/`, `photos/`, and `whatsapp/` within the Source_Folder.
2. IF an expected subfolder is missing from the Source_Folder, THEN THE Pipeline SHALL record the subfolder as absent and continue processing the remaining subfolders.
3. IF a file in the Source_Folder has an unsupported file type, THEN THE Pipeline SHALL skip that file and record the skip with the file path and reason.

### Requirement 4: Extract Structured Records from Images

**User Story:** As the Operator, I want image-based paper records turned into structured rows at low cost, so that the paper backlog becomes queryable data.

#### Acceptance Criteria

1. WHEN image source files are processed, THE Extractor SHALL submit extraction requests through the LLM_Abstraction using the AWS Bedrock Batch API.
2. THE Extractor SHALL supply the business rules for pricing tiers, product rules, school names, and known player names to the LLM_Abstraction as the extraction system prompt.
3. THE Extractor SHALL attach a Confidence_Score to each Extracted_Record.
4. THE Extractor SHALL attach a reference to the originating source file to each Extracted_Record.
5. THE Extractor SHALL write Extracted_Records as CSV files, one CSV per target table among `players`, `sessions`, `payments`, `lessons`, and `student_metrics`.

### Requirement 5: Parse Lesson Documents Directly

**User Story:** As the Operator, I want lesson `.docx` files parsed as text rather than through image OCR, so that lesson content is extracted accurately and cheaply.

#### Acceptance Criteria

1. WHEN a `.docx` file in the `lessons/` subfolder is processed, THE Extractor SHALL parse the file as text without image OCR.
2. WHEN a lesson document is parsed, THE Extractor SHALL produce one `lessons` record per lesson.
3. WHEN a lesson document contains an embedded learning measurement, THE Extractor SHALL produce a `student_metrics` record for that measurement with a `metric_type` from the allowed set.

### Requirement 6: Provider-Agnostic LLM Access

**User Story:** As the founder, I want all model calls to go through one abstraction, so that the business is not locked to a single AI vendor.

#### Acceptance Criteria

1. THE Extractor SHALL make all large-language-model calls through the LLM_Abstraction interface `llm.generate(task, context)`.
2. WHERE a different model provider is configured, THE LLM_Abstraction SHALL route calls to the configured provider without changes to Extractor code.
3. THE Pipeline SHALL exclude any dependency on Amazon Pinpoint, Amazon DynamoDB, Amazon Cognito, and AWS Lambda as application architecture.
4. THE Pipeline SHALL use PostgreSQL as the only database system.

### Requirement 7: Validate and Flag Suspect Records

**User Story:** As the Operator, I want the Pipeline to deterministically flag suspect records, so that I review only the records that need human judgment.

#### Acceptance Criteria

1. THE Validator SHALL evaluate every Extracted_Record using deterministic code without any large-language-model calls.
2. IF an Extracted_Record has a Confidence_Score below the configured threshold, THEN THE Validator SHALL mark the record as a Flagged_Record.
3. IF an Extracted_Record contains a date that is impossible, THEN THE Validator SHALL mark the record as a Flagged_Record.
4. IF an Extracted_Record references a person name that matches no known player name, THEN THE Validator SHALL mark the record as a Flagged_Record.
5. IF a payment Extracted_Record has an amount that matches no Pricing_Tier and no product price, THEN THE Validator SHALL mark the record as a Flagged_Record.
6. WHEN the Validator marks a record as a Flagged_Record, THE Validator SHALL record the reason for the flag on that record.
7. WHEN an Extracted_Record passes all validation rules, THE Validator SHALL mark the record as a Clean_Record.
8. WHEN validation completes, THE Validator SHALL present only Flagged_Records to the Operator for review.

### Requirement 8: Deduplicate Players into a Single Master List

**User Story:** As the founder, I want each person represented once, so that a learner who also games at the lounge has one identity and one history.

#### Acceptance Criteria

1. WHEN player records are loaded, THE Loader SHALL store learners and lounge customers in the single `players` table.
2. IF two extracted player records refer to the same person, THEN THE Loader SHALL merge those records into one `players` row.
3. WHEN two player records are merged, THE Loader SHALL associate the combined session, payment, and metric history with the single resulting `players` row.
4. WHEN a new `players` row is created, THE Loader SHALL set its `consent_status` to `pending`.
5. WHEN player loading completes, THE Loader SHALL include all 73 learners and the lounge regulars as deduplicated rows in the `players` table.

### Requirement 9: Load Clean Records into PostgreSQL

**User Story:** As the Operator, I want clean CSVs imported into the Database, so that the historical dataset is queryable in PostgreSQL.

#### Acceptance Criteria

1. WHEN the load step runs, THE Loader SHALL import Clean_Records into the `players`, `sessions`, `payments`, `lessons`, and `student_metrics` tables.
2. THE Loader SHALL execute the load using deterministic code without any large-language-model calls.
3. WHEN a legible paper record has been extracted and validated as clean, THE Loader SHALL create a corresponding row in the appropriate table.
4. WHEN a lesson-embedded metric has been extracted and validated as clean, THE Loader SHALL create a corresponding `student_metrics` row.
5. IF a Clean_Record would create a duplicate of an already-loaded record, THEN THE Loader SHALL skip the insertion and record the skip.

### Requirement 10: Index the Lesson Archive

**User Story:** As the founder, I want every lesson indexed and tagged, so that lesson content is retrievable and linked to its original file.

#### Acceptance Criteria

1. WHEN the lesson archive is processed, THE Loader SHALL create one `lessons` row for each lesson.
2. THE Loader SHALL store a reference to each lesson's original file in Object_Storage on the corresponding `lessons` row.
3. THE Loader SHALL tag each `lessons` row with its topic and its phenomenon.

### Requirement 11: Maintain the Consent Ledger as Append-Only

**User Story:** As the founder, I want consent history preserved, so that the business has a defensible POPIA audit trail.

#### Acceptance Criteria

1. WHEN a consent record is written, THE Loader SHALL append a new row to the `consents` table.
2. WHEN a consent is revoked, THE Loader SHALL append a new revocation row to the `consents` table rather than modifying an existing row.
3. THE Loader SHALL retain every existing `consents` row without deletion or overwrite.

### Requirement 12: Archive Originals to S3

**User Story:** As the founder, I want every original source file archived, so that no source material is lost and the Pipeline can be re-run.

#### Acceptance Criteria

1. WHEN a source file is processed, THE Archiver SHALL store the original file in Object_Storage under the `raw/` prefix.
2. THE Archiver SHALL store archived files in region `af-south-1`.
3. THE Archiver SHALL preserve each archived original without modifying its content.

### Requirement 13: Provide a Single Re-Runnable Command

**User Story:** As the Operator, I want one documented command to reprocess a folder, so that new source material can be onboarded without manual step-by-step work.

#### Acceptance Criteria

1. WHEN the Operator runs the Documented_Command against a Source_Folder, THE Pipeline SHALL execute collection, extraction, validation, load, and archival end to end.
2. THE Pipeline SHALL provide written documentation of the Documented_Command.
3. WHEN the Documented_Command is run again over the same Source_Folder, THE Pipeline SHALL avoid creating duplicate rows for records already loaded.
4. WHEN the Documented_Command is run over a new Source_Folder, THE Pipeline SHALL process the new folder end to end.

### Requirement 14: Protect Personal Data by Design

**User Story:** As the founder, I want POPIA-aligned data protection built in, so that the business lawfully handles data about minors.

#### Acceptance Criteria

1. THE Pipeline SHALL exclude national identity numbers and physical addresses from extracted and loaded records.
2. THE Pipeline SHALL store the Database with encryption at rest.
3. THE Pipeline SHALL transmit data to and from the Database and Object_Storage using encryption in transit.
4. THE Pipeline SHALL keep the Database and Object_Storage in region `af-south-1`.
5. WHEN the Pipeline writes a record to the Database, THE Pipeline SHALL record the acting identity in `logged_by` and append a corresponding `sync_log` entry.

### Requirement 15: Operate Offline-First

**User Story:** As the Operator, I want the Pipeline to run locally, so that it works without a persistent internet connection except where remote services are required.

#### Acceptance Criteria

1. THE Pipeline SHALL run on the Operator laptop.
2. THE Pipeline SHALL require internet connectivity only for AWS Bedrock calls and for Object_Storage archival and Database load.
3. WHILE performing collection and validation, THE Pipeline SHALL operate without requiring internet connectivity.
