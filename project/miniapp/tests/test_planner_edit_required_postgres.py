"""Atomic editing, mandatory resource authority, and legacy receipt compatibility."""
from uuid import UUID, uuid4, uuid5

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, TOKEN
from project.miniapp.tests.test_planner_k1_postgres import post, patch, delete, code


def task_input(task):
    keys = ('id', 'title', 'material_ids', 'status', 'due_at', 'timezone',
            'timezone_confirmed', 'remind_before_minutes')
    return {**{key: task[key] for key in keys}, 'expected_version': task['version']}


async def edit(client, headers, goal, tasks, removed=(), key='edit-plan'):
    return await post(client, headers, f"goals/{goal['id']}/edit-plan", dict(
        expected_version=goal['version'], title='Изменённая цель', tasks=tasks,
        removed_tasks=list(removed), confirmed=True), key)


async def required_plan(pool, client, headers, monkeypatch):
    from project.llm.services.planner_policy import issue_policy
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    owner = str(OWNERS[0])
    epoch = await pool.fetchval('SELECT epoch FROM account_epochs WHERE owner=$1', owner) or 0
    question = 'Найти работу'
    context = dict(question=question, material_ids=[], required_resources=['app_work'],
        revision=await pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1'),
        policy=issue_policy(owner, epoch, question, ['app_work']))
    goal_id = str(uuid4())
    body = dict(goal=dict(id=goal_id, title=question), context=context, tasks=[], confirmed=True)
    result = await post(client, headers, 'commit', body, 'required-commit')
    assert result.status_code == 201, result.text
    return result.json(), body


@pytest.mark.asyncio
async def test_missing_mandatory_step_added_and_locked(contour, monkeypatch):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    saved, body = await required_plan(pool, client, headers, monkeypatch)
    task = saved['tasks'][0]
    assert task['id'] == str(uuid5(UUID(saved['goal']['id']), 'app_work'))
    assert task['required_resource'] == 'app_work'
    assert task['material_ids'] == ['app_work'] and task['links'][0]['url'] == '/?section=work'
    assert code(await patch(client, headers, task['id'], dict(expected_version=1, title='Подмена'))) == 'required_task_locked'
    assert code(await delete(client, headers, task['id'])) == 'required_task_locked'
    changed = await patch(client, headers, task['id'], dict(expected_version=1, status='done'))
    assert changed.status_code == 200 and changed.json()['task']['status'] == 'done'
    assert (await post(client, headers, 'commit', body, 'required-commit')).json() == saved


@pytest.mark.asyncio
async def test_policy_tampering_owner_binding_and_atomic_failure(contour, monkeypatch):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    saved, body = await required_plan(pool, client, a, monkeypatch)
    body['goal']['id'] = str(uuid4())
    result = await post(client, b, 'commit', body, 'foreign-policy')
    assert result.status_code == 422 and code(result) == 'invalid_policy'
    body['context']['required_resources'] = ['app_help']
    result = await post(client, a, 'commit', body, 'changed-policy')
    assert result.status_code == 422 and code(result) == 'invalid_policy'
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 1


@pytest.mark.asyncio
async def test_edit_required_step_cannot_be_removed_or_renamed(contour, monkeypatch):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    saved, _ = await required_plan(pool, client, headers, monkeypatch)
    goal, task = saved['goal'], saved['tasks'][0]
    removal = await edit(client, headers, goal, [], [dict(id=task['id'], expected_version=1)])
    assert removal.status_code == 409 and code(removal) == 'required_task_locked'
    replacement = task_input(task)
    replacement['material_ids'] = []
    changed = await edit(client, headers, goal, [replacement])
    assert changed.status_code == 409 and code(changed) == 'required_task_locked'
    valid = await edit(client, headers, goal, [{**task_input(task), 'status': 'done'}])
    assert valid.status_code == 200, valid.text
    assert valid.json()['goal']['version'] == 2
    assert valid.json()['goal']['context'] == goal['context']
    assert valid.json()['tasks'][0]['links'] == task['links']
    assert valid.json()['tasks'][0]['required_resource'] == 'app_work'


