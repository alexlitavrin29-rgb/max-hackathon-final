"""Offline retrieval over the caller's current, child-visible material catalog.

The catalog is the publication/visibility boundary: never read additional nodes
into ranking. Returned dictionaries are the supplied objects.
"""
import math
import re
from collections import Counter

MAX_CANDIDATES = 8
_WORD = re.compile(r'[а-яa-z0-9]+')
_STOP = set('а и но или в на по к с со у из за от до о об для при как что это ли же бы я мне меня мой мы он она вы ты не нет есть делать нужно можно хочу получить помощь помогите подскажите инструкция вопрос узнать нужно нужен нужна'.split())
_SPELLING = ('паспорт', 'потерял', 'зарплату', 'пособие', 'квартиру', 'аренду', 'резюме', 'полиса')
# Prefix aliases intentionally cover common inflections, colloquial spellings and
# observed misspellings without a network service or a new morphology dependency.
_ALIASES = (
    ('газ', ('газ', 'загаз')),
    ('запах', ('пах', 'пахн', 'запах', 'запахн')),
    ('жиль', ('квартир', 'жиль', 'жилищ', 'комнат')),
    ('долг', ('долг', 'задолж')),
    ('коммун', ('коммун', 'жкх', 'жку')),
    ('работ', ('работ', 'ваканс', 'трудоустр')),
    ('замок', ('замок', 'замк')),
    ('потер', ('потер', 'утрат', 'утер')),
    ('право', ('право', 'положен', 'полага', 'полагает')),
    ('плат', ('плат', 'оплат')),
    ('стир', ('стир', 'стират', 'стира', 'постира')),
    ('ночлег', ('ночлег', 'ночев', 'переноч')),
    ('протеч', ('протеч', 'протек', 'течет', 'теч', 'льет')),
)


def _plain(text):
    text = str(text or '').lower().replace('ё', 'е')
    text = re.sub(r'https?://\S+', ' ', text)
    # Correct a single edit in common domain words, without matching a whole
    # test sentence or altering words whose correction is ambiguous.
    def correct(match):
        word = match.group()
        if len(word) < 5:
            return word
        close = [candidate for candidate in _SPELLING if abs(len(candidate) - len(word)) <= 1
                 and _edit_distance_one(word, candidate)]
        return close[0] if len(close) == 1 else word
    return _WORD.sub(correct, text)


def _edit_distance_one(left, right):
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = j = errors = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1; j += 1
        else:
            errors += 1
            if errors > 1:
                return False
            if len(left) == len(right):
                i += 1
            j += 1
    return errors + (len(right) - j) <= 1


def _tokens(text):
    result = []
    for word in _WORD.findall(_plain(text)):
        if word in _STOP or len(word) < 3 or word.startswith(('помощ', 'получ', 'хоч', 'нужн')):
            continue
        alias = next((root for root, prefixes in _ALIASES if word.startswith(prefixes)), None)
        # Five-character stems cover productive Russian inflections; preserve
        # short words such as газ, УК, ЖКХ through the alias vocabulary.
        result.append(alias or word[:5])
    return result


def _has(text, pattern):
    return bool(re.search(pattern, text))


def _rental_negated(text):
    action = r'(?:снять|снимать|арендовать)'
    refusal = r'(?:хочу|собираюсь|буду|планирую)'
    return _has(text, rf'\bне\s+(?:{refusal}\s+)?{action}\b|'
                 rf'\b{action}\b\s+(?:\w+\s+){{0,2}}не\s+{refusal}\b')


def _gas_smell(text):
    gas_seen = False
    for clause in re.split(r'[,;.!?]|\bно\b|\bоднако\b', text):
        gas_here = _has(clause, r'газ\w*')
        inherited_smell = gas_seen and _has(clause, r'(?:сейчас|снова|до\s+сих\s+пор).*пах\w*\s*$')
        gas_seen = gas_seen or gas_here
        if not ((gas_here and _has(clause, r'пах\w*|запах\w*|утеч\w*|несет\s+газ\w*')) or inherited_smell):
            continue
        if _has(clause, r'\bне\s+(?:пах\w*|несет)|газ\w*\s+не\s+пах\w*|'
                        r'нет\s+(?:запах\w*|утеч\w*)\s+газ\w*|'
                        r'(?:запах\w*|утеч\w*)\s+газ\w*\s+(?:сейчас\s+)?нет'):
            continue
        return True
    return False


