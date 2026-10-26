"""Checked local replacements for exact published document answers."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .help_routes import _allowed, _path


def publication_fingerprint(node):
    value = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache(maxsize=1)
def records():
    return json.loads(Path(__file__).with_name('document_review.json').read_text(encoding='utf-8'))['cards']


def reviewed_nodes(config, previous):
    published = {node['id']: node for node in config.get('nodes', [])}
    branches = {branch['id']: branch for branch in config.get('branches', [])}
    values = {'role': 'child'}
    replacements = {}
    for identifier, review in records().items():
        original = published.get(identifier)
        if (not original or original.get('content_status') != 'unverified_reference'
                or publication_fingerprint(original) != review['fingerprint']
                or not _allowed(original, branches, values)
                or not _path(config, identifier, values)):
            continue
        node = deepcopy(original)
        node['text'] = review['text']
        node['adult_text'] = review['text']
        node['content_status'] = 'source_checked_reference'
        node['editorial_sources'] = []
        replacements[identifier] = node
    return [*(node for node in previous if node['id'] not in replacements), *replacements.values()]
