"""Synthetic signed API checks for the stage-05 goal and card path."""
import json
from uuid import uuid4

import pytest

from project.llm.services import planner_ai
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, TOKEN, web


@pytest.mark.asyncio
async def test_priority_keeps_second_goal_and_plan_uses_both_cards(contour, monkeypatch):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)
    calls = []

    async def provider(messages, **options):
        calls.append(messages)
        topic = json.loads(messages[1]['content']).get('selected_topic')
        if topic == 'work':
            return json.dumps({'status': 'draft', 'topic': 'work', 'route_alias': 'work',
                               'material_ids': ['resume_first']})
        if topic == 'housing':
            return json.dumps({'status': 'draft', 'topic': 'housing', 'route_alias': 'rent',
                               'material_ids': ['hq_rent_view']})
        return json.dumps({'status': 'clarify', 'question_id': 'topic_choice',
                           'choice_ids': ['work', 'housing']})

    monkeypatch.setattr(planner_ai, 'call_llm', provider)
    text = 'Ищу комнату в найм и первую работу'
    first = await client.post('/api/planner/ai/find-help-route', headers=headers, json={'text': text})
    assert first.status_code == 200
    assert first.json()['status'] == 'clarify'
    work = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text': text, 'question_id': 'topic_choice', 'answer': 'work',
              'pending_topics': ['work', 'housing']})
    assert work.status_code == 200
    assert work.json()['context']['material_ids'] == []
    assert work.json()['context']['required_resources'] == ['app_work']
    assert [item['id'] for item in work.json()['related_topics']] == ['housing']
    assert '/?section=work' in [item['url'] for item in work.json()['information']['sources']]
    housing = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text': text, 'question_id': 'topic_choice', 'answer': 'housing',
              'pending_topics': ['housing']})
    assert housing.status_code == 200
    assert housing.json()['context']['material_ids'] == ['hq_rent_view']
    assert 'app_rental' in housing.json()['context']['required_resources']
    plan = await client.post('/api/planner/ai/draft-plan', headers=headers,
        json={'text': text, 'selected_material_ids': ['hq_rent_view']})
    assert plan.status_code == 200
    assert plan.json()['context']['material_ids'] == ['hq_rent_view']
    urls = [item['url'] for item in plan.json()['information']['sources']]
    assert '/?handbook=hq_rent_view' in urls
    assert {'app_work', 'app_rental'} <= set(plan.json()['context']['required_resources'])
    assert len(calls) <= 3


@pytest.mark.asyncio
async def test_denial_card_survives_clarification_and_plan(contour, monkeypatch):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)

    async def forbidden(*args, **kwargs):
        raise AssertionError('Specific prepared route must not call the provider')

    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    text = 'Есть проблема с поликлиникой'
    response = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text': text, 'question_id': 'health_kind',
              'answer': 'Меня не приняли без бумажного полиса'})
    assert response.status_code == 200
    result = response.json()
    assert result['context']['material_ids'] == ['care_denial']
    assert '/?handbook=care_denial' in [item['url'] for item in result['information']['sources']]
    planned = await client.post('/api/planner/ai/draft-plan', headers=headers,
        json={'text': text, 'selected_material_ids': ['care_denial']})
    assert planned.status_code == 200
    assert planned.json()['context']['material_ids'] == ['care_denial']


@pytest.mark.asyncio
@pytest.mark.parametrize(('text', 'fact', 'selected', 'expected'), [
    ('Нужна помощь с поликлиникой', 'Меня не приняли без бумажного полиса',
     ['clinic_attachment', 'care_denial'], ['care_denial']),
    ('Нужна бесплатная помощь юриста', 'Что взять на бесплатную встречу с юристом?',
     ['bq_legal_who', 'bq_legal_steps'], ['bq_legal_steps']),
])
async def test_refined_plan_persists_only_concrete_materials(contour, monkeypatch,
                                                           text, fact, selected, expected):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)

    async def forbidden(*args, **kwargs):
        raise AssertionError('Refining selected cards must not call a provider')

    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    response = await client.post('/api/planner/ai/draft-plan', headers=headers,
        json={'text': text, 'description': fact, 'selected_material_ids': selected})
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal['context']['material_ids'] == expected
    body = dict(goal=dict(id=str(uuid4()), title=proposal['title']), confirmed=True,
                context=proposal['context'],
                tasks=[dict(id=str(uuid4()), title=task['title'], material_ids=task['material_ids'])
                       for task in proposal['tasks']])
    saved = await client.post('/api/planner/commit', headers={**headers,
                              'Idempotency-Key': 'refined-plan'}, json=body)
    assert saved.status_code == 201, saved.text
    document = saved.json()
    assert document['goal']['context']['material_ids'] == expected
    assert {identifier for task in document['tasks'] for identifier in task['material_ids']} == set(expected)
    read = await client.get('/api/planner/goals/' + body['goal']['id'], headers=headers)
    assert read.status_code == 200
    assert read.json()['goal'] == document['goal']
    for identifier in expected:
        assert (await client.get('/api/handbook/' + identifier, headers=headers)).status_code == 200
