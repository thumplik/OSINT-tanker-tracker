// Shapes of the files written by `tw export` (src/tw/views/day.py). Schema version 1.

export type Tier = "official" | "provisional";

export interface Region {
  id: string;
  name: string;
  status: "watch" | "alerting";
  boxes: [number, number, number, number][]; // [lat_min, lat_max, lon_min, lon_max]
}

export interface SiteMeta {
  schema: number;
  regions: Region[];
  families: Record<string, string[]>;
  attribution: string[];
  official_alerts_from: string | null;
}

export interface IndexDay {
  date: string;
  tier: Tier;
  alerts: number;
  provisional_alerts: number;
}

export interface SiteIndex {
  schema: number;
  days: IndexDay[];
  latest: string | null;
}

export interface Heat {
  families: string[];
  cells: string[];
  rows: number[][]; // r4: [cell, hour, family, n]; r6: [cell, family, n]
}

export interface RegionRow {
  region: string;
  family: string;
  status: Region["status"] | null;
  observed: number;
  aircraft: number;
  median: number | null;
  mad: number | null;
  n_days: number;
  z: number | null;
}

export interface CoverageRow {
  region: string;
  aircraft_hours: number;
  median: number | null;
  ratio: number | null;
}

export interface AlertRow {
  alert_id: string;
  tier: Tier;
  detector: string;
  region: string;
  window_start: string;
  window_end: string;
  observed: number;
  expected: number;
  score: number;
  severity: string;
  status: string;
  evidence: Record<string, unknown>;
  sortie_ids: string[];
  created_at: string;
}

export interface SortieRow {
  sortie_id: string;
  hex: string;
  type: string | null;
  families: string[];
  callsign: string | null;
  start: string;
  end: string;
  minutes: number;
  origin: string | null;
  dest: string | null;
  regions: string[];
  max_alt: number | null;
  end_reason: string | null;
  is_open: boolean;
  counted: boolean;
}

export interface DayView {
  meta: {
    schema: number;
    date: string;
    tier: Tier;
    baseline_as_of: string | null;
    weekday: number;
    min_sortie_minutes: number;
    min_baseline_samples: number;
    baseline_window_days: number;
    d1_min_z: number;
    coverage_min_ratio: number;
    coverage_max_ratio: number;
    generated_at: string;
    config_hash: string;
    code_version: string;
  };
  heat: Heat;
  regions: RegionRow[];
  coverage: CoverageRow[];
  alerts: AlertRow[];
  sorties: SortieRow[];
}

export interface DayR6 extends Heat {
  date: string;
  tier: Tier;
}
