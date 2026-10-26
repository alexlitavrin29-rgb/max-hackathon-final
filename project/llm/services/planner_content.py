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
NO_ANSWER = 'Пока у нас недостаточно проверенных материалов по этой ситуации'
SEARCHES = {'app_work': ('Посмотреть вакансии', 'work'),
            'app_rental': ('Посмотреть варианты жилья', 'rental'),
            'app_help': ('Найти места помощи', 'help')}
TOPIC_SEARCH = {'work': 'app_work', 'housing': 'app_rental', 'help': 'app_help'}
CANONICAL_ALIASES = {
    'housing_list': 'hq_state_housing_apply',
    'housing_receive': 'hq_state_housing_agreement',
    'special_rent': 'hq_hq_sale_owner',
    'social_rent': 'hq_hq_sale_owner',
    'ownership': 'hq_hq_sale_advice',
    'rent_contract': 'hq_rent_contract',
}
# Deliberate distinctions for later intent matching. Unlisted cards have no
# inferred intent; a shared topic alone does not establish a shared goal.
PURPOSE = {
    'social_card': ('application', 'regional_social_card'),
    'social_transport': ('booking', 'accessible_transport'),
    'gas_connection': ('connection', 'gas_supply'),
    'gas_subsidy': ('application', 'gas_expenses'),
    'egrn_extract': ('request', 'property_register'),
    'residence_extract': ('request', 'residence_register'),
    'deregistration': ('registration_change', 'residence'),
    'social_rehabilitation': ('application', 'social_rehabilitation'),
    'orphan_status_proof': ('confirmation', 'orphan_status'),
    'orphan_benefits': ('orientation', 'orphan_benefits'),
    'university_quota': ('admission', 'university_quota'),
    'vocational_training': ('training', 'vocational_training'),
    'utility_compensation': ('eligibility', 'utility_compensation'),
    'utility_subsidy': ('application', 'utility_subsidy'),
    'orphan_payments': ('orientation', 'orphan_payments'),
    'graduation_support': ('application', 'graduation_payment'),
    'social_scholarship': ('application', 'social_scholarship'),
    'study_books': ('application', 'study_books'),
    'study_provision': ('application', 'state_study_support'),
    'orphan_unemployment': ('application', 'unemployment_payment'),
    'orphan_wage_topup': ('application', 'wage_topup'),
    'bq_pension_who': ('eligibility', 'survivor_pension'),
    'pension_topup': ('application', 'pension_topup'),
    'study_training_allowance': ('application', 'training_allowance'),
    'scholarship_increased': ('application', 'scholarship_increased'),
    'income_start': ('income_options', 'livelihood'),
    'housing_start': ('orientation', 'housing_options'),
    'hq_state_housing_right': ('eligibility', 'state_housing'),
    'hq_state_housing_apply': ('application', 'state_housing'),
    'hq_state_housing_agreement': ('agreement', 'state_housing'),
    'housing_move': ('transfer', 'state_housing'),
    'hq_rent_contract': ('contract_check', 'private_rental'),
    'hq_rent_view': ('rental_search', 'private_rental'),
    'resume_first': ('preparation', 'first_job'),
    'job_minor': ('eligibility', 'minor_employment'),
    'work_wages': ('payment_problem', 'wages'),
    'career_interests': ('choice', 'career'),
    'doc_passport_q3': ('recovery', 'passport'),
    'gosuslugi_access': ('access_recovery', 'gosuslugi'),
    'money_budget': ('planning', 'personal_budget'),
    'benefit_unknown': ('identification', 'benefit'),
    'payment_delay': ('payment_problem', 'benefit'),
    'bq_legal_steps': ('consultation', 'legal_aid'),
    'oms_policy': ('access', 'healthcare'),
    'care_denial': ('denial', 'healthcare'),
    'college_return': ('return', 'education'),
    'study_transfer': ('transfer', 'education'),
    'support_services': ('consultation', 'psychological_support'),
    'childbirth_allowance': ('application', 'family_benefit'),
    'hq_household_wash': ('instructions', 'household'),
}


