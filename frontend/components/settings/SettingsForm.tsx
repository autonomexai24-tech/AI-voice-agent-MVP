"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";

import { DEFAULT_LANGUAGES, type SettingsActionState } from "@/types/settings";
import type { BusinessSettings } from "@/types/api";

export function SettingsForm({
  initialSettings,
  action
}: {
  initialSettings: BusinessSettings;
  action: (
    previousState: SettingsActionState,
    formData: FormData
  ) => Promise<SettingsActionState>;
}) {
  const [state, formAction] = useActionState(action, {
    status: "idle",
    message: null,
    settings: initialSettings
  });
  const settings = state.settings ?? initialSettings;

  return (
    <form
      action={formAction}
      className="grid grid-cols-[minmax(0,1fr)_360px] gap-6"
    >
      <section className="space-y-4 rounded-md border border-[var(--line)] bg-white p-5">
        <Field label="Business name" name="business_name" defaultValue={settings.business_name} />
        <Field label="Business type" name="business_type" defaultValue={settings.business_type} />
        <label className="block">
          <span className="text-sm font-semibold text-ink-950">Services</span>
          <textarea
            name="services"
            defaultValue={settings.services.join("\n")}
            rows={6}
            className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm leading-6 outline-none focus:border-[var(--steel)]"
          />
        </label>
        <Field
          label="Receptionist tone"
          name="receptionist_tone"
          defaultValue={settings.receptionist_tone ?? ""}
        />
        <label className="block">
          <span className="text-sm font-semibold text-ink-950">Greeting prompt</span>
          <textarea
            name="greeting_prompt"
            defaultValue={settings.greeting_prompt ?? ""}
            rows={4}
            className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm leading-6 outline-none focus:border-[var(--steel)]"
          />
        </label>
        <label className="block">
          <span className="text-sm font-semibold text-ink-950">Refusal policy</span>
          <textarea
            name="refusal_policy"
            defaultValue={settings.refusal_policy ?? ""}
            rows={4}
            className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm leading-6 outline-none focus:border-[var(--steel)]"
          />
        </label>
      </section>

      <aside className="space-y-4">
        <section className="rounded-md border border-[var(--line)] bg-white p-5">
          <label className="block">
            <span className="text-sm font-semibold text-ink-950">Default language</span>
            <select
              name="default_language"
              defaultValue={settings.default_language}
              className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm outline-none focus:border-[var(--steel)]"
            >
              {DEFAULT_LANGUAGES.map((language) => (
                <option key={language.value} value={language.value}>
                  {language.label}
                </option>
              ))}
            </select>
          </label>
        </section>

        <section className="rounded-md border border-[var(--line)] bg-white p-5">
          <div className="text-sm font-semibold text-ink-950">Sound realism</div>
          <label className="mt-4 flex items-center justify-between gap-4 text-sm text-ink-600">
            <span>Background ambience</span>
            <input type="checkbox" name="ambience_enabled" disabled className="h-4 w-4" />
          </label>
          <label className="mt-4 block text-sm text-ink-600">
            Realism profile
            <select
              name="realism_profile"
              disabled
              defaultValue="balanced"
              className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
            >
              <option value="balanced">Balanced</option>
              <option value="quiet">Quiet office</option>
              <option value="clinic">Clinic reception</option>
            </select>
          </label>
        </section>

        <section className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-5">
          <SubmitButton />
          {state.message ? (
            <div
              className={
                state.status === "error"
                  ? "mt-3 text-sm font-medium text-[var(--danger)]"
                  : "mt-3 text-sm font-medium text-[var(--moss)]"
              }
            >
              {state.message}
            </div>
          ) : null}
        </section>
      </aside>
    </form>
  );
}

function Field({
  label,
  name,
  defaultValue
}: {
  label: string;
  name: string;
  defaultValue: string;
}) {
  return (
    <label className="block">
      <span className="text-sm font-semibold text-ink-950">{label}</span>
      <input
        name={name}
        defaultValue={defaultValue}
        className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm outline-none focus:border-[var(--steel)]"
      />
    </label>
  );
}

function SubmitButton() {
  const status = useFormStatus();
  return (
    <button
      type="submit"
      disabled={status.pending}
      className="w-full rounded-md bg-ink-950 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-ink-800 disabled:cursor-not-allowed disabled:bg-ink-600"
    >
      {status.pending ? "Saving..." : "Save settings"}
    </button>
  );
}
