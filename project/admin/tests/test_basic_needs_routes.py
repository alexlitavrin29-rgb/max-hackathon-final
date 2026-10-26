"""General needs lead to reviewed options and actions without a provider."""
import asyncio
import json
from pathlib import Path

import pytest

from project.llm.services import planner_ai
from project.llm.services.handbook import handbook_material
from project.llm.services.planner_content import candidate_catalog


CONFIG = json.loads(Path('submission/scenario.json').read_text(encoding='utf8'))['config']


def ask(monkeypatch, text, *, raw='{"status":"unsupported"}', **kwargs):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    calls = []
    async def controlled(*args, **options):
        calls.append(True)
        return raw
    monkeypatch.setattr(planner_ai, 'call_llm', controlled)
    operation = kwargs.pop('operation', 'find_help_route')
    result = asyncio.run(planner_ai.PlannerAI().propose('basic-needs-test', operation,
                                                       text, config=CONFIG, **kwargs))
    return result, calls


@pytest.mark.parametrize('text', ['как достать деньги', 'Хочу получить денег',
    'Как заработать?', 'Где можно заработать деньги на жизнь?', 'Мне нужны деньги',
    'Как я заработаю деньги без опыта?', 'Где заработаем денег на жизнь?',
    'Как получить деньги на жизнь?', 'Хочу получать деньги легально',
    'Где я получу деньги на жизнь?', 'Где достану денег?',
    'Как добыть деньги?', 'Как зарабатывать деньги?',
    'Как получить доход?', 'Хочу получать доход легально',
    'Получить деньги на жизнь', 'Заработать денег',
    'Как найти источник дохода?', 'На что мне жить?'])
def test_income_options(monkeypatch, text):
    result, calls = ask(monkeypatch, text)
    assert not calls
    assert result['context']['material_ids'] == ['income_start']
    assert 'app_work' in result['context']['required_resources']
    assert '/?section=work' in {item['url'] for item in result['information']['sources']}
    assert 'выплат' in result['information']['text'].lower()


@pytest.mark.parametrize('text', ['хочу получить жилье и квартиру', 'Как получить квартиру',
    'Какое жильё мне положено?', 'Ищу где жить', 'Как найти квартиру?', 'У меня нет своего жилья'])
def test_housing_options(monkeypatch, text):
    result, calls = ask(monkeypatch, text)
    assert not calls
    assert result['context']['material_ids'] == ['housing_start']
    assert 'app_rental' in result['context']['required_resources']
    assert 'государствен' in handbook_material(CONFIG, 'housing_start')['text'].lower()


def test_unknown_housing_answer_is_useful(monkeypatch):
    result, calls = ask(monkeypatch, 'Какое жильё мне положено?',
                        question_id='housing_kind', answer='Не знаю')
    assert not calls
    assert result['status'] == 'draft'
    assert result['context']['material_ids'] == ['housing_start']
    assert 'app_rental' in result['context']['required_resources']


def test_no_home_keeps_urgent_help_and_housing_options(monkeypatch):
    result, calls = ask(monkeypatch, 'Мне сегодня негде жить и переночевать')
    assert not calls and result['information']['priority'] == 'urgent'
    assert result['context']['material_ids'] == ['shelter']
    urls = {item['url'] for item in result['information']['sources']}
    assert {'/?section=help', '/?section=rental', '/?handbook=housing_start'} <= urls


def test_money_and_housing_survive_plan(monkeypatch):
    query = 'Нужны деньги и жильё'
    response, calls = ask(monkeypatch, query)
    assert not calls
    assert set(response['context']['material_ids']) == {'income_start', 'housing_start'}
    plan, calls = ask(monkeypatch, query, operation='draft_plan',
                      selected_material_ids=response['context']['material_ids'])
    assert not calls
    assert {t.get('required_resource') for t in plan['tasks']} >= {'app_work', 'app_rental'}
    assert {i for t in plan['tasks'] for i in t['material_ids']} >= {'income_start', 'housing_start'}


def test_housing_payment_meanings(monkeypatch):
    result, calls = ask(monkeypatch, 'Выплата за квартиру')
    assert not calls and result['status'] == 'clarify'
    assert 'сертификат' in result['question'].lower() and 'жку' in result['question'].lower()
    assert result.get('information')


