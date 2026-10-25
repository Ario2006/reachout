-- Reachout schema v1. Emails are always stored lowercase (enforced by CHECK).

CREATE TABLE leads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT,
    email            TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
    role             TEXT,
    company          TEXT,
    domain           TEXT,
    source_url       TEXT,
    context_snippet  TEXT,
    email_confidence TEXT NOT NULL DEFAULT 'published_generic'
        CHECK (email_confidence IN ('published_personal', 'published_generic', 'guessed')),
    mode             TEXT CHECK (mode IN ('internship', 'investor', 'service')),
    status           TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'drafted', 'approved', 'queued', 'sent', 'replied',
                          'bounced', 'opted_out', 'auto_reply', 'closed')),
    notes            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_leads_domain ON leads (domain);
CREATE INDEX idx_leads_status ON leads (status);

CREATE TABLE search_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_text  TEXT NOT NULL,
    plan_json     TEXT,
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    leads_found   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mode            TEXT CHECK (mode IN ('internship', 'investor', 'service')),
    name            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    followup_1_body TEXT,
    followup_2_body TEXT
);

CREATE TABLE campaigns (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL,
    template_id        INTEGER REFERENCES templates (id),
    attachment_path    TEXT,
    daily_cap          INTEGER NOT NULL DEFAULT 25 CHECK (daily_cap BETWEEN 1 AND 100),
    window_start       TEXT NOT NULL DEFAULT '09:00',
    window_end         TEXT NOT NULL DEFAULT '18:00',
    followups_enabled  INTEGER NOT NULL DEFAULT 0,
    followup_days_json TEXT NOT NULL DEFAULT '[4, 9]',
    state              TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'running', 'paused', 'done')),
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id),
    lead_id     INTEGER NOT NULL REFERENCES leads (id),
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    flags_json  TEXT NOT NULL DEFAULT '[]',
    approved    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (campaign_id, lead_id)
);

CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads (id),
    campaign_id INTEGER NOT NULL REFERENCES campaigns (id),
    kind        TEXT NOT NULL CHECK (kind IN ('initial', 'followup_1', 'followup_2')),
    message_id  TEXT UNIQUE,
    subject     TEXT,
    sent_at     TEXT,
    smtp_result TEXT
);
CREATE INDEX idx_messages_lead ON messages (lead_id);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER REFERENCES leads (id),
    message_ref INTEGER REFERENCES messages (id),
    type        TEXT NOT NULL CHECK (type IN ('reply', 'bounce', 'auto_reply', 'opt_out')),
    snippet     TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE suppression (
    email    TEXT PRIMARY KEY CHECK (email = lower(email)),
    reason   TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Non-secret settings only; values are JSON-encoded.
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Single-row liveness marker written by the worker process.
CREATE TABLE worker_heartbeat (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    pid     INTEGER NOT NULL,
    beat_at TEXT NOT NULL
);
