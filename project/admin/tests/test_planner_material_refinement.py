"""A clarified goal replaces provisional cards without losing independent goals."""
import json
from pathlib import Path

import pytest

from project.llm.services import planner_ai


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    async def forbidden(*args, **kwargs):
        raise AssertionError('A plan from reviewed selected cards must not call a provider')
    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    return json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']


@pytest.mark.asyncio
@pytest.mark.parametrize(('text', 'description', 'history', 'selected', 'expected', 'actions'), [
    ('Нужна помощь с поликлиникой', 'Меня не приняли без бумажного полиса', [],
     ['clinic_attachment', 'care_denial'], ['care_denial'], []),
    ('Нужна бесплатная помощь юриста', None,
     [{'question_id': 'help_kind', 'answer': 'Что взять на бесплатную встречу с юристом?'}],
     ['bq_legal_who', 'bq_legal_help', 'bq_legal_steps'], ['bq_legal_steps'], []),
    ('Нужна работа и помощь с поликлиникой', 'Меня не приняли без бумажного полиса', [],
     ['resume_first', 'clinic_attachment', 'care_denial'], ['care_denial', 'resume_first'], ['app_work']),
])
async def test_latest_concrete_goal_removes_provisional_cards(config, text, description, history,
                                                              selected, expected, actions):
    result = await planner_ai.PlannerAI().propose(
        'synthetic-refinement', 'draft_plan', text, config=config,
        description=description, history=history, selected_material_ids=selected)
    assert result['context']['material_ids'] == expected
    linked = {identifier for task in result['tasks'] for identifier in task.get('material_ids', [])}
    assert linked == set(expected) | set(actions)
    assert result['context']['required_resources'] == actions
    rejected = set(selected) - set(expected)
    assert not any(source['url'].removeprefix('/?handbook=') in rejected
                   for source in result['information']['sources'])


@pytest.mark.asyncio
async def test_independently_requested_same_topic_goal_survives_refinement(config):
    result = await planner_ai.PlannerAI().propose(
        'synthetic-refinement', 'draft_plan',
        'Хочу прикрепиться к поликлинике и обжаловать отказ без бумажного полиса',
        config=config, description='Мне отказали без бумажного полиса',
        selected_material_ids=['clinic_attachment', 'care_denial'])
    assert set(result['context']['material_ids']) == {'clinic_attachment', 'care_denial'}


@pytest.mark.asyncio
async def test_no_new_concrete_fact_preserves_previously_agreed_cards(config):
    result = await planner_ai.PlannerAI().propose(
        'synthetic-refinement', 'draft_plan', 'Нужна помощь с поликлиникой',
        config=config, description='Не знаю',
        selected_material_ids=['clinic_attachment', 'care_denial'])
    assert set(result['context']['material_ids']) == {'clinic_attachment', 'care_denial'}
