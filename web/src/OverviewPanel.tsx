import { useMemo, useRef, useState } from "react";
import { dayBefore, standout, totalSeries, typicalOf, whereNow } from "./overview";
import { byLabel } from "./theater";
import type { AlertRow, DayView, SiteMeta } from "./types";
import { alertNumbers, alertTitle, familyLabel, regionName } from "./wording";

// ---- Trend chart ---------------------------------------------------------------------------

const W = 320;

function TrendChart({
  values,
  last,
  typical,
  height = 120,
  label,
}: {
  values: (number | null)[];
  last: string;
  typical: number | null;
  height?: number;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<SVGSVGElement>(null);
  const pad = { l: 4, r: 30, t: 8, b: 16 };
  const nums = values.filter((v): v is number => v !== null);
  const max = Math.max(1, ...nums, typical ?? 0);
  const n = values.length;
  const x = (i: number) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  let d = "";
  values.forEach((v, i) => {
    if (v === null) return;
    d += `${d && values[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  const lastVal = values[n - 1];
  const onMove = (e: React.PointerEvent) => {
    const r = box.current?.getBoundingClientRect();
    if (!r || n < 2) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const months = [];
  for (let i = 0; i < n; i++) {
    const date = dayBefore(last, n - 1 - i);
    if (date.endsWith("-01"))
      months.push({
        i,
        text: date.slice(5, 7) === "01" ? date.slice(0, 4) : MONTH[Number(date.slice(5, 7)) - 1],
      });
  }
  const hv = hover !== null ? values[hover] : null;
  return (
    <div className="trend">
      <svg
        ref={box}
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`${label}: ${n} days to ${last}; latest ${lastVal ?? "missing"}, typical ${typical ?? "unknown"}`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        <line className="axis" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
        <text className="tick" x={W - pad.r + 4} y={y(max) + 4}>
          {max}
        </text>
        <text className="tick" x={W - pad.r + 4} y={y(0) + 3}>
          0
        </text>
        {months.map((m) => (
          <text key={m.i} className="tick" x={x(m.i)} y={height - 3} textAnchor="middle">
            {m.text}
          </text>
        ))}
        {typical !== null && (
          <>
            <line className="typ" x1={pad.l} x2={W - pad.r} y1={y(typical)} y2={y(typical)} />
            <text className="tick" x={W - pad.r + 4} y={y(typical) + 3}>
              typ
            </text>
          </>
        )}
        <path className="series" d={d} />
        {lastVal != null && <circle className="dot" cx={x(n - 1)} cy={y(lastVal)} r={3.5} />}
        {hover !== null && (
          <>
            <line className="cross" x1={x(hover)} x2={x(hover)} y1={pad.t} y2={y(0)} />
            {hv != null && <circle className="dot" cx={x(hover)} cy={y(hv)} r={3.5} />}
          </>
        )}
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          {dayBefore(last, n - 1 - hover)}: <b>{hv ?? "no data"}</b>
        </div>
      )}
    </div>
  );
}

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ---- Panel ---------------------------------------------------------------------------------

function signed(n: number): string {
  return n > 0 ? `+${n}` : String(n);
}

function Standout({
  a,
  meta,
  onPick,
}: {
  a: AlertRow;
  meta: SiteMeta | null;
  onPick: (a: AlertRow) => void;
}) {
  const cov = a.evidence.coverage_ratio;
  return (
    <li>
      <button className="so" onClick={() => onPick(a)}>
        <span className="so-title">{alertTitle(a, meta)}</span>
        <span className="so-nums">{alertNumbers(a)}</span>
        <span className="so-meta">
          {a.detector}
          {typeof cov === "number" && ` · receiver coverage ${Math.round(cov * 100)}% of usual`}
          {a.evidence.coverage === "unchecked" && " · live data, coverage not checked yet"}
        </span>
      </button>
    </li>
  );
}

export function OverviewPanel({
  view,
  meta,
  family,
  ccmd,
  onCcmd,
  onRegion,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
  ccmd: string | null;
  onCcmd: (id: string | null) => void;
  onRegion: (id: string | null) => void;
}) {
  const th = view.theaters;
  const ccmds = useMemo(() => meta?.ccmds?.map((c) => c.id) ?? [], [meta]);
  const so = standout(view, family, meta?.official_alerts_from ?? null);
  const fam = familyLabel(family).toLowerCase();
  const name = (id: string) => regionName(meta, id);
  const minZ = view.meta.d1_min_z;
  const pick = (a: AlertRow) => (ccmds.includes(a.region) ? onCcmd(a.region) : onRegion(a.region));

  const where = th ? whereNow(th, family, ccmds) : [];
  const total = where.reduce((s, r) => s + r.seen, 0);
  const span = view.meta.baseline_window_days - (th?.window_days ?? 7) + 1;
  const skip = th?.window_days ?? 7;
  const series = th
    ? ccmd
      ? (th.history[`${ccmd}|${family}`] ?? [])
      : totalSeries(th, family, ccmds)
    : [];
  const typical = typicalOf(series, span, skip);
  const totalTypical = th ? typicalOf(totalSeries(th, family, ccmds), span, skip) : null;
  const [showHeld, setShowHeld] = useState(true);

  return (
    <div className="overview">
      <section>
        <h2>What stands out</h2>
        <p className="muted small">
          {so.source === "alerts"
            ? `Official alerts for ${view.meta.date}.`
            : view.meta.tier === "provisional"
              ? "From today's live data so far; the official run follows after the day's history release."
              : "What this day's numbers flag, by the same rules as alerts (worked out for this page; not stored or posted)."}
        </p>
        {so.shown.length > 0 ? (
          <ul className="standout">
            {so.shown.map((a) => (
              <Standout key={a.alert_id} a={a} meta={meta} onPick={pick} />
            ))}
          </ul>
        ) : (
          <p className="calm">Nothing unusual for {fam} on this day.</p>
        )}
        {so.held.length > 0 && (
          <>
            <button
              className="link"
              onClick={() => setShowHeld(!showHeld)}
              aria-expanded={showHeld}
            >
              {so.held.length} more held back: receiver coverage was far from usual, so these may
              reflect receivers rather than aircraft
            </button>
            {showHeld && (
              <ul className="standout held">
                {so.held.map((a) => (
                  <Standout key={a.alert_id} a={a} meta={meta} onPick={pick} />
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      {th && (
        <section>
          <h2>Where they are</h2>
          <p className="hero">
            <b>{total}</b> {fam} seen in the last {th.max_age_days} days
            {totalTypical !== null && (
              <span className="muted"> · typical {Math.round(totalTypical)}</span>
            )}
          </p>
          <table className="tbl where">
            <thead>
              <tr>
                <th>Last seen in</th>
                <th className="num">Aircraft</th>
                <th className="num">Typical</th>
                <th className="num">Change</th>
              </tr>
            </thead>
            <tbody>
              {where.map((r) => {
                const big = r.z !== null && Math.abs(r.z) >= minZ;
                const types = byLabel(r.byType)
                  .map(([t, k]) => `${k} ${t}`)
                  .join(", ");
                return (
                  <tr
                    key={r.ccmd}
                    className={r.ccmd === ccmd ? "sel" : ""}
                    onClick={() => onCcmd(r.ccmd === ccmd ? null : r.ccmd)}
                    title={types || "none"}
                  >
                    <td>{name(r.ccmd)}</td>
                    <td className="num">
                      <b>{r.seen}</b>
                    </td>
                    <td className="num muted">{r.typical ?? "–"}</td>
                    <td className={`num ${big ? "hi" : "muted"}`}>
                      {r.typical === null ? "–" : signed(Math.round(r.seen - r.typical))}
                      {big && <span className="flag" aria-label="far from typical" />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted small">
            Map: each aircraft at its last known position (where its latest flight started or
            ended). Solid: seen in the last 24 hours. Aircraft not broadcasting aren't counted, so
            these are floors.
          </p>
        </section>
      )}

      {th && (
        <section>
          <h2>
            Over time{" "}
            <span className="muted small">
              {ccmd ? `${fam} last seen in ${name(ccmd)}` : `all ${fam} seen`}, daily
            </span>
          </h2>
          <TrendChart
            values={series}
            last={view.meta.date}
            typical={typical}
            label={`${familyLabel(family)} ${ccmd ? `in ${name(ccmd)}` : "seen"}`}
          />
          <div className="minis">
            {ccmds.map((c) => {
              const s = th.history[`${c}|${family}`] ?? [];
              return (
                <button
                  key={c}
                  className={`mini ${c === ccmd ? "sel" : ""}`}
                  onClick={() => onCcmd(c === ccmd ? null : c)}
                  aria-pressed={c === ccmd}
                >
                  <span className="mini-head">
                    {name(c)} <b>{s[s.length - 1] ?? "–"}</b>
                  </span>
                  <TrendChart
                    values={s}
                    last={view.meta.date}
                    typical={typicalOf(s, span, skip)}
                    height={56}
                    label={`${familyLabel(family)} in ${name(c)}`}
                  />
                </button>
              );
            })}
          </div>
          {ccmd && (
            <button className="link" onClick={() => onCcmd(null)}>
              Show all CCMDs
            </button>
          )}
        </section>
      )}

      <details className="about">
        <summary>About these numbers</summary>
        <p>
          Public ADS-B only: aircraft that don't broadcast, or fly where no receiver hears them,
          aren't seen. Counts go up and down with receiver coverage as well as aircraft, so each
          number is compared with its own recent normal and checked against coverage. CCMD
          boundaries are approximate. Unusual counts say nothing about purpose. Times are UTC.
        </p>
      </details>
    </div>
  );
}
