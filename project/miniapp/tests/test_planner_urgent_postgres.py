"""Urgent read-only responses preserve signed owner/publication guards."""
import pytest
from project.miniapp.tests.test_planner_postgres import contour,launch,OWNERS,web
from project.llm.services.planner_ai import PlannerAI

@pytest.mark.asyncio
async def test_urgent_signed_response_never_reserves_or_commits_a_plan(contour,monkeypatch):
    pool,client,_=contour
    monkeypatch.setenv('PLANNER_AI_ENABLED','1');monkeypatch.setenv('LLM_ENABLED','1')
    monkeypatch.setattr(web.app.state,'planner_ai',PlannerAI(),raising=False)
    headers=await launch(client,OWNERS[0])
    assert (await client.post('/api/planner/ai/find-help-route',json={'text':'В квартире пахнет газом'})).status_code==401
    for route in ['find-help-route','draft-plan']:
        result=await client.post('/api/planner/ai/'+route,headers=headers,json={'text':'В квартире пахнет газом'})
        assert result.status_code==200,result.text
        assert result.json()['information']['priority']=='urgent'
        assert result.json()['tasks']==[] and 'goal_id' not in result.json()
    assert await pool.fetchval('SELECT count(*) FROM planner_proposal_policies')==0
    assert await pool.fetchval('SELECT count(*) FROM planner_goals')==0
    assert (await client.get('/api/planner/ai/latest',headers=headers)).json()['proposal'] is None

@pytest.mark.asyncio
async def test_publication_change_rejects_withdrawn_urgent_guidance(contour,monkeypatch):
    pool,client,_=contour;headers=await launch(client,OWNERS[0])
    class Withdrawn:
        async def propose(self,*args,**kwargs):
            await pool.execute('UPDATE scenario_state SET published_id=(SELECT min(id) FROM scenario_versions) WHERE id=1')
            return {'status':'information','information':{'text':'Old snapshot','sources':[],'priority':'urgent'},'tasks':[]}
    monkeypatch.setattr(web.app.state,'planner_ai',Withdrawn(),raising=False)
    result=await client.post('/api/planner/ai/find-help-route',headers=headers,json={'text':'Газ'})
    assert result.status_code==409 and result.json()['detail']['code']=='materials_changed'
