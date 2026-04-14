export default function TaskInput({ form, setForm, onSubmit, isRunning }) {
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
      </label>

      <div className="field-row">
        <label className="field">
          <span>Budget cap (USD)</span>
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={form.budget_cap_usd}
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                budget_cap_usd: Number(event.target.value),
              }))
            }
            required
          />
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
      </div>

      <button className="submit-button" type="submit" disabled={isRunning}>
        {isRunning ? "Running orchestration..." : "Launch run"}
      </button>
    </form>
  );
}

