"""Regressions from real ProxyAPI truncation and contradictory help instructions."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from project.llm.services import llm, planner_ai
from project.llm.tests.test_llm import sdk_transport, completion, close_managed_llm


def test_deepseek_planner_keeps_budget_for_visible_json(monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    selected = dict(status='draft', topic='work', route_alias='work',
                    action_ids=['work_preferences', 'work_skills', 'work_resume'])

    def handler(request):
        body = json.loads(request.content)
        # Catalog selection needs stable sampling rather than creative alternatives.
        assert body.get('temperature') == 0
        # Captured failure: all600 output tokens consumed as reasoning, no content.
        content = json.dumps(selected) if body.get('reasoning_effort') == 'none' else None
        reply = completion(content)
        reply['choices'][0]['finish_reason'] = 'stop' if content else 'length'
        return httpx.Response(200, json=reply)

    sdk_transport(monkeypatch, handler)
    monkeypatch.setattr(llm, '_settings', lambda: llm.LLMSettings('synthetic', 'https://api.proxyapi.ru/v1', 'deepseek/deepseek-v4-flash', 5))
    result = asyncio.run(planner_ai.PlannerAI().propose('synthetic', 'draft_plan', 'Первая работа'))
    assert result['status'] == 'draft'
    assert len(result['tasks']) == 3


@pytest.mark.parametrize('operation', ['find_help_route', 'draft_plan'])
def test_help_response_has_only_fields_requested_for_its_operation(monkeypatch, operation):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')
    config = json.loads(Path('submission/scenario.json').read_text())['config']
    from project.llm.services.planner_content import candidates
    material = next(item for item in candidates(config, 'Первая работа') if item['id'] == 'resume_first')

    async def provider(messages, **kwargs):
        system = messages[0]['content']
        selected = dict(status='draft', topic='work', route_alias='work', material_ids=[material['id']])
        # Reproduce the observed provider following the final action_ids instruction.
        if 'action_ids содержит' in system:
            selected['action_ids'] = ['work_preferences', 'work_skills', 'work_resume']
        return json.dumps(selected)

    monkeypatch.setattr(planner_ai, 'call_llm', provider)
    result = asyncio.run(planner_ai.PlannerAI().propose('synthetic', operation, 'Первая работа', config=config))
    assert result['status'] == 'draft'
    assert result['information']['text']
    if operation == 'find_help_route':
        assert result['tasks'] == []
    else:
        assert result['tasks'][0]['links']
