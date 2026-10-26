#!/usr/bin/env python3
"""Frozen-corpus diagnostics. Offline oracle assembly is NOT model correctness.

Run using the project's virtualenv. --source-root can point at an isolated old
checkout; imports and scenario then come exclusively from that checkout. Corpus
is always read from the evaluator checkout unless --corpus is supplied.
Provider calls require --live AND ANSWER_QUALITY_ALLOW_PROVIDER=1 AND valid
provider configuration; no environment values or provider responses are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / 'submission/ai-evals/questions.v1.json'
FROZEN_SHA256 = '5a1abd573b638960291e1634f03015a59e9437e4c5cdc784ca47f9ca4448a087'


def normalized(text):
    """Ignore display markup/numbering, not words, negations or qualifications."""
    text = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', str(text))
    text = re.sub(r'^\s*(?:[•☐*]|\d+[.)])\s*', '', text, flags=re.M)
    return re.sub(r'\s+', ' ', text.replace('**', '').replace('__', '')).strip()


def ratio(passed, total):
    return {'passed': passed, 'total': total, 'rate': passed / total if total else None}


def retrieval_measure(case, ranked_ids):
    groups = case['expected_material_groups']
    positions = {mid: ranked_ids.index(mid) + 1 for group in groups for mid in group if mid in ranked_ids}
    ranks = [min((positions[mid] for mid in group if mid in positions), default=None) for group in groups]
    return {'eligible': bool(groups), 'positions': positions, 'group_ranks': ranks,
            'all_groups_at': {str(k): all(rank is not None and rank <= k for rank in ranks)
                              for k in (1, 3, 5)},
            'all_groups_in_context': bool(groups) and all(rank is not None for rank in ranks),
            'forbidden_materials_present': sorted(set(ranked_ids) & set(case['forbidden_material_ids']))}


def information_text(info, *, surfaced=False):
    fields = [info.get('text', ''), info.get('intro', '')]
    fields += [p.get('text', '') for p in info.get('points', []) if isinstance(p, dict)]
    if not surfaced:
        fields += [p.get('text', '') for p in info.get('details', []) if isinstance(p, dict)]
    return normalized('\n'.join(fields))


def assembly_measure(case, info, source_texts):
    text = information_text(info)
    surfaced = information_text(info, surfaced=True)
    sources = info.get('sources', [])
    actual_urls = {x.get('url') for x in sources if isinstance(x, dict)}
    expected_urls = {url for entry in case['expected_sources'] for url in entry['urls']}
    qualifiers = [unit for entry in case['expected_sources'] for unit in entry['qualifier_units']]
    first_actions = [unit for entry in case['expected_sources'] for unit in entry['first_action_units']]
    points = info.get('points')
    if not isinstance(points, list):
        points = [{'text': line[2:]} for line in info.get('text', '').splitlines() if line.startswith('• ')]
    # Every substantive displayed point must be an intact source passage, or a
    # composition of whole source sentences/lines. Never use topic overlap.
    source_joined = normalized('\n'.join(source_texts.values()))
    ungrounded = []
    for point in points:
        value = normalized(point.get('text', ''))
        if value and value not in source_joined:
            units = [normalized(x) for x in re.split(r'(?<=[.!?])\s+|\n+', point.get('text', '')) if normalized(x)]
            if any(unit not in source_joined for unit in units):
                ungrounded.append(value)
    associations = []
    for point in points:
        refs = point.get('sources', [])
        # Structured refs may be IDs or source dictionaries.
        ids = [ref.get('id', ref.get('material_id')) if isinstance(ref, dict) else ref for ref in refs]
        if refs:
            associations.append(all(mid in source_texts for mid in ids) and any(normalized(point.get('text', '')) in normalized(source_texts.get(mid, '')) for mid in ids))
    return {
        'expected_urls': len(expected_urls), 'expected_urls_present': len(expected_urls & actual_urls),
        'missing_urls': sorted(expected_urls - actual_urls),
        'unexpected_urls': sorted(actual_urls - expected_urls),
        'qualifier_units_surfaced': sum(normalized(x) in surfaced for x in qualifiers),
        'qualifier_units': len(qualifiers), 'qualifier_units_preserved': sum(normalized(x) in text for x in qualifiers),
        'missing_qualifier_units': [x for x in qualifiers if normalized(x) not in text],
        'first_action_units': len(first_actions), 'first_action_units_surfaced': sum(normalized(x) in surfaced for x in first_actions),
        'point_count': len(points), 'global_point_budget_ok': 3 <= len(points) <= 5,
        'source_passage_fidelity_ok': not ungrounded, 'ungrounded_points': ungrounded,
        'structured_source_associations': len(associations),
        'structured_source_associations_ok': all(associations) if associations else None,
    }


def validate_corpus(corpus, config, content, handbook):
    allowed = {x['id'] for x in content.material_catalog(config)}
    reachable = {x['id'] for x in handbook.handbook_catalog(config)}
    failures = []
    for case in corpus['cases']:
        for entry in case['expected_sources']:
            mid = entry['material_id']
            card = handbook.handbook_material(config, mid)
            if mid not in allowed & reachable or not card:
                failures.append({'case': case['id'], 'material': mid, 'error': 'unavailable'})
                continue
            if card.get('content_status') in {'unverified_reference', 'draft', 'archived'}:
                failures.append({'case': case['id'], 'material': mid, 'error': 'unsafe_status'})
            if hashlib.sha256(card['text'].encode()).hexdigest() != entry['source_text_sha256']:
                failures.append({'case': case['id'], 'material': mid, 'error': 'source_text_changed'})
            for unit in entry['qualifier_units'] + entry['first_action_units']:
                if unit not in card['text']:
                    failures.append({'case': case['id'], 'material': mid, 'error': 'oracle_unit_absent'})
    return failures


def summarize(rows):
    retrieval = [r['retrieval'] for r in rows if r.get('retrieval', {}).get('eligible')]
    assembly = [r['ideal_selection_assembly'] for r in rows if 'ideal_selection_assembly' in r]
    resources = [r['resources'] for r in rows if 'resources' in r]
    return {
        'retrieval': {**{f'all_topics_recall_at_{k}': ratio(sum(r['all_groups_at'][str(k)] for r in retrieval), len(retrieval)) for k in (1, 3, 5)},
                      'all_topics_context_recall': ratio(sum(r['all_groups_in_context'] for r in retrieval), len(retrieval))},
        'ideal_selection_assembly_NOT_model_output': {
            'global_point_budget': ratio(sum(r['global_point_budget_ok'] for r in assembly), len(assembly)),
            'source_passage_fidelity': ratio(sum(r['source_passage_fidelity_ok'] for r in assembly), len(assembly)),
            'source_url_coverage': ratio(sum(r['expected_urls_present'] for r in assembly), sum(r['expected_urls'] for r in assembly)),
            'qualifier_preservation': ratio(sum(r['qualifier_units_preserved'] for r in assembly), sum(r['qualifier_units'] for r in assembly)),
            'qualifier_surfaced': ratio(sum(r['qualifier_units_surfaced'] for r in assembly), sum(r['qualifier_units'] for r in assembly)),
            'structured_source_association_fidelity': ratio(sum(r['structured_source_associations_ok'] is True for r in assembly), sum(r['structured_source_associations_ok'] is not None for r in assembly)),
            'first_action_surfaced': ratio(sum(r['first_action_units_surfaced'] for r in assembly), sum(r['first_action_units'] for r in assembly))},
        'resources_with_oracle_topic_NOT_model_choice': {
            'required_present': ratio(sum(r['required_ok'] for r in resources if r['required_count']), sum(bool(r['required_count']) for r in resources)),
            'forbidden_absent': ratio(sum(r['forbidden_ok'] for r in resources), len(resources))},
        'errors': sum(bool(r.get('errors')) for r in rows),
        'total_elapsed_ms': round(sum(r['elapsed_ms'] for r in rows), 2),
    }


async def live_cases(cases, config, ai_module, *, handbook_module=None,
                     clock=time.monotonic, sleep=asyncio.sleep):
    """Observe actual selections/answers; never assemble an oracle answer here.

    Dependency injection is only for evaluator unit tests, not quality scores.
    The rolling 55-call budget applies at the real provider boundary, including
    failed calls. Each asynchronous wait is at most five seconds.
    """
    if handbook_module is None:
        handbook_module = importlib.import_module('project.llm.services.handbook')
    original = ai_module.call_llm
    observed, invocation_times = [], []

    async def wait_for_provider_budget():
        # This wait MUST precede propose/admit and its provider timeout.
        while True:
            now = clock()
            invocation_times[:] = [stamp for stamp in invocation_times if now - stamp < 60]
            if len(invocation_times) < 55:
                break
            await sleep(min(5.0, max(0.001, 60.001 - (now - invocation_times[0]))))

    async def observing_call(*args, **kwargs):
        # PlannerAI makes at most one provider call per proposal. Accounting is
        # at invocation, while pacing happens outside its 18-second timeout.
        invocation_times.append(clock())
        observed.append({'invoked': True})
        raw = await original(*args, **kwargs)
        try:
            parsed = json.loads(raw)
            observed[-1]['selection'] = {k: parsed.get(k) for k in ('status', 'material_ids', 'topic')}
        except (ValueError, TypeError, AttributeError):
            observed[-1]['parse_error'] = True
        return raw

    ai_module.call_llm = observing_call
    results = []
    planner = ai_module.PlannerAI()
    try:
        for case in cases:
            observed.clear()
            start = clock()
            entry = {'id': case['id'], 'expected_materials_eligible': bool(case['expected_material_groups'])}
            try:
                await wait_for_provider_budget()
                proposal = await planner.propose('answer-eval-' + case['id'], 'find_help_route', case['question'], config=config)
                entry.update(returned_status=proposal.get('status'), returned_reason=proposal.get('reason'))
                info = proposal.get('information')
                deterministic_urgent = not observed and (
                    proposal.get('priority') == 'urgent' or isinstance(info, dict) and info.get('priority') == 'urgent')
                entry['response_origin'] = ('deterministic_urgent' if deterministic_urgent else
                                            'provider_selected' if observed else 'no_provider_fallback')
                if isinstance(info, dict):
                    # Ground the ACTUAL returned points against the published
                    # cards named by that response. Expected units remain the
                    # frozen question-specific oracle. No build_information call.
                    actual_ids = set(proposal.get('context', {}).get('material_ids', []))
                    actual_ids.update(source.get('id') for source in info.get('sources', []) if isinstance(source, dict))
                    texts = {}
                    for mid in actual_ids:
                        if isinstance(mid, str):
                            card = handbook_module.handbook_material(config, mid)
                            if card:
                                texts[mid] = card['text']
                    entry['live_actual_answer_assembly'] = assembly_measure(case, info, texts)
            except Exception as exc:
                # Exception messages/provider bodies may contain secrets. Keep
                # only the class and continue with the next independent case.
                entry['error_type'] = type(exc).__name__
                entry.setdefault('response_origin', 'provider_error' if observed else 'runner_error')
            entry.update(elapsed_ms=round((clock() - start) * 1000, 2),
                         provider_invoked=bool(observed),
                         provider_invocation_count=len(observed),
                         provider_result_received=bool(observed and 'selection' in observed[-1]))
            if entry['provider_result_received']:
                # Keep invalid raw selections in the denominator as failures.
                entry['intent_correct'] = False
                entry['material_selection'] = retrieval_measure(case, [])
                try:
                    choice = observed[-1]['selection']
                    entry['actual_provider_selection'] = choice
                    ids = choice.get('material_ids')
                    if ids is None and choice.get('status') in {'clarify', 'unsupported'}:
                        ids = []
                    if (not isinstance(ids, list) or len(ids) > 3
                            or any(not isinstance(mid, str) for mid in ids)
                            or len(set(ids)) != len(ids)):
                        entry['provider_selection_shape_valid'] = False
                        raise ValueError('Invalid provider material selection shape')
                    entry['provider_selection_shape_valid'] = True
                    expected_status = {'answer': 'draft', 'urgent': 'draft', 'clarify': 'clarify', 'unsupported': 'unsupported'}[case['intent']]
                    entry['intent_correct'] = choice.get('status') == expected_status
                    entry['material_selection'] = retrieval_measure(case, ids)
                except Exception as exc:
                    entry['error_type'] = type(exc).__name__
            results.append(entry)
    finally:
        ai_module.call_llm = original
    called = [r for r in results if r['provider_result_received']]
    material_rows = [r['material_selection'] for r in called if r['material_selection']['eligible']]

    def actual_assembly_summary(origin):
        rows = [r['live_actual_answer_assembly'] for r in results
                if r['response_origin'] == origin and r['expected_materials_eligible'] and 'live_actual_answer_assembly' in r]
        return {'eligible_actual_answers': len(rows),
                'global_point_budget': ratio(sum(r['global_point_budget_ok'] for r in rows), len(rows)),
                'source_passage_fidelity': ratio(sum(r['source_passage_fidelity_ok'] for r in rows), len(rows)),
                'source_url_coverage': ratio(sum(r['expected_urls_present'] for r in rows), sum(r['expected_urls'] for r in rows)),
                'qualifier_preservation': ratio(sum(r['qualifier_units_preserved'] for r in rows), sum(r['qualifier_units'] for r in rows)),
                'first_action_surfaced': ratio(sum(r['first_action_units_surfaced'] for r in rows), sum(r['first_action_units'] for r in rows))}

    return {'status': 'RUN', 'cases': results, 'actual_provider_intent_correct': ratio(sum(r['intent_correct'] for r in called), len(called)),
            'actual_provider_all_material_groups_selected': ratio(sum(r['all_groups_in_context'] for r in material_rows), len(material_rows)),
            'provider_invocations': sum(r['provider_invocation_count'] for r in results),
            'live_actual_answer_assembly': {'provider_selected': actual_assembly_summary('provider_selected'),
                                            'deterministic_urgent_NOT_model_output': actual_assembly_summary('deterministic_urgent'),
                                            'no_provider_fallback_NOT_model_output': actual_assembly_summary('no_provider_fallback')},
            'deterministic_urgent_responses': sum(r['response_origin'] == 'deterministic_urgent' for r in results),
            'case_errors': sum('error_type' in r for r in results),
            'pacing': {'provider_invocations_per_rolling_minute': 55, 'max_async_sleep_seconds': 5}}

def run(args):
    source_root = Path(args.source_root).resolve()
    sys.path.insert(0, str(source_root))
    content = importlib.import_module('project.llm.services.planner_content')
    handbook = importlib.import_module('project.llm.services.handbook')
    config = json.loads((source_root / 'submission/scenario.json').read_text())['config']
    corpus_path = Path(args.corpus)
    digest = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    if digest != FROZEN_SHA256:
        raise ValueError('Corpus differs from frozen version; freeze a new explicit version before scoring')
    corpus = json.loads(corpus_path.read_text())
    invalid = validate_corpus(corpus, config, content, handbook)
    if invalid:
        raise ValueError('Oracle validation failed: ' + json.dumps(invalid, ensure_ascii=False))
    try:
        urgent = importlib.import_module('project.llm.services.planner_retrieval').urgent_material
    except (ImportError, AttributeError):
        urgent = None
    rows = []
    for case in corpus['cases']:
        start = time.monotonic()
        row = {'id': case['id'], 'question': case['question'], 'intent': case['intent'], 'errors': []}
        try:
            ranked = content.candidates(config, case['question'])
            row['retrieval'] = retrieval_measure(case, [x['id'] for x in ranked])
            row['retrieval']['context_size'] = len(ranked)
            row['retrieval']['excerpt_characters'] = sum(len(x.get('excerpt', '')) for x in ranked)
            selection = {'topic': case['topic'], 'secondary_topics': list(dict.fromkeys(x['topic'] for x in content.material_catalog(config) if x['id'] in case['ideal_material_ids'] and x['topic'] != case['topic']))}
            resources = content.required_searches(selection, case['question'])
            row['resources'] = {'actual': resources, 'required_count': len(case['required_resources']), 'required_ok': set(case['required_resources']) <= set(resources), 'forbidden_ok': not set(case['forbidden_resources']) & set(resources)}
            if case['ideal_material_ids']:
                info = content.build_information(config, case['ideal_material_ids'])
                texts = {mid: handbook.handbook_material(config, mid)['text'] for mid in case['ideal_material_ids']}
                row['ideal_selection_assembly'] = assembly_measure(case, info, texts)
            # Optional new shortcut is evaluated separately, not conflated with retrieval.
            if urgent:
                actual = urgent(config, case['question'], content.material_catalog(config))
                row['urgent_shortcut'] = {'expected_urgent': case['intent'] == 'urgent', 'actual': actual, 'correct': actual in case['ideal_material_ids'] if case['intent'] == 'urgent' else actual is None}
            else:
                row['urgent_shortcut'] = {'status': 'NOT_IMPLEMENTED'}
        except Exception as exc:
            row['errors'].append(type(exc).__name__)
        row['elapsed_ms'] = round((time.monotonic() - start) * 1000, 2)
        rows.append(row)
    live = {'status': 'NOT_RUN', 'reason': 'Offline diagnostics; model choice, clarification and unsupported correctness unverified.'}
    if args.live:
        if os.environ.get('ANSWER_QUALITY_ALLOW_PROVIDER') != '1':
            raise ValueError('Live requires ANSWER_QUALITY_ALLOW_PROVIDER=1')
        if os.environ.get('PLANNER_AI_ENABLED') != '1' or os.environ.get('LLM_ENABLED', '1') == '0':
            raise ValueError('Live requires enabled PlannerAI provider configuration')
        importlib.import_module('project.llm.config').LLMSettings.from_env()
        live = asyncio.run(live_cases(corpus['cases'], config, importlib.import_module('project.llm.services.planner_ai')))
    source_files = ['submission/scenario.json', *['project/llm/services/' + name + '.py' for name in ('planner_content', 'planner_retrieval', 'planner_information', 'handbook', 'planner_ai')]]
    source_files += ['project/llm/services/handbook_sources.py', 'project/llm/services/handbook_source_links.json']
    source_hashes = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in source_files if (source_root / name).is_file()}
    result = {'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'source_file_sha256': source_hashes, 'schema_version': 1, 'label': args.label, 'source_root': str(source_root), 'corpus_sha256': digest,
              'cases': len(rows), 'oracle_validation': 'PASS', 'summary': summarize(rows), 'live_model': live,
              'limitations': ['Curated user-style questions; not production usage distribution.', 'Ideal selection assembly cannot establish model correctness.', 'Exact source passage and qualifier checks are mechanical diagnostics, not legal or semantic adjudication.', 'Resource checks use oracle topic; live model correctness has a separate denominator.'], 'results': rows}
    urgent_rows = [r['urgent_shortcut'] for r in rows if 'correct' in r.get('urgent_shortcut', {})]
    result['summary']['urgent_shortcut'] = {
        'all_cases': ratio(sum(r['correct'] for r in urgent_rows), len(urgent_rows)),
        'urgent_recall': ratio(sum(r['correct'] for r in urgent_rows if r['expected_urgent']), sum(r['expected_urgent'] for r in urgent_rows)),
        'nonurgent_specificity': ratio(sum(r['correct'] for r in urgent_rows if not r['expected_urgent']), sum(not r['expected_urgent'] for r in urgent_rows))}
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in {'results', 'live_model'}}, ensure_ascii=False, indent=2))
    print(json.dumps({'live_model': {k: v for k, v in live.items() if k != 'cases'}}, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', default=str(ROOT))
    parser.add_argument('--corpus', default=str(CORPUS))
    parser.add_argument('--output')
    parser.add_argument('--label', default='candidate')
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        # Do not print provider exception messages: they could contain request data.
        print(json.dumps({'error': type(exc).__name__, 'stage': 'configuration_or_oracle_validation'}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
