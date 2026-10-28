// Theater (CCMD) helpers: type labels, transit arcs. Families are never summed together.
import type { Ccmd, TransitRow } from "./types";

export const TYPE_LABEL: Record<string, string> = {
  K35R: "KC-135R",
  KC46: "KC-46A",
  B762: "KC-46A", // adsb.lol types the KC-46A as B762 (military hex block only)
  C17: "C-17A",
  C5M: "C-5M",
  C130: "C-130",
  C30J: "C-130J-30",
};

/** Aircraft by display type, merging codes that are the same aircraft (B762 = KC-46A). */
export function byLabel(byType: Record<string, number>): [string, number][] {
  const out = new Map<string, number>();
  for (const [code, n] of Object.entries(byType)) {
    const label = TYPE_LABEL[code] ?? code;
    out.set(label, (out.get(label) ?? 0) + n);
  }
  return [...out.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export interface Arc {
  dest: string;
  count: number;
  from: [number, number];
  to: [number, number];
}

/** One arc per destination: trips from home of the given family (or all) resolved in the window. */
export function transitArcs(
  transits: TransitRow[],
  ccmds: Ccmd[],
  home: string,
  family: string,
): Arc[] {
  const anchor = new Map(ccmds.map((c) => [c.id, c.anchor]));
  const from = anchor.get(home);
  if (!from) return [];
  const counts = new Map<string, number>();
  for (const t of transits) {
    if (family !== "all" && t.family !== family) continue;
    if (t.dest === home) continue;
    counts.set(t.dest, (counts.get(t.dest) ?? 0) + 1);
  }
  return [...counts.entries()]
    .filter(([dest]) => anchor.has(dest))
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([dest, count]) => ({ dest, count, from, to: anchor.get(dest)! }));
}

/** Where a trip went, in words: "CENTCOM via EUCOM". */
export function route(t: TransitRow, name: (id: string) => string): string {
  return t.via.length ? `${name(t.dest)} via ${t.via.map(name).join(", ")}` : name(t.dest);
}
