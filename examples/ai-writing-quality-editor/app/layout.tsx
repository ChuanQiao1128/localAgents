import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Writing Quality Editor",
  description: "RC-4B demo 1 — deterministic writing analyzer.",
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
