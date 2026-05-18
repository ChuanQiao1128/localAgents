import Link from "next/link";

/**
 * Explicit 404 page.
 *
 * Without this file Next 15.5 auto-generates one but its prerender step
 * occasionally fails with `Cannot find module .../_not-found/page.js` when
 * `outputFileTracingRoot` is pinned (see next.config.mjs). Owning the route
 * here keeps the build deterministic.
 */
export default function NotFound() {
  return (
    <div className="design-empty" style={{ marginTop: "var(--sp-6)" }}>
      <h1 style={{ fontSize: 20, marginBottom: "var(--sp-3)" }}>
        404 · 页面不存在
      </h1>
      <p style={{ marginBottom: "var(--sp-4)" }}>
        这个路径不在 Studio Console 里。可能是旧 bookmark,或者写错了 URL。
      </p>
      <p>
        <Link
          href="/projects"
          style={{ color: "var(--color-info)", textDecoration: "underline" }}
        >
          回项目列表
        </Link>
      </p>
    </div>
  );
}
