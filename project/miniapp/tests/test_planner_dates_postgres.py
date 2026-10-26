"""Calendar date scheduling stays separate from exact instants and reminders."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from project.miniapp.planner_api import CommitTask, UpdateTask
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS
from project.miniapp.tests.test_planner_k1_postgres import task, post, patch
from project.miniapp.tests.test_planner_time_validation import planner_rows


def test_strict_calendar_wire_dates():
    for value in ('2099-02-28', '2000-02-29', None):
        assert CommitTask(id=uuid4(), title='Шаг', due_date=value).due_date == value
    for value in ('2099-2-01', '2099-02-29', '2099-01-01T00:00:00Z', '', 4070908800, True):
        with pytest.raises(ValidationError):
            CommitTask(id=uuid4(), title='Шаг', due_date=value)
        with pytest.raises(ValidationError):
            UpdateTask(expected_version=1, due_date=value)


@pytest.mark.asyncio
async def test_date_create_switch_clear_and_receipt(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    body = task(due_date='2099-02-28')
    created = await post(client, headers, 'tasks', body, 'date-create')
    assert created.status_code == 201, created.text
    date_task = created.json()['task']
    assert date_task['due_date'] == '2099-02-28'
    assert date_task['due_at'] is date_task['notify_at'] is None
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications') == 0
    assert (await post(client, headers, 'tasks', body, 'date-create')).json() == created.json()
    exact = await patch(client, headers, date_task['id'], dict(expected_version=1,
        due_at='2099-03-01T12:00:00+03:00', timezone='Europe/Moscow',
        timezone_confirmed=True, remind_before_minutes=60))
    assert exact.status_code == 200, exact.text
    assert exact.json()['task']['due_date'] is None
    assert await pool.fetchval("SELECT count(*) FROM planner_notifications WHERE state='pending'") == 1
    switched = await patch(client, headers, date_task['id'], dict(expected_version=2, due_date='2099-03-02'))
    assert switched.status_code == 200, switched.text
    result = switched.json()['task']
    assert result['due_date'] == '2099-03-02'
    assert all(result[k] is None for k in ('due_at','timezone','remind_before_minutes','notification_override_at','notify_at'))
    assert result['timezone_confirmed'] is False
    assert await pool.fetchval("SELECT count(*) FROM planner_notifications WHERE state='pending'") == 0
    cleared = await patch(client, headers, date_task['id'], dict(expected_version=3, due_date=None))
    assert cleared.status_code == 200 and cleared.json()['task']['due_date'] is None


@pytest.mark.asyncio
async def test_conflicting_date_aggregate_atomicity_and_views(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    body = dict(goal=dict(id=str(uuid4()), title='План'), confirmed=True,
        tasks=[task(due_date='2099-01-01'), task(due_date='2099-01-02')])
    created = await post(client, headers, 'commit', body)
    assert created.status_code == 201, created.text
    saved = created.json()
    for view, expected in [('upcoming', 2), ('overdue', 0), ('undated', 0)]:
        response = await client.get('/api/planner/tasks', headers=headers,
            params=dict(view=view, as_of='2099-01-01T23:59:59Z'))
        assert response.status_code == 200 and len(response.json()['tasks']) == expected
    before = await planner_rows(pool)
    edited = [dict(id=t['id'], title=t['title'], expected_version=t['version'], due_date=t['due_date']) for t in saved['tasks']]
    edited[0]['title'] = 'Правка'
    edited[1].update(due_at='2099-01-02T12:00:00Z', timezone='UTC', timezone_confirmed=True)
    rejected = await post(client, headers, f"goals/{saved['goal']['id']}/edit-plan", dict(
        expected_version=1, title='Правка плана', tasks=edited, confirmed=True), 'bad-date-edit')
    assert rejected.status_code == 422
    assert await planner_rows(pool) == before
    edited[1].pop('due_at'); edited[1].pop('timezone'); edited[1].pop('timezone_confirmed')
    accepted = await post(client, headers, f"goals/{saved['goal']['id']}/edit-plan", dict(
        expected_version=1, title='Правка плана', tasks=edited, confirmed=True), 'good-date-edit')
    assert accepted.status_code == 200, accepted.text
    assert [t['due_date'] for t in accepted.json()['tasks']] == ['2099-01-01','2099-01-02']
    for conflict in (dict(due_date='2099-01-01', remind_before_minutes=0),
            dict(due_date='2099-01-01', due_at='2099-01-01T12:00:00Z', timezone='UTC', timezone_confirmed=True)):
        before = await planner_rows(pool)
        assert (await post(client, headers, 'tasks', task(**conflict))).status_code == 422
        assert await planner_rows(pool) == before


def test_date_field_preserves_legacy_hash_and_patch_null():
    from project.miniapp.planner_api import CreateTask, mutation_body
    from project.llm.services.planner_store import request_hash
    old = task()
    parsed = mutation_body(CreateTask.model_validate(old), 'create_task')
    assert 'due_date' not in parsed
    # Existing defaults are unchanged; date-less create receipts retain hashes.
    assert request_hash(parsed) == request_hash({**old, 'goal_id': None,
        'status': 'not_started', 'due_at': None, 'timezone': None,
        'timezone_confirmed': False, 'remind_before_minutes': None})
    assert mutation_body(UpdateTask(expected_version=1, due_date=None), 'update_task') == dict(expected_version=1, due_date=None)
    from project.miniapp.planner_api import EditPlan
    edit = dict(expected_version=1, title='План', confirmed=True,
        tasks=[{**old, 'expected_version': 1}])
    assert 'due_date' not in mutation_body(EditPlan.model_validate(edit), 'edit_plan')['tasks'][0]
    edit['tasks'][0]['due_date'] = None
    assert mutation_body(EditPlan.model_validate(edit), 'edit_plan')['tasks'][0]['due_date'] is None


@pytest.mark.asyncio
async def test_date_noop_completed_owner_and_database_constraints(contour):
    import asyncpg
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    created = (await post(client, a, 'tasks', task(due_date='2000-02-29'))).json()['task']
    unchanged = await patch(client, a, created['id'], dict(expected_version=1, due_date='2000-02-29'))
    assert unchanged.status_code == 200 and unchanged.json()['task'] == created
    assert (await client.get('/api/planner/tasks/'+created['id'], headers=b)).status_code == 404
    completed = await patch(client, a, created['id'], dict(expected_version=1, status='done'))
    assert completed.status_code == 200 and completed.json()['task']['due_date'] == '2000-02-29'
    assert completed.json()['task']['notify_at'] is None
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications') == 0
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE planner_tasks SET due_at='2099-01-01T12:00:00Z',timezone='UTC',timezone_confirmed=true")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute('UPDATE planner_tasks SET remind_before_minutes=0')


@pytest.mark.asyncio
async def test_aggregate_edit_explicit_null_clears_date_and_replays(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    saved = (await post(client, headers, 'commit', dict(
        goal=dict(id=str(uuid4()), title='План'), confirmed=True,
        tasks=[task(due_date='2099-02-28')]))).json()
    original = saved['tasks'][0]
    body = dict(expected_version=1, title='План', confirmed=True, tasks=[dict(
        id=original['id'], title=original['title'], expected_version=1, due_date=None)])
    path = f"goals/{saved['goal']['id']}/edit-plan"
    response = await post(client, headers, path, body, 'clear-aggregate-date')
    assert response.status_code == 200, response.text
    cleared = response.json()['tasks'][0]
    assert cleared['due_date'] is None
    assert cleared['due_at'] is cleared['notify_at'] is None
    assert cleared['version'] == 2
    assert (await post(client, headers, path, body, 'clear-aggregate-date')).json() == response.json()
    persisted = await client.get('/api/planner/tasks/'+original['id'], headers=headers)
    assert persisted.json()['task'] == cleared
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications') == 0


@pytest.mark.asyncio
async def test_plan_order_survives_uuid_listing_and_aggregate_reorder(contour):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    # Deliberately submit reverse UUID order; reload consumers use plan_order.
    high, low = 'ffffffff-ffff-4fff-afff-ffffffffffff', '00000000-0000-4000-a000-000000000001'
    saved = (await post(client, headers, 'commit', dict(
        goal=dict(id=str(uuid4()), title='План'), confirmed=True,
        tasks=[dict(id=high, title='Первый'), dict(id=low, title='Второй')]))).json()
    assert [t['plan_order'] for t in saved['tasks']] == [0, 1]
    params = dict(goal_id=saved['goal']['id'], limit=1)
    first = (await client.get('/api/planner/tasks', headers=headers, params=params)).json()
    second = (await client.get('/api/planner/tasks', headers=headers,
        params={**params, 'after': first['next_after']})).json()
    loaded = first['tasks'] + second['tasks']
    assert [t['id'] for t in sorted(loaded, key=lambda t: t['plan_order'])] == [high, low]
    reordered = [dict(id=t['id'], title=t['title'], expected_version=t['version']) for t in reversed(saved['tasks'])]
    result = await post(client, headers, f"goals/{saved['goal']['id']}/edit-plan", dict(
        expected_version=1, title='План', confirmed=True, tasks=reordered))
    assert result.status_code == 200, result.text
    assert [(t['id'],t['plan_order']) for t in result.json()['tasks']] == [(low,0),(high,1)]
    added = (await post(client, headers, 'tasks', task(goal_id=saved['goal']['id']))).json()['task']
    assert added['plan_order'] == 2
    assert (await patch(client, headers, added['id'], dict(expected_version=1, plan_order=0))).status_code == 422


@pytest.mark.asyncio
async def test_date_filtered_views_use_explicit_phone_calendar(contour):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    await post(client, headers, 'tasks', task(due_date='2099-01-01'))
    # Phone is already Jan2 while UTC still Jan1.
    params = dict(as_of='2099-01-01T23:00:00Z', calendar_date='2099-01-02')
    overdue = await client.get('/api/planner/tasks', headers=headers, params={**params, 'view':'overdue'})
    assert overdue.status_code == 200 and len(overdue.json()['tasks']) == 1
    upcoming = await client.get('/api/planner/tasks', headers=headers, params={**params, 'view':'upcoming'})
    assert upcoming.status_code == 200 and upcoming.json()['tasks'] == []
    for invalid in ('2099-1-2','2099-02-29','2099-01-02T00:00:00Z'):
        assert (await client.get('/api/planner/tasks', headers=headers,
            params={**params, 'calendar_date':invalid})).status_code == 422
