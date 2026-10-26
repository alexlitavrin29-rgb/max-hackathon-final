"""Bounded opaque draft validation and real PostgreSQL draft lifecycle."""
import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from project.miniapp.planner_drafts import PutDraft, DeleteDraft, sweep_expired
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS


def test_draft_wire_limits():
    assert PutDraft(expected_version=0, state={'kind': 'editor', 'title': 'Черновик'}).expected_version == 0
    for state in ([], {}, {'kind': 'command'}, {'kind': 'editor', '__proto__': {}},
                  {'kind': 'editor', 'data': {'constructor': {}}},
                  {'kind': 'editor', 'text': '\x00'}, {'kind': 'editor', '\x00': 'value'},
                  {'kind': 'editor', 'text': 'я' * 9000}, {'kind': 'editor', 'number': float('inf')}):
        with pytest.raises(ValidationError):
            PutDraft(expected_version=0, state=state)
    nested = {'kind': 'editor'}
    cursor = nested
    for _ in range(12):
        cursor['child'] = {}
        cursor = cursor['child']
    with pytest.raises(ValidationError):
        PutDraft(expected_version=0, state=nested)
    for version in (-1, True, '0'):
        with pytest.raises(ValidationError):
            PutDraft(expected_version=version, state={'kind': 'editor'})
    with pytest.raises(ValidationError):
        DeleteDraft(expected_version=0)


async def put(client, headers, version, state):
    return await client.put('/api/planner/draft', headers=headers,
                            json=dict(expected_version=version, state=state))


async def remove(client, headers, version):
    return await client.request('DELETE', '/api/planner/draft', headers=headers,
                                json=dict(expected_version=version))


@pytest.mark.asyncio
async def test_draft_ttl_noop_conflict_and_owner_scope(contour):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    assert (await client.get('/api/planner/draft', headers=a)).json() == {'draft': None}
    state = dict(kind='editor', title='Мой черновик', selected=[1, 2])
    first = await put(client, a, 0, state)
    assert first.status_code == 200, first.text
    draft = first.json()['draft']
    version = draft['version']
    assert version >= 1 and draft['state'] == state
    row = await pool.fetchrow('SELECT * FROM planner_drafts')
    assert row['expires_at'] - row['updated_at'] == timedelta(hours=24)
    assert (await client.get('/api/planner/draft', headers=a)).json() == first.json()
    assert (await put(client, a, version, state)).json() == first.json()
    assert (await pool.fetchrow('SELECT * FROM planner_drafts')) == row
    assert (await client.get('/api/planner/draft', headers=b)).json() == {'draft': None}
    assert (await remove(client, b, 1)).status_code == 409
    assert (await put(client, a, 0, state)).json()['detail']['code'] == 'draft_conflict'
    changed = await put(client, a, version, {**state, 'title': 'Изменено'})
    changed_version = changed.json()['draft']['version']
    assert changed_version > version
    assert datetime.fromisoformat(changed.json()['draft']['expires_at']) > datetime.fromisoformat(draft['expires_at'])
    assert (await remove(client, a, version)).status_code == 409
    assert (await remove(client, a, changed_version)).json() == {'deleted': True}
    recreated = (await put(client, a, 0, state)).json()['draft']
    assert recreated['version'] > changed_version
    assert (await put(client, a, changed_version, {'kind': 'editor', 'title': 'Stale device'})).status_code == 409
    assert (await remove(client, a, changed_version)).status_code == 409
    assert (await client.get('/api/planner/draft', headers=a)).json()['draft'] == recreated
    for table in ('planner_goals', 'planner_tasks', 'planner_operations'):
        assert await pool.fetchval('SELECT count(*) FROM ' + table) == 0


