-- Mandatory resource descriptors are written only by the authenticated planner store.
ALTER TABLE planner_tasks ADD COLUMN IF NOT EXISTS required_resource TEXT;
ALTER TABLE planner_tasks ADD CONSTRAINT planner_tasks_required_resource_check
    CHECK (required_resource IS NULL OR required_resource IN ('app_work','app_rental','app_help'));
CREATE UNIQUE INDEX planner_tasks_required_resource_unique
    ON planner_tasks(owner,goal_id,required_resource) WHERE required_resource IS NOT NULL;

-- Reservations remain recognizable after expiry; an expired AI identity must
-- never fall back to a manual plan simply because its token was omitted.
CREATE TABLE planner_proposal_policies (
    owner TEXT NOT NULL,
    account_epoch BIGINT NOT NULL CHECK (account_epoch >= 0),
    goal_id UUID NOT NULL,
    question TEXT NOT NULL,
    required_resources JSONB NOT NULL CHECK (jsonb_typeof(required_resources) = 'array'),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours',
    PRIMARY KEY(owner,account_epoch,goal_id)
);
