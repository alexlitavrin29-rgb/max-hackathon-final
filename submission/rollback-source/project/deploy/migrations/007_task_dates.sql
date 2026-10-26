-- Calendar dates carry no instant, timezone or reminder. Existing instant
-- constraints in002 remain in force and already reject reminders without due_at.
ALTER TABLE planner_tasks ADD COLUMN due_date DATE;
ALTER TABLE planner_tasks ADD CONSTRAINT planner_tasks_due_date_exclusive
    CHECK (due_date IS NULL OR due_at IS NULL);
CREATE INDEX planner_tasks_calendar_date ON planner_tasks(owner,due_date,id)
    WHERE status <> 'done' AND due_date IS NOT NULL;

-- Legacy original submission order was not recorded. Backfill a stable order;
-- new commits/aggregate edits persist their submitted array order explicitly.
ALTER TABLE planner_tasks ADD COLUMN plan_order INTEGER NOT NULL DEFAULT 0 CHECK (plan_order >= 0);
WITH ordered AS (
    SELECT owner,id,row_number() OVER (PARTITION BY owner,goal_id ORDER BY created_at,id)-1 AS position
    FROM planner_tasks
)
UPDATE planner_tasks AS task SET plan_order=ordered.position
FROM ordered WHERE task.owner=ordered.owner AND task.id=ordered.id;
CREATE INDEX planner_tasks_plan_order ON planner_tasks(owner,goal_id,plan_order,id);
