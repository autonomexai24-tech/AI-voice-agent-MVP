import type { Metadata } from "next";
import type { ReactNode } from "react";

import "@/app/globals.css";
import { AppShell } from "@/layouts/AppShell";
import { logFrontendRuntimeReady } from "@/lib/logging";
import { logFrontendEnvironment } from "@/services/endpoints";

export const metadata: Metadata = {
  title: "AI Receptionist Operations",
  description: "Operational frontend for calls, bookings, transcripts, and agent settings."
};

export default function RootLayout({ children }: { children: ReactNode }) {
  logFrontendEnvironment();
  logFrontendRuntimeReady();

  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
