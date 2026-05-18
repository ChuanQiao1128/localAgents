"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * 左侧导航 —— RC-5A.12.2 起的最终结构。
 *
 * 只剩两个入口：总览 + 项目。
 * 旧的 6 页（Design / Plan / Run / Review / Evidence / Change Request）已经
 * 在 RC-5A.12.2 被改成 deprecation stub —— 它们仍然占用 URL（防止 bookmark
 * 死链），但不在主导航出现。所有日常工作都通过项目工作台完成。
 *
 * 锁定 spec: docs/STUDIO_CONSOLE_SPEC.md § 5。
 */

type NavEntry = {
  num: string;
  label: string;
  hint: string;
  href: string;
  matchPrefix?: string;
};

const PRIMARY: readonly NavEntry[] = [
  { num: "1", label: "总览", hint: "Dashboard", href: "/dashboard" },
  {
    num: "2",
    label: "项目",
    hint: "Projects",
    href: "/projects",
    matchPrefix: "/projects",
  },
];

export default function SidebarNav() {
  const pathname = usePathname();

  function isActive(entry: NavEntry): boolean {
    if (pathname === entry.href) return true;
    const prefix = entry.matchPrefix ?? entry.href;
    if (prefix === "/") return false;
    return pathname?.startsWith(prefix) === true;
  }

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <strong>Local Agent Studio</strong>
        <small>本地控制台</small>
      </div>
      <div className="sidebar-nav">
        {PRIMARY.map((entry) => (
          <Link
            key={entry.href}
            href={entry.href}
            className={isActive(entry) ? "nav-item active" : "nav-item"}
            title={entry.hint}
          >
            <span className="nav-item-num">{entry.num}</span>
            <span className="nav-item-text">
              <span className="nav-item-label">{entry.label}</span>
              <span className="nav-item-hint">{entry.hint}</span>
            </span>
          </Link>
        ))}
      </div>
    </nav>
  );
}
