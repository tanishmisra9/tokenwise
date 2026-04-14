function formatUsd(value) {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

function formatLatencyLabel(latencyMs) {
  return `${(latencyMs / 1000).toFixed(1)}s`;
}

function buildLatencySummary(subtasks) {
  const tierStats = new Map();

  for (const subtask of Object.values(subtasks ?? {})) {
    for (const attempt of subtask.attempts ?? []) {
      const tier = attempt?.tier;
      const latencyMs = Number(attempt?.latency_ms ?? 0);
      if (!tier || latencyMs <= 0) {
        continue;
      }

      const existing = tierStats.get(tier) ?? { total: 0, count: 0 };
      existing.total += latencyMs;
      existing.count += 1;
      tierStats.set(tier, existing);
    }
  }

  return [...tierStats.entries()]
    .sort(([leftTier], [rightTier]) => leftTier - rightTier)
    .map(([tier, { total, count }]) => `Tier ${tier}: ${formatLatencyLabel(total / count)}`)
    .join(" · ");
}

export default function RunStats({ run }) {
  const runStats = run?.runStats ?? null;
  const latencySummary = buildLatencySummary(run?.subtasks);

  if (!runStats) {
    return (
      <div className="run-stats">
        <div className="panel-heading">
          <p className="eyebrow">Run economics</p>
          <h2>Cost and usage audit</h2>
        </div>
        <div className="empty-state">
          <p>The current run’s token and savings breakdown appears here as the workflow advances.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="run-stats">
      <div className="panel-heading">
        <p className="eyebrow">Run economics</p>
        <h2>Cost and usage audit</h2>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <span>Tokens</span>
          <strong>{Number(runStats.tokens_used ?? 0).toLocaleString()}</strong>
        </div>
        <div className="stat-card">
          <span>Actual cost</span>
          <strong>{formatUsd(runStats.actual_cost_usd)}</strong>
        </div>
        <div className="stat-card">
          <span>Baseline</span>
          <strong>{formatUsd(runStats.baseline_cost_usd)}</strong>
        </div>
        <div className="stat-card">
          <span>Saved</span>
          <strong>{formatUsd(runStats.saved_usd)}</strong>
        </div>
        <div className="stat-card">
          <span>Savings %</span>
          <strong>{Number(runStats.savings_pct ?? 0).toFixed(2)}%</strong>
        </div>
        <div className="stat-card">
          <span>Escalations</span>
          <strong>{runStats.escalations ?? 0}</strong>
        </div>
        <div className="stat-card stat-card-wide">
          <span>Avg latency / tier</span>
          <strong>{latencySummary || "Awaiting attempts"}</strong>
        </div>
      </div>

      <div className="model-usage">
        {Object.entries(runStats.models_used ?? {}).map(([model, count]) => (
          <div className="model-chip" key={model}>
            <span>{model}</span>
            <strong>× {count}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}
