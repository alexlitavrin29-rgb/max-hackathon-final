"""Personal housing marks use verified owners and an isolated real PostgreSQL."""
import asyncio

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS


async def save(client, headers, status='in_list', version=0, **extra):
    return await client.put('/api/housing', headers=headers, json={
        'status': status, 'expected_version': version, **extra})


@pytest.mark.asyncio
async def test_housing_persists_per_owner_and_is_deleted_with_account(contour):
    pool, client, _ = contour
    assert (await client.get('/api/housing')).status_code == 401
    assert (await save(client, {})).status_code == 401
    a, b = [await launch(client, owner) for owner in OWNERS]
    assert (await client.get('/api/housing', headers=a)).json() == {'housing': None}
    saved = await save(client, a)
    assert saved.status_code == 200, saved.text
    document = saved.json()
    assert document['housing']['status'] == 'in_list'
    assert 'owner' not in document['housing']
    assert (await client.get('/api/housing', headers=a)).json() == document
    assert (await client.get('/api/housing', headers=b)).json() == {'housing': None}
    assert saved.headers['cache-control'] == 'no-store'
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM housing_statuses') == 0
    assert (await client.get('/api/housing', headers=a)).status_code == 401
    fresh = await launch(client, OWNERS[0])
    assert (await save(client, fresh, version=document['housing']['version'])).status_code == 409
    new = await save(client, fresh, status='received')
    assert new.json()['housing']['version'] > document['housing']['version']


@pytest.mark.asyncio
async def test_housing_concurrent_changes_cannot_overwrite_another_window(contour):
    _, client, _ = contour
    a = await launch(client, OWNERS[0])
    first = (await save(client, a)).json()['housing']
    assert (await save(client, a, version=first['version'])).json()['housing'] == first
    results = await asyncio.gather(*[
        save(client, a, status=status, version=first['version'])
        for status in ('received', 'application_sent')])
    assert sorted(r.status_code for r in results) == [200, 409]
    current = (await client.get('/api/housing', headers=a)).json()['housing']
    assert current['status'] in ('received', 'application_sent')
    assert current['version'] > first['version']


@pytest.mark.asyncio
async def test_housing_validates_marks_and_keeps_unknown_explicit(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    for status in ('invented', True, None, 1):
        assert (await save(client, a, status=status)).status_code == 422
    for version in (-1, True, '0', 2**63):
        assert (await save(client, a, version=version)).status_code == 422
    assert (await save(client, a, owner=str(OWNERS[1]))).status_code == 422
    assert await pool.fetchval('SELECT count(*) FROM housing_statuses') == 0
    version = 0
    for status in ('not_in_list', 'application_sent', 'in_list', 'offered', 'received', 'social_rent', 'unknown'):
        response = await save(client, a, status=status, version=version)
        assert response.status_code == 200, response.text
        version = response.json()['housing']['version']
        assert response.json()['housing']['status'] == status


@pytest.mark.asyncio
async def test_housing_rejects_expired_sessions_and_hides_stale_epoch(contour, monkeypatch):
    from project.miniapp.tests.test_planner_postgres import web
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    assert (await save(client, a)).status_code == 200
    await pool.execute('UPDATE housing_statuses SET account_epoch=account_epoch+1')
    assert (await client.get('/api/housing', headers=a)).json() == {'housing': None}
    assert await pool.fetchval('SELECT count(*) FROM housing_statuses') == 0
    monkeypatch.setattr(web.app.state, 'preview', True)
    assert (await client.get('/api/housing', headers=a)).status_code in (401, 403)
    monkeypatch.setattr(web.app.state, 'preview', False)
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '1 hour'")
    assert (await client.get('/api/housing', headers=a)).status_code == 401
    assert (await save(client, a)).status_code == 401


@pytest.mark.asyncio
async def test_missing_housing_migration_fails_without_implicit_schema_changes(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    await pool.execute('DROP TABLE housing_statuses')
    assert (await client.get('/api/housing', headers=a)).status_code == 503
    assert (await save(client, a)).status_code == 503
    assert not await pool.fetchval("SELECT to_regclass('housing_statuses') IS NOT NULL")
