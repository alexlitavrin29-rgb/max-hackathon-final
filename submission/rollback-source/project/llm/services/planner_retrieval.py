"""Offline retrieval over the caller's current, child-visible material catalog.

The catalog is the publication/visibility boundary: never read additional nodes
into ranking. Returned dictionaries are the supplied objects. The provider sees
at most 36 cards (the global top 30 plus support from up to six topics).
"""
import math
import re
from collections import Counter

MAX_CANDIDATES = 36
_WORD = re.compile(r'[а-яa-z0-9]+')
_STOP = set('а и но или в на по к с со у из за от до о об для при как что это ли же бы я мне меня мой мы он она вы ты не нет есть делать нужно можно'.split())
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
    return text


def _tokens(text):
    result = []
    for word in _WORD.findall(_plain(text)):
        if word in _STOP or len(word) < 3:
            continue
        alias = next((root for root, prefixes in _ALIASES if word.startswith(prefixes)), None)
        # Five-character stems cover productive Russian inflections; preserve
        # short words such as газ, УК, ЖКХ through the alias vocabulary.
        result.append(alias or word[:5])
    return result


def _has(text, pattern):
    return bool(re.search(pattern, text))


def _intents(question):
    q = _plain(question)
    result = set()
    if _has(q, r'газ\w*') and _has(q, r'пах\w*|запах\w*|утеч\w*|несет\s+газ\w*'):
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
    if _has(q, r'протеч\w*|протек\w*|течет\s+вод|вод\w*\s+(?:течет|льет)|льет\w*\s+с\s+потолк'):
        result.add('water')
    if _has(q, r'(?:полож\w*|право|полага\w*|сирот\w*)') and _has(q, r'квартир\w*|жиль\w*'):
        result.add('housing_right')
    if _has(q, r'паспорт\w*') and _has(q, r'потер\w*|утер\w*|утрат\w*|укра\w*'):
        result.add('passport_lost')
    if _has(q, r'стира\w*|стирк\w*|постира\w*'):
        result.add('wash')
    if _has(q, r'негде\s+(?:переночев\w*|ночев\w*|жить)|некуда\s+(?:(?:идти|пойти)\s+)?(?:спать|ночев\w*|переночев\w*)|нет\s+места\s+для\s+ночлег\w*'):
        result.add('shelter')
    return result


_INTENT_IDS = {
    'gas': ('hq_household_gas',),
    'locked': ('hq_housing_problem_locked',),
    'debt': ('hq_utilities_debt',),
    'water': ('hq_household_water',),
    'housing_right': ('hq_state_housing_right',),
    'passport_lost': ('doc_passport_q3',),
    'wash': ('hq_household_wash',),
    'shelter': ('shelter',),
}


