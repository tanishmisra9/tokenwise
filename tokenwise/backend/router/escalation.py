from __future__ import annotations


class EscalationManager:
    def should_retry_same_model(self, attempt_number: int, validation_failed: bool) -> bool:
        return validation_failed and attempt_number == 1

    def should_switch_provider(self, provider_failures: int) -> bool:
        return provider_failures == 0

    def can_escalate(self, tier: int) -> bool:
        return tier < 3

