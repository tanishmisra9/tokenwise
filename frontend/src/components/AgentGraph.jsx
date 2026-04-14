import { useRef } from "react";

function statusTone(status) {
  if (status === "completed") return "tone-success";
  if (status === "running") return "tone-live";
  if (status === "failed") return "tone-danger";
  return "tone-muted";
}

function truncateLabel(description) {
  const words = description.trim().split(/\s+/);
  if (words.length <= 6) {
    return description;
  }
  return `${words.slice(0, 6).join(" ")}…`;
}

export default function AgentGraph({ run }) {
  const subtasks = run.plan ?? [];
  const scrollRef = useRef(null);

  function scrollToLatest() {
    if (!scrollRef.current) {
      return;
    }
    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }

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
          <div className="subtask-scroll-region" ref={scrollRef}>
            <div className="subtask-list">
              {subtasks.map((subtask, index) => {
                const liveState = run.subtasks?.[subtask.id] ?? {};
                const attempts = liveState.attempts ?? [];
                const route = liveState.route;
                const latestAttempt = attempts.at(-1);

                return (
                  <article className={`subtask-card ${statusTone(liveState.status)}`} key={subtask.id}>
                    <div className="subtask-step">{String(index + 1).padStart(2, "0")}</div>
                    <div className="subtask-body">
                      <div className="subtask-head">
                        <div className="subtask-heading-copy">
                          <p className="subtask-id">{subtask.id}</p>
                          <h3 title={subtask.description}>{truncateLabel(subtask.description)}</h3>
                        </div>
                        <span className="status-pill">{liveState.status ?? "queued"}</span>
                      </div>

                      <div className="subtask-meta">
                        <span>{subtask.complexity}</span>
                        <span>{subtask.output_format}</span>
                        <span>{subtask.routing_hint}</span>
                        {route ? <span>{route.provider} · Tier {route.tier}</span> : null}
                        <span>Attempt {latestAttempt?.attempt_number ?? 0}</span>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>

          <button className="show-more-button" type="button" onClick={scrollToLatest}>
            Show more ↓
          </button>
        </div>
      )}
    </div>
  );
}
