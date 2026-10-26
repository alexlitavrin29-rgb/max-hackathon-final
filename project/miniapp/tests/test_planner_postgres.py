"""K0 integration: signed synthetic MAX identities and isolated real PostgreSQL.

Run explicitly with PLANNER_TEST_DSN pointed at a disposable local database.
The runner must block external networking; no MAX transport or model is used.
"""
import asyncio
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio

from project.llm.services.scenario import ScenarioStore
from project.llm.services.planner_store import apply_migration
from project.miniapp.favorites import Favorites
from project.miniapp.session_store import SessionStore

web = importlib.import_module('project.miniapp.app')
TOKEN = 'planner-k0-synthetic-only'
OWNERS = (900000000001, 900000000002)


def signed(owner, *, stamp=None):
    values = dict(auth_date=str(int(time.time()) if stamp is None else stamp),
                  user=json.dumps({'id': owner}))
    secret = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    payload = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    return urlencode({**values, 'hash': hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()})


@pytest_asyncio.fixture
async def contour(monkeypatch):
    dsn = os.getenv('PLANNER_TEST_DSN')
    if not dsn:
        pytest.skip('PLANNER_TEST_DSN required for isolated PostgreSQL integration')
    schema = 'k0_' + uuid4().hex
    control = await asyncpg.connect(dsn)
    await control.execute(f'CREATE SCHEMA {schema}')
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5,
                                    server_settings={'search_path': schema})
    try:
        scenario = ScenarioStore(pool)
        await scenario.initialize()
        config = json.loads(Path('submission/scenario.json').read_text())['config']
        current = await scenario.read()
        await scenario.publish(await scenario.save(config, current['revision']), 'Synthetic K0 reference')
        shared, favorites = SessionStore(pool), Favorites(pool)
        await shared.initialize()
        await favorites.initialize()
        await apply_migration(pool)
        monkeypatch.setenv('PLANNER_ENABLED', '1')
        for name, value in dict(store=scenario, shared=shared, favorites=favorites,
                                preview=False, origin='https://planner.test', token=TOKEN,
                                published_cache=None, sessions={}).items():
            monkeypatch.setattr(web.app.state, name, value, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),
                                     base_url='https://planner.test') as client:
            yield pool, client, schema
    finally:
        await pool.close()
        await control.execute(f'DROP SCHEMA {schema} CASCADE')
        await control.close()


async def launch(client, owner):
    response = await client.post('/api/session', json={'init_data': signed(owner)})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['session']}


async def create(client, headers, goal, key='create-goal'):
    return await client.post('/api/planner/goals', json=goal,
                             headers={**headers, 'Idempotency-Key': key})


@pytest.mark.asyncio
async def test_create_read_owner_isolation_and_atomic_retry(contour):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    goal = {'id': str(uuid4()), 'title': 'Подготовить документы'}
    created = await create(client, a, goal)
    assert created.status_code == 201
    result = created.json()
    assert result['goal']['id'] == goal['id']
    assert result['goal']['title'] == goal['title']
    assert result['goal']['version'] == 1
    assert 'owner' not in result['goal']
    path = '/api/planner/goals/' + goal['id']
    assert (await client.get(path, headers=a)).json() == result
    foreign = await client.get(path, headers=b)
    missing = await client.get('/api/planner/goals/' + str(uuid4()), headers=b)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    repeated = await asyncio.gather(*(create(client, a, goal) for _ in range(4)))
    assert all(r.status_code == 201 and r.json() == result for r in repeated)
    conflict = await create(client, a, {**goal, 'title': 'Другая цель'})
    assert conflict.status_code == 409
    own_b = await create(client, b, goal)
    assert own_b.status_code == 201
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 2
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 2


