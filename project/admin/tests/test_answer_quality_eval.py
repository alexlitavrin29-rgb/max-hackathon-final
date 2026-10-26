"""Evaluator denominators must not confuse an ideal selection with model output."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('answer_quality_eval', ROOT / 'tools/submission/answer_quality_eval.py')
eval_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_module)


def test_frozen_corpus_diversity_and_integrity():
    import hashlib
    corpus = json.loads(eval_module.CORPUS.read_text())
    assert hashlib.sha256(eval_module.CORPUS.read_bytes()).hexdigest() == eval_module.FROZEN_SHA256
    assert 50 <= len(corpus['cases']) <= 100
    assert len({c['id'] for c in corpus['cases']}) == len(corpus['cases'])
    assert {'urgent', 'clarify', 'unsupported', 'answer'} == {c['intent'] for c in corpus['cases']}
    assert any(len(c['expected_material_groups']) > 1 for c in corpus['cases'])
    assert any('typo' in c['tags'] for c in corpus['cases'])
    for case in corpus['cases']:
        assert not set(case['required_resources']) & set(case['forbidden_resources'])
        assert set(case['ideal_material_ids']) == {s['material_id'] for s in case['expected_sources']}


def test_multitopic_retrieval_requires_all_groups():
    case = {'expected_material_groups': [['one', 'alternative'], ['two']], 'forbidden_material_ids': ['bad']}
    score = eval_module.retrieval_measure(case, ['alternative', 'bad', 'two'])
    assert score['group_ranks'] == [1, 3]
    assert not score['all_groups_at']['1']
    assert score['all_groups_at']['3']
    assert score['forbidden_materials_present'] == ['bad']


def test_no_oracle_means_no_retrieval_denominator():
    score = eval_module.retrieval_measure({'expected_material_groups': [], 'forbidden_material_ids': []}, ['unrelated'])
    assert not score['eligible']
    assert eval_module.ratio(0, 0)['rate'] is None


def test_source_qualifier_negation_and_global_budget():
    case = {'expected_sources': [{'urls': ['/?handbook=x'], 'qualifier_units': ['не гарантирует зачисление'], 'first_action_units': ['Проверь правила.']}]}
    info = {'text': 'Проверь правила. Квота гарантирует зачисление.', 'sources': [], 'points': [
        {'text': 'Квота гарантирует зачисление.'}, {'text': 'Проверь правила.'}]}
    score = eval_module.assembly_measure(case, info, {'x': 'Квота не гарантирует зачисление. Проверь правила.'})
    assert score['qualifier_units_preserved'] == 0
    assert score['expected_urls_present'] == 0
    assert not score['global_point_budget_ok']
    assert not score['source_passage_fidelity_ok']


def test_full_details_preserve_qualifier_and_detect_six_points():
    case = {'expected_sources': [{'urls': ['/?handbook=x'], 'qualifier_units': ['Только после проверки.'], 'first_action_units': []}]}
    info = {'text': 'Действуй.', 'sources': [{'url': '/?handbook=x'}], 'points': [{'text': 'Действуй.'}] * 6,
            'details': [{'text': 'Только после проверки.'}]}
    score = eval_module.assembly_measure(case, info, {'x': 'Действуй. Только после проверки.'})
    assert score['qualifier_units_preserved'] == 1
    assert score['source_passage_fidelity_ok']
    assert not score['global_point_budget_ok']


def test_summary_separates_retrieval_and_ideal_assembly():
    result = eval_module.summarize([])
    assert result['retrieval']['all_topics_context_recall']['total'] == 0
    assert result['ideal_selection_assembly_NOT_model_output']['qualifier_preservation']['rate'] is None
    assert 'model_correctness' not in result


def test_source_association_cannot_borrow_fact_from_another_material():
    case = {'expected_sources': []}
    info = {'sources': [], 'points': [{'text': 'Квота не гарантирует зачисление.', 'sources': [{'id': 'wrong'}]}]}
    score = eval_module.assembly_measure(case, info, {'right': 'Квота не гарантирует зачисление.', 'wrong': 'Запишись к врачу.'})
    assert score['source_passage_fidelity_ok']
    assert score['structured_source_associations_ok'] is False


def test_unit_only_live_pacing_sanitizes_errors_and_continues():
    """Synthetic runner mechanics only; this is not a live quality evaluation."""
    import asyncio
    from types import SimpleNamespace
    now, waits, invocation_stamps = [0.0], [], []

    async def fake_sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    async def fake_provider(question):
        invocation_stamps.append(now[0])
        if question == 'fail':
            raise RuntimeError('secret provider body must not reach result')
        return '{"status":"unsupported"}'

    module = SimpleNamespace(call_llm=fake_provider)

    class UnitPlanner:
        async def propose(self, owner, operation, question, **kwargs):
            await module.call_llm(question)
            return {'status': 'unsupported'}

    module.PlannerAI = UnitPlanner
    cases = [{'id': str(i), 'question': 'fail' if i == 1 else 'normal', 'intent': 'unsupported',
              'expected_material_groups': [], 'forbidden_material_ids': []} for i in range(58)]
    result = asyncio.run(eval_module.live_cases(cases, {}, module, handbook_module=SimpleNamespace(),
                                                clock=lambda: now[0], sleep=fake_sleep))
    assert len(result['cases']) == 58
    assert result['case_errors'] == 1
    assert result['cases'][1]['error_type'] == 'RuntimeError'
    assert 'secret provider body' not in json.dumps(result)
    assert result['provider_invocations'] == 58
    assert result['actual_provider_intent_correct']['total'] == 57
    assert waits and all(0 < seconds <= 5 for seconds in waits)
    assert all(sum(0 <= stamp - previous < 60 for previous in invocation_stamps[:i + 1]) <= 55
               for i, stamp in enumerate(invocation_stamps))
    assert module.call_llm is fake_provider


def test_unit_only_actual_answer_is_not_replaced_by_ideal_or_urgent_model_credit():
    """Synthetic wrong answer must stay wrong; mocks produce no quality report."""
    import asyncio
    from types import SimpleNamespace
    oracle = {'urls': ['/?handbook=right'], 'qualifier_units': ['не гарантирует зачисление'], 'first_action_units': []}
    base = {'intent': 'answer', 'expected_material_groups': [['right']], 'forbidden_material_ids': [],
            'expected_sources': [oracle], 'ideal_material_ids': ['right']}
    cases = [dict(base, id='model', question='model'), dict(base, id='urgent', question='urgent', intent='urgent')]

    async def fake_provider():
        return '{"status":"draft","material_ids":["wrong"],"topic":"study"}'

    module = SimpleNamespace(call_llm=fake_provider)
    texts = {'right': 'Квота не гарантирует зачисление.', 'wrong': 'Запишись к врачу.'}

    class UnitPlanner:
        async def propose(self, owner, operation, question, **kwargs):
            mid = 'right' if question == 'urgent' else 'wrong'
            if mid == 'wrong':
                await module.call_llm()
            return {'status': 'information', 'context': {'material_ids': [mid]},
                    'information': {'priority': 'urgent' if mid == 'right' else 'normal',
                                    'text': texts[mid], 'sources': [{'id': mid, 'url': '/?handbook=' + mid}],
                                    'points': [{'text': texts[mid], 'sources': [{'id': mid}]}] * 3}}

    module.PlannerAI = UnitPlanner
    handbook = SimpleNamespace(handbook_material=lambda config, mid: {'text': texts[mid]})
    result = asyncio.run(eval_module.live_cases(cases, {}, module, handbook_module=handbook))
    wrong, urgent = result['cases']
    assert wrong['live_actual_answer_assembly']['qualifier_units_preserved'] == 0
    assert wrong['live_actual_answer_assembly']['expected_urls_present'] == 0
    assert not wrong['material_selection']['all_groups_in_context']
    assert wrong['live_actual_answer_assembly']['source_passage_fidelity_ok']
    assert urgent['response_origin'] == 'deterministic_urgent'
    assert not urgent['provider_invoked']
    assert 'actual_provider_selection' not in urgent
    assert result['actual_provider_intent_correct']['total'] == 1
    assert result['deterministic_urgent_responses'] == 1
    assemblies = result['live_actual_answer_assembly']
    assert assemblies['provider_selected']['qualifier_preservation']['passed'] == 0
    assert assemblies['deterministic_urgent_NOT_model_output']['qualifier_preservation']['passed'] == 1


def test_unit_only_pacing_wait_does_not_consume_real_async_provider_timeout():
    """Scaled real asyncio timeout reproduces the service's 18-second boundary."""
    import asyncio
    from types import SimpleNamespace
    now, waits = [0.0], []

    async def paced_sleep(seconds):
        now[0] += seconds
        waits.append(seconds)
        # Longer than the simulated provider timeout: the former implementation
        # awaiting this from call_llm would return unavailable on call 56.
        await asyncio.sleep(0.005)

    async def fake_provider():
        return '{"status":"unsupported"}'

    module = SimpleNamespace(call_llm=fake_provider)

    class TimeoutPlanner:
        async def propose(self, owner, operation, question, **kwargs):
            try:
                async with asyncio.timeout(0.001):
                    await module.call_llm()
                return {'status': 'unsupported'}
            except TimeoutError:
                return {'status': 'unsupported', 'reason': 'unavailable'}

    module.PlannerAI = TimeoutPlanner
    cases = [{'id': str(i), 'question': 'synthetic', 'intent': 'unsupported',
              'expected_material_groups': [], 'forbidden_material_ids': []} for i in range(56)]
    result = asyncio.run(eval_module.live_cases(cases, {}, module, handbook_module=SimpleNamespace(),
                                                clock=lambda: now[0], sleep=paced_sleep))
    assert waits
    assert result['provider_invocations'] == 56
    assert result['actual_provider_intent_correct']['passed'] == 56
    assert all(row.get('returned_reason') is None for row in result['cases'])
    assert result['case_errors'] == 0