@pytest.mark.parametrize('text,topic,identifier', [
    ('Уже в списке на жильё, меня пропустили', 'housing', 'hq_state_housing_delay'),
    ('Переезжаю в другой регион, что будет с квартирной очередью?', 'housing', 'housing_move'),
    ('Не хочу снимать жильё, уточню право на госжильё', 'housing', 'hq_state_housing_right'),
    ('Как вернуть деньги за покупку?', 'help', None),
    ('Не ищу работу, какие меры социальной помощи доступны?', 'benefits', 'social_assistance'),
    ('Как получить загранпаспорт?', 'documents', None),
])
def test_specific_or_different_goals_are_not_overridden(monkeypatch, text, topic, identifier):
    raw = json.dumps(dict(status='draft', topic=topic, route_alias=topic, material_ids=[identifier])) if identifier else '{"status":"unsupported"}'
    result, _ = ask(monkeypatch, text, raw=raw)
    ids = (result.get('context') or {}).get('material_ids', [])
    assert not {'income_start', 'housing_start'}.intersection(ids)
    assert 'app_work' not in (result.get('context') or {}).get('required_resources', [])


@pytest.mark.parametrize('text', [
    'Как не потерять заработанные деньги?',
    'На что потратить заработанные деньги?',
    'Как хранить заработанные летом деньги?',
    'Получаю деньги нерегулярно, как разложить траты на четыре недели?',
    'Получил деньги, как сохранить их до конца месяца?',
    'Как хранить полученные деньги?',
    'Достал деньги, как распределить траты?',
    'Куда положить добытые деньги?',
])
def test_already_earned_money_is_not_a_new_income_goal(monkeypatch, text):
    result, _ = ask(monkeypatch, text)
    context = result.get('context') or {}
    assert 'income_start' not in context.get('material_ids', [])
    assert 'app_work' not in context.get('required_resources', [])


@pytest.mark.parametrize('text', [
    'Получаю деньги нерегулярно, как разложить траты на четыре недели?',
    'Получаю доход нерегулярно, как распланировать расходы?',
    'Начал получать доход, как вести бюджет?',
    'Уже стал зарабатывать деньги, как их распределить?',
    'В пятницу получу деньги, как их распределить?',
    'Не хочу заработать деньги, хочу сохранить накопленное.',
    'Мне не нужен доход, хочу распределить имеющиеся деньги.',
])
def test_existing_irregular_income_keeps_budget_answer_and_plan(monkeypatch, text):
    raw = json.dumps(dict(status='draft', topic='budget', route_alias='budget',
                          material_ids=['money_budget']))
    result, _ = ask(monkeypatch, text, raw=raw)
    assert result['context']['material_ids'] == ['money_budget']
    assert 'app_work' not in result['context']['required_resources']
    assert result['information']['sources']
    plan, calls = ask(monkeypatch, text, operation='draft_plan',
                      selected_material_ids=result['context']['material_ids'])
    assert not calls
    assert plan['tasks']
    assert {i for task in plan['tasks'] for i in task['material_ids']} == {'money_budget'}
    assert 'app_work' not in plan['context']['required_resources']


@pytest.mark.parametrize('text', [
    'Получаю доход нерегулярно, как распланировать расходы?',
    'Как хранить полученный доход?',
    'Получил доход, хочу сохранить его до конца месяца',
    'Начал получать доход, как вести бюджет?',
    'Уже стал зарабатывать деньги, как их распределить?',
    'В пятницу получу деньги, как их распределить?',
    'Не хочу заработать деньги, хочу сохранить накопленное.',
    'Мне не нужен доход, хочу распределить имеющиеся деньги.',
])
def test_existing_income_is_not_a_new_income_goal(text):
    from project.llm.services.adjacent_guides import basic_need_goals
    from project.llm.services.planner_content import required_searches
    assert 'income' not in basic_need_goals(text)
    assert 'app_work' not in required_searches({}, text)


@pytest.mark.parametrize('text', [
    'Получаю доход нерегулярно, но хочу найти другой источник дохода',
    'Уже стал зарабатывать деньги, но мне нужно найти дополнительный доход',
    'В пятницу получу деньги, но хочу устроиться на новую работу',
    'Уже зарабатываю деньги, хочу найти другую работу',
    'Не хочу заработать деньги, хочу найти другую работу',
    'Мне не нужен доход от подработки, но хочу найти другой источник дохода',
    'Не хочу зарабатывать деньги сейчас, но позже хочу получить доход',
])
def test_new_work_or_income_goal_survives_existing_income(text):
    from project.llm.services.planner_content import required_searches
    assert 'app_work' in required_searches({}, text)


