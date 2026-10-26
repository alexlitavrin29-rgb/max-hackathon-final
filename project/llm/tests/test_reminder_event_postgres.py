"""Regression for the preserved runtime flow/reminders pair; never sends MAX.

PLANNER_TEST_DSN must select the dedicated isolated synthetic PostgreSQL contour.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from project.llm.services import flow, reminders
from project.llm.services.bot_sessions import BotSessions
from project.llm.services.scenario import ScenarioStore


@pytest_asyncio.fixture
async def reminder_contour():
    dsn = os.getenv('PLANNER_TEST_DSN')
    if not dsn:
        pytest.skip('PLANNER_TEST_DSN required for real PostgreSQL reminder regression')
    schema = 'k0_event_' + uuid4().hex
    control = await asyncpg.connect(dsn)
    await control.execute(f'CREATE SCHEMA {schema}')
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4,
                                    server_settings={'search_path': schema})
    try:
        scenarios, sessions = ScenarioStore(pool), BotSessions(pool)
        await scenarios.initialize()
        await sessions.initialize()
        store = reminders.ReminderStore(pool)
        await store.initialize()
        yield pool, scenarios, sessions, store
    finally:
        await pool.close()
        await control.execute(f'DROP SCHEMA {schema} CASCADE')
        await control.close()


async def pending_confirmation(sessions, owner):
    due = datetime.now(timezone.utc) + timedelta(days=2)
    nonce = uuid4().hex
    state = flow.FlowSession()
    state.pending = dict(stage='confirm', text='Synthetic runtime reminder', due=due,
                         zone='UTC', city='UTC', date=due.strftime('%H:%M, %d.%m.%Y'),
                         nonce=nonce, return_node='menu')
    await sessions.save(owner, state)
    return 'save:' + nonce


@pytest.mark.asyncio
async def test_event_replay_after_reminder_commit_before_session_save_is_not_duplicated(reminder_contour, monkeypatch):
    pool, scenarios, sessions, store = reminder_contour
    owner, event_id = 900000000021, 'synthetic-runtime-event-one'
    payload = await pending_confirmation(sessions, owner)
    original_save = sessions.save
    crashed = False

    async def interrupted_save(user, state, event_id=None, replies=None):
        nonlocal crashed
        if event_id and not crashed:
            crashed = True
            raise RuntimeError('synthetic crash before session commit')
        return await original_save(user, state, event_id, replies)

    monkeypatch.setattr(sessions, 'save', interrupted_save)
    first = flow.PublishedDialogue(store, scenarios, sessions)
    with pytest.raises(RuntimeError, match='synthetic crash before session commit'):
        await first.handle(owner, payload=payload, event_id=event_id)
    assert await pool.fetchval('SELECT count(*) FROM reminders WHERE user_id=$1', owner) == 1
    assert not await sessions.processed(event_id)
    assert (await sessions.load(owner)).pending['stage'] == 'confirm'

    # Recreate the dialogue as after a process restart. Real DB still holds the
    # pre-save confirmation, so the reminder creation path runs a second time.
    recovered = flow.PublishedDialogue(store, scenarios, sessions)
    replies = await recovered.handle(owner, payload=payload, event_id=event_id)
    assert replies
    assert await pool.fetchval('SELECT count(*) FROM reminders WHERE user_id=$1', owner) == 1
    row = await pool.fetchrow('SELECT id,text,state FROM reminders WHERE user_id=$1', owner)
    assert row['id'] == hashlib.sha256(f'{owner}:{event_id}'.encode()).hexdigest()[:32]
    assert row['text'] == 'Synthetic runtime reminder' and row['state'] == 'pending'
    assert await sessions.processed(event_id)
    assert (await sessions.load(owner)).pending is None
    assert await recovered.handle(owner, payload=payload, event_id=event_id) is None
    assert await pool.fetchval('SELECT count(*) FROM reminders') == 1


@pytest.mark.asyncio
async def test_event_scope_resets_on_success_and_failure_and_keeps_owner_event_ids_distinct(reminder_contour, monkeypatch):
    pool, scenarios, sessions, store = reminder_contour
    scope = getattr(reminders, 'CURRENT_EVENT_ID', None)
    assert scope is not None
    assert scope.get() is None
    owner, other = 900000000022, 900000000023
    token = scope.set('outer-context-sentinel')
    try:
        first = await pending_confirmation(sessions, owner)
        dialogue = flow.PublishedDialogue(store, scenarios, sessions)
        assert await dialogue.handle(owner, payload=first, event_id='synthetic-event-A')
        assert scope.get() == 'outer-context-sentinel'
        second = await pending_confirmation(sessions, owner)
        assert await dialogue.handle(owner, payload=second, event_id='synthetic-event-B')
        assert scope.get() == 'outer-context-sentinel'
        ids = await pool.fetch('SELECT id FROM reminders WHERE user_id=$1', owner)
        assert {r['id'] for r in ids} == {
            hashlib.sha256(f'{owner}:{e}'.encode()).hexdigest()[:32]
            for e in ('synthetic-event-A', 'synthetic-event-B')}
        # Scope restoration must also happen when engine.handle raises.
        engine = await dialogue.refresh()
        original = engine.handle
        async def failure(*args, **kwargs):
            assert scope.get() == 'synthetic-failing-event'
            raise RuntimeError('synthetic engine failure')
        monkeypatch.setattr(engine, 'handle', failure)
        with pytest.raises(RuntimeError, match='synthetic engine failure'):
            await dialogue.handle(owner, event_id='synthetic-failing-event')
        assert scope.get() == 'outer-context-sentinel'
        monkeypatch.setattr(engine, 'handle', original)
    finally:
        scope.reset(token)
    assert scope.get() is None
    # The same event value under two owners cannot collide in the actual store.
    token = scope.set('synthetic-same-event-value')
    due = datetime.now(timezone.utc) + timedelta(days=1)
    try:
        one = await store.create(owner, 'Synthetic owner A', due, 'UTC')
        two = await store.create(other, 'Synthetic owner B', due, 'UTC')
        assert one != two
        assert await store.create(owner, 'Synthetic owner A', due, 'UTC') == one
    finally:
        scope.reset(token)
    assert scope.get() is None
    assert len(await store.list(other)) == 1
    # Unscoped calls retain legacy random IDs rather than collapsing unrelated actions.
    random_one = await store.create(other, 'Unscoped synthetic one', due, 'UTC')
    random_two = await store.create(other, 'Unscoped synthetic two', due, 'UTC')
    assert random_one != random_two
