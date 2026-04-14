from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from limits import parse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.wrappers import Limit

from tokenwise.backend.main import create_app
from tokenwise.backend.models.schemas import RunResult, RunStats, utcnow_iso
from tokenwise.backend.runtime import TokenwiseCoordinator


def rate_limit_error(limit_value: str) -> RateLimitExceeded:
    return RateLimitExceeded(
        Limit(
            parse(limit_value),
            get_remote_address,
            None,
            False,
            None,
            None,
            None,
            1,
            True,
        )
    )


def make_coordinator(mock_settings):
    settings = mock_settings()
    coordinator = TokenwiseCoordinator(settings=settings)
    coordinator.run = AsyncMock(return_value=None)
    return settings, coordinator


def test_post_run_returns_429_after_ten_requests_when_limiter_is_mocked(mock_settings):
    settings, coordinator = make_coordinator(mock_settings)
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)
    call_count = {"count": 0}

    def fake_check(request, endpoint_func=None, in_middleware=True):
        if request.url.path == "/run":
            request.state.view_rate_limit = None
            call_count["count"] += 1
            if call_count["count"] > 10:
                raise rate_limit_error("10/minute")

    with patch.object(app.state.limiter, "_check_request_limit", side_effect=fake_check):
        with TestClient(app) as client:
            for _ in range(10):
                response = client.post("/run", json={"task": "Task", "quality_floor": "medium"})
                assert response.status_code == 202

            eleventh = client.post("/run", json={"task": "Task", "quality_floor": "medium"})

    assert eleventh.status_code == 429


def test_post_run_returns_429_when_daily_spend_exceeds_limit(mock_settings):
    settings = mock_settings(daily_budget_usd=0.01)
    coordinator = TokenwiseCoordinator(settings=settings)
    coordinator.run = AsyncMock(return_value=None)
    coordinator.history_store.write_run(
        RunResult(
            run_id="run_spent",
            task="Spent already",
            started_at=utcnow_iso(),
            completed_at=utcnow_iso(),
            status="completed",
            budget_cap_usd=999.0,
            run_stats=RunStats(actual_cost_usd=0.02),
        )
    )
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)

    with TestClient(app) as client:
        response = client.post("/run", json={"task": "Task", "quality_floor": "medium"})

    assert response.status_code == 429
    assert response.json()["detail"] == "Daily spend limit reached"


def test_post_run_returns_429_when_three_runs_are_already_in_flight(mock_settings):
    settings = mock_settings(max_concurrent_runs=3)
    coordinator = TokenwiseCoordinator(settings=settings)

    async def slow_run(run_id, request):
        await asyncio.sleep(0.15)

    coordinator.run = AsyncMock(side_effect=slow_run)
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)

    with TestClient(app) as client:
        first = client.post("/run", json={"task": "Task 1", "quality_floor": "medium"})
        second = client.post("/run", json={"task": "Task 2", "quality_floor": "medium"})
        third = client.post("/run", json={"task": "Task 3", "quality_floor": "medium"})
        fourth = client.post("/run", json={"task": "Task 4", "quality_floor": "medium"})
        time.sleep(0.2)

    assert first.status_code == 202
    assert second.status_code == 202
    assert third.status_code == 202
    assert fourth.status_code == 429
    assert fourth.json()["detail"] == "Too many concurrent runs"


def test_post_run_returns_422_when_task_exceeds_max_length(mock_settings):
    settings = mock_settings(max_task_length=2000)
    coordinator = TokenwiseCoordinator(settings=settings)
    coordinator.run = AsyncMock(return_value=None)
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)

    with TestClient(app) as client:
        response = client.post("/run", json={"task": "x" * 2001, "quality_floor": "medium"})

    assert response.status_code == 422


def test_delete_unknown_run_id_returns_404(mock_settings):
    settings, coordinator = make_coordinator(mock_settings)
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)

    with TestClient(app) as client:
        response = client.delete("/runs/run_missing")

    assert response.status_code == 404


def test_delete_known_run_returns_200_and_sets_cancelled_flag(mock_settings):
    settings, coordinator = make_coordinator(mock_settings)
    coordinator.event_hub.ensure_run("run_known")
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)

    with TestClient(app) as client:
        response = client.delete("/runs/run_known")

    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert coordinator.event_hub.is_cancelled("run_known") is True
