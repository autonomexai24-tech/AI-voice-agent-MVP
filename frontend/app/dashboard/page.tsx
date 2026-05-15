import Link from "next/link";
import type { ReactNode } from "react";

import { CallTable } from "@/components/calls/CallTable";
import { BookingTable } from "@/components/bookings/BookingTable";
import { ApiErrorPanel } from "@/components/ui/ApiErrorPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard } from "@/components/ui/StatCard";
import { getDashboardData } from "@/services/dashboard";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  try {
    const data = await getDashboardData();

    return (
      <div>
        <PageHeader
          title="Dashboard"
          description="Recent call activity, persisted bookings, and appointment completion."
        />
        <section className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
          {data.metrics.map((metric) => (
            <StatCard
              key={metric.label}
              label={metric.label}
              value={metric.value}
              detail={metric.detail}
              tone={metric.tone}
            />
          ))}
        </section>

        <section className="mt-8 grid grid-cols-1 gap-7">
          <Panel
            title="Recent calls"
            href="/calls"
            linkLabel="View calls"
          >
            <CallTable calls={data.calls.slice(0, 6)} />
          </Panel>
          <Panel
            title="Recent bookings"
            href="/bookings"
            linkLabel="View bookings"
          >
            <BookingTable bookings={data.bookings.slice(0, 6)} />
          </Panel>
        </section>
      </div>
    );
  } catch (error) {
    return (
      <ApiErrorPanel
        title="Dashboard unavailable"
        message={error instanceof Error ? error.message : "Could not load dashboard data"}
        retryHref="/dashboard"
      />
    );
  }
}

function Panel({
  title,
  href,
  linkLabel,
  children
}: {
  title: string;
  href: string;
  linkLabel: string;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-ink-950">{title}</h2>
        <Link className="text-sm font-semibold text-[var(--steel)]" href={href}>
          {linkLabel}
        </Link>
      </div>
      {children}
    </section>
  );
}
