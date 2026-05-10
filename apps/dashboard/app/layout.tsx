import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Local Agent Dev Studio",
  description: "Local workflow, task, artifact, and approval dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
