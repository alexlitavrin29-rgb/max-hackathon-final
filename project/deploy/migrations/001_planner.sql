-- K0 only: durable manual goals and owner/epoch operation receipts.
-- Apply explicitly in an isolated test DB via planner_store.apply_migration.
CREATE TABLE IF NOT EXISTS planner_goals (
    owner TEXT NOT NULL,
    id UUID NOT NULL,
    title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 160 AND title = btrim(title)),
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, id)
);
CREATE TABLE IF NOT EXISTS planner_operations (
    owner TEXT NOT NULL,
    account_epoch BIGINT NOT NULL,
    key TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, account_epoch, key)
);
