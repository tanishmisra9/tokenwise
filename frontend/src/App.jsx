import { startTransition, useEffect, useRef, useState } from "react";
import AgentGraph from "./components/AgentGraph";
import HistoryPanel from "./components/HistoryPanel";
import ResultOutput from "./components/ResultOutput";
import RunStats from "./components/RunStats";
import TaskInput from "./components/TaskInput";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const initialRunState = {
  runId: null,
  status: "idle",
  plan: [],
  routes: {},
  subtasks: {},
  finalOutput: "",
  runStats: null,
  historyStats: null,
  error: "",
};

function buildWebSocketUrl(runId) {
  const url = new URL(`/runs/${runId}`, API_BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function eventReducer(previous, event) {
  const next = { ...previous };
  const payload = event.payload ?? {};

  if (event.event === "run_started") {
    next.runId = event.run_id;
    next.status = "running";
    next.error = "";
    return next;
  }

  if (event.event === "plan_ready") {
    next.plan = payload.subtasks ?? [];
    next.routes = Object.fromEntries((payload.routes ?? []).map((route) => [route.subtask_id, route]));

    const subtasks = {};
    for (const subtask of payload.subtasks ?? []) {
      subtasks[subtask.id] = {
        ...subtask,
        status: "queued",
        attempts: [],
        route: (payload.routes ?? []).find((route) => route.subtask_id === subtask.id) ?? null,
        events: [],
        output: "",
      };
    }
    next.subtasks = subtasks;
    return next;
  }

  const subtaskId = payload.subtask_id;
  if (subtaskId && next.subtasks[subtaskId]) {
    next.subtasks = {
      ...next.subtasks,
      [subtaskId]: {
        ...next.subtasks[subtaskId],
      },
    };
  }

  if (event.event === "subtask_started" && subtaskId) {
    next.subtasks[subtaskId].status = "running";
    next.subtasks[subtaskId].activeAttempt = payload.attempt_number;
    next.subtasks[subtaskId].route = {
      provider: payload.provider,
      model_id: payload.model,
      tier: payload.tier,
      forced_by_budget: payload.forced_by_budget,
    };
    next.subtasks[subtaskId].attempts = [
      ...(next.subtasks[subtaskId].attempts ?? []),
      {
        attempt_number: payload.attempt_number,
        provider: payload.provider,
        model_id: payload.model,
        tier: payload.tier,
        status: "running",
      },
    ];
    return next;
  }

  if (event.event === "subtask_escalated" && subtaskId) {
    next.subtasks[subtaskId].events = [
      ...(next.subtasks[subtaskId].events ?? []),
      {
        action: payload.action,
        reason: payload.reason,
        to_route: payload.to_route,
      },
    ];
    if (payload.to_route) {
      next.subtasks[subtaskId].route = payload.to_route;
    }
    return next;
  }

  if (event.event === "subtask_completed" && subtaskId) {
    next.subtasks[subtaskId].status = "completed";
    next.subtasks[subtaskId].output = payload.output;
    next.subtasks[subtaskId].lastTokens = payload.tokens;
    next.subtasks[subtaskId].lastCost = payload.cost_usd;
    next.subtasks[subtaskId].lastLatency = payload.latency_ms;
    next.subtasks[subtaskId].attempts = (next.subtasks[subtaskId].attempts ?? []).map((attempt, index, attempts) =>
      index === attempts.length - 1
        ? {
            ...attempt,
            status: "completed",
            cost_usd: payload.cost_usd,
            latency_ms: payload.latency_ms,
            tokens: payload.tokens,
          }
        : attempt,
    );
    next.runStats = payload.run_stats ?? next.runStats;
    return next;
  }

  if (event.event === "run_completed") {
    next.status = "completed";
    next.finalOutput = payload.final_output ?? "";
    next.runStats = payload.run_stats ?? null;
    next.historyStats = payload.history_stats ?? null;
    return next;
  }

  if (event.event === "run_failed") {
    next.status = "failed";
    next.error = payload.error ?? "Run failed.";
    next.runStats = payload.run_stats ?? null;
    next.historyStats = payload.history_stats ?? null;
    return next;
  }

  return next;
}

export default function App() {
  const [form, setForm] = useState({
    task: "",
    budget_cap_usd: 0.05,
    quality_floor: "medium",
  });
  const [history, setHistory] = useState({
    total_runs: 0,
    total_tokens: 0,
    total_spent_usd: 0,
    total_saved_usd: 0,
    avg_savings_pct: 0,
    runs: [],
  });
  const [currentRun, setCurrentRun] = useState(initialRunState);
  const [connectionStatus, setConnectionStatus] = useState("idle");
  const [error, setError] = useState("");
  const socketRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    async function loadHistory() {
      try {
        const response = await fetch(`${API_BASE_URL}/history`);
        const payload = await response.json();
        if (!cancelled) {
          startTransition(() => setHistory(payload));
        }
      } catch (fetchError) {
        if (!cancelled) {
          setError(fetchError.message);
        }
      }
    }

    loadHistory();
    return () => {
      cancelled = true;
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, []);

  async function handleRunSubmit(event) {
    event.preventDefault();
    setError("");
    setConnectionStatus("starting");
    setCurrentRun({ ...initialRunState, status: "starting" });

    try {
      const response = await fetch(`${API_BASE_URL}/run`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(form),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail ?? "Unable to start run.");
      }

      const payload = await response.json();
      const socket = new WebSocket(buildWebSocketUrl(payload.run_id));
      socketRef.current = socket;
      setCurrentRun({ ...initialRunState, runId: payload.run_id, status: "starting" });

      socket.onopen = () => setConnectionStatus("streaming");
      socket.onerror = () => setConnectionStatus("error");
      socket.onclose = () => {
        setConnectionStatus((status) => (status === "error" ? status : "closed"));
      };
      socket.onmessage = (message) => {
        const parsed = JSON.parse(message.data);
        startTransition(() => {
          setCurrentRun((previous) => eventReducer(previous, parsed));
        });

        if (parsed.event === "run_completed" || parsed.event === "run_failed") {
          if (parsed.payload?.history_stats) {
            startTransition(() => {
              setHistory((previous) => ({
                ...previous,
                ...parsed.payload.history_stats,
                runs: previous.runs,
              }));
            });
          }
          refreshHistory();
        }
      };
    } catch (submitError) {
      setError(submitError.message);
      setConnectionStatus("error");
      setCurrentRun({ ...initialRunState, status: "failed", error: submitError.message });
    }
  }

  async function refreshHistory() {
    try {
      const response = await fetch(`${API_BASE_URL}/history`);
      const payload = await response.json();
      startTransition(() => setHistory(payload));
    } catch (fetchError) {
      setError(fetchError.message);
    }
  }

  return (
    <div className="app-shell">
      <div className="backdrop-grid" />
      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Tokenwise orchestration MVP</p>
          <h1>Route complex work through the cheapest model that can actually finish it.</h1>
          <p className="lede">
            Live decomposition, tier-aware retries, validator-driven escalation, and a running
            savings ledger in one dashboard.
          </p>
        </div>
        <div className="hero-metrics">
          <div className="metric-chip">
            <span>Connection</span>
            <strong>{connectionStatus}</strong>
          </div>
          <div className="metric-chip">
            <span>Total saved</span>
            <strong>${Number(history.total_saved_usd ?? 0).toFixed(4)}</strong>
          </div>
        </div>
      </header>

      {error ? <div className="alert-banner">{error}</div> : null}

      <main className="dashboard-grid">
        <section className="panel panel-input">
          <TaskInput form={form} setForm={setForm} onSubmit={handleRunSubmit} isRunning={currentRun.status === "running" || currentRun.status === "starting"} />
        </section>

        <section className="panel panel-agent">
          <AgentGraph run={currentRun} />
        </section>

        <section className="panel panel-result">
          <ResultOutput run={currentRun} />
        </section>

        <section className="panel panel-stats">
          <RunStats runStats={currentRun.runStats} />
        </section>

        <section className="panel panel-history">
          <HistoryPanel history={history} />
        </section>
      </main>
    </div>
  );
}
