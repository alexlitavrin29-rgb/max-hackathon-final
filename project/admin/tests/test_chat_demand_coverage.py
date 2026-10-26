"""Topic-derived examples; free checks, no replay or claim about chat percentages."""
import json
from copy import deepcopy
import pytest
from project.admin.tests.test_orphan_payments import CONFIG,ask
from project.llm.services.handbook import handbook_material
from project.llm.services.planner_content import candidate_catalog

NEW = ['social_card','social_transport','gas_connection','gas_subsidy','egrn_extract',
       'residence_extract','deregistration','social_rehabilitation','orphan_status_proof']
CASES = [
 ('Как получить социальную карту?', 'social_card'),
 ('Как получить социальное такси?', 'social_transport'),
 ('Как подключить газ к дому?', 'gas_connection'),
 ('Как оформить субсидию на газификацию?', 'gas_subsidy'),
 ('Как получить выписку ЕГРН?', 'egrn_extract'),
 ('Нужна справка о зарегистрированных в квартире', 'residence_extract'),
 ('Как выписаться из квартиры?', 'deregistration'),
 ('Нужна социальная реабилитация', 'social_rehabilitation'),
 ('Нужна справка о статусе сироты', 'orphan_status_proof'),
 ('Как пройти медицинскую реабилитацию?', 'medical_rehabilitation'),
 ('Как получить ТСР по ИПРА?', 'rehabilitation_equipment'),
 ('Как оформить материальную помощь?', 'social_assistance'),
 ('Пособие не пришло в срок', 'payment_delay'),
 ('Путёвка в санаторий по инвалидности', 'disability_nsu'),
]


@pytest.mark.parametrize('question,identifier', CASES)
def test_specific_question_reaches_canonical_card_without_provider(monkeypatch,question,identifier):
    result=ask(monkeypatch,question)
    assert result['status']=='information',result
    assert result['context']['material_ids']==[identifier]
    assert not result['context']['required_resources']
    assert 'https://' in result['information']['text']
    assert handbook_material(CONFIG,identifier)


@pytest.mark.parametrize('question,question_id,ids', [
 ('Нужна реабилитация','benefits_rehabilitation',{'medical_rehabilitation','social_rehabilitation','rehabilitation_equipment'}),
 ('Как получить путёвку?','benefits_voucher',{'sanatorium_right','child_recreation','disability_nsu'}),
 ('Нужна выписка','benefits_extract',{'egrn_extract','residence_extract','deregistration'}),
 ('Газификация дома','benefits_gas_options',{'gas_connection','gas_subsidy'}),
 ('Социальные услуги: как получить помощь?','benefits_services',{'social_services','social_rehabilitation','social_service_cost','social_accompaniment'}),
])
def test_ambiguous_request_asks_relevant_question(monkeypatch,question,question_id,ids):
    result=ask(monkeypatch,question)
    assert result['question_id']==question_id,result
    assert {c['id'] for c in result['choices']}==ids
    identifier=sorted(ids)[0]
    response=ask(monkeypatch,question,question_id=question_id,answer=identifier)
    assert response['context']['material_ids']==[identifier]


def test_new_cards_admitted_openable_and_plannable(monkeypatch):
    allowed={m['id']:m for m in candidate_catalog(CONFIG)}
    for identifier in NEW:
        assert allowed[identifier]['intent'] and allowed[identifier]['object']
        card=handbook_material(CONFIG,identifier)
        assert card['actions'] and 'Условия и ограничения' in card['text']
        plan=ask(monkeypatch,'Разобраться с выбранным вопросом',operation='draft_plan',selected_material_ids=[identifier])
        assert plan['context']['material_ids']==[identifier] and len(plan['tasks'])>1


def test_two_gas_goals_survive_priority_and_plan(monkeypatch):
    question='Хочу подключить газ к дому и оформить субсидию на газификацию'
    first=ask(monkeypatch,question)
    assert first['question_id']=='federal_priority'
    response=ask(monkeypatch,question,question_id='federal_priority',answer='gas_subsidy')
    assert response['context']['material_ids']==['gas_subsidy','gas_connection']
    plan=ask(monkeypatch,question,operation='draft_plan',selected_material_ids=response['context']['material_ids'])
    assert set(plan['context']['material_ids'])=={'gas_subsidy','gas_connection'}
    assert {i for t in plan['tasks'] for i in t['material_ids']}=={'gas_subsidy','gas_connection'}


def test_explicit_negation_and_neighbour_goal(monkeypatch):
    result=ask(monkeypatch,'Не нужна субсидия на газификацию, хочу подключить газ к дому')
    assert result['context']['material_ids']==['gas_connection']
    result=ask(monkeypatch,'Не знаю свой статус')
    assert result['context']['material_ids']==['status_orientation']


