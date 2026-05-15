export function TopHeader() {
  return (
    <header className="flex h-16 items-center justify-between border-b border-[var(--line)] bg-[rgba(251,250,247,0.86)] px-8">
      <div>
        <div className="text-sm font-semibold text-ink-950">
          Receptionist control panel
        </div>
        <div className="text-xs text-ink-600">Operations workspace</div>
      </div>
      <div className="rounded-md border border-[var(--line)] bg-white px-3 py-1.5 text-xs font-medium text-ink-600">
        Local MVP
      </div>
    </header>
  );
}
