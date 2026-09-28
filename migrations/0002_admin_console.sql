-- Admin console: operator-facing memo, a URL-safe identifier that is not the license key,
-- and an append-only audit log of console operations.

ALTER TABLE licenses ADD COLUMN memo TEXT NOT NULL DEFAULT '' CHECK (length(memo) <= 200);

-- Always set by the application's INSERT statements; nullable only because ALTER TABLE cannot add NOT NULL without a constant default.
ALTER TABLE licenses ADD COLUMN public_id TEXT;
UPDATE licenses SET public_id = 'lic_' || lower(hex(randomblob(8))) WHERE public_id IS NULL;
CREATE UNIQUE INDEX idx_licenses_public_id ON licenses (public_id);
CREATE INDEX idx_licenses_created_at ON licenses (created_at);

CREATE TABLE console_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    operator_email TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('issue', 'update_limit', 'suspend', 'activate', 'update_memo', 'sign_in')),
    license_key TEXT REFERENCES licenses (license_key) ON DELETE RESTRICT,
    before_json TEXT,
    after_json TEXT,
    source_ip TEXT,
    created_at TEXT NOT NULL,
    CHECK ((action = 'sign_in') = (license_key IS NULL))
);

CREATE INDEX idx_console_audit_logs_license ON console_audit_logs (license_key, id);