def _intents(question):
    q = _plain(question)
    result = set()
    if _gas_smell(q):
        result.add('gas')
    physical_lock = _has(q, r'(?:смен\w*|поменя\w*)\s+зам(?:ок|ки|ка|ков)\b')
    virtual_access = _has(q, r'личн\w*\s+кабинет\w*|аккаунт\w*|учетн\w*\s+запис\w*|приложени\w*|сайт\w*')
    dwelling_access = (not virtual_access and _has(q, r'\bне\s+пуска\w*|не\s+могу\s+(?:попасть|войти)')
                       and _has(q, r'домой|дом\b|квартир\w*|жиль\w*|комнат\w*'))
    if physical_lock or dwelling_access:
        result.add('locked')
    if (_has(q, r'долг\w*|задолж\w*|нечем\s+плат\w*|рассроч\w*')
            and _has(q, r'коммун\w*|жк[ху]|квитанц\w*|плат\w*|долг\w*')):
        result.add('debt')
    if _has(q, r'протеч\w*|протек\w*|течет\s+вод|вод\w*\s+(?:течет|льет)|льет\w*\s+с\s+потолк|'
               r'(?:течет|льется|залива\w*)\s+(?:\w+\s+){0,3}(?:сверху|комнат\w*|квартир\w*|спальн\w*)|'
               r'с\s+потолк\w*.{0,30}(?:льет\w*|льется|течет|капает|залива\w*)'):
        result.add('water')
    if _has(q, r'(?:полож\w*|право|полага\w*|сирот\w*)') and _has(q, r'квартир\w*|жиль\w*'):
        result.add('housing_right')
    moving_housing = _has(q, r'переех\w*|переезд\w*|переезж\w*|переед\w*|(?:смен\w*|друг\w*)\s+регион\w*') and _has(q, r'жиль\w*|квартир\w*')
    listed_housing = _has(q, r'\b(?:уже\s+)?(?:стою|числюсь|нахожусь)\s+(?:\w+\s+){0,3}(?:списк\w*|очеред\w*|жиль\w*)|\bв\s+(?:списк\w*|очеред\w*)')
    delayed_housing = listed_housing and _has(q, r'пропуст\w*|не\s+(?:движ\w*|дают|предостав\w*)|задерж\w*|долг\w*|где\s+свер\w*|како\w*\s+(?:мест\w*|номер\w*)|что\s+с\s+очеред\w*')
    if delayed_housing and _has(q, r'жиль\w*|квартир\w*|очеред\w*') and not moving_housing:
        result.add('housing_delay')
    if (_has(q, r'заявлен\w*|спис\w*|очеред\w*')
            and _has(q, r'жиль\w*|квартир\w*')
            and not moving_housing
            and not delayed_housing
            and not (_has(q, r'аренд\w*|снять|снимать') and not _rental_negated(q))):
        result.add('housing_apply')
    if moving_housing:
        result.add('housing_move')
    if (_has(q, r'аренд\w*|снять|снимать|наем|найм') and _has(q, r'жиль\w*|квартир\w*|комнат\w*')
            and not _rental_negated(q)):
        result.add('rental')
    if (_has(q, r'паспорт\w*') and _has(q, r'потер\w*|утер\w*|утрат\w*|укра\w*')
            and not _has(q, r'\bне\s+(?:потер\w*|утер\w*|утрат\w*)|паспорт\w*\s+не\s+(?:потер\w*|утер\w*|утрат\w*)')):
        result.add('passport_lost')
    if _has(q, r'резюм\w*') and _has(q, r'без\s+опыт\w*|не\s+работал\w*|перв\w*\s+работ\w*'):
        result.add('first_resume')
    if _has(q, r'перв\w*\s+работ\w*|работ\w*\s+без\s+опыт\w*'):
        result.add('first_job')
    if (_has(q, r'выплат\w*|пособи\w*|пенси\w*') and
            _has(q, r'одобр\w*|назнач\w*|не\s+перечисл\w*|не\s+пришл\w*|задерж\w*')):
        result.add('benefit_delay')
    if _has(q, r'юридическ\w*|правов\w*|юрист\w*') and _has(q, r'консультац\w*|помощ\w*|обращ\w*|юрист\w*'):
        result.add('legal_preparation' if _has(q, r'подготов\w*|что\s+взять|что\s+приготов\w*')
                   else 'legal_consultation')
    if _has(q, r'стира\w*|стирк\w*|постира\w*'):
        result.add('wash')
    wage = r'(?:зарплат\w*|заработн\w*\s+плат\w*)'
    unpaid = r'(?:не\s+(?:вы|за|о)?плат\w*|задерж\w*)'
    if (not _has(q, r'не\s+задерж\w*|нет\s+задерж\w*')
            and _has(q, unpaid + r'.{0,45}' + wage + '|' + wage + r'.{0,45}' + unpaid)):
        result.add('wages')
    if _has(q, r'негде\s+(?:\w+\s+){0,4}(?:переночев\w*|ночев\w*|жить|спать)|(?:жить|спать|ночевать)\s+негде|некуда\s+(?:(?:идти|пойти)\s+)?(?:спать|ночев\w*|переночев\w*)|нет\s+места\s+для\s+ночлег\w*'):
        result.add('shelter')
    from .study_guides import route as study_route
    study = study_route(q)
    if study and study['kind'] == 'material':
        result.add(study['id'])
    from .access_guides import route as access_route
    access = access_route(q)
    if access and access['kind'] == 'material':
        result.add(access['id'])
    from .debt_work_guides import route as debt_work_route
    debt_work = debt_work_route(q)
    if debt_work and debt_work['kind'] == 'material':
        result.update(debt_work['ids'])
    from .adjacent_guides import money_route
    money = money_route(q)
    if money and money['kind'] == 'material':
        result.add(money['id'])
    from .federal_guides import match_ids, PAYMENT_IDS, DEMAND_IDS
    result.update(set(match_ids(q)).intersection(PAYMENT_IDS | DEMAND_IDS))
    result.update(set(match_ids(q)).intersection({'university_quota', 'vocational_training', 'utility_compensation'}))
    return result


