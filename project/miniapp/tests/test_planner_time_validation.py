"""RFC3339 boundary regressions: real signed API and disposable PostgreSQL."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS
from project.miniapp.tests.test_planner_k1_postgres import task, post, patch

ENDPOINTS = ('create', 'commit', 'patch_due', 'patch_override', 'as_of')
TABLES = ('planner_goals', 'planner_tasks', 'planner_notifications', 'planner_operations')
INVALID_TIMES = (
    '4070908800',                    # epoch seconds string, previously coerced to UTC
    '4070908800000',                 # epoch milliseconds string
    '4070908800.0',                  # fractional numeric string
    '2099-01-01T00:00:00',           # no explicit offset
    '2099-01-01 00:00:00Z',          # ISO separator accepted by Pydantic, not RFC3339 T
    '2099-01-01T00:00Z',             # seconds missing
    '2099-01-01T00:00:00+0000',      # offset colon missing
    '2099-01-01T00:00:00Z trailing', # trailing data
    '2099-13-01T00:00:00Z',          # syntax shaped but calendar invalid
    'not-a-time',
)


async def planner_rows(pool):
    # Compare every persisted business/receipt field, including version/generation.
    return {table: await pool.fetch('SELECT * FROM ' + table + ' ORDER BY owner,' +
        ('account_epoch,key' if table == 'planner_operations' else 'id')) for table in TABLES}


async def send_time(client, headers, endpoint, value, *, identifier=None, version=1, zone='UTC'):
    dated = dict(due_at=value, timezone=zone, timezone_confirmed=True, remind_before_minutes=0)
    if endpoint == 'create':
        return await post(client, headers, 'tasks', task(**dated))
    if endpoint == 'commit':
        return await post(client, headers, 'commit', dict(
            goal=dict(id=str(uuid4()), title='Синтетический RFC3339 план'),
            tasks=[task(), task(**dated)], confirmed=True))
    if endpoint == 'patch_due':
        return await patch(client, headers, identifier, dict(expected_version=version,
            due_at=value, timezone=zone, timezone_confirmed=True))
    if endpoint == 'patch_override':
        return await patch(client, headers, identifier, dict(expected_version=version,
            notification_override_at=value))
    return await client.get('/api/planner/tasks', headers=headers, params={'as_of': value})


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ENDPOINTS)
async def test_all_time_inputs_reject_non_rfc3339_without_persisted_changes(contour, endpoint):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    identifier = None
    if endpoint in ('patch_due', 'patch_override', 'as_of'):
        created = await post(client, headers, 'tasks', task(due_at='2099-02-01T00:00:00Z',
            timezone='UTC', timezone_confirmed=True, remind_before_minutes=0))
        assert created.status_code == 201
        identifier = created.json()['task']['id']
    before = await planner_rows(pool)
    for value in INVALID_TIMES:
        response = await send_time(client, headers, endpoint, value, identifier=identifier)
        after = await planner_rows(pool)
        assert response.status_code == 422, (
            endpoint, value, response.status_code, {name: len(rows) for name, rows in after.items()})
        assert after == before, (endpoint, value, 'rejected request changed planner data/receipts')


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ENDPOINTS)
async def test_all_time_inputs_accept_rfc3339_z_offsets_and_fraction(contour, endpoint):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    identifier, version = None, 1
    if endpoint in ('patch_due', 'patch_override'):
        created = await post(client, headers, 'tasks', task(due_at='2099-02-01T00:00:00Z',
            timezone='UTC', timezone_confirmed=True, remind_before_minutes=0))
        assert created.status_code == 201
        identifier = created.json()['task']['id']
    for value, zone in (
        ('2099-01-01T00:00:00Z', 'UTC'),
        ('2099-01-02T03:00:00+03:00', 'Europe/Moscow'),
        ('2099-01-03T00:00:00.123456+00:00', 'UTC'),
    ):
        response = await send_time(client, headers, endpoint, value, identifier=identifier,
                                   version=version, zone=zone)
        assert response.status_code == (201 if endpoint in ('create', 'commit') else 200)
        result = response.json()
        if endpoint == 'as_of':
            actual = result['as_of']
        else:
            row = result['tasks'][1] if endpoint == 'commit' else result['task']
            actual = row['notification_override_at'] if endpoint == 'patch_override' else row['due_at']
            version = row['version']
        assert datetime.fromisoformat(actual) == datetime.fromisoformat(value).astimezone(timezone.utc)