def required_searches(selection, question):
    from .planner_retrieval import required_resources
    resources = required_resources(selection, question)
    if selection.get('selected_topic'):
        resources = [item for item in resources if item == TOPIC_SEARCH.get(selection['selected_topic'])]
    return resources


def material_topic(node):
    identifier = node['id']
    from .adjacent_guides import TOPIC_OVERRIDES as NEED_TOPICS
    if identifier in NEED_TOPICS:
        return NEED_TOPICS[identifier]
    from .federal_guides import TOPIC_OVERRIDES as FEDERAL_TOPICS
    if identifier in FEDERAL_TOPICS:
        return FEDERAL_TOPICS[identifier]
    from .debt_work_guides import TOPIC_OVERRIDES as DEBT_TOPICS
    from .access_guides import TOPIC_OVERRIDES as ACCESS_TOPICS
    from .study_guides import TOPIC_OVERRIDES as STUDY_TOPICS
    TOPIC_OVERRIDES = {**DEBT_TOPICS, **ACCESS_TOPICS, **STUDY_TOPICS}
    if identifier in TOPIC_OVERRIDES:
        return TOPIC_OVERRIDES[identifier]
    if identifier == 'money_budget':
        return 'budget'
    if identifier in {'money_today', 'fraud_money_loss', 'fraud_shared_code'}:
        return 'help'
    if identifier.startswith('support_') or identifier == 'personal_safety':
        return 'help'
    if identifier == 'work_wages':
        return 'work'
    if identifier == 'bank_access':
        return 'help'
    if identifier.startswith(('job_', 'resume_')) or identifier in {'portfolio', 'employment_docs', 'interview', 'first_job'}:
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
                or not MARKDOWN_LINK.sub('', part).strip(' \t\r\n•·')):
            continue
        # Repeated navigation filler does not answer what to do or where to go.
        if part.startswith('С паспортом связано много первых самостоятельных дел.'):
            continue
        if part.startswith(('Перед оформлением на работу полезно заранее собрать вопросы',
                            'Давай разберёмся с поступлением в вуз.',
                            'Трудовой договор стоит прочитать спокойно,')):
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


def _ai_exclusion(node, branches, values, reference_ids, config):
    identifier = node.get('id')
    if not isinstance(identifier, str) or not NODE_ID.fullmatch(identifier):
        return 'invalid_id'
    if not _allowed(node, branches, values):
        return 'child_role_or_conditions'
    if node.get('content_status') in {'unverified_reference', 'draft', 'archived'}:
        return node['content_status']
    if not material_topic(node):
        return 'no_ai_topic_mapping'
    if node.get('kind') != 'message' or node.get('next') or node.get('routes'):
        return 'not_standalone_message'
    if (re.search(r'\{[A-Za-z_][A-Za-z0-9_]*\}', node.get('text', ''))
            or any(re.search(r'\{[A-Za-z_][A-Za-z0-9_]*\}', task)
                   for task in node.get('tasks', []))):
        return 'unresolved_template'
    if any(not button.get('url')
           and button.get('target') not in {config.get('menu'), 'checklist', 'reminder_tasks'}
           and button.get('values', {}).get('navigation') not in {'back', 'contents'}
           for button in node.get('buttons', [])):
        return 'navigation_or_index'
    if not excerpt(node):
        return 'no_concrete_excerpt'
    if identifier not in reference_ids and not _path(config, identifier, values):
        return 'no_pure_child_navigation_path'
    return None


