import { CallTable } from "@/components/calls/CallTable";
import { ApiErrorPanel } from "@/components/ui/ApiErrorPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { listCalls } from "@/services/api";

export const dynamic = "force-dynamic";

export default async function CallsPage() {
  try {
    const response = await listCalls(100);

    return (
      <div>
        <PageHeader
          title="Calls"
          description="Recent call records with language, duration, booking outcome, and timestamps."
        />
        <CallTable calls={response.items} />
      </div>
    );
  } catch (error) {
    return (
      <ApiErrorPanel
        title="Calls unavailable"
        message={error instanceof Error ? error.message : "Could not load calls"}
        retryHref="/calls"
      />
    );
  }
}
