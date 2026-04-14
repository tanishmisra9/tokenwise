function truncateLabel(description) {
  const words = description.trim().split(/\s+/);
  if (words.length <= 6) {
    return description;
  }
  return `${words.slice(0, 6).join(" ")}…`;
}

function formatStatusLabel(status) {
  if (!status) return "Queued";
  if (status === "completed_degraded") return "Completed";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function statusTone(status) {
  if (status === "completed" || status === "completed_degraded") return "tone-success";
  if (status === "running") return "tone-live";
  if (status === "failed") return "tone-danger";
  return "tone-muted";
}

function tierLabel(route, attempt) {
  const tier = attempt?.tier ?? route?.tier ?? null;
  return tier ? `T${tier}` : "T?";
}

export default function AgentGraph({ run }) {
  const subtasks = run.plan ?? [];

  if (!subtasks.length) {
    return null;
  }

  return (
    <div className="agent-graph">
      <div className="section-heading section-heading-compact">
        <p className="eyebrow">Live execution</p>
        <h2>Timeline</h2>
      </div>

      <div className="subtask-scroll-region timeline-scroll">
        <div className="subtask-list timeline-track">
          {subtasks.map((subtask, index) => {
            const liveState = run.subtasks?.[subtask.id] ?? {};
            const latestAttempt = liveState.attempts?.at(-1);
            const latestReason = liveState.events?.at(-1)?.reason ?? "";
            const tier = latestAttempt?.tier ?? liveState.route?.tier ?? subtask.route?.tier ?? null;

            return (
              <article
                className={`subtask-pill ${statusTone(liveState.status)}`}
                key={subtask.id}
                data-tooltip={latestReason || undefined}
                title={latestReason || undefined}
              >
                <div className="subtask-pill-top">
                  <span className="subtask-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className={`tier-badge tier-${tier ?? "unknown"}`}>{tierLabel(liveState.route, latestAttempt)}</span>
                  <span className={`status-pill ${statusTone(liveState.status)}`}>
                    <span className="status-pill-dot" aria-hidden="true" />
                    <span>{formatStatusLabel(liveState.status)}</span>
                  </span>
                </div>
                <p className="subtask-pill-label">{truncateLabel(subtask.description)}</p>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
