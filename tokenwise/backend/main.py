from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.extension import _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

from tokenwise.backend.config import Settings
from tokenwise.backend.models.schemas import RunAcceptedResponse, RunRequest
from tokenwise.backend.runtime import TokenwiseCoordinator


def create_app(
    *,
    settings: Settings | None = None,
    coordinator: TokenwiseCoordinator | None = None,
    validate_provider_keys: bool = True,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_coordinator = coordinator or TokenwiseCoordinator(settings=resolved_settings)
    limiter = Limiter(key_func=get_remote_address)

    async def cleanup_loop() -> None:
        while True:
            await resolved_coordinator.event_hub.cleanup_expired()
            await asyncio.sleep(60)

    async def run_with_release(run_id: str, run_request: RunRequest) -> None:
        try:
            await resolved_coordinator.run(run_id, run_request)
        finally:
            app.state.run_semaphore.release()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if validate_provider_keys:
            resolved_settings.require_provider_keys()
        cleanup_task = asyncio.create_task(cleanup_loop())
        app.state.cleanup_task = cleanup_task
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

    app = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.coordinator = resolved_coordinator
    app.state.background_tasks = set()
    app.state.run_semaphore = asyncio.Semaphore(resolved_settings.max_concurrent_runs)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def healthcheck():
        return {"status": "ok"}

    @app.post("/run", response_model=RunAcceptedResponse, status_code=202)
    @limiter.limit("10/minute")
    async def create_run(request: Request, run_request: RunRequest):
        spent_today = app.state.coordinator.history_store.get_started_today_spend_utc()
        if spent_today >= resolved_settings.daily_budget_usd:
            raise HTTPException(status_code=429, detail="Daily spend limit reached")

        run_id = app.state.coordinator.new_run_id()
        try:
            await asyncio.wait_for(app.state.run_semaphore.acquire(), timeout=0)
        except asyncio.TimeoutError:
            if app.state.run_semaphore.locked():
                raise HTTPException(status_code=429, detail="Too many concurrent runs") from None
            await app.state.run_semaphore.acquire()
        app.state.coordinator.event_hub.ensure_run(run_id)
        task = asyncio.create_task(run_with_release(run_id, run_request))
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)
        return RunAcceptedResponse(run_id=run_id, ws_path=f"/runs/{run_id}")

    @app.get("/history")
    @limiter.limit("30/minute")
    async def get_history(request: Request):
        return await app.state.coordinator.history_response()

    @app.delete("/runs/{run_id}")
    @limiter.limit("20/minute")
    async def cancel_run(request: Request, run_id: str):
        if not app.state.coordinator.event_hub.has_run(run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        app.state.coordinator.event_hub.cancel(run_id)
        return {"cancelled": True}

    @app.websocket("/runs/{run_id}")
    async def run_stream(websocket: WebSocket, run_id: str):
        if not app.state.coordinator.event_hub.has_run(run_id):
            await websocket.accept()
            await websocket.close(code=4404, reason="Unknown run ID.")
            return

        await websocket.accept()
        backlog, queue, closed = app.state.coordinator.event_hub.subscribe(run_id)

        try:
            for event in backlog:
                await websocket.send_json(event.model_dump(mode="json"))
            if closed:
                return

            while True:
                if app.state.coordinator.event_hub.is_closed(run_id) and queue.empty():
                    break
                event = await queue.get()
                if event is None:
                    if app.state.coordinator.event_hub.is_closed(run_id):
                        break
                    continue
                await websocket.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            app.state.coordinator.event_hub.unsubscribe(run_id, queue)
        finally:
            app.state.coordinator.event_hub.unsubscribe(run_id, queue)

    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "tokenwise.backend.main:app",
        host=Settings().api_host,
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
    )


if __name__ == "__main__":
    main()
