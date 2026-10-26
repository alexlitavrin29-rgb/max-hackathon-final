-- Personal career stage; applied explicitly, independent of housing and plans.
CREATE SEQUENCE IF NOT EXISTS career_status_versions;
CREATE TABLE career_statuses (
    owner TEXT PRIMARY KEY,
    account_epoch BIGINT NOT NULL CHECK (account_epoch >= 0),
    status TEXT NOT NULL CHECK (status IN ('unknown', 'searching', 'resume', 'interview', 'employed')),
    version BIGINT NOT NULL DEFAULT nextval('career_status_versions') CHECK (version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
