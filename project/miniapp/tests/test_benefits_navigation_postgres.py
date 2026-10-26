"""Isolated signed API, real PostgreSQL, benefits choice → card → draft → plan."""
from uuid import uuid4
import pytest
from project.llm.services import planner_ai
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, TOKEN, web


@pytest.mark.asyncio
async def test_benefit_choices_and_restored_plan(contour, monkeypatch):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)
    async def forbidden(*args, **kwargs):
        raise AssertionError('No provider in controlled integration')
    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    query = 'Какие льготы положены выпускнику детдома?'
    response = await client.post('/api/planner/ai/find-help-route', headers=headers, json={'text':query})
    assert response.status_code == 200, response.text
    assert response.json()['question_id'] == 'benefits_category'
    assert len(response.json()['choices']) == 13
    choose = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text':query, 'question_id':'benefits_category', 'answer':'education'})
    assert choose.status_code == 200 and choose.json()['question_id'] == 'benefits_education'
    history = [{'question_id':'benefits_category', 'answer':'education'}]
    answer = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text':query, 'question_id':'benefits_education', 'answer':'university_quota', 'history':history})
    assert answer.status_code == 200, answer.text
    assert answer.json()['context']['material_ids'] == ['university_quota']
    card = await client.get('/api/handbook/university_quota', headers=headers)
    assert card.status_code == 200
    assert card.json()['card']['text'] == answer.json()['information']['text']
    mixed = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text':'Какие льготы и хочу найти работу', 'question_id':'benefits_education',
              'answer':'university_quota', 'pending_topics':['benefits','work']})
    assert mixed.status_code == 200, mixed.text
    assert {t['id'] for t in mixed.json()['related_topics']} == {'work'}
    # Persist the open second-level question and restore it before choosing.
    state = dict(kind='consultation', original=query, selectedMaterials=[], phase='clarify',
                 operation='find-help-route', questionID='benefits_education', answers=history,
                 question=choose.json()['question'], choices=choose.json()['choices'])
    saved = await client.put('/api/planner/draft', headers=headers, json={'expected_version':0, 'state':state})
    assert saved.status_code == 200, saved.text
    assert (await client.get('/api/planner/draft',headers=headers)).json()['draft']['state'] == state
    identifiers = ['university_quota', 'vocational_training']
    plan = await client.post('/api/planner/ai/draft-plan', headers=headers,
        json={'text':query, 'selected_material_ids':identifiers, 'history':history})
    assert plan.status_code == 200, plan.text
    proposal = plan.json()
    assert set(proposal['context']['material_ids']) == set(identifiers)
    assert not proposal['context']['required_resources']
    body = dict(goal={'id':proposal['goal_id'], 'title':query}, confirmed=True, context=proposal['context'],
        tasks=[dict(id=str(uuid4()), title=t['title'], material_ids=t['material_ids']) for t in proposal['tasks']])
    committed = await client.post('/api/planner/commit', headers={**headers, 'Idempotency-Key':'benefits-navigation'}, json=body)
    assert committed.status_code == 201, committed.text
    read = await client.get('/api/planner/goals/'+proposal['goal_id'], headers=headers)
    assert read.status_code == 200
    assert set(read.json()['goal']['context']['material_ids']) == set(identifiers)
    tasks = await client.get('/api/planner/tasks', headers=headers, params={'goal_id':proposal['goal_id']})
    assert tasks.status_code == 200
    assert {i for t in tasks.json()['tasks'] for i in t['material_ids']} == set(identifiers)
