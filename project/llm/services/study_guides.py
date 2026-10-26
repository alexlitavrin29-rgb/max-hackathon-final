"""Source-checked educational navigation; no admission, role or service promises."""
import re

PAUSE_RULES='https://www.sao.ru/educat/Docs/prikaz_303.pdf'
EDUCATION_FAQ='https://minobrnauki.gov.ru/open_ministry/reception/faq/v-sfere-obrazovaniya/'
DEBT_RULES='https://epp.genproc.gov.ru/ru/proc_18/activity/legal-education/explain/e192108/'
MENTOR_PROGRAM='https://krasbezsirot.ru/nastavnichestvo'
MENTOR_CONTACT='https://krasbezsirot.ru/needhelp'
SERVICE_ACTIONS={'study_debts':[dict(label='Академические долги: разъяснение статьи58',url=DEBT_RULES,
    description='О промежуточной аттестации и запрете платы; свой график и использованные попытки уточни в учреждении.')]}
TOPIC_OVERRIDES={i:'study' for i in ('study_pause','college_return','study_transfer','study_debts','work_study_current')}
TOPIC_OVERRIDES['mentor_options']='help'
LIMITS=('Общие правила относятся к программам СПО и высшего образования. Уточни в своём учреждении '
        'локальный порядок, документы и последствия для оплаты, стипендии и иных мер поддержки. '
        'Бот не гарантирует место, восстановление, индивидуальный график или сохранение выплат. '
        'Документы предъявляют учреждению, не отправляй их в бот.')
