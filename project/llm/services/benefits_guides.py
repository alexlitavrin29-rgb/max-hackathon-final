"""Benefit discovery: choose a sphere, then one checked canonical material."""
from copy import deepcopy
import re

# Navigation only. All facts and eligibility conditions stay in the cards.
GROUPS = {
    'housing': ('Жильё, сертификат и жилищные документы', ['hq_state_housing_right', 'hq_state_housing_when', 'hq_state_housing_certificate', 'egrn_extract', 'residence_extract', 'deregistration']),
    'graduation': ('Выпускное пособие и вещи', ['graduation_support']),
    'education': ('Поступление и бесплатное обучение', ['university_quota', 'university_preparation', 'vocational_training', 'second_vocational']),
    'students': ('Стипендии и обеспечение студентов', ['social_scholarship', 'scholarship_increased', 'study_books', 'study_provision', 'study_training_allowance']),
    'employment': ('Безработица и доплата к зарплате', ['orphan_unemployment', 'orphan_wage_topup']),
    'pensions': ('Пенсия и доплата к ней', ['bq_pension_who', 'pension_topup', 'survivor_pension_work']),
    'travel': ('Проезд и социальное такси', ['study_travel', 'treatment_travel', 'social_transport', 'disability_nsu']),
    'health': ('Медицина, санаторий и реабилитация', ['federal_medical_care', 'sanatorium_right', 'child_recreation', 'medical_rehabilitation', 'rehabilitation_equipment', 'equipment_certificate']),
    'legal': ('Бесплатная юридическая помощь', ['bq_legal_who', 'bq_legal_steps']),
    'utilities': ('ЖКУ и газификация', ['utility_subsidy', 'utility_compensation', 'gas_connection', 'gas_subsidy']),
    'services': ('Социальные услуги и порядок получения', ['social_services', 'social_rehabilitation', 'social_service_cost', 'social_accompaniment']),
    'documents': ('Статус и социальная карта', ['status_orientation', 'orphan_status_proof', 'social_card']),
    'payments_help': ('Оформление помощи, суммы и задержки', ['orphan_payments', 'social_assistance', 'social_contract', 'application_tracking', 'payment_delay', 'benefit_refusal', 'benefit_unknown']),
}
SUBGROUPS = {
    'rehabilitation': ('Какая реабилитация нужна', ['medical_rehabilitation', 'social_rehabilitation', 'rehabilitation_equipment']),
    'voucher': ('Какая путёвка нужна', ['sanatorium_right', 'child_recreation', 'disability_nsu']),
    'extract': ('Что имеется в виду под выпиской', ['egrn_extract', 'residence_extract', 'deregistration']),
    'gas_options': ('Подключение газа или помощь с расходами', ['gas_connection', 'gas_subsidy']),
}
QUESTIONS = {'benefits_category': 'О каких льготах и мерах поддержки рассказать? Выбери тему.'}
QUESTIONS.update({'benefits_' + key: title + ': какой именно вопрос тебя интересует?'
                  for key, (title, _) in {**GROUPS, **SUBGROUPS}.items()})


def available_groups(config, sections=None):
    from .planner_content import candidate_catalog
    allowed = {m['id']: m for m in candidate_catalog(config)}
    return {key: (title, [allowed[i] for i in ids if i in allowed])
            for key, (title, ids) in (sections or GROUPS).items() if any(i in allowed for i in ids)}


def _answer(config, identifier, question, operation):
    from .planner_content import enrich
    from .handbook import handbook_material
    result = enrich(config, dict(material_ids=[identifier]), question, intent_question=question)
    result['information']['text'] = handbook_material(config, identifier)['text']
    result['topic'] = 'benefits'
    if operation != 'draft_plan':
        result.update(status='information', tasks=[])
    return result


