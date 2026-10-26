"""Published material eligibility and legacy housing links."""
import hashlib
import json
from pathlib import Path
import re

import pytest

from project.llm.services.handbook import handbook_material
from project.llm.services.planner_content import (
    PURPOSE, candidate_catalog, material_access, material_catalog,
    material_links, selected_materials,
)


def config():
    return json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']


def test_legacy_housing_ids_have_one_current_answer_and_working_old_links():
    publication = config()
    replacements = {
        'housing_list': 'hq_state_housing_apply',
        'housing_receive': 'hq_state_housing_agreement',
        'special_rent': 'hq_hq_sale_owner',
        'social_rent': 'hq_hq_sale_owner',
        'ownership': 'hq_hq_sale_advice',
        'rent_contract': 'hq_rent_contract',
    }
    available = {item['id'] for item in candidate_catalog(publication)}
    assert not replacements.keys() & available
    assert set(replacements.values()) <= available
    for old_id, current_id in replacements.items():
        old = handbook_material(publication, old_id)
        current = handbook_material(publication, current_id)
        assert old and old['text'] == current['text']
        assert old['node_id'] == old_id
        assert old['favorite_id'] == hashlib.sha256(('advice:' + old_id).encode()).hexdigest()
        assert material_links(publication, [old_id])[0]['url'] == f'/?handbook={old_id}'


def test_unique_housing_move_is_selectable_and_opens_in_handbook():
    publication = config()
    assert 'housing_move' in {item['id'] for item in candidate_catalog(publication)}
    card = handbook_material(publication, 'housing_move')
    assert card and card['node_id'] == 'housing_move'
    assert card['text'].startswith('Переезжаю в другой регион')


def test_unverified_and_withdrawn_content_never_becomes_ai_or_legacy_link():
    publication = config()
    next(node for node in publication['nodes'] if node['id'] == 'doc_birth_q1')['text'] += '\nНепроверенное изменение.'
    statuses = material_access(publication)
    foreign = statuses['doc_birth_q1']
    assert foreign['handbook'] == 'visible'
    assert foreign['ai'] == 'excluded'
    assert foreign['reason'] == 'unverified_reference'
    assert 'doc_birth_q1' not in {item['id'] for item in candidate_catalog(publication)}
    assert statuses['housing_list']['canonical_id'] == 'hq_state_housing_apply'
    assert statuses['housing_list']['reason'] == 'canonical_alias'
    assert statuses['housing_move']['ai'] == 'selectable'
    assert all(item['id'] in statuses for item in material_catalog(publication))
    assert set(PURPOSE) <= {item['id'] for item in candidate_catalog(publication)}
    assert statuses['payment_delay']['intent'] == 'payment_problem'
    with pytest.raises(ValueError):
        selected_materials(publication, ['doc_birth_q1'])

    target = next(node for node in publication['nodes'] if node['id'] == 'hq_state_housing_apply')
    target['content_status'] = 'unverified_reference'
    assert handbook_material(publication, 'housing_list') is None
    assert 'hq_state_housing_apply' not in {item['id'] for item in candidate_catalog(publication)}

    target['content_status'] = 'reviewed_reference'
    old = next(node for node in publication['nodes'] if node['id'] == 'housing_list')
    old['content_status'] = 'unverified_reference'
    assert handbook_material(publication, 'housing_list') is None


def test_child_scope_still_excludes_adult_branch():
    publication = config()
    statuses = material_access(publication)
    assert statuses['pa_home']['ai'] == 'excluded'
    assert statuses['pa_home']['reason'] == 'child_role_or_conditions'
    assert handbook_material(publication, 'pa_home') is None


def test_task_placeholders_are_excluded_from_both_catalogs():
    publication = config()
    node = next(node for node in publication['nodes'] if node['id'] == 'hq_rent_contract')
    node['tasks'] = ['Проверь {address}']
    assert handbook_material(publication, node['id']) is None
    assert node['id'] not in {item['id'] for item in candidate_catalog(publication)}
    assert material_access(publication)[node['id']]['reason'] == 'unresolved_template'


