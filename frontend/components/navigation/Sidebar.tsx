"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cx } from "@/lib/classNames";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/calls", label: "Calls" },
  { href: "/bookings", label: "Bookings" },
  { href: "/settings", label: "Agent Settings" }
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-[var(--line)] bg-[var(--panel)] px-4 py-5">
      <Link href="/dashboard" className="mb-8 block">
        <div className="text-[13px] font-semibold uppercase tracking-[0.18em] text-ink-600">
          AI Receptionist
        </div>
        <div className="mt-2 text-xl font-semibold tracking-normal text-ink-950">
          Operations
        </div>
      </Link>

      <nav className="space-y-1">
        {navItems.map((item) => {
          const isActive =
            pathname === item.href ||
            (item.href !== "/dashboard" && pathname.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cx(
                "block rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-[var(--moss-soft)] text-[var(--moss)]"
                  : "text-ink-600 hover:bg-white hover:text-ink-950"
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto rounded-md border border-[var(--line)] bg-white p-3">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-600">
          Runtime
        </div>
        <div className="mt-2 text-sm font-medium text-ink-950">Voice engine</div>
      </div>
    </aside>
  );
}
