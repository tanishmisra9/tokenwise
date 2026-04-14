export default function ResultOutput({ run }) {
  return (
    <div className="result-output">
      <div className="panel-heading">
        <p className="eyebrow">Final output</p>
        <h2>Composed response</h2>
      </div>

      {run.status === "idle" || run.status === "starting" ? (
        <div className="empty-state">
          <p>The composed answer lands here after the subtasks complete.</p>
        </div>
      ) : null}

      {run.status === "failed" ? (
        <div className="result-shell result-error">
          <h3>Run failed</h3>
          <p>{run.error}</p>
        </div>
      ) : null}

      {run.finalOutput ? (
        <div className="result-shell">
          <pre>{run.finalOutput}</pre>
        </div>
      ) : null}
    </div>
  );
}

