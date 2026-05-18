"use client";

import { useEffect, useState } from "react";

/**
 * Open questions panel — interactive checkbox list backed by
 * open-questions.md. Lock rule: 0 unresolved (`- [ ]`) lines.
 *
 * Parser model:
 *   - Walk lines.
 *   - Lines matching `^\s*-\s+\[([ x])\]\s+(.+)$` are questions.
 *   - All other lines are "preamble" preserved verbatim above the
 *     question block when re-serializing.
 *
 * This way the operator's free-form headers / notes stay intact.
 */

type Question = {
  text: string;
  resolved: boolean;
};

export type OpenQuestionsPanelProps = {
  /** Loaded-from-server text (initial state). */
  initialValue: string;
  /** Caller writes via PUT /api/contracts/[id]. Receives the new full markdown. */
  onSave: (newMarkdown: string) => Promise<void>;
  /** When the contract is LOCKED, panel is read-only. */
  readOnly?: boolean;
};

export default function OpenQuestionsPanel({
  initialValue,
  onSave,
  readOnly = false,
}: OpenQuestionsPanelProps) {
  const [{ preamble, questions }, setState] = useState<{
    preamble: string;
    questions: Question[];
  }>(() => parseOpenQuestions(initialValue));
  const [draftText, setDraftText] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setState(parseOpenQuestions(initialValue));
    setDraftText("");
    setError(null);
  }, [initialValue]);

  async function persist(newQuestions: Question[]) {
    if (readOnly) return;
    setBusy(true);
    setError(null);
    try {
      const md = serializeOpenQuestions(preamble, newQuestions);
      await onSave(md);
      setState({ preamble, questions: newQuestions });
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function toggleAt(index: number) {
    const next = questions.map((q, i) =>
      i === index ? { ...q, resolved: !q.resolved } : q,
    );
    await persist(next);
  }

  async function deleteAt(index: number) {
    const next = questions.filter((_, i) => i !== index);
    await persist(next);
  }

  async function addQuestion() {
    const text = draftText.trim();
    if (!text) return;
    const next = [...questions, { text, resolved: false }];
    setDraftText("");
    await persist(next);
  }

  const unresolvedCount = questions.filter((q) => !q.resolved).length;
  const resolvedCount = questions.length - unresolvedCount;

  return (
    <div className="oq-panel">
      <div className="oq-panel-summary">
        <span className="badge" data-variant={unresolvedCount > 0 ? "warning" : "completed"}>
          {unresolvedCount} unresolved
        </span>
        <span className="badge">{resolvedCount} resolved</span>
        {busy && <span className="badge" data-variant="running">Saving…</span>}
      </div>

      {error && (
        <div className="oq-panel-error">
          <strong>Save failed:</strong> {error}
        </div>
      )}

      {questions.length === 0 ? (
        <p className="oq-panel-empty">
          No open questions yet. Add one below to track an unresolved
          decision; the lock gate refuses to lock while any
          <code> - [ ] </code> question remains.
        </p>
      ) : (
        <ul className="oq-panel-list">
          {questions.map((q, i) => (
            <li key={i} className={q.resolved ? "oq-item resolved" : "oq-item"}>
              <label className="oq-item-checkbox">
                <input
                  type="checkbox"
                  checked={q.resolved}
                  onChange={() => void toggleAt(i)}
                  disabled={readOnly || busy}
                />
                <span className="oq-item-text">{q.text}</span>
              </label>
              {!readOnly && (
                <button
                  type="button"
                  className="oq-item-delete"
                  onClick={() => void deleteAt(i)}
                  disabled={busy}
                  aria-label="Delete question"
                  title="Delete question"
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {!readOnly && (
        <div className="oq-panel-add">
          <input
            type="text"
            className="oq-panel-input"
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
            placeholder="Add a new open question…"
            disabled={busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && draftText.trim()) {
                e.preventDefault();
                void addQuestion();
              }
            }}
          />
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => void addQuestion()}
            disabled={busy || !draftText.trim()}
          >
            Add
          </button>
        </div>
      )}

      <p className="oq-panel-footer">
        Format: <code>- [ ] unresolved</code> · <code>- [x] resolved</code>.
        Edit the raw markdown via the open-questions.md tab if you need
        more control (free text, sub-bullets, etc.) — this panel preserves
        any non-checkbox content above the list.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Parser / serializer
// ---------------------------------------------------------------------------

const CHECKBOX_RE = /^\s*-\s+\[([ x])\]\s+(.+?)\s*$/i;

function parseOpenQuestions(text: string): {
  preamble: string;
  questions: Question[];
} {
  if (!text) return { preamble: "", questions: [] };
  const lines = text.split("\n");
  const preambleLines: string[] = [];
  const questions: Question[] = [];

  let inQuestionBlock = false;
  for (const line of lines) {
    const match = line.match(CHECKBOX_RE);
    if (match) {
      inQuestionBlock = true;
      questions.push({
        text: match[2],
        resolved: match[1].toLowerCase() === "x",
      });
      continue;
    }
    // Once we've seen the first checkbox, stop accumulating preamble —
    // any subsequent free text is dropped (the panel re-serializes
    // questions in a clean block; preserving inline notes between
    // checkboxes would make the round-trip lossy).
    if (!inQuestionBlock) {
      preambleLines.push(line);
    }
  }

  // Trim trailing blank lines from preamble; the serializer adds one.
  while (preambleLines.length > 0 && preambleLines[preambleLines.length - 1].trim() === "") {
    preambleLines.pop();
  }

  return {
    preamble: preambleLines.join("\n"),
    questions,
  };
}

function serializeOpenQuestions(preamble: string, questions: Question[]): string {
  const parts: string[] = [];
  if (preamble.trim()) {
    parts.push(preamble);
    parts.push("");
  }
  for (const q of questions) {
    parts.push(`- [${q.resolved ? "x" : " "}] ${q.text}`);
  }
  return parts.join("\n") + "\n";
}