GUIDES=[
 dict(id='study_pause',anchor='college9',title='Перерыв в учёбе и выход из академического отпуска',
      text=('Если ты ещё учишься в колледже или вузе и временно не можешь продолжать обучение, '
            'обсуди основание академического отпуска в учебной части или деканате. '
            'По действующему приказу №303 решение требует заявления руководителю и документов по основанию. '
            'Если подтверждающих документов нет, уточни порядок рассмотрения комиссией; не прекращай посещение '
            'самостоятельно, пока решение не оформлено. Академические долги и отчисление — другие ситуации.\n\n'
            'Если уже находишься в академическом отпуске, проверь дату его окончания и порядок возвращения '
            'в учреждении. Для досрочного выхода нужны заявление и приказ о допуске; это не новое поступление.\n\n'
            f'[Действующий порядок академического отпуска, приказ303]({PAUSE_RULES})'),
      tasks=['Уточнить в учебной части или деканате основание и порядок заявления на отпуск либо возвращение.',
             'Проверить решение учреждения и согласовать следующие действия по своему учебному статусу.']),
 dict(id='college_return',anchor='college9',title='Восстановиться после отчисления из колледжа или вуза',
      text=('После отчисления сначала обратись в учебную часть прежнего колледжа или деканат вуза. '
            'Уточни основание и дату отчисления, наличие мест, порядок восстановления и учёт освоенных дисциплин. '
            'Статья62 различает отчисление по своему заявлению и инициативе учреждения: во втором случае '
            'условия определяются локальным актом. Общую гарантию для одного основания нельзя переносить на другое. '
            'Если восстановление невозможно, попроси объяснить причину и отдельно обсуди варианты продолжения '
            'образования; обычное новое поступление не заменяет проверку восстановления.\n\n'
            f'[Минобрнауки: восстановление, раздел7.4]({EDUCATION_FAQ})'),
      tasks=['Связаться с учебной частью прежнего колледжа или деканатом вуза и уточнить условия восстановления.',
             'Узнать основание отчисления, доступные места и документы по локальному порядку.']),
 dict(id='study_transfer',anchor='college9',title='Перевестись в другой колледж или вуз',
      text=('Если ты ещё учишься и хочешь перевестись, сначала уточни условия у принимающего учреждения. '
            'Спроси про программу соответствующего уровня, свободные места, зачёт результатов и возможную '
            'разницу дисциплин. Порядки для СПО и высшего образования различаются. Затем согласуй документы '
            'и последовательность с учебной частью исходного учреждения. Не отчисляйся по собственному желанию '
            'до проверки порядка перевода: перевод и восстановление после отчисления — разные процедуры.\n\n'
            f'[Минобрнауки: перевод, раздел7.5]({EDUCATION_FAQ})'),
      tasks=['Уточнить у принимающего учреждения условия перевода на выбранную программу.',
             'Согласовать с исходным учреждением документы и последовательность перевода.']),
 dict(id='study_debts',anchor='college9',title='Разобраться с академической задолженностью',
      text=('Долги по предметам не равны кредитам или перерыву по личным обстоятельствам. '
            'Запроси в учебной части или деканате перечень задолженностей, результаты аттестации, '
            'использованные попытки и действующий график пересдач. Уточни причины пропуска и документы '
            'при уважительных обстоятельствах. За промежуточную аттестацию и её повторное прохождение '
            'для погашения академического долга плата не взимается. Если уже издан приказ об отчислении, '
            'проверь его основание и порядок восстановления отдельно. Не считай академический отпуск '
            'автоматическим способом отменить задолженность.\n\n'
            f'[Прокуратура: аттестация и академические долги, статья58]({DEBT_RULES})'),
      tasks=['Получить у учебной части или деканата перечень задолженностей и график пересдач.',
             'Уточнить использованные попытки, основания и доступный порядок для своей ситуации.']),
 dict(id='work_study_current',anchor='college9',title='Продолжить учёбу и согласовать работу',
      text=('Если учёба сейчас в приоритете или ты уже работаешь и хочешь закончить колледж либо вуз, '
            'сопоставь расписание, дорогу и время на подготовку с рабочим графиком. Уточни в учреждении '
            'доступную форму и расписание своей программы, отдельно обсуди график с работодателем. '
            'Это не обещание учебного отпуска или особых трудовых льгот. Для несовершеннолетних условия '
            'труда проверяются отдельно. Поиск новой работы не добавляется автоматически.\n\n'
            f'[Минобрнауки: вопросы обучения и условия учреждения]({EDUCATION_FAQ})'),
      tasks=['Уточнить в учреждении расписание и варианты продолжения своей программы.',
             'Сопоставить учебную нагрузку с работой и обсудить возможный график с работодателем.']),
 dict(id='mentor_options',anchor='college9',title='Взрослый для обсуждения шагов: условия наставничества',
      text=('Наставник может помочь обсудить планы и повседневные шаги, но не заменяет психолога '
            'или экстренную помощь. В программе фонда «Жёлтый Аист» опубликованы группы: воспитанники '
            'детских домов Красноярского края 12–18 лет, выпускники 16–23 лет, дети из опекунских семей 12–18 лет. '
            'Для разных групп форматы различаются; сопровождение выпускника не означает гарантированный '
            'подбор личного наставника.\n\nПлатность, свободные места и возможность участия вне Красноярского края '
            'не подтверждены. Сначала уточни условия у оператора на его сайте. Возраст и статус не выводятся '
            'из роли в приложении. Если условия не подходят, пока у нас недостаточно проверенных материалов '
            'по этой ситуации для подбора другой программы. Бот не отправляет анкеты; контакты и документы '
            'передают только выбранному оператору после ознакомления с условиями, не в бот.\n\n'
            f'[Программа: аудитория и форматы]({MENTOR_PROGRAM})\n'
            f'[Оператор: условия обращения за помощью]({MENTOR_CONTACT})'),tasks=[]),
]
GUIDES=[dict(g,text=g['text']+'\n\n'+LIMITS) if g['id']!='mentor_options' else g for g in GUIDES]


