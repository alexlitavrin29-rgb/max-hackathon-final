"""Durable planner transactions; caller holds the authenticated owner lock.

No transport, session TTL, LLM call, or implicit startup migration. Future workers
must acquire owner lock BEFORE task/notification locks; see the v1 API contract.
"""
from datetime import date, datetime, timedelta, timezone
import re
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, available_timezones

MIGRATION = Path(__file__).resolve().parents[2] / 'deploy/migrations/001_planner.sql'
MIGRATIONS = tuple(MIGRATION.with_name(name) for name in (
    '001_planner.sql', '002_planner.sql', '003_planner_delivery.sql', '004_planner_context.sql',
    '005_planner_drafts.sql', '006_required_search.sql', '007_task_dates.sql', '008_resume.sql'))
SUPPORTED_TIMEZONES = tuple(sorted(z for z in available_timezones()
    if z != 'localtime' and not z.startswith(('posix/', 'right/'))))
TASK_FIELDS = ('goal_id', 'title', 'status', 'due_at', 'due_date', 'timezone', 'timezone_confirmed',
               'plan_order',
               'remind_before_minutes', 'notification_override_at')


class PlannerConflict(ValueError):
    def __init__(self, code='idempotency_conflict', status=409):
        self.code, self.status = code, status
        super().__init__(code)


def fail(code, status=422):
    raise PlannerConflict(code, status)


async def apply_migration(pool):
    """Explicit additive migration; atomic, serialized and checksum checked."""
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute('SELECT pg_advisory_xact_lock(78738101)')
            await connection.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            for path in MIGRATIONS:
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                existing = await connection.fetchval(
                    'SELECT sha256 FROM schema_migrations WHERE version=$1', path.stem)
                if existing:
                    if existing != digest:
                        raise RuntimeError('Planner migration checksum mismatch')
                    continue
                await connection.execute(raw.decode('utf-8'))
                await connection.execute('INSERT INTO schema_migrations(version,sha256) VALUES($1,$2)',
                                         path.stem, digest)


def serial(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(type(value).__name__)


def request_hash(body):
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=serial)
    return hashlib.sha256(canonical.encode()).hexdigest()


def json_value(value, default=None):
    return json.loads(value) if isinstance(value, str) else value if value is not None else default


def goal_document(row):
    result = dict(id=str(row['id']), title=row['title'], version=row['version'],
                created_at=serial(row['created_at']), updated_at=serial(row['updated_at']))
    if row.get('context') is not None:
        result['context'] = json_value(row['context'])
    return result


def notify_at(task):
    if task.get('due_date') is not None or task['due_at'] is None or task['remind_before_minutes'] is None or task['status'] == 'done':
        return None
    return task['notification_override_at'] or task['due_at'] - timedelta(minutes=task['remind_before_minutes'])


def task_document(row):
    fields = ('id', *TASK_FIELDS, 'schedule_generation', 'version', 'completed_at', 'created_at', 'updated_at')
    result = {key: serial(row[key]) if isinstance(row[key], (UUID, date)) else row[key] for key in fields}
    result['material_ids'] = json_value(row.get('material_ids'), [])
    result['links'] = json_value(row.get('material_links'), [])
    if row.get('required_resource') is not None:
        result['required_resource'] = row['required_resource']
    result['notify_at'] = serial(notify_at(row)) if notify_at(row) is not None else None
    return result