@pytest.mark.asyncio
async def test_new_bearer_process_and_missing_receipt_do_not_duplicate(contour):
    import subprocess
    import sys
    pool, client, schema = contour
    a = await launch(client, OWNERS[0])
    goal = {'id': str(uuid4()), 'title': 'Синтетическая цель после перезапуска'}
    first = await create(client, a, goal)
    assert first.status_code == 201
    await pool.execute("UPDATE miniapp_launches SET launched_at=now()-interval '6 seconds'")
    new = await launch(client, OWNERS[0])
    assert a != new
    assert (await create(client, new, goal)).json() == first.json()
    script = '''
import asyncio, importlib, json, os, sys
from types import SimpleNamespace
import asyncpg, httpx
from project.miniapp.session_store import SessionStore
async def main(data):
    web=importlib.import_module('project.miniapp.app')
    pool=await asyncpg.create_pool(os.environ['PLANNER_TEST_DSN'],min_size=1,max_size=2,
        server_settings={'search_path':data['schema']})
    try:
        web.app.state.preview=False
        web.app.state.origin='https://planner.test'
        web.app.state.shared=SessionStore(pool)
        web.app.state.store=SimpleNamespace(pool=pool)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url='https://planner.test') as c:
            read=await c.get('/api/planner/goals/'+data['goal']['id'],headers=data['headers'])
            retry=await c.post('/api/planner/goals',json=data['goal'],headers={**data['headers'],'Idempotency-Key':'create-goal'})
            assert read.status_code==200 and retry.status_code==201
            assert read.json()==retry.json()==data['expected']
        print('restart-read-retry-ok')
    finally:
        await pool.close()
asyncio.run(main(json.load(sys.stdin)))
'''
    # New interpreter + its own database connections and app instance; no parent
    # globals or in-memory cache can satisfy these assertions.
    proc = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script],
        input=json.dumps(dict(schema=schema, goal=goal, headers=new, expected=first.json())),
        text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == 'restart-read-retry-ok'
    await pool.execute('DELETE FROM planner_operations WHERE owner=$1', str(OWNERS[0]))
    assert (await create(client, new, goal, 'after-receipt-expiry')).json() == first.json()
    assert (await create(client, new, {**goal, 'title': 'Другой текст'}, 'conflict-uuid')).status_code == 409
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1


