"""Stand mechanics, not evidence of real model quality."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load(name):
    path = ROOT / 'tools/submission' / (name + '.py')
    assert path.is_file(), f'Missing evaluation component: {name}'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_screenshot_water_fails_even_with_right_links():
    m = load('answer_eval_stand')
    case = {'id': 'work', 'fact_groups': [['паспорт'], ['снилс', 'индивидуального уч']],
            'required_links': ['employment_docs', 'app_work'], 'forbidden_resources': [],
            'max_chars': 900, 'forbidden_claims': []}
    reply = {'status': 'information', 'information': {'text': 'Перед оформлением на работу полезно заранее собрать вопросы о документах. Эта памятка поможет подготовиться к разговору с работодателем.', 'sources': [
        {'id': 'employment_docs', 'url': '/?handbook=employment_docs'}, {'id': 'app_work', 'url': '/work'}]}}
    checks = m.check_answer(case, reply)
    assert not checks['facts_immediate']['pass']
    assert checks['required_links']['pass']
    assert not m.overall(checks)


def test_details_cannot_rescue_missing_visible_facts_and_errors_are_not_success():
    m = load('answer_eval_stand')
    case = {'fact_groups': [['паспорт']], 'required_links': [], 'forbidden_resources': [], 'max_chars': 900, 'forbidden_claims': []}
    reply = {'status': 'information', 'information': {'text': 'Прочитай памятку.', 'details': [{'text': 'Подготовь паспорт.'}]}}
    assert not m.check_answer(case, reply)['facts_immediate']['pass']
    assert not m.overall(m.check_answer(case, {'status': 'unavailable'}))
    assert m.check_answer(case, {'status': 'clarify'})['facts_immediate']['status'] == 'NOT_APPLICABLE'


def test_safe_html_and_links():
    m = load('answer_eval_stand')
    row = {'id': 'x', 'question': '<script>alert(1)</script>', 'origin': 'oracle_assembly_NOT_model',
           'proposal': {'status': 'information', 'information': {'text': '<img src=x onerror=alert(1)>', 'sources': [
               {'title': 'click', 'url': 'javascript:alert(1)'}, {'title': 'Doc', 'url': '/?handbook=a'}]}}, 'checks': {}}
    page = m.render_report({'label': 'test', 'rows': [row], 'live': 'NOT_RUN'})
    assert '<img src=x' not in page and '<script>alert' not in page
    assert 'href="javascript:' not in page
    assert 'не проверено живой моделью' in page
    assert 'Вода' in page and 'exportReview' in page


def test_budget_persists_and_reserves_both_transport_attempts(tmp_path):
    m = load('eval_budget')
    path = tmp_path / 'budget.json'
    a = m.Budget(path, limit_rub=0.5)
    assert a.reserve([{'role':'user','content':'x'}], max_tokens=600, purpose='answer')['rub'] > 0.048
    b = m.Budget(path, limit_rub=0.5)
    assert b.spent == a.spent
    try:
        b.reserve([{'role':'user','content':'x'}], max_tokens=600, purpose='judge')
        b.reserve([{'role':'user','content':'x'}], max_tokens=600, purpose='judge')
    except m.BudgetExceeded:
        pass
    else:
        raise AssertionError('cumulative cap must reject an additional paid call')
    assert m.Budget(path, limit_rub=0.5).spent <= 0.5


def test_budget_rejects_uncosted_provider_and_limits(tmp_path):
    m = load('eval_budget')
    for bad in (0, -1, 51, float('nan'), float('inf')):
        try: m.Budget(tmp_path / 'x.json', limit_rub=bad)
        except ValueError: pass
        else: raise AssertionError('invalid budget')
    assert m.pricing_supported('https://api.proxyapi.ru/v1', 'deepseek/deepseek-v4-flash')
    assert not m.pricing_supported('https://polza.ai/api/v1', 'deepseek/deepseek-v4-flash')
    assert not m.pricing_supported('https://api.proxyapi.ru/v1', 'another-model')


def test_snapshot_bridge_is_replay_only_and_cannot_average_critical_failure(tmp_path):
    m = load('promptfoo_bridge')
    p = tmp_path / 'snapshot.json'
    p.write_text(json.dumps({'rows': [{'id':'x','question':'Q','origin':'oracle_assembly_NOT_model',
        'proposal': {'information': {'text': 'Water'}}, 'checks': {'facts_immediate': {'pass':False, 'status':'FAIL'}}}]}))
    reply = m.call_api('Q', {'config':{'snapshot':str(p)}}, {'vars':{'case_id':'x'}})
    result = m.get_assert(reply['output'], {})
    assert result['pass'] is False
    assert result['score'] == 0
    assert 'error' in m.call_api('Q', {'config':{'snapshot':str(p)}}, {'vars':{'case_id':'missing'}})


def test_unrun_and_content_gap_do_not_get_quality_credit():
    m = load('answer_eval_stand')
    assert not m.overall(m.check_answer({}, {'status':'not_run'}))
    checks = m.check_answer({'content_gap':True}, {'status':'information','information':{'text':'Прочитай договор.'}})
    assert not checks['content_coverage']['pass']


def test_prompt_capture_never_invokes_transport_and_restores_observer(tmp_path, monkeypatch):
    import asyncio
    m = load('answer_eval_stand')
    from project.llm.services import planner_ai, planner_content
    async def forbidden(*args,**kwargs):
        raise AssertionError('Capture attempted network')
    monkeypatch.setattr(planner_ai,'call_llm',forbidden)
    monkeypatch.setenv('PLANNER_AI_ENABLED','1')
    monkeypatch.setenv('LLM_ENABLED','1')
    config=json.loads((ROOT/'submission/scenario.json').read_text())['config']
    cases=[{'id':'x','question':'Какие документы для работы?','fact_groups':[]}]
    rows,quotes=asyncio.run(m.live_pilot(cases,config,planner_content,tmp_path/'never.json',capture=True))
    assert rows[0]['origin']=='capture_NOT_model'
    assert len(quotes)==1 and quotes[0]['attempts_reserved']==2
    assert planner_ai.call_llm is forbidden
    assert not (tmp_path/'never.json').exists()


def test_unknown_provider_configuration_cannot_make_paid_request(tmp_path,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    m=load('answer_eval_stand')
    from project.llm.config import LLMSettings
    from project.llm.services import planner_content,planner_ai
    original=planner_ai.call_llm
    monkeypatch.setattr(LLMSettings,'from_env',lambda:SimpleNamespace(base_url='https://unpriced.example',model='unknown'))
    try:asyncio.run(m.live_pilot([],{},planner_content,tmp_path/'budget.json'))
    except ValueError:pass
    else:raise AssertionError('Unpriced provider must fail closed')
    assert planner_ai.call_llm is original


def test_replay_not_run_is_not_a_passing_answer():
    m=load('promptfoo_bridge')
    result=m.get_assert(json.dumps({'checks':{'availability':{'pass':None,'status':'NOT_RUN'}}}),{})
    assert not result['pass']


def test_correct_facts_do_not_excuse_generic_preface():
    m=load('answer_eval_stand')
    p={'status':'information','information':{'text':'Перед оформлением на работу полезно заранее собрать вопросы о документах. Подготовь паспорт.'}}
    result=m.check_answer({'fact_groups':[['паспорт']]},p)
    assert result['facts_immediate']['pass']
    assert not result['no_filler']['pass']


def test_budget_write_failure_preserves_existing_reservations(tmp_path,monkeypatch):
    m=load('eval_budget');p=tmp_path/'budget.json';a=m.Budget(p)
    a.reserve([{'role':'user','content':'first'}],600,'answer')
    before=p.read_bytes()
    def disk_failure(*args):raise OSError('disk failed')
    monkeypatch.setattr(m.os,'replace',disk_failure)
    try:a.reserve([{'role':'user','content':'second'}],600,'answer')
    except OSError:pass
    else:raise AssertionError('write should fail before paid request')
    assert p.read_bytes()==before


def test_empty_or_malformed_ledger_never_resets_spending(tmp_path):
    m=load('eval_budget');p=tmp_path/'budget.json'
    for text in ('','{}','{"reservations": []}'):
        p.write_text(text)
        try:m.Budget(p)
        except (ValueError,KeyError):pass
        else:raise AssertionError('existing invalid ledger must fail closed')


def test_judge_failure_visible_but_separate_from_mechanical_score():
    m=load('answer_eval_stand')
    row={'id':'a','question':'Q','origin':'live_provider','proposal':{'information':{'text':'Ответ'}},'checks':{},
         'judge':{'status':'UNCALIBRATED_same_model','verdict':{'concrete':False,'reason':'Нет списка документов','quote':'Ответ'}}}
    page=m.render_report({'label':'live','live':'RUN','rows':[row]})
    assert 'Нет списка документов' in page and 'UNCALIBRATED' in page and 'concrete' in page
    result=load('promptfoo_bridge').judge_assert(json.dumps(row),{})
    assert not result['pass'] and result['score']==0
    result=load('promptfoo_bridge').judge_assert(json.dumps({'judge':{'status':'NOT_RUN'}}),{})
    assert not result['pass'] and 'NOT_RUN' in result['reason']


def test_source_id_with_wrong_target_cannot_pass():
    m=load('answer_eval_stand')
    case={'required_links':['employment_docs'],'expected_url_targets':{'employment_docs':['/?handbook=employment_docs']}}
    p={'status':'information','information':{'text':'Подготовь паспорт.','sources':[{'id':'employment_docs','url':'https://example.org/unrelated'}]}}
    result=m.check_answer(case,p)
    assert not result['required_links']['pass']
    assert not result['source_target_integrity']['pass']


def test_real_publication_allows_external_source_and_requires_internal_card():
    m=load('answer_eval_stand')
    from project.llm.services import planner_content as content
    config=json.loads((ROOT/'submission/scenario.json').read_text())['config']
    p={'status':'information','information':content.build_information(config,['employment_docs'])}
    case={'required_links':['employment_docs']}
    checks=m.check_answer(m.source_targets(case,p,config,content),p)
    assert checks['required_links']['pass'] and checks['source_target_integrity']['pass']
    p['information']['sources']=[ref for ref in p['information']['sources'] if ref['url'].startswith('https:')]
    checks=m.check_answer(m.source_targets(case,p,config,content),p)
    assert not checks['required_links']['pass'] and checks['source_target_integrity']['pass']


def test_judge_exception_preserves_actual_answer_and_checks(tmp_path,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    m=load('answer_eval_stand')
    from project.llm.services import planner_ai,planner_content
    from project.llm.config import LLMSettings
    calls=[]
    async def provider(messages,**kwargs):
        calls.append(messages)
        if len(calls)>1:raise RuntimeError('sensitive-provider-body')
        return json.dumps({'status':'draft','topic':'work','route_alias':'work','material_ids':['employment_docs']})
    monkeypatch.setattr(planner_ai,'call_llm',provider)
    monkeypatch.setattr(LLMSettings,'from_env',lambda:SimpleNamespace(base_url='https://api.proxyapi.ru/v1',model='deepseek/deepseek-v4-flash'))
    monkeypatch.setenv('PLANNER_AI_ENABLED','1');monkeypatch.setenv('LLM_ENABLED','1')
    config=json.loads((ROOT/'submission/scenario.json').read_text())['config']
    case={'id':'work','question':'Какие документы нужны для работы?','required_links':['employment_docs']}
    rows,reservations=asyncio.run(m.live_pilot([case],config,planner_content,tmp_path/'budget.json',judge=True))
    row=rows[0]
    assert row['proposal'].get('information'), row
    assert row['judge']['status']=='ERROR' and row['judge']['error_type']=='RuntimeError'
    assert row['checks']['availability']['pass']
    assert 'sensitive-provider-body' not in json.dumps(rows)
    assert len(reservations)==2 and planner_ai.call_llm is provider


def test_ambiguous_question_cannot_pass_arbitrary_concrete_answer():
    m=load('answer_eval_stand')
    p={'status':'information','information':{'text':'Подготовь паспорт.'}}
    assert not m.check_answer({'clarify_required':True},p)['response_contract']['pass']


def test_outside_base_cannot_pass_unrelated_card():
    m=load('answer_eval_stand')
    case={'outside_base':True}
    p={'status':'information','information':{'text':'Подготовь паспорт.','sources':[{'id':'x','url':'/?handbook=x'}]}}
    assert not m.overall(m.check_answer(case,p))
    assert m.overall(m.check_answer(case,{'status':'unsupported'}))


def test_required_external_source_is_checked_in_addition_to_card():
    m=load('answer_eval_stand')
    case={'required_source_urls':['https://official.example/instruction']}
    p={'status':'information','information':{'text':'Проверь дату выплаты.','sources':[{'id':'work_wages','url':'/?handbook=work_wages'}]}}
    assert not m.check_answer(case,p)['required_sources']['pass']
    p['information']['sources'].append({'id':'work_wages','url':'https://official.example/instruction'})
    assert m.check_answer(case,p)['required_sources']['pass']


def test_production_offline_uses_enrich_searches_without_model():
    from project.llm.services import planner_content
    m=load('answer_eval_stand')
    c=json.loads((ROOT/'submission/scenario.json').read_text())['config']
    case={'id':'pipeline','question':'Хочу устроиться на работу.','oracle_material_ids':['employment_docs'],
          'required_links':['employment_docs','app_work']}
    row=m.offline([case],c,planner_content,production=True)[0]
    assert row['checks']['required_links']['pass']
    assert row['origin']=='oracle_assembly_NOT_model'
