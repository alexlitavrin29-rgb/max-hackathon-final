"""Minimal lifecycle persistence shared by candidate and delivery-free rollback."""

async def record_bot_availability(pool, owner, stopped, event_timestamp=None):
    owner=str(owner)
    if not owner.isascii() or not owner.isdecimal() or not 0<int(owner)<2**63:
        return False
    if event_timestamp is not None and (type(event_timestamp) is not int or not 0<event_timestamp<2**63):
        return False
    async with pool.acquire(timeout=3) as c:
        async with c.transaction():
            await c.execute("SET LOCAL lock_timeout='2s'")
            await c.execute("SET LOCAL statement_timeout='3s'")
            await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',owner)
            present=await c.fetchval("""SELECT count(*)=2 FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name='account_epochs'
                AND column_name IN ('planner_stopped','planner_event_at')""")
            if not present:
                return False
            timestamp=event_timestamp or await c.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
            previous=await c.fetchrow('SELECT planner_stopped,planner_event_at FROM account_epochs WHERE owner=$1',owner)
            if previous and (timestamp<previous['planner_event_at'] or
                    timestamp==previous['planner_event_at'] and (not stopped or previous['planner_stopped'])):
                # Duplicate/replayed events cannot undo a later state. Stop wins ties.
                return False
            await c.execute('''INSERT INTO account_epochs(owner,epoch,planner_stopped,planner_event_at)
                VALUES($1,0,$2,$3) ON CONFLICT(owner) DO UPDATE
                SET planner_stopped=$2,planner_event_at=$3''',owner,stopped,timestamp)
            if stopped and await c.fetchval("SELECT to_regclass('planner_tasks')"):
                await c.execute('''UPDATE planner_tasks SET remind_before_minutes=NULL,notification_override_at=NULL,
                    schedule_generation=schedule_generation+1,version=version+1,updated_at=now()
                    WHERE owner=$1 AND remind_before_minutes IS NOT NULL''',owner)
                await c.execute('''UPDATE planner_notifications SET state='cancelled',finished_at=now(),lease_until=NULL
                    WHERE owner=$1 AND state IN ('pending','claimed')''',owner)
            return True
