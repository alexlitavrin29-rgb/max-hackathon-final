"""Resolve only published source definitions and safe outbound navigation."""
from urllib.parse import parse_qs, urlparse


def safe_url(url):
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
        return False
    try:
        parsed = urlparse(url)
        return (parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port != 0)
    except ValueError:
        return False


def action_url(url):
    """MAX links may only open the three local search screens."""
    if not safe_url(url):
        return None
    parsed = urlparse(url)
    if parsed.hostname.lower().rstrip('.') in {'max.ru', 'www.max.ru'}:
        query = parse_qs(parsed.query)
        target = query.get('startapp', [None])
        if (len(target) == 1 and target[0] in {'work', 'rental', 'help'}
                and set(query) == {'startapp'}):
            return '/?section=' + target[0]
        return None
    return url


def editorial_actions(config, node):
    """ID references and literal source URLs use the same safety boundary.

    A missing ID is never turned into a URL. A URL only inherits metadata from
    a definition with that exact URL, never from an unrelated source entry.
    """
    definitions = config.get('sources', {})
    if not isinstance(definitions, dict):
        definitions = {}
    result, seen = [], set()
    for reference in [*node.get('sources', []), *node.get('editorial_sources', [])]:
        if not isinstance(reference, str):
            continue
        source = definitions.get(reference)
        if not isinstance(source, dict):
            source = next((s for s in definitions.values()
                           if isinstance(s, dict) and s.get('url') == reference), {})
        raw_url = source.get('url') or reference
        url = action_url(raw_url)
        if not url or url in seen:
            continue
        # Literal URLs without definitions still have an intelligible label.
        # This card explicitly cites article 65 in its prepared source prose.
        if (not source.get('title') and node.get('id') == 'employment_docs'
                and raw_url == 'https://www.consultant.ru/document/cons_doc_LAW_34683/b618fae23b33471d3e7e3e373dd93fcced4356b8/'):
            label = 'ТК РФ, статья 65 — документы при приёме на работу'
        else:
            label = source.get('title') or 'Источник: ' + urlparse(raw_url).hostname
        result.append(dict(label=label, url=url))
        seen.add(url)
    return result
