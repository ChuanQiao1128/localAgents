/**
 * Placeholder content rendered on every Console page until its named
 * RC-5A subtask fleshes it out. Lets the scaffold (RC-5A.1) ship a
 * fully clickable shell without leaking unfinished UI to the operator.
 */

export default function PageStub({
  title,
  subtitle,
  subtask,
  what,
}: {
  title: string;
  subtitle: string;
  subtask: string;
  what: string;
}) {
  return (
    <>
      <h1 className="page-title">{title}</h1>
      <p className="page-subtitle">{subtitle}</p>
      <div className="stub-banner">
        <strong>Coming in {subtask}</strong>
        {what}
      </div>
    </>
  );
}
