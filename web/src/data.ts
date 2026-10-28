import type { DayR6, DayView, SiteIndex, SiteMeta } from "./types";

const BASE = (import.meta.env.VITE_DATA_BASE_URL ?? "/data").replace(/\/$/, "");

export class NotFound extends Error {}

const cache = new Map<string, Promise<unknown>>();

async function getJson<T>(path: string, fresh = false): Promise<T> {
  const key = path;
  if (!fresh && cache.has(key)) return cache.get(key) as Promise<T>;
  const p = fetch(`${BASE}/${path}`).then(async (r) => {
    if (r.status === 404 || r.status === 403) throw new NotFound(path);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return (await r.json()) as T;
  });
  cache.set(key, p);
  p.catch(() => cache.delete(key));
  return p;
}

export const loadMeta = () => getJson<SiteMeta>("meta.json");
export const loadIndex = () => getJson<SiteIndex>("index.json", true);
export const loadDay = (date: string) => getJson<DayView>(`days/${date}.json`);
export const loadR6 = (date: string) => getJson<DayR6>(`days/${date}.r6.json`);