def prepared_response(config, question, question_id, answer, operation):
    """Discovery does not override named benefits or reported service problems."""
    from .federal_guides import match_records
    choosing = question_id in QUESTIONS
    q = str(question).casefold().replace('ё', 'е')
    if (not choosing and re.search(r'выписк', q)
            and re.search(r'медицин|больниц|поликлиник|стационар|банков|\bсчет', q)
            and not re.search(r'егрн|зарегистрирован|домов\w*\s+книг', q)):
        return None
    broad = bool(re.search(r'льгот|мер\w*\s+поддержк|поддержк\w*\s+студент|'
                          r'бесплатн\w*\s+(?:образован|проезд)|'
                          r'какая\s+поддержк|господдержк|'
                          r'что.{0,20}положено.{0,35}(?:сирот|детдом|выпуск)|'
                          r'реабилитац|путевк|выписк|газификац|социальн\w*\s+услуг', q))
    problem = re.search(r'отказ|задерж|приостанов|не\s+(?:приш|поступ|перечисл)|назначен', q)
    concrete = [r for r in match_records(question) if r['id'] != 'F53']
    if question_id == 'topic_choice' and answer != 'benefits':
        return None
    if not choosing and (not broad or problem or concrete):
        return None
    if not question_id:
        from .planner_retrieval import work_search_requested
        topics = ['benefits']
        if work_search_requested(q):
            topics.append('work')
        if re.search(r'(?:восстановить|получить|оформить|потерял\w*)\s+(?:паспорт|снилс|документ|аттестат)', q):
            topics.append('documents')
        if re.search(r'(?:снять|найти|арендовать|ищу)\s+(?:\w+\s+){0,2}(?:жиль|квартир|комнат)', q):
            topics.append('housing')
        if len(topics) > 1:
            from .planner_ai import topic_clarification
            return topic_clarification(topics[:3])
    groups = available_groups(config, {**GROUPS, **SUBGROUPS})
    unknown = str(answer or '').casefold() in {'не знаю', 'неизвестно', 'не помню', 'все'}
    if choosing and unknown:
        from .planner_content import candidate_catalog
        if 'orphan_benefits' in {m['id'] for m in candidate_catalog(config)}:
            return _answer(config, 'orphan_benefits', question, operation)
    category = answer if question_id == 'benefits_category' and answer in groups else None
    if question_id and question_id.startswith('benefits_') and question_id != 'benefits_category':
        category = question_id[len('benefits_'):]
        if category not in groups:
            category = None
        elif answer in {m['id'] for m in groups[category][1]}:
            return _answer(config, answer, question, operation)
    if choosing and answer and not category:
        # A person may type the concrete question instead of pressing a button.
        matched = match_records(answer)
        allowed = {m['id'] for _, cards in groups.values() for m in cards}
        ids = [i for r in matched for i in r['runtime_ids'] if i in allowed]
        if len(ids) == 1:
            return _answer(config, ids[0], question, operation)
    if not category:
        patterns = {
            'housing': r'жиль|квартир|жилищ', 'graduation': r'выпускн\w*\s+пособи|вещи\s+при\s+выпуск',
            'education': r'поступлен|образован|профподготов|обучени',
            'students': r'студент|стипенди|учеб', 'employment': r'безработ|зарплат|трудоустр',
            'pensions': r'пенси', 'travel': r'проезд|транспорт',
            'health': r'лечени|медицин|санатор|отдых|здоров',
            'legal': r'юридическ|юрист', 'utilities': r'жку|жкх|коммунал',
            'services': r'социальн\w*\s+услуг', 'documents': r'статус|соцкарт|социальн\w*\s+карт',
            'payments_help': r'материальн\w*\s+помощ|оформлен\w*\s+помощ',
            'rehabilitation': r'реабилитац', 'voucher': r'путевк',
            'extract': r'выписк', 'gas_options': r'газификац',
        }
        phrase = str(answer or '').casefold().replace('ё', 'е') if choosing else q
        detected = [key for key, pattern in patterns.items() if key in groups and re.search(pattern, phrase)]
        if len(detected) == 1:
            category = detected[0]
    if category:
        cards = groups[category][1]
        if len(cards) == 1:
            return _answer(config, cards[0]['id'], question, operation)
        identifier = 'benefits_' + category
        return dict(status='clarify', question_id=identifier, question=QUESTIONS[identifier],
                    choices=[dict(id=m['id'], title=m['title']) for m in cards])
    return dict(status='clarify', question_id='benefits_category', question=QUESTIONS['benefits_category'],
                choices=[dict(id=key, title=title) for key, (title, _) in groups.items() if key in GROUPS])


def navigation(config):
    """MAX buttons use the same eligibility-filtered material catalogue as AI."""
    from .help_routes import _allowed, _path
    branches = {b['id']: b for b in config.get('branches', [])}
    root = next((n for n in config.get('nodes', []) if n['id'] == 'benefits'), None)
    if (not root or root.get('content_status') in {'draft', 'archived', 'unverified_reference'}
            or not _allowed(root, branches, {'role': 'child'})
            or not _path(config, 'benefits', {'role': 'child'})):
        return {}
    from .reference_guides import nodes
    references = {n['id']: n for n in nodes(config)}
    result = {'benefits': deepcopy(root)}
    buttons = []
    for key, (title, cards) in available_groups(config).items():
        group_id = 'benefits_' + key
        buttons.append(dict(label=title, target=group_id))
        result[group_id] = dict(id=group_id, kind='message', branch='benefits', title=title,
            text=title + '\n\nВыбери конкретный вопрос. В карточке будут условия и ссылки.',
            tasks=[], buttons=[*[dict(label=m['title'], target=m['id']) for m in cards],
                dict(label='Назад', target='benefits', values={'navigation': 'back'})])
        for material in cards:
            identifier = material['id']
            base = references.get(identifier) or next(n for n in config['nodes'] if n['id'] == identifier)
            node = result.setdefault(identifier, deepcopy(base))
            node.update(format='markdown', buttons=[
                dict(label='Назад', target=group_id, values={'navigation': 'back'}),
                dict(label='К оглавлению', target=config['menu'], values={'navigation': 'contents'})])
    result['benefits']['buttons'] = buttons + [
        dict(label='Выплаты и пенсии: все вопросы', target='bq_group_payments'),
        dict(label='Назад', target=config['menu'], values={'navigation': 'back'})]
    return result
