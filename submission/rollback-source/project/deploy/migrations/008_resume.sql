-- Saved resumes have no TTL; versions remain unique across account deletion.
CREATE SEQUENCE IF NOT EXISTS planner_resume_versions;
CREATE TABLE planner_resumes (
    owner TEXT PRIMARY KEY,
    account_epoch BIGINT NOT NULL CHECK (account_epoch >= 0),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    version BIGINT NOT NULL DEFAULT nextval('planner_resume_versions') CHECK (version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
