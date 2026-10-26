"""Read-only projection of published child navigation into handbook materials.

No dialogue engine, scenario actions, input collection or role changes are run.
"""
from collections import deque
import hashlib
import json
import re

from .planner_content import CANONICAL_ALIASES, plain
from .content_text import MARKDOWN_LINK, URL, sentence_preview
from .material_sources import action_url, editorial_actions

categories = [dict(id=identifier, title=title) for identifier, title in (
    ('health', 'Медицинская помощь'), ('housing', 'Жильё'), ('work', 'Работа'), ('documents', 'Документы'),
    ('benefits', 'Выплаты и льготы'), ('study', 'Учёба'),
    ('help', 'Помощь и защита прав'), ('family', 'Семья'),
    ('daily', 'Повседневные дела'))]
CATEGORIES = categories
BRANCH_CATEGORY = {'housing': 'housing', 'rental': 'housing', 'work': 'work',
                   'work_choices': 'work', 'documents': 'documents', 'benefits': 'benefits',
                   'money': 'benefits', 'education': 'study', 'help': 'help',
                   'help_points': 'help', 'rights': 'help', 'family': 'family'}
SEARCH = {'work': ('work', 'Поиск работы', 'В поиске работы можно указать город, профессию и пожелания к вакансии.'),
          'rental': ('rental', 'Поиск жилья', 'В поиске жилья можно указать город и максимальную плату за аренду квартиры в месяц.'),
          'help_points': ('help', 'Где могут помочь', 'В поиске помощи можно найти еду, одежду, ночлег и другие места поддержки в своём городе.')}
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,79}')
VARIABLE = re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')


def _conditions(rules, values):
    # Same allowlisted publication predicates as bot navigation, without loading
    # or running the dialogue engine and its network integrations.
    for rule in rules:
        a, b, op = values.get(rule.get('field')), rule.get('value'), rule.get('op')
        if op == 'known':
            match = (a is not None and a != '') == (str(b).lower() not in {'false', 'нет', '0'})
        elif op in {'lt', 'lte', 'gt', 'gte'}:
            try:
                a, b = float(a), float(b)
            except (TypeError, ValueError):
                return False
            match = {'lt': a < b, 'lte': a <= b, 'gt': a > b, 'gte': a >= b}[op]
        elif op == 'contains':
            match = str(b).lower() in str(a or '').lower()
        elif op in {'eq', 'ne'}:
            match = (str(a).lower() == str(b).lower()) == (op == 'eq')
        else:
            return False
        if not match:
            return False
    return True


def _allowed(node, branches, values):
    return (values['role'] in branches.get(node.get('branch'), {}).get('roles', [])
            and _conditions(node.get('conditions', []), values))


def _category(node, inherited):
    identifier = node['id']
    from .adjacent_guides import TOPIC_OVERRIDES as NEED_TOPICS
    if identifier in NEED_TOPICS:
        return NEED_TOPICS[identifier]
    from .debt_work_guides import TOPIC_OVERRIDES as DEBT_TOPICS
    from .access_guides import TOPIC_OVERRIDES as ACCESS_TOPICS
    from .study_guides import TOPIC_OVERRIDES as STUDY_TOPICS
    TOPIC_OVERRIDES = {**DEBT_TOPICS, **ACCESS_TOPICS, **STUDY_TOPICS}
    if identifier in TOPIC_OVERRIDES:
        return TOPIC_OVERRIDES[identifier]
    if (identifier == 'household' or identifier.startswith('hq_household_')
            or inherited == 'daily' and node.get('branch') == 'housing'):
        return 'daily'
    return BRANCH_CATEGORY.get(node.get('branch'), inherited)


