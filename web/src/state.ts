// View state lives in the URL so every view is a shareable link (SPEC §11).

export type Tab = "alerts" | "regions" | "sorties";

export interface ViewState {
  date: string | null; // null: latest
  family: string; // a family id or "all"
  region: string | null;
  res: 4 | 6;
  hour: number | null; // null: whole day
  tab: Tab;
}

export const DEFAULT_STATE: ViewState = {
  date: null,
  family: "all",
  region: null,
  res: 4,
  hour: null,
  tab: "alerts",
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TABS: Tab[] = ["alerts", "regions", "sorties"];

export function parseState(search: string): ViewState {
  const q = new URLSearchParams(search);
  const date = q.get("date");
  const hour = q.get("hour");
  const h = hour !== null && /^\d{1,2}$/.test(hour) ? Number(hour) : null;
  const tab = q.get("tab") as Tab | null;
  return {
    date: date && DATE.test(date) ? date : null,
    family: q.get("family") || DEFAULT_STATE.family,
    region: q.get("region") || null,
    res: q.get("res") === "6" ? 6 : 4,
    hour: h !== null && h >= 0 && h <= 23 ? h : null,
    tab: tab && TABS.includes(tab) ? tab : DEFAULT_STATE.tab,
  };
}

export function serializeState(s: ViewState): string {
  const q = new URLSearchParams();
  if (s.date) q.set("date", s.date);
  if (s.family !== DEFAULT_STATE.family) q.set("family", s.family);
  if (s.region) q.set("region", s.region);
  if (s.res !== DEFAULT_STATE.res) q.set("res", String(s.res));
  if (s.hour !== null && s.res === 4) q.set("hour", String(s.hour));
  if (s.tab !== DEFAULT_STATE.tab) q.set("tab", s.tab);
  const out = q.toString();
  return out ? `?${out}` : "";
}

/** The neighbouring available day (dates are ISO strings, so they sort as text). */
export function stepDate(days: string[], current: string, delta: -1 | 1): string | null {
  const i = days.indexOf(current);
  if (i < 0) return null;
  return days[i + delta] ?? null;
}
