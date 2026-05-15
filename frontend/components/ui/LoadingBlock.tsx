export function LoadingBlock() {
  return (
    <div className="space-y-4">
      <div className="h-8 w-80 animate-pulse rounded-md bg-[var(--line)]" />
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
        {Array.from({ length: 5 }).map((_, index) => (
          <div
            key={index}
            className="h-32 animate-pulse rounded-md bg-[var(--panel)]"
          />
        ))}
      </div>
      <div className="h-96 animate-pulse rounded-md bg-[var(--panel)]" />
    </div>
  );
}