_INTENT_IDS = {
    **{i:(i,) for i in ('utility_subsidy', 'social_card', 'social_transport', 'gas_connection', 'gas_subsidy',
        'egrn_extract', 'residence_extract', 'deregistration', 'social_rehabilitation', 'orphan_status_proof',
        'status_orientation', 'sanatorium_right', 'child_recreation', 'treatment_travel',
        'rehabilitation_equipment', 'equipment_certificate', 'social_services', 'social_accompaniment',
        'social_service_cost', 'social_assistance', 'social_contract', 'income_evidence',
        'application_tracking', 'payment_delay', 'additional_documents', 'benefit_refusal', 'disability_nsu', 'medical_rehabilitation')},
    **{i:(i,) for i in ('university_quota', 'vocational_training', 'utility_compensation')},
    **{i:(i,) for i in ('orphan_payments','graduation_support','social_scholarship','study_books',
        'study_provision','orphan_unemployment','orphan_wage_topup','bq_pension_who',
        'pension_topup','study_training_allowance','scholarship_increased')},
    **{i:(i,) for i in ('study_pause','college_return','study_transfer','study_debts','work_study_current','mentor_options')},
    **{identifier: (identifier,) for identifier in ('documents_all_lost', 'gosuslugi_create', 'gosuslugi_access', 'registration_barrier', 'oms_policy', 'clinic_attachment', 'care_denial')},
    'money_budget': ('money_budget',), 'money_today': ('money_today',),
    'fraud_money_loss': ('fraud_money_loss',), 'fraud_shared_code': ('fraud_shared_code',),
    'bank_access': ('bank_access',),
    'debt_credit': ('debt_credit',), 'debt_utilities': ('debt_utilities',),
    'debt_collection': ('debt_collection',), 'work_deductions': ('work_deductions',),
    'work_dismissal': ('work_dismissal',), 'work_unregistered': ('work_unregistered',),
    'work_wages': ('work_wages',), 'work_lodging_contract': ('work_lodging_contract',),
    'cover_letter': ('cover_letter',),
    'gas': ('hq_household_gas',),
    'locked': ('hq_housing_problem_locked',),
    'debt': ('hq_utilities_debt',),
    'water': ('hq_household_water',),
    'housing_right': ('hq_state_housing_right',),
    'housing_apply': ('hq_state_housing_apply',),
    'housing_delay': ('hq_state_housing_delay',),
    'housing_move': ('housing_move',),
    'rental': ('hq_rent_view', 'hq_rent_contract'),
    'passport_lost': ('doc_passport_q3',),
    'first_resume': ('resume_first',),
    'first_job': ('first_job', 'resume_first', 'job_minor'),
    'benefit_delay': ('payment_delay',),
    'legal_consultation': ('bq_legal_who', 'bq_legal_help', 'bq_legal_steps'),
    'legal_preparation': ('bq_legal_steps',),
    'wash': ('hq_household_wash',),
    'wages': ('work_wages',),
    'shelter': ('shelter',),
}