@pytest.mark.asyncio
async def test_draft_expiry_epoch_and_account_deletion(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    state = {'kind': 'consultation', 'question': 'Вопрос'}
    first_version = (await put(client, a, 0, state)).json()['draft']['version']
    await pool.execute("UPDATE planner_drafts SET updated_at=now()-interval '25 hours', expires_at=now()-interval '1 hour'")
    assert (await client.get('/api/planner/draft', headers=a)).json() == {'draft': None}
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 0
    assert (await put(client, a, first_version, state)).status_code == 409
    recreated = (await put(client, a, 0, state)).json()['draft']
    assert recreated['version'] > first_version
    assert (await put(client, a, first_version, {'kind': 'editor', 'title': 'Stale device'})).status_code == 409
    assert (await remove(client, a, first_version)).status_code == 409
    assert (await client.get('/api/planner/draft', headers=a)).json()['draft'] == recreated
    # A stale row from an earlier account lifetime cannot be read or reused.
    await pool.execute('UPDATE planner_drafts SET account_epoch=account_epoch+1')
    assert (await client.get('/api/planner/draft', headers=a)).json() == {'draft': None}
    current_version = (await put(client, a, 0, state)).json()['draft']['version']
    assert current_version > recreated['version']
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 0
    fresh = await launch(client, OWNERS[0])
    assert (await client.get('/api/planner/draft', headers=fresh)).json() == {'draft': None}
    assert (await put(client, fresh, 0, state)).json()['draft']['version'] > current_version


@pytest.mark.asyncio
async def test_draft_explicit_migration_gate_and_authority(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    assert (await client.get('/api/planner/draft')).status_code == 401
    assert (await client.put('/api/planner/draft', headers={**a, 'Origin': 'https://foreign.test'},
        json=dict(expected_version=0, state={'kind': 'editor'}))).status_code == 403
    assert (await put(client, a, 0, {'kind': 'editor', 'text': 'x' * 25_000})).status_code == 413
    assert (await put(client, a, 0, {'kind': 'editor', 'text': 'x' * 18_000})).status_code == 422
    await pool.execute('DROP TABLE planner_drafts')
    await pool.execute("DELETE FROM schema_migrations WHERE version='005_planner_drafts'")
    for response in (await client.get('/api/planner/draft', headers=a),
                     await put(client, a, 0, {'kind': 'editor'}), await remove(client, a, 1)):
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'planner_migration_required'
    await pool.execute('DROP TABLE schema_migrations')
    assert (await client.get('/api/planner/draft', headers=a)).json()['detail']['code'] == 'planner_migration_required'


@pytest.mark.asyncio
async def test_draft_concurrent_devices_and_json_scalar_types(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    state = {'kind': 'editor', 'selected': True}
    first_version = (await put(client, a, 0, state)).json()['draft']['version']
    # JSON true and 1 must not count as equal merely because Python equates them.
    next_version = (await put(client, a, first_version, {**state, 'selected': 1})).json()['draft']['version']
    assert next_version > first_version
    results = await asyncio.gather(*[
        put(client, a, next_version, {**state, 'device': device}) for device in ('a', 'b')])
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result.json() for result in results if result.status_code == 200)
    assert winner['draft']['version'] > next_version
    assert (await client.get('/api/planner/draft', headers=a)).json() == winner


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['editor', 'consultation'])
async def test_real_edits_renew_only_live_owned_proposal_policy(contour, kind):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    owner, other = map(str, OWNERS)
    epoch = await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    goal_id, expired_id = uuid4(), uuid4()
    for policy_owner, policy_epoch, identifier, interval in (
            (owner, epoch, goal_id, '1 hour'), (other, epoch, goal_id, '1 hour'),
            (owner, epoch + 1, goal_id, '1 hour'), (owner, epoch, expired_id, '-1 hour')):
        await pool.execute('''INSERT INTO planner_proposal_policies
            (owner,account_epoch,goal_id,question,required_resources,expires_at)
            VALUES($1,$2,$3,'Question','[]',now()+$4::text::interval)''',
            policy_owner, policy_epoch, identifier, interval)
    initial = await pool.fetch('SELECT * FROM planner_proposal_policies')

    def draft_state(identifier, text):
        reference = {'goalID': str(identifier)} if kind == 'editor' else {'proposal': {'goal_id': str(identifier)}}
        return {'kind': kind, 'text': text, **reference}

    state = draft_state(goal_id, 'First edit')
    created = (await put(client, a, 0, state)).json()['draft']
    renewed = await pool.fetchrow('''SELECT * FROM planner_proposal_policies
        WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3''', owner, epoch, goal_id)
    assert renewed['expires_at'] == datetime.fromisoformat(created['expires_at'])
    for original in initial:
        if original['owner'] == owner and original['account_epoch'] == epoch and original['goal_id'] == goal_id:
            assert renewed['expires_at'] > original['expires_at']
        else:
            assert await pool.fetchval('''SELECT expires_at FROM planner_proposal_policies
                WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3''',
                original['owner'], original['account_epoch'], original['goal_id']) == original['expires_at']
    assert (await client.get('/api/planner/draft', headers=a)).json()['draft'] == created
    assert (await put(client, a, created['version'], state)).json()['draft'] == created
    assert await pool.fetchval('''SELECT expires_at FROM planner_proposal_policies
        WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3''', owner, epoch, goal_id) == renewed['expires_at']
    changed = (await put(client, a, created['version'], draft_state(goal_id, 'Second edit'))).json()['draft']
    expiry = await pool.fetchval('''SELECT expires_at FROM planner_proposal_policies
        WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3''', owner, epoch, goal_id)
    assert expiry == datetime.fromisoformat(changed['expires_at']) and expiry > renewed['expires_at']
    expired_before = next(row['expires_at'] for row in initial if row['goal_id'] == expired_id)
    expired_draft = (await put(client, a, changed['version'], draft_state(expired_id, 'Expired proposal'))).json()['draft']
    assert await pool.fetchval('SELECT expires_at FROM planner_proposal_policies WHERE goal_id=$1', expired_id) == expired_before
    version = expired_draft['version']
    for invalid in (None, {}, 'not-a-uuid', 'x' * 100):
        state = {'kind': kind, 'goalID': invalid} if kind == 'editor' else {'kind': kind, 'proposal': {'goal_id': invalid}}
        response = await put(client, a, version, state)
        assert response.status_code == 200
        version = response.json()['draft']['version']
    await pool.execute('DROP TABLE planner_proposal_policies')
    assert (await put(client, a, version, draft_state(goal_id, 'Migration 005 only'))).status_code == 200


@pytest.mark.asyncio
async def test_retention_sweep_removes_expired_payloads_and_keeps_identity_tombstones(contour):
    pool, _, _ = contour
    expired_owner, active_owner = map(str, OWNERS)
    for owner, age in ((expired_owner, '25 hours'), (active_owner, '0 hours')):
        await pool.execute('''INSERT INTO planner_drafts
            (owner,account_epoch,state,updated_at,expires_at)
            VALUES($1,0,'{"kind":"editor","text":"Sensitive draft"}',
                   now()-$2::text::interval,now()-$2::text::interval+interval '24 hours')''', owner, age)
    expired_id, active_id = uuid4(), uuid4()
    for identifier, offset in ((expired_id, '-1 hour'), (active_id, '1 hour')):
        await pool.execute('''INSERT INTO planner_proposal_policies
            (owner,account_epoch,goal_id,question,required_resources,expires_at)
            VALUES($1,0,$2,'Sensitive question','["app_work"]',now()+$3::text::interval)''',
            expired_owner, identifier, offset)
    active_draft = await pool.fetchrow('SELECT * FROM planner_drafts WHERE owner=$1', active_owner)
    active_policy = await pool.fetchrow('SELECT * FROM planner_proposal_policies WHERE goal_id=$1', active_id)
    expired_policy = await pool.fetchrow('SELECT * FROM planner_proposal_policies WHERE goal_id=$1', expired_id)
    await sweep_expired(pool)
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 1
    assert await pool.fetchrow('SELECT * FROM planner_drafts WHERE owner=$1', active_owner) == active_draft
    assert await pool.fetchrow('SELECT * FROM planner_proposal_policies WHERE goal_id=$1', active_id) == active_policy
    tombstone = await pool.fetchrow('SELECT * FROM planner_proposal_policies WHERE goal_id=$1', expired_id)
    assert tombstone['question'] == '' and tombstone['required_resources'] == '[]'
    assert {key: tombstone[key] for key in ('owner', 'account_epoch', 'goal_id', 'expires_at')} == {
        key: expired_policy[key] for key in ('owner', 'account_epoch', 'goal_id', 'expires_at')}
    await sweep_expired(pool)
    assert await pool.fetchrow('SELECT * FROM planner_proposal_policies WHERE goal_id=$1', expired_id) == tombstone
    # Migration 005 alone still permits draft cleanup without the policy table.
    await pool.execute('DROP TABLE planner_proposal_policies')
    await sweep_expired(pool)
    # Old installations must remain a no-op without implicitly installing 005.
    await pool.execute("DELETE FROM schema_migrations WHERE version='005_planner_drafts'")
    await pool.execute("UPDATE planner_drafts SET updated_at=now()-interval '25 hours',expires_at=now()-interval '1 hour'")
    await sweep_expired(pool)
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 1
    await pool.execute('DROP TABLE planner_drafts')
    await sweep_expired(pool)
    await pool.execute('DROP TABLE schema_migrations')
    await sweep_expired(pool)
