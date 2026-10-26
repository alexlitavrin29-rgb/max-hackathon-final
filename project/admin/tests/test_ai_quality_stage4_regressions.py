"""Regressions discovered with frozen, previously unseen stage-4 requests."""
import asyncio
import json
from pathlib import Path

import pytest

from project.llm.services import planner_retrieval
from project.llm.services import planner_ai
from project.llm.services.planner_content import candidates, material_catalog


@pytest.fixture(scope='module')
def config():
    return json.loads((Path(__file__).resolve().parents[3] / 'submission/scenario.json').read_text(encoding='utf-8'))['config']


@pytest.mark.parametrize('query', [
    'Снимать не собираюсь, хочу проверить, есть ли я в списке на жильё',
    'Арендовать квартиру не хочу, проверяю очередь на жильё',
])
def test_negated_rental_does_not_hide_state_housing(config, query):
    ids = [item['id'] for item in candidates(config, query)]
    assert 'hq_state_housing_apply' in ids
    assert 'hq_rent_view' not in ids
    assert planner_retrieval.required_resources({}, query) == []


@pytest.mark.parametrize('query', [
    'Не пахнет газом, хочу узнать, где смотреть тариф за коммуналку',
    'Газом не пахнет, нужен тариф за ЖКХ',
])
def test_negated_gas_does_not_displace_tariff(config, query):
    ids = [item['id'] for item in candidates(config, query)]
    assert 'hq_utilities_tariffs' in ids
    assert 'hq_household_gas' not in ids
    assert planner_retrieval.urgent_material(config, query, material_catalog(config)) is None


@pytest.mark.parametrize('query', [
    'Течёт сверху и заливает комнату прямо сейчас',
    'С потолка льётся и заливает спальню',
])
def test_active_leak_without_word_water_uses_reviewed_route(config, query):
    assert planner_retrieval.urgent_material(config, query, material_catalog(config)) == 'hq_household_water'


def test_negation_does_not_suppress_second_active_hazard(config):
    query = 'Газом не пахнет, но с потолка льётся и заливает комнату'
    assert planner_retrieval.urgent_material(config, query, material_catalog(config)) == 'hq_household_water'


@pytest.mark.parametrize('query,expected', [
    ('Ребёнок родился, а паспорт я потерял: что делать сначала?', {'childbirth_allowance', 'doc_passport_q3'}),
    ('Составить бюджет и оформить пособие', {'money_budget'}),
])
def test_separate_goals_keep_their_own_shortlist_slot(config, query, expected):
    ids = {item['id'] for item in candidates(config, query)}
    assert expected <= ids


def test_live_provider_secondary_material_field_is_validated_and_combined(config):
    query = 'Ребёнок родился, а паспорт я потерял: что делать сначала?'
    materials = candidates(config, query)
    reply = {'status': 'draft', 'topic': 'benefits', 'route_alias': 'benefits',
             'material_ids': ['childbirth_allowance'], 'secondary_topics': ['documents'],
             'secondary_material_ids': ['doc_passport_q3']}
    selected = planner_ai.validate_reply(json.dumps(reply), 'find_help_route',
                                         materials=materials, intent_question=query)
    assert selected['material_ids'] == ['childbirth_allowance', 'doc_passport_q3']
    reply['secondary_material_ids'] = ['untrusted_id']
    with pytest.raises(ValueError):
        planner_ai.validate_reply(json.dumps(reply), 'find_help_route',
                                  materials=materials, intent_question=query)


def test_generic_work_problem_asks_a_useful_question(config, monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    async def controlled(*args, **kwargs):
        return json.dumps({'status': 'draft', 'topic': 'work',
                           'route_alias': 'work', 'material_ids': []})
    monkeypatch.setattr(planner_ai, 'call_llm', controlled)
    result = asyncio.run(planner_ai.PlannerAI().propose('stage4-work', 'find_help_route',
                                                       'Проблема с работой', config=config))
    assert result['status'] == 'clarify'
    assert result['question_id'] == 'work_kind'
