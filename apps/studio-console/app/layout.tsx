import type { Metadata } from "next";
import "./globals.css";
import SidebarNav from "@/components/SidebarNav";
import TopBar from "@/components/TopBar";

export const metadata: Metadata = {
  title: "Local Agent Studio Console",
  description:
    "Local Agent Studio 的本地前端控制台：需求合同 → 开发前检查 → 运行监控 → 人工审核 / 交付证据 → 变更请求。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="shell">
          <SidebarNav />
          <TopBar />
          <main className="content">{children}</main>
          <footer className="footer">
            Local Agent Studio Console &middot; v0.1 &middot; 读
            <code> .agent-studio/</code> &middot; 写
            <code> .studio-console/</code>
          </footer>
        </div>
      </body>
    </html>
  );
}