def test_regions_and_safety_are_not_promised():
    card=lambda i:handbook_material(CONFIG,i)['text']
    assert 'регион' in card('social_card') and 'не назначает' in card('social_card')
    assert 'регион' in card('social_transport') and 'экстренн' in card('social_transport')
    assert 'границ' in card('gas_connection') and 'внутри' in card('gas_connection')
    assert 'сиротский статус' in card('gas_subsidy').lower()
    assert 'не обещает' in card('gas_subsidy')
    assert 'собственник' in card('egrn_extract') and 'сво' in card('egrn_extract')
    assert 'несовершеннолетн' in card('deregistration')


def test_hidden_material_cannot_be_selected(monkeypatch):
    config=deepcopy(CONFIG)
    next(b for b in config['branches'] if b['id']=='documents')['roles']=['parent']
    assert 'social_card' not in {m['id'] for m in candidate_catalog(config)}
    result=ask(monkeypatch,'Льготы',question_id='benefits_documents',answer='social_card',config=config)
    assert 'social_card' not in result.get('context',{}).get('material_ids',[])


@pytest.mark.parametrize('question,identifier',[
 ('Субсидия на догазификацию','gas_subsidy'),
 ('Что подтверждает статус ребёнка-сироты?','orphan_status_proof'),
 ('Нужно получить выписку о регистрации жильцов в квартире','residence_extract'),
])
def test_related_formulations_keep_one_concrete_goal(monkeypatch,question,identifier):
    result=ask(monkeypatch,question)
    assert result['context']['material_ids']==[identifier]
    assert not result['context']['required_resources']


@pytest.mark.parametrize('question',['Нужна медицинская выписка из больницы','Нужна банковская выписка по счёту'])
def test_other_extract_objects_do_not_become_housing(question):
    from project.llm.services.benefits_guides import prepared_response
    assert prepared_response(CONFIG,question,None,None,'find_help_route') is None


def test_gas_emergency_precedes_connection(monkeypatch):
    result=ask(monkeypatch,'Хочу подключить газ, но сейчас в квартире пахнет газом')
    assert result['information']['priority']=='urgent'
    assert result['context']['material_ids']==['hq_household_gas']


def test_refinement_preserves_named_independent_goal():
    from project.llm.services.planner_content import selected_materials,refine_plan_materials
    selected=selected_materials(CONFIG,['orphan_benefits','egrn_extract','residence_extract'])
    result=refine_plan_materials(selected,'Нужна выписка ЕГРН и справка о зарегистрированных','Сначала выписка ЕГРН')
    assert [m['id'] for m in result]==['egrn_extract','residence_extract']


def test_document_request_does_not_hide_explicit_rental_search():
    from project.llm.services.planner_retrieval import required_resources
    assert required_resources({},'Нужна справка о зарегистрированных и хочу найти квартиру')==['app_rental']


@pytest.mark.parametrize('question,identifiers',[
 ('Субсидия на подключение газа',['gas_subsidy']),
 ('Субсидия на газовое оборудование в квартире',['gas_subsidy']),
 ('Нужна субсидия ЖКУ и субсидия на газовое оборудование',['utility_subsidy','gas_subsidy']),
 ('Нужна субсидия на газовое оборудование и субсидия на ЖКУ',['utility_subsidy','gas_subsidy']),
])
def test_named_gas_expense_is_not_a_second_subsidy_goal(question,identifiers):
    from project.llm.services.federal_guides import match_ids
    assert match_ids(question)==identifiers


def test_separate_subsidies_keep_both_goals(monkeypatch):
    question='Нужна субсидия на ЖКУ и субсидия на газовое оборудование'
    first=ask(monkeypatch,question)
    assert first['question_id']=='federal_priority'
    response=ask(monkeypatch,question,question_id='federal_priority',answer='gas_subsidy')
    assert response['context']['material_ids']==['gas_subsidy','utility_subsidy']
    plan=ask(monkeypatch,question,operation='draft_plan',selected_material_ids=response['context']['material_ids'])
    assert {i for t in plan['tasks'] for i in t['material_ids']}=={'gas_subsidy','utility_subsidy'}


def test_separate_subsidies_survive_model_normalization():
    from project.llm.services.planner_ai import normalize_material_selection
    raw=json.dumps(dict(status='draft',material_ids=['orphan_benefits','utility_subsidy','gas_subsidy']))
    result=json.loads(normalize_material_selection(raw,candidate_catalog(CONFIG),
        'Нужна субсидия на ЖКУ и субсидия на газовое оборудование',None))
    assert set(result['material_ids'])=={'utility_subsidy','gas_subsidy'}
