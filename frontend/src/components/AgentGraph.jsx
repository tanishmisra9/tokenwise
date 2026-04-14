function statusTone(status) {
  if (status === "completed" || status === "completed_degraded") return "tone-success";
  if (status === "running") return "tone-live";
  if (status === "failed") return "tone-danger";
  return "tone-muted";
}

function formatStatusLabel(status) {
  if (!status) {
    return "QUEUED";
  }
  if (status === "completed_degraded") {
    return "COMPLETED";
  }
  return status.toUpperCase();
}

function formatComplexityLabel(complexity) {
  if (complexity === "low") return "Low";
  if (complexity === "medium") return "Med";
  if (complexity === "high") return "High";
  return complexity;
}

function truncateLabel(description) {
  const words = description.trim().split(/\s+/);
  if (words.length <= 6) {
    return description;
  }
  return `${words.slice(0, 6).join(" ")}…`;
}

function truncateReason(reason) {
  if (!reason) {
    return "";
  }
  if (reason.length <= 80) {
    return reason;
  }
  return `${reason.slice(0, 79)}…`;
}

export default function AgentGraph({ run }) {
  const subtasks = run.plan ?? [];

  return (
    <div className="agent-graph">
      <div className="panel-heading">
        <p className="eyebrow">Live execution</p>
        <h2>Subtasks, tier shifts, and provider handoffs.</h2>
      </div>

      {!subtasks.length ? (
        <div className="empty-state">
          <p>The live graph appears here after the orchestrator emits a plan.</p>
        </div>
      ) : (
        <div className="subtask-stack">
          <div className="subtask-scroll-region">
            <div className="subtask-list">
              {subtasks.map((subtask, index) => {
                const liveState = run.subtasks?.[subtask.id] ?? {};
                const attempts = liveState.attempts ?? [];
                const route = liveState.route;
                const latestAttempt = attempts.at(-1);
                const modelLabel = latestAttempt?.model_id ?? route?.model_id ?? "Pending";
                const latestReason = truncateReason(liveState.events?.at(-1)?.reason ?? "");

                return (
                  <article className={`subtask-card ${statusTone(liveState.status)}`} key={subtask.id}>
                    <div className="subtask-step">{String(index + 1).padStart(2, "0")}</div>
                    <div className="subtask-body">
                      <div className="subtask-head">
                        <div className="subtask-heading-copy">
                          <p className="subtask-id">{subtask.id}</p>
                          <h3 title={subtask.description}>{truncateLabel(subtask.description)}</h3>
                        </div>
                        <span className="status-pill">{formatStatusLabel(liveState.status ?? "queued")}</span>
                      </div>

                      <div className="subtask-meta">
                        <span title={modelLabel}>{modelLabel}</span>
                        <span>Attempt {latestAttempt?.attempt_number ?? 0}</span>
                        <span>{formatComplexityLabel(subtask.complexity)}</span>
                      </div>

                      {latestReason ? <p className="subtask-reason">{latestReason}</p> : null}
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