def rank_materials(config, query, catalog):
    """Rank full source text, preserving strong matches before topic support.

    No per-topic quota discards relevant housing cards. A small reserved tail
    keeps cross-topic alternatives available for ambiguous/multiple intents.
    """
    items = list(catalog)
    if not items:
        return []
    query_tokens = set(_tokens(query))
    documents = [set(_tokens(item.get('text', ''))) for item in items]
    frequencies = Counter(token for document in documents for token in document)
    weights = {token: math.log(1 + (len(items) + 1) / (frequencies[token] + 1))
               for token in query_tokens}
    boosted = {identifier for intent in _intents(query) for identifier in _INTENT_IDS[intent]}
    query_phrase = ' '.join(_WORD.findall(_plain(query)))
    scored = []
    for index, (item, document) in enumerate(zip(items, documents)):
        title = set(_tokens(item.get('title', '')))
        excerpt = set(_tokens(item.get('excerpt', '')))
        score = sum(weights[token] * (3 * (token in title) + 1.5 * (token in excerpt)
                                      + (token in document)) for token in query_tokens)
        if len(query_phrase) >= 8 and query_phrase in _plain(item.get('text', '')):
            score += 15
        if item.get('id') in boosted:
            score += 40
        scored.append((score, index, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    topics = list(dict.fromkeys(item.get('topic') for item in items))[:6]
    chosen = scored[:MAX_CANDIDATES - len(topics)]
    selected_indices = {index for _, index, _ in chosen}
    represented = {item.get('topic') for _, _, item in chosen}
    for topic in topics:
        if topic not in represented:
            match = next(row for row in scored if row[2].get('topic') == topic)
            chosen.append(match)
            selected_indices.add(match[1])
    for row in scored:
        if len(chosen) >= MAX_CANDIDATES:
            break
        if row[1] not in selected_indices:
            chosen.append(row)
            selected_indices.add(row[1])
    chosen.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in chosen]


def required_resources(selection, question):
    """Search tools follow the user's intent, never model-assigned topics."""
    q = _plain(question)
    found = set()
    for clause in re.split(r'[,;.!?]|\bно\b|\bоднако\b', q):
        found.update(_clause_resources(clause))
    return [resource for resource in ('app_work', 'app_rental', 'app_help') if resource in found]


def _clause_resources(q):
    result = []
    work_negative = _has(q, r'\bне\s+(?:ищу|ищем|хочу\s+(?:искать|найти)|нужна)\s+(?:работ|ваканси)|(?:работ\w*|ваканси\w*)\s+не\s+ищ')
    vacancy_information = _has(q, r'отказ\w*\s+от\s+ваканси\w*|подходит\s+ли|возрастн\w*\s+услов\w*|(?:прав\w*|услов\w*|требован\w*|провер\w*)\s+(?:\w+\s+){0,3}(?:ваканси\w*|работ\w*)')
    work = (not vacancy_information and _has(q, r'ваканси\w*|трудоустр\w*')) or _has(q, r'устроиться\s+на\s+работ\w*|хочу\s+работать|(?:ищ\w*|найти|поиск\w*|нужна|нужен)\s+(?:\w+\s+){0,3}(?:работ\w*|подработ\w*)|что\s+делать\s+с\s+работой\s+после\s+выпуск\w*')
    information_search = _has(q, r'(?:ищ\w*|найти|поиск\w*)\s+(?:информац\w*|ответ\w*|сведени\w*|совет\w*)')
    if work and not work_negative and not information_search:
        result.append('app_work')
    housing_negative = _has(q, r'\bне\s+(?:ищу|ищем|хочу\s+(?:снимать|снять|искать|найти))\s+(?:\w+\s+){0,2}(?:квартир\w*|жиль\w*|комнат\w*)')
    housing = _has(q, r'нужн\w*\s+(?:квартир\w*|жиль\w*|комнат\w*)\s+в\s+аренд\w*|(?:ищ\w*|найти|поиск\w*|снять|снимать|арендовать)\s+(?:\w+\s+){0,2}(?:квартир\w*|жиль\w*|комнат\w*)')
    if (housing or 'housing_right' in _intents(q)) and not housing_negative:
        result.append('app_rental')
    help_negative = _has(q, r'\bне\s+(?:нужн\w*|ищ\w*|хочу)|помощ\w*\s+не\s+нужн')
    if not help_negative and ('shelter' in _intents(q) or _has(q, r'социальн\w*\s+помощ\w*|мест\w*\s+помощ\w*|негде\s+ночев\w*|нечего\s+есть|нет\s+еды|(?:получить|найти|нужна|нужны|выдают)\s+(?:\w+\s+){0,3}(?:одежд\w*|ед[ау]|ночлег\w*)')):
        result.append('app_help')
    return result


def urgent_material(config, question, catalog):
    """Return only a reviewed, currently supplied safety card for a live hazard.

    Negation is scoped to individual clauses so an explicitly absent gas smell
    cannot suppress a separate active water leak. Educational/hypothetical and
    resolved historical descriptions continue through normal retrieval.
    """
    q = _plain(question)
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
