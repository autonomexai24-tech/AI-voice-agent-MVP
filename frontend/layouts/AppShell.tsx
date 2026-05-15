import type { ReactNode } from "react";

import { Sidebar } from "@/components/navigation/Sidebar";
import { TopHeader } from "@/components/navigation/TopHeader";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-[var(--surface)] text-ink-950">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopHeader />
        <main className="mx-auto w-full max-w-[1440px] flex-1 px-8 py-7">
          {children}
        </main>
      </div>
    </div>
  );
}
