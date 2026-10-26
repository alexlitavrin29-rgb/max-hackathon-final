"""Opaque, bounded owner drafts; no planner mutations or provider calls."""
import json
import math
from typing import Any, Annotated
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, field_validator

from .planner_api import StrictBody, authenticated_owner
from project.llm.services.planner_store import fail, json_value, serial


MAX_STATE_BYTES = 18_000
MAX_STATE_DEPTH = 12
router = APIRouter(prefix='/api/planner', tags=['Planner drafts'])


async def sweep_expired(pool):
    """Bounded retention cleanup; caller schedules it without startup migration."""
    async with pool.acquire(timeout=2) as connection:
        async with connection.transaction():
            await connection.execute("SET LOCAL statement_timeout = '2s'")
            tables_exist = await connection.fetchval("""SELECT
                to_regclass('planner_drafts') IS NOT NULL
                AND to_regclass('schema_migrations') IS NOT NULL""")
            if not tables_exist or not await connection.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version='005_planner_drafts')"):
                return
            # READ COMMITTED rechecks the predicate after a concurrent edit's row
            # lock is released, so cleanup cannot remove a newly renewed draft.
            await connection.execute('DELETE FROM planner_drafts WHERE expires_at<=now()')
            if await connection.fetchval("SELECT to_regclass('planner_proposal_policies') IS NOT NULL"):
                # Retain the UUID tombstone so an expired AI plan cannot become
                # a manual plan; its question and resource choices need not live on.
                await connection.execute("""UPDATE planner_proposal_policies
                    SET question='',required_resources='[]'::jsonb
                    WHERE expires_at<=now()
                      AND (question<>'' OR required_resources<>'[]'::jsonb)""")


def validate_state(value):
    if type(value) is not dict or value.get('kind') not in ('consultation', 'editor'):
        raise ValueError('Draft state object with consultation/editor kind required')
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_STATE_DEPTH:
            raise ValueError('Draft state nesting exceeds limit')
        if type(item) is dict:
            if any(type(key) is not str or '\x00' in key or key in ('__proto__', 'prototype', 'constructor') for key in item):
                raise ValueError('Invalid draft state key')
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError('Draft state must contain JSON values')
        elif type(item) is float and not math.isfinite(item):
            raise ValueError('Draft state numbers must be finite')
        elif type(item) is str and '\x00' in item:
            # PostgreSQL JSONB cannot represent U+0000 text.
            raise ValueError('Draft state text cannot contain null characters')
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError):
        raise ValueError('Invalid draft JSON') from None
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError('Draft state exceeds 18000 bytes')
    return value


class PutDraft(StrictBody):
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    state: dict[str, Any]

    validate_state = field_validator('state', mode='before')(validate_state)


class DeleteDraft(StrictBody):
    expected_version: Annotated[int, Field(strict=True, ge=1)]


async def current_draft(connection, owner):
    tables_exist = await connection.fetchval("""SELECT to_regclass('planner_drafts') IS NOT NULL
        AND to_regclass('planner_draft_versions') IS NOT NULL
        AND to_regclass('schema_migrations') IS NOT NULL""")
    if not tables_exist or not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version='005_planner_drafts')"):
        fail('planner_migration_required', 409)
    epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    # authenticated_owner holds the owner advisory lock across cleanup and mutation.
    await connection.execute('''DELETE FROM planner_drafts WHERE owner=$1
        AND (account_epoch<>$2 OR expires_at<=now())''', owner, epoch)
    row = await connection.fetchrow('SELECT * FROM planner_drafts WHERE owner=$1 AND account_epoch=$2', owner, epoch)
    return epoch, row


def document(row):
    return {'draft': dict(version=row['version'], state=json_value(row['state']),
                          expires_at=serial(row['expires_at'])) if row else None}


async def renew_proposal_policy(connection, owner, epoch, state):
    """A real draft edit keeps its still-live AI reservation for the same TTL."""
    if state['kind'] == 'editor':
        identifier = state.get('goalID')
    else:
        proposal = state.get('proposal')
        identifier = proposal.get('goal_id') if type(proposal) is dict else None
    if type(identifier) is not str or not 1 <= len(identifier) <= 36:
        return
    try:
        goal_id = UUID(identifier)
    except ValueError:
        return
    if await connection.fetchval("SELECT to_regclass('planner_proposal_policies') IS NOT NULL"):
        await connection.execute('''UPDATE planner_proposal_policies
            SET expires_at=now()+interval '24 hours'
            WHERE owner=$1 AND account_epoch=$2 AND goal_id=$3 AND expires_at>now()''',
            owner, epoch, goal_id)


@router.get('/draft')
async def get_draft(request: Request):
    async with authenticated_owner(request) as (connection, owner):
        _, row = await current_draft(connection, owner)
        return document(row)


@router.put('/draft')
async def put_draft(body: PutDraft, request: Request):
    async with authenticated_owner(request) as (connection, owner):
        epoch, row = await current_draft(connection, owner)
        version = row['version'] if row else 0
        if body.expected_version != version:
            fail('draft_conflict', 409)
        encoded = json.dumps(body.state, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if row:
            # PostgreSQL JSON equality preserves scalar types (Python equates true and 1).
            same = await connection.fetchval('SELECT state=$2::jsonb FROM planner_drafts WHERE owner=$1', owner, encoded)
            if same:
                return document(row)
        row = await connection.fetchrow('''INSERT INTO planner_drafts
            (owner,account_epoch,state,expires_at,updated_at)
            VALUES($1,$2,$3::jsonb,now()+interval '24 hours',now())
            ON CONFLICT(owner) DO UPDATE SET state=EXCLUDED.state,
                version=EXCLUDED.version,expires_at=EXCLUDED.expires_at,
                updated_at=EXCLUDED.updated_at
            RETURNING *''', owner, epoch, encoded)
        await renew_proposal_policy(connection, owner, epoch, body.state)
        return document(row)


@router.delete('/draft')
async def delete_draft(body: DeleteDraft, request: Request):
    async with authenticated_owner(request) as (connection, owner):
        _, row = await current_draft(connection, owner)
        if not row or row['version'] != body.expected_version:
            fail('draft_conflict', 409)
        await connection.execute('DELETE FROM planner_drafts WHERE owner=$1', owner)
        return {'deleted': True}
