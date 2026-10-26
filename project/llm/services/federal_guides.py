"""Checked federal facts projected through the existing shared reference catalogue.

This file contains presentation/visibility rules. Factual prose and source
definitions live once in federal_catalog.json, never in a separate chat prompt.
"""
from copy import deepcopy
from datetime import date, datetime
from functools import lru_cache
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from .help_routes import _allowed, _path


@lru_cache(maxsize=1)
def catalogue():
    return json.loads(Path(__file__).with_name('federal_catalog.json').read_text())


def records():
    return catalogue()['records']


TOPIC_OVERRIDES = {identifier: record['topic'] for record in records()
                   for identifier in record['runtime_ids']}
BRANCHES = {'health':'help', 'benefits':'benefits', 'help':'help',
            'documents':'documents', 'housing':'housing', 'study':'education'}
PAYMENT_RECORDS = {'F33', 'F34', *('F'+str(i) for i in range(53, 62))}
PAYMENT_IDS = {i for r in records() if r['id'] in PAYMENT_RECORDS for i in r['runtime_ids']}
BENEFIT_RECORDS = {'F'+str(i) for i in range(62, 70)}
DEMAND_RECORDS = {'F01', 'F04', 'F05', 'F06', 'F12', 'F13', 'F14', 'F17', 'F19',
                  'F20', 'F21', 'F22', 'F23', 'F24', 'F25', 'F26', 'F32', 'F51', 'F52',
                  *('F'+str(i) for i in range(70, 79))}
DEMAND_IDS = {i for r in records() if r['id'] in DEMAND_RECORDS for i in r['runtime_ids']}


def today():
    return datetime.now(ZoneInfo('Europe/Moscow')).date()


def limits(record, on=None):
    if record['id']!='F51':
        return record['limits']
    on=on or today()
    cutoff=date(on.year,10,1)
    year=on.year+1 if on<cutoff else on.year+2
    return ('По общему правилу выбор меняют до 1 октября на следующий год, но есть специальные случаи. '
            f'Дата обращения: {on:%d.%m.%Y}. При подаче сейчас обычный порядок относится к {year} году. '
            'Изменение с ближайшего января и специальные исключения уточни в СФР; '
            'бот не подтверждает индивидуальное право.')


def _links(record):
    index={link['id']:link for link in catalogue()['links']}
    return [index[identifier] for identifier in record['link_ids']]


def text(record):
    paragraphs=[record['audience'],record['answer'],
                'Ближайшие шаги:\n'+'\n'.join('• '+step for step in record['steps']),
                'Подготовь для обращения (не отправляй документы боту):\n'
                +'\n'.join('• '+item for item in record['prepare']),
                'Условия и ограничения: '+limits(record)]
    for link in _links(record):
        status=link['status']
        label=link['label']
        if status=='portal_shell':
            label='Страница услуги: '+label+' (подача после входа не проверена)'
        elif status not in {'readable','relevant_text'}:
            label='Официальный адрес: '+label+' (доступность подачи не подтверждена)'
        paragraphs.append(f'[{label}]({link["url"]})')
    paragraphs.append('Открытие ссылки не отправляет заявление. Условия конкретной услуги '
                      'и доступный способ обращения уточни в указанном ведомстве или организации.')
    return '\n\n'.join(paragraphs)


def extend_nodes(config, previous):
    """Retain existing publication/role/anchor checks before extending any ID."""
    published={n['id']:n for n in config.get('nodes',[])}
    existing={n['id']:n for n in previous}
    branches={b['id']:b for b in config.get('branches',[])}
    values={'role':'child'}
    root=published.get(config.get('menu'))
    if (not root or root.get('content_status') in {'draft','archived','unverified_reference'}
            or not _allowed(root,branches,values)):
        return previous
    additions=[]
    for record in records():
        for identifier in record['runtime_ids']:
            original=published.get(identifier)
            if original and original.get('content_status') in {'draft','archived','unverified_reference'}:
                continue
            base=existing.get(identifier) or original
            if base and not _allowed(base,branches,values):
                continue
            if record['disposition']=='extend':
                if not base or not _allowed(base,branches,values):
                    continue
                if identifier not in existing and not _path(config,identifier,values):
                    continue
            branch=base['branch'] if base else BRANCHES[record['topic']]
            if 'child' not in branches.get(branch,{}).get('roles',[]):
                continue
            extend=record['disposition']=='extend'
            node=deepcopy(base) if base and extend else dict(id=identifier,title=record['title'],
                                                kind='message',branch=branch,tasks=[],buttons=[],sources=[])
            node['text']=(node.get('text','')+'\n\n' if base and extend else '')+text(record)
            if record['id'] in PAYMENT_RECORDS | BENEFIT_RECORDS | DEMAND_RECORDS | {'F03','F32','F36','F37','F38','F40'}:
                node['tasks']=record['steps']
            node['content_status']='source_checked_reference'
            additions.append(node)
    replacement={n['id'] for n in additions}
    return [*(n for n in previous if n['id'] not in replacement),*additions]


