-- Stable client identity for applied offline sync actions.
--
-- Existing entitlement-update audit rows and writes from an old API revision
-- during the migration-first rollout inherit TRUE. The new audit helper writes
-- FALSE explicitly, so post-cutover direct draws are not legacy candidates.
-- Keep the TRUE default until every old API instance has drained; a later
-- migration may safely change it to FALSE.
ALTER TABLE sync_log
    ADD COLUMN IF NOT EXISTS client_id TEXT;

ALTER TABLE sync_log
    ADD COLUMN IF NOT EXISTS legacy_client_id_missing BOOLEAN NOT NULL DEFAULT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_sync_log_client_id
    ON sync_log (client_id)
    WHERE client_id IS NOT NULL;
