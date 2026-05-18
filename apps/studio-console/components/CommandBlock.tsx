"use client";

import { useState } from "react";

/**
 * Mono-font command block with a copy-to-clipboard button.
 *
 * Used everywhere the Console wants the operator to run something in the
 * terminal manually. The Preview-mode safety pattern:
 *   - render the exact command,
 *   - one-click copy,
 *   - operator paste-into-terminal.
 *
 * No execution, no spawn, no subprocess — copy only. Live mode (RC-5A.10)
 * adds a sibling "Run locally" button next to the Copy button.
 */

export default function CommandBlock({
  command,
  multiline = false,
  hint,
}: {
  /** The exact shell command(s). Multi-line strings render as a script block. */
  command: string;
  /** When true, render as a script block (preserve newlines + extra spacing). */
  multiline?: boolean;
  /** Optional caption rendered above the block, e.g. "Run from repo root:". */
  hint?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Older browsers / iframes without clipboard permission — operator
      // can still select-and-copy manually. Do nothing.
    }
  }

  return (
    <div className="command-block-wrapper">
      {hint && <div className="command-block-hint">{hint}</div>}
      <div className="command-block">
        <pre className={multiline ? "command-block-pre multiline" : "command-block-pre"}>
          <code>{command}</code>
        </pre>
        <button
          type="button"
          className="command-block-copy btn"
          data-variant="ghost"
          onClick={handleCopy}
          aria-label="Copy command to clipboard"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}
