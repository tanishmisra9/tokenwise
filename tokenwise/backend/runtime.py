from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from tokenwise.backend.agents.composer import ComposerAgent
from tokenwise.backend.agents.orchestrator import OrchestratorAgent
from tokenwise.backend.agents.validator import ValidatorAgent
from tokenwise.backend.config import Settings, build_model_registry
from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import (
    Complexity,
    ExecutionPlan,
    OutputFormat,
    Provider,
    QualityFloor,
    RouteDecision,
    RoutingHint,
    RunEvent,
    RunEventType,
    RunRequest,
    RunResult,
    SubTask,
    SubTaskAttempt,
    SubTaskResult,
    utcnow_iso,
)
from tokenwise.backend.router.escalation import EscalationManager
from tokenwise.backend.router.tier_router import TierRouter
from tokenwise.backend.tracker.cost import compute_cost, summarise_run_stats
from tokenwise.backend.tracker.history import HistoryStore

logger = logging.getLogger(__name__)


@dataclass
class StreamState:
    backlog: list[RunEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue[RunEvent | None]] = field(default_factory=list)
    closed: bool = False
    cancelled: bool = False
    last_event_at: float = field(default_factory=time.monotonic)


class RunEventHub:
    def __init__(self, cleanup_ttl_seconds: int = 600) -> None:
        self._streams: dict[str, StreamState] = {}
        self.cleanup_ttl_seconds = cleanup_ttl_seconds

    def ensure_run(self, run_id: str) -> StreamState:
        return self._streams.setdefault(run_id, StreamState())

    async def publish(self, run_id: str, event: RunEvent) -> None:
        stream = self.ensure_run(run_id)
        stream.backlog.append(event)
        stream.last_event_at = time.monotonic()
        for queue in list(stream.subscribers):
            await queue.put(event)
        if event.event in {RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED}:
            stream.closed = True

    def subscribe(self, run_id: str) -> tuple[list[RunEvent], asyncio.Queue[RunEvent | None], bool]:
        stream = self.ensure_run(run_id)
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        stream.subscribers.append(queue)
        return list(stream.backlog), queue, stream.closed

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[RunEvent | None]) -> None:
        stream = self._streams.get(run_id)
        if not stream:
            return
        if queue in stream.subscribers:
            stream.subscribers.remove(queue)

    def is_closed(self, run_id: str) -> bool:
        stream = self._streams.get(run_id)
        return bool(stream and stream.closed)

    def has_run(self, run_id: str) -> bool:
        return run_id in self._streams

    def cancel(self, run_id: str) -> bool:
        stream = self._streams.get(run_id)
        if not stream:
            return False

        stream.cancelled = True
        stream.last_event_at = time.monotonic()
        for queue in list(stream.subscribers):
            queue.put_nowait(None)
        return True

    def is_cancelled(self, run_id: str) -> bool:
        stream = self._streams.get(run_id)
        return bool(stream and stream.cancelled)

    async def cleanup_expired(self) -> None:
        now = time.monotonic()
        expired_run_ids = [
            run_id
            for run_id, stream in self._streams.items()
            if stream.closed and now - stream.last_event_at > self.cleanup_ttl_seconds
        ]
        for run_id in expired_run_ids:
            del self._streams[run_id]