def test_old_id_cannot_bypass_withdrawn_canonical_card_if_navigation_changes():
    publication = config()
    menu = next(node for node in publication['nodes'] if node['id'] == publication['menu'])
    menu['buttons'].append({'label': 'Старый вход', 'target': 'housing_list'})
    target = next(node for node in publication['nodes'] if node['id'] == 'hq_state_housing_apply')
    target['content_status'] = 'archived'
    assert handbook_material(publication, 'housing_list') is None
    assert material_access(publication)['housing_list']['reason'] == 'canonical_unavailable'


def test_five_individually_reviewed_document_answers_are_openable_and_selectable():
    publication = config()
    reviewed = {'doc_foreign_q1', 'doc_foreign_q2', 'doc_foreign_q3',
                'doc_snils_q1', 'doc_inn_q1'}
    statuses = material_access(publication)
    candidates = {item['id'] for item in candidate_catalog(publication)}
    assert reviewed <= candidates
    for identifier in reviewed:
        card = handbook_material(publication, identifier)
        assert statuses[identifier]['ai'] == 'selectable'
        assert statuses[identifier]['handbook'] == 'visible'
        assert card and card['node_id'] == identifier
        assert card['content_status'] == 'source_checked_reference'
        assert any(a['url'].startswith('https://www.gosuslugi.ru/') or
                   a['url'].startswith('https://www.nalog.gov.ru/') for a in card['actions'])
    assert 'ускоряет прохождение границы' not in handbook_material(publication, 'doc_foreign_q1')['text']
    assert '2–4 штуки' not in handbook_material(publication, 'doc_foreign_q3')['text']
    assert 'регистрации в банках' not in handbook_material(publication, 'doc_inn_q1')['text']


def test_document_review_is_bound_to_the_checked_publication_text():
    publication = config()
    original = next(n for n in publication['nodes'] if n['id'] == 'doc_foreign_q1')
    original['text'] += '\nНовая непроверенная фраза.'
    status = material_access(publication)['doc_foreign_q1']
    assert status['ai'] == 'excluded'
    assert status['reason'] == 'unverified_reference'
    assert 'doc_foreign_q1' not in {item['id'] for item in candidate_catalog(publication)}


def test_document_review_is_bound_to_checked_navigation_and_conditions():
    publication = config()
    original = next(n for n in publication['nodes'] if n['id'] == 'doc_foreign_q1')
    original['buttons'].append({'label': 'Непроверенная ссылка', 'url': 'https://example.org/new'})
    assert material_access(publication)['doc_foreign_q1']['reason'] == 'unverified_reference'
    assert 'doc_foreign_q1' not in {item['id'] for item in candidate_catalog(publication)}

    publication = config()
    original = next(n for n in publication['nodes'] if n['id'] == 'doc_foreign_q1')
    original['conditions'].append({'key': 'region', 'value': 'Москва'})
    assert material_access(publication)['doc_foreign_q1']['ai'] == 'excluded'
    assert 'doc_foreign_q1' not in {item['id'] for item in candidate_catalog(publication)}


def test_reviewed_foreign_passport_answers_state_child_fingerprint_age():
    publication = config()
    for identifier in ('doc_foreign_q1', 'doc_foreign_q2'):
        text = handbook_material(publication, identifier)['text']
        assert 'с 12 лет' in text


def test_reviewed_foreign_passport_steps_keep_order_in_prepared_information():
    from project.llm.services.planner_information import build_information

    answer = build_information(config(), ['doc_foreign_q2'])['text']
    positions = [answer.index(f'{step}. ') for step in range(1, 6)]
    assert positions == sorted(positions)


def test_reviewed_document_excerpts_start_with_substantive_answers():
    publication = config()
    for item in material_catalog(publication):
        if item['id'] in {'doc_foreign_q1', 'doc_foreign_q2', 'doc_foreign_q3',
                          'doc_goskey_use', 'doc_goskey_where', 'doc_goskey_setup',
                          'doc_snils_q1', 'doc_inn_q1', 'registration_q1', 'doc_reg_without'}:
            assert item['excerpt'] and not item['excerpt'].endswith('?'), item['id']