def payment_navigation(config):
    """The MAX menu reads the same checked cards; stored publication stays intact."""
    menu=next((n for n in config.get('nodes',[]) if n['id']=='bq_group_payments'),None)
    branches={b['id']:b for b in config.get('branches',[])}
    if (not menu or menu.get('content_status') in {'draft','archived','unverified_reference'}
            or not _allowed(menu,branches,{'role':'child'})
            or not _path(config,menu['id'],{'role':'child'})):
        return {}
    from .reference_guides import nodes
    cards={n['id']:deepcopy(n) for n in nodes(config) if n['id'] in PAYMENT_IDS}
    for node in cards.values():
        node['format']='markdown'
        node['buttons']=[dict(label='Назад',target=menu['id'],values={'navigation':'back'}),
                         dict(label='К оглавлению',target=config['menu'],values={'navigation':'contents'})]
    menu=deepcopy(menu)
    targets={b.get('target') for b in menu.get('buttons',[])}
    menu['buttons']=[dict(label=n['title'],target=n['id']) for n in cards.values()
                     if n['id'] not in targets]+menu.get('buttons',[])
    return {**cards,menu['id']:menu}


def context(identifier):
    record=next((r for r in records() if identifier in r['runtime_ids']),None)
    if record is None:
        return {}
    result=dict(source_card_ids=[record['id']],audience=record['audience'],
                basis=record['basis'],conditions=limits(record),
                requires_basis_check=record['requires_basis_check'],
                basis_question=record['basis_question'],
                steps=record['steps'],prepare=record['prepare'],
                permitted_links=[dict(id=l['id'],url=l['url'],purpose=l['purpose'],status=l['status'])
                                 for l in _links(record)])
    if record['disposition']=='add':
        result['allowed_actions']=check_actions(record)
    return result


def check_actions(record):
    """A plan to check conditions, not a promise to receive the measure."""
    titles=[record['steps'][0],*('Подготовить для консультации: '+p for p in record['prepare'][:2])]
    return [dict(id=record['id'].lower()+'_check_'+str(i),title=title)
            for i,title in enumerate(titles)]