def material_catalog(config):
    branches = {branch['id']: branch for branch in config.get('branches', [])}
    values = {'role': 'child'}
    result = []
    from .reference_guides import nodes as reference_nodes
    references = reference_nodes(config)
    reference_ids = {n['id'] for n in references}
    for node in [*(n for n in config.get('nodes', []) if n['id'] not in reference_ids), *references]:
        identifier = node.get('id', '')
        if identifier in CANONICAL_ALIASES or _ai_exclusion(node, branches, values, reference_ids, config):
            continue
        text = excerpt(node)
        from .federal_guides import context
        purpose = PURPOSE.get(identifier)
        result.append(dict(id=identifier, title=node['title'], text=node['text'], excerpt=text,
                           topic=material_topic(node), url=f'/?handbook={identifier}',
                           **(dict(intent=purpose[0], object=purpose[1]) if purpose else {}),
                           **context(identifier)))
    return result


def candidate_catalog(config):
    """Canonical reviewed answers that are openable through the handbook."""
    from .handbook import handbook_catalog
    visible = {item['id'] for item in handbook_catalog(config)}
    return [item for item in material_catalog(config) if item['id'] in visible]


def material_access(config):
    """Report the same AI gate and handbook projection for each effective node."""
    from .handbook import handbook_catalog
    from .reference_guides import nodes as reference_nodes
    references = reference_nodes(config)
    reference_ids = {node['id'] for node in references}
    nodes = [*(node for node in config.get('nodes', []) if node['id'] not in reference_ids), *references]
    branches = {branch['id']: branch for branch in config.get('branches', [])}
    values = {'role': 'child'}
    visible = {item['id']: item for item in handbook_catalog(config)}
    selectable = {item['id'] for item in candidate_catalog(config)}
    result = {}
    for node in nodes:
        identifier = node['id']
        reason = _ai_exclusion(node, branches, values, reference_ids, config)
        target = CANONICAL_ALIASES.get(identifier)
        handbook = ('visible' if identifier in visible else 'legacy_alias'
                    if target in selectable and reason is None else 'excluded')
        purpose = PURPOSE.get(target or identifier)
        result[identifier] = dict(
            id=identifier, title=node.get('title', ''), canonical_id=target or identifier,
            kind=visible[identifier]['kind'] if identifier in visible else
                 'material' if handbook == 'legacy_alias' else 'node',
            audience=branches.get(node.get('branch'), {}).get('roles', []),
            content_status=node.get('content_status'),
            topic=material_topic(node), intent=purpose[0] if purpose else None,
            object=purpose[1] if purpose else None,
            handbook=handbook,
            handbook_reason=(None if handbook == 'visible' else 'canonical_alias'
                             if handbook == 'legacy_alias' else 'child_role_or_conditions'
                             if reason == 'child_role_or_conditions' else 'not_in_child_handbook'),
            ai='selectable' if identifier in selectable else 'excluded',
            reason=(None if identifier in selectable else reason or
                    ('canonical_alias' if target in selectable else 'canonical_unavailable'
                     if target else 'not_in_handbook')))
    return result


