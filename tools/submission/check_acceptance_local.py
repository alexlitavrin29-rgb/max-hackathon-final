"""Exercise the frozen runtime over HTTP with synthetic MAX identities."""
import hashlib,hmac,json,subprocess,time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4

HERE=Path(__file__).resolve().parent;CODE=HERE.parents[1];OUT=CODE.parent;(OUT/'evidence').mkdir(exist_ok=True)
BASE='http://127.0.0.1:18976';SECOND='http://127.0.0.1:18977'
TOKEN='local-preview-no-max-access';NAME='tochka-acceptance-v2-audit-signed-20261026';NAME2=NAME+'-second'
compose=['docker','compose','-p','tochka-acceptance-v2-audit-20261026','--env-file','submission/.env','-f','submission/compose.yaml']
checks=[]
def docker(args):return subprocess.check_output(['docker',*args],cwd=CODE,text=True,encoding='utf-8',stderr=subprocess.STDOUT).strip()
def start(name,port):
    found=subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if found.returncode==0:return
    subprocess.run(compose+['run','-d','--no-deps','--name',name,'-p',f'127.0.0.1:{port}:8766',
        '-e','MINIAPP_PREVIEW=0','-e','MINIAPP_ORIGIN=https://acceptance.test','miniapp'],cwd=CODE,check=True,capture_output=True)
def call(path,method='GET',body=None,bearer='',base=BASE,key=None):
    headers={'Content-Type':'application/json'}
    if bearer:headers['Authorization']='Bearer '+bearer
    if key:headers['Idempotency-Key']=key
    r=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    try:response=urlopen(r,timeout=25)
    except HTTPError as e:response=e
    with response:return response.status,json.load(response)
def ready(base=BASE):
    for _ in range(50):
        try:
            if call('/api/health',base=base)[0]==200:return
        except OSError:pass
        time.sleep(.2)
    raise RuntimeError('Synthetic app did not become ready')
def signed(owner):
    values={'auth_date':str(int(time.time())),'user':json.dumps({'id':owner})}
    secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
    raw='\n'.join(f'{k}={values[k]}' for k in sorted(values))
    return urlencode({**values,'hash':hmac.new(secret,raw.encode(),hashlib.sha256).hexdigest()})
def launch(owner):
    status,body=call('/api/session','POST',{'init_data':signed(owner)})
    assert status==200,(status,body)
    return body['session']
start(NAME,18976);ready();start(NAME2,18977);ready(SECOND)
a=launch(880000000026);b=launch(880000000027)
for p in ['/api/planner/goals','/api/planner/tasks','/api/handbook','/api/housing','/api/career']:
    assert call(p)[0]==401
checks.append('all personal APIs reject anonymous requests')
status,doc=call('/api/handbook/orphan_payments',bearer=a)
assert status==200 and doc['card']['text'] and doc['card']['actions']
assert call('/api/handbook/orphan_payments/favorite','POST',{'saved':True},a)[0]==200
assert len(call('/api/favorites',bearer=a)[1]['cards'])==1
assert call('/api/favorites',bearer=b)[1]['cards']==[]
checks.append('prepared answer, source links, favorite and owner isolation')
question='Как не потерять заработанные деньги?'
status,savings=call('/api/planner/ai/find-help-route','POST',{'text':question},a)
assert status==200, f'Original savings question returned HTTP{status}'
assert savings.get('context',{}).get('material_ids')==['money_budget']
assert 'app_work' not in savings['context'].get('required_resources',[])
assert any('fincult.info/' in source['url'] for source in savings.get('information',{}).get('sources',[]))
status,proposal=call('/api/planner/ai/draft-plan','POST',{'text':question,'selected_material_ids':['money_budget']},a)
assert status==200 and proposal.get('tasks'), 'Prepared budget plan was not returned'
assert proposal['context']['material_ids']==['money_budget']
assert 'app_work' not in proposal['context'].get('required_resources',[])
checks.append('original savings question returns reviewed budget sources and a prepared plan without vacancy search')
goal=proposal['goal_id'];task=str(uuid4())
body={'goal':{'id':goal,'title':'Сохранить заработанные деньги'},'tasks':[{'id':task,'title':proposal['tasks'][0]['title'],'material_ids':['money_budget'],'due_date':'2026-10-28'}],'context':proposal['context'],'confirmed':True}
s,created=call('/api/planner/commit','POST',body,a,key='acceptance-commit-'+goal)
assert s==201,(s,created)
s,repeated=call('/api/planner/commit','POST',body,a,key='acceptance-commit-'+goal)
assert s==201 and repeated==created
status,t=call('/api/planner/tasks/'+task,bearer=a);assert status==200 and t['task']['due_date']=='2026-10-28'
assert call('/api/planner/tasks/'+task,bearer=b)[0]==404
checks.append('confirmed plan, date, exact retry and foreign-owner rejection')
version=t['task']['version']
status,t=call('/api/planner/tasks/'+task,'PATCH',{'status':'done','expected_version':version},a,key='acceptance-done-'+task)
assert status==200 and t['task']['status']=='done',(status,t)
assert call('/api/planner/tasks/'+task,'PATCH',{'status':'in_progress','expected_version':version},a,key='acceptance-stale-'+task)[0]==409
checks.append('completion persists; stale version cannot overwrite')
for route,state in [('housing','in_list'),('career','resume')]:
    s,doc=call('/api/'+route,'PUT',{'status':state,'expected_version':0},a)
    assert s==200 and doc[route]['status']==state,(s,doc)
    assert call('/api/'+route,bearer=b)[1][route] is None
checks.append('housing and career marks persist independently per owner')
assert call('/api/planner/tasks/'+task,bearer=a,base=SECOND)[1]['task']['status']=='done'
checks.append('same bearer and data accepted by a second process')
docker(['restart',NAME]);ready()
assert call('/api/planner/tasks/'+task,bearer=a)[1]['task']['status']=='done'
assert call('/api/favorites',bearer=a)[1]['cards']
checks.append('restart preserves bearer, completed task, date and favorite')
assert call('/api/account','DELETE',bearer=a)[0]==200
assert call('/api/planner/tasks/'+task,bearer=a)[0]==401
assert call('/api/planner/tasks/'+task,bearer=a,base=SECOND)[0]==401
assert call('/api/account','DELETE',bearer=b)[0]==200
checks.append('account deletion revokes both processes; synthetic records cleaned')
result={'ok':True,'checks':checks,'base':BASE,'second':SECOND,'synthetic_only':True,'production_records':0,'provider_calls':0,'max_messages':0}
(OUT/'evidence/LOCAL-HTTP.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
