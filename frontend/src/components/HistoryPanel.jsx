function formatUsd(value) {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

function formatHintLabel(hint) {
  return String(hint ?? "")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function buildBreakdownRows(history) {
  return Object.entries(history.routing_hint_breakdown ?? {})
    .map(([hint, value]) => ({
      hint,
      label: formatHintLabel(hint),
      subtaskCount: Number(value?.subtask_count ?? 0),
      avgSavingsPct: Number(value?.avg_savings_pct ?? 0),
    }))
    .sort((left, right) => {
      if (right.avgSavingsPct !== left.avgSavingsPct) {
        return right.avgSavingsPct - left.avgSavingsPct;
      }
      return left.label.localeCompare(right.label);
    });
}

export default function HistoryPanel({ history }) {
  const breakdownRows = buildBreakdownRows(history);
  const maxPositiveSavings = Math.max(
    1,
    ...breakdownRows.map((row) => Math.max(0, row.avgSavingsPct)),
  );

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

      <div className="history-breakdown">
        <div className="history-breakdown-head">
          <h3>Savings by routing hint</h3>
        </div>

        {breakdownRows.length ? (
          <div className="history-breakdown-list">
            {breakdownRows.map((row) => {
              const barWidth = Math.max(0, row.avgSavingsPct) / maxPositiveSavings * 100;

              return (
                <div className="history-breakdown-row" key={row.hint}>
                  <div className="history-breakdown-copy">
                    <strong>{row.label}</strong>
                    <span>
                      {row.subtaskCount} subtasks · {row.avgSavingsPct.toFixed(2)}%
                    </span>
                  </div>
                  <svg
                    className="history-breakdown-chart"
                    viewBox="0 0 100 14"
                    preserveAspectRatio="none"
                    aria-hidden="true"
                  >
                    <rect x="0" y="1" width="100" height="12" rx="6" fill="var(--surface-strong)" />
                    <rect x="0" y="1" width={barWidth} height="12" rx="6" fill="var(--text-primary)" />
                  </svg>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-state history-breakdown-empty">
            <p>Routing-hint savings appear here after completed runs accumulate.</p>
          </div>
        )}
      </div>
    </div>
  );
}