def test_unit_only_invalid_raw_selection_does_not_abort_or_escape_denominator():
    import asyncio
    from types import SimpleNamespace

    async def fake_provider(question):
        return ('{"status":"draft","material_ids":1}' if question == 'invalid'
                else '{"status":"draft","material_ids":["right"]}')

    module = SimpleNamespace(call_llm=fake_provider)

    class ValidatingPlanner:
        async def propose(self, owner, operation, question, **kwargs):
            await module.call_llm(question)
            return {'status': 'unsupported', 'reason': 'unavailable'}

    module.PlannerAI = ValidatingPlanner
    cases = [{'id': str(i), 'question': question, 'intent': 'answer',
              'expected_material_groups': [['right']], 'forbidden_material_ids': []}
             for i, question in enumerate(['invalid', 'valid'])]
    result = asyncio.run(eval_module.live_cases(cases, {}, module, handbook_module=SimpleNamespace()))
    assert len(result['cases']) == 2
    assert result['cases'][0]['provider_selection_shape_valid'] is False
    assert result['cases'][0]['error_type'] == 'ValueError'
    assert result['cases'][1]['material_selection']['all_groups_in_context']
    assert result['actual_provider_all_material_groups_selected'] == {'passed': 1, 'total': 2, 'rate': 0.5}
    assert result['case_errors'] == 1
    assert module.call_llm is fake_provider
