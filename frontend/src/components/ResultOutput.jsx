import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

const COPY_RESET_MS = 1800;

export default function ResultOutput({ run }) {
  const scrollRef = useRef(null);
  const copyResetRef = useRef(null);
  const [copyState, setCopyState] = useState("idle");
  const [showFade, setShowFade] = useState(false);

  useEffect(() => {
    return () => {
      if (copyResetRef.current) {
        window.clearTimeout(copyResetRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !run.finalOutput) {
      setShowFade(false);
      return undefined;
    }

    function updateFade() {
      const hasOverflow = node.scrollHeight > node.clientHeight + 2;
      const atBottom = node.scrollTop + node.clientHeight >= node.scrollHeight - 2;
      setShowFade(hasOverflow && !atBottom);
    }

    node.scrollTop = 0;
    updateFade();

    node.addEventListener("scroll", updateFade);
    window.addEventListener("resize", updateFade);

    const resizeObserver =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(updateFade)
        : null;

    resizeObserver?.observe(node);
    if (node.firstElementChild) {
      resizeObserver?.observe(node.firstElementChild);
    }

    return () => {
      node.removeEventListener("scroll", updateFade);
      window.removeEventListener("resize", updateFade);
      resizeObserver?.disconnect();
    };
  }, [run.finalOutput, run.status]);

  async function handleCopy() {
    if (!run.finalOutput) {
      return;
    }

    try {
      await navigator.clipboard.writeText(run.finalOutput);
      setCopyState("copied");

      if (copyResetRef.current) {
        window.clearTimeout(copyResetRef.current);
      }

      copyResetRef.current = window.setTimeout(() => {
        setCopyState("idle");
      }, COPY_RESET_MS);
    } catch {
      setCopyState("idle");
    }
  }

  const CopyIcon = copyState === "copied" ? Check : Copy;
  const copyLabel = copyState === "copied" ? "Copied" : "Copy";
  const allSubtasksTerminal =
    (run.plan?.length ?? 0) > 0 &&
    (run.plan ?? []).every((subtask) =>
      ["completed", "completed_degraded"].includes(run.subtasks?.[subtask.id]?.status),
    );
  const showComposerLoading =
    !run.error &&
    !run.finalOutput &&
    ["running", "starting"].includes(run.status) &&
    allSubtasksTerminal;

  return (
    <div className="result-output">
      <div className="section-heading result-heading">
        <div>
          <p className="eyebrow">Final output</p>
          <h2>Composed response</h2>
        </div>
        <button
          className="copy-button"
          type="button"
          onClick={handleCopy}
          disabled={!run.finalOutput}
          aria-label={copyLabel}
        >
          <CopyIcon size={16} strokeWidth={1.8} />
          <span className="copy-button-label">{copyLabel}</span>
        </button>
      </div>

      {run.status === "idle" || run.status === "starting" ? (
        <div className="empty-state empty-state-inline">
          <p>The composed answer lands here after the subtasks complete.</p>
        </div>
      ) : null}

      {run.status === "failed" ? (
        <div className="result-shell result-error">
          <h3>Run failed</h3>
          <p>{run.error}</p>
        </div>
      ) : null}

      {showComposerLoading ? (
        <div className="result-shell result-loading">
          <div className="composer-spinner" aria-hidden="true" />
          <p className="composer-loading-copy">Composing the final response…</p>
        </div>
      ) : null}

      {run.finalOutput ? (
        <div className={`result-shell ${showFade ? "has-fade" : ""}`}>
          <div className="result-scroll-region" ref={scrollRef}>
            <div className="result-markdown">
              <ReactMarkdown>{run.finalOutput}</ReactMarkdown>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
