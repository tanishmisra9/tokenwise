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
  const maxPositiveSavings = Math.max(1, ...breakdownRows.map((row) => Math.max(0, row.avgSavingsPct)));

  return (
    <div className="history-panel">
      <div className="section-heading section-heading-compact">
        <p className="eyebrow">Historical totals</p>
        <h2>Cumulative savings</h2>
      </div>

      <div className="history-summary-row">
        <div className="history-summary-item">
          <strong>{history.total_runs ?? 0}</strong>
          <span>Total runs</span>
        </div>
        <div className="history-summary-item">
          <strong>{Number(history.total_tokens ?? 0).toLocaleString()}</strong>
          <span>Total tokens</span>
        </div>
        <div className="history-summary-item">
          <strong>{formatUsd(history.total_spent_usd)}</strong>
          <span>Total spent</span>
        </div>
        <div className="history-summary-item">
          <strong>{formatUsd(history.total_saved_usd)}</strong>
          <span>Total saved</span>
        </div>
        <div className="history-summary-item">
          <strong>{Number(history.avg_savings_pct ?? 0).toFixed(2)}%</strong>
          <span>Avg savings/run</span>
        </div>
      </div>

      <div className="history-breakdown">
        <div className="history-breakdown-head">
          <h3>Savings by routing hint</h3>
        </div>

        {breakdownRows.length ? (
          <div className="history-breakdown-list">
            {breakdownRows.map((row) => {
              const barWidth = (Math.max(0, row.avgSavingsPct) / maxPositiveSavings) * 100;

              return (
                <div className="history-breakdown-row" key={row.hint}>
                  <div className="history-breakdown-copy">
                    <strong>{row.label}</strong>
                    <span>{row.subtaskCount} subtasks</span>
                  </div>
                  <div className="history-breakdown-bar">
                    <div className="history-breakdown-fill" style={{ width: `${barWidth}%` }} />
                  </div>
                  <div className="history-breakdown-value">{row.avgSavingsPct.toFixed(2)}%</div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-state empty-state-inline">
            <p>Routing-hint savings appear here after completed runs accumulate.</p>
          </div>
        )}
      </div>
    </div>
  );
}
