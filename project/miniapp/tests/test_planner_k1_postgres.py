"""K1 integration on the dedicated disposable PostgreSQL, signed synthetic owners.

No MAX transport/model/real profile is used. Every fixture owns a unique schema.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, create, OWNERS, web
from project.llm.services.planner_store import apply_migration, MIGRATION, MIGRATIONS


def task(**changes):
    return {'id': str(uuid4()), 'title': 'Синтетическое дело', **changes}


def scheduled(**changes):
    return task(due_at='2099-02-01T00:30:00+10:00', timezone='Asia/Vladivostok',
                timezone_confirmed=True, remind_before_minutes=60, **changes)


async def post(client, headers, path, body, key=None):
    return await client.post('/api/planner/' + path, json=body,
                             headers={**headers, 'Idempotency-Key': key or uuid4().hex})


async def patch(client, headers, identifier, body, key=None, kind='tasks'):
    return await client.patch('/api/planner/' + kind + '/' + identifier, json=body,
                              headers={**headers, 'Idempotency-Key': key or uuid4().hex})


async def delete(client, headers, identifier, version=1, key=None, kind='tasks'):
    return await client.request('DELETE', '/api/planner/' + kind + '/' + identifier,
        json={'expected_version': version},
        headers={**headers, 'Idempotency-Key': key or uuid4().hex})


def code(response):
    return response.json()['detail']['code']


@pytest.mark.asyncio
async def test_commit_crud_lists_foreign_ids_and_sql_composite_links(contour):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    goal = dict(id=str(uuid4()), title='  Моя цель  ')
    t = scheduled()
    plan = dict(goal=goal, tasks=[t, task()], confirmed=True)
    response = await post(client, a, 'commit', plan, 'plan-1')
    assert response.status_code == 201
    result = response.json()
    assert result['goal']['title'] == goal['title'].strip()
    assert len(result['tasks']) == 2
    first = result['tasks'][0]
    assert first['goal_id'] == goal['id']
    assert first['due_at'].startswith('2099-01-31T14:30:00')
    assert first['notify_at'].startswith('2099-01-31T13:30:00')
    assert first['version'] == first['schedule_generation'] == 1
    assert 'owner' not in first
    path = '/api/planner/tasks/' + t['id']
    assert (await client.get(path, headers=a)).json() == {'task': first}
    assert (await client.get(path, headers=b)).status_code == 404
    assert (await patch(client, b, t['id'], dict(expected_version=1, status='done'))).status_code == 404
    assert (await delete(client, b, t['id'])).status_code == 404
    assert (await post(client, b, 'tasks', task(goal_id=goal['id']))).status_code == 404
    assert (await post(client, a, 'tasks', task(goal_id=str(uuid4())))).status_code == 404
    other = task()
    standalone = await post(client, b, 'tasks', other)
    assert standalone.status_code == 201 and standalone.json()['task']['goal_id'] is None
    assert (await patch(client, a, other['id'], dict(expected_version=1, goal_id=goal['id']))).status_code == 404
    assert (await client.get('/api/planner/goals', headers=a)).json()['goals'] == [result['goal']]
    listing = (await client.get('/api/planner/tasks', params={'goal_id': goal['id']}, headers=a)).json()
    assert {row['id'] for row in listing['tasks']} == {row['id'] for row in result['tasks']}
    assert (await client.get('/api/planner/tasks', params={'goal_id': goal['id']}, headers=b)).status_code == 404
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute('''INSERT INTO planner_tasks(owner,id,goal_id,title)
            VALUES($1,$2,$3,'foreign SQL')''', str(OWNERS[1]), uuid4(), UUID(goal['id']))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute('''INSERT INTO planner_notifications
            (owner,id,task_id,generation,task_version,notify_at,account_epoch)
            VALUES($1,$2,$3,1,1,now(),0)''', str(OWNERS[1]), uuid4(), UUID(t['id']))
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications') == 1


@pytest.mark.asyncio
async def test_atomic_commit_sql_row_and_receipt_failures_roll_back_every_row(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    await pool.execute('''CREATE FUNCTION reject_task() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.title='SQL reject' THEN RAISE check_violation USING MESSAGE='row rejected';
        END IF; RETURN NEW; END $$;
        CREATE TRIGGER reject_task BEFORE INSERT ON planner_tasks
        FOR EACH ROW EXECUTE FUNCTION reject_task();''')
    body = dict(goal=dict(id=str(uuid4()), title='Атомарно'),
                tasks=[scheduled(), task(title='SQL reject')], confirmed=True)
    with pytest.raises(asyncpg.CheckViolationError, match='row rejected'):
        await post(client, a, 'commit', body)
    for table in ('planner_goals', 'planner_tasks', 'planner_notifications', 'planner_operations'):
        assert await pool.fetchval('SELECT count(*) FROM ' + table) == 0
    await pool.execute('DROP TRIGGER reject_task ON planner_tasks')
    await pool.execute('''CREATE FUNCTION reject_op() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE check_violation USING MESSAGE='receipt rejected'; END $$;
        CREATE TRIGGER reject_op BEFORE INSERT ON planner_operations
        FOR EACH ROW EXECUTE FUNCTION reject_op();''')
    with pytest.raises(asyncpg.CheckViolationError, match='receipt rejected'):
        await post(client, a, 'commit', body)
    for table in ('planner_goals', 'planner_tasks', 'planner_notifications', 'planner_operations'):
        assert await pool.fetchval('SELECT count(*) FROM ' + table) == 0
    await pool.execute('DROP TRIGGER reject_op ON planner_operations')
    success = await post(client, a, 'commit', body, 'atomic')
    assert success.status_code == 201
    assert (await post(client, a, 'commit', body, 'atomic')).json() == success.json()
    assert (await post(client, a, 'commit', body)).status_code == 409
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks') == 2


@pytest.mark.asyncio
async def test_durable_task_retry_new_bearer_process_conflicts_and_versions(contour):
    import os
    import subprocess
    import sys
    pool, client, schema = contour
    a = await launch(client, OWNERS[0])
    body = scheduled()
    original = await post(client, a, 'tasks', body, 'task-retry')
    assert original.status_code == 201
    repeated = await asyncio.gather(*(post(client, a, 'tasks', body, 'task-retry') for _ in range(3)))
    assert all(row.status_code == 201 and row.json() == original.json() for row in repeated)
    await pool.execute("UPDATE miniapp_launches SET launched_at=now()-interval '6 seconds'")
    fresh = await launch(client, OWNERS[0])
    assert fresh != a
    assert (await post(client, fresh, 'tasks', body, 'task-retry')).json() == original.json()
    script = '''
import asyncio, importlib, json, os, sys
from types import SimpleNamespace
import asyncpg, httpx
from project.miniapp.session_store import SessionStore
async def main(d):
    web=importlib.import_module('project.miniapp.app')
    pool=await asyncpg.create_pool(os.environ['PLANNER_TEST_DSN'],min_size=1,max_size=2,
        server_settings={'search_path':d['schema']})
    try:
        web.app.state.preview=False; web.app.state.origin='https://planner.test'
        web.app.state.shared=SessionStore(pool); web.app.state.store=SimpleNamespace(pool=pool)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url='https://planner.test') as c:
            read=await c.get('/api/planner/tasks/'+d['body']['id'],headers=d['headers'])
            retry=await c.post('/api/planner/tasks',json=d['body'],headers={**d['headers'],'Idempotency-Key':'task-retry'})
            assert read.status_code==200 and retry.status_code==201
            assert read.json()==retry.json()==d['expected']
        print('k1-restart-ok')
    finally: await pool.close()
asyncio.run(main(json.load(sys.stdin)))
'''
    process = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script],
        input=json.dumps(dict(schema=schema, body=body, headers=fresh, expected=original.json())),
        text=True, capture_output=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == 'k1-restart-ok'
    conflict = await post(client, fresh, 'tasks', {**body, 'title': 'Изменено'}, 'task-retry')
    assert conflict.status_code == 409 and code(conflict) == 'idempotency_conflict'
    changed = await patch(client, fresh, body['id'], dict(expected_version=1, title='Новое'), 'update')
    assert changed.status_code == 200 and changed.json()['task']['version'] == 2
    assert (await patch(client, fresh, body['id'], dict(expected_version=1, title='Новое'), 'update')).json() == changed.json()
    stale = await patch(client, fresh, body['id'], dict(expected_version=1, status='done'))
    assert stale.status_code == 409 and code(stale) == 'stale_version'
    assert (await delete(client, fresh, body['id'], 1)).status_code == 409
    # Receipt replay is historical even after a later edit/delete, never a new write.
    assert (await post(client, fresh, 'tasks', body, 'task-retry')).json() == original.json()
    await pool.execute('DELETE FROM planner_operations WHERE owner=$1', str(OWNERS[0]))
    fallback = await post(client, fresh, 'tasks', body, 'lost-receipt')
    assert fallback.status_code == 201 and fallback.json() == changed.json()
    assert (await post(client, fresh, 'tasks', {**body, 'title': 'Другое'}, 'different')).status_code == 409
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks') == 1
    deleted = await delete(client, fresh, body['id'], 2, 'delete')
    assert deleted.status_code == 200
    assert (await delete(client, fresh, body['id'], 2, 'delete')).json() == deleted.json()
    assert (await post(client, fresh, 'tasks', body, 'lost-receipt')).json() == fallback.json()
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks') == 0


@pytest.mark.asyncio
async def test_schedule_generations_done_reopen_override_and_notification_history(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    body = scheduled()
    response = await post(client, a, 'tasks', body)
    assert response.status_code == 201
    row = response.json()['task']
    # claim is cancelled atomically when only notification time is moved.
    await pool.execute('''UPDATE planner_notifications SET state='claimed',claim_token=$1,
        lease_until=now()+interval '1 minute' ''', uuid4())
    moved = await patch(client, a, body['id'], dict(expected_version=1,
        notification_override_at='2099-02-02T00:00:00Z'))
    assert moved.status_code == 200
    row2 = moved.json()['task']
    assert row2['due_at'] == row['due_at'] and row2['schedule_generation'] == 2
    assert row2['notify_at'].startswith('2099-02-02T00:00:00')
    assert await pool.fetchval("SELECT count(*) FROM planner_notifications WHERE state='cancelled'") == 1
    due = await patch(client, a, body['id'], dict(expected_version=2,
        due_at='2099-02-03T00:30:00+10:00', timezone_confirmed=True))
    row3 = due.json()['task']
    assert due.status_code == 200 and row3['schedule_generation'] == 3
    assert row3['notification_override_at'] is None
    assert row3['notify_at'].startswith('2099-02-02T13:30:00')
    disabled = await patch(client, a, body['id'], dict(expected_version=3, remind_before_minutes=None))
    assert disabled.status_code == 200 and disabled.json()['task']['notify_at'] is None
    enabled = await patch(client, a, body['id'], dict(expected_version=4, remind_before_minutes=0))
    assert enabled.status_code == 200
    assert enabled.json()['task']['notify_at'] == enabled.json()['task']['due_at']
    done = await patch(client, a, body['id'], dict(expected_version=5, status='done'))
    assert done.status_code == 200
    complete = done.json()['task']
    assert complete['status'] == 'done' and complete['completed_at'] is not None
    assert complete['notify_at'] is complete['remind_before_minutes'] is None
    assert complete['schedule_generation'] == 6
    repeat = await patch(client, a, body['id'], dict(expected_version=6, status='done'))
    assert repeat.status_code == 200 and repeat.json() == done.json()
    reopened = await patch(client, a, body['id'], dict(expected_version=6, status='in_progress'))
    assert reopened.status_code == 200
    reopened_row = reopened.json()['task']
    assert reopened_row['schedule_generation'] == 7 and reopened_row['completed_at'] is None
    assert reopened_row['notify_at'] is reopened_row['remind_before_minutes'] is None
    assert await pool.fetchval("SELECT count(*) FROM planner_notifications WHERE state IN ('pending','claimed')") == 0
    again = await patch(client, a, body['id'], dict(expected_version=7, remind_before_minutes=60))
    assert again.status_code == 200 and again.json()['task']['schedule_generation'] == 8
    await patch(client, a, body['id'], dict(expected_version=8, title='Только название'))
    assert await pool.fetchval("SELECT task_version FROM planner_notifications WHERE state='pending'") == 9
    assert (await delete(client, a, body['id'], 9)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications') == 0


@pytest.mark.asyncio
async def test_time_confirmation_dst_past_reminder_and_strict_server_validation(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    base = scheduled()
    for replacement, expected in [
        ({'timezone': 'Unknown/Browser'}, 'invalid_timezone'),
        ({'timezone_confirmed': False}, 'timezone_confirmation_required'),
        ({'due_at': '2099-02-01T00:30:00+03:00'}, 'invalid_time_offset'),
        ({'due_at': '2001-01-01T10:00:00+10:00'}, 'notification_in_past'),
        ({'due_at': None}, 'invalid_schedule'),
    ]:
        r = await post(client, a, 'tasks', {**base, **replacement})
        assert r.status_code == 422 and code(r) == expected
    for replacement in ({'due_at': '2099-01-01T10:00:00'}, {'due_at': 12345},
        {'remind_before_minutes': 1440}, {'remind_before_minutes': True},
        {'title': ' '}, {'title': 42}, {'title': 'x'*241}, {'status': 'overdue'},
        {'owner': str(OWNERS[1])}, {'timezone_confirmed': 'true'}):
        assert (await post(client, a, 'tasks', {**base, **replacement})).status_code == 422
    gap = task(due_at='2099-03-08T02:30:00-05:00', timezone='America/New_York',
        timezone_confirmed=True)
    # Use a known transition year, no reminder required for historical examples.
    gap['due_at'] = '2026-03-08T02:30:00-05:00'
    assert code(await post(client, a, 'tasks', gap)) == 'invalid_time_offset'
    folds = []
    for offset in ('-04:00', '-05:00'):
        r = await post(client, a, 'tasks', task(due_at='2026-11-01T01:30:00'+offset,
            timezone='America/New_York', timezone_confirmed=True))
        assert r.status_code == 201
        folds.append(r.json()['task']['due_at'])
    assert datetime.fromisoformat(folds[1]) - datetime.fromisoformat(folds[0]) == timedelta(hours=1)
    zones = await client.get('/api/planner/timezones', headers=a)
    assert zones.status_code == 200
    assert {'Asia/Vladivostok', 'Europe/Moscow', 'America/New_York', 'UTC'} <= set(zones.json()['timezones'])
    no_date = await post(client, a, 'tasks', task())
    assert no_date.status_code == 201 and no_date.json()['task']['notify_at'] is None
    past = await post(client, a, 'tasks', task(due_at='2001-01-01T00:00:00Z',
        timezone='UTC', timezone_confirmed=True))
    assert past.status_code == 201
    r = await patch(client, a, past.json()['task']['id'], dict(expected_version=1, remind_before_minutes=0))
    assert r.status_code == 422 and code(r) == 'notification_in_past'
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications') == 0


@pytest.mark.asyncio
async def test_four_views_midnight_and_keyset_pagination(contour):
    _, client, _ = contour
    a = await launch(client, OWNERS[0])
    rows = [task(), task(status='done'),
        task(due_at='2035-01-01T23:59:59Z', timezone='UTC', timezone_confirmed=True),
        task(due_at='2035-01-02T00:00:00Z', timezone='UTC', timezone_confirmed=True)]
    for body in rows:
        assert (await post(client, a, 'tasks', body)).status_code == 201
    for view, index in [('undated', 0), ('completed', 1), ('overdue', 2), ('upcoming', 3)]:
        r = await client.get('/api/planner/tasks', params=dict(view=view, as_of='2035-01-02T00:00:00Z'), headers=a)
        assert r.status_code == 200 and [t['id'] for t in r.json()['tasks']] == [rows[index]['id']]
    first = (await client.get('/api/planner/tasks', params={'limit': 2}, headers=a)).json()
    second = (await client.get('/api/planner/tasks', params={'limit': 2, 'after': first['next_after'],
        'as_of': first['as_of']}, headers=a)).json()
    assert len(first['tasks']) == len(second['tasks']) == 2
    assert second['next_after'] is None
    assert [r['id'] for r in first['tasks'] + second['tasks']] == sorted(r['id'] for r in rows)
    for params in ({'limit': 101}, {'limit': 0}, {'after': 'bad'}, {'view': 'today'}, {'as_of': '2035-01-01'}):
        assert (await client.get('/api/planner/tasks', params=params, headers=a)).status_code == 422


@pytest.mark.asyncio
async def test_goal_edit_delete_explicit_consequences_and_manual_plan_bounds(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    g = dict(id=str(uuid4()), title='Пустая цель')
    committed = await post(client, a, 'commit', dict(goal=g, tasks=[], confirmed=True))
    assert committed.status_code == 201 and committed.json()['tasks'] == []
    updated = await patch(client, a, g['id'], dict(expected_version=1, title='Переименовано'), kind='goals')
    assert updated.status_code == 200 and updated.json()['goal']['version'] == 2
    t = task(goal_id=g['id'])
    assert (await post(client, a, 'tasks', t)).status_code == 201
    rejected = await delete(client, a, g['id'], 2, kind='goals')
    assert rejected.status_code == 409 and code(rejected) == 'goal_not_empty'
    assert (await patch(client, a, t['id'], dict(expected_version=1, goal_id=None))).status_code == 200
    assert (await delete(client, a, g['id'], 2, kind='goals')).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks') == 1
    large = dict(goal=dict(id=str(uuid4()), title='Ручной план'), tasks=[task() for _ in range(50)], confirmed=True)
    assert (await post(client, a, 'commit', large)).status_code == 201
    for body in ({**large, 'tasks': [task() for _ in range(51)]}, {**large, 'confirmed': False},
        {**large, 'tasks': [t, t]}, {**large, 'owner': str(OWNERS[1])}):
        assert (await post(client, a, 'commit', body)).status_code == 422


@pytest.mark.asyncio
async def test_patch_boundaries_and_account_delete_race_all_rows_flagoff(contour, monkeypatch):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    items = [scheduled(), scheduled()]
    for headers, body in zip((a, b), items):
        assert (await post(client, headers, 'tasks', body)).status_code == 201
    path = '/api/planner/tasks/' + items[0]['id']
    headers = {**a, 'Idempotency-Key': 'boundary'}
    assert (await client.patch(path, json=dict(expected_version=1, title='Изменено'),
        headers={**headers, 'Origin': 'https://foreign.invalid'})).status_code == 403
    assert (await client.patch(path, content='x'*24001, headers=headers)).status_code == 413
    assert (await client.patch(path, json=dict(expected_version=1, title='Изменено'))).status_code == 422
    assert (await patch(client, {'Authorization': 'Bearer forged'}, items[0]['id'], dict(expected_version=1, status='done'))).status_code == 401
    assert (await patch(client, a, items[0]['id'], dict(expected_version=1, owner=str(OWNERS[1])))).status_code == 422
    original = web.shared_owner
    captured, proceed = asyncio.Event(), asyncio.Event()
    async def delayed(request):
        state = await original(request)
        if request.method == 'PATCH':
            captured.set()
            await proceed.wait()
        return state
    monkeypatch.setattr(web, 'shared_owner', delayed)
    update = asyncio.create_task(patch(client, a, items[0]['id'], dict(expected_version=1, status='done')))
    await asyncio.wait_for(captured.wait(), 5)
    try:
        monkeypatch.setenv('PLANNER_ENABLED', '0')
        assert (await client.delete('/api/account', headers=a)).status_code == 200
    finally:
        monkeypatch.setenv('PLANNER_ENABLED', '1')
        proceed.set()
    assert (await asyncio.wait_for(update, 5)).status_code == 401
    for table in ('planner_goals', 'planner_tasks', 'planner_notifications', 'planner_operations'):
        assert await pool.fetchval('SELECT count(*) FROM '+table+' WHERE owner=$1', str(OWNERS[0])) == 0
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks WHERE owner=$1', str(OWNERS[1])) == 1
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications WHERE owner=$1', str(OWNERS[1])) == 1
    fresh = await launch(client, OWNERS[0])
    retry = await post(client, fresh, 'tasks', items[0], 'fresh-epoch')
    assert retry.status_code == 201
    assert await pool.fetchval('SELECT account_epoch FROM planner_notifications WHERE owner=$1', str(OWNERS[0])) == 1


@pytest.mark.asyncio
async def test_additive_upgrade_preserves_k0_and_old_product_checksum_replay(contour):
    pool, client, _ = contour
    # Construct the exact immutable K0 schema to test a genuine upgrade.
    await pool.execute('DROP TABLE IF EXISTS planner_notifications,planner_tasks,planner_drafts,planner_proposal_policies,planner_resumes')
    await pool.execute('DROP TABLE IF EXISTS housing_statuses,career_statuses')
    await pool.execute('DROP SEQUENCE IF EXISTS housing_status_versions,career_status_versions')
    await pool.execute("DELETE FROM schema_migrations WHERE version<>'001_planner'")
    await pool.execute('ALTER TABLE planner_goals DROP COLUMN IF EXISTS context')
    await pool.execute('ALTER TABLE account_epochs DROP COLUMN planner_stopped,DROP COLUMN planner_event_at')
    await pool.execute('ALTER TABLE planner_goals DROP COLUMN IF EXISTS create_hash')
    before = await pool.fetchrow('SELECT * FROM scenario_state WHERE id=1')
    a = await launch(client, OWNERS[0])
    goal = dict(id=str(uuid4()), title='Старые данные K0')
    created = await create(client, a, goal)
    assert created.status_code == 201
    goals_before = await pool.fetch('SELECT * FROM planner_goals')
    receipts_before = await pool.fetch('SELECT * FROM planner_operations')
    await apply_migration(pool)
    await apply_migration(pool)
    assert await pool.fetchval('SELECT count(*) FROM schema_migrations') == len(MIGRATIONS)
    assert [{k:v for k,v in dict(row).items() if k != 'context'} for row in await pool.fetch('SELECT * FROM planner_goals')] == [dict(row) for row in goals_before]
    assert await pool.fetch('SELECT * FROM planner_operations') == receipts_before
    assert (await create(client, a, goal)).json() == created.json()
    assert await pool.fetchrow('SELECT * FROM scenario_state WHERE id=1') == before
    assert await pool.fetchval("SELECT sha256 FROM schema_migrations WHERE version='001_planner'") == hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
    await pool.execute("UPDATE schema_migrations SET sha256='wrong' WHERE version='002_planner'")
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        await apply_migration(pool)


def test_frozen_openapi_has_meaningful_planner_schemas_security_and_equality():
    from tools.submission.export_openapi import contract
    document = contract()
    assert json.loads(Path('openapi.yaml').read_text()) == document
    paths = document['paths']
    for path, methods in {'/api/planner/goals': ('get','post'),
        '/api/planner/tasks': ('get','post'), '/api/planner/tasks/{identifier}': ('get','patch','delete'),
        '/api/planner/commit': ('post',)}.items():
        assert path in paths
        for method in methods:
            operation = paths[path][method]
            assert operation['security'] == [{'MaxSession': []}]
            assert {'401','404','422'} <= operation['responses'].keys()
            success = operation['responses']['201' if method=='post' else '200']
            assert success['content']['application/json']['schema'].get('$ref')
    schemas = document['components']['schemas']
    assert {'id','title','status','version','schedule_generation','notify_at'} <= set(schemas['TaskDocument']['required'])
    assert schemas['CommitPlan']['properties']['tasks']['maxItems'] == 50


@pytest.mark.asyncio
async def test_k0_receipt_wire_format_survives_schema_and_response_model_upgrade(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    goal = dict(id=str(uuid4()), title='Исходный формат K0')
    row = await pool.fetchrow('''INSERT INTO planner_goals(owner,id,title)
        VALUES($1,$2,$3) RETURNING *''', str(OWNERS[0]), UUID(goal['id']), goal['title'])
    original = {'goal': dict(id=goal['id'], title=goal['title'], version=1,
        created_at=row['created_at'].isoformat(), updated_at=row['updated_at'].isoformat())}
    canonical = json.dumps(goal, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    await pool.execute('''INSERT INTO planner_operations
        (owner,account_epoch,key,operation,request_hash,http_status,response)
        VALUES($1,0,'k0-wire','create_goal',$2,201,$3::jsonb)''', str(OWNERS[0]),
        hashlib.sha256(canonical.encode()).hexdigest(), json.dumps(original, ensure_ascii=False))
    retry = await create(client, a, goal, 'k0-wire')
    assert retry.status_code == 201 and retry.json() == original
    assert (await client.get('/api/planner/goals/'+goal['id'], headers=a)).json() == original


@pytest.mark.asyncio
async def test_sending_history_is_not_recanted_and_invalid_states_rejected_by_sql(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    body = scheduled()
    created = await post(client, a, 'tasks', body)
    assert created.status_code == 201
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE planner_notifications SET state='claimed'")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE planner_notifications SET state='delivered'")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE planner_tasks SET status='overdue'")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute('UPDATE planner_tasks SET remind_before_minutes=1440')
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute('''INSERT INTO planner_notifications
            (owner,id,task_id,generation,task_version,account_epoch,notify_at)
            SELECT owner,$1,task_id,generation,task_version,account_epoch,notify_at FROM planner_notifications''', uuid4())
    # Fake the already-started state entirely in SQL; there is no sender/HTTP.
    await pool.execute("UPDATE planner_notifications SET state='sending',claim_token=$1,sending_at=now()", uuid4())
    assert (await patch(client, a, body['id'], dict(expected_version=1, status='done'))).status_code == 200
    assert await pool.fetchval('SELECT state FROM planner_notifications') == 'sending'
    assert await pool.fetchval('SELECT generation FROM planner_notifications') == 1
    assert await pool.fetchval('SELECT schedule_generation FROM planner_tasks') == 2


@pytest.mark.asyncio
async def test_standalone_task_keeps_epoch_when_receipt_is_missing_during_session_cleanup(contour):
    pool, client, _ = contour
    owner = str(OWNERS[0])
    a = await launch(client, OWNERS[0])
    await pool.execute('''INSERT INTO account_epochs(owner,epoch) VALUES($1,7)
        ON CONFLICT(owner) DO UPDATE SET epoch=7''', owner)
    body = scheduled()
    assert (await post(client, a, 'tasks', body)).status_code == 201
    # Same missing-receipt recovery assumption as the stable UUID fallback tests.
    await pool.execute('DELETE FROM planner_operations WHERE owner=$1', owner)
    await pool.execute("UPDATE account_epochs SET changed_at=now()-interval '2 days'")
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '1 second'")
    await pool.execute("UPDATE miniapp_launches SET launched_at=now()-interval '6 seconds'")
    fresh = await launch(client, OWNERS[0])
    assert await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) == 7
    assert (await post(client, fresh, 'tasks', body, 'recovered')).status_code == 201
    assert await pool.fetchval('SELECT account_epoch FROM planner_operations WHERE owner=$1', owner) == 7
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks') == 1


@pytest.mark.asyncio
async def test_delete_waiting_for_inflight_write_removes_committed_rows_and_receipt(contour, monkeypatch):
    from project.miniapp.planner_api import store
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    body = scheduled()
    assert (await post(client, a, 'tasks', body)).status_code == 201
    acquired, allow_write, delete_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_change, original_owner = store._change_task, web.shared_owner
    async def held(*args, **kwargs):
        acquired.set()
        await allow_write.wait()
        return await original_change(*args, **kwargs)
    async def observed(request):
        # shared_owner touches the session row already held by the writer.
        # Signal before that lookup so the barrier cannot wait on itself.
        if request.url.path == '/api/account':
            delete_started.set()
        return await original_owner(request)
    monkeypatch.setattr(store, '_change_task', held)
    monkeypatch.setattr(web, 'shared_owner', observed)
    writing = asyncio.create_task(patch(client, a, body['id'], dict(expected_version=1, title='До удаления'), 'racing-write'))
    await asyncio.wait_for(acquired.wait(), 5)
    deleting = asyncio.create_task(client.delete('/api/account', headers=a))
    try:
        await asyncio.wait_for(delete_started.wait(), 5)
    finally:
        allow_write.set()
    results = await asyncio.wait_for(asyncio.gather(writing, deleting), 5)
    assert [r.status_code for r in results] == [200, 200]
    for table in ('planner_goals','planner_tasks','planner_notifications','planner_operations'):
        assert await pool.fetchval('SELECT count(*) FROM '+table) == 0
    assert (await post(client, a, 'tasks', body, 'racing-write')).status_code == 401


@pytest.mark.asyncio
async def test_past_notification_override_and_elapsed_pending_disable_are_atomic(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    body = scheduled()
    original = await post(client, a, 'tasks', body)
    assert original.status_code == 201
    rejected = await patch(client, a, body['id'], dict(expected_version=1,
        notification_override_at='2001-01-01T00:00:00Z'), 'past-override')
    assert rejected.status_code == 422 and code(rejected) == 'notification_in_past'
    assert (await client.get('/api/planner/tasks/'+body['id'], headers=a)).json() == original.json()
    assert await pool.fetchval("SELECT count(*) FROM planner_operations WHERE key='past-override'") == 0
    # Simulate time passing without waiting, only in this unique test schema.
    await pool.execute("UPDATE planner_tasks SET due_at='2001-01-01T00:00:00Z'")
    await pool.execute("UPDATE planner_notifications SET notify_at='2000-12-31T23:00:00Z'")
    title = await patch(client, a, body['id'], dict(expected_version=1, title='Уже прошло'))
    assert title.status_code == 200
    assert title.json()['task']['schedule_generation'] == 1
    off = await patch(client, a, body['id'], dict(expected_version=2, remind_before_minutes=None))
    assert off.status_code == 200 and off.json()['task']['notify_at'] is None
    assert await pool.fetchval('SELECT state FROM planner_notifications') == 'cancelled'
    refused = await patch(client, a, body['id'], dict(expected_version=3, remind_before_minutes=0))
    assert refused.status_code == 422 and code(refused) == 'notification_in_past'


@pytest.mark.asyncio
async def test_k0_only_schema_and_flagoff_keeps_account_deletion_compatible(contour, monkeypatch):
    pool, client, _ = contour
    await pool.execute('DROP TABLE planner_notifications,planner_tasks')
    await pool.execute("DELETE FROM schema_migrations WHERE version='002_planner'")
    a, b = [await launch(client, owner) for owner in OWNERS]
    goals = [dict(id=str(uuid4()), title='Старая цель '+str(i)) for i in range(2)]
    for headers, goal in zip((a,b), goals):
        assert (await create(client, headers, goal)).status_code == 201
    monkeypatch.setenv('PLANNER_ENABLED', '0')
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 1
    assert await pool.fetchval('SELECT owner FROM planner_goals') == str(OWNERS[1])