def selected_materials(config, identifiers):
    if (not isinstance(identifiers, list) or len(identifiers) > 3
            or any(not isinstance(item, str) for item in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Invalid materials')
    if not identifiers:
        return []
    catalog = {item['id']: item for item in material_catalog(config)}
    nodes = {node['id']: node for node in config.get('nodes', [])}
    branches = {branch['id']: branch for branch in config.get('branches', [])}
    visible = ({item['id'] for item in candidate_catalog(config)}
               if any(identifier in CANONICAL_ALIASES for identifier in identifiers) else set())
    for identifier in identifiers:
        target = CANONICAL_ALIASES.get(identifier)
        if target in visible and identifier in nodes and _ai_exclusion(
                nodes[identifier], branches, {'role': 'child'}, set(), config) is None:
            catalog[identifier] = catalog[target]
    if any(identifier not in catalog for identifier in identifiers):
        raise ValueError('Material unavailable')
    return [catalog[identifier] for identifier in identifiers]


def refine_plan_materials(materials, question, clarification):
    """Replace provisional alternatives only when a selected concrete goal is clarified."""
    if not clarification or len(materials) < 2:
        return materials
    from .planner_retrieval import _intents, _INTENT_IDS
    preferred = {identifier for intent in _intents(clarification)
                 for identifier in _INTENT_IDS[intent] if len(_INTENT_IDS[intent]) == 1}
    from .benefits_guides import GROUPS
    if preferred.intersection(i for _, ids in GROUPS.values() for i in ids):
        materials = [m for m in materials if m['id'] != 'orphan_benefits']
    from .federal_guides import PAYMENT_IDS, match_ids
    payments=set(match_ids(clarification)).intersection(PAYMENT_IDS)-{'orphan_payments'}
    if payments:
        materials=[m for m in materials if m['id']!='orphan_payments']
        preferred.update(payments)
    topics = {item['topic'] for item in materials if item['id'] in preferred}
    if not topics:
        return materials
    # An earlier independently requested goal remains agreed, even in the same
    # topic. A broad alternative group containing the new goal is a refinement.
    protected = set()
    for clause in [question, *re.split(r'\b(?:и|а|но|также)\b|\n', question.casefold())]:
        for intent in _intents(clause):
            alternatives = set(_INTENT_IDS[intent])
            if not alternatives.intersection(preferred):
                protected.update(alternatives)
    protected.update(set(match_ids(question)).intersection(PAYMENT_IDS)-preferred-{'orphan_payments'})
    kept = [item for item in materials if item['topic'] not in topics
            or item['id'] in preferred or item['id'] in protected]
    return sorted(kept, key=lambda item: item['id'] not in preferred)


def material_links(config, identifiers):
    if (not isinstance(identifiers, list) or len(identifiers) > 3
            or any(not isinstance(item, str) for item in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Invalid materials')
    if not identifiers:
        return []
    from .handbook import handbook_material
    catalog = {item['id']: item for item in candidate_catalog(config)}
    result = []
    for identifier in identifiers:
        if identifier in SEARCHES:
            title, target = SEARCHES[identifier]
            result.append(dict(id=identifier, title=title, url=f'/?section={target}'))
        elif identifier in catalog:
            item = catalog[identifier]
            result.append(dict(id=identifier, title=item['title'], url=item['url']))
        else:
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
    from .planner_retrieval import rank_materials
    return rank_materials(config, query, candidate_catalog(config))


def money_urgent_response(config, question):
    """Stop a current financial attack before topic/description/provider flow."""
    from .adjacent_guides import money_route
    from .handbook import handbook_material
    route = money_route(question)
    if not route or not route.get('immediate'):
        return None
    identifier = route['id']
    if identifier not in {item['id'] for item in material_catalog(config)}:
        return dict(status='information', information=dict(text=NO_ANSWER, sources=[], priority='normal'), tasks=[])
    information = build_information(config, [identifier])
    information.update(text=handbook_material(config, identifier)['text'], priority='urgent')
    return dict(status='information', information=information, tasks=[])


def urgent_response(config, question):
    """Return a full published safety card; no model-generated steps or plan."""
    from .handbook import handbook_catalog, handbook_material
    from .planner_retrieval import urgent_material, required_resources
    reachable = {item['id'] for item in handbook_catalog(config)}
    catalog = [item for item in material_catalog(config) if item['id'] in reachable]
    identifier = urgent_material(config, question, catalog)
    if not identifier:
        return None
    card = handbook_material(config, identifier)
    identifiers, resources = [identifier], ['app_help'] if identifier == 'shelter' else []
    housing = None
    if identifier == 'shelter' and 'housing_start' in {item['id'] for item in catalog}:
        from .planner_retrieval import _rental_negated
        housing = handbook_material(config, 'housing_start')
        identifiers.append('housing_start')
        if not _rental_negated(question):
            resources.append('app_rental')
        if 'app_work' in required_resources({}, question):
            resources.append('app_work')
    information = build_information(config, identifiers, resources=resources)
    result = {'text': card['text'] + ('\n\n' + housing['text'] if housing else ''),
              'sources': information['sources'], 'priority': 'urgent'}
    if identifier == 'shelter':
        result['lead'] = '\n\n'.join(card['text'].split('\n\n')[:3])
        if housing:
            result['lead'] += '\n\n' + housing['text'].split('\n\n')[0]
    return dict(status='information', information=result, tasks=[],
                context=dict(question=question.strip(), material_ids=[identifier],
                             required_resources=resources))


def enrich(config, selection, question, *, suggest_actions=True, intent_question=None):
    effective_question = question if intent_question is None else intent_question
    urgent = urgent_response(config, effective_question)
    if urgent is not None:
        return urgent
    ids = selection.get('material_ids', [])
    materials = selected_materials(config, ids)
    if suggest_actions and len(materials) > 1:
        from .planner_retrieval import _intents, _INTENT_IDS
        preferred = {identifier for intent in _intents(effective_question)
                     for identifier in _INTENT_IDS[intent]}
        if preferred:
            ids = sorted(ids, key=lambda identifier: identifier not in preferred)
            materials = selected_materials(config, ids)
    resources = required_searches(selection, effective_question)
    information = build_information(config, ids, resources=resources)
    if not materials and resources:
        information['text'] = information['intro'] = 'Поиск доступен сразу по ссылкам ниже.'
    if not materials and not resources:
        return dict(status='information', reason='no_material', information=information, tasks=[])
    if re.search(r'кто\s+поможет|консультац|провер\w*\s+услов|юридическ\w*\s+помощ', effective_question, re.I):
        consultation = next((source for source in information['sources']
                             if re.search(r'юридическ\w*\s+помощ|консультац', source['title'], re.I)), None)
        if consultation:
            information['primary_action'] = consultation
    context = dict(question=question, material_ids=ids, required_resources=resources)
    if not suggest_actions:
        return dict(status='draft', title=question, information=information, tasks=[], context=context)
    actions = selection.get('tasks', []) if materials else []
    if materials:
        from .reference_guides import nodes as reference_nodes
        nodes = {node['id']: node for node in [*config.get('nodes', []), *reference_nodes(config)]}
        action_lists = [(item['id'], nodes[item['id']].get('tasks', [])) for item in materials]
        prepared = [dict(title=tasks[index], description='', material_ids=[identifier])
                    for index in range(max((len(tasks) for _, tasks in action_lists), default=0))
                    for identifier, tasks in action_lists if index < len(tasks)]
        if prepared:
            actions = prepared
    tasks = []
    if materials:
        main = materials[0]
        title = 'Изучить выбранные материалы' if len(materials) > 1 else 'Изучить «'+main['title']+'»'
        tasks.append(dict(title=title, description='Узнайте условия и порядок действий в справочнике.',
                          material_ids=ids, links=material_links(config, ids), kind='information'))
    for resource in resources:
        tasks.append(dict(title=SEARCHES[resource][0], description='Поиск доступен прямо в мини-приложении.',
                          material_ids=[resource], links=material_links(config, [resource]),
                          kind='action', required_resource=resource))
    for action in actions:
        if materials and action.get('informational'):
            continue
        action_resources = action.get('material_ids', [])
        if any(resource in SEARCHES and resource not in resources for resource in action_resources):
            continue
        if any(resource in resources for resource in action_resources):
            continue
        if len(tasks) >= 5:
            break
        tasks.append(dict(title=action['title'], description=action.get('description', ''),
                          material_ids=action_resources, links=material_links(config, action_resources), kind='action'))
    if not 1 <= len(tasks) <= 5:
        raise ValueError('Invalid final plan size')
    return dict(status='draft', title=question, information=information, tasks=tasks,
                context=context)
