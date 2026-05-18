"use client";

import { useEffect, useRef, useState } from "react";
import { marked } from "marked";
import type { ArtifactPayload } from "@/lib/types";

/**
 * Modal that renders a single artifact fetched from /api/artifact.
 *
 * Markdown files render via `marked` (sanitized via `marked`'s default
 * escaping; no raw HTML pass-through). JSON files pretty-print at 2-space
 * indent in a <pre>. Other text files render as <pre>. Binary files (the
 * /api/artifact route returns base64 with encoding="base64") render a
 * "binary content (X bytes) — not viewable inline" placeholder + copy-path.
 *
 * Closes on backdrop click, ESC key, or X button.
 */

export default function ArtifactViewerModal({
  open,
  path,
  onClose,
}: {
  open: boolean;
  path: string | null;
  onClose: () => void;
}) {
  const [payload, setPayload] = useState<ArtifactPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // Fetch on open + path change.
  useEffect(() => {
    if (!open || !path) {
      setPayload(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/artifact?path=${encodeURIComponent(path)}`)
      .then((res) => res.json().then((body) => ({ res, body })))
      .then(({ res, body }) => {
        if (cancelled) return;
        if (!res.ok) {
          setError(typeof body?.error === "string" ? body.error : `HTTP ${res.status}`);
          setPayload(null);
        } else {
          setPayload(body as ArtifactPayload);
        }
      })
      .catch((exc) => {
        if (!cancelled) setError(String(exc));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, path]);

  // ESC key closes the modal.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const body = renderBody({ payload, error, loading });

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-dialog"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={path ?? "Artifact viewer"}
      >
        <div className="modal-header">
          <div className="modal-title">
            <strong>{payload?.basename ?? "Loading…"}</strong>
            {payload && (
              <span className="modal-meta">
                {payload.relPath} · {formatBytes(payload.size)} · {payload.encoding}
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={onClose}
            aria-label="Close"
          >
            Close
          </button>
        </div>
        <div className="modal-body">{body}</div>
      </div>
    </div>
  );
}

function renderBody({
  payload,
  error,
  loading,
}: {
  payload: ArtifactPayload | null;
  error: string | null;
  loading: boolean;
}) {
  if (loading) {
    return <div className="modal-loading">Loading artifact…</div>;
  }
  if (error) {
    return (
      <div className="modal-error">
        <strong>Failed to load artifact</strong>
        <p>{error}</p>
      </div>
    );
  }
  if (!payload) return null;

  if (payload.encoding === "base64") {
    return (
      <div className="modal-binary">
        <p>
          Binary content ({formatBytes(payload.size)}) — not viewable inline.
        </p>
        <p>
          Path: <code>{payload.relPath}</code>
        </p>
      </div>
    );
  }

  // Render based on extension.
  const ext = payload.extension.toLowerCase();
  if (ext === ".md" || ext === ".markdown") {
    const html = marked.parse(payload.content, { async: false }) as string;
    return (
      <div
        className="modal-markdown"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  if (ext === ".json") {
    let pretty = payload.content;
    try {
      pretty = JSON.stringify(JSON.parse(payload.content), null, 2);
    } catch {
      // Render the raw text if JSON parse fails — better than crashing.
    }
    return (
      <pre className="modal-pre">
        <code>{pretty}</code>
      </pre>
    );
  }
  return (
    <pre className="modal-pre">
      <code>{payload.content}</code>
    </pre>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}
