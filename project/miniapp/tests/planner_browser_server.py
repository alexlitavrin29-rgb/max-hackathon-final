"""Local K2 browser harness: real application, real PG, synthetic signed MAX only.

Run only on planner-k0-net with PLANNER_TEST_DSN; unique schema removed on shutdown.
No extra product endpoints, auth bypass or transport. Browser signs its own fixture.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg

from project.llm.services.scenario import ScenarioStore
from project.llm.services.planner_store import apply_migration
from project.miniapp.favorites import Favorites
from project.miniapp.session_store import SessionStore

web = importlib.import_module('project.miniapp.app')


@asynccontextmanager
async def local_lifespan(app):
    assert os.environ['PLANNER_ENABLED'] == '1'
    dsn = os.environ['PLANNER_TEST_DSN']
    assert '@planner-k0-postgres/planner_k0' in dsn
    schema = 'k2_browser_' + uuid4().hex
    control = await asyncpg.connect(dsn)
    await control.execute(f'CREATE SCHEMA {schema}')
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5,
        server_settings={'search_path': schema})
    try:
        scenario = ScenarioStore(pool)
        await scenario.initialize()
        config = json.loads(Path('submission/scenario.json').read_text())['config']
        current = await scenario.read()
        await scenario.publish(await scenario.save(config, current['revision']), 'Synthetic K2 fixture')
        shared, favorites = SessionStore(pool), Favorites(pool)
        await shared.initialize()
        await favorites.initialize()
        await apply_migration(pool)
        for name, value in dict(store=scenario, shared=shared, favorites=favorites,
            preview=False, origin=os.environ['K2_BROWSER_ORIGIN'],
            token='planner-k0-synthetic-only', bot_name='synthetic_local_only',
            published_cache=None, sessions={}, launches={}, capacity_lock=asyncio.Lock(),
            search_slots=asyncio.Semaphore(8), active_searches=0, max_active_searches=0,
            action_metrics=[]).items():
            setattr(app.state, name, value)
        print('K2 isolated schema ready', flush=True)
        yield
    finally:
        await pool.close()
        await control.execute(f'DROP SCHEMA {schema} CASCADE')
        await control.close()


web.app.router.lifespan_context = local_lifespan
app = web.app
