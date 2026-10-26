"""Grounded content selection, safe fallback, and short-lived return state."""
import copy
import json
from pathlib import Path

import pytest

from project.llm.services.planner_ai import PlannerAI, validate_reply
from project.llm.services.planner_content import (
    build_information, candidates, enrich, material_catalog, material_links,
)


@pytest.fixture
def config():
    return json.loads((Path(__file__).resolve().parents[3] / 'submission/scenario.json').read_text())['config']


def selection(**changes):
    return dict(status='draft', topic='housing', route_alias='housing',
                material_ids=['hq_state_housing_right'],
                action_ids=['housing_info', 'housing_status', 'housing_questions'], **changes)


def test_specific_published_material_replaces_index(config):
    catalog = {item['id']: item for item in material_catalog(config)}
    assert 'hq_state_housing_right' in catalog
    assert 'state_housing' not in catalog
    node = next(node for node in config['nodes'] if node['id'] == 'hq_state_housing_right')
    info = build_information(config, ['hq_state_housing_right'])
    assert catalog[node['id']]['text'] == node['text']
    assert info['text'].startswith(catalog[node['id']]['excerpt'])
    assert 'Начни с опеки' in info['text']
    assert 'Подготовь паспорт, документы о статусе' in info['text']
    assert 1 <= len(info['points']) <= 5
    assert info['details'][0]['text'].startswith(node['text'])
    assert info['sources'][0]['url'] == '/?handbook=hq_state_housing_right'


def test_semantic_candidates_cover_work_help_and_housing_right(config):
    catalog = material_catalog(config)
    assert {item['topic'] for item in catalog} == {'housing', 'work', 'study', 'benefits', 'documents', 'help', 'health', 'budget'}
    assert 'hq_state_housing_right' in {item['id'] for item in candidates(config, 'Положена ли мне квартира?')}
    assert 'doc_passport_q3' in {item['id'] for item in candidates(config, 'Потерял паспорт')}


def test_plan_has_first_material_and_no_duplicate_intro_action(config):
    validated = validate_reply(json.dumps(selection()), 'draft_plan', materials=material_catalog(config))
    result = enrich(config, validated, 'Положена ли мне квартира?')
    assert len(result['tasks']) == 3
    assert result['tasks'][0]['kind'] == 'information'
    assert result['tasks'][0]['links'][0]['title'] == 'Кто имеет право?'
    assert result['context']['question'] == result['title']
    assert result['context']['required_resources'] == []
    assert not any(task.get('required_resource') == 'app_rental' for task in result['tasks'])
    assert result['information']['text']


def test_unknown_material_friendly_preparation_and_search_links(config):
    result = enrich(config, {}, 'Хочу пробежать марафон')
    assert result['status'] == 'information' and result['tasks'] == []
    assert not result['information']['sources']
    assert 'недостаточно проверенных' in result['information']['text']
    assert material_links(config, ['app_work'])[0]['url'] == '/?section=work'
    assert material_links(config, ['app_rental'])[0]['url'] == '/?section=rental'
    assert material_links(config, ['app_help'])[0]['url'] == '/?section=help'
    with pytest.raises(ValueError):
        build_information(config, ['app_work'])


@pytest.mark.parametrize('change', [
    {'material_ids': ['missing']}, {'material_ids': ['hq_state_housing_right'] * 2},
    {'material_ids': ['doc_passport_q3']}, {'owner': 'forbidden'},
    {'action_ids': ['housing_info'] * 3}, {'secondary_topics': ['housing']},
])
def test_strict_model_selection_rejects_invalid_ids_or_extra_fields(config, change):
    data = selection()
    data.update(change)
    with pytest.raises(ValueError):
        validate_reply(json.dumps(data), 'draft_plan', materials=material_catalog(config))


def test_multitopic_materials_and_actions_are_validated_together(config):
    data = selection()
    data.update(secondary_topics=['documents'], material_ids=['hq_state_housing_right', 'doc_passport_q3'],
                action_ids=['housing_status', 'documents_available', 'documents_contact'])
    selected = validate_reply(json.dumps(data), 'draft_plan', materials=material_catalog(config))
    result = enrich(config, selected, 'Положена ли мне квартира и как восстановить потерянный паспорт?')
    assert len(result['tasks']) >= 4
    assert {link['url'] for link in result['tasks'][0]['links']} == {
        '/?handbook=hq_state_housing_right', '/?handbook=doc_passport_q3'}


def test_information_dedup_keeps_final_three_and_rejects_sixth(config):
    data = selection(secondary_topics=['benefits'])
    data['action_ids'] = ['housing_info', 'benefits_info', 'housing_status']
    selected = validate_reply(json.dumps(data), 'draft_plan', materials=material_catalog(config))
    assert len(enrich(config, selected, 'Жильё и поддержка')['tasks']) == 2
    data['action_ids'] = ['housing_info', 'housing_status', 'housing_questions', 'benefits_info', 'benefits_questions']
    with pytest.raises(ValueError):
        validate_reply(json.dumps(data), 'draft_plan', materials=material_catalog(config))


