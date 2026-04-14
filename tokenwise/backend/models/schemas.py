from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from enum import Enum
import os
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Provider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OutputFormat(str, Enum):
    PARAGRAPH = "paragraph"
    LIST = "list"
    JSON = "json"
    MARKDOWN = "markdown"


class RoutingHint(str, Enum):
    GENERAL_REASONING = "general_reasoning"
    STRUCTURED_OUTPUT = "structured_output"
    INSTRUCTION_FOLLOWING = "instruction_following"
    CREATIVE_SYNTHESIS = "creative_synthesis"
    CODE_GENERATION = "code_generation"


class QualityFloor(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunEventType(str, Enum):
    RUN_STARTED = "run_started"
    PLAN_READY = "plan_ready"
    SUBTASK_STARTED = "subtask_started"
    SUBTASK_COMPLETED = "subtask_completed"
    SUBTASK_ESCALATED = "subtask_escalated"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class TokenUsage(BaseModel):
    input: int = 0
    output: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output


class TokenPricing(BaseModel):
    input_per_million: float
    output_per_million: float


class ModelProfile(BaseModel):
    alias: str
    display_name: str
    provider: Provider
    tier: int
    model_id: str
    pricing: TokenPricing
    capability_flags: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    tier: int
    provider: Provider
    model_alias: str
    model_id: str
    routing_reason: str
    forced_by_budget: bool = False


class SubTask(BaseModel):
    id: str
    description: str
    complexity: Complexity
    depends_on: list[str] = Field(default_factory=list)
    output_format: OutputFormat = OutputFormat.PARAGRAPH
    routing_hint: RoutingHint = RoutingHint.GENERAL_REASONING


class ExecutionPlan(BaseModel):
    subtasks: list[SubTask]

    @model_validator(mode="after")
    def validate_plan(self) -> "ExecutionPlan":
        if not 3 <= len(self.subtasks) <= 7:
            raise ValueError("Execution plans must contain between 3 and 7 subtasks.")

        ids = {subtask.id for subtask in self.subtasks}
        if len(ids) != len(self.subtasks):
            raise ValueError("Subtask IDs must be unique.")

        for subtask in self.subtasks:
            missing = [dependency for dependency in subtask.depends_on if dependency not in ids]
            if missing:
                raise ValueError(f"Subtask {subtask.id} depends on unknown tasks: {missing}")
            if subtask.id in subtask.depends_on:
                raise ValueError(f"Subtask {subtask.id} cannot depend on itself.")

        self.topological_order()
        return self

    def topological_order(self) -> list[SubTask]:
        incoming: dict[str, int] = {subtask.id: len(subtask.depends_on) for subtask in self.subtasks}
        outgoing: dict[str, list[str]] = defaultdict(list)
        task_map = {subtask.id: subtask for subtask in self.subtasks}

        for subtask in self.subtasks:
            for dependency in subtask.depends_on:
                outgoing[dependency].append(subtask.id)

        queue = deque([task_id for task_id, count in incoming.items() if count == 0])
        ordered_ids: list[str] = []

        while queue:
            current = queue.popleft()
            ordered_ids.append(current)
            for downstream in outgoing[current]:
                incoming[downstream] -= 1
                if incoming[downstream] == 0:
                    queue.append(downstream)

        if len(ordered_ids) != len(self.subtasks):
            raise ValueError("Execution plan contains a dependency cycle.")

        return [task_map[task_id] for task_id in ordered_ids]


class RunRequest(BaseModel):
    task: str = Field(min_length=1)
    budget_cap_usd: float = Field(default=999.0, gt=0)
    quality_floor: QualityFloor = QualityFloor.MEDIUM

    @field_validator("task")
    @classmethod
    def validate_task_length(cls, value: str) -> str:
        max_task_length = int(os.getenv("TOKENWISE_MAX_TASK_LENGTH", "2000"))
        if len(value) > max_task_length:
            raise ValueError(f"Task must be {max_task_length} characters or fewer.")
        return value


class RunAcceptedResponse(BaseModel):
    run_id: str
    ws_path: str


class ValidationResult(BaseModel):
    passed: bool
    reason: str = ""


class LLMResponse(BaseModel):
    output_text: str
    usage: TokenUsage
    latency_ms: int


class SubTaskAttempt(BaseModel):
    attempt_number: int
    tier: int
    provider: Provider
    model_alias: str
    model_id: str
    started_at: str = Field(default_factory=utcnow_iso)
    completed_at: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0
    latency_ms: int = 0
    output_text: str = ""
    validation: ValidationResult | None = None
    error: str | None = None


class SubTaskResult(BaseModel):
    subtask: SubTask
    route: RouteDecision
    attempts: list[SubTaskAttempt] = Field(default_factory=list)
    final_output: str | None = None
    status: str = "pending"
    escalations: int = 0


class RunStats(BaseModel):
    tokens_used: int = 0
    actual_cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0
    saved_usd: float = 0.0
    savings_pct: float = 0.0
    models_used: dict[str, int] = Field(default_factory=dict)
    escalations: int = 0


class HistoryRunSummary(BaseModel):
    run_id: str
    task_preview: str
    status: str
    created_at: str
    actual_cost_usd: float
    saved_usd: float
    savings_pct: float
    tokens_used: int
    escalations: int


class HistoryStats(BaseModel):
    total_runs: int = 0
    total_tokens: int = 0
    total_spent_usd: float = 0.0
    total_saved_usd: float = 0.0
    avg_savings_pct: float = 0.0


class HistoryResponse(BaseModel):
    total_runs: int
    total_tokens: int
    total_spent_usd: float
    total_saved_usd: float
    avg_savings_pct: float
    runs: list[HistoryRunSummary]
    routing_hint_breakdown: dict[str, dict[str, float | int]] = Field(default_factory=dict)


class RunResult(BaseModel):
    run_id: str
    task: str
    started_at: str
    completed_at: str | None = None
    status: str = "running"
    budget_cap_usd: float
    budget_locked: bool = False
    plan: ExecutionPlan | None = None
    subtask_results: list[SubTaskResult] = Field(default_factory=list)
    final_output: str | None = None
    run_stats: RunStats = Field(default_factory=RunStats)
    history_stats: HistoryStats | None = None
    error: str | None = None


class RunEvent(BaseModel):
    event: RunEventType
    run_id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    payload: dict[str, Any] = Field(default_factory=dict)