def validate_schedule(task, supplied, *, creating=False, schedule_changed=False):
    calendar = task.get('due_date')
    if calendar is not None:
        if isinstance(calendar, str):
            if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', calendar) is None:
                fail('invalid_schedule')
            try:
                calendar = date.fromisoformat(calendar)
            except ValueError:
                fail('invalid_schedule')
        if type(calendar) is not date:
            fail('invalid_schedule')
        task['due_date'] = calendar
        if task['due_at'] is not None:
            fail('invalid_schedule')
    due, zone = task['due_at'], task['timezone']
    if due is None:
        if zone is not None or task['remind_before_minutes'] is not None or task['notification_override_at'] is not None:
            fail('invalid_schedule')
        task['timezone_confirmed'] = False
        return
    if zone not in SUPPORTED_TIMEZONES:
        fail('invalid_timezone')
    if (creating or 'due_at' in supplied or 'timezone' in supplied) and supplied.get('timezone_confirmed') is not True:
        fail('timezone_confirmation_required')
    if not task['timezone_confirmed']:
        fail('timezone_confirmation_required')
    # Explicit RFC3339 offset disambiguates folds and rejects nonexistent local time.
    if 'due_at' in supplied and due.utcoffset() != due.astimezone(ZoneInfo(zone)).utcoffset():
        fail('invalid_time_offset')
    if task['notification_override_at'] is not None and task['remind_before_minutes'] is None:
        fail('invalid_schedule')
    when = notify_at(task)
    if (creating or schedule_changed) and when is not None and when <= datetime.now(timezone.utc):
        fail('notification_in_past')


