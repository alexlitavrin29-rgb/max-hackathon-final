"""Controlled selector and local publication checks for stage 3."""
import asyncio
import json
from pathlib import Path

import pytest

from project.llm.services import planner_ai
from project.llm.services.planner_content import candidates, required_searches


@pytest.fixture(scope='module')
def config():
    return json.loads((Path(__file__).resolve().parents[3] / 'submission/scenario.json').read_text(encoding='utf-8'))['config']


@pytest.mark.parametrize('query,identifier', [
    ('Как написать резюме, если нигде не работал?', 'resume_first'),
    ('Шеф задерживает зарлату, что предпринять?', 'work_wages'),
    ('Мне положена квартира как выпускнику центра?', 'hq_state_housing_right'),
    ('Хочу подать заявление на жильё от государства', 'hq_state_housing_apply'),
    ('Переехал в другой регион, что станет с жильём?', 'housing_move'),
    ('Ищу комнату в аренду', 'hq_rent_view'),
    ('Патерял пасспорт, куда обратиться?', 'doc_passport_q3'),
    ('Как распределить деньги до следующей зарплаты?', 'money_budget'),
    ('Пособие одобрили, но на карту не перечислили', 'payment_delay'),
    ('Не понимаю, как подготовиться к бесплатной юридической консультации', 'bq_legal_steps'),
    ('Не могу найти сведения о полисе ОМС', 'oms_policy'),
    ('После отчисления хочу вернуться в колледж', 'college_return'),
    ('Тяжело одному, хочу поговорить с психологом', 'support_services'),
    ('После рождения малыша как оформить разовое пособие?', 'childbirth_allowance'),
    ('Как постирать и высушить вещи без машинки?', 'hq_household_wash'),
])
def test_concrete_goal_ranked_first(config, query, identifier):
    assert candidates(config, query)[0]['id'] == identifier


@pytest.mark.parametrize('query,identifier,topic,route,search', [
    ('Подберите вакансии без опыта в Казани', 'resume_first', 'work', 'work', '/?section=work'),
    ('Как написать резюме, если нигде не работал?', 'resume_first', 'work', 'work', None),
    ('Шеф задерживает зарлату, что предпринять?', 'work_wages', 'work', 'work', None),
    ('Мне положена квартира как выпускнику центра?', 'hq_state_housing_right', 'housing', 'housing', None),
    ('Хочу подать заявление на жильё от государства', 'hq_state_housing_apply', 'housing', 'housing', None),
    ('Переехал в другой регион, что станет с жильём?', 'housing_move', 'housing', 'housing', None),
    ('Ищу комнату в аренду', 'hq_rent_view', 'housing', 'rent', '/?section=rental'),
    ('Патерял пасспорт, куда обратиться?', 'doc_passport_q3', 'documents', 'documents', None),
    ('Как распределить деньги до следующей зарплаты?', 'money_budget', 'budget', 'budget', None),
    ('Пособие одобрили, но на карту не перечислили', 'payment_delay', 'benefits', 'benefits', None),
    ('Как подготовиться к бесплатной юридической консультации?', 'bq_legal_steps', 'help', 'legal', None),
    ('Не могу найти сведения о полисе ОМС', 'oms_policy', 'health', 'help', None),
    ('После отчисления хочу вернуться в колледж', 'college_return', 'study', 'study', None),
    ('Тяжело одному, хочу поговорить с психологом', 'support_services', 'help', 'help', None),
    ('После рождения малыша как оформить разовое пособие?', 'childbirth_allowance', 'benefits', 'benefits', None),
    ('Как постирать и высушить вещи без машинки?', 'hq_household_wash', 'housing', 'housing', None),
])
def test_controlled_result_keeps_material_and_action(config, monkeypatch, query, identifier, topic, route, search):
    model = dict(status='draft', topic=topic, route_alias=route, material_ids=[identifier])
    result, _ = _run(config, monkeypatch, model, query)
    urls = {source['url'] for source in result['information']['sources']}
    assert result['status'] in {'draft', 'information'}
    if query == 'Подберите вакансии без опыта в Казани':
        assert result['context']['material_ids'] == []
    else:
        assert identifier in result['context']['material_ids']
        assert f'/?handbook={identifier}' in urls
    if search:
        assert search in urls