# These are intent aliases, not a second store of facts, contacts or conditions.
_INTENTS = {
 'F01': r'(?:не\s+зна\w*|не\s+понима\w*|выясн\w*|подтверд\w*).{0,55}статус|какой.{0,20}статус',
 'F02': r'(?:23|24|двадцат\w*).{0,65}(?:прав|поддерж|потер|выпуск|льгот)',
 'F03': r'бесплатн\w*.{0,20}медицинск\w*.{0,30}помощ',
 'F04': r'санатор', 'F05':r'лагер|путевк\w*.{0,25}(?:отдых|оздоров)',
 'F06':r'(?:оплат\w*|билет\w*|дорог\w*|проезд\w*).{0,30}(?:лечени|отдых|путевк)',
 'F07':r'(?:потер\w*|нет|восстанов\w*|ошибк\w*).{0,20}(?:полис|омс)|полис.{0,20}ошибк',
 'F08':r'прикреп\w*.{0,35}поликлиник|переех\w*.{0,50}поликлиник',
 'F09':r'(?:отказ\w*|не\s+принима\w*).{0,30}(?:врач|прием|омс)|врач.{0,30}(?:платн|отказ)',
 'F10':r'мсэ|оформ\w*.{0,20}инвалид|установ\w*.{0,20}инвалид',
 'F11':r'(?:измен\w*|поменя\w*|переосвидетел\w*).{0,30}групп\w*|групп\w*.{0,20}измен',
 'F12':r'ипра|\bтср\b|средств\w*.{0,15}реабилитац|коляск',
 'F13':r'(?:электронн\w*\s+сертификат|сертификат.{0,30}(?:тср|реабилитац))',
 'F14':r'социальн\w*\s+обслуживан|соцобслуживан|не\s+справля\w*.{0,20}быт',
 'F15':r'(?:нет|не\s+хватает|нечего).{0,15}(?:ед[ауы]|одежд|есть)|голод',
 'F16':r'негде\s+(?:ночев|жить)|некуда\s+(?:идти|спать)|ночлег',
 'F17':r'социальн\w*\s+сопровожд|сопровожд\w*.{0,25}оформлен',
 'F18':r'психолог', 'F19':r'(?:плат\w*|стоимост\w*|оплат\w*).{0,25}социальн\w*\s+обслуживан|социальн\w*\s+обслуживан.{0,30}(?:плат|стоимост)',
 'F20':r'государственн\w*\s+социальн\w*\s+помощ|помощ\w*.{0,25}низк\w*\s+доход|материальн\w*\s+помощ',
 'F21':r'соцконтракт|социальн\w*\s+контракт',
 'F22':r'справк\w*.{0,25}доход|подтверд\w*.{0,15}доход',
 'F23':r'заявлен\w*.{0,30}(?:принят|отправ|рассмотр|статус)|статус.{0,25}заявлен',
 'F24':r'(?:выплат\w*.{0,20}назнач|назнач\w*.{0,20}выплат).{0,55}(?:нет|не\s+приш|не\s+поступ)|денег\s+нет.{0,30}назнач|(?:пособи\w*|выплат\w*).{0,25}(?:не\s+(?:приш|поступ|перечисл)|задерж)|задерж\w*.{0,25}(?:пособи|выплат)',
 'F25':r'не\s+принима\w*.{0,15}документ|требу\w*.{0,20}лишн\w*\s+справ|документ\w*.{0,20}не\s+принима',
 'F26':r'(?:отказ\w*|приостанов\w*).{0,30}(?:выплат|пособ|доход)|(?:выплат|пособ).{0,25}(?:отказ|приостанов)',
 'F27':r'(?:ошибк|опечатк).{0,30}документ|документ.{0,30}(?:ошибк|опечатк)',
 'F28':r'досудебн\w*\s+жалоб|пожалова\w*.{0,30}госуслуг|жалоб\w*.{0,25}госуслуг',
 'F29':r'сколько\s+ждать\s+ответ|срок.{0,25}(?:рассмотрен|ответ|жалоб)',
 'F30':r'портал.{0,35}(?:не\s+работ|не\s+дает|ошиб|сбой)|сбой.{0,20}портал',
 'F31':r'нет\s+регистрац|без\s+регистрац', 'F32':r'субсиди\w*.{0,45}(?:жку|коммунал|квартир)|(?:жку|коммунал).{0,35}субсиди',
 'F33':r'подъемн|\bвыпускные\b|(?:выдать|выдадут|обеспечени\w*).{0,30}выпуск|выпуск.{0,35}(?:выдать|выдадут|обеспечени|одежд)|(?:денег|деньги|пособи\w*|выплат\w*).{0,25}(?:при|после|на)\s+выпуск',
 'F34':r'поддержк\w*.{0,45}(?:учеб|учебы|учиться)|социальн\w*\s+стипенди',
 'F35':r'академическ|академ\b', 'F36':r'проезд.{0,40}(?:учеб|учебу|домой)',
 'F37':r'втор\w*.{0,25}(?:спо|образован|колледж)',
 'F38':r'подготовительн\w*\s+отделен|подготов\w*.{0,20}поступлен.{0,15}вуз',
 'F39':r'(?:прежн\w*\s+центр|центр).{0,35}каникул|каникул.{0,30}центр',
 'F40':r'пенси\w*.{0,30}потер\w*\s+кормильц|потер\w*\s+кормильц.{0,35}пенси',
 'F41':r'пишут.{0,25}(?:соцзащит|банк)|(?:банк|соцзащит).{0,35}(?:ссылк|обман|проверить)',
 'F42':r'(?:передал|сообщил|назвал).{0,20}код|(?:обман|мошен).{0,25}(?:деньг|перевел)|(?:перевел|украли|списали).{0,20}деньг',
 'F43':r'единое\s+пособи', 'F44':r'(?:родил\w*\s+ребенок|рождени\w*\s+ребенка|единовременн\w*\s+пособи)',
 'F45':r'пособи\w*.{0,15}уход\w*.{0,20}ребен|полутора\s+лет|1[,.]5\s+лет',
 'F46':r'беремен\w*.{0,35}(?:уч\w*|студент)|студент\w*.{0,30}беремен',
 'F47':r'материнск\w*\s+капитал|маткапитал', 'F48':r'погребени|похорон',
 'F49':r'инвалидност.{0,40}пенси|пенси.{0,30}инвалидност', 'F50':r'\bедв\b',
 'F51':r'\bнсу\b|набор\w*\s+социальн\w*\s+услуг|(?:путевк|санатор|проезд).{0,40}инвалидност|инвалидност.{0,40}(?:путевк|санатор|проезд)',
 'F52':r'медицинск\w*\s+реабилитац|реабилитаци\w*.{0,25}(?:после|лечени|врач)',
 'F54':r'учебник|канцеляр|учебн\w*\s+литератур|письменн\w*\s+принадлеж',
 'F55':r'гособеспечени|государственн\w*\s+обеспечени|(?:выплат\w*|компенсац\w*|деньг\w*).{0,30}(?:питани|одежд).{0,40}(?:учеб|колледж|вуз)',
 'F56':r'п[ао]соби\w*\s+по\s+безработиц|(?:сирот|детдом).{0,70}(?:бирж\w*\s+труд|центр\w*\s+занятост|\bцзн\b)|(?:сколько|выплат|платят).{0,30}бирж\w*\s+труд',
 'F57':r'доплат\w*.{0,40}(?:зарплат|заработн\w*\s+плат)|(?:зарплат|трудоустройств).{0,40}доплат',
 'F58':r'пенси\w*.{0,35}потер\w*\s+кормильц|потер\w*\s+кормильц.{0,35}пенси|оба\s+родител\w*\s+неизвестн',
 'F59':r'(?:социальн\w*\s+)?доплат\w*.{0,25}пенси|пенси\w*.{0,40}(?:прожиточн|минимум)',
 'F60':r'(?:\bовз\b|ограниченн\w*\s+возможност).{0,80}(?:переподготов|пособи)|переподготов\w*.{0,80}(?:\bовз\b|пособи)',
 'F61':r'повышенн\w*.{0,30}стипенди|стипенди\w*.{0,30}повышенн',
 'F63':r'особ\w*\s+квот|квот\w*.{0,35}(?:вуз|поступлен)|поступлен\w*.{0,35}квот',
 'F64':r'профподготов|профессиональн\w*\s+подготовк',
 'F65':r'компенсаци\w*.{0,35}(?:жку|жкх|коммунал)|(?:жку|жкх|коммунал).{0,35}компенсаци',
 'F70':r'социальн\w*\s+карт|соцкарт',
 'F71':r'социальн\w*\s+такси|соцтакси|(?:перевозк|транспорт).{0,25}маломобильн',
 'F72':r'догазификац|(?:подключ\w*|провес\w*|подвест\w*).{0,25}газ',
 'F73':r'(?:субсиди|компенсаци|возмещени|выплат).{0,35}(?:газификац|газов\w*\s+оборудован|подключени\w*\s+газ)|(?:газификац|газов\w*\s+оборудован).{0,35}(?:субсиди|компенсаци|возмещени)',
 'F74':r'\bегрн\b|выписк\w*.{0,25}(?:недвижим|прав\w*\s+собственност)',
 'F75':r'(?:справк|сведени|выписк).{0,30}(?:зарегистрирован|регистраци|состав\w*\s+семьи|домов\w*\s+книг)',
 'F76':r'\bвыписаться\b|\bсняться\s+с\s+регистраци',
 'F77':r'социальн\w*\s+реабилитац|соцреабилитац',
 'F78':r'(?:справк|документ|подтверждени|подтвержда|подтверд).{0,30}(?:статус\w*\s+(?:ребенк\w*[- ]?)?сирот|сиротск\w*\s+статус|ребенк\w*[- ]сирот)',
}