@pytest.mark.asyncio
async def test_atomic_edit_versions_receipt_and_concurrent_addition(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    body = dict(goal=dict(id=str(uuid4()), title='Цель'), tasks=[dict(id=str(uuid4()), title='Первый')], confirmed=True)
    saved = (await post(client, headers, 'commit', body)).json()
    goal, first = saved['goal'], saved['tasks'][0]
    added = (await post(client, headers, 'tasks', dict(id=str(uuid4()), goal_id=goal['id'], title='Из другой вкладки'), 'concurrent')).json()['task']
    stale = await edit(client, headers, goal, [task_input(first)])
    assert stale.status_code == 409 and code(stale) == 'stale_version'
    tasks = [{**task_input(first), 'title': 'Первый изменён'}, dict(id=str(uuid4()), title='Новый')]
    removed = [dict(id=added['id'], expected_version=1)]
    response = await edit(client, headers, goal, tasks, removed)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['goal']['version'] == 2 and len(result['tasks']) == 2
    assert result['tasks'][0]['version'] == 2
    assert (await edit(client, headers, goal, tasks, removed)).json() == result
    assert (await client.get('/api/planner/tasks/'+added['id'], headers=headers)).status_code == 404
    # A fresh key with stale versions cannot alter any row.
    rejected = await edit(client, headers, goal, tasks, removed, 'stale-edit')
    assert rejected.status_code == 409
    assert (await client.get('/api/planner/goals/'+goal['id'], headers=headers)).json()['goal'] == result['goal']


def test_legacy_commit_hash_and_edit_shape():
    from pydantic import ValidationError
    from project.miniapp.planner_api import CommitPlan, EditPlan, mutation_body
    from project.llm.services.planner_store import request_hash
    body = dict(goal=dict(id=str(uuid4()), title='Цель'), confirmed=True,
                context=dict(question='Вопрос', material_ids=[], revision=1), tasks=[])
    assert request_hash(mutation_body(CommitPlan.model_validate(body), 'commit')) == request_hash(body)
    edit_body = dict(expected_version=1, title='Цель', tasks=[], removed_tasks=[], confirmed=True)
    assert EditPlan.model_validate(edit_body).tasks == []
    with pytest.raises(ValidationError):
        EditPlan.model_validate({**edit_body, 'confirmed': 1})
    task = dict(id=str(uuid4()), title='Шаг', required_resource='app_work')
    with pytest.raises(ValidationError):
        EditPlan.model_validate({**edit_body, 'tasks': [task]})


@pytest.mark.asyncio
async def test_edit_schedule_failure_rolls_back_and_confirmation_clears_draft(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    owner = str(OWNERS[0])

    async def save_draft():
        await pool.execute("""INSERT INTO planner_drafts(owner,account_epoch,state,version,expires_at)
            VALUES($1,0,'{"kind":"editor"}',1,now()+interval '24 hours')""", owner)

    await save_draft()
    body = dict(goal=dict(id=str(uuid4()), title='Цель'),
        tasks=[dict(id=str(uuid4()), title='Первый'), dict(id=str(uuid4()), title='Второй')], confirmed=True)
    saved_response = await post(client, headers, 'commit', body)
    assert saved_response.status_code == 201, saved_response.text
    saved = saved_response.json()
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 0
    await save_draft()
    first, second = [task_input(task) for task in saved['tasks']]
    first['title'] = 'Первая правка'
    second.update(due_at='2000-01-01T10:00:00+00:00', timezone='UTC', timezone_confirmed=True,
                  remind_before_minutes=0)
    failed = await edit(client, headers, saved['goal'], [first, second], key='invalid-schedule')
    assert failed.status_code == 422 and code(failed) == 'notification_in_past'
    assert (await client.get('/api/planner/tasks/'+first['id'], headers=headers)).json()['task'] == saved['tasks'][0]
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 1
    valid = await edit(client, headers, saved['goal'], [first, task_input(saved['tasks'][1])])
    assert valid.status_code == 200, valid.text
    assert await pool.fetchval('SELECT count(*) FROM planner_drafts') == 0


@pytest.mark.asyncio
async def test_pre006_legacy_mutation_and_required_policy_gate(contour, monkeypatch):
    from project.llm.services.planner_policy import issue_policy
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    await pool.execute('ALTER TABLE planner_tasks DROP COLUMN required_resource')
    body = dict(goal=dict(id=str(uuid4()), title='Старая цель'),
                tasks=[dict(id=str(uuid4()), title='Старая задача')], confirmed=True)
    saved = await post(client, headers, 'commit', body, 'old-schema')
    assert saved.status_code == 201, saved.text
    assert (await patch(client, headers, body['tasks'][0]['id'], dict(expected_version=1, status='done'))).status_code == 200
    context = dict(question='Работа', revision=await pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1'),
                   material_ids=[], required_resources=['app_work'])
    context['policy'] = issue_policy(str(OWNERS[0]), 0, context['question'], context['required_resources'])
    blocked = await post(client, headers, 'commit', {**body, 'goal': dict(id=str(uuid4()), title='Новая цель'), 'context': context}, 'new-schema-required')
    assert blocked.status_code == 409 and code(blocked) == 'planner_migration_required'
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1


@pytest.mark.asyncio
async def test_registered_identity_enforces_resources_when_context_stripped(contour):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    for mode in ('no_context', 'unsigned_context', 'expired'):
        goal_id = str(uuid4())
        await pool.execute("""INSERT INTO planner_proposal_policies(owner,account_epoch,goal_id,question,required_resources,expires_at)
            VALUES($1,0,$2,'Найти работу','["app_work"]',now()+$3::text::interval)""",
            str(OWNERS[0]), UUID(goal_id), '-1 hour' if mode == 'expired' else '1 hour')
        manual = await post(client, headers, 'goals', dict(id=goal_id, title='Ручной обход'), 'manual-'+mode)
        assert manual.status_code == 409 and code(manual) == 'reserved_plan'
        body = dict(goal=dict(id=goal_id, title='Цель'), tasks=[], confirmed=True)
        if mode == 'unsigned_context':
            body['context'] = dict(question='Подменённый вопрос', material_ids=[],
                revision=await pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1'))
        result = await post(client, headers, 'commit', body, mode)
        if mode == 'expired':
            assert result.status_code == 409 and code(result) == 'policy_expired'
        else:
            assert result.status_code == 201, result.text
            assert result.json()['goal']['context']['question'] == 'Найти работу'
            assert result.json()['goal']['context']['required_resources'] == ['app_work']
            assert result.json()['tasks'][0]['required_resource'] == 'app_work'
    assert (await client.delete('/api/account', headers=headers)).status_code == 200
    assert await pool.fetchval('SELECT count(*) FROM planner_proposal_policies') == 0


@pytest.mark.asyncio
async def test_required_only_goal_deletable_but_individual_step_locked(contour, monkeypatch):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    saved, _ = await required_plan(pool, client, headers, monkeypatch)
    goal, task = saved['goal'], saved['tasks'][0]
    assert code(await delete(client, headers, task['id'])) == 'required_task_locked'
    ordinary = (await post(client, headers, 'tasks', dict(id=str(uuid4()), title='Обычное дело', goal_id=goal['id']))).json()['task']
    blocked = await delete(client, headers, goal['id'], kind='goals')
    assert blocked.status_code == 409 and code(blocked) == 'goal_not_empty'
    assert (await delete(client, headers, ordinary['id'])).status_code == 200
    removed = await delete(client, headers, goal['id'], kind='goals')
    assert removed.status_code == 200
    assert (await client.get('/api/planner/tasks/'+task['id'], headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_required_auto_insertion_respects_final_task_limit(contour, monkeypatch):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    _, body = await required_plan(pool, client, headers, monkeypatch)
    body['goal']['id'] = str(uuid4())
    body['tasks'] = [dict(id=str(uuid4()), title='Обычный шаг') for _ in range(50)]
    rejected = await post(client, headers, 'commit', body, 'too-many-after-required')
    assert rejected.status_code == 422 and code(rejected) == 'task_limit'
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 1
