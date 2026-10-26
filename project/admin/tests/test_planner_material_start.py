"""Direct planner material links never execute the legacy checklist engine."""
import asyncio
import json
from pathlib import Path

import pytest

from project.llm.services.flow import FlowDialogue, PreviewReminders


def published():
    return json.loads(Path('submission/scenario.json').read_text())['config']


def buttons(replies):
    return [button for reply in replies for attachment in reply.get('attachments', [])
            for row in attachment['payload']['buttons'] for button in row]


def test_direct_material_displays_prepared_text_without_executing_checklist(monkeypatch):
    monkeypatch.setenv('PLANNER_ENABLED', '1')

    async def run():
        config = published()
        node = next(n for n in config['nodes'] if n['id'] == 'doc_inn_lookup')
        config['sources']['direct_test'] = {'title': 'ФНС', 'url': 'https://service.nalog.ru/inn.do'}
        node['sources'] = ['direct_test']
        dialogue = FlowDialogue(PreviewReminders(), config)
        session = dialogue.session(7)
        session.tasks = ['Существующая задача']; session.done = {0}
        session.pending = {'stage': 'text'}; session.work = {'query': 'temporary'}
        nonce = session.nonce
        replies = await dialogue.handle(7, payload='route_start:p2_doc_inn_lookup')
        assert replies[0]['text'] == node['text']
        assert replies[0]['format'] == 'markdown'
        assert session.tasks == ['Существующая задача'] and session.done == {0} and session.nonce == nonce
        assert session.pending is None and not session.work
        assert session.node == node['id'] and session.history == ['menu']
        rendered = buttons(replies)
        assert {'type': 'link', 'text': 'ФНС', 'url': 'https://service.nalog.ru/inn.do'} in rendered
        assert {'type': 'open_app', 'text': 'Вернуться к плану',
                'web_app': config['rules']['miniapp_search_bot'], 'payload': 'plan_review'} in rendered
        assert not any(b.get('payload', '').startswith(('task:', 'done:', 'g:')) for b in rendered)
        await dialogue.handle(7, payload='back')
        assert session.node == 'menu'
        replies = await dialogue.handle(7, payload='jump:doc_inn_lookup')
        assert not any(b.get('type') == 'open_app' and b.get('payload') in {'plan', 'plan_review'} for b in buttons(replies))

    asyncio.run(run())


@pytest.mark.parametrize('payload', [
    'p2_unknown', 'p2_documents', 'p2_doc_inn_lookup\n', 'p2_doc_inn_lookup:action',
    'p2_Документы', 'p2_' + 'a' * 51,
])
def test_unknown_or_malformed_material_payload_never_executes(payload):
    async def run():
        dialogue = FlowDialogue(PreviewReminders(), published())
        session = dialogue.session(7); session.node = 'menu'; session.tasks = ['Сохранено']
        replies = await dialogue.handle(7, payload='route_start:' + payload)
        assert 'недоступен' in replies[0]['text']
        assert session.node == 'menu' and session.tasks == ['Сохранено']
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'kind': 'action'}, {'next': 'documents'}, {'content_status': 'draft'},
    {'text': 'Личные сведения: {city}'},
    {'conditions': [{'field': 'age', 'op': 'gte', 'value': 18}]},
])
def test_material_is_revalidated_against_current_publication(change):
    async def run():
        config = published()
        next(n for n in config['nodes'] if n['id'] == 'doc_inn_lookup').update(change)
        dialogue = FlowDialogue(PreviewReminders(), config)
        replies = await dialogue.handle(7, payload='route_start:p2_doc_inn_lookup')
        assert 'недоступен' in replies[0]['text']
    asyncio.run(run())


def test_direct_material_respects_role_and_child_only():
    async def run():
        config = published(); config['rules']['child_only'] = False
        dialogue = FlowDialogue(PreviewReminders(), config)
        session = dialogue.session(7); session.values['role'] = 'parent'
        assert 'недоступен' in (await dialogue.handle(7, payload='route_start:p2_doc_inn_lookup'))[0]['text']
        config['rules']['child_only'] = True
        replies = await dialogue.handle(7, payload='route_start:p2_doc_inn_lookup')
        assert 'недоступен' not in replies[0]['text']
        assert session.values['role'] == 'child'
    asyncio.run(run())


def test_legacy_housing_material_link_opens_current_canonical_answer():
    async def run():
        config = published()
        canonical = next(n for n in config['nodes'] if n['id'] == 'hq_state_housing_apply')
        dialogue = FlowDialogue(PreviewReminders(), config)
        session = dialogue.session(7)
        replies = await dialogue.handle(7, payload='route_start:p2_housing_list')
        assert replies[0]['text'] == canonical['text']
        assert session.node == canonical['id']

        for withdrawn in ('housing_list', canonical['id']):
            updated = published()
            next(n for n in updated['nodes'] if n['id'] == withdrawn)['content_status'] = 'draft'
            replies = await FlowDialogue(PreviewReminders(), updated).handle(
                7, payload='route_start:p2_housing_list')
            assert 'недоступен' in replies[0]['text']

    asyncio.run(run())