def match_records(question):
    q=str(question or '').casefold().replace('ё','е')
    q=re.sub(r'\bне\s+(?:нуж\w*|хочу|интересует)\s+[^,.!?;\n]*?'
             r'(?=$|[,.!?;\n]|\s+(?:и|но|а|зато)\s+(?:(?:я|мне)\s+)?'
             r'(?:хочу|нуж\w*|интересует|ищу)\b)','',q)
    selected={key for key,pattern in _INTENTS.items() if re.search(pattern,q)}
    # A named payment owns its question. The overview only owns discovery;
    # loss of an existing payment still belongs to the problem routes.
    if 'F40' in selected and not re.search(r'работ|подработ|трудоустр',q):
        selected.discard('F40')
    if 'F40' in selected:selected.discard('F58')
    if 'F59' in selected:
        selected.discard('F58');selected.discard('F40')
    if 'F61' in selected or 'F54' in selected or 'F55' in selected or 'F60' in selected:
        selected.discard('F34')
    if 'F57' in selected:selected.discard('F56')
    if 'F33' in selected and not re.search(r'рождени|родил|ребен',q):
        selected.discard('F44')
    if selected.intersection({'F56','F57','F58','F59','F60','F61'}):
        selected.discard('F33')
    if (not selected and not re.search(r'не\s+приш|не\s+поступ|задерж|приостанов|отказ|назначен|справк',q)
            and re.search(r'выплат|пособи|платеж',q) and re.search(
            r'какие|все\b|основн|положен|полага|могу\s+оформить|узнать',q)):
        selected.add('F53')
    if 'F18' in selected and re.search(r'(?:работ\w*|поступ\w*|учиться).{0,40}психолог',q):
        from .adjacent_guides import needs_support
        if not needs_support(q):selected.discard('F18')
    # A certificate and a service cost are specific sub-intents, not extra goals.
    for specific,general in [('F13','F12'),('F19','F14'),('F18','F14'),('F51','F04'),('F51','F06'),('F78','F01')]:
        if specific in selected:selected.discard(general)
    if 'F42' in selected:selected.discard('F41')
    if 'F24' in selected:selected.discard('F23')
    if 'F73' in selected:
        clauses=re.split(r'[,;.!?\n]|\b(?:и|а|но|также)\b',q)
        for general in ['F72','F32']:
            if general=='F32' and re.search(r'жк[ух]|коммунал',q):
                continue
            # Equipment/connection named inside a request for its subsidy is
            # the object of that request. Retain a separately stated goal.
            if not any(re.search(_INTENTS[general],clause)
                       and not re.search(_INTENTS['F73'],clause) for clause in clauses):
                selected.discard(general)
    if 'F26' in selected:
        refusals = list(re.finditer(r'отказ\w*', q))
        if refusals and all(re.search(r'\bне\s+$', q[max(0, event.start()-12):event.start()])
                            or re.match(r'отказа?\s+не\s+было', q[event.start():])
                            for event in refusals):
            selected.discard('F26')
    if 'турист' in q or 'турпоезд' in q:selected.discard('F05')
    if not selected and re.search(r'ребен\w*.{0,25}(?:какие|пособи)|(?:какие|пособи).{0,25}ребен',q):
        selected={'F43','F44','F45','F47'}
    return [record for record in records() if record['id'] in selected]


