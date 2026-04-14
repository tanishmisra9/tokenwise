# Tokenwise

Tokenwise is a full-stack orchestration app that turns one complex prompt into a dependency-aware execution plan, routes each subtask to a cost-conscious model tier, validates outputs, escalates when necessary, composes a final answer, and keeps a persistent cost/savings history.

The current product includes:

- A FastAPI backend with live WebSocket streaming
- A React + Vite frontend with a single-column execution dashboard
- OpenAI + Anthropic model routing
- SQLite-backed run history and savings analytics
- Rate limiting, run cancellation, and daily spend guards
- A comprehensive mocked test suite

## How Tokenwise works

At a high level, a run goes through these stages:

1. The orchestrator breaks a user task into 3 to 7 subtasks.
2. Each subtask gets an initial route based on complexity, quality floor, routing hint, and session-level escalation memory.
3. Dependency-free subtasks can run in parallel; dependent subtasks wait until prerequisites complete.
4. Each subtask is executed, validated, retried on transient issues, and escalated by provider or tier when needed.
5. When all subtasks finish, the composer produces a final answer.
6. The composed answer is validated once, optionally revised once, and then returned.
7. Run economics and historical savings are written to SQLite and surfaced to the frontend.

## Current frontend

The frontend is a React 19 + Vite app with a dark, single-column dashboard. It includes:

- A sticky hero bar with connection status and total savings
- A centered task composer with quality floor segmented control
- A live execution timeline with per-subtask status, tier, and escalation context
- A full-width composed response renderer with markdown + code block support
- A compact run economics strip
- Historical totals and routing-hint savings breakdown

The frontend uses `VITE_API_BASE_URL` when provided and otherwise defaults to `http://localhost:8000`.

## Repository structure

```text
tokenwise/
├── frontend/                     # React + Vite UI
├── tests/                        # Pytest suite
├── tokenwise/backend/agents/     # Orchestrator, validator, composer
├── tokenwise/backend/execution/  # OpenAI / Anthropic runner adapters
├── tokenwise/backend/router/     # Tier routing and escalation logic
├── tokenwise/backend/tracker/    # Cost + history tracking
├── tokenwise/backend/main.py     # FastAPI app entrypoint
├── tokenwise/backend/runtime.py  # Coordinator + event hub
└── tokenwise/backend/models/     # Shared schemas
```

## Requirements

- Python 3.11+
- Node.js 18+
- `uv`
- Valid `OPENAI_API_KEY`
- Valid `ANTHROPIC_API_KEY`

## Local setup

1. Copy the environment template:

```bash
cp .env.example .env
```

2. Add provider credentials to `.env`:

```bash
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

3. Install backend dependencies:

```bash
uv sync
```

4. Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

## Running locally

Start the backend:

```bash
uv run uvicorn tokenwise.backend.main:app --reload
```

Start the frontend in a second terminal:

```bash
cd frontend
npm run dev
```

Default local addresses:

- Backend: `http://127.0.0.1:8000`
- Frontend: `http://127.0.0.1:5173`

By default, run history is stored in `tokenwise.db` at the repo root.

## Environment variables

The most important settings are documented in [.env.example](/Users/tanishmisra/Code/Tokenwise/.env.example).

### Core app settings

- `TOKENWISE_API_HOST` default `127.0.0.1`
- `TOKENWISE_API_PORT` default `8000`
- `TOKENWISE_DB_PATH` default `tokenwise.db`
- `TOKENWISE_DAILY_BUDGET_USD` default `10.0`
- `TOKENWISE_MAX_TASK_LENGTH` default `2000`
- `TOKENWISE_MAX_CONCURRENT_RUNS` default `3`

### Timeout settings

- `TOKENWISE_TIER1_TIMEOUT` default `15`
- `TOKENWISE_TIER2_TIMEOUT` default `30`
- `TOKENWISE_TIER3_TIMEOUT` default `90`

### Max output token settings

- `TOKENWISE_TIER1_MAX_OUTPUT_TOKENS` default `1500`
- `TOKENWISE_TIER2_MAX_OUTPUT_TOKENS` default `2000`
- `TOKENWISE_TIER3_MAX_OUTPUT_TOKENS` default `4000`

### Model configuration

- `TOKENWISE_META_AGENT_PROVIDER` default `openai`
- `TOKENWISE_OPENAI_TIER1_MODEL_ID` default `gpt-4o-mini`
- `TOKENWISE_OPENAI_TIER2_MODEL_ID` default `gpt-4o`
- `TOKENWISE_OPENAI_TIER3_MODEL_ID` default `o1`
- `TOKENWISE_ANTHROPIC_TIER1_MODEL_ID` default `claude-3-5-haiku-20241022`
- `TOKENWISE_ANTHROPIC_TIER2_MODEL_ID` default `claude-sonnet-4-20250514`
- `TOKENWISE_ANTHROPIC_TIER3_MODEL_ID` default `claude-opus-4-1-20250805`

