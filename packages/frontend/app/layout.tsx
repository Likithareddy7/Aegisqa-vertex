import type { Metadata } from "next";
import "./globals.css";
import { AuthGuard } from "../components/AuthGuard";

export const metadata: Metadata = {
  title: "AegisQA — Agentic Testing",
  description: "Generate, edit, and execute Playwright tests through chat.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthGuard>{children}</AuthGuard>
      </body>
    </html>
  );
}