@pytest.mark.parametrize('text', [
    'Планирую заработать денег',
    'Собираюсь получить доход',
    'Мне бы заработать денег',
    'Можно заработать денег?',
])
def test_explicit_income_request_keeps_common_intention_forms(text):
    from project.llm.services.adjacent_guides import basic_need_goals
    from project.llm.services.planner_content import required_searches
    assert 'income' in basic_need_goals(text)
    assert 'app_work' in required_searches({}, text)


@pytest.mark.parametrize('text', [
    'Как не потерять заработанные деньги?',
    'Как хранить полученный доход?',
    'Получаю деньги нерегулярно, как разложить траты на четыре недели?',
    'Уже стал зарабатывать деньги, как их распределить?',
    'В пятницу получу деньги, как их распределить?',
    'Не хочу заработать деньги, хочу сохранить накопленное.',
])
def test_existing_money_gets_reviewed_budget_sources_and_plan(monkeypatch, text):
    result, calls = ask(monkeypatch, text)
    assert not calls
    assert result['context']['material_ids'] == ['money_budget']
    assert 'app_work' not in result['context']['required_resources']
    assert any('fincult.info/' in source['url'] for source in result['information']['sources'])
    plan, calls = ask(monkeypatch, text, operation='draft_plan',
                      selected_material_ids=result['context']['material_ids'])
    assert not calls and plan['tasks']
    assert 'app_work' not in plan['context']['required_resources']


@pytest.mark.parametrize('text', [
    'Мошенники украли деньги, хочу их вернуть',
    'У меня украли деньги, как сохранить оставшиеся?',
    'Потерял заработанные деньги, как их вернуть?',
    'Как не потерять банковскую карту?',
    'Как сохранить деньги на банковской карте?',
    'Как не потерять паспорт?',
    'Получил деньги, как восстановить паспорт?',
    'Как сохранить доход от инвестиций?',
    'Как не потерять деньги на биткоине?',
    'Как сохранить банковский пароль?',
    'Мне нужно отложить получение пособия',
    'Как сохранить накопленные документы?',
])
def test_savings_mapping_does_not_override_other_financial_or_document_goals(text):
    from project.llm.services.adjacent_guides import money_route
    result = money_route(text)
    assert not result or result.get('id') != 'money_budget'


def test_savings_and_separate_income_request_offer_both_topics(monkeypatch):
    text = 'Хочу сохранить накопленное и хочу найти другой источник дохода'
    result, calls = ask(monkeypatch, text)
    assert not calls and result['status'] == 'clarify'
    assert {'budget', 'work'} <= {choice['id'] for choice in result['choices']}
    for topic, material in [('budget', 'money_budget'), ('work', 'income_start')]:
        answer, calls = ask(monkeypatch, text, question_id='topic_choice', answer=topic)
        assert not calls
        assert answer['context']['material_ids'] == [material]
        assert answer['information']['sources']


def test_new_cards_are_canonical_and_openable():
    catalog = {item['id']: item for item in candidate_catalog(CONFIG)}
    for identifier in ['income_start', 'housing_start']:
        assert identifier in catalog
        card = handbook_material(CONFIG, identifier)
        assert card['id'] == identifier and card['actions']


def test_urgent_housing_does_not_drop_work_goal(monkeypatch):
    result, calls = ask(monkeypatch, 'Нужны деньги, сегодня негде переночевать')
    assert not calls and result['information']['priority'] == 'urgent'
    assert {'app_work', 'app_help', 'app_rental'} <= set(result['context']['required_resources'])


def test_concrete_queue_clarification_replaces_general_housing_card(monkeypatch):
    result, calls = ask(monkeypatch, 'Нужны деньги и жильё', operation='draft_plan',
        description='Я уже в жилищном списке, меня пропустили в очереди',
        selected_material_ids=['income_start', 'housing_start', 'hq_state_housing_delay'])
    assert not calls
    assert set(result['context']['material_ids']) == {'income_start', 'hq_state_housing_delay'}
    assert 'app_work' in result['context']['required_resources']
