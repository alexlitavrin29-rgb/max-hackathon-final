"""Free controlled checks of the benefits chooser and canonical answers."""
from copy import deepcopy
import pytest
from project.admin.tests.test_orphan_payments import CONFIG, ask
from project.llm.services.handbook import handbook_material
from project.llm.services.planner_content import candidate_catalog


@pytest.mark.parametrize('question', ['Какие льготы положены выпускнику детдома?',
    'Хочу узнать все меры поддержки', 'Льготы', 'Льготы детям-сиротам'])
def test_general_question_offers_categories(monkeypatch, question):
    result = ask(monkeypatch, question)
    assert result['status'] == 'clarify', result
    assert result['question_id'] == 'benefits_category'
    assert {c['id'] for c in result['choices']} == {
        'housing', 'graduation', 'education', 'students', 'employment',
        'pensions', 'travel', 'health', 'legal', 'utilities', 'services', 'documents', 'payments_help'}


CASES = [
    ('housing', 'hq_state_housing_right'), ('housing', 'hq_state_housing_when'), ('housing', 'hq_state_housing_certificate'),
    ('education', 'university_quota'), ('education', 'vocational_training'),
    ('education', 'second_vocational'), ('education', 'university_preparation'),
    ('students', 'social_scholarship'), ('students', 'study_books'),
    ('students', 'study_provision'), ('students', 'scholarship_increased'),
    ('students', 'study_training_allowance'),
    ('employment', 'orphan_unemployment'), ('employment', 'orphan_wage_topup'),
    ('pensions', 'bq_pension_who'), ('pensions', 'pension_topup'),
    ('travel', 'study_travel'), ('travel', 'treatment_travel'),
    ('health', 'federal_medical_care'), ('health', 'sanatorium_right'),
    ('health', 'child_recreation'), ('legal', 'bq_legal_who'), ('legal', 'bq_legal_steps'),
    ('utilities', 'utility_subsidy'), ('utilities', 'utility_compensation'),
]


@pytest.mark.parametrize('category,identifier', CASES)
def test_category_then_specific_measure_answers_and_builds_plan(monkeypatch, category, identifier):
    question = 'Какие льготы положены?'
    choose = ask(monkeypatch, question, question_id='benefits_category', answer=category)
    assert choose['status'] == 'clarify' and choose['question_id'] == 'benefits_' + category
    assert identifier in {c['id'] for c in choose['choices']}
    history = [{'question_id': 'benefits_category', 'answer': category}]
    response = ask(monkeypatch, question, question_id=choose['question_id'], answer=identifier, history=history)
    assert response['status'] == 'information', response
    assert response['context']['material_ids'] == [identifier]
    assert not response['context']['required_resources']
    card = handbook_material(CONFIG, identifier)
    assert card['text'] == response['information']['text']
    assert 'https://' in card['text'] and len(card['text']) > 400
    plan = ask(monkeypatch, question, operation='draft_plan', selected_material_ids=[identifier])
    assert plan['context']['material_ids'] == [identifier] and plan['tasks']
    assert all(identifier in t['material_ids'] for t in plan['tasks'])


def test_graduation_is_already_one_specific_goal(monkeypatch):
    result = ask(monkeypatch, 'Какие льготы?', question_id='benefits_category', answer='graduation')
    assert result['context']['material_ids'] == ['graduation_support']


@pytest.mark.parametrize('question,category', [('Какие льготы на проезд?', 'travel'),
    ('Бесплатный проезд', 'travel'), ('Бесплатное образование сироте', 'education'),
    ('Какие льготы по ЖКУ?', 'utilities'), ('Льготы при поступлении', 'education'),
    ('Поддержка студентов', 'students'), ('Льготы на лечение и отдых', 'health')])
def test_named_category_skips_root(monkeypatch, question, category):
    result = ask(monkeypatch, question)
    assert result['question_id'] == 'benefits_' + category, result


def test_unknown_choice_returns_overview_and_can_open_each_card(monkeypatch):
    result = ask(monkeypatch, 'Какие льготы?', question_id='benefits_category', answer='Не знаю')
    assert result['context']['material_ids'] == ['orphan_benefits']
    allowed = {c['id'] for c in candidate_catalog(CONFIG)}
    for _, identifier in CASES:
        assert identifier in allowed
        assert '/?handbook=' + identifier in result['information']['text']


