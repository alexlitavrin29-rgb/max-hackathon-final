"""Retrieval contracts against published scenario and independent edge fixtures."""
import json
from pathlib import Path

import pytest

from project.llm.services import planner_content


@pytest.fixture
def config():
    return json.loads((Path(__file__).resolve().parents[3] / 'submission/scenario.json').read_text())['config']


def retrieval():
    from project.llm.services import planner_retrieval
    return planner_retrieval


@pytest.mark.parametrize('question,expected', [
    ('Пахнет газом в квартире', 'hq_household_gas'),
    ('Мне пахнет газом', 'hq_household_gas'),
    ('В квартире пахнит газом, что делать?', 'hq_household_gas'),
    ('Не могу попасть в квартиру, поменяли замки', 'hq_housing_problem_locked'),
    ('Хозяин сменил замок и не пускает домой', 'hq_housing_problem_locked'),
    ('У меня долг за коммуналку', 'hq_utilities_debt'),
    ('Нечем платить за ЖКХ, как получить рассрочку?', 'hq_utilities_debt'),
    ('Положена ли мне квартира?', 'hq_state_housing_right'),
    ('Потерял паспорт', 'doc_passport_q3'),
    ('Как стирать и сушить вещи?', 'hq_household_wash'),
])
def test_real_published_targets_rank_in_first_five(config, question, expected):
    ranked = retrieval().rank_materials(config, question, planner_content.material_catalog(config))
    assert expected in [item['id'] for item in ranked[:5]]


def test_full_text_match_after_excerpt_and_no_topic_quota():
    catalog = [dict(id=f'card_{i}', title=f'Памятка {i}', excerpt='Общие сведения',
                    text=f'Общие сведения.\n\nУникальный маркер{i} о рассрочке коммунальных платежей.', topic='housing')
               for i in range(14)]
    ranked = retrieval().rank_materials({}, 'рассрочка коммунальных платежей', catalog)
    assert len(ranked) == retrieval().MAX_CANDIDATES
    assert all(item in catalog for item in ranked)


def test_bounded_stable_context_keeps_goals_without_topic_padding(config):
    catalog = planner_content.material_catalog(config)
    ranked = retrieval().rank_materials(config, 'Потерял паспорт, ищу работу и квартиру', catalog)
    assert len(ranked) <= retrieval().MAX_CANDIDATES
    assert ranked[0]['id'] == 'doc_passport_q3'
    assert retrieval().required_resources({}, 'Потерял паспорт, ищу работу и квартиру') == ['app_work', 'app_rental']
    assert ranked == retrieval().rank_materials(config, 'Потерял паспорт, ищу работу и квартиру', catalog)
    assert all(any(item is allowed for allowed in catalog) for item in ranked)
    assert retrieval().rank_materials(config, 'газ', []) == []


@pytest.mark.parametrize('question,expected', [
        ('Положена ли мне квартира?', []),
        ('Мне 19, я сирота, имею ли право на жилье от государства?', []),
    ('Ищу работу после колледжа', ['app_work']),
    ('Мне нужна работа', ['app_work']),
    ('Как устроиться на работу без опыта?', ['app_work']),
    ('Хочу работать грузчиком', ['app_work']),
    ('Нужна квартира в аренду', ['app_rental']),
    ('Мне нужна еда, денег нет', ['app_help']),
    ('Не ищу работу в Москве, ищу работу в Туле', ['app_work']),
    ('Что делать с работой после выпуска?', ['app_work']),
    ('Хочу найти вакансии и снять комнату', ['app_work', 'app_rental']),
    ('Негде ночевать и нечего есть', ['app_rental', 'app_help']),
    ('Где получить одежду и еду бесплатно?', ['app_help']),
    ('Нужна социальная помощь', ['app_help']),
    ('Пахнет газом в квартире', []),
    ('Как стирать рабочую одежду?', []),
    ('Работодатель не выплатил зарплату, как защитить права?', []),
    ('Какие права у работника?', []),
    ('Потерял паспорт на работе в квартире', []),
    ('Ищу информацию о правах на работе', []),
    ('Не ищу работу, хочу взыскать зарплату', []),
    ('Не хочу снимать квартиру, проверяю договор', []),
    ('Где посмотреть тарифы на газ?', []),
    ('Ищу информацию о работе', []),
    ('Не нужна социальная помощь, хочу узнать закон', []),
    ('Работу не ищу, меня интересует право на отпуск', []),
    ('Не ищу вакансии, работодатель не платит', []),
])
def test_resources_follow_question_intent_not_selected_topics(question, expected):
    selected = dict(topic='housing', secondary_topics=['work', 'help'])
    assert retrieval().required_resources(selected, question) == expected