def test_return_cache_is_owner_epoch_scoped_bounded_and_expires(monkeypatch):
    import project.llm.services.planner_ai as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    ai = PlannerAI()
    ai.remember('a', 1, {'title': 'Личная цель'})
    assert ai.latest('b', 1) is None
    snapshot = ai.latest('a', 1)
    snapshot['title'] = 'Не менять оригинал'
    assert ai.latest('a', 1)['title'] == 'Личная цель'
    assert ai.latest('a', 2) is None
    ai.remember('a', 2, {'title': 'Новая'})
    clock[0] += 1801
    assert ai.latest('a', 2) is None
    for index in range(201):
        ai.remember(str(index), 1, {})
    assert len(ai.recent) == 200 and ai.latest('0', 1) is None


@pytest.mark.asyncio
async def test_provider_receives_only_catalog_and_current_question(monkeypatch, config):
    from project.llm.services import planner_ai
    captured = []

    async def fake(messages, **kwargs):
        captured.extend(messages)
        return json.dumps(dict(status='draft', topic='housing', route_alias='housing',
                               material_ids=['hq_state_housing_right']))

    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setattr(planner_ai, 'call_llm', fake)
    result = await PlannerAI().propose('private-max-owner-999', 'draft_plan', 'Положена ли мне квартира?', config=config)
    assert result['status'] == 'draft' and result['information']['sources']
    assert 'private-max-owner-999' not in json.dumps(captured)


def test_hidden_or_changed_publication_cannot_supply_material(config):
    changed = copy.deepcopy(config)
    node = next(node for node in changed['nodes'] if node['id'] == 'hq_state_housing_right')
    node['conditions'] = [{'field': 'role', 'op': 'eq', 'value': 'adult'}]
    with pytest.raises(ValueError):
        build_information(changed, ['hq_state_housing_right'])


def test_consultation_first_and_all_three_required_searches_stay_in_app(config):
    from project.llm.services.handbook import handbook_catalog
    selected = dict(topic='housing', secondary_topics=['work', 'help'],
                    material_ids=['hq_state_housing_right'], tasks=[])
    consultation = enrich(config, selected, 'Ищу работу и жильё, нужна социальная помощь', suggest_actions=False)
    assert consultation['tasks'] == []
    assert consultation['information']['sources']
    result = enrich(config, selected, 'Ищу работу и жильё, нужна социальная помощь')
    required = {task['required_resource'] for task in result['tasks'] if task.get('required_resource')}
    assert required == {'app_work', 'app_rental', 'app_help'}
    assert 3 <= len(result['tasks']) <= 5
    for task in result['tasks']:
        for link in task['links']:
            assert link['url'].startswith('/?')
    reachable = {card['id'] for card in handbook_catalog(config)}
    assert all(card['id'] in reachable for card in candidates(config, 'работа и документы'))


def test_background_location_does_not_force_an_unrelated_search():
    from project.llm.services.planner_content import required_searches
    assert required_searches({'topic': 'documents'}, 'Потерял паспорт на работе в квартире') == []
    assert required_searches({'topic': 'work'}, 'Что делать с работой после выпуска?') == ['app_work']


def test_initial_response_preserves_concrete_steps_limits_and_existing_service_links(config):
    housing = build_information(config, ['hq_state_housing_right'])
    assert 'Попроси письменное подтверждение включения, дату и основание решения.' in housing['text']
    assert 'Если уже исполнилось 23 года' in housing['text']
    assert 'с бесплатным юристом' in housing['text']
    passport = build_information(config, ['doc_passport_q3'])
    assert 'Записать обстоятельства утраты' in passport['text']
    assert 'МВД' in passport['text']
    assert '[Памятка Госуслуг о восстановлении документов](https://www.gosuslugi.ru/landing/document-recovery)' in passport['text']
    assert any(source['url'] == 'https://www.gosuslugi.ru/landing/document-recovery'
               for source in passport['sources'])
    docs = build_information(config, ['employment_docs'])
    assert 'Подготовить паспорт или другой документ' in docs['text']
    assert 'Это не полный список.' in docs['text']
    assert 'ИНН и банковская карта не включены' in docs['text']
    certificate = build_information(config, ['bq_certificate_steps'])
    assert 'сохранить номер заявления' in '\n'.join(item['text'] for item in certificate['details'])
    assert 'перечень приложений' in certificate['text']
    assert any(source['url'] == 'https://www.gosuslugi.ru/648561/1/form'
               for source in certificate['sources'])
