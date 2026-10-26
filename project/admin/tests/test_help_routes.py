"""K3 published allowlist: no MAX/model/provider traffic."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from project.llm.services.help_routes import ROUTES, resolve_route, parse_payload, task_payload
from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.llm.bot import event_input


def published():
    return json.loads(Path('submission/scenario.json').read_text())['config']


@pytest.mark.parametrize('alias', ['housing','benefits','payments','legal','documents','study','work','rent','help'])
def test_catalog_resolves_only_safe_published_nodes(alias):
    route=resolve_route(published(),alias,{'role':'child'})
    assert route['available'] and route['node_id']==ROUTES[alias]['node_id']
    assert route['payload']=='r1_'+alias
    assert route['url'].endswith('?start=r1_'+alias)
    assert route['manual_path']


@pytest.mark.parametrize('payload',['','r1_unknown','r1_help_points','state_housing','jump:documents','https://evil.test','r1_'+'a'*70,'r1_документы','r1_documents\n',None,{},42])
def test_payload_is_exact_allowlist(payload):
    assert parse_payload(payload) is None


def test_revalidates_current_publication_role_conditions_and_node_type():
    c=published()
    assert not resolve_route(c,'help',{'role':'parent'})['available']
    n=next(n for n in c['nodes'] if n['id']=='documents')
    for change in [{'kind':'action'},{'next':'benefits'},{'routes':[{'field':'role','op':'eq','value':'child','target':'benefits'}]},{'conditions':[{'field':'age','op':'gte','value':18}]}]:
        changed=deepcopy(c);next(x for x in changed['nodes'] if x['id']=='documents').update(change)
        assert not resolve_route(changed,'documents',{'role':'child'})['available']
    assert resolve_route(c,'documents',{'role':'child'})['available']
    c['nodes'].remove(n)
    assert not resolve_route(c,'documents',{'role':'child'})['available']
    assert resolve_route(c,'documents',{'role':'child'})['url'] is None


def test_bot_start_preserves_navigation_as_untrusted_data():
    assert event_input({'update_type':'bot_started','user':{'user_id':7},'payload':'r1_documents'})[2]=='route_start:r1_documents'
    assert event_input({'update_type':'bot_started','user':{'user_id':7},'payload':'jump:documents'})[2]=='route_start:jump:documents'
    assert event_input({'update_type':'bot_started','user':{'user_id':7}})[2]=='reset'


@pytest.mark.parametrize('alias',list(['housing','benefits','payments','legal','documents','study','work','rent','help']))
def test_route_history_back_and_transient_clear_preserve_legacy_tasks(alias,monkeypatch):
    monkeypatch.setenv('PLANNER_ENABLED','1')
    async def run():
        d=FlowDialogue(PreviewReminders(),published());s=d.session(7)
        s.node='work';s.pending={'stage':'text'};s.work={'query':'temporary'};s.values.update(city='Тула',query='temporary');s.tasks=['Сохранённый legacy checklist']
        replies=await d.handle(7,payload='route_start:r1_'+alias)
        assert s.node==ROUTES[alias]['node_id'] and s.history==['menu']
        assert s.pending is None and not s.work and 'query' not in s.values
        assert s.tasks==['Сохранённый legacy checklist']
        buttons=[b for r in replies for a in r.get('attachments',[]) for row in a['payload']['buttons'] for b in row]
        assert any(b.get('type')=='open_app' and b.get('payload')=='plan' for b in buttons)
        await d.handle(7,payload='back');assert s.node=='menu'
    asyncio.run(run())


def test_task_pointer_is_only_canonical_uuid():
    from uuid import UUID
    assert task_payload(UUID('8b434c34-729c-4d59-b605-d5b0b46bc665'))=='task_8b434c34-729c-4d59-b605-d5b0b46bc665'
    with pytest.raises(ValueError):task_payload('personal text')


@pytest.mark.parametrize('payload',['jump:documents','documents','r1_unknown','r1_'+'x'*65,'https://evil.test','reset'])
def test_untrusted_start_never_executes_callback_or_resets_session(payload):
    async def run():
        d=FlowDialogue(PreviewReminders(),published());s=d.session(7)
        s.node='menu';s.tasks=['сохранённый список']
        replies=await d.handle(7,payload='route_start:'+payload)
        assert s.node=='menu' and s.tasks==['сохранённый список']
        assert 'недоступен' in replies[0]['text']
    asyncio.run(run())


def test_route_rejects_published_checklist_and_preserves_existing_tasks():
    async def run():
        c=published();next(n for n in c['nodes'] if n['id']=='documents')['tasks']=['Publication supplied checklist']
        assert not resolve_route(c,'documents',{'role':'child'})['available']
        d=FlowDialogue(PreviewReminders(),c);s=d.session(7)
        s.node='menu';s.tasks=['Existing checklist'];s.done={0};nonce=s.nonce
        replies=await d.handle(7,payload='route_start:r1_documents')
        assert s.node=='menu' and s.tasks==['Existing checklist'] and s.done=={0} and s.nonce==nonce
        assert 'недоступен' in replies[0]['text']
    asyncio.run(run())
