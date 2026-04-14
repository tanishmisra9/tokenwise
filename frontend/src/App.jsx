import { startTransition, useEffect, useRef, useState } from "react";
import AgentGraph from "./components/AgentGraph";
import HistoryPanel from "./components/HistoryPanel";
import ResultOutput from "./components/ResultOutput";
import RunStats from "./components/RunStats";
import TaskInput from "./components/TaskInput";

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

function formatConnectionLabel(status) {
  if (status === "streaming") return "Connected";
  if (status === "starting") return "Starting";
  if (status === "error") return "Error";
  if (status === "closed") return "Closed";
  return "Idle";
}

function markFailedSubtask(subtasks) {
  const entries = Object.entries(subtasks ?? {});
  if (!entries.length) {
    return subtasks;
  }

  const runningEntry = entries.find(([, subtask]) => subtask.status === "running");
  const fallbackEntry =
    [...entries]
      .filter(
        ([, subtask]) =>
          !["completed", "completed_degraded"].includes(subtask.status) &&
          (subtask.attempts?.length ?? 0) > 0,
      )
      .sort(([, left], [, right]) => {
        const leftAttempt = left.attempts?.at(-1)?.attempt_number ?? -1;
        const rightAttempt = right.attempts?.at(-1)?.attempt_number ?? -1;
        return rightAttempt - leftAttempt;
      })[0] ?? null;

  const failedEntry = runningEntry ?? fallbackEntry;
  if (!failedEntry) {
    return subtasks;
  }

  const [subtaskId, failedSubtask] = failedEntry;
  return {
    ...subtasks,
    [subtaskId]: {
      ...failedSubtask,
      status: "failed",
      attempts: (failedSubtask.attempts ?? []).map((attempt, index, attempts) =>
        index === attempts.length - 1
          ? {
              ...attempt,
              status: "failed",
            }
          : attempt,
      ),
    },
  };
}

function buildWebSocketUrl(runId) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/runs/${runId}`;
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
    next.subtasks[subtaskId].status = payload.degraded ? "completed_degraded" : "completed";
    next.subtasks[subtaskId].output = payload.output;
    next.subtasks[subtaskId].lastTokens = payload.tokens;
    next.subtasks[subtaskId].lastCost = payload.cost_usd;
    next.subtasks[subtaskId].lastLatency = payload.latency_ms;
    next.subtasks[subtaskId].attempts = (next.subtasks[subtaskId].attempts ?? []).map((attempt, index, attempts) =>
      index === attempts.length - 1
        ? {
            ...attempt,
            status: payload.degraded ? "completed_degraded" : "completed",
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
    next.subtasks = markFailedSubtask(next.subtasks);
    next.runStats = payload.run_stats ?? null;
    next.historyStats = payload.history_stats ?? null;
    return next;
  }

  return next;
}

export default function App() {
  const [form, setForm] = useState({
    task: "",
    quality_floor: "medium",
  });
  const [history, setHistory] = useState({
    total_runs: 0,
    total_tokens: 0,
    total_spent_usd: 0,
    total_saved_usd: 0,
    avg_savings_pct: 0,
    runs: [],
    routing_hint_breakdown: {},
  });
  const [currentRun, setCurrentRun] = useState(initialRunState);
  const [connectionStatus, setConnectionStatus] = useState("idle");
  const [error, setError] = useState("");
  const socketRef = useRef(null);
  const showLiveExecution =
    (currentRun.plan?.length ?? 0) > 0 && ["starting", "running"].includes(currentRun.status);

  useEffect(() => {
    let cancelled = false;

    async function loadHistory() {
      try {
        const response = await fetch("/history");
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
      const response = await fetch("/run", {
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
      const response = await fetch("/history");
      const payload = await response.json();
      startTransition(() => setHistory(payload));
    } catch (fetchError) {
      setError(fetchError.message);
    }
  }

  return (
    <div className="app-shell">
      <div className="backdrop-grid" />
      <header className="hero-bar">
        <div className="hero-inner">
          <div className="brand-wordmark" aria-label="Tokenwise">
            <span className="brand-text">TOKENWISE</span>
            <span className="brand-dot" aria-hidden="true" />
          </div>
          <div className="hero-status">
            <div className={`connection-pill state-${connectionStatus}`}>
              <span className="connection-pill-dot" aria-hidden="true" />
              <span>{formatConnectionLabel(connectionStatus)}</span>
            </div>
            <div className="saved-counter">
              <span>Total saved</span>
              <strong>${Number(history.total_saved_usd ?? 0).toFixed(4)}</strong>
            </div>
          </div>
        </div>
      </header>

      {error ? (
        <div className="content-frame">
          <div className="alert-banner">{error}</div>
        </div>
      ) : null}

      <main className="dashboard-flow">
        <section className="content-section panel panel-input">
          <TaskInput
            form={form}
            setForm={setForm}
            onSubmit={handleRunSubmit}
            isRunning={currentRun.status === "running" || currentRun.status === "starting"}
          />
        </section>

        {showLiveExecution ? (
          <section className="content-section panel panel-agent">
            <AgentGraph run={currentRun} />
          </section>
        ) : null}

        <section className="content-section panel panel-result">
          <ResultOutput run={currentRun} />
        </section>

        <section className="content-section panel panel-stats">
          <RunStats run={currentRun} />
        </section>

        <section className="content-section panel panel-history">
          <HistoryPanel history={history} />
        </section>
      </main>
    </div>
  );
}
