"""DB readiness failures stay bounded and do not expose connection details."""
import asyncio
from types import SimpleNamespace

import asyncpg
import httpx
import pytest

from project.miniapp import app as api


async def request_health():
    transport = httpx.ASGITransport(app=api.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        return await client.get('/api/health')


def install_pool(monkeypatch, fetchval):
    monkeypatch.setattr(api.app.state, 'store',
                        SimpleNamespace(pool=SimpleNamespace(fetchval=fetchval)), raising=False)


def test_health_success_keeps_existing_response(monkeypatch):
    async def fetchval(query):
        assert query == 'SELECT 1'
        return 1

    install_pool(monkeypatch, fetchval)
    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


@pytest.mark.parametrize('error', [
    ConnectionRefusedError('private-db:5432 password=do-not-publish'),
    asyncpg.PostgresConnectionError('private-db:5432 password=do-not-publish'),
    asyncpg.InterfaceError('private-db:5432 password=do-not-publish'),
    TimeoutError('private-db:5432 password=do-not-publish'),
])
def test_health_db_failure_returns_generic_503(monkeypatch, error):
    async def fetchval(query):
        raise error

    install_pool(monkeypatch, fetchval)
    response = asyncio.run(request_health())
    assert response.status_code == 503
    assert response.json() == {'detail': 'Service unavailable'}


def test_health_stalled_db_is_cancelled_and_returns_503(monkeypatch):
    async def run():
        cancelled = asyncio.Event()

        async def fetchval(query):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        install_pool(monkeypatch, fetchval)
        response = await asyncio.wait_for(request_health(), timeout=3)
        assert response.status_code == 503
        assert response.json() == {'detail': 'Service unavailable'}
        assert cancelled.is_set()

    asyncio.run(run())


def test_health_external_cancellation_propagates(monkeypatch):
    async def run():
        started = asyncio.Event()

        async def fetchval(query):
            started.set()
            await asyncio.Event().wait()

        install_pool(monkeypatch, fetchval)
        task = asyncio.create_task(api.health())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