def match_ids(question):
    return [identifier for record in match_records(question) for identifier in record['runtime_ids']]


def _dependent_step(question,event):
    """An explicitly linked preparation step is not a separate chosen goal."""
    before=question[:event.start()]
    after=question[event.end():]
    return (re.search(r'\b(?:для\s+(?:этого|него|нее|(?:его|ее)\s+оформления)|'
                      r'чтобы\s+(?:его|ее)\s+оформить)\s*[,:\-]?\s*(?:(?:нужно|надо)\s+)?$',before)
            or re.match(r'\s+для\s+(?:этого|(?:его|ее)\s+оформления)\b',after))


def prepared_response(config,question,question_id,answer,operation,*,prepared_only=False):
    """Read general facts freely; a condition check never establishes entitlement."""
    if (question_id=='benefits_kind'
            and str(answer or '').strip().casefold() in {'не знаю','неизвестно','не помню'}):
        # The existing identification route owns an unnamed payment. A report
        # of refusal or delay does not establish which payment it concerns.
        return None
    from .access_guides import route as access_route
    access = access_route(question + '\n' + (answer or ''), question_id=question_id, answer=answer)
    if access and access.get('id') == 'care_denial':
        # A reported refusal is a more specific goal than obtaining a policy.
        return None
    from .planner_ai import CATALOG,topic_clarification
    from .handbook import handbook_material
    from .planner_content import NO_ANSWER,build_information,material_catalog
    matched=match_records(question)
    available={m['id'] for m in material_catalog(config)}
    matched=[r for r in matched if any(i in available for i in r['runtime_ids'])]
    if (matched and not any(r['id'] in PAYMENT_RECORDS for r in matched)
            and all(r['disposition']=='extend' for r in matched)
            and not re.search(r'положен|могу\s+ли|имею\s+право',question,re.I)):
        # Their accepted prepared routes already own dependent steps, priority,
        # immediate-needs notices and explicit search actions. They now read
        # the extended facts through the same catalogue.
        from .adjacent_guides import prepared_route
        selected=answer if question_id=='topic_choice' and answer in CATALOG else None
        legacy=prepared_route(question+'\n'+(answer or ''),selected,
                              question_id=question_id,answer=answer)
        if legacy and legacy['kind'] in {'material','comparison','choice'}:
            return None
    original_matches=len(matched)
    basis_record=next((r for r in records() if question_id=='fed_'+r['id'].lower()+'_basis'),None)
    basis_followup=question_id=='federal_basis' or basis_record is not None
    priority_chosen=(question_id=='topic_choice' and answer in CATALOG) or question_id=='federal_priority'
    if basis_record:
        matched=[r for r in matched if r['id']==basis_record['id']]
        priority_chosen=True
    if question_id=='topic_choice' and answer in CATALOG:
        matched=[r for r in matched if r['topic']==answer]
    rental_requested=(bool(re.search(r'(?:снять|ищ\w*|найти|арендовать)\s+(?:\w+\s+){0,2}(?:квартир\w*|жиль\w*|комнат\w*)',question,re.I))
                      and not re.search(r'\bне\s+(?:хочу\s+)?(?:снять|ищу|найти|арендовать)',question,re.I))
    utility_and_rent=rental_requested and any(r['id']=='F32' for r in matched)
    if question_id=='federal_priority' and answer=='app_rental' and utility_and_rent:
        from .planner_content import enrich
        return enrich(config,dict(topic='housing',selected_topic='housing',material_ids=[]),
                      question,suggest_actions=False,intent_question='Хочу найти квартиру в аренду')
    identifiers=[i for r in matched for i in r['runtime_ids']]
    if question_id=='federal_priority':
        if answer not in identifiers:
            return dict(status='information',tasks=[],information=dict(text=NO_ANSWER,sources=[],priority='normal'))
        if all(r['id'] in DEMAND_RECORDS for r in matched):
            # Explicit independent service goals remain available after choosing
            # the first one, including goals from the same sphere.
            identifiers=[answer,*(i for i in identifiers if i!=answer)][:3]
            matched=sorted((r for r in matched if r['runtime_ids'][0] in identifiers),
                           key=lambda r:identifiers.index(r['runtime_ids'][0]))
        else:
            identifiers=[answer]
            matched=[r for r in matched if answer in r['runtime_ids']]
    if not identifiers:
        if basis_followup:
            return dict(status='information',tasks=[],information=dict(text=NO_ANSWER,sources=[],priority='normal'))
        return None
    if utility_and_rent and not priority_chosen:
        return dict(status='clarify',question_id='federal_priority',question='Что решаем сначала: субсидию или поиск аренды?',
                    choices=[dict(id='utility_subsidy',title='Субсидия на ЖКУ'),dict(id='app_rental',title='Поиск аренды')])
    topics=list(dict.fromkeys(r['topic'] for r in matched))
    if not priority_chosen:
        from .planner_retrieval import work_search_requested
        if work_search_requested(str(question).casefold().replace('ё','е')) and 'work' not in topics:
            topics.append('work')
        q=str(question).casefold().replace('ё','е')
        for topic,pattern in (
            ('housing',r'(?:снять|найти|арендовать|ищу)\s+(?:\w+\s+){0,2}(?:жиль|квартир|комнат)'),
            ('documents',r'(?:восстановить|получить|оформить)\s+(?:паспорт|документ|снилс|аттестат)'),
            ('study',r'(?:хочу|нужно)\s+(?:учиться|поступить)'),
        ):
            affirmative=any(not re.search(r'\bне\s+(?:хочу\s+)?$',q[:m.start()])
                            and not re.match(r'\s+(?:(?:мне|сейчас)\s+)?не\s+(?:нуж|хоч)',q[m.end():])
                            and not _dependent_step(q,m)
                            for m in re.finditer(pattern+r'\w*',q))
            if affirmative and topic not in topics:
                topics.append(topic)
    if len(topics)>1:
        if (matched and (all(r['id'] in PAYMENT_RECORDS for r in matched)
                         or all(r['id'] in DEMAND_RECORDS for r in matched))
                and set(topics)<=set(r['topic'] for r in matched)):
            # Education and pension are different spheres, but this choice
            # concerns two concrete payments. Keep both available by ID.
            topics=['benefits']
    if len(topics)>1:
        return topic_clarification(topics[:3])
    if len(matched)>1 and question_id!='federal_priority':
        return dict(status='clarify',question_id='federal_priority',
                    question='С чего начнём? Выбери первый вопрос.',
                    choices=[dict(id=r['runtime_ids'][0],title=r['title']) for r in matched[:6]])
    cards=[handbook_material(config,i) for i in identifiers]
    cards=[c for c in cards if c]
    if not cards:
        return dict(status='information',tasks=[],information=dict(text=NO_ANSWER,sources=[],priority='normal'))
    identifiers=[c['id'] for c in cards][:3]
    information=build_information(config,identifiers)
    information['text']='\n\n'.join(c['title']+'\n\n'+c['text'] for c in cards[:3])
    information['priority']='normal'
    if all(r['id'] in PAYMENT_RECORDS | BENEFIT_RECORDS for r in matched) or (
            len(matched)==1 and matched[0]['id']=='F40'):
        from .planner_content import enrich
        selection=dict(topic=matched[0]['topic'],material_ids=identifiers,
                       tasks=[dict(title=step,material_ids=[c['id']])
                              for c in cards[:3] for step in next(
                                  r['steps'] for r in matched if c['id'] in r['runtime_ids'])])
        result=enrich(config,selection,question,intent_question=question+'\n'+(answer or ''))
        if operation!='draft_plan':
            result.update(status='information',tasks=[])
        result['information']=information
        return result
    personal=re.search(r'\bхочу\b|положен|могу\s+ли|имею\s+право|получить\s+мне',question,re.I)
    right_claim=re.search(r'положен|могу\s+ли|имею\s+право',question,re.I)
    general=re.search(r'общие\s+условия|общая\s+справка',question,re.I)
    conditional=any(r['requires_basis_check'] for r in matched)
    new_measure=any(r['disposition']=='add' for r in matched)
    needs_check=right_claim or (new_measure and (personal or operation=='draft_plan'))
    if conditional and needs_check and not general and not basis_followup:
        basis_id='fed_'+matched[0]['id'].lower()+'_basis' if original_matches>1 or priority_chosen else 'federal_basis'
        return dict(status='clarify',question_id=basis_id,
                    question=matched[0]['basis_question'],
                    information=information,choices=[dict(id='read',title='Читать общую справку'),
                                                     dict(id='check',title='Уточнить основание и условия')])
    if basis_followup and answer=='check' and operation=='draft_plan':
        from .planner_content import enrich
        selection=dict(topic=matched[0]['topic'],selected_topic=matched[0]['topic'],material_ids=identifiers,
                       tasks=[dict(title=a['title']) for a in check_actions(matched[0])])
        return enrich(config,selection,question,intent_question='Проверить условия выбранной помощи')
    if all(r['id'] in DEMAND_RECORDS for r in matched):
        from .planner_content import enrich
        selection=dict(topic=matched[0]['topic'],material_ids=identifiers,
                       tasks=[dict(title=step,material_ids=[c['id']])
                              for c in cards[:3] for step in next(
                                  r['steps'] for r in matched if c['id'] in r['runtime_ids'])])
        result=enrich(config,selection,question,intent_question=question+'\n'+(answer or ''))
        if operation!='draft_plan':
            result.update(status='information',tasks=[])
        result['information']=information
        return result
    if prepared_only or basis_followup or question_id=='federal_priority':
        return dict(status='information',tasks=[],information=information)
    return None
