from __future__ import annotations


class EscalationManager:
    def __init__(self) -> None:
        self._fail_counts: dict[tuple[str, int], int] = {}

    def should_retry_same_model(self, attempt_number: int, validation_failed: bool) -> bool:
        return validation_failed and attempt_number == 1

    def should_switch_provider(self, provider_failures: int) -> bool:
        return provider_failures == 0

    def can_escalate(self, tier: int) -> bool:
        return tier < 3

    def record_failure(self, routing_hint: str, tier: int) -> None:
        key = (routing_hint, tier)
        self._fail_counts[key] = self._fail_counts.get(key, 0) + 1

    def suggested_start_tier(self, routing_hint: str, base_tier: int) -> int:
        fail_count = self._fail_counts.get((routing_hint, base_tier), 0)
        if fail_count > 2:
            return min(3, base_tier + 1)
        return base_tier
