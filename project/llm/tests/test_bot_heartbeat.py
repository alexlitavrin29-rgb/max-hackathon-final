import asyncio
import json
from types import SimpleNamespace

from project.llm import bot


class Dialogue:
    sessions = {}

    async def refresh(self):
        return SimpleNamespace(config={'reminders_enabled': False})

    async def handle(self, user, text, payload):
        return [{'text': 'Ответ после ошибки heartbeat'}]


def runner(tmp_path, monkeypatch, store, api=None):
    monkeypatch.setattr(bot, 'ROOT', tmp_path)
    (tmp_path / 'project').mkdir()
    return bot.Runner(api, Dialogue(), store, 'synthetic-bot')


def test_health_write_error_does_not_cancel_polling_and_recovers(tmp_path, monkeypatch, caplog):
    async def run():
        failed_cycle, resume, recovered_cycle, sent = (asyncio.Event() for _ in range(4))

        class Store:
            visits = 0

            async def cleanup(self):
                self.visits += 1
                if self.visits == 2:
                    failed_cycle.set()
                    await resume.wait()
                elif self.visits == 3:
                    recovered_cycle.set()
                    await asyncio.Event().wait()

        class Api:
            reply = None
            calls = 0

            async def updates(self, marker=None):
                self.calls += 1
                if self.calls > 1:
                    await asyncio.Event().wait()
                await failed_cycle.wait()
                return {'updates': [
                    {'update_type': 'message_created', 'timestamp': 10**15,
                     'message': {'recipient': {'chat_type': 'dialog'},
                                 'sender': {'user_id': 1},
                                 'body': {'mid': 'after-health-error', 'text': 'Вопрос'}}}],
                        'marker': 1}

            async def send(self, user, reply):
                self.reply = reply
                sent.set()

        api = Api()
        worker = runner(tmp_path, monkeypatch, Store(), api)
        blocked = tmp_path / 'private-filesystem-path'
        blocked.write_text('A file temporarily occupies the logs directory', encoding='utf-8')
        worker.health = blocked / 'bot-health.json'
        async with asyncio.TaskGroup() as group:
            polling = group.create_task(worker.poll())
            reminders = group.create_task(worker.reminders())
            await asyncio.wait_for(sent.wait(), timeout=10)
            assert api.reply == {'text': 'Ответ после ошибки heartbeat'}
            assert not polling.done() and not reminders.done()
            assert 'bot_heartbeat_failed type=' in caplog.text
            assert 'private-filesystem-path' not in caplog.text
            blocked.unlink()
            blocked.mkdir()
            resume.set()
            await asyncio.wait_for(recovered_cycle.wait(), timeout=10)
            data = json.loads(worker.health.read_text(encoding='utf-8'))
            assert data['status'] == 'running' and data['username'] == 'synthetic-bot'
            assert data['updated_at'] >= data['last_poll'] > 0
            assert not worker.health.with_suffix('.tmp').exists()
            polling.cancel()
            reminders.cancel()

    asyncio.run(run())


def test_failed_temporary_write_preserves_previous_health_json(tmp_path, monkeypatch, caplog):
    async def run():
        next_cycle = asyncio.Event()

        class Store:
            visits = 0

            async def cleanup(self):
                self.visits += 1
                if self.visits == 2:
                    next_cycle.set()
                    await asyncio.Event().wait()

        worker = runner(tmp_path, monkeypatch, Store())
        previous = '{"status":"running","updated_at":123,"last_poll":122,"username":"previous"}'
        worker.health.write_text(previous, encoding='utf-8')
        worker.health.with_suffix('.tmp').mkdir()
        task = asyncio.create_task(worker.reminders())
        try:
            await asyncio.wait_for(next_cycle.wait(), timeout=10)
            assert not task.done()
            assert worker.health.read_text(encoding='utf-8') == previous
            assert json.loads(worker.health.read_text(encoding='utf-8'))['last_poll'] == 122
            assert 'bot_heartbeat_failed type=' in caplog.text
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
