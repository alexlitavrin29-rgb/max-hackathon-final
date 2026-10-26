"""Reviewed federal payments: discovery, distinct goals, plans and MAX navigation."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from project.llm.services import planner_ai
from project.llm.services.handbook import handbook_material
from project.llm.services.planner_content import candidate_catalog, refine_plan_materials, selected_materials

CONFIG = json.loads(Path('submission/scenario.json').read_text(encoding='utf8'))['config']
CASES = [
    ('Какие выплаты положены выпускнику детдома?', 'orphan_payments'),
    ('Я сирота, какие пособия могу оформить?', 'orphan_payments'),
    ('Какие пособия положены выпускнику детского дома?', 'orphan_payments'),
    ('Какие выплаты мне положены?', 'orphan_payments'),
    ('Хочу узнать все основные выплаты детям-сиротам', 'orphan_payments'),
    ('Подъёмные после выпуска из детдома', 'graduation_support'),
    ('Сколько денег должны выдать при выпуске из колледжа?', 'graduation_support'),
    ('Пособие по безработице сироте, раньше не работал', 'orphan_unemployment'),
    ('Пасобие по безработице', 'orphan_unemployment'),
    ('Мне 24, я сирота, сколько платят на бирже труда?', 'orphan_unemployment'),
    ('Доплата к зарплате сироте после трудоустройства через ЦЗН', 'orphan_wage_topup'),
    ('Ежегодное пособие на учебники и канцелярию', 'study_books'),
    ('Я сирота, как оформить социальную стипендию?', 'social_scholarship'),
    ('Повышенная социальная стипендия на втором курсе вуза', 'scholarship_increased'),
    ('Выплаты на питание и одежду во время учёбы', 'study_provision'),
    ('Мне 18, как оформить пенсию по потере кормильца?', 'bq_pension_who'),
    ('Оба родителя неизвестны, положена ли пенсия?', 'bq_pension_who'),
    ('Социальная доплата к пенсии до прожиточного минимума', 'pension_topup'),
    ('Сирота с ОВЗ на переподготовке, ежемесячное пособие', 'study_training_allowance'),
]


def ask(monkeypatch, text, **kwargs):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    async def forbidden(*args, **options):
        raise AssertionError('A reviewed payments route must not call a provider')
    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    return asyncio.run(planner_ai.PlannerAI().propose('payments-test',
        kwargs.pop('operation', 'find_help_route'), text, config=kwargs.pop('config', CONFIG), **kwargs))


@pytest.mark.parametrize('text,identifier', CASES)
def test_specific_payment_is_found_without_provider(monkeypatch, text, identifier):
    result = ask(monkeypatch, text)
    assert result['status'] == 'information', result
    assert result['context']['material_ids'] == [identifier]
    assert not result['context']['required_resources']
    assert '/?handbook=' + identifier in {s['url'] for s in result['information']['sources']}
    assert len(result['information']['text']) > 250


def test_cards_are_canonical_openable_and_conditioned():
    catalog = {x['id']: x for x in candidate_catalog(CONFIG)}
    for identifier in {i for _, i in CASES}:
        assert identifier in catalog
        card = handbook_material(CONFIG, identifier)
        assert card['id'] == identifier and card['actions']
        assert catalog[identifier].get('intent') and catalog[identifier].get('object')
    overview = handbook_material(CONFIG, 'orphan_payments')
    for identifier in ['graduation_support', 'study_books', 'social_scholarship',
                       'orphan_unemployment', 'orphan_wage_topup', 'bq_pension_who',
                       'pension_topup', 'hq_state_housing_certificate', 'social_contract']:
        assert '/?handbook=' + identifier in overview['text']


def test_concrete_followup_replaces_overview_and_keeps_other_goal(monkeypatch):
    materials = selected_materials(CONFIG, ['orphan_payments', 'study_books', 'orphan_unemployment'])
    refined = refine_plan_materials(materials, 'Какие выплаты положены? И пособие на учебники',
                                   'Хочу пособие по безработице')
    assert {m['id'] for m in refined} == {'study_books', 'orphan_unemployment'}
    plan = ask(monkeypatch, 'Пособие на учебники и пособие по безработице', operation='draft_plan',
               selected_material_ids=['study_books', 'orphan_unemployment'])
    assert set(plan['context']['material_ids']) == {'study_books', 'orphan_unemployment'}
    assert {i for t in plan['tasks'] for i in t.get('material_ids', [])} == {'study_books', 'orphan_unemployment'}
    assert not plan['context']['required_resources']


def test_unknown_payment_problem_stays_a_problem(monkeypatch):
    result = ask(monkeypatch, 'Выплату приостановили, требуют справку', question_id='benefits_kind', answer='Не знаю')
    assert result['context']['material_ids'] == ['benefit_unknown']


def test_general_unknown_answer_has_useful_overview(monkeypatch):
    result = ask(monkeypatch, 'Какие выплаты мне положены?', question_id='benefits_kind', answer='Не знаю')
    assert result['context']['material_ids'] == ['orphan_payments']


def test_payment_routes_obey_child_visibility(monkeypatch):
    config = deepcopy(CONFIG)
    next(b for b in config['branches'] if b['id'] == 'benefits')['roles'] = ['parent']
    assert 'orphan_payments' not in {m['id'] for m in candidate_catalog(config)}


def test_max_menu_uses_same_cards_and_preserves_configuration(monkeypatch):
    from project.llm.services.flow import FlowDialogue, PreviewReminders
    before = deepcopy(CONFIG)
    engine = FlowDialogue(PreviewReminders(), CONFIG)
    menu = engine.nodes['bq_group_payments']
    targets = {b['target'] for b in menu['buttons']}
    assert {'orphan_payments', 'graduation_support', 'orphan_unemployment', 'bq_pension'} <= targets
    for identifier in {i for _, i in CASES}:
        node = engine.nodes[identifier]
        assert node['text'] in handbook_material(CONFIG, identifier)['text']
        assert any(b['target'] == CONFIG['menu'] for b in node['buttons'])
    assert CONFIG == before


def test_work_and_pension_do_not_compete(monkeypatch):
    result = ask(monkeypatch, 'Если начну работать, пенсия по потере кормильца останется?', prepared_only=True)
    assert result['context']['material_ids'] == ['survivor_pension_work']


def test_independent_two_payments_offer_priority(monkeypatch):
    result = ask(monkeypatch, 'Хочу пособие на учебники и пенсию по потере кормильца')
    assert result['status'] == 'clarify'
    # Both payments are one sphere; the choice names the actual measures.
    assert {x['id'] for x in result['choices']} == {'study_books', 'bq_pension_who'}
    chosen = ask(monkeypatch, 'Хочу пособие на учебники и пенсию по потере кормильца',
                 question_id=result['question_id'], answer='study_books')
    assert chosen['context']['material_ids'] == ['study_books']


def test_explicit_work_search_is_retained_with_payment(monkeypatch):
    result = ask(monkeypatch, 'Доплата к зарплате после трудоустройства и хочу найти работу')
    assert result['status'] == 'clarify'
    assert {c['id'] for c in result['choices']} == {'benefits', 'work'}


def test_overview_links_are_available():
    import re
    card = handbook_material(CONFIG, 'orphan_payments')
    assert all(handbook_material(CONFIG, i) for i in re.findall(r'/\?handbook=([a-z0-9_]+)', card['text']))


def test_negated_pension_does_not_compete(monkeypatch):
    result = ask(monkeypatch, 'Мне не нужна пенсия по потере кормильца, хочу пособие по безработице')
    assert result['context']['material_ids'] == ['orphan_unemployment']


def test_max_callbacks_open_new_card_and_return(monkeypatch):
    from project.llm.services.flow import FlowDialogue, PreviewReminders
    async def run():
        engine = FlowDialogue(PreviewReminders(), CONFIG, 11)
        session = engine.session(901)
        session.values = {'role':'child'}
        replies = await engine.enter(901, session, 'bq_group_payments')
        buttons = [b for r in replies for a in r.get('attachments', []) for row in a['payload']['buttons'] for b in row]
        payload = next(b['payload'] for b in buttons if b['text'] == 'Основные выплаты выпускникам: что проверить')
        replies = await engine.handle(901, payload=payload)
        assert session.node == 'orphan_payments' and 'социальная стипендия' in replies[0]['text']
        replies = await engine.handle(902, payload='route_start:p2_orphan_unemployment')
        assert '15 886' in replies[0]['text']
    asyncio.run(run())


def test_model_normalization_preserves_two_payments_and_removes_overview():
    raw = json.dumps(dict(status='draft',topic='benefits',route_alias='benefits',
        material_ids=['orphan_payments','study_books','pension_topup']))
    query = 'Хочу пособие на учебники и доплату к пенсии'
    result = json.loads(planner_ai.normalize_material_selection(raw,candidate_catalog(CONFIG),query,None))
    assert set(result['material_ids']) == {'study_books','pension_topup'}


def test_income_options_link_to_the_payment_overview(monkeypatch):
    result = ask(monkeypatch, 'Мне нужны деньги')
    assert result['context']['material_ids'] == ['income_start']
    assert '/?handbook=orphan_payments' in {s['url'] for s in result['information']['sources']}


@pytest.mark.parametrize('question', ['Нужны выплаты', 'Нужна поддержка', 'Хочу оформить пособия'])
def test_unnamed_general_payment_is_discovery_not_an_existing_payment_problem(monkeypatch, question):
    result = ask(monkeypatch, question, question_id='benefits_kind', answer='Не знаю')
    assert result['context']['material_ids'] == ['orphan_payments']


@pytest.mark.parametrize('question', ['Выплату назначили, но не помню её название',
                                     'Выплата назначена, название не помню'])
def test_already_assigned_unknown_payment_stays_identification(monkeypatch, question):
    result = ask(monkeypatch, question, question_id='benefits_kind', answer='Не знаю')
    assert result['context']['material_ids'] == ['benefit_unknown']
