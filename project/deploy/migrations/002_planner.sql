-- K1 additive data model. Explicit migration runner only, never app startup.
CREATE TABLE planner_tasks (
    owner TEXT NOT NULL,
    id UUID NOT NULL,
    goal_id UUID,
    title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 240 AND title = btrim(title)),
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started','in_progress','done')),
    due_at TIMESTAMPTZ,
    timezone TEXT,
    timezone_confirmed BOOLEAN NOT NULL DEFAULT false,
    remind_before_minutes INTEGER CHECK (remind_before_minutes IN (0,60)),
    notification_override_at TIMESTAMPTZ,
    schedule_generation BIGINT NOT NULL DEFAULT 1 CHECK (schedule_generation >= 1),
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    account_epoch BIGINT NOT NULL DEFAULT 0 CHECK (account_epoch >= 0),
    create_hash TEXT,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner,id),
    FOREIGN KEY (owner,goal_id) REFERENCES planner_goals(owner,id) ON DELETE RESTRICT,
    CHECK ((due_at IS NULL AND timezone IS NULL AND NOT timezone_confirmed
            AND remind_before_minutes IS NULL AND notification_override_at IS NULL)
        OR (due_at IS NOT NULL AND timezone IS NOT NULL AND timezone_confirmed)),
    CHECK (notification_override_at IS NULL OR remind_before_minutes IS NOT NULL),
    CHECK ((status='done') = (completed_at IS NOT NULL)),
    CHECK (status <> 'done' OR (remind_before_minutes IS NULL AND notification_override_at IS NULL))
);
CREATE INDEX planner_tasks_goal ON planner_tasks(owner,goal_id,id);
CREATE INDEX planner_tasks_due ON planner_tasks(owner,due_at,id) WHERE status <> 'done';
CREATE INDEX planner_tasks_completed ON planner_tasks(owner,id) WHERE status='done';

CREATE TABLE planner_notifications (
    owner TEXT NOT NULL,
    id UUID NOT NULL,
    task_id UUID NOT NULL,
    generation BIGINT NOT NULL CHECK (generation >= 1),
    task_version BIGINT NOT NULL CHECK (task_version >= 1),
    account_epoch BIGINT NOT NULL CHECK (account_epoch >= 0),
    notify_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN
        ('pending','claimed','sending','delivered','cancelled','missed','failed','unknown')),
    claim_token UUID,
    lease_until TIMESTAMPTZ,
    sending_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    message_id TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner,id),
    FOREIGN KEY (owner,task_id) REFERENCES planner_tasks(owner,id) ON DELETE CASCADE,
    UNIQUE (owner,task_id,generation),
    CHECK (state <> 'claimed' OR (claim_token IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK (state <> 'sending' OR (claim_token IS NOT NULL AND sending_at IS NOT NULL)),
    CHECK (state NOT IN ('delivered','cancelled','missed','failed','unknown') OR finished_at IS NOT NULL)
);
CREATE INDEX planner_notifications_pending ON planner_notifications(notify_at,owner,task_id)
    WHERE state='pending';
CREATE INDEX planner_notifications_claimed ON planner_notifications(lease_until)
    WHERE state='claimed';
CREATE INDEX planner_notifications_owner_task ON planner_notifications(owner,task_id);
