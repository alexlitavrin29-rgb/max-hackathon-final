"""Authenticated, owner-scoped handbook snapshots on an isolated PostgreSQL schema."""
import asyncio
import json

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, web


def path(identifier='doc_passport'):
    return '/api/handbook/' + identifier


@pytest.mark.asyncio
async def test_signed_session_required_and_planner_flag_does_not_gate(contour, monkeypatch):
    _, client, _ = contour
    monkeypatch.delenv('PLANNER_ENABLED', raising=False)
    for url in ['/api/handbook', path()]:
        assert (await client.get(url)).status_code == 401
    assert (await client.post(path() + '/favorite', json={'saved': True})).status_code == 401
    owner = await launch(client, OWNERS[0])
    listing = await client.get('/api/handbook?category=documents&q=паспорт', headers=owner)
    assert listing.status_code == 200
    assert listing.json()['entries']
    assert all(e['category'] == 'documents' for e in listing.json()['entries'])
    assert {c['id'] for c in listing.json()['categories']} == {
        'health', 'housing', 'work', 'documents', 'benefits', 'study', 'help', 'family', 'daily'}
    root = (await client.get(path('identity'), headers=owner)).json()
    assert root['card']['children']
    assert (await client.get(path('mentor'), headers=owner)).status_code == 404
    monkeypatch.setattr(web.app.state, 'preview', True)
    assert (await client.get('http://testserver/api/handbook', headers=owner)).status_code == 401


@pytest.mark.asyncio
async def test_snapshot_seen_authority_publication_updates_and_explicit_replace(contour):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    # Server ownership: arbitrary body fields and save-before-open are rejected.
    assert (await client.post(path() + '/favorite', headers=a, json={'saved': True})).status_code == 409
    original = (await client.get(path(), headers=a)).json()
    card = original['card']
    assert original['saved'] is False
    assert (await client.post(path() + '/favorite', headers=a,
                             json={'saved': True, 'text': 'injected'})).status_code == 422
    saved = await client.post(path() + '/favorite', headers=a,
                              json={'saved': True, 'content_hash': card['content_hash']})
    assert saved.status_code == 200
    assert saved.json()['saved_card']['text'] == card['text']
    assert (await client.get(path(), headers=b)).json()['saved'] is False
    assert (await client.post(path() + '/favorite', headers=b,
                             json={'saved': True, 'content_hash': '0' * 64})).status_code == 409
    current = await web.app.state.store.read()
    config = current['config']
    next(n for n in config['nodes'] if n['id'] == 'doc_passport')['text'] += '\n\nПроверенная новая редакция.'
    await web.app.state.store.publish(await web.app.state.store.save(config, current['revision']), 'Synthetic handbook update')
    # Save without another GET still uses the seen server-owned version.
    unchanged = (await client.post(path() + '/favorite', headers=a, json={'saved': True})).json()
    assert unchanged['updated'] is True
    assert unchanged['saved_card']['text'] == card['text']
    assert (await client.post(path() + '/favorite', headers=a,
                             json={'saved': True, 'replace': True})).status_code == 409
    latest = (await client.get(path(), headers=a)).json()
    assert latest['revision'] != original['revision']
    assert latest['updated'] is True
    assert latest['saved_card']['text'] == card['text']
    assert latest['card']['text'] != card['text']
    favorites = (await client.get('/api/favorites', headers=a)).json()['cards']
    assert favorites[0]['text'] == card['text']
    assert favorites[0]['updated'] is True
    assert favorites[0]['current_hash'] == latest['card']['content_hash']
    # Repeated saving cannot silently overwrite even after reading the new version.
    preserved = (await client.post(path() + '/favorite', headers=a, json={'saved': True})).json()
    assert preserved['saved_card']['text'] == card['text']
    replacement = (await client.post(path() + '/favorite', headers=a,
                                     json={'saved': True, 'replace': True,
                                           'content_hash': latest['card']['content_hash']})).json()
    assert replacement['saved_card']['text'] == latest['card']['text']
    assert replacement['updated'] is False
    removed = await client.post(path() + '/favorite', headers=a, json={'saved': False})
    assert removed.status_code == 200 and removed.json()['saved'] is False
    assert await pool.fetchval('SELECT count(*) FROM miniapp_favorites') == 0


@pytest.mark.asyncio
async def test_reads_reuse_owner_connection_and_expired_session_is_rejected(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    replies = await asyncio.wait_for(asyncio.gather(*[
        client.get(path(), headers=headers) for _ in range(8)]), timeout=5)
    assert all(r.status_code == 200 for r in replies)
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '2 hours'")
    assert (await client.get(path(), headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_withdrawn_favorite_can_be_removed_only_by_its_owner(contour):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    original = (await client.get(path(), headers=a)).json()['card']
    assert (await client.post(path() + '/favorite', headers=a, json={'saved': True})).status_code == 200
    current = await web.app.state.store.read()
    config = current['config']
    # A publication can hide a material from every child without changing its ID.
    next(n for n in config['nodes'] if n['id'] == 'doc_passport')['conditions'] = [
        {'field': 'role', 'op': 'eq', 'value': 'parent'}]
    await web.app.state.store.publish(await web.app.state.store.save(config, current['revision']),
                                      'Synthetic withdrawn handbook material')
    assert (await client.get(path(), headers=a)).status_code == 404
    assert (await client.post(path() + '/favorite', json={'saved': False})).status_code == 401
    foreign = await client.post(path() + '/favorite', headers=b, json={'saved': False})
    assert foreign.status_code == 200 and foreign.json()['card'] is None
    assert await pool.fetchval('SELECT count(*) FROM miniapp_favorites') == 1
    removed = await client.post(path() + '/favorite', headers=a, json={'saved': False})
    assert removed.status_code == 200 and removed.json()['saved'] is False
    assert removed.json()['card']['text'] == original['text']
    assert await pool.fetchval('SELECT count(*) FROM miniapp_favorites') == 0
    assert (await client.post(path() + '/favorite', headers=a, json={'saved': False})).status_code == 200
