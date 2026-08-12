/**
 * Time formatting helpers. Absolute timestamps respect the UI timezone preference
 * (localStorage; default = browser timezone). Relative times are timezone-independent.
 */
import { displayTimezone } from "@/state/timezone";

function activeTz(): string {
  return displayTimezone.value || "UTC";
}

function partsInZone(date: Date, timeZone: string): Record<string, string> {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
  });
  const out: Record<string, string> = {};
  for (const p of fmt.formatToParts(date)) {
    if (p.type !== "literal") out[p.type] = p.value;
  }
  // Some engines emit hour "24" for midnight — normalize.
  if (out.hour === "24") out.hour = "00";
  return out;
}

/**
 * Format ISO timestamp as YYYY-MM-DD HH:mm:ss in the selected display timezone.
 */
export function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";

  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp;

    const p = partsInZone(date, activeTz());
    const year = p.year ?? "????";
    const month = p.month ?? "??";
    const day = p.day ?? "??";
    const hours = p.hour ?? "??";
    const minutes = p.minute ?? "??";
    const seconds = p.second ?? "??";
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
  } catch {
    return timestamp;
  }
}

/**
 * Relative time (e.g. "5 分钟前" / "55 分钟后"). Independent of display timezone.
 */
export function formatRelativeTime(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";

  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp;

    const diffMs = date.getTime() - Date.now();
    const absSeconds = Math.floor(Math.abs(diffMs) / 1000);
    const isFuture = diffMs > 0;

    if (absSeconds < 5) return isFuture ? "马上" : "刚刚";
    if (absSeconds < 60) return `${absSeconds} 秒${isFuture ? "后" : "前"}`;

    const absMinutes = Math.floor(absSeconds / 60);
    if (absMinutes < 60) return `${absMinutes} 分钟${isFuture ? "后" : "前"}`;

    const absHours = Math.floor(absMinutes / 60);
    if (absHours < 24) return `${absHours} 小时${isFuture ? "后" : "前"}`;

    const absDays = Math.floor(absHours / 24);
    if (absDays < 7) return `${absDays} 天${isFuture ? "后" : "前"}`;

    return formatTimestamp(timestamp);
  } catch {
    return timestamp;
  }
}

export function formatDate(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";
  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp;
    const p = partsInZone(date, activeTz());
    return `${p.year ?? "????"}-${p.month ?? "??"}-${p.day ?? "??"}`;
  } catch {
    return timestamp;
  }
}

/** HH:mm in display timezone (charts). */
export function formatClockTime(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";
  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp;
    const p = partsInZone(date, activeTz());
    return `${p.hour ?? "??"}:${p.minute ?? "??"}`;
  } catch {
    return timestamp;
  }
}
