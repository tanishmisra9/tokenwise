function estimateSubtasks(charCount) {
  return Math.max(3, Math.min(7, Math.round(charCount / 120)));
}

function formatUsdRange(value) {
  return value.toFixed(2);
}

export default function TaskInput({ form, setForm, onSubmit, isRunning }) {
  const charCount = form.task.length;
  const estimatedSubtasks = estimateSubtasks(charCount);
  const lowEstimate = estimatedSubtasks * 0.00038;
  const highEstimate = estimatedSubtasks * 0.006;

  return (
    <form className="task-form" onSubmit={onSubmit}>
      <div className="panel-heading">
        <p className="eyebrow">New run</p>
        <h2>Paste the work and let the engine route it.</h2>
      </div>

      <label className="field">
        <span>Task</span>
        <textarea
          value={form.task}
          onChange={(event) => setForm((previous) => ({ ...previous, task: event.target.value }))}
          placeholder="Draft a launch plan, summarize a long brief, break down a research task, or any other complex job."
          rows={8}
          required
        />
        <p className="field-help task-estimate">
          ~{charCount} chars · Est. {estimatedSubtasks} subtasks · ~$
          {formatUsdRange(lowEstimate)}–${formatUsdRange(highEstimate)}
        </p>
      </label>

      <label className="field">
        <span>Quality floor</span>
        <select
          value={form.quality_floor}
          onChange={(event) => setForm((previous) => ({ ...previous, quality_floor: event.target.value }))}
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>

      <button className="submit-button" type="submit" disabled={isRunning}>
        {isRunning ? "Running orchestration..." : "Launch run"}
      </button>
    </form>
  );
}
