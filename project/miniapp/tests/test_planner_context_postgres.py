"""Prepared information and task materials are durable owner-scoped snapshots."""
import json
from uuid import uuid4

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS
from project.miniapp.tests.test_planner_k1_postgres import post, patch, delete, code


@pytest.mark.asyncio
async def test_context_snapshot_links_retry_and_deletion(contour):
    pool, client, _ = contour
    a, b = [await launch(client, owner) for owner in OWNERS]
    row = await pool.fetchrow('SELECT v.id,v.config FROM scenario_state s JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1')
    from project.llm.services.planner_content import material_catalog
    material = material_catalog(json.loads(row['config']))[0]
    goal_id, task_id = str(uuid4()), str(uuid4())
    body = dict(goal=dict(id=goal_id, title='Сохранённая цель'), confirmed=True,
        context=dict(question='Как начать?', revision=row['id'], material_ids=[material['id']]),
        tasks=[dict(id=task_id, title='Первый шаг', material_ids=[material['id']])])
    saved = await post(client, a, 'commit', body, 'context-save')
    assert saved.status_code == 201, saved.text
    document = saved.json()
    assert document['goal']['context']['question'] == 'Как начать?'
    assert document['goal']['context']['information']['sources']
    task = document['tasks'][0]
    assert task['material_ids'] == [material['id']] and task['links']
    assert (await client.get('/api/planner/goals/'+goal_id, headers=a)).json()['goal'] == document['goal']
    assert (await client.get('/api/planner/goals/'+goal_id, headers=b)).status_code == 404
    changed = await patch(client, a, task_id, dict(expected_version=1, title='Другой заголовок'))
    assert changed.json()['task']['links'] == task['links']
    # Publication changes never invalidate an exact historical operation receipt.
    await pool.execute("UPDATE scenario_state SET published_id=(SELECT min(id) FROM scenario_versions) WHERE id=1")
    assert (await post(client, a, 'commit', body, 'context-save')).json() == document
    assert (await client.get('/api/planner/tasks/'+task_id, headers=a)).json()['task']['links'] == task['links']
    assert (await client.delete('/api/account', headers=a)).status_code == 200
    for table in ('planner_goals', 'planner_tasks', 'planner_operations'):
        assert await pool.fetchval('SELECT count(*) FROM '+table) == 0


@pytest.mark.asyncio
async def test_material_validation_is_atomic_and_strict(contour):
    pool, client, _ = contour
    a = await launch(client, OWNERS[0])
    revision = await pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1')
    body = dict(goal=dict(id=str(uuid4()), title='Цель'), confirmed=True,
        context=dict(question='Вопрос', revision=revision, material_ids=['missing']), tasks=[])
    invalid = await post(client, a, 'commit', body)
    assert invalid.status_code == 422
    body['context']['material_ids'] = []
    body['context']['revision'] = revision + 1
    stale = await post(client, a, 'commit', body)
    assert stale.status_code == 409 and code(stale) == 'materials_changed'
    body['context']['revision'] = revision
    body['context']['material_ids'] = ['same', 'same']
    assert (await post(client, a, 'commit', body)).status_code == 422
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 0
    assert await pool.fetchval('SELECT count(*) FROM planner_operations') == 0


def test_context_wire_schema_accepts_only_material_ids():
    from project.miniapp.planner_api import CommitPlan
    from pydantic import ValidationError
    body = dict(goal=dict(id=str(uuid4()), title='Цель'), confirmed=True,
        context=dict(question='Вопрос', revision=1, material_ids=['known']),
        tasks=[dict(id=str(uuid4()), title='Шаг', material_ids=['known'])])
    assert CommitPlan.model_validate(body).context.question == 'Вопрос'
    body['tasks'][0]['material_ids'] = ['known', 'known']
    with pytest.raises(ValidationError):
        CommitPlan.model_validate(body)


