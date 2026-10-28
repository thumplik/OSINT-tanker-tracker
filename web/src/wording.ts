// Public wording. Hedged, observed-vs-typical only; never intent (CLAUDE.md rule 7, vocabulary
// from config/post_language.yaml). Numbers come straight from the alert record.
import type { AlertRow, SiteMeta } from "./types";

export const FAMILY_LABEL: Record<string, string> = {
  all: "All tracked",
  tanker: "Tankers",
  strategic_airlift: "Strategic airlift",
  tactical_airlift: "Tactical airlift",
};

// Singular, for "Tanker sorties", "Strategic airlift at KCRP".
const FAMILY_NOUN: Record<string, string> = {
  tanker: "tanker",
  strategic_airlift: "strategic airlift",
  tactical_airlift: "tactical airlift",
};

export function familyLabel(f: string): string {
  return FAMILY_LABEL[f] ?? f.replace(/_/g, " ");
}

export function regionName(meta: SiteMeta | null, id: string): string {
  return meta?.regions.find((r) => r.id === id)?.name ?? id;
}

function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function alertTitle(a: AlertRow, meta: SiteMeta | null): string {
  const f = String(a.evidence.family ?? "");
  const family = FAMILY_NOUN[f] ?? f.replace(/_/g, " ");
  const region = regionName(meta, a.region);
  if (a.detector === "D1") {
    return `${cap(family)} sorties above normal in ${region}`;
  }
  if (a.detector === "D2") {
    return `${cap(family)} at ${String(a.evidence.airfield ?? "?")}, not seen recently`;
  }
  return `${a.detector} in ${region}`;
}

export function windowHours(a: AlertRow): number {
  return Math.round((Date.parse(a.window_end) - Date.parse(a.window_start)) / 3_600_000);
}

export function alertNumbers(a: AlertRow): string {
  const h = windowHours(a);
  if (a.detector === "D1") {
    return `${fmt(a.observed)} sorties in ${h}h vs a typical ${fmt(a.expected)}`;
  }
  if (a.detector === "D2") {
    return `${fmt(a.observed)} aircraft arrived in ${h}h vs ${fmt(a.expected)} in the baseline period`;
  }
  return `observed ${fmt(a.observed)} vs expected ${fmt(a.expected)}`;
}

export const SEVERITY_LABEL: Record<string, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

export function utc(ts: string): string {
  return `${ts.slice(0, 10)} ${ts.slice(11, 16)}Z`;
}

export function hhmm(ts: string): string {
  return `${ts.slice(11, 16)}Z`;
}
