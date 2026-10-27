-- Milestones 3-6 (compose, sending, tracking, follow-ups). Only the lead agent writes migrations;
-- owners ask for a change instead of adding a file.

-- ---- compose ---------------------------------------------------------------------------------
ALTER TABLE templates ADD COLUMN is_starter INTEGER NOT NULL DEFAULT 0;
ALTER TABLE templates ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'));
ALTER TABLE templates ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'));

-- Free-form "my profile" merge fields ({{my_summary}} etc). key = the merge name, lowercase snake_case.
CREATE TABLE profile_fields (
    key      TEXT PRIMARY KEY CHECK (key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_]*'),
    label    TEXT NOT NULL,
    value    TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

-- No code wrote to drafts before milestone 3, so it is rebuilt with the final shape.
DROP TABLE drafts;
CREATE TABLE drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    lead_id       INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    kind          TEXT NOT NULL DEFAULT 'initial' CHECK (kind IN ('initial', 'followup_1', 'followup_2')),
    subject       TEXT NOT NULL,
    body          TEXT NOT NULL,                 -- rendered template, WITHOUT the footer (added at send time)
    opener        TEXT,                          -- the one LLM-written sentence block inside body
    opener_source TEXT NOT NULL DEFAULT 'none' CHECK (opener_source IN ('llm', 'fallback', 'manual', 'none')),
    flags_json    TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'needs_review', 'approved', 'rejected')),
    approved      INTEGER NOT NULL DEFAULT 0 CHECK (approved = (status = 'approved')),
    approved_at   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (campaign_id, lead_id, kind)
);
CREATE INDEX idx_drafts_campaign_status ON drafts (campaign_id, status);

-- Approval covers the exact text approved: any later edit to subject or body withdraws it.
CREATE TRIGGER trg_drafts_edit_withdraws_approval
AFTER UPDATE OF subject, body ON drafts
WHEN OLD.status = 'approved' AND (NEW.subject IS NOT OLD.subject OR NEW.body IS NOT OLD.body)
BEGIN
    UPDATE drafts SET status = 'pending', approved = 0, approved_at = NULL WHERE id = NEW.id;
END;

-- ---- campaigns / sending ---------------------------------------------------------------------
ALTER TABLE campaigns ADD COLUMN mode TEXT CHECK (mode IN ('internship', 'investor', 'service'));
ALTER TABLE campaigns ADD COLUMN delay_min_s INTEGER;      -- NULL = use Settings
ALTER TABLE campaigns ADD COLUMN delay_max_s INTEGER;
ALTER TABLE campaigns ADD COLUMN followups_require_approval INTEGER NOT NULL DEFAULT 0;
ALTER TABLE campaigns ADD COLUMN pause_reason TEXT;        -- human sentence shown in the UI banner
ALTER TABLE campaigns ADD COLUMN paused_by TEXT CHECK (paused_by IN ('user', 'bounce_rate', 'smtp_login', 'shutdown', 'all'));
ALTER TABLE campaigns ADD COLUMN next_send_at REAL;        -- unix seconds; the jittered earliest time of the next send
ALTER TABLE campaigns ADD COLUMN test_sent_at TEXT;
ALTER TABLE campaigns ADD COLUMN dry_run_at TEXT;
ALTER TABLE campaigns ADD COLUMN started_at TEXT;
ALTER TABLE campaigns ADD COLUMN finished_at TEXT;

ALTER TABLE messages ADD COLUMN draft_id INTEGER REFERENCES drafts (id);
ALTER TABLE messages ADD COLUMN body TEXT;                 -- snapshot of the approved draft taken at enqueue (no footer)
ALTER TABLE messages ADD COLUMN in_reply_to TEXT;          -- follow-ups: Message-ID of the previous email in the thread
ALTER TABLE messages ADD COLUMN references_text TEXT;      -- follow-ups: space-separated Message-IDs, oldest first
ALTER TABLE messages ADD COLUMN not_before REAL;           -- unix seconds; never claimed earlier (follow-ups)

-- Runtime flags: pause_all, last_clean_shutdown. Not user settings.
CREATE TABLE app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---- tracking --------------------------------------------------------------------------------
CREATE TABLE imap_state (
    folder          TEXT PRIMARY KEY,
    uidvalidity     INTEGER,
    last_uid        INTEGER NOT NULL DEFAULT 0,
    last_checked_at REAL,
    last_error      TEXT
);

ALTER TABLE events ADD COLUMN imap_message_id TEXT;        -- the incoming email's own Message-ID, for de-duplication
ALTER TABLE events ADD COLUMN from_addr TEXT;
ALTER TABLE events ADD COLUMN subject TEXT;
ALTER TABLE events ADD COLUMN status_code TEXT;            -- bounces: 5.1.1 etc
ALTER TABLE events ADD COLUMN read_at TEXT;                -- NULL = unread in the Inbox
ALTER TABLE events ADD COLUMN confirmed_at TEXT;           -- opt-out replies: set when the user confirms
CREATE UNIQUE INDEX uq_events_imap ON events (imap_message_id) WHERE imap_message_id IS NOT NULL;
CREATE INDEX idx_events_lead ON events (lead_id, occurred_at);
CREATE INDEX idx_events_type ON events (type, occurred_at);

ALTER TABLE leads ADD COLUMN outcome TEXT CHECK (outcome IN ('interested', 'not_interested', 'meeting_booked'));
ALTER TABLE leads ADD COLUMN last_activity_at TEXT;
CREATE INDEX idx_leads_activity ON leads (last_activity_at);
