export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

export function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatRelative(value: string) {
  const then = new Date(value).getTime();
  const now = Date.now();
  const diff = then - now;
  const minutes = Math.round(diff / 60000);
  if (Math.abs(minutes) < 1) return "just now";
  if (Math.abs(minutes) < 60) return relative(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return relative(hours, "hour");
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return relative(days, "day");
  return formatDate(value);
}

function relative(value: number, unit: Intl.RelativeTimeFormatUnit) {
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
    value,
    unit,
  );
}

export function retentionLabel(lastForegroundAt: string, days = 30) {
  const expires = new Date(lastForegroundAt).getTime() + days * 86_400_000;
  const remaining = Math.ceil((expires - Date.now()) / 86_400_000);
  if (remaining <= 0) return "Expired";
  if (remaining === 1) return "Expires tomorrow";
  if (remaining < 30) return `Expires in ${remaining} days`;
  return `Expires ${formatDate(new Date(expires).toISOString())}`;
}

export function preparationLabel(state: string) {
  if (state === "ready") return "Ready";
  if (state === "queued") return "Preparing";
  if (state === "failed") return "Needs attention";
  return state;
}

export function percent(value: number, total: number) {
  if (!total) return "0%";
  return `${Math.round((value / total) * 100)}%`;
}
