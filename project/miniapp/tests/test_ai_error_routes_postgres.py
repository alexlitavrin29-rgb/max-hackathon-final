"""Malformed provider output must stay a handled failure across signed HTTP routes."""
import httpx
import pytest

from project.llm.config import LLMSettings
from project.llm.services import flow, llm, planner_ai, rental
from project.miniapp.tests.test_planner_postgres import contour, launch, OWNERS, web


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['find-help-route', 'draft-plan'])
async def test_malformed_provider_does_not_escape_signed_ai_route(contour, monkeypatch, route):
    pool, client, _ = contour
    headers = await launch(client, OWNERS[0])
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    monkeypatch.setattr(web.app.state, 'planner_ai', planner_ai.PlannerAI(), raising=False)
    settings = LLMSettings('synthetic', 'https://provider.invalid/v1', 'synthetic', 1)
    service = llm.LLMService(settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': None}]})))
    async def provider(messages, **kwargs):
        return await llm.call_llm(messages, settings=settings, service=service, **kwargs)
    monkeypatch.setattr(planner_ai, 'call_llm', provider)
    try:
        result = await client.post('/api/planner/ai/'+route, headers=headers,
                                   json={'text': 'Хочу восстановить паспорт'})
        assert result.status_code == 200
        assert result.json()['reason'] == 'unavailable'
        assert await pool.fetchval('SELECT count(*) FROM planner_goals') == 0
        assert await pool.fetchval('SELECT count(*) FROM planner_tasks') == 0
        assert await pool.fetchval('SELECT count(*) FROM planner_proposal_policies') == 0
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('section', ['work', 'rental'])
async def test_malformed_provider_keeps_search_http_response_handled(contour, monkeypatch, section):
    _, client, _ = contour
    headers = await launch(client, OWNERS[0])
    # The fixture does not execute application lifespan; initialize its search gates.
    import asyncio
    monkeypatch.setattr(web.app.state, 'search_slots', asyncio.Semaphore(4), raising=False)
    monkeypatch.setattr(web.app.state, 'action_metrics', [], raising=False)
    monkeypatch.setattr(web.app.state, 'active_searches', 0, raising=False)
    monkeypatch.setattr(web.app.state, 'max_active_searches', 0, raising=False)
    assert (await client.post('/api/action', headers=headers, json={'section': section})).status_code == 200
    settings = LLMSettings('synthetic', 'https://provider.invalid/v1', 'synthetic', 1)
    service = llm.LLMService(settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': None}]})))
    calls = []
    async def provider(messages, **kwargs):
        calls.append(True)
        return await llm.call_llm(messages, settings=settings, service=service, **kwargs)
    monkeypatch.setattr(flow, 'call_llm', provider)
    monkeypatch.setattr(rental, 'call_llm', provider)
    try:
        await asyncio.sleep(.45)
        result = await client.post('/api/action', headers=headers, json={'text': 'Помоги разобраться'})
        assert calls
        assert result.status_code == 200
        assert result.json()['cards'] == []
        assert result.json()['notices']
    finally:
        await service.close()
