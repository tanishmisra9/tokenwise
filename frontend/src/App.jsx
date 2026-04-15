import { startTransition, useEffect, useRef, useState } from "react";
import AgentGraph from "./components/AgentGraph";
import HistoryPanel from "./components/HistoryPanel";
import ResultOutput from "./components/ResultOutput";
import RunStats from "./components/RunStats";
import TaskInput from "./components/TaskInput";

const RECONNECT_DELAY_MS = 2000;
const MAX_RECONNECT_ATTEMPTS = 5;
const LEDGER_REVEAL_DELAY_MS = 150;

function isTerminalStatus(status) {
  return ["completed", "failed", "cancelled"].includes(status);
}

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
  if (status === "reconnecting") return "Reconnecting...";
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
    next.error = "";
    return next;
  }

  if (event.event === "run_failed") {
    if (payload.error === "Run cancelled by user") {
      next.status = "cancelled";
      next.error = "";
      next.finalOutput = "";
      next.runStats = null;
      next.historyStats = payload.history_stats ?? null;
      return next;
    }

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
  const [liveExecutionVisible, setLiveExecutionVisible] = useState(false);
  const [resultVisible, setResultVisible] = useState(false);
  const [statsVisible, setStatsVisible] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const statsRevealTimeoutRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const activeRunIdRef = useRef(null);
  const terminalEventReceivedRef = useRef(false);
  const socketGenerationRef = useRef(0);
  const hasStartedSubtask = Object.values(currentRun.subtasks ?? {}).some(
    (subtask) => (subtask.attempts?.length ?? 0) > 0,
  );
  const isRunBusy = currentRun.status !== "idle" && !isTerminalStatus(currentRun.status);
  const canStop = Boolean(currentRun.runId) && !isTerminalStatus(currentRun.status);
  const showLiveExecution = liveExecutionVisible && (currentRun.plan?.length ?? 0) > 0;

  function clearStatsRevealTimeout() {
    if (statsRevealTimeoutRef.current) {
      window.clearTimeout(statsRevealTimeoutRef.current);
      statsRevealTimeoutRef.current = null;
    }
  }

  function clearReconnectTimeout() {
    if (reconnectTimeoutRef.current) {
      window.clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }

  function closeSocket() {
    clearReconnectTimeout();
    socketGenerationRef.current += 1;

    if (!socketRef.current) {
      return;
    }

    const socket = socketRef.current;
    socketRef.current = null;
    socket.onopen = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;

    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close();
    }
  }

  useEffect(() => {
    if (hasStartedSubtask) {
      setLiveExecutionVisible(true);
    }
  }, [hasStartedSubtask]);

  useEffect(() => {
    clearStatsRevealTimeout();

    if (currentRun.status === "completed" && currentRun.finalOutput) {
      setResultVisible(true);
      statsRevealTimeoutRef.current = window.setTimeout(() => {
        setStatsVisible(true);
      }, LEDGER_REVEAL_DELAY_MS);
      return () => clearStatsRevealTimeout();
    }

    setResultVisible(false);
    setStatsVisible(false);
    return () => clearStatsRevealTimeout();
  }, [currentRun.status, currentRun.finalOutput]);

  function scheduleReconnect(runId) {
    if (terminalEventReceivedRef.current) {
      return;
    }

    if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
      setConnectionStatus("error");
      return;
    }

    reconnectAttemptsRef.current += 1;
    setConnectionStatus("reconnecting");
    clearReconnectTimeout();
    reconnectTimeoutRef.current = window.setTimeout(() => {
      reconnectTimeoutRef.current = null;
      if (terminalEventReceivedRef.current || activeRunIdRef.current !== runId) {
        return;
      }
      connectToRun(runId);
    }, RECONNECT_DELAY_MS);
  }

  function connectToRun(runId) {
    clearReconnectTimeout();
    activeRunIdRef.current = runId;

    const generation = socketGenerationRef.current + 1;
    socketGenerationRef.current = generation;

    if (socketRef.current) {
      const previousSocket = socketRef.current;
      socketRef.current = null;
      previousSocket.onopen = null;
      previousSocket.onclose = null;
      previousSocket.onerror = null;
      previousSocket.onmessage = null;
      if (previousSocket.readyState === WebSocket.OPEN || previousSocket.readyState === WebSocket.CONNECTING) {
        previousSocket.close();
      }
    }

    const socket = new WebSocket(buildWebSocketUrl(runId));
    socketRef.current = socket;

    socket.onopen = () => {
      if (socketGenerationRef.current !== generation) {
        return;
      }
      reconnectAttemptsRef.current = 0;
      clearReconnectTimeout();
      setConnectionStatus("streaming");
    };

    socket.onerror = () => {
      if (socketGenerationRef.current !== generation || terminalEventReceivedRef.current) {
        return;
      }
      setConnectionStatus("reconnecting");
    };

    socket.onclose = () => {
      if (socketGenerationRef.current !== generation) {
        return;
      }

      socketRef.current = null;

      if (terminalEventReceivedRef.current) {
        setConnectionStatus("closed");
        return;
      }

      scheduleReconnect(runId);
    };

    socket.onmessage = (message) => {
      if (socketGenerationRef.current !== generation) {
        return;
      }

      const parsed = JSON.parse(message.data);
      if (parsed.event === "ping") {
        return;
      }

      if (parsed.event === "run_completed" || parsed.event === "run_failed") {
        terminalEventReceivedRef.current = true;
        reconnectAttemptsRef.current = 0;
        clearReconnectTimeout();
      }

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
  }

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
      clearStatsRevealTimeout();
      closeSocket();
    };
  }, []);

  async function handleRunSubmit(event) {
    event.preventDefault();
    setError("");
    setIsStopping(false);
    closeSocket();
    clearStatsRevealTimeout();
    reconnectAttemptsRef.current = 0;
    terminalEventReceivedRef.current = false;
    activeRunIdRef.current = null;
    setLiveExecutionVisible(false);
    setResultVisible(false);
    setStatsVisible(false);
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
      reconnectAttemptsRef.current = 0;
      terminalEventReceivedRef.current = false;
      activeRunIdRef.current = payload.run_id;
      setCurrentRun({ ...initialRunState, runId: payload.run_id, status: "starting" });
      connectToRun(payload.run_id);
    } catch (submitError) {
      setError(submitError.message);
      setConnectionStatus("error");
      reconnectAttemptsRef.current = 0;
      terminalEventReceivedRef.current = true;
      setCurrentRun({ ...initialRunState, status: "failed", error: submitError.message });
    }
  }

  async function handleStopRun() {
    const runId = activeRunIdRef.current ?? currentRun.runId;
    if (!runId || isStopping) {
      return;
    }

    setIsStopping(true);
    setError("");

    try {
      const response = await fetch(`/runs/${runId}`, { method: "DELETE" });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail ?? "Unable to stop run.");
      }

      terminalEventReceivedRef.current = true;
      reconnectAttemptsRef.current = 0;
      clearReconnectTimeout();
      clearStatsRevealTimeout();
      closeSocket();
      activeRunIdRef.current = runId;
      setConnectionStatus("closed");
      setResultVisible(false);
      setStatsVisible(false);
      setCurrentRun((previous) => ({
        ...previous,
        status: "cancelled",
        finalOutput: "",
        runStats: null,
        historyStats: null,
        error: "",
      }));
    } catch (stopError) {
      setError(stopError.message);
    } finally {
      setIsStopping(false);
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
            onStop={handleStopRun}
            isRunning={isRunBusy || isStopping}
            canStop={canStop}
            isStopping={isStopping}
          />
        </section>

        {showLiveExecution ? (
          <section className="content-section panel panel-agent section-reveal">
            <AgentGraph run={currentRun} />
          </section>
        ) : null}

        {resultVisible ? (
          <section className="content-section panel panel-result section-reveal">
            <ResultOutput run={currentRun} />
          </section>
        ) : null}

        {statsVisible ? (
          <section className="content-section panel panel-stats section-reveal">
            <RunStats run={currentRun} />
          </section>
        ) : null}

        <section className="content-section panel panel-history">
          <HistoryPanel history={history} />
        </section>
      </main>
    </div>
  );
}
