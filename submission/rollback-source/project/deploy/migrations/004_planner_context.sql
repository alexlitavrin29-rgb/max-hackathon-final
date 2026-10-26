-- Prepared answer and trusted source snapshots remain attached to their owner.
-- Explicit deployment migration only; never run from application startup.
ALTER TABLE planner_goals ADD COLUMN context JSONB
    CHECK (context IS NULL OR jsonb_typeof(context) = 'object');
ALTER TABLE planner_tasks ADD COLUMN material_ids JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(material_ids) = 'array' AND jsonb_array_length(material_ids) <= 3);
ALTER TABLE planner_tasks ADD COLUMN material_links JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(material_links) = 'array' AND jsonb_array_length(material_links) <= 3);
