"""Server grounding for real model proposals with adjacent and invented cards."""
import json
from pathlib import Path

import pytest

from project.llm.services import planner_ai
from project.llm.services.planner_content import candidate_catalog, enrich


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    return json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']


@pytest.mark.parametrize(('query', 'topic', 'ids', 'expected'), [
    ('В поликлинике отказали без бумажного полиса, что делать?', 'health',
     ['oms_policy', 'clinic_attachment', 'care_denial'], ['care_denial']),
    ('Что взять на бесплатную встречу с юристом?', 'help',
     ['bq_legal_who', 'bq_legal_help', 'bq_legal_steps'], ['bq_legal_steps']),
    ('Мне нужна бесплатная правовая помощь, кто принимает?', 'help',
     ['bq_legal_who', 'bq_legal_help', 'bq_legal_steps'], ['bq_legal_who']),
])
def test_adjacent_cards_do_not_replace_concrete_goal(config, query, topic, ids, expected):
    raw = json.dumps(dict(status='draft', topic=topic, route_alias=topic,
                          material_ids=ids))
    normalized = planner_ai.normalize_material_selection(raw, candidate_catalog(config), query, None)
    assert json.loads(normalized)['material_ids'] == expected


def test_second_topic_is_preserved_but_not_repeated_after_selection(config):
    materials = candidate_catalog(config)
    query = 'Ребёнок родился, а паспорт я потерял: что делать сначала?'
    raw = json.dumps(dict(status='draft', topic='documents', route_alias='documents',
                          material_ids=['doc_lost_start', 'doc_passport_q3', 'childbirth_allowance']))
    initial = json.loads(planner_ai.normalize_material_selection(raw, materials, query, None))
    assert initial['material_ids'] == ['doc_passport_q3', 'childbirth_allowance']
    assert initial['secondary_topics'] == ['benefits']
    selected = json.loads(planner_ai.normalize_material_selection(raw, materials, query, 'documents'))
    assert selected['material_ids'] == ['doc_passport_q3']


def test_plan_starts_with_the_specific_refusal_after_clarification(config):
    result = enrich(config, dict(topic='health',
                                 material_ids=['clinic_attachment', 'care_denial']),
                    'Нужна помощь с поликлиникой',
                    intent_question='Нужна помощь с поликлиникой\nМеня не приняли без бумажного полиса')
    assert result['context']['material_ids'] == ['care_denial', 'clinic_attachment']
    assert result['tasks'][0]['title'] == 'Изучить выбранные материалы'
    assert result['tasks'][1]['material_ids'] == ['care_denial']


@pytest.mark.asyncio
async def test_actual_second_topic_continuation_keeps_passport_card(config, monkeypatch):
    query = 'Ребёнок родился, а паспорт я потерял: что делать сначала?'
    async def reply(*_args, **_kwargs):
        return json.dumps(dict(status='draft', topic='documents', route_alias='documents',
                               material_ids=['doc_passport_q3', 'doc_lost_start', 'childbirth_allowance']))
    monkeypatch.setattr(planner_ai, 'call_llm', reply)
    service = planner_ai.PlannerAI()
    first = await service.propose('synthetic-owner', 'find_help_route', query, config=config)
    assert first['context']['material_ids'] == ['doc_passport_q3', 'childbirth_allowance']
    assert [item['id'] for item in first['related_topics']] == ['benefits']
    second = await service.propose('synthetic-owner', 'find_help_route', query,
                                   'topic_choice', 'documents', config=config,
                                   selected_material_ids=['childbirth_allowance'])
    assert second['context']['material_ids'] == ['doc_passport_q3']


@pytest.mark.asyncio
async def test_missing_model_secondary_topic_stays_available(config, monkeypatch):
    async def reply(*_args, **_kwargs):
        return json.dumps(dict(status='draft', topic='documents', route_alias='documents',
                               material_ids=['doc_passport_q3']))
    monkeypatch.setattr(planner_ai, 'call_llm', reply)
    result = await planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route',
        'Ребёнок родился, а паспорт я потерял: что делать сначала?', config=config)
    assert result['context']['material_ids'] == ['doc_passport_q3']
    assert [item['id'] for item in result['related_topics']] == ['benefits']


@pytest.mark.asyncio
async def test_deferred_goal_can_follow_a_first_card(config, monkeypatch):
    async def reply(*_args, **_kwargs):
        return json.dumps(dict(status='draft', topic='housing', route_alias='housing',
                               material_ids=['hq_rent_view', 'hq_rent_contract'],
                               secondary_topics=['work']))
    monkeypatch.setattr(planner_ai, 'call_llm', reply)
    result = await planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route', 'Ищу комнату в найм и первую работу', config=config)
    assert result['context']['material_ids'] == ['hq_rent_view']
    assert [item['id'] for item in result['related_topics']] == ['work']


@pytest.mark.asyncio
async def test_general_question_does_not_repeat_a_known_refusal(config, monkeypatch):
    async def reply(*_args, **_kwargs):
        return json.dumps(dict(status='clarify', topic='health', question_id='health_kind'))
    monkeypatch.setattr(planner_ai, 'call_llm', reply)
    result = await planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route',
        'В поликлинике отказали без бумажного полиса, что делать?', config=config)
    assert result['context']['material_ids'] == ['care_denial']


@pytest.mark.asyncio
async def test_wrong_card_recovers_only_when_goal_is_grounded(config, monkeypatch):
    async def reply(*_args, **_kwargs):
        return json.dumps(dict(status='draft', topic='housing', route_alias='housing',
                               material_ids=['housing_docs_left']))
    monkeypatch.setattr(planner_ai, 'call_llm', reply)
    result = await planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route',
        'Снимать не собираюсь, хочу проверить, есть ли я в списке на жильё', config=config)
    assert result['context']['material_ids'] == ['hq_state_housing_apply']
    async def nonexistent(*_args, **_kwargs):
        return json.dumps(dict(status='draft', topic='housing', route_alias='housing',
                               material_ids=['made_up_housing']))
    monkeypatch.setattr(planner_ai, 'call_llm', nonexistent)
    result = await planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route', 'Неизвестный вопрос про жильё', config=config)
    assert result['status'] == 'unsupported' and result['reason'] == 'unavailable'