def route(question,selected_topic=None,*,question_id=None,answer=None):
    q=str(question or '').casefold().replace('ё','е')
    a=str(answer or '').casefold().replace('ё','е').strip()
    has=lambda pattern:bool(re.search(pattern,q))
    educational=has(r'учеб|учусь|учиться|учит|колледж|университет|\bвуз|сесси|предмет|академ|отчисл')
    mentor=any(not re.search(r'\bне\s+(?:(?:хочу|нужен|нужна)\s+)?$',q[:event.start()])
               and not re.match(r'\s+(?:мне\s+)?не\s+нуж',q[event.end():])
               for event in re.finditer(r'наставник\w*|взросл\w*.{0,30}обсудить\s+шаг|взросл\w*.{0,30}обсуждени\w*\s+шаг',q))
    if not educational and not mentor:return None
    returning_context=has(r'вернуться|продолжить|восстановиться|отчисл')
    uncertain_status=(not a and has(r'\bне\s+(?:знаю|уверен\w*|помню|понимаю)\b')
                      and has(r'\b(?:учусь|учусь\s+еще|учусь\s+ещё)\b')
                      and has(r'\bотчисл\w*'))
    if returning_context and question_id=='study_kind' and a in {'не знаю','неизвестно','не помню'}:
        return dict(kind='study_gap')
    if has(r'\bшкол\w*') and not has(r'колледж|университет|\bвуз'):
        return dict(kind='study_gap')
    def actual_event(pattern):
        for event in re.finditer(pattern,q):
            prefix=q[:event.start()]
            denied=re.search(r'\bне(?:\s+(?:был|была|были|еще|пока|восстановление|восстановиться)){0,3}\s+$',prefix)
            prospective=re.search(r'\b(?:могу|может|могут|можем|буду|будет|будут)(?:\s+быть)?\s+$',prefix)
            if not denied and not prospective:return True
        return False
    # A noun or an infinitive can describe a risk, not an established status.
    returned=not uncertain_status and actual_event(r'\b(?:отчислили|отчислен[аы]?|отчислился|отчислилась)\b|\bпосле\s+отчисления\b')
    returned=returned or (actual_event(r'восстанов(?:иться|ление|ления).{0,30}(?:учеб|учиться|колледж|университет|\bвуз)')
                          and not has(r'восстановлен\w*\s+(?:документ|аттестат|паспорт|снилс)'))
    debt=has(r'(?:долг|задолж)\w*.{0,20}(?:предмет|учеб|дисциплин)|академическ\w*\s+задолж|не\s+сдал\w*\s+сесси')
    transfer=has(r'перевестись|перевод\w*.{0,25}(?:колледж|университет|\bвуз)')
    pause=has(r'перерыв|академическ\w*\s+отпуск|\bакадем\b')
    if returning_context and question_id=='study_kind' and a in {'еще учусь','учусь','в академическом отпуске'}:pause=True
    current=has(r'работаю|уже\s+работ\w*') and has(r'законч|совмещ|продолж')
    combined=has(r'работ') and has(r'продолжить\s+учиться|совмещ')
    identifier=('college_return' if returned else 'study_transfer' if transfer else 'study_debts' if debt
                else 'study_pause' if pause else 'work_study_current' if current or combined
                else 'mentor_options' if mentor else None)
    if mentor and selected_topic=='help':identifier='mentor_options'
    ambiguous=uncertain_status or (not identifier and returning_context)
    if not identifier and not ambiguous:return None
    topic='help' if identifier=='mentor_options' else 'study'
    topics=[topic]
    from .planner_retrieval import work_search_requested
    if work_search_requested(q) and 'work' not in topics:topics.append('work')
    from .access_guides import route as access_route
    from .debt_work_guides import route as debt_route
    for other in (access_route(q),debt_route(q)):
        if other:
            other_topics=(other.get('choices',[]) if other['kind']=='choice' else [other.get('topic')])
            if other['kind']=='access_clarify':
                other_topics=[{'documents_kind':'documents','health_kind':'health'}.get(other.get('question_id'))]
            topics.extend(t for t in other_topics if t and t not in topics)
    need=r'(?:нужен|нужна|нужно|нужны|хочу)\s+(?:[^,;.!?\n]{0,60}?\s+и\s+)?'
    for extra,pattern in (
        ('benefits',r'(?:получить|оформить)\s+(?:социальн\w*\s+)?(?:пособи|выплат)|'+need+r'(?:пособи|выплат)'),
        ('housing',r'(?:снять|найти|арендовать|ищу)\s+(?:жиль|квартир|комнат)|нуждаюсь\s+в\s+(?:жиль|квартир|комнат)|'+need+r'(?:жиль|квартир|комнат)'),
        ('budget',r'(?:составить|вести)\s+бюджет|'+need+r'бюджет'),
        ('documents',r'(?:восстановить|получить|оформить)\s+(?:паспорт|документ|снилс|аттестат)|'+need+r'(?:паспорт|документ|снилс|аттестат)'),
    ):
        affirmative=any(not re.search(r'\bне\s+(?:хочу\s+)?$',q[:event.start()])
                        and not re.match(r'\s+(?:(?:мне|сейчас)\s+)?не\s+(?:нуж|хоч)',q[event.end():])
                        for event in re.finditer('(?:'+pattern+r')\w*',q))
        if affirmative and extra not in topics:topics.append(extra)
    if mentor and 'help' not in topics:topics.append('help')
    if len(topics)>1 and not selected_topic:
        return dict(kind='choice',choices=topics,question='Что сейчас важнее решить? Выбери, с чего начнём.')
    if selected_topic and selected_topic!=topic:return None
    if ambiguous:
        if question_id=='study_kind' and a:return dict(kind='study_gap')
        return dict(kind='study_clarify',question='Вы ещё учитесь, в академическом отпуске, отчислены или хотите перевестись?')
    return dict(kind='material',id=identifier,topic=topic,information_only=identifier=='mentor_options')
