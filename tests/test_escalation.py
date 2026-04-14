from __future__ import annotations

from tokenwise.backend.router.escalation import EscalationManager


def test_should_retry_same_model_only_on_first_attempt_with_validation_failure():
    manager = EscalationManager()

    assert manager.should_retry_same_model(1, validation_failed=True) is True
    assert manager.should_retry_same_model(1, validation_failed=False) is False


def test_should_retry_same_model_returns_false_on_attempt_two():
    manager = EscalationManager()

    assert manager.should_retry_same_model(2, validation_failed=True) is False


def test_should_switch_provider_only_when_no_prior_provider_failures():
    manager = EscalationManager()

    assert manager.should_switch_provider(0) is True
    assert manager.should_switch_provider(1) is False


def test_can_escalate_for_tiers_one_and_two_only():
    manager = EscalationManager()

    assert manager.can_escalate(1) is True
    assert manager.can_escalate(2) is True
    assert manager.can_escalate(3) is False


def test_record_failure_increments_per_hint_and_tier():
    manager = EscalationManager()

    manager.record_failure("general_reasoning", 1)
    manager.record_failure("general_reasoning", 1)
    manager.record_failure("structured_output", 1)
    manager.record_failure("general_reasoning", 2)

    assert manager._fail_counts[("general_reasoning", 1)] == 2
    assert manager._fail_counts[("structured_output", 1)] == 1
    assert manager._fail_counts[("general_reasoning", 2)] == 1


def test_suggested_start_tier_returns_base_when_fail_count_below_three():
    manager = EscalationManager()
    manager.record_failure("general_reasoning", 1)
    manager.record_failure("general_reasoning", 1)

    assert manager.suggested_start_tier("general_reasoning", 1) == 1


def test_suggested_start_tier_returns_base_plus_one_when_fail_count_is_three_capped_at_three():
    manager = EscalationManager()
    for _ in range(3):
        manager.record_failure("general_reasoning", 2)
    for _ in range(3):
        manager.record_failure("creative_synthesis", 3)

    assert manager.suggested_start_tier("general_reasoning", 2) == 3
    assert manager.suggested_start_tier("creative_synthesis", 3) == 3

