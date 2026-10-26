"""Synthetic candidate probe. Preflight is offline; live needs an explicit code hash.

Only this process's environment supplies the provider key. No app, store, MAX
transport, database or production API is initialized or called.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sys
import time
from uuid import uuid4

HERE = Path(__file__).resolve().parent
MAX_ATTEMPTS = 20
CAP_MICRORUB = 5_000_000
MAX_OUTPUT = 600
PRICE = dict(input=20, output=40, cached=4,
             unit='RUB per million tokens', checked_date='2026-10-26',
             source='https://proxyapi.ru/models/deepseek/deepseek-v4-flash')
_SECRET = None
CASES = [
    dict(id='REGRESSION', query='Как не потерять заработанные деньги?',
         ids=['money_budget'], forbidden_ids=['income_start'], forbidden_resources=['app_work']),
    dict(id='EARN', query='Как мне заработать денег?',
         ids=['income_start'], resources=['app_work'], plan=True),
    dict(id='TWO_NEEDS', query='Нужны деньги и жильё',
         ids=['income_start', 'housing_start'], resources=['app_work', 'app_rental'], plan=True),
    dict(id='WORK_DIALOGUE', query='Проблема с работой', clarify='work_kind',
         answer='Ищу первую работу, опыта нет, резюме ещё не делал',
         resources_after=['app_work'], plan=True),
    dict(id='WAGES', query='Не ищу работу, хочу взыскать невыплаченную зарплату',
         forbidden_resources=['app_work']),
    dict(id='PASSPORT', query='Потерял паспорт, куда обращаться?', ids=['doc_passport_q3']),
    dict(id='BUDGET', query='Получаю деньги нерегулярно, как разложить траты на четыре недели?',
         ids=['money_budget'], forbidden_resources=['app_work']),
    dict(id='BUDGET_INCOME', query='Получаю доход нерегулярно, как распланировать расходы?',
         ids=['money_budget'], forbidden_resources=['app_work']),
    dict(id='ONGOING_INCOME', query='Уже стал зарабатывать деньги, как их распределить?',
         ids=['money_budget'], forbidden_resources=['app_work']),
    dict(id='EXPECTED_INCOME', query='В пятницу получу деньги, как их распределить?',
         ids=['money_budget'], forbidden_resources=['app_work']),
    dict(id='NEGATED_INCOME', query='Не хочу заработать деньги, хочу сохранить накопленное.',
         ids=['money_budget'], forbidden_resources=['app_work']),
    dict(id='PAYMENT', query='Выплата уже назначена, а на счёте пусто'),
    dict(id='HOUSING_LIST', query='Снимать не собираюсь, хочу проверить, есть ли я в списке на жильё',
         forbidden_resources=['app_rental']),
    dict(id='RENTAL', query='Ищу комнату в аренду, хочу посмотреть варианты и проверить договор.', resources=['app_rental']),
    dict(id='STUDY', query='После отчисления хочу восстановиться на прежней специальности'),
    dict(id='HEALTH_ADMIN', query='Переехал и хочу сменить поликлинику для приёма по ОМС'),
    dict(id='LEGAL', query='Где получить бесплатную юридическую помощь?'),
    dict(id='TWO_GOALS', query='Ребёнок родился, а паспорт я потерял: что делать сначала?',
         goal_groups={'benefits': 'childbirth_allowance', 'documents': 'doc_passport_q3'}, plan=True),
    dict(id='URGENT', query='Газом не пахнет, но с потолка льётся и заливает комнату',
         ids=['hq_household_water'], offline=True),
    dict(id='UNSUPPORTED', query='Подбери дозу антибиотика при пневмонии', unsupported=True),
]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def append(path, row):
    serialized = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
    if _SECRET and _SECRET in serialized:
        raise BudgetStop('secret_in_evidence_blocked')
    with path.open('a', encoding='utf-8') as stream:
        stream.write(serialized + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line] if path.exists() else []


def source_manifest(root, config_bytes):
    paths = sorted(p for p in (root / 'project/llm').rglob('*')
                   if p.is_file() and p.suffix in {'.py', '.json'} and '__pycache__' not in p.parts)
    hashes = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in paths}
    return dict(runtime=hashes, scenario_sha256=digest(config_bytes),
                probe_sha256=digest(Path(__file__).read_bytes()),
                cases_sha256=digest(json.dumps(CASES, ensure_ascii=False).encode()))


class BudgetStop(Exception):
    pass


class PromptCaptured(Exception):
    pass


def accounting(rows):
    attempts = {r['attempt_id']: r for r in rows if r['event'] == 'reserved'}
    outcomes = {r['attempt_id']: r for r in rows if r['event'] == 'resolved'}
    conservative = {r['attempt_id'] for r in rows if r['event'] == 'operator_reserved_as_spent'
                    and r['attempt_id'] in attempts
                    and r['reserved_microrub'] == attempts[r['attempt_id']]['reserved_microrub']
                    and r['limits'] == dict(attempts=MAX_ATTEMPTS, rub=5)}
    unresolved = [key for key in attempts if (key not in outcomes or not outcomes[key]['usage_known'])
                  and key not in conservative]
    charged = sum(outcomes[key]['charged_microrub'] if key in outcomes and outcomes[key]['usage_known']
                  else value['reserved_microrub'] for key, value in attempts.items())
    return len(attempts), charged, unresolved


def usage_charge(usage):
    if usage is None:
        raise BudgetStop('missing_usage')
    prompt, completion = usage.prompt_tokens, usage.completion_tokens
    if type(prompt) is not int or type(completion) is not int or min(prompt, completion) < 0:
        raise BudgetStop('invalid_usage')
    cached = getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None) or 0
    if type(cached) is not int or not 0 <= cached <= prompt:
        raise BudgetStop('invalid_cached_usage')
    charge = (prompt - cached) * PRICE['input'] + cached * PRICE['cached'] + completion * PRICE['output']
    return dict(prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached), charge


def guard_budget(rows, reserve):
    count, spent, unresolved = accounting(rows)
    if unresolved or count >= MAX_ATTEMPTS or spent + reserve > CAP_MICRORUB:
        raise BudgetStop('attempt_or_ruble_limit')


def assess(case, result, *, after=False, plan=False, retained_ids=()):
    errors = []
    context = result.get('context') or {}
    ids = set(context.get('material_ids', []))
    resources = set(context.get('required_resources', []))
    if result.get('reason') == 'unavailable' or result.get('status') == 'unavailable':
        errors.append('unavailable')
    if case.get('unsupported'):
        from project.llm.services.planner_content import NO_ANSWER
        refusal = dict(status='information', reason='no_material', tasks=[],
                       information=dict(text=NO_ANSWER, intro=NO_ANSWER, sources=[],
                                        points=[], details=[], priority='normal'))
        no_content = ({key: value for key, value in result.items() if key != 'context'} == refusal
                      and set(context) <= {'question', 'material_ids', 'required_resources'}
                      and not ids and not resources)
        if result != dict(status='unsupported') and not no_content:
            errors.append('medical_request_not_rejected')
        return errors
    if not after and case.get('clarify'):
        if result.get('status') != 'clarify' or result.get('question_id') != case['clarify']:
            errors.append('expected_clarification_not_offered')
        return errors
    if not set(case.get('ids', [])) <= ids:
        errors.append('required_material_missing')
    if case.get('goal_groups'):
        if not plan:
            ids.update(retained_ids)
        offered = {item['id'] for item in result.get('related_topics', []) + result.get('choices', [])}
        if any(mid not in ids and (plan or topic not in offered)
               for topic, mid in case['goal_groups'].items()):
            errors.append('independent_goal_lost')
        if plan and not set(case['goal_groups'].values()) <= {
                mid for task in result.get('tasks', []) for mid in task.get('material_ids', [])}:
            errors.append('independent_goal_missing_from_plan')
    if ids.intersection(case.get('forbidden_ids', [])):
        errors.append('incidental_earned_money_became_income_goal')
    required = case.get('resources_after', []) if after else case.get('resources', [])
    if not set(required) <= resources:
        errors.append('required_search_missing')
    if resources.intersection(case.get('forbidden_resources', [])):
        errors.append('unrequested_search')
    if result.get('status') in {'draft', 'information'}:
        info = result.get('information') or {}
        if not info.get('sources'):
            errors.append('answer_without_sources')
    elif result.get('status') != 'clarify':
        errors.append('supported_question_without_answer')
    if plan and not result.get('tasks'):
        errors.append('empty_plan')
    if after and result.get('status') == 'clarify':
        errors.append('clarification_repeated')
    return errors


async def main(args):
    global _SECRET
    root = Path(args.source).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    config_bytes = Path(args.config).read_bytes()
    config = json.loads(config_bytes)
    config = config.get('config', config)
    manifest = source_manifest(root, config_bytes)
    manifest_hash = digest(json.dumps(manifest, sort_keys=True).encode())
    if args.mode == 'run' and args.expected_manifest != manifest_hash:
        raise SystemExit('Candidate manifest differs from the explicitly frozen hash')
    (out / 'SOURCE-MANIFEST.json').write_text(json.dumps(dict(sha256=manifest_hash, **manifest), indent=2), encoding='utf-8')
    (out / 'PLAN.json').write_text(json.dumps(dict(cases=CASES, max_underlying_attempts=MAX_ATTEMPTS,
        cap_rub=5, max_output_tokens=MAX_OUTPUT, price=PRICE, source_manifest_sha256=manifest_hash,
        unknown_cost_policy='reserve full upper bound and stop; no automatic retry'), ensure_ascii=False, indent=2), encoding='utf-8')
    sys.path.insert(0, str(root))
    from project.llm.services import planner_ai, llm
    os.environ['PLANNER_AI_ENABLED'] = os.environ['LLM_ENABLED'] = '1'
    logging.getLogger('openai').setLevel(logging.CRITICAL)
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    ledger = out / 'ATTEMPTS.jsonl'
    results = out / ('RESULTS.jsonl' if args.mode == 'run' else 'PREFLIGHT.jsonl')
    if args.mode == 'preflight' and results.exists():
        raise SystemExit('Use a fresh evidence directory for another preflight')
    service = None
    settings = None
    completed = {(row['manifest_sha256'], row['case_id'], row['step']): row for row in read_rows(results)}
    active = {}
    if args.mode == 'run':
        from project.llm.config import LLMSettings
        settings = LLMSettings.from_env(env_file=out / 'NO_ENV_FILE')
        if settings.base_url.rstrip('/') != 'https://api.proxyapi.ru/v1' or settings.model != 'deepseek/deepseek-v4-flash':
            raise SystemExit('Provider/model differs from the approved live plan')
        _SECRET = settings.api_key
        prior_rows = read_rows(ledger)
        if args.account_full_unknown_reserve:
            if not ledger.exists() or digest(ledger.read_bytes()) != args.prior_ledger_sha256:
                raise SystemExit('Manual budget decision does not match the exact prior ledger')
            reservations = {r['attempt_id']: r for r in prior_rows if r['event'] == 'reserved'}
            for attempt_id in accounting(prior_rows)[2]:
                append(ledger, dict(event='operator_reserved_as_spent', attempt_id=attempt_id,
                    reserved_microrub=reservations[attempt_id]['reserved_microrub'],
                    limits=dict(attempts=MAX_ATTEMPTS, rub=5), created_utc=int(time.time()),
                    reason='Explicit operator decision: retain full unknown reserve as spent within the same approval'))
        if accounting(read_rows(ledger))[2]:
            raise SystemExit('Unresolved/unknown cost in the shared ledger; manual reconciliation required')
        service = llm.LLMService(settings)
        real_create = service.client.chat.completions.create

        async def budgeted_create(**options):
            if source_manifest(root, Path(args.config).read_bytes()) != manifest:
                raise BudgetStop('candidate_changed_during_probe')
            if options.get('max_tokens') != MAX_OUTPUT or options.get('reasoning_effort') != 'none':
                raise BudgetStop('unexpected_provider_options')
            messages = options['messages']
            # A UTF-8 byte upper bound avoids relying on a cached tokenizer.
            input_upper = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 512
            reserve = input_upper * PRICE['input'] + MAX_OUTPUT * PRICE['output']
            guard_budget(read_rows(ledger), reserve)
            attempt_id = uuid4().hex
            started = time.monotonic()
            append(ledger, dict(event='reserved', attempt_id=attempt_id, case_id=active['case_id'],
                step=active['step'], manifest_sha256=manifest_hash, created_utc=int(time.time()),
                prompt_sha256=digest(json.dumps(messages, ensure_ascii=False).encode()),
                input_token_upper=input_upper, max_output_tokens=MAX_OUTPUT, reserved_microrub=reserve))
            try:
                response = await real_create(**options)
                usage, charge = usage_charge(response.usage)
                if charge > reserve or usage['completion_tokens'] > MAX_OUTPUT:
                    raise BudgetStop('usage_exceeds_reservation')
            except Exception as error:
                append(ledger, dict(event='resolved', attempt_id=attempt_id, usage_known=False,
                    reserved_microrub=reserve, error_type=type(error).__name__,
                    elapsed_ms=round((time.monotonic() - started) * 1000)))
                # Do not let the runtime retry a request whose charge is unknown.
                raise BudgetStop('unknown_provider_cost') from None
            append(ledger, dict(event='resolved', attempt_id=attempt_id, usage_known=True,
                usage=usage, charged_microrub=charge, elapsed_ms=round((time.monotonic() - started) * 1000),
                finish_reason=response.choices[0].finish_reason if response.choices else None,
                provider_reply=response.choices[0].message.content if response.choices else None))
            return response

        service.client.chat.completions.create = budgeted_create

    async def boundary(messages, **options):
        if args.mode == 'preflight':
            active['prompt_bytes'] = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
            active['prompt_sha256'] = digest(json.dumps(messages, ensure_ascii=False).encode())
            raise PromptCaptured()
        return await llm.call_llm(messages, settings=settings, service=service, **options)

    planner_ai.call_llm = boundary
    if args.mode == 'preflight':
        def forbidden(*a, **k):
            raise RuntimeError('Network forbidden in offline preflight')
        socket.socket.connect = forbidden
        socket.create_connection = forbidden

    async def step(case, number, operation='find_help_route', **kwargs):
        key = (manifest_hash, case['id'], number)
        if key in completed:
            return completed[key]['result']
        active.clear()
        active.update(case_id=case['id'], step=number)
        before = accounting(read_rows(ledger))[0]
        started = time.monotonic()
        captured = False
        try:
            result = await planner_ai.PlannerAI().propose('acceptance-synthetic-' + uuid4().hex,
                operation, case['query'], config=config, **kwargs)
        except PromptCaptured:
            captured, result = True, None
        calls = accounting(read_rows(ledger))[0] - before
        errors = assess(case, result, after=number > 1, plan=operation == 'draft_plan',
                        retained_ids=kwargs.get('selected_material_ids', [])) if result else []
        if case.get('offline') and calls:
            errors.append('urgent_route_called_provider')
        row = dict(manifest_sha256=manifest_hash, case_id=case['id'], step=number,
            query=case['query'], operation=operation, request_context=kwargs,
            paid_attempts=calls, prompt_captured=captured, elapsed_ms=round((time.monotonic() - started) * 1000),
            errors=errors, result=result, **{k: active[k] for k in ['prompt_bytes', 'prompt_sha256'] if k in active})
        append(results, row)
        completed[key] = row
        if errors and args.mode == 'run':
            raise BudgetStop('semantic_failure')
        return result

    stopped = None
    try:
        for case in CASES:
            first = await step(case, 1)
            previous, history, plan_step = first, [], 2
            if case.get('clarify'):
                if not first or first.get('status') != 'clarify' or first.get('question_id') != case['clarify']:
                    continue
                history = [dict(question_id=first['question_id'], answer=case['answer'])]
                previous = await step(case, 2, question_id=first['question_id'], answer=case['answer'], history=history)
                plan_step = 3
            ids = list(((previous or {}).get('context') or {}).get('material_ids', []))
            for topic, material_id in case.get('goal_groups', {}).items():
                if material_id in ids:
                    continue
                offered = (previous or {}).get('related_topics', []) + (previous or {}).get('choices', [])
                if topic not in {item['id'] for item in offered}:
                    continue
                previous = await step(case, plan_step, question_id='topic_choice', answer=topic,
                                      selected_material_ids=ids)
                plan_step += 1
                ids = list(dict.fromkeys([*ids, *((previous or {}).get('context') or {}).get('material_ids', [])]))
            if case.get('plan') and ids and previous.get('status') in {'draft', 'information'}:
                await step(case, plan_step, operation='draft_plan', selected_material_ids=ids, history=history)
    except BudgetStop as error:
        stopped = str(error)
    finally:
        if service:
            await service.close()
    attempts, charge, unresolved = accounting(read_rows(ledger))
    resolved = {r['attempt_id']: r for r in read_rows(ledger) if r['event'] == 'resolved'}
    unknown_count = sum(not r['usage_known'] for r in resolved.values())
    known_charge = sum(r['charged_microrub'] for r in resolved.values() if r['usage_known'])
    all_rows = read_rows(results)
    rows = [row for row in all_rows if row['manifest_sha256'] == manifest_hash]
    summary = dict(mode=args.mode, manifest_sha256=manifest_hash, max_attempts=MAX_ATTEMPTS,
        cap_rub=5, attempts=attempts, charged_or_reserved_rub=charge / 1_000_000,
        known_usage_cost_rub=known_charge / 1_000_000,
        unknown_or_conservative_reserve_rub=(charge - known_charge) / 1_000_000,
        unknown_cost_attempts=unknown_count, unreconciled_cost_attempts=len(unresolved),
        stopped=stopped, app_steps=len(rows),
        historical_app_steps=len(all_rows) - len(rows),
        historical_failed_steps=sum(bool(row['errors']) for row in all_rows
                                    if row['manifest_sha256'] != manifest_hash),
        failed_steps=[dict(case_id=r['case_id'], step=r['step'], errors=r['errors']) for r in rows if r['errors']],
        provider_called=args.mode == 'run' and attempts > 0,
        production_db_connections=0, production_test_writes=0, max_messages_sent=0)
    (out / ('SUMMARY.json' if args.mode == 'run' else 'PREFLIGHT-SUMMARY.json')).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['preflight', 'run'], default='preflight')
    parser.add_argument('--source', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', default=str(HERE / 'evidence'))
    parser.add_argument('--expected-manifest')
    parser.add_argument('--account-full-unknown-reserve', action='store_true')
    parser.add_argument('--prior-ledger-sha256')
    args = parser.parse_args()
    lock = None
    if args.mode == 'run':
        folder = Path(args.out).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        lock = folder / 'RUNNING.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit('Another live process/unfinished run holds the budget lock') from None
        os.close(descriptor)
    try:
        asyncio.run(main(args))
    finally:
        if lock:
            lock.unlink()
