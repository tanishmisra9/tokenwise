from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if validate_provider_keys:
            resolved_settings.require_provider_keys()
        yield

    app = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.coordinator = resolved_coordinator
    app.state.background_tasks = set()

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
    async def create_run(request: RunRequest):
        run_id = app.state.coordinator.new_run_id()
        task = asyncio.create_task(app.state.coordinator.run(run_id, request))
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)
        return RunAcceptedResponse(run_id=run_id, ws_path=f"/runs/{run_id}")

    @app.get("/history")
    async def get_history():
        return await app.state.coordinator.history_response()

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
                await websocket.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            app.state.coordinator.event_hub.unsubscribe(run_id, queue)
        finally:
            app.state.coordinator.event_hub.unsubscribe(run_id, queue)

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "tokenwise.backend.main:app",
        host=Settings().api_host,
        port=Settings().api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
