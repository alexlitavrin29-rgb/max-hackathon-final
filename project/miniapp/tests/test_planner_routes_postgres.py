"""K3 server links use current publication and owner-scoped task reads."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, web
from project.miniapp.tests.test_planner_k1_postgres import post


@pytest.mark.asyncio
async def test_route_links_and_task_pointer_are_authenticated_and_owner_scoped(contour):
    pool,client,_=contour
    a,b=[await launch(client,owner) for owner in OWNERS]
    task={'id':str(uuid4()),'title':'Личный текст не должен оказаться в ссылке'}
    assert (await post(client,a,'tasks',task)).status_code==201
    path='/api/planner/tasks/'+task['id']+'/links'
    assert (await client.get('/api/planner/routes')).status_code==401
    assert (await client.get(path)).status_code==401
    own=await client.get(path,headers=a)
    assert own.status_code==200
    links=own.json();assert links['task_url'].endswith('?startapp=task_'+task['id'])
    assert links['plan_url'].endswith('?startapp=plan')
    assert task['title'] not in json.dumps(links) and 'owner' not in json.dumps(links)
    missing=await client.get('/api/planner/tasks/'+str(uuid4())+'/links',headers=b)
    foreign=await client.get(path,headers=b)
    assert foreign.status_code==missing.status_code==404 and foreign.json()==missing.json()
    catalog=(await client.get('/api/planner/routes',headers=a)).json()
    assert len(catalog['routes'])==9 and all(r['available'] for r in catalog['routes'])
    assert all(r['url'].endswith('?start=r1_'+r['alias']) for r in catalog['routes'])
    # Navigation does not mutate a persistent plan or its version/generation.
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks')==1
    assert (await client.get('/api/planner/tasks/'+task['id'],headers=a)).json()['task']['version']==1
    # Refresh a real publication, never use a cached old/unsaved draft catalog.
    c=json.loads(Path('submission/scenario.json').read_text())['config']
    n=next(n for n in c['nodes'] if n['id']=='documents');n['conditions']=[{'field':'age','op':'gte','value':18}]
    from project.llm.services.flow import PublishedDialogue, PreviewReminders
    store=web.app.state.store
    dialogue=PublishedDialogue(PreviewReminders(),store)
    await dialogue.handle(OWNERS[0],payload='route_start:r1_documents')
    assert dialogue.engine.session(OWNERS[0]).node=='documents'
    before=(await client.get('/api/planner/tasks/'+task['id'],headers=a)).json()
    assert before['task']['version']==1
    current=await store.read()
    await store.publish(await store.save(c,current['revision']),'Synthetic route revision')
    changed=(await client.get('/api/planner/routes',headers=a)).json()
    assert changed['revision']!=catalog['revision']
    doc=next(r for r in changed['routes'] if r['alias']=='documents')
    assert doc['available'] is False and doc['url'] is None
    await dialogue.refresh() # New publication creates a new engine when no persistent bot-session store is attached.
    dialogue.engine.session(OWNERS[0]).node='menu'
    fallback=await dialogue.handle(OWNERS[0],payload='route_start:r1_documents')
    assert dialogue.engine.session(OWNERS[0]).node=='menu'
    assert 'недоступен' in fallback[0]['text']
    assert (await client.get('/api/planner/tasks/'+task['id'],headers=a)).json()==before
    # The same pointer requires a new, valid owner session after previous bearer expires.
    await pool.execute("UPDATE miniapp_sessions SET expires_at=now()-interval '2 hours'")
    await pool.execute('DELETE FROM miniapp_launches')
    assert (await client.get(path,headers=a)).status_code==401
    renewed=await launch(client,OWNERS[0])
    assert (await client.get(path,headers=renewed)).json()==links


@pytest.mark.asyncio
async def test_route_reads_do_not_nest_pool_acquisitions(contour):
    import asyncio
    _,client,_=contour
    a=await launch(client,OWNERS[0])
    replies=await asyncio.wait_for(asyncio.gather(*[
        client.get('/api/planner/routes',headers=a) for _ in range(8)
    ]),timeout=3)
    assert all(r.status_code==200 for r in replies)
