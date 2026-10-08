-- Adds the time a SMART reading was taken. Safe to run more than once.
-- The API runs the same statement at startup; run this by hand only if the API's database user is not allowed to ALTER tables.
ALTER TABLE smart_reading ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP NULL;
CREATE INDEX IF NOT EXISTS ix_smart_reading_drive_collected ON smart_reading (drive_id, collected_at);
