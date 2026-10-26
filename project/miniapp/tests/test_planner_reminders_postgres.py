"""K4: fake MAX, real disposable PostgreSQL, synthetic owners, injected2099 clock."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS
from project.miniapp.tests.test_planner_k1_postgres import post, patch, delete
from project.llm.channels.max import MaxError
from project.llm.services.planner_reminders import PlannerReminders, apply_delivery_migration

NOW=datetime(2099,1,2,12,tzinfo=timezone.utc)


class FakeMax:
    def __init__(self,error=None):self.calls=[];self.error=error
    async def send(self,owner,message):
        self.calls.append((owner,message))
        if self.error:raise self.error
        return {'message':{'body':{'mid':'synthetic-mid'}}}


@pytest_asyncio.fixture
async def delivery(contour):
    pool,client,_=contour;await apply_delivery_migration(pool)
    headers=await launch(client,OWNERS[0]);api=FakeMax()
    worker=PlannerReminders(pool,api,allowed_owners={str(OWNERS[0])},max_lateness_seconds=3600,clock=lambda:NOW)
    return pool,client,headers,api,worker


async def seed(delivery,seconds=10,title='Синтетическое дело'):
    pool,client,headers,_,_=delivery
    identifier=str(uuid4());due=NOW-timedelta(seconds=seconds)
    result=await post(client,headers,'tasks',dict(id=identifier,title=title,due_at=due.isoformat(),timezone='UTC',timezone_confirmed=True,remind_before_minutes=0))
    assert result.status_code==201,result.text
    return identifier


async def notification(pool,identifier):
    return await pool.fetchrow('SELECT * FROM planner_notifications WHERE task_id=$1 ORDER BY generation DESC',UUID(identifier))


@pytest.mark.asyncio
async def test_concurrent_claim_tokens_and_only_one_external_send(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery)
    other=PlannerReminders(pool,api,allowed_owners=w.allowed_owners,max_lateness_seconds=3600,clock=lambda:NOW)
    claims=await asyncio.gather(w.claim(),other.claim());tickets=[x for batch in claims for x in batch]
    assert len(tickets)==1 and tickets[0]['claim_token']
    await asyncio.gather(w.deliver(tickets[0]),other.deliver(tickets[0]))
    assert len(api.calls)==1 and (await notification(pool,identifier))['state']=='delivered'


@pytest.mark.asyncio
@pytest.mark.parametrize('age,state',[(3599,'delivered'),(3600,'delivered'),(3601,'missed')])
async def test_owner_hour_policy_is_inclusive_and_preserves_task(delivery,age,state):
    pool,client,h,api,w=delivery;identifier=await seed(delivery,age)
    await w.tick();assert (await notification(pool,identifier))['state']==state
    assert len(api.calls)==(state=='delivered')
    assert (await client.get('/api/planner/tasks/'+identifier,headers=h)).json()['task']['status']=='not_started'


@pytest.mark.asyncio
async def test_crash_before_sending_reclaims_only_expired_lease(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);old=(await w.claim())[0]
    assert await w.claim()==[]
    w.clock=lambda:NOW+timedelta(seconds=31)
    new=(await w.claim())[0];assert new['claim_token']!=old['claim_token']
    await w.deliver(old);assert not api.calls
    await w.deliver(new);assert len(api.calls)==1


@pytest.mark.asyncio
async def test_crash_after_sending_is_unknown_and_never_retried(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);ticket=(await w.claim())[0]
    assert await w.prepare(ticket)
    w.clock=lambda:NOW+timedelta(seconds=31)
    await w.tick();await w.tick()
    assert (await notification(pool,identifier))['state']=='unknown' and not api.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('error,state',[(MaxError('http',403),'failed'),(MaxError('http',429),'failed'),(MaxError('timeout'),'unknown'),(MaxError('connection'),'unknown')])
async def test_delivery_faults_never_retry_or_delete_task(delivery,error,state):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);api.error=error
    await w.tick();await w.tick()
    assert len(api.calls)==1 and (await notification(pool,identifier))['state']==state
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks')==1


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['done','delete','disable','move'])
async def test_task_change_wins_before_sending(delivery,change):
    pool,client,h,api,w=delivery;identifier=await seed(delivery);ticket=(await w.claim())[0]
    if change=='delete':r=await delete(client,h,identifier)
    else:
        fields={'done':{'status':'done'},'disable':{'remind_before_minutes':None},'move':{'due_at':(NOW+timedelta(days=1)).isoformat(),'timezone_confirmed':True}}[change]
        r=await patch(client,h,identifier,dict(expected_version=1,**fields))
    assert r.status_code==200
    await w.deliver(ticket);assert not api.calls


@pytest.mark.asyncio
async def test_account_delete_wins_and_finish_never_resurrects(delivery):
    pool,client,h,api,w=delivery;await seed(delivery);ticket=(await w.claim())[0]
    assert (await client.delete('/api/account',headers=h)).status_code==200
    await w.deliver(ticket);await w.finish(ticket,'delivered')
    assert not api.calls and await pool.fetchval('SELECT count(*) FROM planner_notifications')==0


@pytest.mark.asyncio
async def test_wrong_token_epoch_generation_and_allowlist_block_sending(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);ticket=(await w.claim())[0]
    assert await w.prepare({**ticket,'claim_token':uuid4()}) is None
    await pool.execute('UPDATE planner_tasks SET schedule_generation=schedule_generation+1 WHERE id=$1',UUID(identifier))
    await w.deliver(ticket);assert not api.calls
    other=PlannerReminders(pool,api,allowed_owners={str(OWNERS[1])},max_lateness_seconds=3600,clock=lambda:NOW)
    assert await other.claim()==[]


@pytest.mark.asyncio
async def test_stop_start_preserves_tasks_but_does_not_restore_notifications(delivery):
    pool,client,h,api,w=delivery;identifier=await seed(delivery);ticket=(await w.claim())[0]
    await w.set_stopped(str(OWNERS[0]),True);await w.deliver(ticket)
    task=(await client.get('/api/planner/tasks/'+identifier,headers=h)).json()['task']
    assert task['title']=='Синтетическое дело' and task['remind_before_minutes'] is None
    assert (await notification(pool,identifier))['state']=='cancelled' and not api.calls
    await w.set_stopped(str(OWNERS[0]),False);await w.tick();assert not api.calls


@pytest.mark.asyncio
async def test_callback_actor_generation_and_duplicate_done(delivery):
    pool,client,h,_,w=delivery;identifier=await seed(delivery);await w.tick();row=await notification(pool,identifier)
    payload=f"p1d_{row['id'].hex}_{row['generation']}"
    assert await w.complete_callback(str(OWNERS[1]),payload)=='unavailable'
    assert await w.complete_callback(str(OWNERS[0]),payload)=='done'
    first=(await client.get('/api/planner/tasks/'+identifier,headers=h)).json()['task']
    assert first['status']=='done'
    assert await w.complete_callback(str(OWNERS[0]),payload)=='done'
    assert (await client.get('/api/planner/tasks/'+identifier,headers=h)).json()['task']['version']==first['version']
    result=await patch(client,h,identifier,dict(expected_version=first['version'],status='not_started'))
    assert result.status_code==200
    assert await w.complete_callback(str(OWNERS[0]),payload)=='stale'
    assert (await client.get('/api/planner/tasks/'+identifier,headers=h)).json()['task']['status']=='not_started'


@pytest.mark.asyncio
async def test_old_callback_after_move_cannot_complete_new_schedule(delivery):
    pool,client,h,_,w=delivery;identifier=await seed(delivery);await w.tick();row=await notification(pool,identifier)
    result=await patch(client,h,identifier,dict(expected_version=1,due_at=(NOW+timedelta(days=1)).isoformat(),timezone_confirmed=True))
    assert result.status_code==200
    assert await w.complete_callback(str(OWNERS[0]),f"p1d_{row['id'].hex}_{row['generation']}")=='stale'
    assert (await notification(pool,identifier))['state']=='pending'


@pytest.mark.asyncio
async def test_stop_rejects_silent_reschedule_until_new_start(delivery):
    _,client,h,_,w=delivery;await w.set_stopped(str(OWNERS[0]),True)
    result=await post(client,h,'tasks',dict(id=str(uuid4()),title='Остановленный бот',due_at=(NOW+timedelta(days=1)).isoformat(),timezone='UTC',timezone_confirmed=True,remind_before_minutes=0))
    assert result.status_code==409 and result.json()['detail']['code']=='notifications_paused'
    await w.set_stopped(str(OWNERS[0]),False)
    assert await seed(delivery)


@pytest.mark.asyncio
async def test_sending_boundary_releases_db_locks_and_done_does_not_recall_http(delivery):
    pool,client,h,api,w=delivery;identifier=await seed(delivery)
    entered=asyncio.Event();release=asyncio.Event()
    async def sending(owner,body):
        api.calls.append((owner,body));entered.set();await release.wait()
        return {'message':{'body':{'mid':'already-protected'}}}
    api.send=sending
    job=asyncio.create_task(w.tick());await entered.wait()
    result=await asyncio.wait_for(patch(client,h,identifier,dict(expected_version=1,status='done')),timeout=1)
    assert result.status_code==200
    release.set();await job
    assert len(api.calls)==1 and (await notification(pool,identifier))['state']=='delivered'
    assert (await client.get('/api/planner/tasks/'+identifier,headers=h)).json()['task']['status']=='done'


@pytest.mark.asyncio
async def test_delete_account_during_http_cannot_restore_any_row(delivery):
    pool,client,h,api,w=delivery;await seed(delivery);entered=asyncio.Event();release=asyncio.Event()
    async def sending(owner,body):
        entered.set();await release.wait();return {'message':{'body':{'mid':'accepted'}}}
    api.send=sending;job=asyncio.create_task(w.tick());await entered.wait()
    assert (await asyncio.wait_for(client.delete('/api/account',headers=h),timeout=1)).status_code==200
    release.set();await job
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks')==0
    assert await pool.fetchval('SELECT count(*) FROM planner_notifications')==0


@pytest.mark.asyncio
async def test_cancel_during_http_becomes_unknown_and_will_not_retry(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);entered=asyncio.Event()
    async def sending(owner,body):
        api.calls.append((owner,body));entered.set();await asyncio.Event().wait()
    api.send=sending;job=asyncio.create_task(w.tick());await entered.wait();job.cancel()
    with pytest.raises(asyncio.CancelledError):await job
    assert (await notification(pool,identifier))['state']=='unknown'
    await w.tick();assert len(api.calls)==1


@pytest.mark.asyncio
async def test_epoch_tombstone_blocks_old_generation(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);ticket=(await w.claim())[0]
    await pool.execute('INSERT INTO account_epochs(owner,epoch) VALUES($1,1) ON CONFLICT(owner) DO UPDATE SET epoch=1',str(OWNERS[0]))
    await w.deliver(ticket)
    assert not api.calls and (await notification(pool,identifier))['state']=='cancelled'


@pytest.mark.asyncio
async def test_real_timeout_is_bounded_and_terminates_unknown(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery)
    async def hang(owner,body):api.calls.append((owner,body));await asyncio.Event().wait()
    api.send=hang;w.send_timeout=.02
    await asyncio.wait_for(w.tick(),timeout=1)
    assert (await notification(pool,identifier))['state']=='unknown' and len(api.calls)==1
    await w.tick();assert len(api.calls)==1


@pytest.mark.asyncio
async def test_second_singleton_guard_rejected_on_distinct_connections(delivery):
    pool,*_=delivery
    async with pool.acquire() as first:
        async with pool.acquire() as second:
            assert await first.fetchval('SELECT pg_try_advisory_lock($1)',991004)
            try:assert not await second.fetchval('SELECT pg_try_advisory_lock($1)',991004)
            finally:await first.fetchval('SELECT pg_advisory_unlock($1)',991004)


@pytest.mark.asyncio
async def test_candidate_to_real_rollback_source_delete_and_return(delivery):
    import os,json,sys
    from pathlib import Path
    rollback=Path(os.getenv('PLANNER_ROLLBACK_ROOT','/rollback'))
    if not (rollback/'project/miniapp/app.py').exists():pytest.skip('Explicit compatible rollback checkout mount required')
    pool,client,h,api,w=delivery;identifier=await seed(delivery)
    b=await launch(client,OWNERS[1]);bid=str(uuid4())
    assert (await post(client,b,'tasks',dict(id=bid,title='Другой владелец',due_at=(NOW+timedelta(days=1)).isoformat(),timezone='UTC',timezone_confirmed=True,remind_before_minutes=0))).status_code==201
    await pool.execute("UPDATE planner_notifications SET state='unknown',finished_at=now() WHERE task_id=$1",UUID(bid))
    for owner in OWNERS:
        await pool.execute('''INSERT INTO planner_drafts(owner,account_epoch,state,expires_at,updated_at)
            VALUES($1,0,'{"kind":"editor"}'::jsonb,now()+interval '24 hours',now())''',str(owner))
        await pool.execute('''INSERT INTO planner_proposal_policies(owner,account_epoch,goal_id,question,required_resources)
            VALUES($1,0,$2,'Синтетическая консультация','[]'::jsonb)''',str(owner),uuid4())
    before=(await client.get('/api/planner/tasks/'+bid,headers=b)).json()
    await pool.executemany("INSERT INTO planner_resumes(owner,account_epoch,state) VALUES($1,0,'{}'::jsonb)",[(str(owner),) for owner in OWNERS])
    schema=await pool.fetchval('SELECT current_schema()')
    environment={**os.environ,'PYTHONPATH':str(rollback),'PLANNER_ENABLED':'0','PLANNER_NOTIFICATIONS_ENABLED':'0','LLM_ENABLED':'0','ROLLBACK_SCHEMA':schema}
    child=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve().parents[3]/'tools/submission/planner_rollback_probe.py'),cwd=rollback,env=environment,
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    output,error=await asyncio.wait_for(child.communicate(json.dumps({'headers':h}).encode()),timeout=15)
    assert child.returncode==0,error.decode()
    assert json.loads(output)['deleted_owner_only']
    assert await pool.fetchval('SELECT count(*) FROM planner_tasks WHERE owner=$1',str(OWNERS[0]))==0
    assert await pool.fetchval('SELECT count(*) FROM planner_operations WHERE owner=$1',str(OWNERS[0]))==0
    for table in ('planner_drafts','planner_proposal_policies','planner_resumes'):
        assert await pool.fetchval('SELECT count(*) FROM '+table+' WHERE owner=$1',str(OWNERS[0]))==0
        assert await pool.fetchval('SELECT count(*) FROM '+table+' WHERE owner=$1',str(OWNERS[1]))==1
    assert (await client.get('/api/planner/tasks/'+bid,headers=b)).json()==before
    await w.tick();assert not api.calls
    bw=PlannerReminders(pool,api,allowed_owners={str(OWNERS[1])},max_lateness_seconds=3600,clock=lambda:NOW+timedelta(days=2))
    await bw.tick();assert not api.calls and (await notification(pool,bid))['state']=='unknown'


@pytest.mark.asyncio
async def test_legacy_sending_without_lease_is_unknown_not_stuck(delivery):
    pool,_,_,api,w=delivery;identifier=await seed(delivery);ticket=(await w.claim())[0]
    assert await w.prepare(ticket)
    # Frozen K1 schema requires sending_at/token, but permits a null sending lease.
    await pool.execute('UPDATE planner_notifications SET lease_until=NULL WHERE task_id=$1',UUID(identifier))
    await w.tick()
    assert (await notification(pool,identifier))['state']=='unknown' and not api.calls


@pytest.mark.asyncio
async def test_restart_reconciles_old_stop_but_does_not_replay_old_text(delivery):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from project.llm.bot import Runner
    pool,_,_,api,w=delivery;identifier=await seed(delivery)
    dialogue=SimpleNamespace(sessions={},handle=AsyncMock(return_value=[]))
    runner=Runner(api,dialogue,SimpleNamespace(pool=pool,delete_user=AsyncMock()),'test',planner=w)
    await runner.process({'update_type':'bot_stopped','timestamp':runner.started_ms-1000,'user':{'user_id':OWNERS[0]}})
    await w.tick();assert not api.calls and (await notification(pool,identifier))['state']=='cancelled'
    await runner.process({'update_type':'message_created','timestamp':runner.started_ms-1000,'message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':OWNERS[0]},'body':{'text':'stale input'}}})
    assert not dialogue.handle.called


@pytest.mark.asyncio
async def test_stale_availability_replay_cannot_undo_newer_start_or_stop(delivery):
    from project.llm.services.planner_reminders import record_bot_availability
    pool,*_=delivery;owner=str(OWNERS[0])
    await record_bot_availability(pool,owner,True,1000)
    await record_bot_availability(pool,owner,False,2000)
    await record_bot_availability(pool,owner,True,1000)
    assert not await pool.fetchval('SELECT planner_stopped FROM account_epochs WHERE owner=$1',owner)
    await record_bot_availability(pool,owner,True,3000)
    await record_bot_availability(pool,owner,False,2000)
    assert await pool.fetchval('SELECT planner_stopped FROM account_epochs WHERE owner=$1',owner)
    await record_bot_availability(pool,owner,False,3000)
    assert await pool.fetchval('SELECT planner_stopped FROM account_epochs WHERE owner=$1',owner)


@pytest.mark.asyncio
async def test_worker_waits_until_startup_backlog_drained(delivery):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from project.llm.bot import Runner
    pool,_,_,api,w=delivery;identifier=await seed(delivery)
    started=asyncio.Event();release=asyncio.Event();batches=0
    async def updates(marker=None,*,timeout=20):
        nonlocal batches
        batches+=1
        if batches==1:
            assert timeout==0;started.set();await release.wait()
            return {'updates':[{'update_type':'bot_stopped','timestamp':runner.started_ms-1000,'user':{'user_id':OWNERS[0]}}],'marker':1}
        if batches==2:
            assert marker==1 and timeout==0
            return {'updates':[],'marker':2}
        await asyncio.Event().wait()
    api.updates=updates
    runner=Runner(api,SimpleNamespace(sessions={},handle=AsyncMock(return_value=[])),SimpleNamespace(pool=pool,delete_user=AsyncMock()),'test',planner=w)
    poll=asyncio.create_task(runner.poll());job=asyncio.create_task(w.run())
    try:
        await asyncio.wait_for(started.wait(),timeout=.5);await asyncio.sleep(.05)
        assert not api.calls and (await notification(pool,identifier))['state']=='pending'
        release.set();await asyncio.wait_for(runner.planner_ready.wait(),timeout=1)
        assert (await notification(pool,identifier))['state']=='cancelled' and not api.calls
    finally:
        poll.cancel();job.cancel();await asyncio.gather(poll,job,return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_availability_write_keeps_delivery_gate_closed(delivery,monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from project.llm import bot
    pool,_,_,api,w=delivery;await seed(delivery);attempted=asyncio.Event();batches=0
    async def broken(*args):attempted.set();raise RuntimeError('synthetic persistence outage')
    monkeypatch.setattr(bot,'record_bot_availability',broken)
    async def updates(marker=None,*,timeout=20):
        nonlocal batches
        batches+=1
        if batches==1:return {'updates':[{'update_type':'bot_stopped','timestamp':runner.started_ms-1000,'user':{'user_id':OWNERS[0]}}],'marker':1}
        if batches==2:return {'updates':[],'marker':2}
        await asyncio.Event().wait()
    api.updates=updates
    runner=bot.Runner(api,SimpleNamespace(sessions={},handle=AsyncMock()),SimpleNamespace(pool=pool,delete_user=AsyncMock()),'test',planner=w)
    poll=asyncio.create_task(runner.poll());job=asyncio.create_task(w.run())
    try:
        await asyncio.wait_for(attempted.wait(),timeout=1);await asyncio.sleep(.05)
        assert not runner.planner_ready.is_set() and not api.calls
    finally:
        poll.cancel();job.cancel();await asyncio.gather(poll,job,return_exceptions=True)
