function formatUsd(value) {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

export default function HistoryPanel({ history }) {
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
    </div>
  );
}
