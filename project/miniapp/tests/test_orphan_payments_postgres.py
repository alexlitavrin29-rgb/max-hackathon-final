"""Actual isolated PostgreSQL: card → response → plan → draft → commit → read."""
from uuid import uuid4
import pytest
from project.llm.services import planner_ai
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, TOKEN, web


@pytest.mark.asyncio
async def test_payments_survive_saved_plan_and_draft(contour, monkeypatch):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)
    async def forbidden(*args, **kwargs):raise AssertionError('No provider in payment integration')
    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    response = await client.post('/api/planner/ai/find-help-route', headers=headers,
                                json={'text':'Какие выплаты положены выпускнику детдома?'})
    assert response.status_code == 200, response.text
    assert response.json()['context']['material_ids'] == ['orphan_payments']
    unknown = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text':'Нужны выплаты','question_id':'benefits_kind','answer':'Не знаю'})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()['context']['material_ids'] == ['orphan_payments']
    ids = ['study_books', 'orphan_unemployment']
    for identifier in ids:
        card = await client.get('/api/handbook/' + identifier, headers=headers)
        assert card.status_code == 200 and card.json()['card']['actions']
    query = 'Хочу пособие на учебники и пособие по безработице'
    plan = await client.post('/api/planner/ai/draft-plan', headers=headers,
                            json={'text':query,'selected_material_ids':ids})
    assert plan.status_code == 200, plan.text
    proposal = plan.json()
    assert set(proposal['context']['material_ids']) == set(ids)
    assert not proposal['context']['required_resources']
    state = dict(kind='consultation',original=query,selectedMaterials=ids,phase='info',questionID=None,answers=[])
    saved = await client.put('/api/planner/draft',headers=headers,json={'expected_version':0,'state':state})
    assert saved.status_code == 200, saved.text
    assert (await client.get('/api/planner/draft',headers=headers)).json()['draft']['state'] == state
    body = dict(goal={'id':proposal['goal_id'],'title':query},confirmed=True,context=proposal['context'],
        tasks=[dict(id=str(uuid4()),title=t['title'],material_ids=t['material_ids']) for t in proposal['tasks']])
    saved = await client.post('/api/planner/commit',headers={**headers,'Idempotency-Key':'orphan-payments'},json=body)
    assert saved.status_code == 201, saved.text
    result = saved.json()
    assert set(result['goal']['context']['material_ids']) == set(ids)
    assert {i for t in result['tasks'] for i in t['material_ids']} == set(ids)
    read = await client.get('/api/planner/goals/' + proposal['goal_id'],headers=headers)
    assert read.status_code == 200 and read.json()['goal'] == result['goal']
