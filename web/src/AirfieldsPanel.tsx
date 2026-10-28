import { useState } from "react";
import { airfieldCards, type CardSort, extreme, type Grouped } from "./overview";
import { AirfieldCard, TrendChart } from "./OverviewPanel";
import { byLabel } from "./theater";
import type { AirfieldStat, DayView, SiteMeta } from "./types";
import { familyLabel, regionName } from "./wording";

const SORTS: [CardSort, string][] = [
  ["ground", "Most on the ground"],
  ["above", "Furthest above average"],
  ["lost", "Most left and not seen since"],
  ["name", "A–Z"],
];

function signed(n: number): string {
  return n > 0 ? `+${n}` : String(n);
}

/** One airfield's "baseball card": today against its last weeks. */
function StatCard({
  s,
  name,
  ccmd,
  types,
  last,
  selected,
  onPick,
}: {
  s: AirfieldStat;
  name: string;
  ccmd: string;
  types: string;
  last: string;
  selected: boolean;
  onPick: () => void;
}) {
  const ext = extreme(s);
  const delta = s.mean === null ? null : Math.round((s.ground - s.mean) * 10) / 10;
  return (
    <button className={`bcard ${selected ? "sel" : ""}`} onClick={onPick} aria-pressed={selected}>
      <span className="bc-top">
        <b>{s.airfield}</b>
        <span className="bc-ccmd">{ccmd}</span>
      </span>
      <span className="bc-name">{name}</span>
      <span className="bc-hero">
        <span className="bc-big">{s.ground}</span>
        <span className="bc-unit">
          on the ground
          {delta !== null && <span className="muted"> · {signed(delta)} vs avg</span>}
        </span>
      </span>
      {ext && (
        <span className="bc-flag">
          {ext === "high" ? "Above" : "Below"} its {s.days}-day range
        </span>
      )}
      <span className="bc-types">{types || " "}</span>
      <span className="bc-chart">
        <TrendChart
          values={[...s.series, s.ground]}
          last={last}
          typical={s.mean}
          height={48}
          label={`${s.airfield} aircraft on the ground`}
        />
      </span>
      <span className="bc-stats">
        <span>
          <small>Avg</small>
          {s.mean ?? "–"}
        </span>
        <span>
          <small>Min</small>
          {s.min ?? "–"}
        </span>
        <span>
          <small>Max</small>
          {s.max ?? "–"}
        </span>
      </span>
      <span className="bc-out" title="Aircraft whose last flight left here">
        Left here: <b>{s.airborne}</b> airborne · <b>{s.lost}</b> not seen since
      </span>
    </button>
  );
}

export function AirfieldsPanel({
  view,
  meta,
  family,
  ccmd,
  onCcmd,
  grouped,
  field,
  onField,
  at,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
  ccmd: string | null;
  onCcmd: (id: string | null) => void;
  grouped: Grouped;
  field: string | null;
  onField: (code: string | null) => void;
  at: string;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<CardSort>("ground");
  const th = view.theaters;
  if (!th?.airfield_stats || !th.airfields) {
    return <p className="muted">This day was exported before airfield cards existed.</p>;
  }
  const name = (id: string) => regionName(meta, id);
  const cards = airfieldCards(th.airfield_stats, th.airfields, family, ccmd, q, sort);
  const groups = new Map(grouped.fields.map((f) => [f.code, f]));
  const selected = field ? groups.get(field) : undefined;
  const days = view.meta.baseline_window_days;
  return (
    <div className="airfields">
      {selected && <AirfieldCard f={selected} at={at} name={name} onClose={() => onField(null)} />}
      <div className="filters">
        <input
          type="search"
          placeholder="ICAO or name"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Filter airfields"
        />
        <select
          value={ccmd ?? ""}
          onChange={(e) => onCcmd(e.target.value || null)}
          aria-label="Combatant command"
        >
          <option value="">All commands</option>
          {(meta?.ccmds ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as CardSort)}
          aria-label="Sort airfields"
        >
          {SORTS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <p className="muted small">
        {cards.length} airfields with {familyLabel(family).toLowerCase()}. Each card: aircraft whose
        latest position is on the ground there, against the previous {days} days (avg, min, max, and
        the chart); and aircraft whose last flight left there, still airborne or not seen since.
      </p>
      <div className="bcards">
        {cards.map((s) => {
          const info = th.airfields![s.airfield];
          const types = byLabel(
            (groups.get(s.airfield)?.ground ?? []).reduce<Record<string, number>>((m, p) => {
              m[p.type] = (m[p.type] ?? 0) + 1;
              return m;
            }, {}),
          )
            .map(([t, n]) => `${n} ${t}`)
            .join(" · ");
          return (
            <StatCard
              key={s.airfield}
              s={s}
              name={info.name}
              ccmd={name(info.ccmd)}
              types={types}
              last={view.meta.date}
              selected={s.airfield === field}
              onPick={() => onField(s.airfield === field ? null : s.airfield)}
            />
          );
        })}
      </div>
    </div>
  );
}
