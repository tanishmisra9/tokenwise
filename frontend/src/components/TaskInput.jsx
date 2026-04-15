function estimateSubtasks(charCount) {
  return Math.max(3, Math.min(7, Math.round(charCount / 120)));
}

function formatUsdRange(value) {
  return value.toFixed(2);
}

const QUALITY_OPTIONS = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

export default function TaskInput({ form, setForm, onSubmit, onStop, isRunning, canStop, isStopping }) {
  const charCount = form.task.length;
  const estimatedSubtasks = estimateSubtasks(charCount);
  const lowEstimate = estimatedSubtasks * 0.00038;
  const highEstimate = estimatedSubtasks * 0.006;

  return (
    <form className="task-form" onSubmit={onSubmit}>
      <div className="section-heading task-heading">
        <h2>Give Tokenwise the work. It handles the routing, retries, and savings discipline.</h2>
      </div>

      <label className="field task-field">
        <span>Task</span>
        <textarea
          value={form.task}
          onChange={(event) => setForm((previous) => ({ ...previous, task: event.target.value }))}
          placeholder="Draft a launch plan, summarize a long brief, break down a research task, or any other complex job."
          rows={8}
          required
        />
      </label>

      <div className="task-controls">
        <div className="quality-control">
          <span className="quality-label">Quality floor</span>
          <div className="segmented-control" role="radiogroup" aria-label="Quality floor">
            {QUALITY_OPTIONS.map((option) => {
              const isActive = form.quality_floor === option.value;
              return (
                <button
                  key={option.value}
                  className={`segmented-option ${isActive ? "is-active" : ""}`}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => setForm((previous) => ({ ...previous, quality_floor: option.value }))}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="task-actions">
          <button className="submit-button" type="submit" disabled={isRunning}>
            Run
          </button>
          {canStop ? (
            <button className="stop-button" type="button" onClick={onStop} disabled={isStopping}>
              Stop
            </button>
          ) : null}
        </div>
      </div>

      {charCount > 0 ? (
        <p className="field-help task-estimate">
          ${formatUsdRange(lowEstimate)}–${formatUsdRange(highEstimate)}
        </p>
      ) : null}
    </form>
  );
}
