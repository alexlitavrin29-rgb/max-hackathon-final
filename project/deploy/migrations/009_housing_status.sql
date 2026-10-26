-- A personal mark, never a government registry result. Applied explicitly.
CREATE SEQUENCE IF NOT EXISTS housing_status_versions;
CREATE TABLE housing_statuses (
    owner TEXT PRIMARY KEY,
    account_epoch BIGINT NOT NULL CHECK (account_epoch >= 0),
    status TEXT NOT NULL CHECK (status IN ('unknown', 'not_in_list', 'in_list',
        'application_sent', 'offered', 'received', 'social_rent')),
    version BIGINT NOT NULL DEFAULT nextval('housing_status_versions') CHECK (version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
