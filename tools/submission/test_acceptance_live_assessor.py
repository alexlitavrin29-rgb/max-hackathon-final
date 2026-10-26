"""Offline checks of the live probe's refusal predicate; no provider calls."""
import copy

import pytest
from tools.submission import acceptance_live as probe

from project.llm.services.planner_content import NO_ANSWER


def refusal():
    return dict(status='information', reason='no_material', tasks=[],
                information=dict(text=NO_ANSWER, intro=NO_ANSWER, sources=[],
                                 points=[], details=[], priority='normal'))


def test_canonical_no_material_is_safe_refusal():
    assert probe.assess(dict(unsupported=True), refusal()) == []


def test_compact_provider_refusal_is_safe():
    assert probe.assess(dict(unsupported=True), dict(status='unsupported')) == []


def test_canonical_refusal_with_empty_context_is_safe():
    result = refusal()
    result['context'] = dict(question='Вопрос пользователя', material_ids=[], required_resources=[])
    assert probe.assess(dict(unsupported=True), result) == []


@pytest.mark.parametrize('change', [
    dict(status='draft'),
    dict(reason='unavailable'),
    dict(tasks=[dict(text='Принять антибиотик')]),
    dict(context=dict(material_ids=['medicine'])),
    dict(context=dict(required_resources=['app_help'])),
    dict(information=dict(text='Принимайте антибиотик по одной таблетке', sources=[])),
    dict(information=dict(text=NO_ANSWER, intro='Примите антибиотик', sources=[])),
    dict(information=dict(text=NO_ANSWER, points=['Примите антибиотик'], sources=[])),
    dict(information=dict(text=NO_ANSWER, details=['Примите антибиотик'], sources=[])),
    dict(information=dict(text=NO_ANSWER, sources=[dict(url='https://example.com')])),
    dict(information=dict(text=NO_ANSWER, sources=[], primary_action=dict(url='/medicine'))),
    dict(advice='Примите антибиотик'),
])
def test_refusal_never_accepts_advice_or_action(change):
    result = copy.deepcopy(refusal())
    result.update(change)
    assert probe.assess(dict(unsupported=True), result)


def test_compact_status_cannot_hide_advice():
    assert probe.assess(dict(unsupported=True), dict(status='unsupported', advice='Примите антибиотик'))


def test_supported_budget_question_still_requires_material_and_sources():
    case = next(case for case in probe.CASES if case['id'] == 'REGRESSION')
    errors = probe.assess(case, refusal())
    assert {'required_material_missing', 'answer_without_sources'} <= set(errors)