def rank_materials(config, query, catalog, *, _split_goals=True):
    """Rank approved full text, keeping concrete matches for each goal."""
    items = list(catalog)
    if not items:
        return []
    query_tokens = set(_tokens(query))
    documents = [set(_tokens(item.get('text', ''))) for item in items]
    frequencies = Counter(token for document in documents for token in document)
    weights = {token: math.log(1 + (len(items) + 1) / (frequencies[token] + 1))
               for token in query_tokens}
    intents = _intents(query)
    boosted = {identifier for intent in intents for identifier in _INTENT_IDS[intent]}
    query_phrase = ' '.join(_WORD.findall(_plain(query)))
    from .federal_guides import match_ids
    federal_ids=set(match_ids(query))
    scored = []
    for index, (item, document) in enumerate(zip(items, documents)):
        if (item.get('id') == 'hq_household_gas' and 'gas' not in intents
                and _has(_plain(query), r'газ\w*')
                and _has(_plain(query), r'\bне\s+пах\w*|газ\w*\s+не\s+пах\w*|нет\s+запах\w*')):
            continue
        if ('rental' in intents and not intents.intersection({'housing_right', 'housing_apply', 'housing_move'})
                and item.get('object') == 'state_housing'):
            continue
        if (intents.intersection({'housing_right', 'housing_apply', 'housing_move'}) and 'rental' not in intents
                and item.get('object') == 'private_rental'):
            continue
        if ('wages' in intents and item.get('object') in {'first_job', 'career'}):
            continue
        title = set(_tokens(item.get('title', '')))
        excerpt = set(_tokens(item.get('excerpt', '')))
        matched_tokens = query_tokens & (document | title | excerpt)
        if (len(query_tokens) >= 2 and len(matched_tokens) / len(query_tokens) < 0.5
                and item.get('id') not in boosted and item.get('id') not in federal_ids):
            continue
        score = sum(weights[token] * (3 * (token in title) + 1.5 * (token in excerpt)
                                      + (token in document)) for token in query_tokens)
        if len(query_phrase) >= 8 and query_phrase in _plain(item.get('text', '')):
            score += 15
        if item.get('id') in boosted:
            score += 55
        if item.get('id') in federal_ids:
            score += 12
        scored.append((score, index, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    if not scored:
        return []
    # Zero/weak matches must not fill the context merely to represent a topic.
    # A strongly identified second goal keeps its own slot in a mixed request.
    floor = max(1.5, scored[0][0] * 0.35)
    ranked = [item for score, _, item in scored if score >= floor][:MAX_CANDIDATES]
    if _split_goals:
        clauses = [part for part in re.split(r'\b(?:и|а|но|также)\b', _plain(query))
                   if len(set(_tokens(part))) >= 2
                   and not _has(part, r'\bне\s+(?:ищ\w*|хоч\w*|нужн\w*|собира\w*)')]
        if len(clauses) >= 2:
            anchors = [rank_materials(config, part, items, _split_goals=False)[:1] for part in clauses]
            anchors = [item for group in anchors for item in group]
            if len({item['id'] for item in anchors}) >= 2:
                return list({item['id']: item for item in [*anchors, *ranked]}.values())[:MAX_CANDIDATES]
    return ranked


def required_resources(selection, question):
    """Search tools follow the user's intent, never model-assigned topics."""
    q = _plain(question)
    found = set()
    from .adjacent_guides import basic_needs, basic_need_goals
    if basic_needs(q):
        found.add('app_help')
    dispute = _has(q, r'не\s+(?:вы|за|о)?плат\w*|задерж\w*.{0,35}зарплат\w*|зарплат\w*.{0,35}задерж\w*|без\s+договора|трудов\w*\s+прав\w*')
    leaving_care = _has(q, r'выпуск\w*|выпуска\w*|детдом\w*') and not dispute
    for clause in re.split(r'[,;.!?]|\bно\b|\bоднако\b', q):
        found.update(_clause_resources(clause, leaving_care=leaving_care))
    if 'housing' in basic_need_goals(q) and not _rental_negated(q):
        found.add('app_rental')
    contract_question = _has(q, r'договор\w*|мошенник\w*|провер\w*\s+услов\w*')
    listing_request = _has(q, r'ищ\w*\s+(?:\w+\s+){0,3}(?:квартир\w*|жиль\w*|комнат\w*)|'
                           r'найти\s+(?:\w+\s+){0,3}(?:квартир\w*|жиль\w*|комнат\w*)|'
                           r'объявлен\w*|вариант\w*\s+(?:квартир\w*|жиль\w*|комнат\w*)')
    if contract_question and not listing_request:
        found.discard('app_rental')
    return [resource for resource in ('app_work', 'app_rental', 'app_help') if resource in found]


def work_search_requested(q, *, leaving_care=False):
    """One explicit work-search predicate for both priority and search actions."""
    q = _plain(q)
    q = re.sub(r'\bнужн\w*\s+справк\w*\s+для\s+работ\w*', '', q)
    work_negative = _has(q, r'\bне\s+(?:ищу|ищем|хочу\s+(?:искать|найти|менять|сменить)|хочу|нужна)\s+(?:работ|ваканси)|(?:работ\w*|ваканси\w*)\s+не\s+ищ|не\s+хочу\s+работать')
    vacancy_information = _has(q, r'отказ\w*\s+от\s+ваканси\w*|подходит\s+ли|возрастн\w*\s+услов\w*|(?:прав\w*|услов\w*|требован\w*|провер\w*)\s+(?:\w+\s+){0,3}(?:ваканси\w*|работ\w*)')
    vacancy_information = vacancy_information or _has(q, r'доплат\w*.{0,40}(?:зарплат|заработн\w*\s+плат)')
    offer = r'(?:работ\w*|подработ\w*|ваканси\w*|стажиров\w*|практик\w*)'
    work = (not vacancy_information and _has(q, r'ваканси\w*|трудоустр\w*')) or _has(q, rf'устроиться\s+на\s+работ\w*|хочу\s+работать|(?:ищ\w*|иск\w*|найти|поиск\w*|подобр\w*|подбери|покаж\w*|нужна|нужен|хочу)\s+(?:\w+\s+){{0,3}}{offer}')
    work = work or (_has(q, r'стажиров\w*|практик\w*') and _has(q, r'предложени\w*|вариант\w*|где\s+посмотр\w*'))
    work = work or (leaving_care and _has(q, r'что\s+делать\s+с\s+работой'))
    # One search verb may govern two coordinated objects: "ищу комнату и
    # работу" requests both searches, even though the verb is not repeated.
    work = work or _has(q, r'\b(?:ищ\w*|найти|подобрать)\b.{0,60}\b(?:квартир\w*|жиль\w*|комнат\w*)\b.{0,35}\bи\b.{0,25}\b(?:работ\w*|подработ\w*|ваканси\w*)\b')
    from .adjacent_guides import basic_need_goals
    earn_goal = 'income' in basic_need_goals(q)
    information_search = _has(q, r'(?:ищ\w*|найти|поиск\w*)\s+(?:информац\w*|ответ\w*|сведени\w*|совет\w*)')
    return bool((work or earn_goal) and not work_negative and not information_search)


def _clause_resources(q, *, leaving_care=False):
    result = []
    if work_search_requested(q, leaving_care=leaving_care):
        result.append('app_work')
    housing_negative = _rental_negated(q) or _has(q, r'\bне\s+(?:ищу|ищем|хочу\s+(?:искать|найти))\s+(?:\w+\s+){0,2}(?:квартир\w*|жиль\w*|комнат\w*)')
    housing = _has(q, r'нужн\w*\s+(?:квартир\w*|жиль\w*|комнат\w*)\s+в\s+(?:аренд\w*|найм)|(?:ищ\w*|найти|поиск\w*|снять|снимать|арендовать)\s+(?:\w+\s+){0,2}(?:квартир\w*|жиль\w*|комнат\w*)')
    housing = housing or (_has(q, r'нужн\w*.*\bкомнат\w*\b') and
                          not _has(q, r'гос\w*\s+жиль\w*|списк\w*\s+на\s+жиль\w*|очеред\w*\s+на\s+квартир\w*'))
    if housing and not housing_negative:
        result.append('app_rental')
    help_negative = _has(q, r'\bне\s+(?:нужн\w*|ищ\w*|хочу)|помощ\w*\s+не\s+нужн')
    if not help_negative and ('shelter' in _intents(q) or _has(q, r'социальн\w*\s+помощ\w*|мест\w*\s+помощ\w*|негде\s+ночев\w*|нечего\s+есть|нет\s+еды|(?:получить|найти|нужен|нужна|нужны|ищу|выдают)\s+(?:\w+\s+){0,3}(?:одежд\w*|ед[ау]|ночлег\w*)')):
        result.append('app_help')
    return result


def urgent_material(config, question, catalog):
    """Return only a reviewed, currently supplied safety card for a live hazard.

    Negation is scoped to individual clauses so an explicitly absent gas smell
    cannot suppress a separate active water leak. Educational/hypothetical and
    resolved historical descriptions continue through normal retrieval.
    """
    q = _plain(question)
    allowed = {item.get('id') for item in catalog}
    if 'personal_safety' in allowed and personal_danger(question):
        return 'personal_safety'
    educational = _has(q, r'(?:объясни|изучаю|расскажи)\s+(?:\w+\s+){0,3}(?:правил\w*|техник\w*\s+безопасност\w*)|доклад\w*|реферат\w*|для\s+урока|учебн\w*|гипотет\w*|когда.нибудь|если\s+(?:вдруг\s+)?(?:запахнет|будет|случится)|на\s+будущее')
    active = set()
    # Keep subject, hazard predicate and negation together. Only a clause
    # containing an actual past event needs its current-event suffix isolated;
    # splitting every "сейчас" would sever "газом сейчас сильно пахнет".
    for clause in re.split(r'[,;.!?]|\bно\b|\bоднако\b', q):
        current_match = re.search(r'\bсейчас\b|до\s+сих\s+пор|\bснова\b', clause)
        historical = _has(clause, r'вчера|раньше|(?:недел\w*|год\w*|месяц\w*|дн\w*)\s+назад|в\s+прошлом|\bне\s+было\b')
        if historical:
            if not current_match:
                continue
            clause = clause[current_match.start():]
        # Educational framing remains the default until a separate explicit
        # present hazard supplies a real event, including its own negation.
        if educational:
            live_event = _has(clause, r'пахнет|пахнит|несет|течет|льется|не\s+пуска\w*|негде\s+(?:пере)?ночев\w*')
            instruction = _has(clause, r'опиши|расскажи|объясни|реферат\w*|доклад\w*')
            if not current_match or not live_event or instruction:
                continue
        intents = _intents(clause)
        if 'gas' in _intents(q) and _has(clause, r'(?:снова|до\s+сих\s+пор)\s+пах\w*\s*$'):
            intents.add('gas')
        if _has(clause, r'\bне\s+(?:чувств\w*|ощущ\w*)\s+запах\w*\s+газ\w*|не\s+(?:пах\w*|несет)|газ\w*\s+не\s+пах\w*|нет\s+(?:запах\w*|утеч\w*)\s+газ|запах\w*\s+газ\w*\s+(?:сейчас\s+)?нет|утеч\w*\s+газ\w*\s+нет'):
            intents.discard('gas')
        if _has(clause, r'нет\s+протеч\w*|не\s+теч\w*|протеч\w*\s+нет'):
            intents.discard('water')
        active.update(intents)
    nodes = {node.get('id'): node for node in config.get('nodes', [])}
    allowed = {item.get('id') for item in catalog}
    for intent in ('gas', 'water', 'locked', 'shelter'):
        if intent in active:
            identifier = _INTENT_IDS[intent][0]
            node = nodes.get(identifier, {})
            if identifier in allowed and node.get('content_status') == 'reviewed_reference':
                return identifier
    return None


def _physical_threat(clause):
    """Keep a violence predicate and its negation with the same speaker act."""
    speech = list(re.finditer(r'угрожа(?:ет|ют)|обеща(?:ет|ют)', clause))
    risk = r'\b(?:избить|избиени(?:ем|ями|е)|избива\w*|убийств(?:ом|ами)|убить|убьет|изобьет|расправ\w*|насили\w*|нож\w*)\b'
    for index, event in enumerate(speech):
        if re.search(r'\bне\s+$', clause[:event.start()]):
            continue
        # A second promise has its own polarity; do not borrow its object.
        end = speech[index + 1].start() if index + 1 < len(speech) else len(clause)
        tail = clause[event.end():min(end, event.end() + 70)]
        for danger in re.finditer(risk, tail):
            prefix = tail[:danger.start()]
            # A denied act or a court allegation is not a promised assault.
            # Keep this local so another affirmative promise still wins.
            if re.search(r'\b(?:не|без)\s+(?:(?:примен\w*|использ\w*)\s+)?$|'
                         r'\bотрица\w*\s+(?:применение\s+)?$|'
                         r'\b(?:за|о|по\s+поводу)\s+$', prefix):
                continue
            return True
    for event in re.finditer(r'сейчас.{0,35}(?:убьет|изобьет|избивает|бьет)\s+(?:меня|нас)', clause):
        if not re.search(r'\bне\s+(?:убьет|изобьет|избивает|бьет)', event.group()):
            return True
    return False


def personal_danger(question):
    """Explicit personal danger, scoped to clauses; no single-word alarm."""
    q = _plain(question)
    q = re.sub(r'\b(?:умираю|умереть)\s+от\s+(?:смеха|скуки)\b', '', q)
    # First keep coordinated objects of one threat together: threatening court
    # and physical violence remains a physical threat. The clause pass below
    # still separates independent goals/negations joined by «и».
    for statement in re.split(r'[,;.!?\n]|\b(?:но|однако)\b', q):
        if not _has(statement, r'реферат|доклад|для\s+урока|если|раньше|вчера|в\s+прошлом|назад') and _physical_threat(statement):
            return True
    for clause in re.split(r'[,;.!?\n]|\b(?:но|однако|и)\b', q):
        if _has(clause, r'реферат|доклад|для\s+урока|если|раньше|вчера|в\s+прошлом|назад'):
            continue
        if _physical_threat(clause):
            return True
        if _has(clause, r'сейчас\s+(?:есть\s+)?(?:угроза|опасность)\s+(?:для\s+)?жизни|сейчас\s+нужн\w*\s+экстренн\w*\s+(?:медицинск\w*\s+)?помощ'):
            return True
        if _has(clause, r'\bникто\s+не\s+угрож|\bне\s+угрож|\bв\s+безопасн|\bне\s+(?:хочу|собираюсь|буду|могу)\s+(?:себе\s+)?(?:причин|навред|уби|умер)'):
            continue
        if _has(clause, r'угрожа\w*.{0,35}(?:увольнен|штраф|отчислен|суд)'):
            continue
        if _has(clause, r'мне\s+(?:сейчас\s+)?угрожа(?:ют|ет)|'
                        r'сейчас.{0,25}(?:меня\s+бьют|боюсь\s+за\s+свою\s+безопасность)|'
                        r'(?:собираюсь|хочу|могу|буду).{0,25}(?:причинить\s+себе\s+вред|навредить\s+себе|убить\s+себя|покончить\s+с\s+собой)|хочу\s+умереть'):
            return True
    return False