def test_search_and_missing_material_are_independent(config):
    assert required_searches({}, 'Мне положена квартира как выпускнику центра?') == []
    assert required_searches({}, 'Ищу комнату в аренду') == ['app_rental']
    assert required_searches({}, 'Нужен ночлег и восстановить паспорт') == ['app_help']
    assert candidates(config, 'Нужна инструкция по ремонту космического лифта') == []
    ids = {item['id'] for item in candidates(config, 'Родился ребёнок и потерял паспорт')}
    assert {'childbirth_allowance', 'doc_passport_q3'} <= ids


def test_negated_goal_does_not_compete_with_active_goal(config):
    housing = candidates(config, 'Не хочу снимать квартиру, хочу узнать право на госжильё')
    assert housing[0]['id'] == 'hq_state_housing_right'
    assert all(item.get('object') != 'private_rental' for item in housing)
    assert required_searches({}, 'Не хочу снимать квартиру, хочу узнать право на госжильё') == []
    wages = candidates(config, 'Не ищу работу, мне задержали зарплату')
    assert wages[0]['id'] == 'work_wages'
    assert required_searches({}, 'Не ищу работу, мне задержали зарплату') == []


def _run(config, monkeypatch, response, text, operation='find_help_route', **kwargs):
    captured = []

    async def controlled(messages, **options):
        captured.extend(messages)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setattr(planner_ai, 'call_llm', controlled)
    result = asyncio.run(planner_ai.PlannerAI().propose('synthetic-owner', operation, text, config=config, **kwargs))
    return result, captured


def test_correct_model_selection_survives_server_and_reaches_full_card(config, monkeypatch):
    model = dict(status='draft', topic='housing', route_alias='housing', material_ids=['hq_state_housing_apply'])
    result, prompt = _run(config, monkeypatch, model, 'Хочу подать заявление на жильё от государства')
    assert result['status'] == 'draft'
    assert result['information']['sources'][0]['url'] == '/?handbook=hq_state_housing_apply'
    assert result['information']['details'][0]['title'] == 'Как получить квартиру?'
    assert 'hq_state_housing_apply' in prompt[0]['content']


def test_search_available_without_card(config, monkeypatch):
    model = dict(status='draft', topic='housing', route_alias='rent', material_ids=[])
    result, _ = _run(config, monkeypatch, model, 'Ищу комнату в аренду')
    assert result['status'] == 'draft'
    assert any(link['url'] == '/?section=rental' for link in result['information']['sources'])


def test_multigoal_cards_reach_model_and_result(config, monkeypatch):
    model = dict(status='draft', topic='benefits', secondary_topics=['documents'], route_alias='benefits',
                 material_ids=['childbirth_allowance', 'doc_passport_q3'])
    result, prompt = _run(config, monkeypatch, model, 'Родился ребёнок и потерял паспорт')
    assert result['status'] == 'draft'
    assert {item['title'] for item in result['information']['details']} == {
        'Родился ребёнок. Как оформить разовое пособие?', 'Потерян паспорт'}
    assert 'childbirth_allowance' in prompt[0]['content'] and 'doc_passport_q3' in prompt[0]['content']


def test_multigoal_plan_keeps_an_action_for_each_card(config):
    from project.llm.services.planner_content import enrich
    result = enrich(config, dict(topic='work', secondary_topics=['help'],
                                 material_ids=['resume_first', 'bq_legal_steps'], tasks=[]),
                    'Составить резюме и подготовиться к юридической консультации')
    linked = {identifier for task in result['tasks'][1:] for identifier in task['material_ids']}
    assert {'resume_first', 'bq_legal_steps'} <= linked


