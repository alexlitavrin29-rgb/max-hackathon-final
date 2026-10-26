"""Reviewed navigation supplements, separate from the bot publication.

No text/trust promotion: a supplement only applies to the exact reviewed source
body/tasks and only links child-reachable internal targets. Registry contains no
runtime fetch, generated URLs or user values.
"""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from .material_sources import action_url


def fingerprint(node):
    value=json.dumps([node.get('text',''),node.get('tasks',[])],ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache(maxsize=1)
def registry():
    return json.loads(Path(__file__).with_name('handbook_source_links.json').read_text())


def supplemental_actions(config,node,reachable):
    card=registry()['cards'].get(node.get('id'))
    if (not card or card['fingerprint']!=fingerprint(node)
            or node.get('content_status') in {'unverified_reference','draft','archived'}):return []
    result=[]
    for item in card['links']:
        if 'target' in item:
            if item['target'] not in reachable or item['target']==node['id']:continue
            url='/?handbook='+item['target']
        else:
            url=action_url(registry()['sources'][item['source']]['url'])
        if not url:continue
        result.append({k:v for k,v in dict(label=item['label'],url=url,description=item['description'],anchors=item.get('anchors',[]),supplement=True).items() if v is not None})
    return result


def citation_anchors(text, actions):
    """Link citations only to the already published, explicitly named act/article.

    Several sources can concern one law. A bare law number then remains plain,
    while a fully specified article can still link to its precise source.
    """
    laws = {}
    for action in actions:
        for law in re.findall(r'\d+-ФЗ', action['label'], re.I):
            laws.setdefault(law.upper(), set()).add(action['url'])
    result = []
    for action in actions:
        extra = []
        article = re.search(r'ст\.?\s*(\d+)', action['label'], re.I)
        for law in re.findall(r'\d+-ФЗ', action['label'], re.I):
            if article:
                pattern = r'ст(?:атья|атьи|атье|\.)\s*' + article[1] + r'(?!\d)\s+(?:закона\s+)?(?:№\s*)?' + re.escape(law)
                extra.extend(m.group() for m in re.finditer(pattern, text, re.I))
            if len(laws[law.upper()]) == 1:
                extra.extend(m.group() for m in re.finditer(r'(?<!\d)' + re.escape(law) + r'(?!\w)', text, re.I))
        result.append(dict(action, **({'anchors':list(dict.fromkeys([*action.get('anchors',[]), *extra]))[:12]} if extra else {})))
    return result
