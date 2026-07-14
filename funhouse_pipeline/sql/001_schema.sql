-- Feature: phase0-data-foundation
-- Migration 001: core 14-table schema.
--
-- Conventions applied to every table (Req 1.2, 1.3):
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid()
--   created_at / updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
--   location_id UUID (Req 1.3)
--   sync metadata: client_id / device_id / client_timestamp
--   school_id where the table represents school-associated data (Req 1.4)
--
-- Schema deployment is idempotent: CREATE TABLE IF NOT EXISTS means an existing
-- table is left intact with its data (Req 1.6). gen_random_uuid() is part of
-- PostgreSQL core since v13 (the pgcrypto extension is NOT required); AWS RDS
-- runs 13+, so no extension needs to be installed.

-- 1. locations
CREATE TABLE IF NOT EXISTS locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    location_id UUID,                     -- self-reference; nullable at bootstrap
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
);

-- 2. schools
CREATE TABLE IF NOT EXISTS schools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    contract_status TEXT NOT NULL CHECK (contract_status IN ('partner','proposed')),
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
);

-- 3. users (self-managed auth: bcrypt hash travels with the API)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('founder','manager','coach','operator')),
    email TEXT UNIQUE,
    password_hash TEXT,                   -- bcrypt; nullable for backfilled users
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. guardians
CREATE TABLE IF NOT EXISTS guardians (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT NOT NULL,
    last_name TEXT,
    phone TEXT,                           -- no ID numbers, no physical addresses (POPIA, Req 14.1)
    relationship TEXT,
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. players (learners + lounge customers -- ONE table, Req 8.1)
CREATE TABLE IF NOT EXISTS players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name TEXT NOT NULL,
    last_name TEXT,
    birth_date DATE,
    grade TEXT,
    school_id UUID REFERENCES schools(id),         -- nullable (lounge customers)
    guardian_id UUID REFERENCES guardians(id),
    consent_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (consent_status IN ('pending','granted','revoked')),   -- backfill starts pending (Req 8.4, 14)
    consent_date TIMESTAMPTZ,
    photo_consent BOOLEAN NOT NULL DEFAULT false,
    active BOOLEAN NOT NULL DEFAULT true,
    dedup_key TEXT,                                 -- normalized identity for merge/idempotency
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dedup_key)                              -- enforces one row per person
);

-- 6. consents (APPEND-ONLY ledger, Req 11)
CREATE TABLE IF NOT EXISTS consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id),
    guardian_id UUID REFERENCES guardians(id),
    consent_type TEXT NOT NULL,                     -- e.g. data_processing, photo
    granted BOOLEAN NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    method TEXT,                                    -- paper, verbal, whatsapp, form
    captured_by_user_id UUID REFERENCES users(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    -- No UPDATE/DELETE permitted; enforced by trigger + role grants (migration 002).
);

-- 7. products
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('pay_per_use','subscription','once_off_pass')),
    price_cents INTEGER NOT NULL,                   -- store money as integer cents
    rules JSONB NOT NULL DEFAULT '{}'::jsonb,       -- e.g. members, hours/week, term, reset
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
);

-- 8. entitlements (what a player is entitled to under a product)
CREATE TABLE IF NOT EXISTS entitlements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id),
    product_id UUID NOT NULL REFERENCES products(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','expired','cancelled')),
    remaining_units INTEGER,                        -- sessions/time remaining (nullable = unlimited)
    valid_from DATE,
    valid_to DATE,
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9. sessions
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id),
    session_type TEXT NOT NULL
        CHECK (session_type IN ('lesson','kit','esports','lounge')),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_minutes INTEGER,
    school_id UUID REFERENCES schools(id),          -- school-associated (Req 1.4)
    logged_by UUID REFERENCES users(id),            -- who touched the record (Req 14.5)
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    natural_key TEXT UNIQUE                          -- idempotency (Req 9.5)
);

-- 10. attendance
CREATE TABLE IF NOT EXISTS attendance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    player_id UUID NOT NULL REFERENCES players(id),
    attendance_date DATE NOT NULL,
    present BOOLEAN NOT NULL DEFAULT true,
    school_id UUID REFERENCES schools(id),
    logged_by UUID REFERENCES users(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    natural_key TEXT UNIQUE
);

-- 11. payments
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id),
    product_id UUID REFERENCES products(id),
    amount_cents INTEGER NOT NULL,
    method TEXT,                                     -- cash, transfer, etc.
    paid_at TIMESTAMPTZ,
    logged_by UUID REFERENCES users(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    natural_key TEXT UNIQUE
);

-- 12. lessons
CREATE TABLE IF NOT EXISTS lessons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    topic TEXT,                                      -- tag (Req 10.3)
    phenomenon TEXT,                                 -- tag (Req 10.3)
    content TEXT,
    original_file_ref TEXT,                          -- S3 key of original (Req 10.2)
    school_id UUID REFERENCES schools(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    natural_key TEXT UNIQUE
);

-- 13. student_metrics
CREATE TABLE IF NOT EXISTS student_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id),
    lesson_id UUID REFERENCES lessons(id),
    metric_type TEXT NOT NULL
        CHECK (metric_type IN ('typing_wpm','typing_accuracy','homework_done','quiz_score','observation')),  -- Req 1.7
    value TEXT NOT NULL,                             -- text to hold numeric + observation notes
    measured_at TIMESTAMPTZ,
    logged_by UUID REFERENCES users(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    client_id TEXT, device_id TEXT, client_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    natural_key TEXT UNIQUE
);

-- 14. sync_log (who touched what, when -- Req 14.5)
CREATE TABLE IF NOT EXISTS sync_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT,
    user_id UUID REFERENCES users(id),
    entity TEXT NOT NULL,                            -- table name
    record_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('insert','update','delete','skip')),
    client_timestamp TIMESTAMPTZ,
    server_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    location_id UUID NOT NULL REFERENCES locations(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
