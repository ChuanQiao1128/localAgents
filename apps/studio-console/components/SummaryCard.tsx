/**
 * Big-number summary card. Used by Dashboard's headline strip.
 */

export default function SummaryCard({
  value,
  label,
  hint,
  variant,
}: {
  value: string;
  label: string;
  hint?: string;
  variant?: "success" | "info" | "warning" | "default";
}) {
  return (
    <div className="summary-card" data-variant={variant ?? "default"}>
      <div className="summary-card-value">{value}</div>
      <div className="summary-card-label">{label}</div>
      {hint && <div className="summary-card-hint">{hint}</div>}
    </div>
  );
}