@pytest.mark.parametrize('question,expected', [
    ('Пахнет газом в квартире', 'hq_household_gas'),
    ('Пахнит газом, мне страшно', 'hq_household_gas'),
    ('Запах газа сейчас, что делать?', 'hq_household_gas'),
    ('Нет запаха газа, но из трубы течёт вода', 'hq_household_water'),
    ('Сейчас вода льется с потолка', 'hq_household_water'),
    ('Сменили замки, не пускают домой', 'hq_housing_problem_locked'),
    ('Сегодня негде ночевать', 'shelter'),
    ('Газом не пахнет, как оплатить квитанцию?', None),
    ('Не чувствую запаха газа, как узнать тариф?', None),
    ('Газом не пахнет, но пахнет гарью', None),
    ('Раньше пахло газом, сейчас пахнет газом', 'hq_household_gas'),
    ('Вчера пахло газом, сейчас снова пахнет', 'hq_household_gas'),
    ('Вчера пахло газом, сейчас пахнет едой', None),
    ('Объясни правила техники безопасности при запахе газа', None),
    ('Не пахнет газом, это был запах еды', None),
    ('Вчера пахло газом, аварийная служба все проверила, сейчас безопасно', None),
    ('Для доклада: что делать при запахе газа?', None),
    ('Пишу реферат про запах газа', None),
    ('Год назад пахло газом, сейчас все в порядке', None),
    ('Запаха газа нет, как проверить счетчик?', None),
    ('Сейчас не пахнет газом, но вода течет с потолка', 'hq_household_water'),
    ('Что делать, если когда-нибудь запахнет газом?', None),
    ('Как проверить газовую плиту перед покупкой?', None),
    ('Сосед не угрожает, обсуждаем график уборки', None),
])
def test_urgent_is_current_real_situation(config, question, expected):
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) == expected


def test_urgent_never_uses_absent_or_unreviewed_material(config):
    module = retrieval()
    catalog = planner_content.material_catalog(config)
    assert module.urgent_material(config, 'Пахнет газом', []) is None
    node = next(node for node in config['nodes'] if node['id'] == 'hq_household_gas')
    node['content_status'] = 'draft'
    assert module.urgent_material(config, 'Пахнет газом', catalog) is None


@pytest.mark.parametrize('question,expected', [
    ('Мне семнадцать, как понять подходит ли вакансия несовершеннолетнему?', []),
    ('Какие возрастные условия нужно проверить в вакансии?', []),
    ('Ищу вакансии для несовершеннолетних без опыта', ['app_work']),
    ('Где посмотреть вакансии грузчика?', ['app_work']),
    ('Сегодня остался на улице и некуда идти спать', ['app_help']),
    ('Мне 16, сегодня негде переночевать, безопасного взрослого нет', ['app_rental', 'app_help']),
    ('Некуда пойти спать этой ночью', ['app_help']),
])
def test_search_intent_distinguishes_eligibility_and_overnight_need(question, expected):
    assert retrieval().required_resources({}, question) == expected


