"""One explicitly saved, authenticated resume per owner; no model or network calls."""
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from typing import Annotated
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .handbook_api import authority

router = APIRouter(prefix='/api/resume', tags=['Resume'])
ShortText = Annotated[str, StringConstraints(strict=True, max_length=240)]
LongText = Annotated[str, StringConstraints(strict=True, max_length=4000)]


class ResumeState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: ShortText = ''
    contacts: Annotated[str, StringConstraints(strict=True, max_length=1000)] = ''
    city: ShortText = ''
    desired_role: ShortText = ''
    education: LongText = ''
    experience: LongText = ''
    no_experience: bool = Field(default=False, strict=True)
    skills: LongText = ''
    about: LongText = ''

    @field_validator('*', mode='after')
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str):
            if any((ord(c) < 32 and c not in '\n\t\r') or 0xD800 <= ord(c) <= 0xDFFF for c in value):
                raise ValueError('Недопустимый символ в тексте.')
            return value.strip()
        return value


class PutResume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=0, le=9223372036854775807)
    state: ResumeState


class ResumeDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1, le=9223372036854775807)
    state: ResumeState
    updated_at: datetime


class ResumeEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resume: ResumeDocument | None


async def current_resume(connection, owner):
    ready = await connection.fetchval("""SELECT to_regclass('planner_resumes') IS NOT NULL
        AND to_regclass('planner_resume_versions') IS NOT NULL""")
    if not ready:
        raise HTTPException(503, 'Сохранение резюме пока недоступно. Попробуйте позже.')
    epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    await connection.execute('DELETE FROM planner_resumes WHERE owner=$1 AND account_epoch<>$2', owner, epoch)
    row = await connection.fetchrow('SELECT * FROM planner_resumes WHERE owner=$1 AND account_epoch=$2', owner, epoch)
    return epoch, row


def document(row):
    return {'resume': {'version': row['version'], 'state': json.loads(row['state']),
                       'updated_at': row['updated_at'].isoformat()} if row else None}


async def delete_owner_resume(connection, owner):
    """Compatible with deployments where the explicit resume migration is absent."""
    if await connection.fetchval("SELECT to_regclass('planner_resumes') IS NOT NULL"):
        await connection.execute('DELETE FROM planner_resumes WHERE owner=$1', owner)


@router.get('', response_model=ResumeEnvelope)
async def get_resume(request: Request):
    async with authority(request) as (connection, session):
        _, row = await current_resume(connection, session.owner)
        return document(row)


@router.put('', response_model=ResumeEnvelope)
async def put_resume(body: PutResume, request: Request):
    async with authority(request) as (connection, session):
        epoch, row = await current_resume(connection, session.owner)
        if body.expected_version != (row['version'] if row else 0):
            raise HTTPException(409, 'Резюме изменилось в другом окне. Откройте сохранённую версию перед повторным сохранением.')
        state = body.state.model_dump()
        if row and json.loads(row['state']) == state:
            return document(row)
        row = await connection.fetchrow('''INSERT INTO planner_resumes(owner,account_epoch,state)
            VALUES($1,$2,$3::jsonb) ON CONFLICT(owner) DO UPDATE
            SET account_epoch=EXCLUDED.account_epoch,state=EXCLUDED.state,
                version=EXCLUDED.version,updated_at=now() RETURNING *''',
            session.owner, epoch, json.dumps(state, ensure_ascii=False))
        return document(row)


def resume_pdf(state):
    """Escape all user text; ReportLab handles wrapping, pagination and font embedding."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    font = 'ResumeDejaVu'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).parent / 'static/fonts/resume-DejaVuSans.ttf')))
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=45, leftMargin=45,
                            topMargin=42, bottomMargin=42, title='Резюме', author='')
    body = ParagraphStyle('ResumeBody', fontName=font, fontSize=10, leading=15,
                          alignment=TA_LEFT, splitLongWords=True, spaceAfter=9)
    heading = ParagraphStyle('ResumeHeading', parent=body, fontSize=12, leading=17,
                             textColor=colors.HexColor('#263950'), spaceBefore=12, keepWithNext=True)
    title = ParagraphStyle('ResumeTitle', parent=body, fontSize=19, leading=25, spaceAfter=13)
    def safe(value):
        return escape(str(value)).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')
    parts = [Paragraph(safe(state.get('name') or 'Резюме'), title)]
    for key, label in [('desired_role', 'Желаемая работа'), ('contacts', 'Контакты'), ('city', 'Город'),
                       ('education', 'Образование'), ('experience', 'Опыт'), ('skills', 'Навыки'), ('about', 'О себе')]:
        value = state.get(key, '')
        if key == 'experience' and state.get('no_experience'):
            value = 'Пока нет опыта работы.'
        if value:
            parts.extend([Paragraph(label, heading), Paragraph(safe(value), body)])
    parts.append(Spacer(1, 6))
    doc.build(parts)
    return output.getvalue()


@router.get('/pdf', response_class=Response, responses={200: {
    'description': 'Saved resume PDF with embedded Cyrillic font',
    'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}}})
async def get_pdf(request: Request):
    async with authority(request) as (connection, session):
        _, row = await current_resume(connection, session.owner)
        if not row:
            raise HTTPException(404, 'Сначала сохраните резюме.')
        state = json.loads(row['state'])
    # Only the authenticated saved snapshot is exported; browser drafts never enter this route.
    from starlette.concurrency import run_in_threadpool
    pdf = await run_in_threadpool(resume_pdf, state)
    return Response(pdf, media_type='application/pdf', headers={
        'Content-Disposition': 'attachment; filename="resume.pdf"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
