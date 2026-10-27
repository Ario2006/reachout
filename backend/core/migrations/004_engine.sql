-- Milestone 2b: crawl cache and state, lead scoring/provenance/traces, crash-safe message state.

CREATE TABLE page_cache (
    url_hash      TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    final_url     TEXT,
    status        INTEGER NOT NULL,
    content_type  TEXT,
    etag          TEXT,
    last_modified TEXT,
    body          BLOB,                       -- zlib-compressed HTML; NULL when empty
    body_len      INTEGER NOT NULL DEFAULT 0,
    tier          TEXT NOT NULL DEFAULT 'http',
    fetched_at    REAL NOT NULL,              -- unix seconds
    ttl_s         INTEGER NOT NULL
);
CREATE INDEX idx_page_cache_fetched ON page_cache (fetched_at);

CREATE TABLE domain_state (
    host              TEXT PRIMARY KEY,
    robots_txt        TEXT,
    robots_status     INTEGER,
    robots_fetched_at REAL,
    crawl_delay       REAL,
    sitemaps_json     TEXT NOT NULL DEFAULT '[]',
    fail_count        INTEGER NOT NULL DEFAULT 0,
    blocked_until     REAL,                   -- circuit breaker / blocked by the site
    blocked_reason    TEXT,
    dead_until        REAL,                   -- DNS failure or unreachable: negative cache
    no_mx_until       REAL,
    updated_at        REAL
);

CREATE TABLE crawl_urls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES search_runs (id) ON DELETE CASCADE,
    domain     TEXT NOT NULL,
    url        TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'done', 'failed', 'skipped')),
    tier       TEXT,
    status     INTEGER,
    reason     TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at REAL,
    UNIQUE (run_id, url)
);
CREATE INDEX idx_crawl_urls_run ON crawl_urls (run_id, state);

-- Lead quality: score with reasons, a person key for (name, domain) dedupe, a correlation id.
ALTER TABLE leads ADD COLUMN score INTEGER;
ALTER TABLE leads ADD COLUMN score_reasons_json TEXT;
ALTER TABLE leads ADD COLUMN trace_id TEXT;
ALTER TABLE leads ADD COLUMN person_key TEXT;
CREATE INDEX idx_leads_score ON leads (score);
CREATE INDEX idx_leads_person ON leads (person_key);
CREATE INDEX idx_leads_trace ON leads (trace_id);

CREATE TABLE lead_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    run_id          INTEGER,
    source_url      TEXT NOT NULL,
    page_tier       TEXT,
    page_type       TEXT,
    fetched_at      REAL,
    context_snippet TEXT,
    first_seen      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (lead_id, source_url)
);

-- "Why was this found / emailed?": ordered decisions keyed by the lead's correlation id.
CREATE TABLE trace_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id  TEXT NOT NULL,
    run_id    INTEGER,
    at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    kind      TEXT NOT NULL,
    message   TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX idx_trace_events_trace ON trace_events (trace_id, id);

-- Crash-safe sending: a message is 'sending' before SMTP and 'sent' after. A message found in
-- 'sending' after a restart becomes 'needs_confirmation' and is never resent automatically.
ALTER TABLE messages ADD COLUMN state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued', 'sending', 'sent', 'failed', 'needs_confirmation', 'cancelled'));
ALTER TABLE messages ADD COLUMN attempt_id TEXT;
ALTER TABLE messages ADD COLUMN claimed_at REAL;
ALTER TABLE messages ADD COLUMN recipient_email TEXT;
ALTER TABLE messages ADD COLUMN recipient_domain TEXT;
ALTER TABLE messages ADD COLUMN error TEXT;
ALTER TABLE messages ADD COLUMN trace_id TEXT;
-- A person must never get the same email twice: enforced by the database, not only by checks.
CREATE UNIQUE INDEX uq_messages_once ON messages (campaign_id, lead_id, kind);
CREATE INDEX idx_messages_domain ON messages (recipient_domain, claimed_at);
