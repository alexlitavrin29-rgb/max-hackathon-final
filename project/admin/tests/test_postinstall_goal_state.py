"""Postinstall goal failures; controlled replies are not live provider evidence."""
import asyncio
import json
from pathlib import Path

from project.llm.services import planner_ai


def config():
    return json.loads(Path('submission/scenario.json').read_text(encoding='utf8'))['config']


def propose(monkeypatch, query, raw, *, operation='find_help_route', **kwargs):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    async def selector(*args, **kw):
        return raw
    monkeypatch.setattr(planner_ai, 'call_llm', selector)
    return asyncio.run(planner_ai.PlannerAI().propose('goal-test', operation, query,
                                                      config=config(), **kwargs))


def draft(topic, ids):
    return json.dumps(dict(status='draft', topic=topic, route_alias=topic,
                           material_ids=ids))


def test_work_offers_do_not_become_resume(monkeypatch):
    for query in ('Ищу стажировку без опыта в Самаре. Где посмотреть предложения?',
                  'Подбери практику у работодателя, хочу увидеть варианты.',
                  'Где искать подработку на выходные без опыта?'):
        result = propose(monkeypatch, query, draft('work', ['resume_first']))
        assert result['status'] == 'draft'
        assert result['context']['material_ids'] == []
        assert 'app_work' in result['context']['required_resources']
        assert 'Поиск доступен' in result['information']['text']
        assert any(source.get('id') == 'app_work' for source in result['information']['sources'])


def test_resume_request_still_opens_resume(monkeypatch):
    result = propose(monkeypatch, 'Как составить резюме без опыта для стажировки?',
                     draft('work', ['resume_first']))
    assert result['context']['material_ids'] == ['resume_first']
    assert 'app_work' not in result['context']['required_resources']


def test_offer_search_does_not_become_unrelated_study_card(monkeypatch):
    for query in ('Покажи практику у работодателей поблизости',
                  'Покажи вакансии для первой работы'):
        result = propose(monkeypatch, query, draft('study', ['career_try']))
        assert result['status'] == 'draft'
        assert result['context']['material_ids'] == []
        assert result['context']['required_resources'] == ['app_work']


def test_negated_loss_does_not_replace_inn(monkeypatch):
    result = propose(monkeypatch, 'Не потерял паспорт, хочу проверить ИНН',
                     draft('documents', ['doc_inn_lookup']))
    assert result['context']['material_ids'] == ['doc_inn_lookup']


def test_existing_list_vs_first_application(monkeypatch):
    for query in ('В списке на квартиру меня как будто пропустили. Где сверить очередь?',
                  'Уже числюсь на жильё, а очередь не движется. Куда обращаться?'):
        result = propose(monkeypatch, query, draft('housing', ['hq_state_housing_delay']))
        assert result['context']['material_ids'] == ['hq_state_housing_delay']
    result = propose(monkeypatch, 'Хочу впервые подать заявление о включении в список на жильё',
                     draft('housing', ['hq_state_housing_apply']))
    assert result['context']['material_ids'] == ['hq_state_housing_apply']


def test_invalid_secondary_reply_keeps_two_search_goals(monkeypatch):
    raw = json.dumps(dict(status='draft', topic='work', route_alias='work',
                          material_ids=['resume_first'], secondary_topics=['housing'],
                          draft_plan=[{'action': 'work_search'}]))
    query = 'Нужна подработка в Казани и комната рядом'
    result = propose(monkeypatch, query, raw, description='Теперь покажи и жильё',
                     history=[{'question_id': 'topic_choice', 'answer': 'work'}],
                     selected_material_ids=['resume_first'])
    assert result['status'] == 'draft'
    assert result['context']['material_ids'] == []
    assert set(result['context']['required_resources']) == {'app_work', 'app_rental'}
    plan = propose(monkeypatch, query, raw, operation='draft_plan',
                   description='Теперь покажи и жильё',
                   history=[{'question_id': 'topic_choice', 'answer': 'work'}])
    assert plan['status'] == 'draft'
    assert {task.get('required_resource') for task in plan['tasks']} >= {'app_work', 'app_rental'}


def test_priority_work_keeps_only_search_when_model_adds_work_advice(monkeypatch):
    raw = draft('work', ['resume_first', 'job_contract', 'work_lodging_contract'])
    result = propose(monkeypatch, 'Нужна подработка в Казани и комната рядом', raw,
                     question_id='topic_choice', answer='work')
    assert result['context']['material_ids'] == []
    assert result['context']['required_resources'] == ['app_work']


def test_invalid_reply_retains_agreed_card_and_new_search(monkeypatch):
    raw = json.dumps(dict(status='draft', topic='documents', route_alias='documents',
                          material_ids=['doc_passport_q3'], draft_plan=[]))
    result = propose(monkeypatch, 'Потерял паспорт', raw,
                     description='Теперь ещё ищу подработку',
                     selected_material_ids=['doc_passport_q3'])
    assert result['status'] == 'draft'
    assert result['context']['material_ids'] == ['doc_passport_q3']
    assert 'app_work' in result['context']['required_resources']


def test_current_food_need_after_budget_shows_help_before_priority(monkeypatch):
    result = propose(monkeypatch, 'Хочу распределить деньги до зарплаты',
                     draft('budget', ['money_budget']),
                     description='Сегодня нужна еда, денег нет')
    assert result['status'] == 'clarify'
    urls = {item['url'] for item in result['information']['sources']}
    assert '/?handbook=money_today' in urls
    assert '/?section=help' in urls