def _index(config):
    nodes = {n['id']: n for n in config.get('nodes', [])}
    branches = {b['id']: b for b in config.get('branches', [])}
    values = {'role': 'child'}
    root = config.get('menu')
    queue = deque([(root, None, None)])
    seen, result = set(), []
    while queue:
        identifier, parent, inherited = queue.popleft()
        if identifier in seen:
            continue
        seen.add(identifier)
        node = nodes.get(identifier)
        if (not node or not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
                or not _allowed(node, branches, values) or node.get('branch') in {'mentor', 'parent'}):
            continue
        search = SEARCH.get(identifier)
        if identifier != root and not search and (node.get('kind') != 'message' or node.get('next') or node.get('routes')):
            continue
        category = _category(node, inherited)
        if identifier != root:
            text = node.get('text', '').strip() or (search[2] if search else '')
            if VARIABLE.search(text) or any(VARIABLE.search(t) for t in node.get('tasks', [])):
                continue
            if node.get('tasks') and not search:
                text += '\n\n' + '\n'.join('☐ ' + task for task in node['tasks'])
            if not category or not text:
                continue
            summary = sentence_preview(plain(text))
            result.append(dict(id=identifier, title=search[1] if search else node.get('title', identifier),
                               summary=summary, text=text, category=category, parent=parent,
                               kind='search' if search else 'material', **({'search_target': search[0]} if search else {})))
        # Only visible, pure navigation edges. Never follow next/routes or collect values.
        for button in node.get('buttons', []):
            changes = button.get('values', {})
            if (not button.get('target') or button.get('url') or button.get('action')
                    or changes.get('navigation') in {'back', 'contents'}
                    or any(key != 'navigation' for key in changes)
                    or not _conditions(button.get('conditions', []), values)):
                continue
            queue.append((button['target'], identifier if identifier != root else None, category))
    ids = {entry['id']: entry for entry in result}
    for entry in result:
        entry['parent'] = entry['parent'] if (entry['parent'] in ids and ids[entry['parent']]['category'] == entry['category']) else None
        if entry['kind'] != 'search' and any(e['parent'] == entry['id'] for e in result):
            entry['kind'] = 'section'
    return result


def handbook_catalog(config):
    """All reachable entries; text is included for local title-and-body search."""
    from .reference_guides import nodes
    additions = []
    for node in nodes(config):
        text = node['text'] + '\n\n' + '\n'.join('☐ ' + task for task in node['tasks'])
        from .debt_work_guides import TOPIC_OVERRIDES as DEBT_TOPICS
        from .access_guides import TOPIC_OVERRIDES as ACCESS_TOPICS
        from .study_guides import TOPIC_OVERRIDES as STUDY_TOPICS
        TOPIC_OVERRIDES = {**DEBT_TOPICS, **ACCESS_TOPICS, **STUDY_TOPICS}
        category = TOPIC_OVERRIDES.get(node['id']) or ('daily' if node['id'] == 'money_budget' else
                    'help' if node['id'] in {'money_today', 'fraud_money_loss', 'fraud_shared_code'} else
                    'help' if node['id'].startswith('support_') or node['id'] == 'personal_safety' else
                    'work' if node['id'] == 'work_wages' else
                    'help' if node['id'] == 'bank_access' else BRANCH_CATEGORY.get(node['branch']))
        from .federal_guides import TOPIC_OVERRIDES as FEDERAL_TOPICS
        category = FEDERAL_TOPICS.get(node['id'], category)
        additions.append(dict(id=node['id'], title=node['title'], text=text,
                              summary=sentence_preview(text), category=category, parent=None, kind='material'))
    replacement_ids = {entry['id'] for entry in additions}
    from .federal_guides import records as federal_records
    owned={i for r in federal_records() if r['disposition']=='add' for i in r['runtime_ids']}
    entries = [*(entry for entry in _index(config)
                 if entry['id'] not in replacement_ids and entry['id'] not in owned
                 and entry['id'] not in CANONICAL_ALIASES), *additions]
    present = {entry['id'] for entry in entries}
    from .planner_content import _ai_exclusion
    branches = {branch['id']: branch for branch in config.get('branches', [])}
    for node in config.get('nodes', []):
        if (node['id'] in present or node['id'] in CANONICAL_ALIASES
                or _ai_exclusion(node, branches, {'role': 'child'}, set(), config)):
            continue
        text = node['text'] + ('\n\n' + '\n'.join('☐ ' + task for task in node.get('tasks', []))
                               if node.get('tasks') else '')
        entries.append(dict(id=node['id'], title=node['title'], text=text,
                            summary=sentence_preview(plain(text)), category=_category(node, None),
                            parent=None, kind='material'))
    return entries


