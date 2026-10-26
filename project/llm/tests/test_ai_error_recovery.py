"""Regressions for the six failures audited on 2026-10-20; no real provider."""
import asyncio
from copy import deepcopy
import json
import logging
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from project.llm.config import LLMConfigurationError, LLMSettings
from project.llm.services import llm, planner_ai, rental
from project.llm.services.flow import FlowSession


PRIVATE = 'synthetic-private-never-log'


@pytest.mark.parametrize('failure,code,status', [
    (TimeoutError(PRIVATE), 'timeout', None),
    (json.JSONDecodeError(PRIVATE, PRIVATE, 0), 'invalid_json', None),
    (ValueError(PRIVATE), 'invalid_response', None),
    (LLMConfigurationError(PRIVATE), 'configuration', None),
    (llm.LLMError('balance', 402), 'balance', 402),
    (llm.LLMError(PRIVATE), 'invalid_response', None),
])
def test_planner_failure_records_safe_cause_and_releases_slot(monkeypatch, caplog, failure, code, status):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    async def provider(*args, **kwargs):
        raise failure
    monkeypatch.setattr(planner_ai, 'call_llm', provider)
    service = planner_ai.PlannerAI()
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(service.propose(PRIVATE, 'find_help_route', PRIVATE))
    assert result['reason'] == 'unavailable'
    assert service.active == 0
    records = [r for r in caplog.records if getattr(r, 'event', None) == 'planner_ai_failure']
    assert len(records) == 1
    record = records[0]
    assert record.error_code == code and record.http_status == status
    assert record.operation == 'find_help_route'
    assert record.duration_ms >= 0
    assert UUID(record.request_id).version == 4
    assert code in record.getMessage() and record.request_id in record.getMessage()
    assert PRIVATE not in str(record.__dict__) + str(result)


@pytest.mark.parametrize('payload', [
    {'choices': [{'message': None}]},
    {'choices': [None]},
    {'choices': 'bad'},
    {'choices': [{'message': {'content': 42}}]},
    {'choices': [{'message': {'content': ['private-response']}}]},
    {'choices': [{'message': {'content': '{}'}}], 'usage': 'private-response'},
])
@pytest.mark.parametrize('with_metrics', [False, True])
def test_malformed_provider_response_is_normalized(payload, with_metrics, caplog):
    async def run():
        # This test checks response shape; the strict 10ms deadline is tested separately.
        settings = LLMSettings('synthetic', 'https://provider.invalid/v1', 'synthetic', 10)
        service = llm.LLMService(settings, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)))
        try:
            with pytest.raises(llm.LLMError) as caught:
                await service.complete([{'role': 'user', 'content': PRIVATE}],
                                       metrics={} if with_metrics else None)
            assert caught.value.code == 'invalid_response'
            assert PRIVATE not in str(caught.value) + caplog.text
            assert 'private-response' not in caplog.text
        finally:
            await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure,code', [
    (llm.LLMError('timeout'), 'timeout'),
    (llm.LLMError('balance', 402), 'balance'),
    (llm.LLMError('rate_limit', 429), 'rate_limit'),
    (llm.LLMError('connection'), 'connection'),
    (TimeoutError(), 'timeout'),
    (LLMConfigurationError(PRIVATE), 'configuration'),
    (json.JSONDecodeError('broken', '{', 0), 'invalid_json'),
])
def test_rental_preserves_error_category_and_existing_conditions(monkeypatch, failure, code):
    async def provider(*args, **kwargs):
        raise failure
    monkeypatch.setattr(rental, 'call_llm', provider)
    branch = rental.RentalBranch(SimpleNamespace(branches={'rental': {'prompt': ''}}))
    session = FlowSession()
    branch.state(session).update(city={'name': 'Томск'}, budget=30000, other='с кошкой')
    before = deepcopy(session.rental)
    with pytest.raises(llm.LLMError) as caught:
        asyncio.run(branch.input(1, session, 'Помоги разобраться'))
    assert caught.value.code == code
    assert session.rental == before
    assert PRIVATE not in str(caught.value)