class PlannerStore:
    async def get_goal(self, connection, owner, identifier):
        row = await connection.fetchrow('SELECT * FROM planner_goals WHERE owner=$1 AND id=$2', owner, identifier)
        return {'goal': goal_document(row)} if row else None

    async def get_task(self, connection, owner, identifier):
        row = await connection.fetchrow('SELECT * FROM planner_tasks WHERE owner=$1 AND id=$2', owner, identifier)
        return {'task': task_document(row)} if row else None

    async def require_goal(self, connection, owner, identifier):
        if identifier is not None and not await self.get_goal(connection, owner, identifier):
            fail('not_found', 404)

    async def list_goals(self, connection, owner, limit, after):
        rows = await connection.fetch('''SELECT * FROM planner_goals WHERE owner=$1
            AND ($2::uuid IS NULL OR id>$2) ORDER BY id LIMIT $3''', owner, after, limit+1)
        return dict(goals=[goal_document(r) for r in rows[:limit]],
                    next_after=str(rows[limit-1]['id']) if len(rows)>limit else None)

    async def list_tasks(self, connection, owner, limit, after, view, goal_id, as_of, calendar_date=None):
        await self.require_goal(connection, owner, goal_id)
        as_of = as_of or await connection.fetchval('SELECT now()')
        calendar_date = date.fromisoformat(calendar_date) if calendar_date else as_of.astimezone(timezone.utc).date()
        condition = {
            None: 'TRUE', 'upcoming': "status<>'done' AND (due_at>=$4 OR due_date>=$6::date)",
            'overdue': "status<>'done' AND (due_at<$4 OR due_date<$6::date)",
            'undated': "status<>'done' AND due_at IS NULL AND due_date IS NULL",
            'completed': "status='done'",
        }[view]
        rows = await connection.fetch('''SELECT * FROM planner_tasks WHERE owner=$1
            AND ($2::uuid IS NULL OR id>$2) AND ($3::uuid IS NULL OR goal_id=$3)
            AND ($4::timestamptz IS NOT NULL) AND ($6::date IS NOT NULL) AND (''' + condition + ''') ORDER BY id LIMIT $5''',
            owner, after, goal_id, as_of, limit+1, calendar_date)
        return dict(tasks=[task_document(r) for r in rows[:limit]], as_of=serial(as_of),
                    next_after=str(rows[limit-1]['id']) if len(rows)>limit else None)

    async def create_goal(self, connection, owner, identifier, title, key):
        # Exact K0 hash/operation retained for persisted receipts across upgrade.
        return await self.mutate(connection, owner, 'create_goal', dict(id=identifier, title=title), key)

    async def mutate(self, connection, owner, operation, body, key, identifier=None):
        digest = request_hash(body if identifier is None else dict(id=identifier, body=body))
        epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
        receipt = await connection.fetchrow('''SELECT operation,request_hash,response FROM planner_operations
            WHERE owner=$1 AND account_epoch=$2 AND key=$3''', owner, epoch, key)
        if receipt:
            if receipt['operation'] != operation or receipt['request_hash'] != digest:
                fail('idempotency_conflict', 409)
            return json.loads(receipt['response'])
        metadata = await self._resolve_metadata(connection, operation, body, owner, epoch)
        if operation == 'create_goal':
            if await connection.fetchval("SELECT to_regclass('planner_proposal_policies')") and await connection.fetchval(
                    'SELECT 1 FROM planner_proposal_policies WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3',
                    owner, epoch, body['id']):
                fail('reserved_plan', 409)
            response = await self._create_goal(connection, owner, body)
        elif operation == 'create_task':
            response = {'task': await self._create_task(connection, owner, epoch, body, metadata.get('links', {}).get(str(body['id'])))}
        elif operation == 'commit':
            if await self.get_goal(connection, owner, body['goal']['id']):
                fail('id_conflict', 409)
            response = await self._create_goal(connection, owner, body['goal'], metadata.get('context'))
            response['tasks'] = []
            for plan_order, task in enumerate(metadata.get('tasks', body['tasks'])):
                if await self.get_task(connection, owner, task['id']):
                    fail('id_conflict', 409)
                response['tasks'].append(await self._create_task(connection, owner, epoch,
                    {**task, 'goal_id': body['goal']['id']}, metadata.get('links', {}).get(str(task['id'])), plan_order=plan_order))
        elif operation == 'edit_plan':
            response = await self._edit_plan(connection, owner, epoch, identifier, body)
        elif operation in ('update_goal', 'delete_goal'):
            response = await self._change_goal(connection, owner, identifier, body, operation=='delete_goal')
        elif operation in ('update_task', 'delete_task'):
            response = await self._change_task(connection, owner, identifier, body, operation=='delete_task')
        else:
            raise ValueError('Unknown planner operation')
        if operation in ('commit', 'edit_plan'):
            await self._clear_draft(connection, owner)
        await connection.execute('''INSERT INTO planner_operations
            (owner,account_epoch,key,operation,request_hash,http_status,response)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)''', owner, epoch, key, operation, digest,
            201 if operation in ('create_goal', 'create_task', 'commit') else 200,
            json.dumps(response, ensure_ascii=False))
        return response

    async def _resolve_metadata(self, connection, operation, body, owner=None, epoch=0):
        context = body.get('context') if operation == 'commit' else None
        tasks = body.get('tasks', []) if operation == 'commit' else [body] if operation == 'create_task' else []
        required = []
        if context:
            from project.llm.services.planner_policy import verify_policy
            try:
                required = verify_policy(owner, epoch, context)
            except ValueError:
                fail('invalid_policy')
        registry = None
        if operation == 'commit' and await connection.fetchval("SELECT to_regclass('planner_proposal_policies')"):
            registry = await connection.fetchrow('''SELECT * FROM planner_proposal_policies
                WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3''', owner, epoch, body['goal']['id'])
        if registry:
            if registry['expires_at'] <= datetime.now(timezone.utc):
                fail('policy_expired', 409)
            authoritative = json_value(registry['required_resources'], [])
            if context and (context.get('policy') is not None or context.get('required_resources')):
                if required != authoritative or context['question'] != registry['question']:
                    fail('invalid_policy')
            required = authoritative
            context = {**(context or {}), 'question': registry['question'], 'required_resources': required}
            context.setdefault('material_ids', [])
        if required:
            await self._require_resource_schema(connection)
            tasks = self._required_tasks(body['goal']['id'], tasks, required)
        if not context and not any(task.get('material_ids') for task in tasks):
            return {}
        ready = await connection.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='planner_goals' AND column_name='context'")
        if not ready:
            fail('planner_migration_required', 409)
        publication = await connection.fetchrow("""SELECT v.id,v.config FROM scenario_state s
            JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1 FOR SHARE OF s""")
        if registry and context and 'revision' not in context and publication:
            context['revision'] = publication['id']
        if not publication or (context and context['revision'] != publication['id']):
            fail('materials_changed', 409)
        from project.llm.services.planner_content import build_information, material_links
        config = json_value(publication['config'])
        try:
            result = {'tasks': tasks, 'links': {str(task['id']): material_links(config, task.get('material_ids', [])) for task in tasks}}
            if context:
                result['context'] = {**context, 'information': build_information(config, context['material_ids'], resources=required)}
        except ValueError:
            fail('invalid_materials')
        return result

    @staticmethod
    async def _require_resource_schema(connection):
        ready = await connection.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='planner_tasks' AND column_name='required_resource'")
        if not ready:
            fail('planner_migration_required', 409)

    @staticmethod
    def _required_tasks(goal_id, tasks, resources):
        from project.llm.services.planner_content import SEARCHES
        result = [dict(task) for task in tasks]
        for resource in resources:
            title = SEARCHES[resource][0]
            matching = next((task for task in result if task.get('required_resource') == resource
                or (task['title'] == title and task.get('material_ids', []) == [resource])), None)
            if matching is None:
                matching = dict(id=uuid5(UUID(str(goal_id)), resource), title=title,
                    material_ids=[resource], status='not_started', due_at=None,
                    timezone=None, timezone_confirmed=False, remind_before_minutes=None)
                if any(str(task['id']) == str(matching['id']) for task in result):
                    fail('id_conflict', 409)
                result.append(matching)
            matching['required_resource'] = resource
        if len(result) > 50:
            fail('task_limit')
        return result

    @staticmethod
    async def _clear_draft(connection, owner):
        if await connection.fetchval("SELECT to_regclass('planner_drafts')"):
            await connection.execute('DELETE FROM planner_drafts WHERE owner=$1', owner)

    async def _edit_plan(self, connection, owner, epoch, identifier, body):
        goal = await connection.fetchrow('SELECT * FROM planner_goals WHERE owner=$1 AND id=$2 FOR UPDATE', owner, identifier)
        self._check_version(goal, body)
        rows = await connection.fetch('SELECT * FROM planner_tasks WHERE owner=$1 AND goal_id=$2 FOR UPDATE', owner, identifier)
        current = {str(row['id']): row for row in rows}
        submitted = {str(task['id']): task for task in body['tasks']}
        removed = {str(task['id']): task for task in body['removed_tasks']}
        # Every current task must be explicitly retained or removed. A stale editor
        # must never silently delete a task added in another tab.
        if set(current) != ({key for key in submitted if key in current} | set(removed)):
            fail('stale_version', 409)
        for key, row in current.items():
            task = submitted.get(key, removed.get(key))
            if task.get('expected_version') is None:
                fail('stale_version', 409)
            self._check_version(row, task)
            if row.get('required_resource') and (key in removed or
                    task['title'] != row['title'] or task.get('material_ids', []) != json_value(row.get('material_ids'), [])):
                fail('required_task_locked', 409)
        for key, task in submitted.items():
            if key not in current:
                if task.get('expected_version') is not None:
                    fail('not_found', 404)
                if await self.get_task(connection, owner, task['id']):
                    fail('id_conflict', 409)
        context = json_value(goal.get('context'), {})
        # Stored context is authoritative even when an editor omits all resources.
        required = context.get('required_resources', [])
        if required:
            await self._require_resource_schema(connection)
        trusted_tasks = [{**task, **({'required_resource': current[str(task['id'])]['required_resource']}
            if str(task['id']) in current and current[str(task['id'])].get('required_resource') else {})}
            for task in body['tasks']]
        tasks = self._required_tasks(identifier, trusted_tasks, required)
        # Link snapshots remain unchanged for retained, unmodified materials.
        needs_links = [task for task in tasks if str(task['id']) not in current or
            task.get('material_ids', []) != json_value(current[str(task['id'])].get('material_ids'), [])]
        metadata = {}
        if any(task.get('material_ids') for task in needs_links):
            publication = await connection.fetchrow("""SELECT v.config FROM scenario_state s
                JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1 FOR SHARE OF s""")
            if not publication:
                fail('materials_changed', 409)
            from project.llm.services.planner_content import material_links
            try:
                metadata = {str(task['id']): material_links(json_value(publication['config']), task.get('material_ids', []))
                            for task in needs_links}
            except ValueError:
                fail('invalid_materials')
        for key, task in removed.items():
            await self._change_task(connection, owner, UUID(key), task, True)
        output = []
        for plan_order, task in enumerate(tasks):
            key = str(task['id'])
            if key in current:
                changes = {k: v for k, v in task.items() if k in TASK_FIELDS and k != 'goal_id'}
                changes['expected_version'] = task['expected_version']
                changes['plan_order'] = plan_order
                if task.get('material_ids', []) != json_value(current[key].get('material_ids'), []):
                    changes['material_ids'] = task.get('material_ids', [])
                    changes['material_links'] = metadata.get(key, [])
                document = (await self._change_task(connection, owner, task['id'], changes, False))['task']
                if task.get('required_resource') and not current[key].get('required_resource'):
                    await connection.execute('UPDATE planner_tasks SET required_resource=$3 WHERE owner=$1 AND id=$2',
                        owner, task['id'], task['required_resource'])
                    document = (await self.get_task(connection, owner, task['id']))['task']
                output.append(document)
            else:
                data = {k: v for k, v in task.items() if k != 'expected_version'}
                output.append(await self._create_task(connection, owner, epoch,
                    {**data, 'goal_id': identifier}, metadata.get(key, []), plan_order=plan_order))
        await connection.execute('UPDATE planner_goals SET title=$3,version=version+1,updated_at=now() WHERE owner=$1 AND id=$2',
            owner, identifier, body['title'])
        return {**await self.get_goal(connection, owner, identifier), 'tasks': output}

    async def _create_goal(self, connection, owner, body, context=None):
        existing = await self.get_goal(connection, owner, body['id'])
        if existing:
            if existing['goal']['title'] != body['title']:
                fail('id_conflict', 409)
            return existing
        await connection.execute('INSERT INTO planner_goals(owner,id,title) VALUES($1,$2,$3)',
                                 owner, body['id'], body['title'])
        if context is not None:
            await connection.execute('UPDATE planner_goals SET context=$3::jsonb WHERE owner=$1 AND id=$2',
                                     owner, body['id'], json.dumps(context, ensure_ascii=False))
        return await self.get_goal(connection, owner, body['id'])

    async def _create_task(self, connection, owner, epoch, body, links=None, *, plan_order=None):
        digest = request_hash(body)
        existing = await connection.fetchrow('SELECT * FROM planner_tasks WHERE owner=$1 AND id=$2', owner, body['id'])
        if existing:
            if existing['create_hash'] != digest:
                fail('id_conflict', 409)
            return task_document(existing)
        await self.require_goal(connection, owner, body['goal_id'])
        if plan_order is None:
            plan_order = await connection.fetchval('''SELECT COALESCE(MAX(plan_order),-1)+1
                FROM planner_tasks WHERE owner=$1 AND goal_id IS NOT DISTINCT FROM $2::uuid''', owner, body['goal_id'])
        task = {'due_date': None, **body, 'plan_order': plan_order, 'notification_override_at': None, 'schedule_generation': 1,
                'version': 1, 'account_epoch': epoch}
        if task['status'] == 'done':
            task['remind_before_minutes'] = task['notification_override_at'] = None
        validate_schedule(task, body, creating=True)
        task['completed_at'] = datetime.now(timezone.utc) if task['status']=='done' else None
        columns = ('id', *TASK_FIELDS, 'schedule_generation', 'version', 'account_epoch', 'completed_at')
        values = [task[c] for c in columns]
        await connection.execute('INSERT INTO planner_tasks(owner,create_hash,' + ','.join(columns) + ') VALUES('
            + ','.join(f'${i}' for i in range(1,len(columns)+3)) + ')', owner, digest, *values)
        if body.get('material_ids'):
            await connection.execute('UPDATE planner_tasks SET material_ids=$3::jsonb,material_links=$4::jsonb WHERE owner=$1 AND id=$2',
                owner, body['id'], json.dumps(body['material_ids']), json.dumps(links, ensure_ascii=False))
        if body.get('required_resource'):
            await connection.execute('UPDATE planner_tasks SET required_resource=$3 WHERE owner=$1 AND id=$2',
                owner, body['id'], body['required_resource'])
        await self._schedule(connection, owner, task)
        return (await self.get_task(connection, owner, task['id']))['task']

    @staticmethod
    def _check_version(row, body):
        if row is None:
            fail('not_found', 404)
        if row['version'] != body['expected_version']:
            fail('stale_version', 409)

    async def _change_goal(self, connection, owner, identifier, body, deleting):
        row = await connection.fetchrow('SELECT * FROM planner_goals WHERE owner=$1 AND id=$2 FOR UPDATE', owner, identifier)
        self._check_version(row, body)
        if deleting:
            tasks = await connection.fetch('SELECT * FROM planner_tasks WHERE owner=$1 AND goal_id=$2 FOR UPDATE', owner, identifier)
            if any(not task.get('required_resource') for task in tasks):
                fail('goal_not_empty', 409)
            for task in tasks:
                await self._cancel(connection, owner, task['id'])
            if tasks:
                await connection.execute('DELETE FROM planner_tasks WHERE owner=$1 AND goal_id=$2', owner, identifier)
            await connection.execute('DELETE FROM planner_goals WHERE owner=$1 AND id=$2', owner, identifier)
            return dict(deleted=True, id=str(identifier))
        if row['title'] != body['title']:
            await connection.execute('''UPDATE planner_goals SET title=$3,version=version+1,updated_at=now()
                WHERE owner=$1 AND id=$2''', owner, identifier, body['title'])
        return await self.get_goal(connection, owner, identifier)

    async def _cancel(self, connection, owner, identifier):
        await connection.execute('''UPDATE planner_notifications SET state='cancelled',finished_at=now()
            WHERE owner=$1 AND task_id=$2 AND state IN ('pending','claimed')''', owner, identifier)

    async def _schedule(self, connection, owner, task):
        when = notify_at(task)
        if when is not None:
            # to_jsonb keeps the frozen K1-only schema compatible before003.
            stopped = await connection.fetchval("SELECT (to_jsonb(e)->>'planner_stopped')::boolean FROM account_epochs e WHERE owner=$1",owner)
            if stopped:
                fail('notifications_paused',409)
            await connection.execute('''INSERT INTO planner_notifications
                (owner,id,task_id,generation,task_version,account_epoch,notify_at)
                VALUES($1,$2,$3,$4,$5,$6,$7)''', owner, uuid4(), task['id'], task['schedule_generation'],
                task['version'], task['account_epoch'], when)

    async def _change_task(self, connection, owner, identifier, body, deleting):
        row = await connection.fetchrow('SELECT * FROM planner_tasks WHERE owner=$1 AND id=$2 FOR UPDATE', owner, identifier)
        self._check_version(row, body)
        if row.get('required_resource') and (deleting or any(
                key in body and body[key] != row[key] for key in ('title', 'goal_id'))
                or ('material_ids' in body and body['material_ids'] != json_value(row.get('material_ids'), []))):
            fail('required_task_locked', 409)
        if deleting:
            await self._cancel(connection, owner, identifier)
            await connection.execute('''UPDATE planner_tasks SET schedule_generation=schedule_generation+1
                WHERE owner=$1 AND id=$2''', owner, identifier)
            await connection.execute('DELETE FROM planner_tasks WHERE owner=$1 AND id=$2', owner, identifier)
            return dict(deleted=True, id=str(identifier))
        supplied = {k:v for k,v in body.items() if k != 'expected_version'}
        task = {**dict(row), **supplied}
        # A PATCH selecting one schedule mode switches the previous mode off.
        # Explicit contradictory values still reach validation and fail atomically.
        if supplied.get('due_date') is not None:
            for key, empty in (('due_at', None), ('timezone', None),
                    ('timezone_confirmed', False), ('remind_before_minutes', None),
                    ('notification_override_at', None)):
                if key not in supplied:
                    task[key] = empty
        if supplied.get('due_at') is not None and 'due_date' not in supplied:
            task['due_date'] = None
        await self.require_goal(connection, owner, task['goal_id'])
        if any(k in supplied for k in ('due_at','due_date','timezone','remind_before_minutes')) and 'notification_override_at' not in supplied:
            task['notification_override_at'] = None
        if task['status'] == 'done':
            task['remind_before_minutes'] = task['notification_override_at'] = None
        if isinstance(task.get('due_date'), str):
            if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', task['due_date']) is None:
                fail('invalid_schedule')
            try:
                task['due_date'] = date.fromisoformat(task['due_date'])
            except ValueError:
                fail('invalid_schedule')
        schedule_changed = (any(task[k] != row[k] for k in ('due_at','due_date','timezone','remind_before_minutes','notification_override_at'))
                            or (task['status'] != row['status'] and 'done' in (task['status'],row['status'])))
        validate_schedule(task, supplied, schedule_changed=schedule_changed)
        materials_changed = 'material_ids' in supplied and supplied['material_ids'] != json_value(row.get('material_ids'), [])
        if not any(task[k] != row[k] for k in TASK_FIELDS) and not materials_changed:
            return {'task': task_document(row)}
        task['version'] += 1
        if task['status'] != row['status']:
            task['completed_at'] = datetime.now(timezone.utc) if task['status']=='done' else None
        if schedule_changed:
            task['schedule_generation'] += 1
            await self._cancel(connection, owner, identifier)
        columns = (*TASK_FIELDS, 'version', 'schedule_generation', 'completed_at')
        await connection.execute('UPDATE planner_tasks SET ' + ','.join(f'{k}=${i}' for i,k in enumerate(columns,3))
            + ',updated_at=now() WHERE owner=$1 AND id=$2', owner, identifier, *(task[k] for k in columns))
        if materials_changed:
            await connection.execute('UPDATE planner_tasks SET material_ids=$3::jsonb,material_links=$4::jsonb WHERE owner=$1 AND id=$2',
                owner, identifier, json.dumps(task['material_ids']), json.dumps(task['material_links'], ensure_ascii=False))
        if schedule_changed:
            await self._schedule(connection, owner, task)
        else:
            await connection.execute('''UPDATE planner_notifications SET task_version=$3
                WHERE owner=$1 AND task_id=$2 AND state IN ('pending','claimed')''', owner, identifier, task['version'])
        return await self.get_task(connection, owner, identifier)

    async def delete_owner(self, connection, owner):
        await self._clear_draft(connection, owner)
        if await connection.fetchval("SELECT to_regclass('planner_resumes')"):
            await connection.execute('DELETE FROM planner_resumes WHERE owner=$1', owner)
        if await connection.fetchval("SELECT to_regclass('planner_proposal_policies')"):
            await connection.execute('DELETE FROM planner_proposal_policies WHERE owner=$1', owner)
        # Optional schema support survives feature-off and a compatible rollback.
        # Explicit task deletion first handles standalone tasks as well as goals.
        if await connection.fetchval("SELECT to_regclass('planner_tasks')"):
            await connection.execute('DELETE FROM planner_tasks WHERE owner=$1', owner)
        if await connection.fetchval("SELECT to_regclass('planner_goals')"):
            await connection.execute('DELETE FROM planner_operations WHERE owner=$1', owner)
            await connection.execute('DELETE FROM planner_goals WHERE owner=$1', owner)
