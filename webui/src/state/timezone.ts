import { computed, ref, watch } from "vue";

const STORAGE_KEY = "fcam_display_timezone";

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function loadStored(): string | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v && typeof v === "string" && v.trim()) return v.trim();
  } catch {
    // ignore
  }
  return null;
}

/** null/empty means "follow browser" */
const storedTimezone = ref<string | null>(loadStored());

export const displayTimezone = computed(() => storedTimezone.value || browserTimezone());

export const timezoneFollowBrowser = computed(() => !storedTimezone.value);

export function setDisplayTimezone(tz: string | null) {
  const next = (tz || "").trim();
  if (!next || next === browserTimezone()) {
    storedTimezone.value = null;
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    return;
  }
  storedTimezone.value = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // ignore
  }
}

export function resetTimezoneToBrowser() {
  setDisplayTimezone(null);
}

/** Common IANA zones for the picker (browser zone always prepended if missing). */
export const COMMON_TIMEZONES: string[] = [
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Asia/Seoul",
  "Asia/Bangkok",
  "Asia/Kolkata",
  "UTC",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Australia/Sydney",
];

export function timezoneOptions(): { label: string; value: string }[] {
  const browser = browserTimezone();
  const set = new Set<string>([browser, ...COMMON_TIMEZONES]);
  if (storedTimezone.value) set.add(storedTimezone.value);
  return Array.from(set).map((z) => ({
    label: z === browser ? `${z}（浏览器）` : z,
    value: z,
  }));
}

// Keep reactive if user changes system TZ mid-session (rare); re-read on storage events from other tabs.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (ev) => {
    if (ev.key === STORAGE_KEY) {
      storedTimezone.value = loadStored();
    }
  });
}

// Touch watch so tree-shaking keeps ref live when only displayTimezone is imported
watch(storedTimezone, () => undefined);
