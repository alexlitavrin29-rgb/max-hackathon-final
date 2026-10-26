-- Explicit deployment migration only. Drafts are never executable planner commands.
-- Keep versions unique across owner deletion, expiry and account recreation.
-- Deliberately not OWNED BY the table: dropping/recreating it must not reset IDs.
CREATE SEQUENCE IF NOT EXISTS planner_draft_versions;
CREATE TABLE planner_drafts (
    owner TEXT PRIMARY KEY,
    account_epoch BIGINT NOT NULL CHECK (account_epoch >= 0),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object' AND state ? 'kind'
        AND jsonb_typeof(state->'kind') = 'string'
        AND state->>'kind' IN ('consultation', 'editor')),
    version BIGINT NOT NULL DEFAULT nextval('planner_draft_versions') CHECK (version >= 1),
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expires_at = updated_at + interval '24 hours')
);
