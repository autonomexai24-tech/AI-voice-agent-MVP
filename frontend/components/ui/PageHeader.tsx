import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  action
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex items-start justify-between gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-normal text-ink-950">
          {title}
        </h1>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-ink-600">
          {description}
        </p>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
