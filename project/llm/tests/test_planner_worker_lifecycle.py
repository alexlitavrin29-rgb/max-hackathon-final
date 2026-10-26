import asyncio
import os
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock

import pytest

from project.llm.services.planner_reminders import PlannerReminders, configured_worker


def test_delivery_off_by_default_and_config_fails_closed():
    assert configured_worker(None,None,{}) is None
    base={'PLANNER_NOTIFICATIONS_ENABLED':'1','PLANNER_ENABLED':'1','PLANNER_NOTIFICATION_OWNERS':'900000000001','PLANNER_MAX_LATENESS_SECONDS':'3600'}
    assert configured_worker(None,None,base).max_lateness.total_seconds()==3600
    for key in ['PLANNER_ENABLED','PLANNER_NOTIFICATION_OWNERS','PLANNER_MAX_LATENESS_SECONDS']:
        changed={**base};changed.pop(key)
        with pytest.raises(ValueError):configured_worker(None,None,changed)
    for owners in ['*','900000000001,0','user','']:
        with pytest.raises(ValueError):configured_worker(None,None,{**base,'PLANNER_NOTIFICATION_OWNERS':owners})


def test_unexpected_tick_exception_is_isolated_and_heartbeat_has_no_personal_data():
    async def run():
        w=PlannerReminders(None,None,allowed_owners={'900000000001'},max_lateness_seconds=3600)
        w.check_schema=AsyncMock();called=asyncio.Event()
        async def bad():called.set();raise ValueError('private text must never enter heartbeat')
        w.tick=bad;job=asyncio.create_task(w.run());await called.wait();await asyncio.sleep(0)
        assert not job.done() and w.health['status']=='error'
        assert 'private' not in str(w.health)
        w.stopping.set();await job
        assert w.health['status']=='stopped'
    asyncio.run(run())


def test_heartbeat_filesystem_error_does_not_kill_worker():
    w=PlannerReminders(None,None,allowed_owners={'900000000001'},max_lateness_seconds=3600,heartbeat_path='/dev/null/unwritable.json')
    w.heartbeat('running')
    assert w.health['status']=='running'


def test_runner_planner_callback_uses_max_actor_and_bypasses_dialogue():
    from project.llm.bot import Runner
    from types import SimpleNamespace
    async def run():
        api=SimpleNamespace(send=AsyncMock(),answer_callback=AsyncMock())
        dialogue=SimpleNamespace(sessions={},handle=AsyncMock(side_effect=AssertionError('planner callback must not enter old flow')))
        worker=SimpleNamespace(complete_callback=AsyncMock(return_value='done'))
        runner=Runner(api,dialogue,SimpleNamespace(),'test',planner=worker)
        event={'update_type':'message_callback','timestamp':10**15,
               'callback':{'user':{'user_id':900000000001},'payload':'p1d_'+'a'*32+'_1','callback_id':'synthetic-callback'},
               'message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':900000000002},'body':{'text':'old notification'}}}
        await runner.process(event)
        worker.complete_callback.assert_awaited_once_with('900000000001',event['callback']['payload'])
        assert api.send.await_args.args[0]==900000000001
        assert not dialogue.handle.called
    asyncio.run(run())


def test_runner_stop_and_start_update_planner_availability_even_when_delivery_off(monkeypatch):
    from project.llm import bot
    from types import SimpleNamespace
    async def run():
        recorded=AsyncMock();monkeypatch.setattr(bot,'record_bot_availability',recorded)
        pool=object();store=SimpleNamespace(pool=pool,delete_user=AsyncMock())
        dialogue=SimpleNamespace(sessions={},handle=AsyncMock(return_value=[]))
        runner=bot.Runner(SimpleNamespace(),dialogue,store,'test')
        await runner.process({'update_type':'bot_stopped','timestamp':10**15,'user':{'user_id':900000000001}})
        recorded.assert_awaited_with(pool,'900000000001',True,10**15)
        await runner.process({'update_type':'bot_started','timestamp':10**15,'user':{'user_id':900000000001}})
        recorded.assert_awaited_with(pool,'900000000001',False,10**15)
    asyncio.run(run())


def test_ai_off_refuses_before_config_or_network(monkeypatch):
    from project.llm.services import llm
    async def run():
        monkeypatch.setenv('LLM_ENABLED','0')
        monkeypatch.setattr(llm,'_settings',lambda:(_ for _ in ()).throw(AssertionError('must not read credentials or build provider')))
        with pytest.raises(llm.LLMError) as error:await llm.call_llm([{'role':'user','content':'synthetic'}])
        assert error.value.code=='disabled'
    asyncio.run(run())
