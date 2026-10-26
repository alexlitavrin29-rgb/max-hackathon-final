"""Server-owned K3 navigation catalog; never execute arbitrary node IDs or actions."""
import re
from collections import deque
from uuid import UUID

ROUTES = {
    'housing': {'node_id': 'state_housing', 'title': 'Квартира от государства'},
    'benefits': {'node_id': 'benefits', 'title': 'Льготы и выплаты'},
    'payments': {'node_id': 'bq_group_payments', 'title': 'Выплаты и пенсии'},
    'legal': {'node_id': 'bq_legal', 'title': 'Бесплатная юридическая помощь'},
    'documents': {'node_id': 'documents', 'title': 'Документы'},
    'study': {'node_id': 'education', 'title': 'Учёба'},
    'work': {'node_id': 'work_entry', 'title': 'Ищу работу'},
    'rent': {'node_id': 'rent', 'title': 'Как снять жильё'},
    'help': {'node_id': 'help_points', 'title': 'Где могут помочь'},
}


def parse_payload(payload):
    if not isinstance(payload, str) or len(payload) > 64 or not payload.isascii():
        return None
    alias = payload[3:] if payload.startswith('r1_') else ''
    return alias if alias in ROUTES and payload == 'r1_' + alias else None


def bot_name(config):
    value = config.get('rules', {}).get('miniapp_search_bot')
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', value) else None


def app_url(config, payload='plan'):
    bot = bot_name(config)
    if not bot or not (payload == 'plan' or re.fullmatch(r'task_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', payload)):
        return None
    return f'https://max.ru/{bot}?startapp={payload}'


def task_payload(identifier):
    return 'task_' + str(UUID(str(identifier)))


def _conditions(rules, values):
    from .flow import matches
    return all(r.get('op') in {'known','lt','lte','gt','gte','contains','eq','ne'} and matches(r, values) for r in rules)


def _allowed(node, branches, values):
    branch = branches.get(node.get('branch'), {})
    return (values.get('role') in branch.get('roles', [])
            and _conditions(node.get('conditions', []), values))


def _path(config, target, values):
    """Read-only path through visible callback navigation in this publication."""
    nodes = {n['id']: n for n in config['nodes']}
    branches = {b['id']: b for b in config['branches']}
    queue = deque([(config['menu'], ['Главное меню'])]);seen = set()
    while queue:
        identifier, labels = queue.popleft()
        if identifier in seen:
            continue
        seen.add(identifier)
        node = nodes.get(identifier)
        if not node or not _allowed(node, branches, values):
            continue
        if identifier == target:
            return ' → '.join(labels)
        for button in node.get('buttons', []):
            # A manual path must not change the role or execute actions on the way.
            changes = button.get('values', {})
            child = nodes.get(button.get('target'))
            if (not child or button.get('url') or child.get('kind') != 'message'
                    or child.get('routes') or child.get('next')
                    or any(k != 'navigation' for k in changes)
                    or not _conditions(button.get('conditions', []), values)):
                continue
            queue.append((child['id'], labels + [button['label']]))
    return None


def resolve_route(config, alias, values):
    entry = ROUTES.get(alias)
    if not entry:
        return None
    nodes = {n['id']: n for n in config['nodes']}
    branches = {b['id']: b for b in config['branches']}
    node = nodes.get(entry['node_id'])
    path = _path(config, entry['node_id'], values)
    available = bool(node and node.get('kind') == 'message' and not node.get('routes')
                     and not node.get('next') and not node.get('tasks')
                     and _allowed(node, branches, values) and path)
    bot = bot_name(config)
    return dict(alias=alias, **entry, available=available, payload='r1_' + alias,
                url=f'https://max.ru/{bot}?start=r1_{alias}' if available and bot else None,
                manual_path=path or 'Главное меню')


def catalog(config, values=None):
    return [resolve_route(config, alias, values or {'role': 'child'}) for alias in ROUTES]


def plan_button(config):
    bot = bot_name(config)
    return dict(type='open_app', text='Мой план', web_app=bot, payload='plan') if bot else None
