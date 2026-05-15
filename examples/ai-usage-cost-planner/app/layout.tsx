import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Usage & Cost Planner",
  description: "RC-4B demo 2 — deterministic AI cost estimator.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
