"""Actual publication regressions for extractive answers and safe sources."""
import json
from pathlib import Path

import pytest

from project.llm.services.handbook import handbook_catalog, handbook_material
from project.llm.services.material_sources import action_url, editorial_actions
from project.llm.services.planner_information import build_information


@pytest.fixture
def config():
    return json.loads(Path('submission/scenario.json').read_text())['config']


def test_three_housing_cards_share_one_five_point_budget_and_keep_full_details(config):
    ids = ['hq_state_housing_right', 'hq_state_housing_when', 'hq_state_housing_apply']
    # Frozen reference example: old independent card assembly was 2418
    # characters and 11 bullet points before the global answer budget.
    result = build_information(config, ids)
    assert 3 <= len(result['points']) <= 5
    assert len(result['text']) < 1800
    assert 'Если уже исполнилось 23 года' in result['text']
    assert 'сохранение права и причины невключения' in result['text']
    assert 'если такое жильё есть — можно ли в нём жить по закону.' in result['intro']
    nodes = {node['id']: node for node in config['nodes']}
    for identifier, detail in zip(ids, result['details']):
        assert nodes[identifier]['text'] in detail['text']
        assert detail['sources'][0]['id'] == identifier
    for point in result['points']:
        source_id = point['sources'][0]['id']
        assert point['text'] in nodes[source_id]['text'] or point['text'] in nodes[source_id]['tasks']
        assert all(source['id'] == source_id for source in point['sources'])
    assert len({s['url'] for s in result['sources']}) == len(result['sources'])
    assert result['priority'] == 'normal'


def test_housing_application_and_delay_are_each_represented(config):
    result = build_information(config, ['hq_state_housing_right', 'hq_state_housing_apply', 'hq_state_housing_delay'])
    assert len(result['points']) == 5
    assert len(result['text']) < 1800
    assert {'hq_state_housing_apply', 'hq_state_housing_delay'} <= {
        point['sources'][0]['id'] for point in result['points']}


def test_passport_keeps_fee_eligibility_and_recovery_service_limit_visible(config):
    result = build_information(config, ['doc_passport_q3'])
    assert 'она не заменяет подачу заявления.' in result['text']
    assert ('Если у тебя есть статус ребёнка-сироты или ребёнка без попечения родителей, '
            'пошлину за выдачу паспорта платить не нужно.') in result['text']
    assert 'Записать обстоятельства утраты' in result['text']
    assert any(s['url'] == 'https://www.gosuslugi.ru/landing/document-recovery' for s in result['sources'])


def test_employment_literal_editorial_url_is_useful_and_partial_limits_visible(config):
    result = build_information(config, ['employment_docs'])
    node = next(n for n in config['nodes'] if n['id'] == 'employment_docs')
    article = next(s for s in result['sources'] if s['url'] == node['editorial_sources'][0])
    assert 'статья 65' in article['title']
    assert 'документы воинского учёта нужны соответствующим категориям' in article['description']
    assert 'Это не полный список.' in result['text']
    assert 'ИНН и банковская карта не включены здесь как универсальные обязательные документы.' in result['text']
    assert 'документы воинского учёта нужны соответствующим категориям' in result['text']
    assert 'Подготовить паспорт или другой документ' in result['text']


def test_reference_ids_and_urls_resolve_only_exact_definitions():
    config = {'sources': {
        'law': {'url': 'https://example.org/law', 'title': 'Проверенный закон'},
        'unrelated': {'url': 'https://other.example.org/', 'title': 'Чужое название'},
    }}
    node = {'sources': ['law', 'missing'], 'editorial_sources': [
        'https://example.org/law', 'https://example.org/new', 'https://example.org/new']}
    actions = editorial_actions(config, node)
    assert actions == [dict(label='Проверенный закон', url='https://example.org/law'),
                       dict(label='Источник: example.org', url='https://example.org/new')]


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'file:///secret', 'https://user:password@example.org/',
    'https://user@example.org/', 'https://example.org:bad/', 'https://example.org/\nprivate',
    'https://max.ru/bot?startapp=mentor', 'https://max.ru/bot?start=child',
    'https://max.ru/bot?startapp=work&role=parent',
    'https://max.ru/bot?startapp=work&startapp=mentor',
])
def test_sources_reject_unsafe_urls_and_foreign_role_links(url):
    assert action_url(url) is None
    assert editorial_actions({'sources': {'bad': {'url': url}}}, {'sources': ['bad']}) == []


def test_handbook_sources_share_safe_boundary_without_changing_coverage(config):
    before = handbook_catalog(config)
    node = next(n for n in config['nodes'] if n['id'] == 'employment_docs')
    node['editorial_sources'] += ['javascript:alert(1)', 'https://secret:credential@example.org/']
    node['buttons'].append(dict(label='Взрослым', url='https://example.org/private', values={'role': 'mentor'}))
    card = handbook_material(config, node['id'])
    assert any('статья 65' in action['label'] for action in card['actions'])
    assert all('secret' not in action['url'] and '/private' not in action['url'] for action in card['actions'])
    assert handbook_catalog(config) == before
    assert {'employment_docs', 'housing_move'} <= {entry['id'] for entry in before}
    assert len({entry['id'] for entry in before}) == len(before)


def test_unavailable_material_and_no_material(config):
    with pytest.raises(ValueError):
        build_information(config, ['nonexistent'])
    result = build_information(config, [])
    assert result['sources'] == [] and result['points'] == [] and result['details'] == []
