"""Planner v1 HTTP schemas. Authority is the checked signed MAX shared session."""
from contextlib import asynccontextmanager
from datetime import date
import os
import json
import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import (AwareDatetime, BaseModel, BeforeValidator, ConfigDict,
                      Field, PlainSerializer, field_validator, model_validator, AfterValidator)

from project.llm.services.planner_store import PlannerConflict, PlannerStore, SUPPORTED_TIMEZONES


def trimmed(value):
    return value.strip() if isinstance(value, str) else value


def title_text(value):
    if isinstance(value, str) and '\x00' in value:
        raise ValueError('Недопустимый символ в названии.')
    return trimmed(value)


RFC3339 = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})')


def datetime_string(value):
    # AwareDatetime also accepts epoch strings; enforce the wire syntax first.
    if not isinstance(value, str) or RFC3339.fullmatch(value) is None:
        raise ValueError('RFC3339 string with explicit offset required')
    return value


def calendar_date_string(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is None:
        raise ValueError('YYYY-MM-DD calendar date required')
    date.fromisoformat(value)
    return value


CalendarDate = Annotated[str, BeforeValidator(calendar_date_string), Field(json_schema_extra={'format': 'date'})]
GoalTitle = Annotated[str, Field(strict=True, min_length=1, max_length=160), BeforeValidator(title_text)]
TaskTitle = Annotated[str, Field(strict=True, min_length=1, max_length=240), BeforeValidator(title_text)]
InputTime = Annotated[AwareDatetime, BeforeValidator(datetime_string)]
Version = Annotated[int, Field(strict=True, ge=1)]
Reminder = Literal[0, 60] | None
Key = Annotated[str, Header(pattern=r'^[A-Za-z0-9._:-]{1,128}$')]
Status = Literal['not_started', 'in_progress', 'done']
# Preserve K0 receipt timestamp bytes while retaining date-time OpenAPI schemas.
OutputTime = Annotated[AwareDatetime, PlainSerializer(lambda value: value.isoformat(), return_type=str), Field(json_schema_extra={'format': 'date-time'})]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateGoal(StrictBody):
    id: UUID
    title: GoalTitle


class ExpectedVersion(StrictBody):
    expected_version: Version


class UpdateGoal(ExpectedVersion):
    title: GoalTitle


def unique_materials(value):
    if len(set(value)) != len(value):
        raise ValueError('Duplicate material IDs')
    return value


MaterialIds = Annotated[list[Annotated[str, Field(strict=True, min_length=1, max_length=100)]],
                        Field(max_length=3), AfterValidator(unique_materials)]


class PlanContext(StrictBody):
    question: GoalTitle
    material_ids: MaterialIds
    revision: Annotated[int, Field(strict=True, ge=1)]
    required_resources: list[Literal['app_work', 'app_rental', 'app_help']] = Field(default_factory=list, max_length=3)
    policy: str | None = Field(default=None, strict=True, max_length=1000)


class CommitTask(StrictBody):
    id: UUID
    title: TaskTitle
    material_ids: MaterialIds = Field(default_factory=list)
    status: Status = 'not_started'
    due_at: InputTime | None = None
    due_date: CalendarDate | None = None
    timezone: str | None = Field(default=None, strict=True, max_length=100)
    timezone_confirmed: bool = Field(default=False, strict=True)
    remind_before_minutes: Reminder = None

    @field_validator('remind_before_minutes', mode='before')
    @classmethod
    def strict_reminder(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError('Reminder must be null, 0 or 60')
        return value


class CreateTask(CommitTask):
    goal_id: UUID | None = None


class UpdateTask(ExpectedVersion):
    # Non-null types with omitted defaults distinguish omitted vs explicit null.
    title: TaskTitle = None
    goal_id: UUID | None = None
    status: Status = None
    due_at: InputTime | None = None
    due_date: CalendarDate | None = None
    timezone: str | None = Field(default=None, strict=True, max_length=100)
    timezone_confirmed: bool = Field(default=False, strict=True)
    remind_before_minutes: Reminder = None
    notification_override_at: InputTime | None = None

    strict_reminder = field_validator('remind_before_minutes', mode='before')(CommitTask.strict_reminder.__func__)

    @model_validator(mode='after')
    def has_change(self):
        if not self.model_fields_set - {'expected_version'}:
            raise ValueError('At least one task field required')
        return self


class CommitPlan(StrictBody):
    goal: CreateGoal
    tasks: list[CommitTask] = Field(max_length=50)
    context: PlanContext | None = None
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def strict_confirmed(cls, value):
        if value is not True:
            raise ValueError('Explicit confirmation required')
        return value

    @model_validator(mode='after')
    def unique_tasks(self):
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError('Duplicate task IDs')
        return self


class EditPlanTask(CommitTask):
    expected_version: Version | None = None


class RemovedTask(ExpectedVersion):
    id: UUID


class EditPlan(StrictBody):
    expected_version: Version
    title: GoalTitle
    tasks: list[EditPlanTask] = Field(max_length=50)
    removed_tasks: list[RemovedTask] = Field(default_factory=list, max_length=100)
    confirmed: Literal[True]

    strict_confirmed = field_validator('confirmed', mode='before')(CommitPlan.strict_confirmed.__func__)

    @model_validator(mode='after')
    def unique_tasks(self):
        ids = [task.id for task in self.tasks] + [task.id for task in self.removed_tasks]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate task IDs')
        return self


class MaterialLink(BaseModel):
    title: str
    url: str


class InformationSource(MaterialLink):
    id: str
    description: str | None = None
    anchors: list[str] | None = Field(default=None, max_length=12)


class InformationPoint(BaseModel):
    text: str
    sources: list[InformationSource]


class InformationDetail(InformationPoint):
    title: str


class InformationDocument(BaseModel):
    text: str
    sources: list[InformationSource]
    note: str | None = None
    intro: str | None = None
    points: list[InformationPoint] | None = Field(default=None, max_length=5)
    details: list[InformationDetail] | None = None
    priority: Literal['normal', 'urgent'] = 'normal'


class ContextDocument(PlanContext):
    information: InformationDocument


class GoalDocument(BaseModel):
    context: ContextDocument | None = None
    id: UUID
    title: str
    version: int
    created_at: OutputTime
    updated_at: OutputTime


class TaskDocument(BaseModel):
    plan_order: int = Field(default=0, ge=0, json_schema_extra={'readOnly': True})
    required_resource: Literal['app_work', 'app_rental', 'app_help'] | None = None
    material_ids: list[str] = Field(default_factory=list)
    links: list[MaterialLink] = Field(default_factory=list)
    id: UUID
    goal_id: UUID | None
    title: str
    status: Status
    due_at: OutputTime | None
    due_date: CalendarDate | None = None
    timezone: str | None
    timezone_confirmed: bool
    remind_before_minutes: Reminder
    notification_override_at: OutputTime | None
    notify_at: OutputTime | None
    schedule_generation: int
    version: int
    completed_at: OutputTime | None
    created_at: OutputTime
    updated_at: OutputTime


class GoalResponse(BaseModel):
    goal: GoalDocument


class TaskResponse(BaseModel):
    task: TaskDocument


class CommitResponse(GoalResponse):
    tasks: list[TaskDocument]


class GoalPage(BaseModel):
    goals: list[GoalDocument]
    next_after: UUID | None


class TaskPage(BaseModel):
    tasks: list[TaskDocument]
    next_after: UUID | None
    as_of: OutputTime


class DeletedResponse(BaseModel):
    deleted: Literal[True]
    id: UUID


class TimezonesResponse(BaseModel):
    timezones: list[str]


class PlannerErrorCode(BaseModel):
    code: str


class PlannerError(BaseModel):
    detail: PlannerErrorCode | str | list[dict]


class HelpRouteDocument(BaseModel):
    alias: str
    title: str
    available: bool
    url: str | None
    manual_path: str


class HelpRoutesResponse(BaseModel):
    revision: int
    routes: list[HelpRouteDocument]


class TaskLinksResponse(BaseModel):
    task_url: str | None
    plan_url: str | None


router = APIRouter(prefix='/api/planner', tags=['Planner v1'], responses={
    401: {'model': PlannerError, 'description': 'MAX session expired or invalid'},
    404: {'model': PlannerError, 'description': 'Disabled planner or missing/foreign entity'},
    409: {'model': PlannerError, 'description': 'Idempotency, ID, version or nonempty-goal conflict'},
    422: {'model': PlannerError, 'description': 'Invalid shape, time, timezone or reminder'},
    403: {'model': PlannerError, 'description': 'Foreign Origin on mutation'},
    413: {'model': PlannerError, 'description': 'Mutation body exceeds 24000 bytes'},
})
store = PlannerStore()


class AIProposal(StrictBody):
    text: GoalTitle
    situation: Literal['basic_needs'] | None = None
    description: Annotated[str, Field(strict=True, min_length=1, max_length=500), BeforeValidator(trimmed)] | None = None
    question_id: str | None = Field(default=None, strict=True, max_length=40)
    answer: Annotated[str, Field(strict=True, min_length=1, max_length=500), BeforeValidator(trimmed)] | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    selected_material_ids: list[str] = Field(default_factory=list, max_length=3)
    pending_topics: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def clarification(self):
        from project.llm.services.planner_ai import CATALOG, QUESTIONS
        if (self.question_id is None) != (self.answer is None):
            raise ValueError('Question and answer required together')
        if self.question_id is not None and self.question_id not in QUESTIONS:
            raise ValueError('Unknown question')
        if self.question_id == 'topic_choice' and self.answer not in CATALOG:
            raise ValueError('Unknown category')
        if self.situation is not None and (self.question_id != 'topic_choice' or self.answer != 'help'):
            raise ValueError('Situation requires the selected help topic')
        if any(set(item) != {'question_id', 'answer'} or item['question_id'] not in QUESTIONS
               or not item['answer'].strip() or len(item['answer']) > 500 for item in self.history):
            raise ValueError('Invalid clarification history')
        if len(set(self.selected_material_ids)) != len(self.selected_material_ids):
            raise ValueError('Duplicate materials')
        if len(set(self.pending_topics)) != len(self.pending_topics) or any(
                topic not in CATALOG for topic in self.pending_topics):
            raise ValueError('Invalid pending topics')
        return self


async def ai_proposal(body, request, operation):
    from project.llm.services.planner_ai import PlannerAI
    # Check authority, then release all DB resources before waiting for a provider.
    async with authenticated_owner(request) as (connection, owner):
        revision, config = await route_publication(connection)
        epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    if not hasattr(request.app.state, 'planner_ai'):
        request.app.state.planner_ai = PlannerAI()
    result = await request.app.state.planner_ai.propose(
        owner, operation, body.text, body.question_id, body.answer, config=config,
        **({'description': body.description} if body.description is not None else {}),
        **({'situation': body.situation} if body.situation is not None else {}),
        history=body.history, selected_material_ids=body.selected_material_ids)
    if result['status'] in {'draft', 'information'} and body.pending_topics:
        from project.llm.services.planner_ai import TOPIC_TITLES
        from project.llm.services.planner_content import candidate_catalog
        active = body.answer if body.question_id == 'topic_choice' else result.get('topic')
        covered = {item['topic'] for item in candidate_catalog(config)
                   if item['id'] in (result.get('context') or {}).get('material_ids', [])}
        related = {item['id']: item for item in result.get('related_topics', [])}
        for name in body.pending_topics:
            if name != active and name not in covered:
                related.setdefault(name, {'id': name, 'title': TOPIC_TITLES[name]})
        if related:
            result['related_topics'] = list(related.values())
    # Expiry/deletion during the external call must not return an old session's draft.
    async with authenticated_owner(request) as (connection, current_owner):
        if owner != current_owner:
            raise HTTPException(401, 'Откройте приложение заново из MAX.')
        if result['status'] in {'draft', 'information'}:
            current_revision, config = await route_publication(connection)
            if current_revision != revision:
                raise HTTPException(409, {'code': 'materials_changed'})
            current_epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
            if current_epoch != epoch:
                raise HTTPException(401, 'Откройте приложение заново из MAX.')
        if result['status'] in {'information', 'clarify'}:
            request.app.state.planner_ai.forget(owner)
        if result['status'] == 'draft':
            from project.llm.services.help_routes import resolve_route
            if 'context' in result:
                result['context']['revision'] = revision
            if 'route_alias' in result:
                result['route'] = resolve_route(config, result.pop('route_alias'), {'role': 'child'})
            current_epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
            if current_epoch != epoch:
                raise HTTPException(401, 'Откройте приложение заново из MAX.')
            if 'context' in result:
                from project.llm.services.planner_policy import issue_policy
                context = result['context']
                context['policy'] = issue_policy(owner, epoch, context['question'], context.get('required_resources', []))
                if not await connection.fetchval("SELECT to_regclass('planner_proposal_policies')"):
                    raise HTTPException(409, {'code': 'planner_migration_required'})
                goal_id = uuid4()
                await connection.execute('''INSERT INTO planner_proposal_policies
                    (owner,account_epoch,goal_id,question,required_resources) VALUES($1,$2,$3,$4,$5::jsonb)''',
                    owner, epoch, goal_id, context['question'], json.dumps(context.get('required_resources', [])))
                result['goal_id'] = str(goal_id)
            if operation == 'draft_plan':
                request.app.state.planner_ai.remember(owner, epoch, result)
    return result


@router.get('/ai/latest')
async def latest_proposal(request: Request):
    async with authenticated_owner(request) as (connection, owner):
        epoch = await connection.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
        ai = getattr(request.app.state, 'planner_ai', None)
        proposal = ai.latest(owner, epoch) if ai else None
        if proposal:
            revision, _ = await route_publication(connection)
            if proposal.get('context', {}).get('revision') != revision:
                ai.forget(owner)
                proposal = None
        return {'proposal': proposal}


@router.post('/ai/draft-plan')
async def draft_plan(body: AIProposal, request: Request):
    return await ai_proposal(body, request, 'draft_plan')


@router.post('/ai/find-help-route')
async def find_help_route(body: AIProposal, request: Request):
    return await ai_proposal(body, request, 'find_help_route')


@asynccontextmanager
async def authenticated_owner(request):
    from .app import shared_owner, owner_transaction, checked_shared_session
    if os.getenv('PLANNER_ENABLED') != '1':
        raise HTTPException(404, 'Not Found')
    if request.app.state.preview or not getattr(request.app.state, 'shared', None):
        raise HTTPException(401, 'Откройте приложение заново из MAX.')
    initial = await shared_owner(request)
    async with owner_transaction(initial.owner) as connection:
        state = await checked_shared_session(request, connection)
        if state.owner != initial.owner:
            raise HTTPException(401, 'Откройте приложение заново из MAX.')
        try:
            yield connection, state.owner
        except PlannerConflict as exc:
            raise HTTPException(exc.status, {'code': exc.code}) from None


async def mutation(request, operation, body, key, identifier=None):
    async with authenticated_owner(request) as (connection, owner):
        result = await store.mutate(connection, owner, operation,
            mutation_body(body, operation), key, identifier)
        if operation in ('commit', 'edit_plan') and getattr(request.app.state, 'planner_ai', None):
            request.app.state.planner_ai.forget(owner)
        return result


def mutation_body(body, operation):
    result = body.model_dump(exclude_unset=operation == 'update_task')
    if result.get('context') is None:
        result.pop('context', None)
    if result.get('context'):
        # Newly added optional fields must not alter pre-policy receipt hashes.
        if not result['context'].get('required_resources'):
            result['context'].pop('required_resources', None)
        if result['context'].get('policy') is None:
            result['context'].pop('policy', None)
    tasks = result.get('tasks', []) if operation in ('commit', 'edit_plan') else [result] if operation == 'create_task' else []
    for index, task in enumerate(tasks):
        # Preserve legacy hashes when the new field was absent. Aggregate edits
        # must retain explicit null so they can clear a saved calendar date.
        explicit_edit_date = operation == 'edit_plan' and 'due_date' in body.tasks[index].model_fields_set
        if task.get('due_date') is None and not explicit_edit_date:
            task.pop('due_date', None)
        if not task.get('material_ids'):
            task.pop('material_ids', None)
    return result


async def route_publication(connection):
    # Reuse the owner's existing transaction: nested pool acquisition can deadlock
    # when concurrent owner reads exhaust the pool while waiting on its lock.
    row = await connection.fetchrow("""SELECT v.id AS revision,v.config
        FROM scenario_state s JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1""")
    return row['revision'], json.loads(row['config'])


@router.get('/routes', response_model=HelpRoutesResponse)
async def help_routes(request: Request):
    from project.llm.services.help_routes import catalog
    async with authenticated_owner(request) as (connection, owner):
        revision, config = await route_publication(connection)
        return {'revision': revision, 'routes': catalog(config)}


@router.get('/tasks/{identifier}/links', response_model=TaskLinksResponse)
async def task_links(identifier: UUID, request: Request):
    from project.llm.services.help_routes import app_url, task_payload
    async with authenticated_owner(request) as (connection, owner):
        if await store.get_task(connection, owner, identifier) is None:
            raise HTTPException(404, {'code': 'not_found'})
        _, config = await route_publication(connection)
        return {'task_url': app_url(config, task_payload(identifier)),
                'plan_url': app_url(config)}


@router.get('/timezones', response_model=TimezonesResponse)
async def timezones(request: Request):
    async with authenticated_owner(request):
        return {'timezones': SUPPORTED_TIMEZONES}


@router.get('/goals', response_model_exclude_unset=True, response_model=GoalPage)
async def list_goals(request: Request, limit: int = Query(default=50, ge=1, le=100), after: UUID | None = None):
    async with authenticated_owner(request) as (connection, owner):
        return await store.list_goals(connection, owner, limit, after)


@router.post('/goals', status_code=201, response_model_exclude_unset=True, response_model=GoalResponse)
async def create_goal(body: CreateGoal, request: Request, idempotency_key: Key):
    return await mutation(request, 'create_goal', body, idempotency_key)


@router.get('/goals/{identifier}', response_model_exclude_unset=True, response_model=GoalResponse)
async def get_goal(identifier: UUID, request: Request):
    async with authenticated_owner(request) as (connection, owner):
        result = await store.get_goal(connection, owner, identifier)
        if result is None:
            raise HTTPException(404, {'code': 'not_found'})
        return result


@router.patch('/goals/{identifier}', response_model_exclude_unset=True, response_model=GoalResponse)
async def update_goal(identifier: UUID, body: UpdateGoal, request: Request, idempotency_key: Key):
    return await mutation(request, 'update_goal', body, idempotency_key, identifier)


@router.delete('/goals/{identifier}', response_model=DeletedResponse)
async def delete_goal(identifier: UUID, body: ExpectedVersion, request: Request, idempotency_key: Key):
    return await mutation(request, 'delete_goal', body, idempotency_key, identifier)


@router.get('/tasks', response_model_exclude_unset=True, response_model=TaskPage)
async def list_tasks(request: Request, limit: int = Query(default=50, ge=1, le=100),
                     after: UUID | None = None, goal_id: UUID | None = None,
                     view: Literal['upcoming','overdue','undated','completed'] | None = None,
                     as_of: InputTime | None = None, calendar_date: CalendarDate | None = None):
    async with authenticated_owner(request) as (connection, owner):
        return await store.list_tasks(connection, owner, limit, after, view, goal_id, as_of, calendar_date)


@router.post('/tasks', status_code=201, response_model_exclude_unset=True, response_model=TaskResponse)
async def create_task(body: CreateTask, request: Request, idempotency_key: Key):
    return await mutation(request, 'create_task', body, idempotency_key)


@router.get('/tasks/{identifier}', response_model_exclude_unset=True, response_model=TaskResponse)
async def get_task(identifier: UUID, request: Request):
    async with authenticated_owner(request) as (connection, owner):
        result = await store.get_task(connection, owner, identifier)
        if result is None:
            raise HTTPException(404, {'code': 'not_found'})
        return result


@router.patch('/tasks/{identifier}', response_model_exclude_unset=True, response_model=TaskResponse)
async def update_task(identifier: UUID, body: UpdateTask, request: Request, idempotency_key: Key):
    return await mutation(request, 'update_task', body, idempotency_key, identifier)


@router.delete('/tasks/{identifier}', response_model=DeletedResponse)
async def delete_task(identifier: UUID, body: ExpectedVersion, request: Request, idempotency_key: Key):
    return await mutation(request, 'delete_task', body, idempotency_key, identifier)


@router.post('/commit', status_code=201, response_model_exclude_unset=True, response_model=CommitResponse)
async def commit_plan(body: CommitPlan, request: Request, idempotency_key: Key):
    return await mutation(request, 'commit', body, idempotency_key)


@router.post('/goals/{identifier}/edit-plan', response_model_exclude_unset=True, response_model=CommitResponse)
async def edit_plan(identifier: UUID, body: EditPlan, request: Request, idempotency_key: Key):
    return await mutation(request, 'edit_plan', body, idempotency_key, identifier)
