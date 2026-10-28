import type { Heat } from "./types";

export interface Cell {
  hex: string;
  n: number;
}

/**
 * Cells to draw for one family. r4 rows are [cell, hour, family, n] (distinct aircraft in that
 * hour); a single hour shows aircraft, the whole day sums hours into aircraft-hours. r6 rows are
 * [cell, family, n]: distinct aircraft over the whole day.
 */
export function cellsFor(heat: Heat, family: string, hour: number | null): Cell[] {
  const f = heat.families.indexOf(family);
  if (f < 0) return [];
  const sums = new Map<number, number>();
  for (const row of heat.rows) {
    if (row.length === 4) {
      const [c, h, fam, n] = row;
      if (fam !== f || (hour !== null && h !== hour)) continue;
      sums.set(c, (sums.get(c) ?? 0) + n);
    } else {
      const [c, fam, n] = row;
      if (fam === f) sums.set(c, (sums.get(c) ?? 0) + n);
    }
  }
  return [...sums].map(([c, n]) => ({ hex: heat.cells[c], n }));
}

/** Sequential blue ramp, light -> dark (dataviz reference palette, steps 100..700). */
export const RAMP: [number, number, number][] = [
  [0xcd, 0xe2, 0xfb],
  [0x9e, 0xc5, 0xf4],
  [0x6d, 0xa7, 0xec],
  [0x39, 0x87, 0xe5],
  [0x25, 0x6a, 0xbf],
  [0x18, 0x4f, 0x95],
  [0x0d, 0x36, 0x6b],
];

/** Log-scaled bucket: counts span 1..hundreds, so a linear scale would leave most cells pale. */
export function bucket(n: number, max: number): number {
  if (max <= 1) return RAMP.length - 1;
  const t = Math.log(n) / Math.log(max);
  return Math.min(RAMP.length - 1, Math.max(0, Math.floor(t * RAMP.length)));
}
