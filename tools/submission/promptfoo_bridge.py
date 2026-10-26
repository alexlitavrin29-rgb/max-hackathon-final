"""Replay ONLY: no import of an LLM/provider; budget belongs to serial pilot."""
import json
from pathlib import Path


def call_api(prompt, options, context):
    data = json.loads(Path(options['config']['snapshot']).read_text())
    key = context['vars']['case_id']
    row = next((r for r in data['rows'] if r['id'] == key), None)
    if row is None:
        return {'error': 'Snapshot case absent'}
    return {'output': json.dumps(row, ensure_ascii=False)}


def get_assert(output, context):
    row = json.loads(output) if isinstance(output, str) else output
    checks = row.get('checks', {})
    failed = [key for key, value in checks.items() if value.get('pass') is False]
    passed = any(v.get('pass') is True for v in checks.values()) and not failed
    return {'pass': passed, 'score': 1 if passed else 0,
            'reason': ' / '.join(failed) if failed else ('Checks passed; inspect origin, not model accuracy' if passed else 'No checks')}


def judge_assert(output, context):
    row = json.loads(output) if isinstance(output, str) else output
    judge = row.get('judge') or {'status': 'NOT_RUN'}
    verdict = judge.get('verdict', {})
    good = all(verdict.get(k) is True for k in ('concrete', 'relevant', 'conditions_preserved', 'brief'))
    return {'pass': good, 'score': int(good), 'reason': judge['status'] + ': ' + verdict.get('reason', '')}
