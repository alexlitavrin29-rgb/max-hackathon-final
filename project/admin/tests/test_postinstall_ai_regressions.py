"""New wording must retain the concrete medical denial goal."""
import asyncio
import json
from pathlib import Path

from project.llm.services import planner_ai


def test_sent_home_by_registration_is_a_denial(monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')

    async def forbidden(*args, **kwargs):
        raise AssertionError('Reviewed medical route should not call the provider')

    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    config = json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']
    for query in (
        'Меня отправили из регистратуры домой: нет пластиковой карточки ОМС. Как действовать?',
        'Не записали к врачу из-за отсутствия бумажного полиса. Куда обратиться?',
    ):
        result = asyncio.run(planner_ai.PlannerAI().propose(
            'synthetic-postinstall', 'find_help_route', query, config=config))
        assert result['context']['material_ids'] == ['care_denial']


def test_referral_is_not_denial():
    from project.llm.services.access_guides import route

    result = route('Меня отправили из поликлиники на обследование, полис ОМС есть')
    assert result is None or result.get('id') != 'care_denial'


def test_policy_mention_does_not_hide_unrecognized_care_problem():
    from project.llm.services.access_guides import route

    result = route('В регистратуре не дали талон к врачу: у меня нет бумажного полиса')
    assert result is None or result.get('id') != 'oms_policy'


def test_moving_region_keeps_transfer_card_chosen_by_model(monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')

    async def selector(*args, **kwargs):
        return json.dumps(dict(status='draft', topic='housing', route_alias='housing',
                               material_ids=['housing_move']))

    monkeypatch.setattr(planner_ai, 'call_llm', selector)
    config = json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']
    for query in (
        'Переезжаю к родственникам в другую область, но стою в очереди на квартиру. Что станет с ней?',
        'Перееду в соседний регион, уже числюсь в списке на жильё. Как сохранить очередь?',
    ):
        result = asyncio.run(planner_ai.PlannerAI().propose(
            'synthetic-moving', 'find_help_route', query, config=config))
        assert result['context']['material_ids'] == ['housing_move']


def test_coordinated_food_and_overnight_need_keeps_shelter(monkeypatch):
    monkeypatch.setenv('PLANNER_AI_ENABLED', '1')
    monkeypatch.setenv('LLM_ENABLED', '1')

    async def forbidden(*args, **kwargs):
        raise AssertionError('Urgent reviewed shelter route should not call the provider')

    monkeypatch.setattr(planner_ai, 'call_llm', forbidden)
    config = json.loads(Path('submission/scenario.json').read_text(encoding='utf-8'))['config']
    for query in (
        'Сегодня негде поесть и переночевать, в городе никого нет. Где искать помощь?',
        'Остался на улице: негде согреться и переночевать сегодня.',
    ):
        result = asyncio.run(planner_ai.PlannerAI().propose(
            'synthetic-overnight', 'find_help_route', query, config=config))
        assert result['context']['material_ids'] == ['shelter']
        assert 'app_help' in result['context']['required_resources']
        assert result['information']['priority'] == 'urgent'
