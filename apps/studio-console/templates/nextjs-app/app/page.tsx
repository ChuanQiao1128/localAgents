export default function Page() {
  return (
    <main
      style={{
        maxWidth: 720,
        margin: "0 auto",
        padding: "64px 24px",
      }}
    >
      <h1 style={{ fontSize: 28, marginBottom: 12 }}>
        Studio Runtime App — baseline
      </h1>
      <p style={{ color: "#64748b", fontSize: 15, lineHeight: 1.6 }}>
        This page is the empty baseline scaffolded by Local Agent Studio.
        Studio's autonomous loop will replace it with the contents of your
        MVP requirements when you click <strong>Start Development</strong>.
      </p>
      <p style={{ color: "#64748b", fontSize: 13, marginTop: 24 }}>
        If you can read this in the browser at{" "}
        <code>localhost:3000</code> after running <code>npm run dev</code>,
        the runtime project bootstrapped successfully.
      </p>
    </main>
  );
}
