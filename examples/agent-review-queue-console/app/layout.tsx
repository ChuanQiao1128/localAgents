import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Review Queue Console",
  description: "RC-4B demo 3 — agent workflow review console.",
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
