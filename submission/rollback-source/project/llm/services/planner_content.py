"""Published, child-visible concrete materials. AI selects IDs, never writes facts."""
import re

from .help_routes import _allowed, _path
from .content_text import MARKDOWN_LINK, sentence_preview, sentences

NODE_ID = re.compile(r'[a-z][a-z0-9_]{0,49}')
BRANCH_TOPICS = {
    'documents': 'documents', 'housing': 'housing', 'rental': 'housing',
    'benefits': 'benefits', 'money': 'benefits', 'rights': 'help',
    'work': 'work', 'work_choices': 'work', 'education': 'study',
    'help_points': 'help', 'help': 'help',
}
NO_ANSWER = 'Пока не могу ответить на этот вопрос по нашим материалам. Но помогу подготовиться и составить план.'
SEARCHES = {'app_work': ('Посмотреть вакансии', 'work'),
            'app_rental': ('Посмотреть варианты жилья', 'rental'),
            'app_help': ('Найти места помощи', 'help')}
TOPIC_SEARCH = {'work': 'app_work', 'housing': 'app_rental', 'help': 'app_help'}


def required_searches(selection, question):
    from .planner_retrieval import required_resources
    return required_resources(selection, question)


def material_topic(node):
    identifier = node['id']
    if identifier.startswith(('job_', 'resume_', 'bq_unemployment')) or identifier in {'portfolio', 'employment_docs', 'interview', 'first_job'}:
        return 'work'
    if identifier.startswith('bq_legal'):
        return 'help'
    return BRANCH_TOPICS.get(node.get('branch'))


def plain(text):
    text = MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    text = re.sub(r'https?://\S+', '', text)
    return text.replace('**', '').replace('__', '').strip()


def _paragraphs(node):
    paragraphs = []
    for part in re.split(r'\n\s*\n', node.get('text', '')):
        part = part.strip()
        part = re.sub(r'^Ниже — подготовительные шаги\.\s*', '', part)
        if (not part or plain(part) == node.get('title')
                or (not paragraphs and len(part) < 65 and '\n' not in part
                    and not re.search(r'[.!?:]', part))
                or part.startswith(('Полезные ссылки:', 'Для начала можно сделать',
                                    'Вот план, с которого можно начать.'))
                or all(MARKDOWN_LINK.fullmatch(line.strip().removeprefix('• '))
                       for line in part.splitlines())):
            continue
        # Repeated navigation filler does not answer what to do or where to go.
        if part.startswith('С паспортом связано много первых самостоятельных дел.'):
            continue
        paragraphs.append(part)
    return paragraphs


def excerpt(node):
    """Whole source sentences for selection; never discard a long first sentence."""
    paragraphs = _paragraphs(node)
    return sentence_preview(plain(paragraphs[0]), 900) if paragraphs else ''


def _initial_answer(node):
    """A source introduction followed by prepared steps and concrete details."""
    paragraphs = _paragraphs(node)
    if not paragraphs:
        return node.get('text', '').strip()
    intro, *details = paragraphs
    points = list(dict.fromkeys([*node.get('tasks', []), *details]))
    # Small cards still get usable individual points, without manufacturing steps.
    if len(points) < 3:
        points = [sentence for point in points for sentence in
                  ([point] if '\n' in point else sentences(point))]
    if len(points) > 5:
        points = points[:4] + [' '.join(points[4:])]
    if not points:
        return intro
    return intro + '\n\n' + '\n'.join('• ' + point for point in points[:5])


def material_catalog(config):
    branches = {branch['id']: branch for branch in config.get('branches', [])}
    values = {'role': 'child'}
    result = []
    from .reference_guides import nodes as reference_nodes
    references = reference_nodes(config)
    reference_ids = {n['id'] for n in references}
    for node in [*config.get('nodes', []), *references]:
        identifier = node.get('id', '')
        topic = material_topic(node)
        if (not isinstance(identifier, str) or not NODE_ID.fullmatch(identifier) or not topic
                or node.get('kind') != 'message' or node.get('next') or node.get('routes')
                or node.get('content_status') in {'unverified_reference', 'draft', 'archived'}
                or re.search(r'\{[A-Za-z_][A-Za-z0-9_]*\}', node.get('text', ''))
                or not _allowed(node, branches, values)):
            continue
        # Index/menu cards are not a concrete answer, even if they have intro text.
        if any(not button.get('url')
               and button.get('target') not in {config.get('menu'), 'checklist', 'reminder_tasks'}
               and button.get('values', {}).get('navigation') not in {'back', 'contents'}
               for button in node.get('buttons', [])):
            continue
        text = excerpt(node)
        if not text or (identifier not in reference_ids and not _path(config, identifier, values)):
            continue
        result.append(dict(id=identifier, title=node['title'], text=node['text'], excerpt=text, topic=topic,
                           url=f'/?handbook={identifier}'))
    return result


