-- Lead pipeline: run tracking, per-domain results, run log; lead provenance and last-activity.

ALTER TABLE leads ADD COLUMN search_run_id INTEGER REFERENCES search_runs (id);
ALTER TABLE leads ADD COLUMN updated_at TEXT;
UPDATE leads SET updated_at = created_at WHERE updated_at IS NULL;
CREATE INDEX idx_leads_run ON leads (search_run_id);
CREATE INDEX idx_leads_confidence ON leads (email_confidence);

ALTER TABLE search_runs ADD COLUMN mode TEXT;
ALTER TABLE search_runs ADD COLUMN job_id INTEGER;
ALTER TABLE search_runs ADD COLUMN state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled'));
ALTER TABLE search_runs ADD COLUMN stage TEXT;
ALTER TABLE search_runs ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE search_runs ADD COLUMN error TEXT;

CREATE TABLE run_domains (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL REFERENCES search_runs (id) ON DELETE CASCADE,
    domain  TEXT NOT NULL,
    url     TEXT,
    state   TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'crawling', 'done', 'skipped', 'failed')),
    pages   INTEGER NOT NULL DEFAULT 0,
    emails  INTEGER NOT NULL DEFAULT 0,
    leads   INTEGER NOT NULL DEFAULT 0,
    note    TEXT,
    UNIQUE (run_id, domain)
);
CREATE INDEX idx_run_domains_domain ON run_domains (domain);

CREATE TABLE run_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES search_runs (id) ON DELETE CASCADE,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_run_log_run ON run_log (run_id, id);
