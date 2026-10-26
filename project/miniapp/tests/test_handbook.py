"""Handbook is a pure projection of published child navigation, never a bot run."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from project.llm.services.handbook import categories, handbook_catalog, handbook_material


def reference():
    return json.loads(Path('submission/scenario.json').read_text())['config']


def test_published_sections_and_materials_include_document_index_children():
    config = reference()
    before = deepcopy(config)
    entries = {entry['id']: entry for entry in handbook_catalog(config)}
    assert {'housing', 'work_entry', 'work', 'rental', 'documents', 'doc_passport',
            'identity', 'education', 'career', 'household', 'hq_household_gas',
            'benefits', 'bq_certificate_steps', 'help_points'} <= entries.keys()
    assert entries['identity']['kind'] == 'section'
    assert entries['doc_passport']['parent'] == 'identity'
    assert entries['household']['category'] == 'daily'
    assert entries['household']['parent'] is None
    assert entries['work']['search_target'] == 'work'
    assert entries['rental']['search_target'] == 'rental'
    assert entries['help_points']['search_target'] == 'help'
    assert not {'mentor', 'pa_home', 'family', 'work_search', 'checklist', 'hp_city'} & entries.keys()
    assert len(categories) == 9
    assert config == before


def test_child_conditions_role_changes_and_actions_do_not_expose_content():
    config = reference()
    menu = next(n for n in config['nodes'] if n['id'] == config['menu'])
    for identifier, extra in [('hidden_age', {'conditions': [{'field': 'age', 'op': 'gte', 'value': 18}]}),
                              ('hidden_action', {'kind': 'action'}),
                              ('hidden_role', {}), ('hidden_values', {}),
                              ('hidden_button', {}), ('hidden_template', {'text': 'Секрет {city}'}),
                              ('visible_new', {})]:
        node = dict(id=identifier, title=identifier, branch='documents', kind='message', text='Новый подготовленный материал.', **{})
        node.update(extra)
        config['nodes'].append(node)
        button = dict(label=identifier, target=identifier)
        if identifier == 'hidden_role': button['values'] = {'role': 'parent'}
        if identifier == 'hidden_values': button['values'] = {'private': 'value'}
        if identifier == 'hidden_button': button['conditions'] = [{'field': 'role', 'op': 'eq', 'value': 'parent'}]
        menu['buttons'].append(button)
    ids = {entry['id'] for entry in handbook_catalog(config)}
    assert 'visible_new' in ids
    assert not any(identifier.startswith('hidden_') for identifier in ids)


def test_material_preserves_markdown_sources_and_local_navigation():
    config = reference()
    card = handbook_material(config, 'identity')
    assert card['section'] == 'handbook'
    assert card['favorite_id'] == hashlib.sha256(b'advice:identity').hexdigest()
    assert any(a['url'] == '/?handbook=doc_passport' for a in card['actions'])
    assert handbook_material(config, 'mentor') is None
    source = handbook_material(config, 'household')
    assert '[Роспотребнадзор:' in source['text']
    assert all('start=' not in a['url'] and 'startapp=' not in a['url'] for a in source['actions'])
    changed = deepcopy(config)
    next(n for n in changed['nodes'] if n['id'] == 'identity')['text'] += '\nНовая версия.'
    assert handbook_material(changed, 'identity')['content_hash'] != card['content_hash']


def test_every_published_visible_information_edge_has_a_catalog_entry():
    """Coverage manifest: no child index silently drops its prepared descendants.

    Non-members are classified as a non-child/mentor branch, non-informational
    scenario step, root navigation, or an orphan of the current published menu.
    Older orphan nodes stay out even when their old branch remains in JSON.
    """
    from project.llm.services.handbook import _conditions
    config = reference()
    nodes = {n['id']: n for n in config['nodes']}
    branches = {b['id']: b for b in config['branches']}
    catalog = {entry['id']: entry for entry in handbook_catalog(config)}
    from project.llm.services.reference_guides import nodes as reference_nodes
    assert set(catalog) - set(nodes) == {node['id'] for node in reference_nodes(config)} - set(nodes)
    entries = {identifier: entry for identifier, entry in catalog.items() if identifier in nodes}
    for node in [nodes[config['menu']], *[nodes[identifier] for identifier in entries]]:
        for button in node.get('buttons', []):
            target = nodes.get(button.get('target'))
            if (not target or button.get('url') or button.get('action') or button.get('values')
                    or not _conditions(button.get('conditions', []), {'role': 'child'})):
                continue
            branch = branches[target['branch']]
            if ('child' not in branch['roles'] or branch['id'] in {'mentor', 'parent'}
                    or not _conditions(target.get('conditions', []), {'role': 'child'})
                    or target.get('next') or target.get('routes') or target['kind'] != 'message'):
                continue
            assert target['id'] in entries, f"Published child information missing: {node['id']} → {target['id']}"
    excluded = {}
    for identifier, node in nodes.items():
        if identifier in entries:
            continue
        if 'child' not in branches[node['branch']]['roles'] or node['branch'] in {'mentor', 'parent'}:
            reason = 'outside child handbook scope'
        elif identifier in {'welcome', config['menu']}:
            reason = 'role selection or root menu'
        elif node['kind'] != 'message' or node.get('next') or node.get('routes') or identifier == 'hp_city':
            reason = 'input/action step; search represented by work/rental/help entry'
        else:
            reason = 'not reachable through current visible pure child navigation'
        excluded[identifier] = reason
    assert excluded['mentor'] == 'outside child handbook scope'
    assert excluded['hp_city'].startswith('input/action')
    assert excluded['work_search'].startswith('input/action')
    assert excluded['passport_first'].startswith('not reachable')
    assert len(entries) + len(excluded) == len(nodes)


def test_daily_category_is_inherited_by_new_household_descendants():
    config = reference()
    household = next(n for n in config['nodes'] if n['id'] == 'household')
    household['buttons'].append(dict(label='Новое бытовое дело', target='new_daily_tip'))
    config['nodes'].append(dict(id='new_daily_tip', title='Новое бытовое дело', branch='housing',
                                kind='message', text='Самостоятельный полезный совет.'))
    entry = next(e for e in handbook_catalog(config) if e['id'] == 'new_daily_tip')
    assert entry['category'] == 'daily' and entry['parent'] == 'household'


def test_published_source_links_are_available_directly_on_useful_cards():
    config = reference()
    expected = {
        'doc_passport_q3': ('Памятка Госуслуг о восстановлении документов',
                            'https://www.gosuslugi.ru/landing/document-recovery'),
        'bq_certificate_steps': ('Заявление на жилищный сертификат',
                                 'https://www.gosuslugi.ru/648561/1/form'),
        'household': ('МЧС: что делать при запахе газа',
                      'https://01.mchs.gov.ru/deyatelnost/press-centr/novosti/5817720'),
        'hq_state_housing_right': ('Вправе.рф: бесплатная юридическая помощь',
                                   'https://xn--80adbm1cg.xn--p1ai/'),
    }
    for identifier, (label, url) in expected.items():
        card = handbook_material(config, identifier)
        assert dict(label=label, url=url) in card['actions']
        assert len({action['url'] for action in card['actions']}) == len(card['actions'])
    for identifier, instruction in [('work', 'Казани'), ('rental', 'сколько можешь платить'),
                                     ('help_points', 'возраста')]:
        assert instruction in handbook_material(config, identifier)['text']


def test_external_links_do_not_reintroduce_bot_launch_or_unsafe_role_navigation():
    config = reference()
    node = next(n for n in config['nodes'] if n['id'] == 'doc_passport_q3')
    node['text'] += ('\n\n[Поиск](https://max.ru/t143_hakaton_max_bot?startapp=work)'
                     '\n[Диалог](https://max.ru/t143_hakaton_max_bot?start=private)')
    node['buttons'].append(dict(label='Взрослым', target='mentor', values={'role': 'mentor'}))
    actions = handbook_material(config, node['id'])['actions']
    assert dict(label='Поиск', url='/?section=work') in actions
    assert all('max.ru' not in action['url'] and 'mentor' not in action['url'] for action in actions)
