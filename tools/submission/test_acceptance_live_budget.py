"""Pure accounting checks: importing the probe never contacts a provider."""
from types import SimpleNamespace
from tools.submission import acceptance_live as probe


def test_usage_prices_and_cache():
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=400))
    assert probe.usage_charge(usage)[1] == 600 * 20 + 400 * 4 + 200 * 40


def test_unresolved_attempt_retains_full_reservation():
    rows = [dict(event='reserved', attempt_id='one', reserved_microrub=700000),
            dict(event='reserved', attempt_id='retry', reserved_microrub=700000),
            dict(event='resolved', attempt_id='one', usage_known=True, charged_microrub=100000)]
    assert probe.accounting(rows) == (2, 800000, ['retry'])
    rows.append(dict(event='resolved', attempt_id='retry', usage_known=False))
    assert probe.accounting(rows) == (2, 800000, ['retry'])


def test_known_retry_counts_as_another_underlying_attempt():
    rows = []
    for index in range(20):
        rows.extend([dict(event='reserved', attempt_id=str(index), reserved_microrub=700000),
                     dict(event='resolved', attempt_id=str(index), usage_known=True, charged_microrub=100000)])
    assert probe.accounting(rows) == (20, 2000000, [])


def test_missing_or_invalid_usage_stops():
    import pytest
    with pytest.raises(probe.BudgetStop):
        probe.usage_charge(None)
    with pytest.raises(probe.BudgetStop):
        probe.usage_charge(SimpleNamespace(prompt_tokens=1, completion_tokens=1,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2)))


def test_limits_apply_before_the_next_underlying_request():
    import pytest
    rows = []
    for index in range(20):
        rows.extend([dict(event='reserved', attempt_id=str(index), reserved_microrub=700000),
                     dict(event='resolved', attempt_id=str(index), usage_known=True, charged_microrub=100000)])
    with pytest.raises(probe.BudgetStop):
        probe.guard_budget(rows, 1000)
    rows = [dict(event='reserved', attempt_id='one', reserved_microrub=4500000),
            dict(event='resolved', attempt_id='one', usage_known=True, charged_microrub=4500000)]
    probe.guard_budget(rows, 500000)
    with pytest.raises(probe.BudgetStop):
        probe.guard_budget(rows, 500001)


def test_unknown_cost_prevents_a_retry():
    import pytest
    rows = [dict(event='reserved', attempt_id='one', reserved_microrub=700000),
            dict(event='resolved', attempt_id='one', usage_known=False)]
    with pytest.raises(probe.BudgetStop):
        probe.guard_budget(rows, 1)


def test_secret_is_blocked_before_evidence_write(tmp_path):
    import pytest
    probe._SECRET = 'inert-test-secret'
    try:
        with pytest.raises(probe.BudgetStop):
            probe.append(tmp_path / 'blocked.jsonl', dict(result='inert-test-secret'))
        assert not (tmp_path / 'blocked.jsonl').exists()
    finally:
        probe._SECRET = None


def test_manual_continuation_never_discounts_or_erases_unknown_reserve():
    import pytest
    rows = [dict(event='reserved', attempt_id='one', reserved_microrub=1150860),
            dict(event='resolved', attempt_id='one', usage_known=False)]
    with pytest.raises(probe.BudgetStop):
        probe.guard_budget(rows, 100000)
    rows.append(dict(event='operator_reserved_as_spent', attempt_id='one',
                     reserved_microrub=1150860, limits=dict(attempts=20, rub=5)))
    assert probe.accounting(rows) == (1, 1150860, [])
    probe.guard_budget(rows, 3849140)
    with pytest.raises(probe.BudgetStop):
        probe.guard_budget(rows, 3849141)
    assert rows[1]['usage_known'] is False
