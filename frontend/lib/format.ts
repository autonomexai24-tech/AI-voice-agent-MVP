export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Not recorded";
  }
  const timestamp = toTimestamp(value);
  if (timestamp === null) {
    return value;
  }
  const date = new Date(timestamp);
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) {
    return "Open";
  }
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return remaining ? `${minutes}m ${remaining}s` : `${minutes}m`;
}

export function formatValue(value: string | null | undefined): string {
  return value && value.trim() ? value : "Not set";
}

export function titleCase(value: string | null | undefined): string {
  if (!value) {
    return "Not set";
  }
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

export function compareDateTime(
  first: string | null | undefined,
  second: string | null | undefined
): number {
  const firstTimestamp = toTimestamp(first);
  const secondTimestamp = toTimestamp(second);
  if (firstTimestamp === null && secondTimestamp === null) {
    return 0;
  }
  if (firstTimestamp === null) {
    return 1;
  }
  if (secondTimestamp === null) {
    return -1;
  }
  return firstTimestamp - secondTimestamp;
}

function toTimestamp(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
}
