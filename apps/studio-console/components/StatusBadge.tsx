/**
 * Color-coded status badge.
 *
 * Variants map to the locked-spec § 15 status palette:
 *   completed / delivered → green
 *   running               → blue
 *   pending / default     → gray
 *   needs-review / warning→ amber
 *   failed / danger       → red
 *   locked                → purple
 */

export default function StatusBadge({
  variant,
  children,
}: {
  variant:
    | "completed"
    | "delivered"
    | "running"
    | "pending"
    | "needs-review"
    | "warning"
    | "failed"
    | "danger"
    | "locked"
    | "default";
  children: React.ReactNode;
}) {
  return (
    <span className="badge" data-variant={variant}>
      {children}
    </span>
  );
}