@pytest.mark.parametrize('question,expected', [
    ('Из кухни сейчас сильно несёт газом, что мне первым делом сделать?', 'hq_household_gas'),
    ('На лестнице несет газом', 'hq_household_gas'),
    ('Сегодня остался на улице и некуда идти спать', 'shelter'),
    ('Мне 16, сегодня негде переночевать, безопасного взрослого нет', 'shelter'),
    ('Некуда пойти спать этой ночью', 'shelter'),
    ('Для урока: что делать, если некуда идти спать?', None),
    ('Газом не несет, хочу узнать тариф', None),
])
def test_urgent_colloquial_hazards_keep_same_reviewed_catalog_boundary(config, question, expected):
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) == expected


@pytest.mark.parametrize('question,expected', [
    ('Вчера купил плиту, сейчас сильно пахнет газом', 'hq_household_gas'),
    ('Вчера пахло газом и до сих пор пахнет газом', 'hq_household_gas'),
    ('Вчера пахло газом и до сих пор пахнет', 'hq_household_gas'),
    ('Вчера купил плиту, сейчас газом не пахнет', None),
    ('Вчера пахло газом, сейчас нет утечки газа', None),
    ('Не могу попасть в личный кабинет, как оплатить коммуналку?', None),
    ('Не пускают в личный кабинет поставщика газа', None),
    ('Не могу попасть домой', 'hq_housing_problem_locked'),
    ('Нет утечки газа, хочу проверить счетчик', None),
    ('Сейчас нет утечки газа, как оплатить счет?', None),
    ('Нет утечки газа, но сейчас пахнет газом', 'hq_household_gas'),
    ('Нет утечки газа, но вода течет с потолка', 'hq_household_water'),
])
def test_urgent_scopes_current_events_negation_and_physical_access(config, question, expected):
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) == expected


def test_virtual_access_is_not_housing_lockout_even_with_property_context(config):
    question = 'Не могу попасть в личный кабинет по оплате квартиры'
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) is None


@pytest.mark.parametrize('question,expected', [
    ('Я уже снимаю квартиру, хочу проверить договор', []),
    ('Снимаем комнату, арендодатель поднял цену', []),
    ('Хочу снимать квартиру', ['app_rental']),
    ('Уже снимаю квартиру, но ищу другое жилье', ['app_rental']),
    ('Как отказаться от вакансии?', []),
    ('Что написать при отказе от вакансии?', []),
    ('Ищу другую работу, хочу отказаться от старой вакансии', ['app_work']),
        ('Положена ли мне квартира?', []),
])
def test_resources_distinguish_existing_arrangements_from_search(question, expected):
    assert retrieval().required_resources({}, question) == expected


@pytest.mark.parametrize('question,expected', [
    ('Пишу реферат, но сейчас в квартире пахнет газом', 'hq_household_gas'),
    ('Пишу реферат, но сейчас в квартире газом не пахнет', None),
    ('Для доклада, что делать при запахе газа?', None),
])
def test_real_current_hazard_can_follow_educational_context(config, question, expected):
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) == expected


@pytest.mark.parametrize('question', [
    'Для доклада сейчас опиши что делать при запахе газа',
    'Пишу реферат сейчас про запах газа',
])
def test_current_word_alone_does_not_turn_educational_request_into_emergency(config, question):
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) is None


@pytest.mark.parametrize('question,expected', [
    ('Газом сейчас пахнет', 'hq_household_gas'),
    ('В квартире газом сейчас сильно пахнет', 'hq_household_gas'),
    ('Газом сейчас не пахнет', None),
    ('Не было запаха газа, как платить?', None),
    ('Не было запаха газа, но сейчас пахнет газом', 'hq_household_gas'),
    ('Вчера пахло газом и сейчас нет утечки газа', None),
    ('Вчера купил плиту и сейчас сильно пахнет газом', 'hq_household_gas'),
    ('Запаха газа сейчас нет, как платить?', None),
])
def test_hazard_predicates_keep_subject_and_negation_in_the_same_event(config, question, expected):
    assert retrieval().urgent_material(config, question, planner_content.material_catalog(config)) == expected
