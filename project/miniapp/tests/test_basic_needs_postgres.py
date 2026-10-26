"""New reviewed routes through signed API, real disposable PG and saved plan."""
from uuid import uuid4

import pytest

from project.llm.services import planner_ai
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, TOKEN, web


@pytest.mark.asyncio
async def test_basic_needs_cards_actions_plan_and_draft(contour, monkeypatch):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setenv('MAX_BOT_TOKEN', TOKEN)
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)
    async def forbidden(*args, **kwargs):
        raise AssertionError('Prepared basic needs must not call a provider')
    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    unknown = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text': 'Какое жильё мне положено?', 'question_id': 'housing_kind', 'answer': 'Не знаю'})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()['context']['material_ids'] == ['housing_start']
    query = 'Нужны деньги и жильё'
    response = await client.post('/api/planner/ai/find-help-route', headers=headers, json={'text': query})
    assert response.status_code == 200, response.text
    ids = response.json()['context']['material_ids']
    assert set(ids) == {'income_start', 'housing_start'}
    priority = await client.post('/api/planner/ai/find-help-route', headers=headers,
        json={'text': query, 'question_id': 'topic_choice', 'answer': 'work',
              'pending_topics': ['work', 'housing']})
    assert priority.status_code == 200, priority.text
    assert priority.json()['context']['material_ids'] == ['income_start']
    assert 'housing' in {t['id'] for t in priority.json()['related_topics']}
    for identifier in ids:
        card = await client.get('/api/handbook/' + identifier, headers=headers)
        assert card.status_code == 200, card.text
        assert card.json()['card']['id'] == identifier
    planned = await client.post('/api/planner/ai/draft-plan', headers=headers,
                                json={'text': query, 'selected_material_ids': ids})
    assert planned.status_code == 200, planned.text
    proposal = planned.json()
    assert set(proposal['context']['required_resources']) == {'app_work', 'app_rental'}
    state = {'kind': 'consultation', 'original': query, 'selectedMaterials': ids,
             'phase': 'info', 'questionID': None, 'answers': []}
    saved_draft = await client.put('/api/planner/draft', headers=headers,
                                   json={'expected_version': 0, 'state': state})
    assert saved_draft.status_code == 200, saved_draft.text
    assert (await client.get('/api/planner/draft', headers=headers)).json()['draft']['state'] == state
    body = {'goal': {'id': proposal['goal_id'], 'title': query}, 'confirmed': True,
            'context': proposal['context'], 'tasks': [dict(id=str(uuid4()), title=t['title'],
                material_ids=t['material_ids']) for t in proposal['tasks']]}
    committed = await client.post('/api/planner/commit', headers={**headers, 'Idempotency-Key': 'basic-needs'}, json=body)
    assert committed.status_code == 201, committed.text
    result = committed.json()
    assert set(result['goal']['context']['material_ids']) == set(ids)
    assert {t['required_resource'] for t in result['tasks'] if t.get('required_resource')} == {'app_work', 'app_rental'}
    read = await client.get('/api/planner/goals/' + proposal['goal_id'], headers=headers)
    assert read.status_code == 200 and read.json()['goal'] == result['goal']