def test_hidden_category_not_exposed_and_invalid_choice_does_not_call_provider(monkeypatch):
    config = deepcopy(CONFIG)
    next(b for b in config['branches'] if b['id'] == 'housing')['roles'] = ['parent']
    result = ask(monkeypatch, 'Какие льготы?', config=config)
    assert 'housing' not in {c['id'] for c in result['choices']}
    invalid = ask(monkeypatch, 'Какие льготы?', question_id='benefits_housing', answer='study_books')
    assert invalid['status'] == 'clarify' and invalid['question_id'] == 'benefits_housing'


def test_specific_free_text_instead_of_button_and_two_same_topic_goals(monkeypatch):
    result = ask(monkeypatch, 'Какие льготы?', question_id='benefits_category', answer='Ежегодное пособие на учебники')
    assert result['context']['material_ids'] == ['study_books']
    plan = ask(monkeypatch, 'Хочу квоту и бесплатную профподготовку', operation='draft_plan',
               selected_material_ids=['university_quota', 'vocational_training'])
    assert set(plan['context']['material_ids']) == {'university_quota', 'vocational_training'}


def test_max_categories_share_answers_and_keep_back_navigation():
    from project.llm.services.flow import FlowDialogue, PreviewReminders
    flow = FlowDialogue(PreviewReminders(), CONFIG)
    root = flow.nodes['benefits']
    assert any(b.get('target') == 'benefits_utilities' for b in root['buttons'])
    for category, identifier in CASES:
        node = flow.nodes['benefits_' + category]
        assert identifier in {b.get('target') for b in node['buttons']}
        assert any(b.get('target') == 'benefits' for b in node['buttons'])
        assert 'https://' in flow.nodes[identifier]['text']
        assert {b.get('values', {}).get('navigation') for b in flow.nodes[identifier]['buttons']} >= {'back', 'contents'}


@pytest.mark.parametrize('question,identifier', [
    ('Особая квота сироте для поступления в вуз', 'university_quota'),
    ('Бесплатная профессиональная подготовка сироте', 'vocational_training'),
    ('Компенсация ЖКУ выпускнику', 'utility_compensation')])
def test_specific_new_material_skips_category(monkeypatch, question, identifier):
    result = ask(monkeypatch, question)
    assert result['context']['material_ids'] == [identifier]


def test_new_same_topic_goals_survive_model_normalization():
    import json
    from project.llm.services.planner_ai import normalize_material_selection
    raw = json.dumps(dict(status='draft', material_ids=['orphan_benefits', 'university_quota', 'vocational_training']))
    result = json.loads(normalize_material_selection(raw, candidate_catalog(CONFIG),
        'Хочу особую квоту в вуз и бесплатную профессиональную подготовку', None))
    assert set(result['material_ids']) == {'university_quota', 'vocational_training'}


def test_benefits_and_work_search_keep_second_goal(monkeypatch):
    question = 'Какие льготы мне положены и хочу найти работу'
    result = ask(monkeypatch, question)
    assert {c['id'] for c in result['choices']} == {'benefits', 'work'}
    selected = ask(monkeypatch, question, question_id='topic_choice', answer='benefits')
    assert selected['question_id'] == 'benefits_category'


def test_verified_conditions_and_internal_links_remain_available():
    import re
    quota = handbook_material(CONFIG, 'university_quota')['text']
    assert '10%' in quota and 'бакалавриата' in quota and 'не автоматическое' in quota
    certificate = handbook_material(CONFIG, 'hq_state_housing_certificate')['text']
    assert '23 лет' in certificate and '12 календарных месяцев' in certificate
    assert 'МРОТ' in certificate and 'полного погашения' in certificate
    utilities = handbook_material(CONFIG, 'utility_compensation')['text']
    assert 'Единой федеральной компенсации' in utilities and '50%' in utilities and '100%' in utilities
    travel = handbook_material(CONFIG, 'study_travel')['text']
    assert 'кроме такси' in travel and 'один проезд в год' in travel and 'финансирования' in travel
    overview = handbook_material(CONFIG, 'orphan_benefits')['text']
    assert all(handbook_material(CONFIG, identifier) for identifier in re.findall(r'/\?handbook=([a-z0-9_]+)', overview))


def test_category_can_be_typed_instead_of_clicked(monkeypatch):
    result = ask(monkeypatch, 'Какие льготы?', question_id='benefits_category', answer='Проезд')
    assert result['question_id'] == 'benefits_travel'


@pytest.mark.parametrize('question,other', [
    ('Какие льготы и нужно восстановить паспорт', 'documents'),
    ('Какие льготы и хочу снять квартиру', 'housing')])
def test_other_explicit_goal_remains_available(monkeypatch, question, other):
    result = ask(monkeypatch, question)
    assert {c['id'] for c in result['choices']} == {'benefits', other}
