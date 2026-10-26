-- K4 additive stop marker. Apply explicitly; no automatic migration at bot startup.
-- account_epochs is an existing minimal tombstone retained after account deletion.
ALTER TABLE account_epochs ADD COLUMN planner_stopped BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE account_epochs ADD COLUMN planner_event_at BIGINT NOT NULL DEFAULT 0 CHECK (planner_event_at>=0);
CREATE INDEX planner_notifications_sending ON planner_notifications(lease_until) WHERE state='sending';