## Routing model

Tokenwise routes work by both complexity and task type.

### Complexity -> starting tier

- `low` -> Tier 1
- `medium` -> Tier 2
- `high` -> Tier 3

### Quality floor

Quality floor can raise the minimum tier:

- `low` -> no floor
- `medium` -> minimum Tier 2
- `high` -> minimum Tier 3

### Routing hint -> provider affinity

- `structured_output` -> Anthropic
- `instruction_following` -> Anthropic
- `creative_synthesis` -> Anthropic
- `code_generation` -> OpenAI
- `general_reasoning` -> OpenAI

### Escalation memory

The router also keeps in-memory failure counts for `(routing_hint, tier)` pairs during the current process lifetime. After repeated failures, it can start a similar subtask one tier higher on future runs in the same session.

## API surface

### `GET /health`

Simple healthcheck:

```json
{ "status": "ok" }
```

### `POST /run`

Starts a run and returns a WebSocket path.

Example request:

```json
{
  "task": "Compare Spotify and Apple Music over the next 5 years.",
  "quality_floor": "medium"
}
```

`budget_cap_usd` is still supported by the backend contract, but it defaults to `999.0` and the current frontend does not send it.

Example response:

```json
{
  "run_id": "run_1234567890",
  "ws_path": "/runs/run_1234567890"
}
```

### `GET /history`

Returns cumulative totals, recent runs, and routing-hint savings breakdown:

```json
{
  "total_runs": 12,
  "total_tokens": 48291,
  "total_spent_usd": 1.273,
  "total_saved_usd": 0.624,
  "avg_savings_pct": 37.42,
  "runs": [],
  "routing_hint_breakdown": {
    "general_reasoning": {
      "subtask_count": 18,
      "avg_savings_pct": 41.13
    }
  }
}
```

### `DELETE /runs/{run_id}`

Requests cooperative cancellation of an in-flight run:

```json
{ "cancelled": true }
```

Returns `404` if the run ID is unknown.

### `WS /runs/{run_id}`

Streams live event envelopes:

```json
{
  "event": "subtask_started",
  "run_id": "run_123",
  "timestamp": "2026-04-13T21:52:00.000000+00:00",
  "payload": {}
}
```

Current event types:

- `run_started`
- `plan_ready`
- `subtask_started`
- `subtask_escalated`
- `subtask_completed`
- `run_completed`
- `run_failed`

If a client connects to an unknown run ID, the socket is accepted and then closed with code `4404`.

## Runtime behavior and safeguards

### Parallel execution

Independent subtasks run in parallel batches. Subtasks with dependencies wait until prerequisite outputs are available.

### Validation

Subtask outputs are validated with a dedicated validator agent. Structured output uses special handling:

- `json` expects parseable JSON
- `list` accepts bullets, numbered lists, or plain line-separated items

### Retries and escalation

Tokenwise can:

- retry transient empty model responses once inside the runner
- retry the same model once after some validation failures
- switch providers
- escalate from Tier 1 -> 2 -> 3

### Budget lock

If a run exceeds its budget cap, remaining unstarted subtasks are forced to Tier 1 and marked as degraded completions rather than failing the run purely on quality.

### Cancellation

Cancellation is cooperative. Active provider calls are not force-killed mid-request; the coordinator stops at the next safe execution boundary and emits `run_failed` with `Run cancelled by user`.

### History retention

The event hub keeps closed run streams in memory for a short TTL and cleans them up in the background. Run history itself is persisted in SQLite.

## Rate limits and guards

The FastAPI app currently enforces:

- `POST /run`: `10/minute` per IP
- `GET /history`: `30/minute` per IP
- `DELETE /runs/{run_id}`: `20/minute` per IP

It also blocks new runs when:

- total spend started today in UTC reaches `TOKENWISE_DAILY_BUDGET_USD`
- concurrent in-flight runs reach `TOKENWISE_MAX_CONCURRENT_RUNS`
- task length exceeds `TOKENWISE_MAX_TASK_LENGTH`

## Testing

Run the backend test suite:

```bash
uv run pytest --tb=short
```

Run the frontend production build:

```bash
cd frontend
npm run build
```

The test suite uses mocked LLM calls. It does not make real OpenAI or Anthropic API requests.

## Notes

- The frontend uses `lucide-react` for a few small UI controls like copy actions.
- The backend requires both provider keys by default at app startup unless you construct `create_app(..., validate_provider_keys=False)` for tests.
- History statistics include completed and failed runs.
- Savings are tracked against a Tier 3 baseline.
