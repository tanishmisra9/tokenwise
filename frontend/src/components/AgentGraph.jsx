function statusTone(status) {
  if (status === "completed") return "tone-success";
  if (status === "running") return "tone-live";
  if (status === "failed") return "tone-danger";
  return "tone-muted";
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
        <div className="subtask-list">
          {subtasks.map((subtask, index) => {
            const liveState = run.subtasks?.[subtask.id] ?? {};
            const attempts = liveState.attempts ?? [];
            const events = liveState.events ?? [];
            const route = liveState.route;

            return (
              <article className={`subtask-card ${statusTone(liveState.status)}`} key={subtask.id}>
                <div className="subtask-step">{String(index + 1).padStart(2, "0")}</div>
                <div className="subtask-body">
                  <div className="subtask-head">
                    <div>
                      <p className="subtask-id">{subtask.id}</p>
                      <h3>{subtask.description}</h3>
                    </div>
                    <span className="status-pill">{liveState.status ?? "queued"}</span>
                  </div>

                  <div className="subtask-meta">
                    <span>{subtask.complexity}</span>
                    <span>{subtask.output_format}</span>
                    <span>{subtask.routing_hint}</span>
                    {route ? <span>{route.provider} · Tier {route.tier}</span> : null}
                  </div>

                  {attempts.length ? (
                    <div className="attempt-strip">
                      {attempts.map((attempt) => (
                        <div className="attempt-pill" key={`${subtask.id}-${attempt.attempt_number}`}>
                          <strong>Attempt {attempt.attempt_number}</strong>
                          <span>{attempt.model_id}</span>
                          <span>{attempt.status}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  {events.length ? (
                    <div className="event-list">
                      {events.map((item, itemIndex) => (
                        <div className="event-pill" key={`${subtask.id}-${itemIndex}`}>
                          <strong>{item.action.replaceAll("_", " ")}</strong>
                          <span>{item.reason}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  {liveState.output ? (
                    <pre className="subtask-output">{liveState.output}</pre>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

