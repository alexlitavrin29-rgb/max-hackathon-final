"""Conservative, persistent paid-batch reservations; no production configuration changes."""
import fcntl
import json
import math
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

PRICING = {'model': 'deepseek/deepseek-v4-flash', 'host': 'api.proxyapi.ru',
           'input_rub_per_million': 20, 'output_rub_per_million': 40,
           'source': 'https://proxyapi.ru/models/deepseek/deepseek-v4-flash', 'checked': '2026-10-19'}


class BudgetExceeded(ValueError):
    pass


def pricing_supported(base_url, model):
    u = urlsplit(base_url)
    return u.scheme == 'https' and u.hostname == PRICING['host'] and model == PRICING['model'] and not u.username and not u.password


def quote(messages, max_tokens):
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 1200:
        raise ValueError('Unbounded output')
    # One UTF-8 byte/token + large framing allowance. Deliberately pessimistic,
    # not an exact tokenizer. Both transport attempts reserved, never refunded.
    upper = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 4096
    return {'input_token_upper': upper, 'output_token_upper': max_tokens,
            'attempts_reserved': 2, 'rub': (upper * 20 + max_tokens * 40) * 2 / 1_000_000}


class Budget:
    def __init__(self, path, limit_rub=50):
        if not math.isfinite(limit_rub) or not 0 < limit_rub <= 50:
            raise ValueError('Budget must be positive and at most 50 RUB')
        self.path, self.limit = Path(path), limit_rub
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.spent = 0
        self._change(None)

    def _change(self, reservation):
        # The ledger persists between processes/restarts. Do not refund failed or
        # timed-out requests, whose billing may not be observable.
        with self.path.with_suffix('.lock').open('a+', encoding='utf-8') as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {'limit_rub': self.limit, 'pricing': PRICING, 'reservations': []}
            if data['pricing'] != PRICING or data['limit_rub'] != self.limit:
                raise ValueError('Existing ledger pricing/budget mismatch')
            if any(not isinstance(x.get('rub'), (int, float)) or not math.isfinite(x['rub']) or x['rub'] < 0 for x in data['reservations']):
                raise ValueError('Invalid ledger reservation')
            self.spent = sum(x['rub'] for x in data['reservations'])
            if reservation:
                if self.spent + reservation['rub'] > self.limit:
                    raise BudgetExceeded('Paid batch reservation limit reached')
                data['reservations'].append(reservation)
                self.spent += reservation['rub']
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False, encoding='utf-8') as stream:
                    temporary = Path(stream.name)
                    json.dump(data, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()

    def reserve(self, messages, max_tokens, purpose):
        item = dict(quote(messages, max_tokens), purpose=purpose)
        self._change(item)
        return item