@pytest.mark.asyncio
async def test_account_delete_with_flag_off_removes_only_owner_and_revokes_bearer(contour, monkeypatch):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    goals = [dict(id=str(uuid4()), title='Тестовая цель ' + label) for label in ('A', 'B')]
    for headers, goal in zip((a, b), goals):
        assert (await create(client, headers, goal)).status_code == 201
    monkeypatch.setenv('PLANNER_ENABLED', '0')
    deleted = await client.delete('/api/account', headers=a)
    assert deleted.status_code == 200 and deleted.json() == {'deleted': True}
    assert await pool.fetchval('SELECT count(*) FROM planner_goals WHERE owner=$1', str(OWNERS[0])) == 0
    assert await pool.fetchval('SELECT count(*) FROM planner_operations WHERE owner=$1', str(OWNERS[0])) == 0
    assert await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', str(OWNERS[0])) == 1
    monkeypatch.setenv('PLANNER_ENABLED', '1')
    assert (await client.get('/api/planner/goals/' + goals[0]['id'], headers=a)).status_code == 401
    assert (await create(client, a, goals[0])).status_code == 401
    assert (await client.get('/api/planner/goals/' + goals[1]['id'], headers=b)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 1
    fresh = await launch(client, OWNERS[0])
    assert (await client.get('/api/planner/goals/' + goals[0]['id'], headers=fresh)).status_code == 404
    assert (await create(client, fresh, goals[0])).status_code == 201
    assert await pool.fetchval('SELECT account_epoch FROM planner_operations WHERE owner=$1', str(OWNERS[0])) == 1


@pytest.mark.asyncio
async def test_delete_between_identity_lookup_and_owner_lock_prevents_write(contour, monkeypatch):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    original = web.shared_owner
    captured, proceed = asyncio.Event(), asyncio.Event()

    async def delayed(request):
        result = await original(request)
        if request.url.path == '/api/planner/goals':
            captured.set()
            await proceed.wait()
        return result

    monkeypatch.setattr(web, 'shared_owner', delayed)
    goal = dict(id=str(uuid4()), title='Цель, удалённая до записи')
    request = asyncio.create_task(create(client, a, goal))
    await asyncio.wait_for(captured.wait(), 5)
    try:
        assert (await client.delete('/api/account', headers=a)).status_code == 200
    finally:
        proceed.set()
    assert (await asyncio.wait_for(request, 5)).status_code == 401
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 0
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 0


@pytest.mark.asyncio
async def test_auth_validation_boundaries_and_disabled_default(contour, monkeypatch):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    goal = dict(id=str(uuid4()), title='  Тестовая цель  ')
    response = await create(client, a, goal)
    assert response.status_code == 201 and response.json()['goal']['title'] == goal['title'].strip()
    path = '/api/planner/goals/' + goal['id']
    for headers in ({}, {'Authorization': 'Bearer invalid'}):
        assert (await client.get(path, headers=headers)).status_code == 401
        assert (await create(client, headers, goal)).status_code == 401
    assert (await client.post('/api/session', json={'init_data': signed(OWNERS[1], stamp=1)})).status_code == 401
    bad = signed(OWNERS[1]) + '&hash=invalid'
    assert (await client.post('/api/session', json={'init_data': bad})).status_code == 401
    for body in ({**goal, 'owner': str(OWNERS[1])}, {**goal, 'title': ''}, {**goal, 'title': ' '},
                 {**goal, 'title': 123}, {**goal, 'title': 'x' * 161}, {**goal, 'id': 'invalid'}):
        assert (await create(client, a, body, 'invalid-input')).status_code == 422
    assert (await client.post('/api/planner/goals', json=goal, headers=a)).status_code == 422
    assert (await create(client, a, goal, 'x' * 129)).status_code == 422
    assert (await client.post('/api/planner/goals', json=goal,
        headers={**a, 'Idempotency-Key': 'origin', 'Origin': 'https://other.invalid'})).status_code == 403
    assert (await client.post('/api/planner/goals', content='x' * 24001,
        headers={**a, 'Idempotency-Key': 'large'})).status_code == 413
    assert response.headers['cache-control'] == 'no-store'
    monkeypatch.delenv('PLANNER_ENABLED', raising=False)
    assert (await client.get(path, headers=a)).status_code == 404
    monkeypatch.setenv('PLANNER_ENABLED', '1')
    monkeypatch.setattr(web.app.state, 'preview', True)
    assert (await client.get('http://localhost' + path, headers=a)).status_code == 401
    monkeypatch.setattr(web.app.state, 'preview', False)
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '1 second'")
    assert (await client.get(path, headers=a)).status_code == 401


@pytest.mark.asyncio
async def test_migration_replay_checksum_publication_and_transaction_rollback(contour):
    from project.llm.services.planner_store import apply_migration
    pool, client, _ = contour
    before = await pool.fetchrow('SELECT published_id,revision,draft FROM scenario_state WHERE id=1')
    published = await pool.fetchrow('SELECT config FROM scenario_versions WHERE id=$1', before['published_id'])
    await apply_migration(pool)
    await apply_migration(pool)
    from project.llm.services.planner_store import MIGRATIONS
    assert await pool.fetchval('SELECT count(*) FROM schema_migrations') == len(MIGRATIONS)
    a = await launch(client, OWNERS[0])
    await pool.execute('''CREATE FUNCTION reject_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE check_violation USING MESSAGE='synthetic receipt failure'; END $$;
        CREATE TRIGGER reject_receipt BEFORE INSERT ON planner_operations
        FOR EACH ROW EXECUTE FUNCTION reject_receipt();''')
    goal = dict(id=str(uuid4()), title='Атомарная синтетическая цель')
    with pytest.raises(asyncpg.CheckViolationError, match='synthetic receipt failure'):
        await create(client, a, goal)
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 0
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 0
    await pool.execute('DROP TRIGGER reject_receipt ON planner_operations')
    assert (await create(client, a, goal)).status_code == 201
    assert await pool.fetchrow('SELECT published_id,revision,draft FROM scenario_state WHERE id=1') == before
    assert await pool.fetchrow('SELECT config FROM scenario_versions WHERE id=$1', before['published_id']) == published
    await pool.execute("UPDATE schema_migrations SET sha256='synthetic-mismatch'")
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        await apply_migration(pool)


@pytest.mark.asyncio
async def test_session_cleanup_preserves_epoch_for_durable_planner_receipts(contour):
    pool, client, _ = contour
    owner = str(OWNERS[0])
    await pool.execute('''INSERT INTO account_epochs(owner,epoch,changed_at)
        VALUES($1,1,now()-interval '2 days')''', owner)
    a = await launch(client, OWNERS[0])
    # Simulate a recreated account's generation after its old transient sessions
    # have expired. A later launch must not reset its durable receipt namespace.
    await pool.execute('''INSERT INTO account_epochs(owner,epoch,changed_at)
        VALUES($1,1,now()-interval '2 days') ON CONFLICT(owner) DO UPDATE
        SET epoch=1,changed_at=now()-interval '2 days' ''', owner)
    goal = dict(id=str(uuid4()), title='Долговечная синтетическая цель')
    first = await create(client, a, goal)
    assert first.status_code == 201
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '1 second'")
    await pool.execute("UPDATE miniapp_launches SET launched_at=now()-interval '6 seconds'")
    new = await launch(client, OWNERS[0])
    assert await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) == 1
    assert (await create(client, new, goal)).json() == first.json()
    changed = dict(id=str(uuid4()), title='Новая цель с занятым ключом')
    assert (await create(client, new, changed)).status_code == 409
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1


@pytest.mark.asyncio
async def test_unmigrated_disabled_app_keeps_legacy_launch_and_account_deletion(contour, monkeypatch):
    pool, client, _ = contour
    await pool.execute('DROP TABLE planner_notifications,planner_tasks,planner_operations,planner_goals,schema_migrations')
    monkeypatch.delenv('PLANNER_ENABLED', raising=False)
    owner = str(OWNERS[0])
    await pool.execute('''INSERT INTO account_epochs(owner,epoch,changed_at)
        VALUES($1,1,now()-interval '2 days')''', owner)
    a = await launch(client, OWNERS[0])
    assert await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) is None
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    assert await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) == 1