@pytest.mark.asyncio
async def test_pre004_legacy_reads_updates_and_metadata_gate(contour):
    pool, client, _ = contour
    await pool.execute('ALTER TABLE planner_goals DROP COLUMN context')
    await pool.execute('ALTER TABLE planner_tasks DROP COLUMN material_ids,DROP COLUMN material_links')
    await pool.execute("DELETE FROM schema_migrations WHERE version='004_planner_context'")
    a = await launch(client, OWNERS[0])
    task_id = str(uuid4())
    created = await post(client, a, 'tasks', dict(id=task_id, title='Старая задача'))
    assert created.status_code == 201
    assert created.json()['task']['material_ids'] == []
    assert (await patch(client, a, task_id, dict(expected_version=1, title='Обновлено'))).status_code == 200
    revision = await pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1')
    body = dict(goal=dict(id=str(uuid4()), title='Цель'), tasks=[], confirmed=True,
                context=dict(question='Вопрос', revision=revision, material_ids=[]))
    rejected = await post(client, a, 'commit', body)
    assert rejected.status_code == 409 and code(rejected) == 'planner_migration_required'
    assert (await client.delete('/api/account', headers=a)).status_code == 200


@pytest.mark.asyncio
async def test_ai_proposal_return_and_selected_search_save_without_live_provider(contour, monkeypatch):
    from project.llm.services import planner_ai
    from project.miniapp.app import app
    pool, client, _ = contour
    app.state.planner_ai = planner_ai.PlannerAI()
    a, b = [await launch(client, owner) for owner in OWNERS]
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', app.state.token)

    async def fake(*args, **kwargs):
        return json.dumps(dict(status='draft', topic='housing', route_alias='housing',
            material_ids=['hq_state_housing_right']))

    monkeypatch.setattr(planner_ai, 'call_llm', fake)
    response = await client.post('/api/planner/ai/draft-plan', headers=a, json={'text': 'Положена ли мне квартира?'})
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal['information']['sources'] and proposal['context']['revision']
    assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 0
    assert (await client.get('/api/planner/ai/latest', headers=a)).json()['proposal'] == proposal
    assert (await client.get('/api/planner/ai/latest', headers=b)).json()['proposal'] is None
    assert (await client.get('/api/planner/ai/latest')).status_code == 401
    # Skip the first informational action: its answer survives in goal context.
    body = dict(goal=dict(id=str(uuid4()), title=proposal['title']), context=proposal['context'],
                confirmed=True, tasks=[dict(id=str(uuid4()), title='Найти жильё', material_ids=['app_rental'])])
    saved = await post(client, a, 'commit', body, 'selected-search')
    assert saved.status_code == 201, saved.text
    assert saved.json()['goal']['context']['information'] == proposal['information']
    assert saved.json()['tasks'][0]['links'][0]['url'] == '/?section=rental'
    assert (await client.get('/api/planner/ai/latest', headers=a)).json()['proposal'] is None
    assert (await post(client, a, 'commit', body, 'selected-search')).json() == saved.json()


@pytest.mark.asyncio
async def test_source_checked_wage_card_survives_plan_and_favorite(contour):
    pool,client,_=contour
    a=await launch(client,OWNERS[0]);b=await launch(client,OWNERS[1])
    opened=await client.get('/api/handbook/work_wages',headers=a)
    assert opened.status_code==200,opened.text
    card=opened.json()['card']
    favorite=await client.post('/api/handbook/work_wages/favorite',headers=a,json={'saved':True,'content_hash':card['content_hash']})
    assert favorite.status_code==200,favorite.text
    assert 'инспекцию труда' in favorite.json()['saved_card']['text']
    revision=await pool.fetchval('SELECT published_id FROM scenario_state WHERE id=1')
    body=dict(goal=dict(id=str(uuid4()),title='Получить помощь по зарплате'),confirmed=True,
              context=dict(question='Мне не выплатили зарплату',revision=revision,material_ids=['work_wages']),
              tasks=[dict(id=str(uuid4()),title='Изучить памятку Роструда',material_ids=['work_wages'])])
    saved=await post(client,a,'commit',body,'wage-reference-save')
    assert saved.status_code==201,saved.text
    data=saved.json();sources=data['goal']['context']['information']['sources']
    assert any(s['url'].endswith('/instruction/7') for s in sources)
    assert any(s['url'].endswith('/problems') for s in sources)
    assert data['tasks'][0]['links'][0]['url']=='/?handbook=work_wages'
    assert (await client.get('/api/planner/goals/'+body['goal']['id'],headers=a)).json()['goal']==data['goal']
    assert (await client.get('/api/planner/goals/'+body['goal']['id'],headers=b)).status_code==404
