"""Test-only probe executed against a separately mounted compatible rollback tree."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import asyncpg
import httpx


async def main():
    root=Path.cwd()
    assert root==Path(os.environ['PLANNER_ROLLBACK_ROOT'])
    assert os.environ['PLANNER_ENABLED']==os.environ['PLANNER_NOTIFICATIONS_ENABLED']=='0'
    assert os.environ['LLM_ENABLED']=='0'
    if (root/'project/llm/services/planner_reminders.py').exists():
        from project.llm.services.planner_reminders import configured_worker
        assert configured_worker(None, None, os.environ) is None, 'Rollback delivery must be disabled'
    schema=os.environ['ROLLBACK_SCHEMA'];assert re.fullmatch(r'k0_[0-9a-f]{32}',schema)
    dsn=os.environ['PLANNER_TEST_DSN']
    address=urlsplit(dsn)
    assert address.scheme=='postgresql'
    assert (address.hostname,address.path) in {('planner-k0-postgres','/planner_k0'),('postgres','/support_router')}, 'Only the disposable local acceptance database is allowed'
    supplied=json.loads(sys.stdin.read())
    from project.llm.services import llm
    assert Path(llm.__file__).resolve().is_relative_to(root)
    llm._settings=lambda:(_ for _ in ()).throw(AssertionError('AI-off must precede config/provider'))
    try:await llm.call_llm([{'role':'user','content':'synthetic rollback probe'}])
    except llm.LLMError as error:assert error.code=='disabled'
    else:raise AssertionError('Rollback AI was not disabled')
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from project.llm.bot import Runner
    from project.llm.services.scenario import ScenarioStore
    from project.miniapp.session_store import SessionStore
    from project.miniapp.favorites import Favorites
    web=importlib.import_module('project.miniapp.app')
    assert Path(web.__file__).resolve().is_relative_to(root)
    pool=await asyncpg.create_pool(dsn,min_size=1,max_size=3,server_settings={'search_path':schema})
    try:
        web.app.state.store=ScenarioStore(pool);web.app.state.shared=SessionStore(pool)
        web.app.state.favorites=Favorites(pool);web.app.state.preview=False
        web.app.state.origin='https://planner.test';web.app.state.sessions={}
        owner=await web.app.state.shared.owner(supplied['headers']['Authorization'].removeprefix('Bearer '))
        runner=Runner(SimpleNamespace(),SimpleNamespace(sessions={}),SimpleNamespace(pool=pool,delete_user=AsyncMock()),'rollback-fixture')
        await runner.process({'update_type':'bot_stopped','timestamp':runner.started_ms-1000,'user':{'user_id':int(owner)}})
        assert await pool.fetchval('SELECT planner_stopped FROM account_epochs WHERE owner=$1',owner), 'Rollback discarded stop during downtime'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url='https://planner.test') as client:
            assert (await client.get('/api/planner/tasks',headers=supplied['headers'])).status_code==404
            assert (await client.delete('/api/account',headers=supplied['headers'])).status_code==200
            assert (await client.delete('/api/account',headers=supplied['headers'])).status_code==401
        print(json.dumps({'deleted_owner_only':True,'rollback_source_imported':True,'AI_disabled':True,'delivery_disabled':True,'lifecycle_preserved':True}))
    finally:await pool.close()


if __name__=='__main__':asyncio.run(main())