class TokenwiseCoordinator:
    def __init__(
        self,
        *,
        settings: Settings,
        history_store: HistoryStore | None = None,
        event_hub: RunEventHub | None = None,
        runner: LLMRunner | None = None,
        router: TierRouter | None = None,
        escalation_manager: EscalationManager | None = None,
        orchestrator: OrchestratorAgent | None = None,
        validator: ValidatorAgent | None = None,
        composer: ComposerAgent | None = None,
    ) -> None:
        self.settings = settings
        self.model_registry = build_model_registry(settings)
        self.history_store = history_store or HistoryStore(
            str(settings.resolved_db_path),
            recent_runs_limit=settings.recent_runs_limit,
        )
        self.event_hub = event_hub or RunEventHub()
        self.runner = runner or LLMRunner(settings)
        self.escalation_manager = escalation_manager or EscalationManager()
        self.router = router or TierRouter(self.model_registry, self.escalation_manager)
        meta_provider = settings.meta_agent_provider
        orchestrator_profile = self.model_registry[f"tier2_{meta_provider.value}"]
        validator_profile = self.model_registry[f"tier1_{meta_provider.value}"]
        composer_profile = self.model_registry[f"tier2_{meta_provider.value}"]
        self.orchestrator = orchestrator or OrchestratorAgent(
            self.runner,
            orchestrator_profile.provider,
            orchestrator_profile.model_id,
        )
        self.validator = validator or ValidatorAgent(
            self.runner,
            validator_profile.provider,
            validator_profile.model_id,
        )
        self.composer = composer or ComposerAgent(
            self.runner,
            composer_profile.provider,
            composer_profile.model_id,
        )

    def new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:10]}"

    async def history_response(self):
        return self.history_store.get_history_response()

    async def run(self, run_id: str, request: RunRequest) -> None:
        self.event_hub.ensure_run(run_id)
        result = RunResult(
            run_id=run_id,
            task=request.task,
            started_at=utcnow_iso(),
            budget_cap_usd=request.budget_cap_usd,
        )
        try:
            await self._emit(
                run_id,
                RunEventType.RUN_STARTED,
                {
                    "task": request.task,
                    "budget_cap_usd": request.budget_cap_usd,
                    "quality_floor": request.quality_floor.value,
                },
            )

            plan = await self.orchestrator.create_plan(request.task)
            result.plan = plan
            result.subtask_results = self._initialize_subtask_results(plan, request.quality_floor)

            await self._emit(
                run_id,
                RunEventType.PLAN_READY,
                {
                    "subtasks": [subtask.model_dump(mode="json") for subtask in plan.topological_order()],
                    "routes": [
                        {
                            "subtask_id": subtask_result.subtask.id,
                            **subtask_result.route.model_dump(mode="json"),
                        }
                        for subtask_result in result.subtask_results
                    ],
                },
            )

            completed_outputs: dict[str, str] = {}
            pending_subtasks = list(result.subtask_results)

            while pending_subtasks:
                if self.event_hub.is_cancelled(run_id):
                    raise asyncio.CancelledError

                ready_batch = [
                    subtask_result
                    for subtask_result in pending_subtasks
                    if all(dependency in completed_outputs for dependency in subtask_result.subtask.depends_on)
                ]

                if not ready_batch:
                    raise RuntimeError("No executable subtasks found; execution plan may contain unresolved dependencies.")

                batch_context = dict(completed_outputs)
                batch_tasks = []

                for subtask_result in ready_batch:
                    if result.run_stats.actual_cost_usd >= request.budget_cap_usd:
                        result.budget_locked = True

                    if result.budget_locked and not subtask_result.route.forced_by_budget:
                        downgraded = self.router.route(
                            subtask_result.subtask,
                            request.quality_floor,
                            force_tier_one=True,
                        )
                        await self._emit(
                            run_id,
                            RunEventType.SUBTASK_ESCALATED,
                            {
                                "subtask_id": subtask_result.subtask.id,
                                "action": "budget_lock",
                                "reason": "Run budget reached; unstarted subtasks forced to Tier 1.",
                                "from_route": subtask_result.route.model_dump(mode="json"),
                                "to_route": downgraded.model_dump(mode="json"),
                            },
                        )
                        subtask_result.route = downgraded

                    batch_tasks.append(
                        self._execute_subtask(
                            run_id=run_id,
                            request=request,
                            result=result,
                            subtask_result=subtask_result,
                            completed_outputs=batch_context,
                        )
                    )

                batch_successes = await asyncio.gather(*batch_tasks)
                if self.event_hub.is_cancelled(run_id):
                    raise asyncio.CancelledError
                result.run_stats = summarise_run_stats(result.subtask_results)

                for subtask_result, success in zip(ready_batch, batch_successes, strict=True):
                    pending_subtasks.remove(subtask_result)
                    if not success:
                        raise RuntimeError(result.error or f"Subtask {subtask_result.subtask.id} failed.")
                    completed_outputs[subtask_result.subtask.id] = subtask_result.final_output or ""

            if self.event_hub.is_cancelled(run_id):
                raise asyncio.CancelledError
            result.final_output = await self._compose_final_output(result)
            result.status = "completed"
            result.completed_at = utcnow_iso()
            result.run_stats = summarise_run_stats(result.subtask_results)
            self.history_store.write_run(result)
            history = self.history_store.get_history_stats()
            result.history_stats = history

            await self._emit(
                run_id,
                RunEventType.RUN_COMPLETED,
                {
                    "final_output": result.final_output,
                    "run_stats": result.run_stats.model_dump(mode="json"),
                    "history_stats": history.model_dump(mode="json"),
                },
            )
        except asyncio.CancelledError:
            result.status = "failed"
            result.completed_at = utcnow_iso()
            result.error = "Run cancelled by user"
            result.run_stats = summarise_run_stats(result.subtask_results)
            self.history_store.write_run(result)
            history = self.history_store.get_history_stats()
            result.history_stats = history
            await self._emit(
                run_id,
                RunEventType.RUN_FAILED,
                {
                    "error": result.error,
                    "partial_outputs": {
                        subtask_result.subtask.id: subtask_result.final_output
                        for subtask_result in result.subtask_results
                        if subtask_result.final_output
                    },
                    "run_stats": result.run_stats.model_dump(mode="json"),
                    "history_stats": history.model_dump(mode="json"),
                },
            )
        except Exception as exc:
            result.status = "failed"
            result.completed_at = utcnow_iso()
            result.error = str(exc)
            result.run_stats = summarise_run_stats(result.subtask_results)
            self.history_store.write_run(result)
            history = self.history_store.get_history_stats()
            result.history_stats = history
            await self._emit(
                run_id,
                RunEventType.RUN_FAILED,
                {
                    "error": result.error,
                    "partial_outputs": {
                        subtask_result.subtask.id: subtask_result.final_output
                        for subtask_result in result.subtask_results
                        if subtask_result.final_output
                    },
                    "run_stats": result.run_stats.model_dump(mode="json"),
                    "history_stats": history.model_dump(mode="json"),
                },
            )

    def _initialize_subtask_results(self, plan: ExecutionPlan, quality_floor: QualityFloor) -> list[SubTaskResult]:
        results: list[SubTaskResult] = []
        for subtask in plan.topological_order():
            route = self.router.route(subtask, quality_floor)
            results.append(SubTaskResult(subtask=subtask, route=route))
        return results

    async def _execute_subtask(
        self,
        *,
        run_id: str,
        request: RunRequest,
        result: RunResult,
        subtask_result: SubTaskResult,
        completed_outputs: dict[str, str],
    ) -> bool:
        retry_same_model_used = False
        provider_fallbacks = 0
        route = subtask_result.route

        while True:
            if self.event_hub.is_cancelled(run_id):
                raise asyncio.CancelledError

            attempt_number = len(subtask_result.attempts) + 1
            await self._emit(
                run_id,
                RunEventType.SUBTASK_STARTED,
                {
                    "subtask_id": subtask_result.subtask.id,
                    "description": subtask_result.subtask.description,
                    "attempt_number": attempt_number,
                    "provider": route.provider.value,
                    "model": route.model_id,
                    "tier": route.tier,
                    "forced_by_budget": route.forced_by_budget,
                },
            )

            attempt = SubTaskAttempt(
                attempt_number=attempt_number,
                tier=route.tier,
                provider=route.provider,
                model_alias=route.model_alias,
                model_id=route.model_id,
            )

            try:
                timeout_seconds = self._timeout_for_tier(route.tier)
                try:
                    response = await asyncio.wait_for(
                        self.runner.generate(
                            provider=route.provider,
                            model_id=route.model_id,
                            system_prompt=self._build_subtask_system_prompt(subtask_result.subtask),
                            user_prompt=self._build_subtask_user_prompt(
                                request.task,
                                subtask_result.subtask,
                                completed_outputs,
                            ),
                            max_output_tokens=950 if subtask_result.subtask.output_format.value != "json" else 700,
                            temperature=0.2,
                            json_mode=subtask_result.subtask.output_format.value == "json" and route.provider == Provider.OPENAI,
                        ),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"Subtask {subtask_result.subtask.id} timed out after {timeout_seconds:.1f}s at Tier {route.tier}."
                    ) from exc
                attempt.completed_at = utcnow_iso()
                attempt.usage = response.usage
                attempt.latency_ms = response.latency_ms
                attempt.output_text = response.output_text.strip()
                profile = self.router.profile_for_route(route)
                attempt.cost_usd = compute_cost(response.usage, profile.pricing)
                attempt.baseline_cost_usd = compute_cost(
                    response.usage,
                    self.router.tier_three_profile(route.provider).pricing,
                )
                subtask_result.attempts.append(attempt)

                if (
                    response.latency_ms > self.settings.latency_threshold_ms
                    and not route.forced_by_budget
                    and self.escalation_manager.should_switch_provider(provider_fallbacks)
                ):
                    attempt.error = f"Latency threshold exceeded at {response.latency_ms} ms."
                    new_route = self.router.alternate_provider(route)
                    await self._emit_route_change(
                        run_id,
                        subtask_result.subtask,
                        route,
                        new_route,
                        action="provider_fallback",
                        reason=attempt.error,
                    )
                    route = new_route
                    subtask_result.route = new_route
                    subtask_result.escalations += 1
                    retry_same_model_used = False
                    provider_fallbacks += 1
                    continue

                if result.budget_locked and route.forced_by_budget:
                    subtask_result.final_output = attempt.output_text
                    subtask_result.status = "completed_degraded"
                    await self._emit(
                        run_id,
                        RunEventType.SUBTASK_COMPLETED,
                        {
                            "subtask_id": subtask_result.subtask.id,
                            "output": subtask_result.final_output,
                            "tokens": attempt.usage.model_dump(mode="json"),
                            "cost_usd": attempt.cost_usd,
                            "baseline_cost_usd": attempt.baseline_cost_usd,
                            "latency_ms": attempt.latency_ms,
                            "run_stats": summarise_run_stats(result.subtask_results).model_dump(mode="json"),
                            "degraded": True,
                        },
                    )
                    return True

                validation = await self.validator.validate(
                    subtask_result.subtask,
                    attempt.output_text,
                    subtask_result.subtask.routing_hint,
                    subtask_result.subtask.output_format,
                )
                attempt.validation = validation

                if validation.passed:
                    subtask_result.final_output = attempt.output_text
                    subtask_result.status = "completed"
                    await self._emit(
                        run_id,
                        RunEventType.SUBTASK_COMPLETED,
                        {
                            "subtask_id": subtask_result.subtask.id,
                            "output": subtask_result.final_output,
                            "tokens": attempt.usage.model_dump(mode="json"),
                            "cost_usd": attempt.cost_usd,
                            "baseline_cost_usd": attempt.baseline_cost_usd,
                            "latency_ms": attempt.latency_ms,
                            "run_stats": summarise_run_stats(result.subtask_results).model_dump(mode="json"),
                        },
                    )
                    return True

                self.escalation_manager.record_failure(
                    subtask_result.subtask.routing_hint.value,
                    route.tier,
                )

                if self.escalation_manager.should_retry_same_model(attempt_number, validation_failed=True) and not retry_same_model_used:
                    retry_same_model_used = True
                    await self._emit(
                        run_id,
                        RunEventType.SUBTASK_ESCALATED,
                        {
                            "subtask_id": subtask_result.subtask.id,
                            "action": "retry_same_model",
                            "reason": validation.reason,
                            "from_route": route.model_dump(mode="json"),
                            "to_route": route.model_dump(mode="json"),
                        },
                    )
                    continue

                if self.escalation_manager.can_escalate(route.tier) and not route.forced_by_budget:
                    new_route = self.router.escalate(route)
                    await self._emit_route_change(
                        run_id,
                        subtask_result.subtask,
                        route,
                        new_route,
                        action="tier_up",
                        reason=validation.reason,
                    )
                    route = new_route
                    subtask_result.route = new_route
                    subtask_result.escalations += 1
                    retry_same_model_used = False
                    provider_fallbacks = 0
                    continue

                subtask_result.status = "failed"
                result.error = (
                    f"Subtask {subtask_result.subtask.id} failed validation at Tier {route.tier}: "
                    f"{validation.reason}"
                )
                return False
            except Exception as exc:
                attempt.completed_at = utcnow_iso()
                attempt.error = str(exc)
                subtask_result.attempts.append(attempt)
                self.escalation_manager.record_failure(
                    subtask_result.subtask.routing_hint.value,
                    route.tier,
                )

                if not route.forced_by_budget and self.escalation_manager.should_switch_provider(provider_fallbacks):
                    new_route = self.router.alternate_provider(route)
                    await self._emit_route_change(
                        run_id,
                        subtask_result.subtask,
                        route,
                        new_route,
                        action="provider_fallback",
                        reason=str(exc),
                    )
                    route = new_route
                    subtask_result.route = new_route
                    subtask_result.escalations += 1
                    retry_same_model_used = False
                    provider_fallbacks += 1
                    continue

                if not route.forced_by_budget and self.escalation_manager.can_escalate(route.tier):
                    new_route = self.router.escalate(route)
                    await self._emit_route_change(
                        run_id,
                        subtask_result.subtask,
                        route,
                        new_route,
                        action="tier_up",
                        reason=str(exc),
                    )
                    route = new_route
                    subtask_result.route = new_route
                    subtask_result.escalations += 1
                    retry_same_model_used = False
                    provider_fallbacks = 0
                    continue

                subtask_result.status = "failed"
                result.error = f"Subtask {subtask_result.subtask.id} failed: {exc}"
                return False

    async def _emit_route_change(
        self,
        run_id: str,
        subtask: SubTask,
        previous: RouteDecision,
        current: RouteDecision,
        *,
        action: str,
        reason: str,
    ) -> None:
        await self._emit(
            run_id,
            RunEventType.SUBTASK_ESCALATED,
            {
                "subtask_id": subtask.id,
                "action": action,
                "reason": reason,
                "from_route": previous.model_dump(mode="json"),
                "to_route": current.model_dump(mode="json"),
            },
        )

    async def _emit(self, run_id: str, event_type: RunEventType, payload: dict) -> None:
        await self.event_hub.publish(
            run_id,
            RunEvent(
                event=event_type,
                run_id=run_id,
                payload=payload,
            ),
        )

    async def _compose_final_output(self, result: RunResult) -> str:
        first_output = await self.composer.compose(result.task, result.subtask_results)
        first_validation = await self.validator.validate(
            self._composer_validation_subtask(),
            first_output,
            RoutingHint.CREATIVE_SYNTHESIS,
            OutputFormat.MARKDOWN,
        )
        if first_validation.passed:
            return first_output

        revised_output = await self.composer.compose(
            result.task,
            result.subtask_results,
            revision_feedback=first_validation.reason,
        )
        revised_validation = await self.validator.validate(
            self._composer_validation_subtask(),
            revised_output,
            RoutingHint.CREATIVE_SYNTHESIS,
            OutputFormat.MARKDOWN,
        )
        if revised_validation.passed:
            return revised_output

        logger.warning(
            "Composer revision failed quality validation; using first attempt. First reason: %s. Retry reason: %s",
            first_validation.reason,
            revised_validation.reason,
        )
        return first_output

    def _timeout_for_tier(self, tier: int) -> float:
        return {
            1: self.settings.tier1_timeout_seconds,
            2: self.settings.tier2_timeout_seconds,
            3: self.settings.tier3_timeout_seconds,
        }.get(tier, self.settings.request_timeout_seconds)

    def _composer_validation_subtask(self) -> SubTask:
        return SubTask(
            id="composer",
            description="Final composed output",
            complexity=Complexity.MEDIUM,
            routing_hint=RoutingHint.CREATIVE_SYNTHESIS,
            output_format=OutputFormat.MARKDOWN,
        )

    def _build_subtask_system_prompt(self, subtask: SubTask) -> str:
        format_guidance = {
            "paragraph": "Return a tight paragraph unless more structure is clearly useful.",
            "list": "Return a concise flat list.",
            "json": "Return valid JSON only. No markdown fences or commentary.",
            "markdown": "Return clean markdown with short headings if helpful.",
        }[subtask.output_format.value]
        return (
            "You are a Tokenwise execution agent. Complete only the assigned subtask using the context provided. "
            f"{format_guidance}"
        )

    def _build_subtask_user_prompt(
        self,
        task: str,
        subtask: SubTask,
        completed_outputs: dict[str, str],
    ) -> str:
        dependency_context = []
        for dependency in subtask.depends_on:
            if dependency in completed_outputs:
                dependency_context.append(f"{dependency}:\n{completed_outputs[dependency]}")

        dependency_block = "\n\n".join(dependency_context) if dependency_context else "None"
        return (
            f"Original task:\n{task}\n\n"
            f"Assigned subtask ({subtask.id}): {subtask.description}\n"
            f"Expected output format: {subtask.output_format.value}\n"
            f"Routing hint: {subtask.routing_hint.value}\n\n"
            f"Completed dependency outputs:\n{dependency_block}"
        )
