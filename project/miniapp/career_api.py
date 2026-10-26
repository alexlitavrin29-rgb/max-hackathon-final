"""Owner-selected career progress, independent of plans and government registries."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .handbook_api import authority

router = APIRouter(prefix='/api/career', tags=['Career'])
CareerStatus = Literal['unknown', 'searching', 'resume', 'interview', 'employed']


class PutCareer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: CareerStatus
    expected_version: int = Field(strict=True, ge=0, le=9223372036854775807)


class CareerDocument(BaseModel):
    status: CareerStatus
    version: int
    updated_at: datetime


class CareerEnvelope(BaseModel):
    career: CareerDocument | None


async def current_career(connection, owner):
    if not await connection.fetchval("""SELECT to_regclass('career_statuses') IS NOT NULL
        AND to_regclass('career_status_versions') IS NOT NULL"""):
        raise HTTPException(503, 'Сохранение статуса пока недоступно. Попробуй позже.')
    epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    await connection.execute('DELETE FROM career_statuses WHERE owner=$1 AND account_epoch<>$2', owner, epoch)
    row = await connection.fetchrow('SELECT status,version,updated_at FROM career_statuses WHERE owner=$1 AND account_epoch=$2', owner, epoch)
    return epoch, row


def document(row):
    return {'career': dict(row) if row else None}


@router.get('', response_model=CareerEnvelope)
async def get_career(request: Request):
    async with authority(request) as (connection, session):
        _, row = await current_career(connection, session.owner)
        return document(row)


@router.put('', response_model=CareerEnvelope)
async def put_career(body: PutCareer, request: Request):
    async with authority(request) as (connection, session):
        epoch, row = await current_career(connection, session.owner)
        if body.expected_version != (row['version'] if row else 0):
            raise HTTPException(409, 'Статус изменён в другом окне. Обнови его перед сохранением.')
        if row and row['status'] == body.status:
            return document(row)
        row = await connection.fetchrow('''INSERT INTO career_statuses(owner,account_epoch,status)
            VALUES($1,$2,$3) ON CONFLICT(owner) DO UPDATE
            SET account_epoch=EXCLUDED.account_epoch,status=EXCLUDED.status,
                version=EXCLUDED.version,updated_at=now()
            RETURNING status,version,updated_at''', session.owner, epoch, body.status)
        return document(row)
