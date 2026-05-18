"use client";

import { useEffect, useState } from "react";
import { marked } from "marked";

/**
 * Plain `<textarea>` editor with character/word counts and an optional
 * side preview pane (markdown via `marked`).
 *
 * State model — the editor is **uncontrolled** at the framework level:
 * the parent passes `initialValue` and a `onSave(text)` callback. Local
 * dirty state is tracked here so the parent doesn't re-render on every
 * keystroke. When the parent re-mounts (different file selected), the
 * `key` prop forces a fresh editor instance.
 *
 * Locked spec: docs/STUDIO_CONSOLE_SPEC.md § 3 (no rich editor; plain
 * textarea + side preview only).
 */

export type MarkdownEditorProps = {
  /** Loaded-from-server text (initial state for the editor). */
  initialValue: string;
  /** Caller writes the new content via PUT /api/contracts/[id]; passes this for the Save button. */
  onSave: (text: string) => Promise<void>;
  /** Hint shown above the editor; e.g. "raw-requirements.md". */
  fileLabel?: string;
  /** Set true when the contract is LOCKED — disables editing + buttons. */
  readOnly?: boolean;
  /** Optional: minimum textarea height in px (default 320). */
  minHeight?: number;
  /** Optional: caption rendered above the textarea/preview. */
  helperText?: string;
  /** Optional: extra action buttons rendered next to Save (e.g. Create Template). */
  extraActions?: React.ReactNode;
};

type SaveStatus = "saved" | "dirty" | "saving" | "error";

export default function MarkdownEditor({
  initialValue,
  onSave,
  fileLabel,
  readOnly = false,
  minHeight = 320,
  helperText,
  extraActions,
}: MarkdownEditorProps) {
  const [text, setText] = useState<string>(initialValue);
  const [status, setStatus] = useState<SaveStatus>("saved");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState<boolean>(false);

  // When initialValue changes (e.g. parent reloaded the contract), reset
  // local state. We compare by reference so a no-op rerender doesn't blast
  // the operator's in-progress edits.
  useEffect(() => {
    setText(initialValue);
    setStatus("saved");
    setErrorMessage(null);
  }, [initialValue]);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setText(e.target.value);
    setStatus(e.target.value === initialValue ? "saved" : "dirty");
  }

  async function handleSave() {
    if (status === "saving") return;
    setStatus("saving");
    setErrorMessage(null);
    try {
      await onSave(text);
      setStatus("saved");
    } catch (exc) {
      setStatus("error");
      setErrorMessage(String(exc));
    }
  }

  const charCount = text.length;
  const wordCount = text.trim() === "" ? 0 : text.trim().split(/\s+/).length;
  const lineCount = text === "" ? 0 : text.split("\n").length;

  return (
    <div className="md-editor">
      <div className="md-editor-toolbar">
        <div className="md-editor-toolbar-left">
          {fileLabel && (
            <code className="md-editor-file">{fileLabel}</code>
          )}
          <span className="md-editor-counts">
            {charCount} chars · {wordCount} words · {lineCount} lines
          </span>
        </div>
        <div className="md-editor-toolbar-right">
          <label className="md-editor-preview-toggle">
            <input
              type="checkbox"
              checked={showPreview}
              onChange={(e) => setShowPreview(e.target.checked)}
            />
            <span>Preview</span>
          </label>
          {extraActions}
          <SaveStatusPill status={status} />
          <button
            type="button"
            className="btn"
            data-variant="primary"
            onClick={() => void handleSave()}
            disabled={readOnly || status !== "dirty"}
          >
            Save
          </button>
        </div>
      </div>

      {helperText && <p className="md-editor-helper">{helperText}</p>}

      {errorMessage && (
        <div className="md-editor-error">
          <strong>Save failed:</strong> {errorMessage}
        </div>
      )}

      <div className={showPreview ? "md-editor-body split" : "md-editor-body"}>
        <textarea
          className="md-editor-textarea"
          value={text}
          onChange={handleChange}
          readOnly={readOnly}
          spellCheck={false}
          style={{ minHeight }}
          placeholder={readOnly ? "" : "Start typing markdown…"}
        />
        {showPreview && (
          <div
            className="md-editor-preview"
            style={{ minHeight }}
            dangerouslySetInnerHTML={{
              __html: marked.parse(text || "*(empty)*", { async: false }) as string,
            }}
          />
        )}
      </div>
    </div>
  );
}

function SaveStatusPill({ status }: { status: SaveStatus }) {
  const label =
    status === "saved" ? "Saved" :
    status === "dirty" ? "Unsaved changes" :
    status === "saving" ? "Saving…" :
    "Save failed";
  const variant =
    status === "saved" ? "completed" :
    status === "dirty" ? "warning" :
    status === "saving" ? "running" :
    "failed";
  return (
    <span className="badge" data-variant={variant}>
      {label}
    </span>
  );
}
