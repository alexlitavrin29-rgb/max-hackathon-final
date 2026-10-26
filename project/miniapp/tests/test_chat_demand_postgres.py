"""Real isolated PostgreSQL, signed API; no provider or production messages."""
from uuid import uuid4
import pytest
from project.llm.services import planner_ai
from project.miniapp.tests.test_planner_postgres import contour,launch,OWNERS,TOKEN,web


@pytest.mark.asyncio
@pytest.mark.parametrize('second_goal',['gas_connection','utility_subsidy'])
async def test_extract_restore_and_two_gas_goals(contour,monkeypatch,second_goal):
    _,client,_=contour
    headers=await launch(client,OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED','1');monkeypatch.setenv('LLM_ENABLED','1')
    monkeypatch.setenv('MAX_BOT_TOKEN',TOKEN)
    monkeypatch.setattr(web.app.state,'planner_ai',planner_ai.PlannerAI(),raising=False)
    async def forbidden(*a,**kw):raise AssertionError('Provider forbidden')
    monkeypatch.setattr(planner_ai,'call_llm',forbidden)
    async def ask(query,**kw):
        response=await client.post('/api/planner/ai/find-help-route',headers=headers,json=dict(text=query,**kw))
        assert response.status_code==200,response.text
        return response.json()
    query='Нужна выписка'
    choice=await ask(query)
    assert choice['question_id']=='benefits_extract'
    assert {c['id'] for c in choice['choices']}=={'egrn_extract','residence_extract','deregistration'}
    state=dict(kind='consultation',original=query,selectedMaterials=[],phase='clarify',operation='find-help-route',questionID=choice['question_id'],question=choice['question'],choices=choice['choices'],answers=[])
    saved=await client.put('/api/planner/draft',headers=headers,json=dict(expected_version=0,state=state))
    assert saved.status_code==200,saved.text
    restored=(await client.get('/api/planner/draft',headers=headers)).json()['draft']['state']
    assert restored==state
    answer=await ask(query,question_id=restored['questionID'],answer='egrn_extract')
    assert answer['context']['material_ids']==['egrn_extract']
    card=await client.get('/api/handbook/egrn_extract',headers=headers)
    assert card.status_code==200 and card.json()['card']['text']==answer['information']['text']
    query=('Хочу подключить газ к дому и оформить субсидию на газификацию' if second_goal=='gas_connection'
           else 'Нужна субсидия на ЖКУ и субсидия на газовое оборудование')
    first=await ask(query);assert first['question_id']=='federal_priority'
    answer=await ask(query,question_id=first['question_id'],answer='gas_subsidy')
    identifiers=['gas_subsidy',second_goal]
    assert answer['context']['material_ids']==identifiers
    plan=await client.post('/api/planner/ai/draft-plan',headers=headers,json=dict(text=query,selected_material_ids=identifiers))
    assert plan.status_code==200,plan.text
    proposal=plan.json();assert proposal['context']['material_ids']==identifiers
    assert not proposal['context']['required_resources']
    body=dict(goal=dict(id=proposal['goal_id'],title=query),confirmed=True,context=proposal['context'],tasks=[dict(id=str(uuid4()),title=t['title'],material_ids=t['material_ids']) for t in proposal['tasks']])
    committed=await client.post('/api/planner/commit',headers={**headers,'Idempotency-Key':'chat-demand'},json=body)
    assert committed.status_code==201,committed.text
    read=await client.get('/api/planner/goals/'+proposal['goal_id'],headers=headers)
    assert read.status_code==200 and read.json()['goal']['context']['material_ids']==identifiers
    tasks=await client.get('/api/planner/tasks',headers=headers,params=dict(goal_id=proposal['goal_id']))
    assert tasks.status_code==200
    assert {i for t in tasks.json()['tasks'] for i in t['material_ids']}==set(identifiers)
