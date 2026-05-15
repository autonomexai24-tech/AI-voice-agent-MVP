import { SettingsForm } from "@/components/settings/SettingsForm";
import { ApiErrorPanel } from "@/components/ui/ApiErrorPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { getBusinessSettings } from "@/services/api";
import { emptyBusinessSettings } from "@/types/settings";
import { saveBusinessSettings } from "@/app/settings/actions";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  try {
    const settings = (await getBusinessSettings()) ?? emptyBusinessSettings();

    return (
      <div>
        <PageHeader
          title="Agent Settings"
          description="Business configuration used for future receptionist behavior."
        />
        <SettingsForm initialSettings={settings} action={saveBusinessSettings} />
      </div>
    );
  } catch (error) {
    return (
      <ApiErrorPanel
        title="Settings unavailable"
        message={error instanceof Error ? error.message : "Could not load settings"}
        retryHref="/settings"
      />
    );
  }
}
