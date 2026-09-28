-- Timestamps are fixed-width UTC text (YYYY-MM-DDTHH:MM:SS.mmmZ) so that string order equals time order.

CREATE TABLE licenses (
    license_key   TEXT    PRIMARY KEY,
    monthly_limit INTEGER NOT NULL CHECK (monthly_limit >= 0),
    status        TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- Append-only: one row per inference. Inference inputs and results are never stored here.
CREATE TABLE usage_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key TEXT    NOT NULL REFERENCES licenses (license_key) ON DELETE RESTRICT,
    -- Round-tripping through strftime rejects other formats and impossible dates; IS also rejects NULL results.
    used_at     TEXT    NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%fZ', used_at) IS used_at)
);

CREATE INDEX idx_usage_logs_license_used_at ON usage_logs (license_key, used_at);