def handbook_material(config, identifier):
    catalog = handbook_catalog(config)
    entry = next((e for e in catalog if e['id'] == identifier), None)
    source_identifier = identifier
    if entry is None and identifier in CANONICAL_ALIASES:
        from .planner_content import candidate_catalog, selected_materials
        target = CANONICAL_ALIASES[identifier]
        try:
            selected_materials(config, [identifier])
        except ValueError:
            return None
        if target not in {item['id'] for item in candidate_catalog(config)}:
            return None
        source_identifier = target
        source = next((e for e in catalog if e['id'] == target), None)
        if source:
            entry = dict(source, id=identifier)
    if entry is None:
        return None
    from .reference_guides import nodes as reference_nodes, actions as reference_actions
    reference = next((n for n in reference_nodes(config) if n['id'] == source_identifier), None)
    node = reference or next(n for n in config['nodes'] if n['id'] == source_identifier)
    nodes = {e['id']: e for e in catalog}
    children = [{k: v for k, v in e.items() if k != 'text'} for e in catalog if e['parent'] == identifier]
    actions = reference_actions() if source_identifier == 'work_wages' and reference else []
    if reference:
        from .adjacent_guides import SERVICE_ACTIONS
        actions.extend(dict(action) for action in SERVICE_ACTIONS.get(identifier, []))
    values = {'role': 'child'}
    for button in node.get('buttons', []):
        if not _conditions(button.get('conditions', []), values):
            continue
        url = button.get('url')
        changes = button.get('values', {})
        if (button.get('target') in nodes and not button.get('action')
                and not button.get('url')
                and changes.get('navigation') not in {'back', 'contents'}
                and all(key == 'navigation' for key in changes)):
            actions.append(dict(label=button['label'], url='/?handbook=' + button['target']))
        elif not button.get('action') and not changes and action_url(url):
            actions.append(dict(label=button['label'], url=action_url(url)))
    # Links embedded in prepared prose are as useful as button URLs. Expose
    # them directly as labeled actions as well, retaining every existing source.
    embedded = [(m.group(1), m.group(2)) for m in MARKDOWN_LINK.finditer(entry['text'])]
    spans = [(m.start(), m.end()) for m in MARKDOWN_LINK.finditer(entry['text'])]
    embedded.extend((m.group(), m.group().rstrip('.,;')) for m in URL.finditer(entry['text'])
                    if not any(a <= m.start() < b for a, b in spans))
    for label, url in embedded:
        url = action_url(url)
        if url and not any(action['url'] == url for action in actions):
            actions.append(dict(label=label, url=url))
    for action in editorial_actions(config, node):
        if not any(existing['url'] == action['url'] for existing in actions):
            actions.append(action)
    from .handbook_sources import supplemental_actions
    for action in supplemental_actions(config, node, nodes):
        existing = next((a for a in actions if a['url'] == action['url']), None)
        if existing is None:
            actions.append(action)
        else:
            if action.get('anchors'):
                existing['anchors'] = list(dict.fromkeys([*existing.get('anchors', []), *action['anchors']]))
            if not existing.get('description'):
                existing['description'] = action['description']
    unique_actions = {}
    for action in actions:
        unique_actions.setdefault(action['url'], action)
    actions = list(unique_actions.values())
    from .handbook_sources import citation_anchors
    actions = citation_anchors(entry['text'], actions)
    digest = hashlib.sha256(json.dumps([entry['title'], entry['text'], actions], ensure_ascii=False).encode()).hexdigest()
    return dict(entry, node_id=identifier, section='handbook', actions=actions, children=children,
                content_status=node.get('content_status'),
                content_hash=digest, favorite_id=hashlib.sha256(('advice:' + identifier).encode()).hexdigest())
