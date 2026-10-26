"""Product regressions from owner feedback; no real model calls."""
import json
from pathlib import Path
import pytest
from project.llm.services.planner_content import enrich,required_searches

CONFIG=json.loads(Path('submission/scenario.json').read_text())['config']

@pytest.mark.parametrize('question',[
 'Как мне заработать денег?',
 'я скоро выпускаюсь из детдома и не знаю, что делать с работой',
 'Хочу подработку после колледжа',
 'Мне нужна работа, но я без опыта',
])
def test_work_goal_has_search_even_without_literal_find_work(question):
 assert 'app_work' in required_searches({},question)

@pytest.mark.parametrize('question',[
 'Как не потерять заработанные деньги?',
 'Не хочу искать работу, хочу вернуть невыплаченную зарплату',
 'Как стирать рабочую одежду?',
 'Как мне заработать на курсе биткоина?',
 'Ищу информацию о трудовом договоре',
])
def test_incidental_work_or_financial_question_is_not_vacancy_search(question):
 assert 'app_work' not in required_searches({},question)

@pytest.mark.parametrize('ids,question',[
 (['employment_docs'],'Помоги мне подать документы для работы'),
 (['employment_docs','resume_first','job_contract'],'я скоро выпускаюсь из детдома и не знаю, что делать с работой'),
 (['university'],'Разберись, пожалуйста, с поступлением в университет'),
])
def test_initial_answer_is_concrete_and_short_with_full_sources(ids,question):
 p=enrich(CONFIG,{'material_ids':ids},question,suggest_actions=False);i=p['information']
 visible='\n'.join([i['intro'],*[p['text'] for p in i['points']],i.get('note','')])
 assert len(visible)<=900
 assert 'полезно заранее собрать вопросы' not in visible
 assert 'Можно сначала изучить общий порядок' not in visible
 if 'employment_docs' in ids:
  assert 'паспорт' in visible and 'индивидуального учёта, если оно уже есть' in visible
  assert 'Если работа не первая' in visible and 'Это не полный список' in visible
  assert 'соответствующим категориям' in visible and 'подаёт работодатель' in visible
  assert 'ИНН и банковская карта не включены' in visible
 nodes={n['id']:n for n in CONFIG['nodes']}
 for mid,detail in zip(ids,i['details']):assert nodes[mid]['text'] in detail['text']


def test_wage_dispute_has_source_bound_help_instead_of_generic_contract():
 p=enrich(CONFIG,{'material_ids':['work_wages']},'Мне не выплатили зарплату. Как защитить трудовые права?',suggest_actions=False)
 i=p['information'];visible=i.get('intro','')+' '+ ' '.join(x['text'] for x in i.get('points',[]))
 assert 'инспекци' in visible.lower()
 assert 'дат' in visible.lower() and 'выплат' in visible.lower()
 assert any('instruction/7' in s['url'] for s in i['sources'])
 assert any(s['url'].endswith('/problems') for s in i['sources'])
 assert not any(s['id']=='app_work' for s in i['sources'])
 assert 'приостанов' not in visible.lower()


def test_reference_guide_is_explicit_child_only_and_does_not_override_publication():
 import copy
 from project.llm.services.handbook import handbook_catalog,handbook_material
 c=copy.deepcopy(CONFIG)
 assert handbook_material(c,'work_wages')['actions']
 n=next(n for n in c['nodes'] if n['id']=='job_contract')
 n['content_status']='unverified_reference'
 assert handbook_material(c,'work_wages') is None
 c=copy.deepcopy(CONFIG);next(n for n in c['nodes'] if n['id']=='job_contract')['text']+=' Changed.'
 assert handbook_material(c,'work_wages') is None
 c=copy.deepcopy(CONFIG);c['nodes'].append(dict(id='work_wages',kind='message',branch='parent',text='Private.'))
 assert 'work_wages' not in {x['id'] for x in handbook_catalog(c)}


def test_unreachable_published_guide_id_is_not_a_virtual_guide():
 import copy
 from project.llm.services.planner_content import selected_materials
 c=copy.deepcopy(CONFIG);branch=next(n['branch'] for n in c['nodes'] if n['id']=='job_contract')
 c['nodes'].append(dict(id='work_wages',kind='message',branch=branch,title='Private',text='This node is deliberately unreachable.'))
 with pytest.raises(ValueError):selected_materials(c,['work_wages'])


@pytest.mark.parametrize('question',[
 'Зарплату задержали, что делать с работой?',
 'Меня заставляют работать без договора. Что делать с работой?',
 'Я выпускник детдома, зарплату задержали, что делать с работой?',
 'Что делать с работой?',
])
def test_vague_work_problem_is_not_automatically_job_hunting(question):
 assert 'app_work' not in required_searches({},question)


def test_real_new_job_goal_is_not_hidden_by_existing_wage_dispute():
 assert 'app_work' in required_searches({},'Мне не выплатили зарплату. Ищу другую работу.')


@pytest.mark.parametrize('question',[
 'Мне не выплатили зарплату. Как защитить трудовые права?',
 'Работодатель задержал заработную плату, куда обратиться?',
 'Зарплату не платят второй месяц',
 'Мне не заплатили зарплату',
])
def test_wage_complaint_ranks_specific_reference_before_contract(question):
 from project.llm.services.planner_content import candidates
 assert candidates(CONFIG,question)[0]['id']=='work_wages'
