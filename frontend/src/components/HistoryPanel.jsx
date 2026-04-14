function formatUsd(value) {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

export default function HistoryPanel({ history }) {
  return (
    <div className="history-panel">
      <div className="panel-heading">
        <p className="eyebrow">Historical totals</p>
        <h2>Cumulative savings across every run.</h2>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <span>Total runs</span>
          <strong>{history.total_runs ?? 0}</strong>
        </div>
        <div className="stat-card">
          <span>Total tokens</span>
          <strong>{Number(history.total_tokens ?? 0).toLocaleString()}</strong>
        </div>
        <div className="stat-card">
          <span>Total spent</span>
          <strong>{formatUsd(history.total_spent_usd)}</strong>
        </div>
        <div className="stat-card">
          <span>Total saved</span>
          <strong>{formatUsd(history.total_saved_usd)}</strong>
        </div>
        <div className="stat-card">
          <span>Avg savings/run</span>
          <strong>{Number(history.avg_savings_pct ?? 0).toFixed(2)}%</strong>
        </div>
      </div>

      <div className="recent-runs">
        {(history.runs ?? []).map((run) => (
          <article className="history-item" key={run.run_id}>
            <div>
              <p className="history-title">{run.task_preview}</p>
              <span className="history-meta">
                {run.status} · {run.tokens_used.toLocaleString()} tokens · {run.escalations} escalations
              </span>
            </div>
            <div className="history-values">
              <strong>{formatUsd(run.actual_cost_usd)}</strong>
              <span>{Number(run.savings_pct ?? 0).toFixed(1)}% saved</span>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

