"""Published urgent guidance bypasses model latency without bypassing flags/visibility."""
import copy
import json
from pathlib import Path
import pytest
from project.llm.services import planner_ai
from project.llm.services.planner_content import urgent_response

@pytest.fixture
def config():
    return json.loads(Path('submission/scenario.json').read_text())['config']

@pytest.mark.asyncio
async def test_current_gas_uses_full_reviewed_card_without_provider(config,monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED','1');monkeypatch.setenv('LLM_ENABLED','1')
    async def forbidden(*args,**kwargs):
        raise AssertionError('Urgent card must not wait for the provider')
    monkeypatch.setattr(planner_ai,'call_llm',forbidden)
    service=planner_ai.PlannerAI()
    for operation in ['find_help_route','draft_plan']:
        result=await service.propose('synthetic-owner',operation,'В квартире пахнет газом',config=config)
        assert result['status']=='information' and result['tasks']==[]
        info=result['information'];assert info['priority']=='urgent'
        node=next(n for n in config['nodes'] if n['id']=='hq_household_gas')
        assert info['text']==node['text']
        assert any(s['url']=='/?handbook=hq_household_gas' for s in info['sources'])
        assert result['context']['material_ids']==['hq_household_gas']
        assert service.active==0 and service.calls==[]

@pytest.mark.parametrize('query',['Газом не пахнет, хочу снять квартиру','Пишу реферат о запахе газа','Вчера пахло газом, сейчас всё исправили'])
def test_not_active_hazards_do_not_override_normal_consultation(config,query):
    assert urgent_response(config,query) is None

def test_safety_shortcut_never_promotes_unverified_material(config):
    changed=copy.deepcopy(config)
    next(n for n in changed['nodes'] if n['id']=='hq_household_gas')['content_status']='unverified_reference'
    assert urgent_response(changed,'В квартире пахнет газом') is None

@pytest.mark.asyncio
async def test_disabled_feature_remains_disabled_for_urgent_query(config,monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED','0')
    result=await planner_ai.PlannerAI().propose('owner','find_help_route','В квартире пахнет газом',config=config)
    assert result['reason']=='unavailable'

@pytest.mark.asyncio
async def test_clarification_controls_real_search_intent_without_rewriting_question(config,monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED','1');monkeypatch.setenv('LLM_ENABLED','1')
    async def reply(*args,**kwargs):
            return json.dumps(dict(status='draft',topic='work',route_alias='work',material_ids=['resume_first']))
    monkeypatch.setattr(planner_ai,'call_llm',reply)
    result=await planner_ai.PlannerAI().propose('owner','draft_plan','Хочу разобраться','work_kind','Ищу первую работу',config=config)
    assert result['context']['question']=='Хочу разобраться'
    assert result['context']['required_resources']==['app_work']
