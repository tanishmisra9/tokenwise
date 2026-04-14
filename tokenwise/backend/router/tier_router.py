from __future__ import annotations

from tokenwise.backend.models.schemas import (
    Complexity,
    Provider,
    QualityFloor,
    RouteDecision,
    RoutingHint,
    SubTask,
)


class TierRouter:
    def __init__(self, registry: dict[str, object]) -> None:
        self.registry = registry

    def route(
        self,
        subtask: SubTask,
        quality_floor: QualityFloor,
        *,
        force_tier_one: bool = False,
        preferred_provider: Provider | None = None,
    ) -> RouteDecision:
        base_tier = {
            Complexity.LOW: 1,
            Complexity.MEDIUM: 2,
            Complexity.HIGH: 3,
        }[subtask.complexity]
        minimum_tier = {
            QualityFloor.LOW: 1,
            QualityFloor.MEDIUM: 2,
            QualityFloor.HIGH: 3,
        }[quality_floor]
        tier = 1 if force_tier_one else max(base_tier, minimum_tier)
        provider = preferred_provider or self._provider_for_subtask(subtask)
        profile = self.registry[f"tier{tier}_{provider.value}"]
        reason = "budget cap reached; forcing Tier 1" if force_tier_one else self._reason_for_route(subtask, tier, provider)
        return RouteDecision(
            tier=tier,
            provider=provider,
            model_alias=profile.alias,
            model_id=profile.model_id,
            routing_reason=reason,
            forced_by_budget=force_tier_one,
        )

    def alternate_provider(self, route: RouteDecision) -> RouteDecision:
        alternate = Provider.ANTHROPIC if route.provider == Provider.OPENAI else Provider.OPENAI
        profile = self.registry[f"tier{route.tier}_{alternate.value}"]
        return RouteDecision(
            tier=route.tier,
            provider=alternate,
            model_alias=profile.alias,
            model_id=profile.model_id,
            routing_reason=f"Fallback to {alternate.value} at Tier {route.tier}",
            forced_by_budget=route.forced_by_budget,
        )

    def escalate(self, route: RouteDecision) -> RouteDecision:
        next_tier = min(3, route.tier + 1)
        profile = self.registry[f"tier{next_tier}_{route.provider.value}"]
        return RouteDecision(
            tier=next_tier,
            provider=route.provider,
            model_alias=profile.alias,
            model_id=profile.model_id,
            routing_reason=f"Escalated from Tier {route.tier} to Tier {next_tier}",
            forced_by_budget=route.forced_by_budget,
        )

    def tier_three_profile(self, provider: Provider):
        return self.registry[f"tier3_{provider.value}"]

    def profile_for_route(self, route: RouteDecision):
        return self.registry[route.model_alias]

    def _provider_for_subtask(self, subtask: SubTask) -> Provider:
        if subtask.routing_hint in {RoutingHint.STRUCTURED_OUTPUT, RoutingHint.INSTRUCTION_FOLLOWING}:
            return Provider.ANTHROPIC
        return Provider.OPENAI

    def _reason_for_route(self, subtask: SubTask, tier: int, provider: Provider) -> str:
        return (
            f"{subtask.complexity.value} complexity routed to Tier {tier}; "
            f"{provider.value} selected for {subtask.routing_hint.value}"
        )