def selected_materials(config, identifiers):
    if (not isinstance(identifiers, list) or len(identifiers) > 3
            or any(not isinstance(item, str) for item in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Invalid materials')
    if not identifiers:
        return []
    catalog = {item['id']: item for item in material_catalog(config)}
    if any(identifier not in catalog for identifier in identifiers):
        from .handbook import handbook_material
        for identifier in identifiers:
            if identifier not in catalog:
                card = handbook_material(config, identifier)
                if card and card.get('text'):
                    catalog[identifier] = dict(id=identifier, title=card['title'], text=card['text'],
                                              excerpt=card.get('summary') or plain(card['text']),
                                              url=f'/?handbook={identifier}')
    if any(identifier not in catalog for identifier in identifiers):
        raise ValueError('Material unavailable')
    return [catalog[identifier] for identifier in identifiers]


def material_links(config, identifiers):
    if (not isinstance(identifiers, list) or len(identifiers) > 3
            or any(not isinstance(item, str) for item in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Invalid materials')
    if not identifiers:
        return []
    from .handbook import handbook_catalog
    reachable = {item['id'] for item in handbook_catalog(config)}
    catalog = {item['id']: item for item in material_catalog(config) if item['id'] in reachable}
    result = []
    for identifier in identifiers:
        if identifier in SEARCHES:
            title, target = SEARCHES[identifier]
            result.append(dict(id=identifier, title=title, url=f'/?section={target}'))
        elif identifier in catalog:
            item = catalog[identifier]
            result.append(dict(id=identifier, title=item['title'], url=item['url']))
        else:
            from .handbook import handbook_material
            card = handbook_material(config, identifier)
            if not card:
                raise ValueError('Material unavailable')
            result.append(dict(id=identifier, title=card['title'], url=f'/?handbook={identifier}'))
    return result


def build_information(config, identifiers, *, resources=None):
    from .planner_information import build_information as assemble
    information = assemble(config, identifiers)
    known_urls = {source['url'] for source in information['sources']}
    information['sources'].extend(link for link in material_links(config, [] if resources is None else resources)
                                  if link['url'] not in known_urls)
    return information


def candidates(config, query):
    from .handbook import handbook_catalog
    from .planner_retrieval import rank_materials
    reachable = {item['id'] for item in handbook_catalog(config)}
    return rank_materials(config, query, [item for item in material_catalog(config)
                                         if item['id'] in reachable])


def urgent_response(config, question):
    """Return a full published safety card; no model-generated steps or plan."""
    from .handbook import handbook_catalog, handbook_material
    from .planner_retrieval import urgent_material
    reachable = {item['id'] for item in handbook_catalog(config)}
    catalog = [item for item in material_catalog(config) if item['id'] in reachable]
    identifier = urgent_material(config, question, catalog)
    if not identifier:
        return None
    card = handbook_material(config, identifier)
    information = build_information(config, [identifier])
    return dict(status='information', information={
        'text': card['text'], 'sources': information['sources'], 'priority': 'urgent'}, tasks=[])


def enrich(config, selection, question, *, suggest_actions=True, intent_question=None):
    effective_question = question if intent_question is None else intent_question
    urgent = urgent_response(config, effective_question)
    if urgent is not None:
        return urgent
    ids = selection.get('material_ids', [])
    materials = selected_materials(config, ids)
    resources = required_searches(selection, effective_question)
    information = build_information(config, ids, resources=resources)
    context = dict(question=question, material_ids=ids, required_resources=resources)
    if not suggest_actions:
        return dict(status='draft', title=question, information=information, tasks=[], context=context)
    actions = selection.get('tasks', []) if materials else []
    tasks = []
    if materials:
        main = materials[0]
        tasks.append(dict(title='Изучить «'+main['title']+'»', description='Узнайте условия и порядок действий в справочнике.',
                          material_ids=ids, links=material_links(config, ids), kind='information'))
    for resource in resources:
        tasks.append(dict(title=SEARCHES[resource][0], description='Поиск доступен прямо в мини-приложении.',
                          material_ids=[resource], links=material_links(config, [resource]),
                          kind='action', required_resource=resource))
    for action in actions:
        if materials and action.get('informational'):
            continue
        action_resources = action.get('material_ids', [])
        if any(resource in resources for resource in action_resources):
            continue
        if len(tasks) >= 5:
            break
        tasks.append(dict(title=action['title'], description=action.get('description', ''),
                          material_ids=action_resources, links=material_links(config, action_resources), kind='action'))
    if not tasks:
        tasks = [dict(title=title, description=description, material_ids=[], links=[], kind='action')
                 for title, description in [
                     ('Записать желаемый результат', 'Определите, что хотите получить после решения вопроса.'),
                     ('Собрать известные сведения', 'Запишите, что уже знаете и что нужно уточнить.'),
                     ('Подготовить вопросы для обращения', 'Составьте короткий список вопросов подходящему специалисту.')]]
    if len(tasks) < 3:
        from .planner_ai import CATALOG
        titles = {task['title'] for task in tasks}
        entry = CATALOG.get(selection.get('topic'), {})
        for identifier, title in entry.get('actions', {}).items():
            if title in titles or identifier in {'housing_info', 'benefits_info'}:
                continue
            tasks.append(dict(title=title, description='', material_ids=[], links=[], kind='action'))
            titles.add(title)
            if len(tasks) == 3:
                break
    for title in ['Записать желаемый результат', 'Собрать известные сведения', 'Подготовить вопросы для обращения']:
        if len(tasks) >= 3:
            break
        if title not in {task['title'] for task in tasks}:
            tasks.append(dict(title=title, description='', material_ids=[], links=[], kind='action'))
    if not 3 <= len(tasks) <= 5:
        raise ValueError('Invalid final plan size')
    return dict(status='draft', title=question, information=information, tasks=tasks,
                context=context)
