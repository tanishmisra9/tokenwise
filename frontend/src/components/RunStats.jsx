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

  return (
    <div className="run-stats">
      <div className="section-heading section-heading-compact">
        <p className="eyebrow">Run economics</p>
        <h2>Compact ledger</h2>
      </div>

      {runStats ? (
        <>
          <p className="economics-strip">
            <span>Tokens: {Number(runStats.tokens_used ?? 0).toLocaleString()}</span>
            <span className="economics-separator">·</span>
            <span>Cost: {formatUsd(runStats.actual_cost_usd)}</span>
            <span className="economics-separator">·</span>
            <span>
              Saved: {formatUsd(runStats.saved_usd)} ({Number(runStats.savings_pct ?? 0).toFixed(2)}%)
            </span>
            <span className="economics-separator">·</span>
            <span>Escalations: {runStats.escalations ?? 0}</span>
            {latencySummary ? (
              <>
                <span className="economics-separator">·</span>
                <span>{latencySummary}</span>
              </>
            ) : null}
          </p>

          {(Object.keys(runStats.models_used ?? {}).length ?? 0) > 0 ? (
            <div className="model-usage">
              {Object.entries(runStats.models_used ?? {}).map(([model, count]) => (
                <div className="model-chip" key={model}>
                  <span>{model}</span>
                  <strong>× {count}</strong>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <p className="section-note">The run ledger appears here as soon as the first attempts complete.</p>
      )}
    </div>
  );
}
