"""Source review continuation: content boundaries and user-visible projections."""
import json
from pathlib import Path
import re

from project.llm.services.document_review import records
from project.llm.services.handbook import handbook_material
from project.llm.services.planner_content import material_access, material_catalog
from project.llm.services.planner_information import build_information


def publication():
    return json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']


def test_each_original_visible_document_has_an_individual_checked_edition():
    inputs = json.loads(Path('docs/ai-quality-repair/card-inputs-02.json').read_text(encoding='utf-8'))
    identifiers = {card['id'] for card in inputs['cards']}
    assert set(records()) == identifiers
    statuses = material_access(publication())
    for identifier in identifiers:
        assert statuses[identifier]['handbook'] == 'visible'
        assert statuses[identifier]['ai'] == 'selectable', identifier
        assert statuses[identifier]['content_status'] == 'source_checked_reference'
    assert statuses['doc_edu_duplicate']['reason'] == 'navigation_or_index'


def test_document_storage_has_one_consistent_safe_recommendation():
    texts = {identifier: handbook_material(publication(), identifier)['text'] for identifier in
             ('doc_birth_q2', 'doc_lost_photos', 'doc_lost_storage')}
    assert 'Не ламинируй' in texts['doc_birth_q2']
    assert 'НЕ ХРАНИ ФОТО' not in texts['doc_lost_photos']
    assert 'дополнительным подтверждением' in texts['doc_lost_photos']
    assert not any('Google Drive' in text or 'Яндекс Диске' in text for text in texts.values())


def test_police_route_is_specific_to_passport_and_actual_fraud():
    passport = handbook_material(publication(), 'doc_lost_police')['text']
    fraud = handbook_material(publication(), 'doc_lost_fraud')['text']
    assert 'паспорт РФ' in passport and 'талон-уведомление' in passport
    assert 'СНИЛС и ИНН' in passport
    assert 'банк или микрофинансовую организацию' in fraud
    assert 'не заключал' in fraud


def test_travel_and_name_change_have_qualified_current_instructions():
    config = publication()
    travel = handbook_material(config, 'doc_foreign_q4')['text']
    name = handbook_material(config, 'doc_reg_name')['text']
    assert '20 января 2026' in travel and 'до 14 лет' in travel
    assert 'до 3 месяцев' in travel
    assert 'С 14 лет отдельный загранпаспорт необходим' not in travel
    assert '90 календарных дней' in name
    assert 'проверь' in name and 'ОМС' in name
    assert 'полис ОМС менять не нужно' not in name


def test_registration_and_gosuslugi_do_not_promise_unconditional_access():
    config = publication()
    registration = handbook_material(config, 'registration_q3')['text']
    abroad = handbook_material(config, 'registration_q4')['text']
    account = handbook_material(config, 'gosuslugi_q3')['text']
    assert 'администрации общежития' in registration and 'очной' in registration
    assert 'добровольный' in abroad and 'не требует' in abroad
    assert 'до 14 лет' in account and 'с 14 лет' in account
    assert 'доступ ко всем функциям' not in account


def test_checked_sources_are_actions_and_source_only_lines_are_not_answer_points():
    config = publication()
    catalog = {item['id']: item for item in material_catalog(config)}
    link = re.compile(r'\[[^\]\n]+\]\([^\s)]+\)')
    for identifier, review in records().items():
        card = handbook_material(config, identifier)
        urls = [action['url'] for action in card['actions']]
        assert len(urls) == len(set(urls))
        assert set(review['sources']) <= set(urls), identifier
        assert not catalog[identifier]['excerpt'].endswith('?'), identifier
        answer = build_information(config, [identifier])
        for point in answer['points']:
            assert link.sub('', point['text']).strip(' \t\r\n•·'), identifier