def test_wrong_same_topic_material_is_grounded_to_explicit_goal(config, monkeypatch):
    model = dict(status='draft', topic='work', route_alias='work', material_ids=['work_wages'])
    result, _ = _run(config, monkeypatch, model, 'Как написать резюме без опыта?')
    assert result['context']['material_ids'] == ['resume_first']
    from project.llm.services.planner_content import candidate_catalog
    allowed = [item for item in candidate_catalog(config) if item['id'] in {'resume_first', 'work_wages'}]
    with pytest.raises(ValueError, match='material purpose'):
        planner_ai.validate_reply(json.dumps(model), 'find_help_route', materials=allowed,
                                  intent_question='Как написать резюме без опыта?')


def test_answered_question_does_not_repeat(config, monkeypatch):
    model = dict(status='clarify', question_id='work_kind')
    result, prompt = _run(config, monkeypatch, model, 'Первая работа', question_id='work_kind',
                          answer='Первую, опыта нет, резюме ещё не делал')
    assert result['status'] == 'draft'
    assert result['context']['material_ids'] == ['resume_first']
    assert prompt and 'резюме' in prompt[1]['content']


def test_topic_change_accepts_correct_model_choice(config, monkeypatch):
    model = dict(status='draft', topic='work', route_alias='work', material_ids=['job_minor'])
    result, prompt = _run(config, monkeypatch, model, 'Потерял паспорт', question_id='documents_kind',
                          answer='Нет, теперь мне 17 и ищу первую работу')
    assert result['status'] == 'draft'
    assert result['information']['sources'][0]['url'] == '/?handbook=job_minor'
    assert 'previous_goal' in prompt[1]['content']
    planned, _ = _run(config, monkeypatch, model,
                      'Потерял паспорт', operation='draft_plan', question_id='documents_kind',
                      answer='Нет, теперь мне 17 и ищу первую работу', selected_material_ids=['doc_passport_q3'])
    assert planned['context']['material_ids'] == ['job_minor']


def test_agreed_material_is_retained_for_plan(config, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Selection is already agreed')
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    result = asyncio.run(planner_ai.PlannerAI().propose(
        'synthetic-owner', 'draft_plan', 'Нужно резюме без опыта', config=config,
        selected_material_ids=['resume_first']))
    assert result['status'] == 'draft'
    assert result['context']['material_ids'] == ['resume_first']
    assert result['information']['sources'][0]['url'] == '/?handbook=resume_first'


def test_unknown_benefit_and_multigoal_choice_progress(config, monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    benefit = asyncio.run(planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route', 'Какая мне выплата доступна?', 'benefits_kind', 'Не знаю', config=config))
    assert benefit['status'] == 'draft' and benefit['context']['material_ids'] == ['orphan_payments']
    mixed = asyncio.run(planner_ai.PlannerAI().propose(
        'synthetic-owner', 'find_help_route', 'Нужен ночлег и восстановить паспорт', config=config))
    assert mixed['status'] == 'clarify'
    assert {choice['id'] for choice in mixed['choices']} == {'help', 'documents'}
    assert any(source['url'] == '/?section=help' for source in mixed['information']['sources'])


def test_provider_failure_differs_from_missing_material(config, monkeypatch):
    model = dict(status='draft', topic='help', route_alias='help', material_ids=[])
    absent, _ = _run(config, monkeypatch, model, 'Нужна инструкция по ремонту космического лифта')
    error, _ = _run(config, monkeypatch, planner_ai.LLMError('timeout'), 'Нужна инструкция по ремонту космического лифта')
    assert absent['status'] == 'information' and 'недостаточно проверенных' in absent['information']['text']
    assert error['status'] == 'unsupported' and error['reason'] == 'unavailable'
    rejected, _ = _run(config, monkeypatch, {'status': 'unsupported'}, 'Как написать резюме без опыта?')
    assert rejected['status'] == 'unsupported' and rejected['reason'] == 'unavailable'