def test_travel_and_name_change_are_selectable_after_source_resolution():
    publication = config()
    for identifier in ('doc_foreign_q4', 'doc_reg_name'):
        status = material_access(publication)[identifier]
        assert status['handbook'] == 'visible'
        assert status['ai'] == 'selectable'
        assert status['content_status'] == 'source_checked_reference'


def test_goskey_answers_explain_signing_and_its_actual_prerequisites():
    publication = config()
    reviewed = {'doc_goskey_use', 'doc_goskey_where', 'doc_goskey_setup'}
    assert reviewed <= {item['id'] for item in candidate_catalog(publication)}
    for identifier in reviewed:
        card = handbook_material(publication, identifier)
        assert card and card['content_status'] == 'source_checked_reference'
        assert any(action['url'].startswith('https://www.gosuslugi.ru/help/faq/state_key/')
                   for action in card['actions'])
    assert 'подтверждённая учётная запись' in handbook_material(publication, 'doc_goskey_setup')['text']
    assert 'личный кабинет налогоплательщика' not in handbook_material(publication, 'doc_goskey_where')['text'].lower()


def test_registration_access_cards_do_not_make_registration_a_blanket_barrier():
    publication = config()
    for identifier in ('registration_q1', 'doc_reg_without'):
        card = handbook_material(publication, identifier)
        assert material_access(publication)[identifier]['ai'] == 'selectable'
        assert card['content_status'] == 'source_checked_reference'
        assert 'поликлинике по полису ОМС' in card['text']
        assert any(a['url'] == 'https://www.gosuslugi.ru/help/faq/temporary_registration/101280'
                   for a in card['actions'])
    assert 'от 2 000 до 5 000' not in handbook_material(publication, 'doc_reg_without')['text']


def test_document_review_keeps_other_unverified_cards_and_navigation_closed():
    from project.llm.services.reference_guides import nodes as reference_nodes

    publication = config()
    statuses = material_access(publication)
    overlays = {node['id']: node for node in reference_nodes(publication)}
    reviewed = {'doc_foreign_q1', 'doc_foreign_q2', 'doc_foreign_q3',
                'doc_snils_q1', 'doc_inn_q1'}
    for identifier in {'doc_snils_q2', 'identity_reference_2', 'lost_reference_2'}:
        assert statuses[identifier]['handbook'] == 'excluded'
        assert statuses[identifier]['reason'] == 'unverified_reference'
    for identifier in reviewed:
        original = next(node for node in publication['nodes'] if node['id'] == identifier)
        opened = handbook_material(publication, identifier)
        assert opened['node_id'] == original['id']
        assert opened['content_hash']
        assert all('{' not in opened[field] for field in ('title', 'text'))
        assert overlays[identifier]['buttons'] == original['buttons']
    assert statuses['doc_edu_duplicate']['ai'] == 'excluded'


def test_every_visible_unverified_document_keeps_its_handbook_link_and_navigation():
    from project.llm.services.document_review import records
    from project.llm.services.reference_guides import nodes as reference_nodes

    publication = config()
    statuses = material_access(publication)
    effective = {node['id']: node for node in reference_nodes(publication)}
    source_nodes = [node for node in publication['nodes']
                    if node.get('content_status') == 'unverified_reference'
                    and node.get('branch') == 'documents'
                    and node['id'] != 'doc_edu_duplicate'
                    and statuses[node['id']]['handbook'] == 'visible']
    assert set(records()) <= {node['id'] for node in source_nodes}
    for node in source_nodes:
        identifier = node['id']
        opened = handbook_material(publication, identifier)
        assert opened and opened['node_id'] == identifier
        assert material_links(publication, [identifier])[0]['url'] == f'/?handbook={identifier}'
        assert not re.search(r'\{[^{}]+\}', opened['text'])
        if identifier in records():
            assert statuses[identifier]['ai'] == 'selectable'
            assert effective[identifier]['buttons'] == node['buttons']
        else:
            assert statuses[identifier]['ai'] == 'excluded'
