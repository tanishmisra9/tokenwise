from __future__ import annotations

from tokenwise.backend.config import build_model_registry
from tokenwise.backend.models.schemas import Complexity, Provider, QualityFloor, RoutingHint
from tokenwise.backend.router.escalation import EscalationManager
from tokenwise.backend.router.tier_router import TierRouter


def make_router(mock_settings):
    settings = mock_settings()
    return TierRouter(build_model_registry(settings), EscalationManager())


def test_low_complexity_medium_quality_floor_routes_to_tier_2(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(complexity=Complexity.LOW)

    route = router.route(subtask, QualityFloor.MEDIUM)

    assert route.tier == 2


def test_high_complexity_low_quality_floor_routes_to_tier_3(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(complexity=Complexity.HIGH)

    route = router.route(subtask, QualityFloor.LOW)

    assert route.tier == 3


def test_medium_complexity_medium_quality_floor_routes_to_tier_2(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(complexity=Complexity.MEDIUM)

    route = router.route(subtask, QualityFloor.MEDIUM)

    assert route.tier == 2


def test_force_tier_one_always_returns_tier_1(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(complexity=Complexity.HIGH)

    route = router.route(subtask, QualityFloor.HIGH, force_tier_one=True)

    assert route.tier == 1
    assert route.forced_by_budget is True


def test_structured_output_routes_to_anthropic(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(routing_hint=RoutingHint.STRUCTURED_OUTPUT)

    assert router.route(subtask, QualityFloor.LOW).provider == Provider.ANTHROPIC


def test_code_generation_routes_to_openai(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(routing_hint=RoutingHint.CODE_GENERATION)

    assert router.route(subtask, QualityFloor.LOW).provider == Provider.OPENAI


def test_creative_synthesis_routes_to_anthropic(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    subtask = subtask_factory(routing_hint=RoutingHint.CREATIVE_SYNTHESIS)

    assert router.route(subtask, QualityFloor.LOW).provider == Provider.ANTHROPIC


def test_escalate_increments_tier_and_caps_at_three(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    route = router.route(subtask_factory(complexity=Complexity.MEDIUM), QualityFloor.LOW)

    escalated = router.escalate(route)
    capped = router.escalate(router.escalate(escalated))

    assert escalated.tier == route.tier + 1
    assert capped.tier == 3


def test_alternate_provider_flips_provider_and_keeps_tier(mock_settings, subtask_factory):
    router = make_router(mock_settings)
    route = router.route(subtask_factory(routing_hint=RoutingHint.GENERAL_REASONING), QualityFloor.MEDIUM)

    alternate = router.alternate_provider(route)

    assert alternate.provider != route.provider
    assert alternate.tier == route.tier


def test_tier_three_profile_returns_correct_provider_profile(mock_settings):
    settings = mock_settings()
    registry = build_model_registry(settings)
    router = TierRouter(registry, EscalationManager())

    assert router.tier_three_profile(Provider.OPENAI).model_id == settings.openai_tier3_model_id
    assert router.tier_three_profile(Provider.ANTHROPIC).model_id == settings.anthropic_tier3_model_id


def test_escalation_memory_promotes_general_reasoning_after_three_failures(mock_settings, subtask_factory):
    settings = mock_settings()
    escalation_manager = EscalationManager()
    router = TierRouter(build_model_registry(settings), escalation_manager)
    subtask = subtask_factory(complexity=Complexity.LOW, routing_hint=RoutingHint.GENERAL_REASONING)

    for _ in range(3):
        escalation_manager.record_failure("general_reasoning", 1)

    route = router.route(subtask, QualityFloor.LOW)

    assert route.tier == 2

