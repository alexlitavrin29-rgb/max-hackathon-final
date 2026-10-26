"""Owner-selected housing progress, independent of plans and government registries."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .handbook_api import authority

router = APIRouter(prefix='/api/housing', tags=['Housing'])
HousingStatus = Literal['unknown', 'not_in_list', 'in_list', 'application_sent',
                        'offered', 'received', 'social_rent']


class PutHousing(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: HousingStatus
    expected_version: int = Field(strict=True, ge=0, le=9223372036854775807)


class HousingDocument(BaseModel):
    status: HousingStatus
    version: int
    updated_at: datetime


class HousingEnvelope(BaseModel):
    housing: HousingDocument | None


async def current_housing(connection, owner):
    if not await connection.fetchval("""SELECT to_regclass('housing_statuses') IS NOT NULL
        AND to_regclass('housing_status_versions') IS NOT NULL"""):
        raise HTTPException(503, 'Сохранение статуса пока недоступно. Попробуй позже.')
    epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    await connection.execute('DELETE FROM housing_statuses WHERE owner=$1 AND account_epoch<>$2', owner, epoch)
    row = await connection.fetchrow('SELECT status,version,updated_at FROM housing_statuses WHERE owner=$1 AND account_epoch=$2', owner, epoch)
    return epoch, row


def document(row):
    return {'housing': dict(row) if row else None}


@router.get('', response_model=HousingEnvelope)
async def get_housing(request: Request):
    async with authority(request) as (connection, session):
        _, row = await current_housing(connection, session.owner)
        return document(row)


@router.put('', response_model=HousingEnvelope)
async def put_housing(body: PutHousing, request: Request):
    async with authority(request) as (connection, session):
        epoch, row = await current_housing(connection, session.owner)
        if body.expected_version != (row['version'] if row else 0):
            raise HTTPException(409, 'Статус изменён в другом окне. Обнови его перед сохранением.')
        if row and row['status'] == body.status:
            return document(row)
        row = await connection.fetchrow('''INSERT INTO housing_statuses(owner,account_epoch,status)
            VALUES($1,$2,$3) ON CONFLICT(owner) DO UPDATE
            SET account_epoch=EXCLUDED.account_epoch,status=EXCLUDED.status,
                version=EXCLUDED.version,updated_at=now()
            RETURNING status,version,updated_at''', session.owner, epoch, body.status)
        return document(row)
