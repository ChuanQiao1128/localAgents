const phases = [
  { name: "Intake", status: "done" },
  { name: "Research", status: "done" },
  { name: "PRD", status: "approval" },
  { name: "Design", status: "pending" },
  { name: "Architecture", status: "pending" },
  { name: "Implementation", status: "pending" },
  { name: "QA", status: "pending" },
  { name: "Review", status: "pending" },
  { name: "Merge", status: "pending" },
];

const tasks = [
  ["INTAKE-001", "Run intake phase", "done"],
  ["RESEARCH-001", "Generate research artifact", "done"],
  ["PRD-001", "Approve product scope", "review"],
  ["DESIGN-001", "Generate design system", "pending"],
  ["ARCH-001", "Generate task DAG", "pending"],
];

export default function Page() {
  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark" />
          <div>
            <strong>Local Agent Dev Studio</strong>
            <small>localhost</small>
          </div>
        </div>
        <nav>
          <a className="active">Overview</a>
          <a>Task Board</a>
          <a>Artifacts</a>
          <a>Approvals</a>
          <a>Settings</a>
        </nav>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h1>Project Overview</h1>
            <p>Workflow status, task board, artifacts, and approval gates.</p>
          </div>
          <button type="button">Approve PRD</button>
        </header>

        <section className="phaseBand" aria-label="Workflow phases">
          {phases.map((phase) => (
            <div className="phase" data-status={phase.status} key={phase.name}>
              <span />
              <strong>{phase.name}</strong>
              <small>{phase.status}</small>
            </div>
          ))}
        </section>

        <section className="grid">
          <div className="panel wide">
            <div className="panelHead">
              <h2>Task Board</h2>
              <small>pending / running / review / done</small>
            </div>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Task</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map(([id, title, status]) => (
                  <tr key={id}>
                    <td>{id}</td>
                    <td>{title}</td>
                    <td>
                      <span className="pill">{status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <div className="panelHead">
              <h2>Latest Artifacts</h2>
              <small>local files</small>
            </div>
            <ul className="artifactList">
              <li>docs/product/prd.md</li>
              <li>docs/design/component-spec.md</li>
              <li>docs/architecture/api.openapi.yaml</li>
              <li>docs/review/review-report.md</li>
            </ul>
          </div>
        </section>
      </section>
    </main>
  );
}

