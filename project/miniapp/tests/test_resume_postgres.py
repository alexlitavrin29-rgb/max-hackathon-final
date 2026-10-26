"""Signed synthetic sessions, owner/epoch isolation and CAS on real disposable PG."""
import asyncio

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, web


async def save(client, headers, version=0, **state):
    return await client.put('/api/resume', headers=headers, json={'expected_version': version, 'state': state})


@pytest.mark.asyncio
async def test_verified_session_and_owner_scoped_saved_pdf(contour, monkeypatch):
    _, client, _ = contour
    for url in ('/api/resume', '/api/resume/pdf'):
        assert (await client.get(url)).status_code == 401
    assert (await save(client, {})).status_code == 401
    a, b = [await launch(client, owner) for owner in OWNERS]
    monkeypatch.delenv('PLANNER_ENABLED', raising=False)
    initial = await client.get('/api/resume', headers=a)
    assert initial.json() == {'resume': None}
    assert initial.headers['cache-control'] == 'no-store'
    assert (await client.get('/api/resume/pdf', headers=a)).status_code == 404
    created = await save(client, a, name='Анна Иванова', desired_role='Повар')
    assert created.status_code == 200
    assert 'owner' not in created.json()['resume']
    assert (await client.get('/api/resume', headers=b)).json() == {'resume': None}
    assert (await client.get('/api/resume/pdf', headers=b)).status_code == 404
    pdf = await client.get('/api/resume/pdf', headers=a)
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
    assert pdf.headers['cache-control'] == 'no-store'
    monkeypatch.setattr(web.app.state, 'preview', True)
    assert (await client.get('/api/resume', headers=a)).status_code in (401, 403)


@pytest.mark.asyncio
async def test_concurrent_cas_noop_and_account_recreation_rejects_old_version(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    first = (await save(client, a, name='Анна')).json()['resume']
    version = first['version']
    noop = await save(client, a, version, name='Анна')
    assert noop.json()['resume'] == first
    results = await asyncio.gather(*[save(client, a, version, name=name) for name in ('Борис', 'Вера')])
    assert sorted(r.status_code for r in results) == [200, 409]
    assert await pool.fetchval('SELECT count(*) FROM planner_resumes') == 1
    current = (await client.get('/api/resume', headers=a)).json()['resume']
    assert current['state']['name'] in ('Борис', 'Вера')
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_resumes') == 0
    assert (await client.get('/api/resume/pdf', headers=a)).status_code == 401
    fresh = await launch(client, OWNERS[0])
    assert (await client.get('/api/resume', headers=fresh)).json() == {'resume': None}
    assert (await save(client, fresh, current['version'], name='Устаревший браузер')).status_code == 409
    new = (await save(client, fresh, name='Новое резюме')).json()['resume']
    assert new['version'] > current['version']


@pytest.mark.asyncio
async def test_epoch_stale_rows_expiry_and_arbitrary_fields(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    assert (await save(client, a, passport='no')).status_code == 422
    assert (await save(client, a, name='x' * 241)).status_code == 422
    assert (await save(client, a, name='Анна')).status_code == 200
    await pool.execute('UPDATE planner_resumes SET account_epoch=account_epoch+1')
    assert (await client.get('/api/resume', headers=a)).json() == {'resume': None}
    assert await pool.fetchval('SELECT count(*) FROM planner_resumes') == 0
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '1 hour'")
    assert (await save(client, a, name='Просрочено')).status_code == 401
    assert (await client.get('/api/resume/pdf', headers=a)).status_code == 401
