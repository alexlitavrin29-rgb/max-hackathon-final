"""Personal career marks use verified owners and an isolated real PostgreSQL."""
import asyncio

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS


async def save(client, headers, status='searching', version=0, **extra):
    return await client.put('/api/career', headers=headers, json={
        'status': status, 'expected_version': version, **extra})


@pytest.mark.asyncio
async def test_career_persists_per_owner_and_is_deleted_with_account(contour):
    pool, client, _ = contour
    assert (await client.get('/api/career')).status_code == 401
    assert (await save(client, {})).status_code == 401
    a, b = [await launch(client, owner) for owner in OWNERS]
    assert (await client.get('/api/career', headers=a)).json() == {'career': None}
    saved = await save(client, a)
    assert saved.status_code == 200, saved.text
    document = saved.json()
    assert document['career']['status'] == 'searching'
    assert 'owner' not in document['career']
    assert (await client.get('/api/career', headers=a)).json() == document
    assert (await client.get('/api/career', headers=b)).json() == {'career': None}
    assert saved.headers['cache-control'] == 'no-store'
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM career_statuses') == 0
    assert (await client.get('/api/career', headers=a)).status_code == 401
    fresh = await launch(client, OWNERS[0])
    assert (await save(client, fresh, version=document['career']['version'])).status_code == 409
    new = await save(client, fresh, status='employed')
    assert new.json()['career']['version'] > document['career']['version']


@pytest.mark.asyncio
async def test_career_concurrent_changes_cannot_overwrite_another_window(contour):
    _, client, _ = contour
    a = await launch(client, OWNERS[0])
    first = (await save(client, a)).json()['career']
    assert (await save(client, a, version=first['version'])).json()['career'] == first
    results = await asyncio.gather(*[
        save(client, a, status=status, version=first['version'])
        for status in ('employed', 'resume')])
    assert sorted(r.status_code for r in results) == [200, 409]
    current = (await client.get('/api/career', headers=a)).json()['career']
    assert current['status'] in ('employed', 'resume')
    assert current['version'] > first['version']


@pytest.mark.asyncio
async def test_career_validates_marks_and_keeps_unknown_explicit(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    for status in ('invented', True, None, 1):
        assert (await save(client, a, status=status)).status_code == 422
    for version in (-1, True, '0', 2**63):
        assert (await save(client, a, version=version)).status_code == 422
    assert (await save(client, a, owner=str(OWNERS[1]))).status_code == 422
    assert await pool.fetchval('SELECT count(*) FROM career_statuses') == 0
    version = 0
    for status in ('unknown', 'searching', 'resume', 'interview', 'employed'):
        response = await save(client, a, status=status, version=version)
        assert response.status_code == 200, response.text
        version = response.json()['career']['version']
        assert response.json()['career']['status'] == status


@pytest.mark.asyncio
async def test_career_rejects_expired_sessions_and_hides_stale_epoch(contour, monkeypatch):
    from project.miniapp.tests.test_planner_postgres import web
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    assert (await save(client, a)).status_code == 200
    await pool.execute('UPDATE career_statuses SET account_epoch=account_epoch+1')
    assert (await client.get('/api/career', headers=a)).json() == {'career': None}
    assert await pool.fetchval('SELECT count(*) FROM career_statuses') == 0
    monkeypatch.setattr(web.app.state, 'preview', True)
    assert (await client.get('/api/career', headers=a)).status_code in (401, 403)
    monkeypatch.setattr(web.app.state, 'preview', False)
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '1 hour'")
    assert (await client.get('/api/career', headers=a)).status_code == 401
    assert (await save(client, a)).status_code == 401


@pytest.mark.asyncio
async def test_missing_career_migration_fails_without_implicit_schema_changes(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    await pool.execute('DROP TABLE career_statuses')
    assert (await client.get('/api/career', headers=a)).status_code == 503
    assert (await save(client, a)).status_code == 503
    assert not await pool.fetchval("SELECT to_regclass('career_statuses') IS NOT NULL")


@pytest.mark.asyncio
async def test_career_and_housing_marks_remain_independent(contour):
    _, client, _ = contour
    a = await launch(client, OWNERS[0])
    house = await client.put('/api/housing', headers=a, json={'status':'in_list','expected_version':0})
    assert house.status_code == 200
    assert (await save(client, a, status='resume')).status_code == 200
    assert (await client.get('/api/housing', headers=a)).json() == house.json()
    assert (await client.get('/api/career', headers=a)).json()['career']['status'] == 'resume'
