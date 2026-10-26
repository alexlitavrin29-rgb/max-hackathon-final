"""Authenticated handbook reads and explicit server-owned favorite snapshots."""
from contextlib import asynccontextmanager
import json
import hashlib

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from project.llm.services.handbook import IDENTIFIER, categories, handbook_catalog, handbook_material
from .session_store import token_hash

router = APIRouter(prefix='/api/handbook', tags=['Handbook'])


@asynccontextmanager
async def authority(request):
    from .app import shared_owner, owner_transaction, checked_shared_session
    if request.app.state.preview or not getattr(request.app.state, 'shared', None):
        raise HTTPException(401, 'Откройте приложение заново из MAX.')
    initial = await shared_owner(request)
    async with owner_transaction(initial.owner) as connection:
        state = await checked_shared_session(request, connection)
        if state.owner != initial.owner:
            raise HTTPException(401, 'Откройте приложение заново из MAX.')
        yield connection, state


async def publication(connection):
    row = await connection.fetchrow('''SELECT v.id AS revision,v.config
        FROM scenario_state s JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1''')
    if not row:
        raise HTTPException(503, 'Справочник пока недоступен.')
    return row['revision'], json.loads(row['config'])


async def snapshot(connection, owner, favorite_id):
    row = await connection.fetchrow('''SELECT card,saved_at FROM miniapp_favorites
        WHERE owner=$1 AND card_id=$2''', owner, favorite_id)
    return dict(json.loads(row['card']), saved_at=row['saved_at'].isoformat()) if row else None


async def remember(connection, state, request, card):
    state.seen_cards[card['favorite_id']] = card.copy()
    while len(state.seen_cards) > 200:
        del state.seen_cards[next(iter(state.seen_cards))]
    bearer = request.headers.get('authorization', '').removeprefix('Bearer ')
    await connection.execute('UPDATE miniapp_sessions SET state=$2::jsonb WHERE bearer_hash=$1',
                             token_hash(bearer), json.dumps(state.document, ensure_ascii=False))


def response(revision, card, saved_card):
    return dict(revision=revision, card=card, saved=bool(saved_card), saved_card=saved_card,
                updated=bool(saved_card and card['content_hash'] != saved_card.get('content_hash')))


@router.get('')
async def catalog(request: Request, category: str | None = Query(None, max_length=80),
                  parent: str | None = Query(None, max_length=80), q: str = Query('', max_length=240)):
    async with authority(request) as (connection, _):
        revision, config = await publication(connection)
        entries = handbook_catalog(config)
        words = q.casefold().split()
        entries = [entry for entry in entries if (not category or entry['category'] == category)
                   and (parent is None or entry['parent'] == parent)
                   and all(word in (entry['title'] + '\n' + entry['text']).casefold() for word in words)]
        return dict(revision=revision, categories=categories,
                    entries=[{k: v for k, v in entry.items() if k != 'text'} for entry in entries])


@router.get('/{identifier}')
async def detail(identifier: str, request: Request):
    async with authority(request) as (connection, state):
        revision, config = await publication(connection)
        card = handbook_material(config, identifier)
        if card is None:
            raise HTTPException(404, 'Этот материал больше недоступен.')
        await remember(connection, state, request, card)
        return response(revision, card, await snapshot(connection, state.owner, card['favorite_id']))


class Favorite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    saved: bool = Field(strict=True)
    content_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    replace: bool = Field(default=False, strict=True)


@router.post('/{identifier}/favorite')
async def favorite(identifier: str, body: Favorite, request: Request):
    async with authority(request) as (connection, state):
        if not IDENTIFIER.fullmatch(identifier):
            raise HTTPException(404, 'Этот материал больше недоступен.')
        favorite_id = hashlib.sha256(('advice:' + identifier).encode()).hexdigest()
        old = await snapshot(connection, state.owner, favorite_id)
        revision, config = await publication(connection)
        if not body.saved:
            # Removal is an owner-scoped operation on a saved snapshot. A later
            # publication may remove the material, but cannot trap the favorite.
            await request.app.state.favorites.remove(state.owner, favorite_id, connection)
            return dict(revision=revision, card=old, saved=False, saved_card=None, updated=False)
        card = handbook_material(config, identifier)
        if card is None:
            raise HTTPException(404, 'Этот материал больше недоступен.')
        if body.saved:
            seen = state.seen_cards.get(card['favorite_id'])
            if (not seen or seen.get('section') != 'handbook' or seen.get('node_id') != identifier
                    or body.content_hash is not None and body.content_hash != seen.get('content_hash')):
                raise HTTPException(409, 'Откройте материал заново, чтобы сохранить его версию.')
            if body.replace and seen.get('content_hash') != card['content_hash']:
                raise HTTPException(409, 'Материал обновился. Откройте его заново перед заменой.')
            if old is None or body.replace:
                await request.app.state.favorites.save(state.owner, card['favorite_id'], seen, connection)
        return response(revision, card, await snapshot(connection, state.owner, card['favorite_id']))
