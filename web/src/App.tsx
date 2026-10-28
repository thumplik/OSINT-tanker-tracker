import { useCallback, useEffect, useMemo, useState } from "react";
import { loadDay, loadIndex, loadMeta, loadR6, NotFound } from "./data";
import { cellsFor } from "./heat";
import { MapView } from "./MapView";
import { OverviewPanel } from "./OverviewPanel";
import { AlertsPanel, forFamily, RegionsPanel, SortiesPanel, TheatersPanel } from "./panels";
import { parseState, serializeState, stepDate, TABS, type ViewState } from "./state";
import { transitArcs } from "./theater";
import type { DayR6, DayView, SiteIndex, SiteMeta } from "./types";
import { familyLabel } from "./wording";

const TAB_LABEL: Record<string, string> = {
  overview: "Overview",
  alerts: "Alerts",
  theaters: "Theaters",
  sorties: "Flights",
  regions: "Regions",
};

// Tankers first: the site's main subject. Families are never mixed in one count.
const FAMILY_ORDER = ["tanker", "strategic_airlift", "tactical_airlift"];

type Load<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "missing" | "error"; message: string };

function useUrlState(): [ViewState, (patch: Partial<ViewState>) => void] {
  const [state, setState] = useState(() => parseState(window.location.search));
  useEffect(() => {
    const onPop = () => setState(parseState(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const update = useCallback((patch: Partial<ViewState>) => {
    setState((prev) => {
      const next = { ...prev, ...patch };
      const url = `${window.location.pathname}${serializeState(next)}`;
      const push = patch.date !== undefined && patch.date !== prev.date;
      window.history[push ? "pushState" : "replaceState"](null, "", url);
      return next;
    });
  }, []);
  return [state, update];
}

export function App() {
  const [state, update] = useUrlState();
  const [meta, setMeta] = useState<SiteMeta | null>(null);
  const [index, setIndex] = useState<SiteIndex | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [day, setDay] = useState<Load<DayView>>({ status: "loading" });
  const [r6, setR6] = useState<Load<DayR6> | null>(null);
  const [loadMs, setLoadMs] = useState<number | null>(null);

  useEffect(() => {
    Promise.all([loadMeta(), loadIndex()])
      .then(([m, i]) => {
        setMeta(m);
        setIndex(i);
      })
      .catch((e: Error) => setFatal(e.message));
  }, []);

  const days = useMemo(() => index?.days.map((d) => d.date) ?? [], [index]);
  const date = state.date ?? index?.latest ?? null;
  const indexDay = index?.days.find((d) => d.date === date);

  useEffect(() => {
    if (!date) return;
    let live = true;
    const t0 = performance.now();
    setDay({ status: "loading" });
    loadDay(date)
      .then((data) => {
        if (!live) return;
        setDay({ status: "ok", data });
        setLoadMs(Math.round(performance.now() - t0));
      })
      .catch((e: Error) => {
        if (live)
          setDay({
            status: e instanceof NotFound ? "missing" : "error",
            message: e instanceof NotFound ? `No data exported for ${date}.` : e.message,
          });
      });
    return () => {
      live = false;
    };
  }, [date]);

  useEffect(() => {
    if (!date || state.res !== 6 || state.layer !== "activity") return;
    let live = true;
    setR6({ status: "loading" });
    loadR6(date)
      .then((data) => live && setR6({ status: "ok", data }))
      .catch((e: Error) => live && setR6({ status: "error", message: e.message }));
    return () => {
      live = false;
    };
  }, [date, state.res, state.layer]);

  const view = day.status === "ok" ? day.data : null;
  const heat = state.res === 6 ? (r6?.status === "ok" ? r6.data : null) : (view?.heat ?? null);
  const cells = useMemo(
    () => (heat ? cellsFor(heat, state.family, state.res === 4 ? state.hour : null) : []),
    [heat, state.family, state.hour, state.res],
  );
  const unit =
    state.res === 6 ? "aircraft (day)" : state.hour === null ? "aircraft-hours" : "aircraft";
  const families = FAMILY_ORDER.filter((f) => view?.heat.families.includes(f));
  const onRegion = useCallback((id: string | null) => update({ region: id }), [update]);
  const onCcmd = useCallback((id: string | null) => update({ ccmd: id }), [update]);
  const theaterTab = state.tab === "theaters" && !!meta?.ccmds;
  const arcs = useMemo(
    () =>
      view?.theaters && meta?.ccmds
        ? transitArcs(view.theaters.transits, meta.ccmds, view.theaters.home, state.family)
        : [],
    [view, meta, state.family],
  );
  const positions = useMemo(
    () => (view?.theaters?.positions ?? []).filter((p) => p.family === state.family),
    [view, state.family],
  );
  const at = view
    ? view.meta.tier === "official"
      ? new Date(Date.parse(`${view.meta.date}T00:00:00Z`) + 86_400_000).toISOString()
      : view.meta.generated_at
    : "";
  const liveAlerts = view
    ? forFamily(view.alerts, state.family).filter(
        (a) => a.tier === "official" && a.status !== "suppressed",
      ).length
    : 0;

  if (fatal) {
    return (
      <div className="fatal">
        <h1>Tanker Watch</h1>
        <p>The data files could not be loaded ({fatal}).</p>
      </div>
    );
  }

  const tier = view?.meta.tier ?? indexDay?.tier;
  return (
    <div className="app">
      <header className="bar">
        <div className="brand">
          <h1>Tanker Watch</h1>
          <span className="sub">US military tanker & airlift activity from public ADS-B</span>
        </div>
        <div className="controls">
          <div className="datepick" role="group" aria-label="Day (UTC)">
            <button
              aria-label="Previous day"
              disabled={!date || !stepDate(days, date, -1)}
              onClick={() => date && update({ date: stepDate(days, date, -1) })}
            >
              ‹
            </button>
            <input
              type="date"
              aria-label="Day (UTC)"
              value={date ?? ""}
              min={days[0]}
              max={days[days.length - 1]}
              onChange={(e) => e.target.value && update({ date: e.target.value })}
            />
            <button
              aria-label="Next day"
              disabled={!date || !stepDate(days, date, 1)}
              onClick={() => date && update({ date: stepDate(days, date, 1) })}
            >
              ›
            </button>
          </div>
          <div className="seg family" role="group" aria-label="Aircraft family">
            {families.map((f) => (
              <button
                key={f}
                aria-pressed={state.family === f}
                onClick={() => update({ family: f })}
              >
                {familyLabel(f)}
              </button>
            ))}
          </div>
          {tier && (
            <span
              className={`badge ${tier}`}
              title={
                tier === "official"
                  ? "Built from the day's complete public history release"
                  : "Live data only; refreshed hourly; coverage not yet checked"
              }
            >
              {tier === "official" ? "Official day" : "Live, provisional"}
            </span>
          )}
        </div>
      </header>

      <main className="body">
        <section className="map-col">
          <MapView
            layer={state.layer}
            cells={cells}
            unit={unit}
            positions={positions}
            at={at}
            showRegions={state.tab === "regions" || state.tab === "sorties"}
            regions={meta?.regions ?? []}
            selectedRegion={state.region}
            onRegion={onRegion}
            ccmds={theaterTab ? meta?.ccmds : undefined}
            arcs={arcs}
            selectedCcmd={state.ccmd}
            onCcmd={onCcmd}
          />
          <div className="map-controls">
            <div className="seg" role="group" aria-label="Map shows">
              <button
                aria-pressed={state.layer === "positions"}
                onClick={() => update({ layer: "positions" })}
              >
                Positions
              </button>
              <button
                aria-pressed={state.layer === "activity"}
                onClick={() => update({ layer: "activity" })}
              >
                Activity
              </button>
            </div>
            {state.layer === "positions" ? (
              <span className="hint">
                Last known position of each aircraft at{" "}
                {view?.meta.tier === "official" ? "the end of the day" : `${at.slice(11, 16)}Z`}
              </span>
            ) : (
              <>
                <div className="seg" role="group" aria-label="Resolution">
                  <button aria-pressed={state.res === 4} onClick={() => update({ res: 4 })}>
                    Regional
                  </button>
                  <button
                    aria-pressed={state.res === 6}
                    onClick={() => update({ res: 6, hour: null })}
                  >
                    Local
                  </button>
                </div>
                {state.res === 4 ? (
                  <label className="hour">
                    <span>
                      {state.hour === null
                        ? "All day"
                        : `${String(state.hour).padStart(2, "0")}:00Z`}
                    </span>
                    <input
                      type="range"
                      min={-1}
                      max={23}
                      value={state.hour ?? -1}
                      aria-label="Hour (UTC); leftmost is the whole day"
                      onChange={(e) => {
                        const h = Number(e.target.value);
                        update({ hour: h < 0 ? null : h });
                      }}
                    />
                  </label>
                ) : (
                  <span className="hint">
                    {r6?.status === "loading" ? "Loading local detail…" : "Whole day, ~3 km cells"}
                  </span>
                )}
              </>
            )}
          </div>
        </section>

        <aside className="panel">
          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={state.tab === t}
                onClick={() => update({ tab: t })}
              >
                {TAB_LABEL[t]}
                {t === "alerts" && liveAlerts > 0 && <span className="count">{liveAlerts}</span>}
              </button>
            ))}
          </nav>
          <div className="panel-body">
            {day.status === "loading" && <p className="muted">Loading {date}…</p>}
            {(day.status === "missing" || day.status === "error") && (
              <p className="muted">{day.message}</p>
            )}
            {view && state.tab === "overview" && (
              <OverviewPanel
                view={view}
                meta={meta}
                family={state.family}
                ccmd={state.ccmd}
                onCcmd={onCcmd}
                onRegion={(id: string | null) => update({ region: id, tab: "regions" })}
              />
            )}
            {view && state.tab === "alerts" && (
              <AlertsPanel view={view} meta={meta} family={state.family} />
            )}
            {view && state.tab === "regions" && (
              <RegionsPanel
                view={view}
                meta={meta}
                family={state.family}
                selected={state.region}
                onRegion={onRegion}
              />
            )}
            {view && state.tab === "theaters" && (
              <TheatersPanel
                view={view}
                meta={meta}
                family={state.family}
                selected={state.ccmd}
                onCcmd={onCcmd}
              />
            )}
            {view && state.tab === "sorties" && (
              <SortiesPanel
                view={view}
                meta={meta}
                family={state.family}
                region={state.region}
                onRegion={onRegion}
              />
            )}
          </div>
        </aside>
      </main>

      <footer className="foot">
        <span>
          Data: {meta?.attribution.join(" · ") ?? "adsb.lol (ODbL)"}. Public ADS-B only; not
          affiliated with any government. Counts are what public receivers saw, not complete
          activity. Times UTC.
        </span>
        {view && (
          <span className="muted">
            Baseline as of {view.meta.baseline_as_of ?? "n/a"} · {view.meta.code_version}
            {loadMs !== null && ` · ${loadMs} ms`}
          </span>
        )}
      </footer>
    </div>
  );
}
