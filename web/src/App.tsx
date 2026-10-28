import { useCallback, useEffect, useMemo, useState } from "react";
import { loadDay, loadIndex, loadMeta, loadR6, NotFound } from "./data";
import { cellsFor } from "./heat";
import { MapView } from "./MapView";
import { AlertsPanel, RegionsPanel, SortiesPanel } from "./panels";
import { parseState, serializeState, stepDate, type Tab, type ViewState } from "./state";
import type { DayR6, DayView, SiteIndex, SiteMeta } from "./types";
import { familyLabel } from "./wording";

type Load<T> = { status: "loading" } | { status: "ok"; data: T } | { status: "missing" | "error"; message: string };

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
    if (!date || state.res !== 6) return;
    let live = true;
    setR6({ status: "loading" });
    loadR6(date)
      .then((data) => live && setR6({ status: "ok", data }))
      .catch((e: Error) => live && setR6({ status: "error", message: e.message }));
    return () => {
      live = false;
    };
  }, [date, state.res]);

  const view = day.status === "ok" ? day.data : null;
  const heat = state.res === 6 ? (r6?.status === "ok" ? r6.data : null) : view?.heat ?? null;
  const cells = useMemo(
    () => (heat ? cellsFor(heat, state.family, state.res === 4 ? state.hour : null) : []),
    [heat, state.family, state.hour, state.res],
  );
  const unit =
    state.res === 6 ? "aircraft (day)" : state.hour === null ? "aircraft-hours" : "aircraft";
  const families = view?.heat.families ?? ["all"];
  const onRegion = useCallback((id: string | null) => update({ region: id }), [update]);

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
          <select
            aria-label="Aircraft family"
            value={state.family}
            onChange={(e) => update({ family: e.target.value })}
          >
            {families.map((f) => (
              <option key={f} value={f}>
                {familyLabel(f)}
              </option>
            ))}
          </select>
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
            cells={cells}
            unit={unit}
            regions={meta?.regions ?? []}
            selectedRegion={state.region}
            onRegion={onRegion}
          />
          <div className="map-controls">
            <div className="seg" role="group" aria-label="Resolution">
              <button aria-pressed={state.res === 4} onClick={() => update({ res: 4 })}>
                Regional
              </button>
              <button aria-pressed={state.res === 6} onClick={() => update({ res: 6, hour: null })}>
                Local
              </button>
            </div>
            {state.res === 4 ? (
              <label className="hour">
                <span>{state.hour === null ? "All day" : `${String(state.hour).padStart(2, "0")}:00Z`}</span>
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
          </div>
        </section>

        <aside className="panel">
          <nav className="tabs" role="tablist">
            {(["alerts", "regions", "sorties"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={state.tab === t}
                onClick={() => update({ tab: t })}
              >
                {t === "alerts" ? "Alerts" : t === "regions" ? "Regions" : "Sorties"}
                {t === "alerts" && indexDay && indexDay.alerts > 0 && (
                  <span className="count">{indexDay.alerts}</span>
                )}
              </button>
            ))}
          </nav>
          <div className="panel-body">
            {day.status === "loading" && <p className="muted">Loading {date}…</p>}
            {(day.status === "missing" || day.status === "error") && (
              <p className="muted">{day.message}</p>
            )}
            {view && state.tab === "alerts" && (
              <AlertsPanel view={view} meta={meta} />
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
            {view && state.tab === "sorties" && (
              <SortiesPanel view={view} meta={meta} family={state.family} region={state.region} onRegion={onRegion} />
            )}
          </div>
        </aside>
      </main>

      <footer className="foot">
        <span>
          Data: {meta?.attribution.join(" · ") ?? "adsb.lol (ODbL)"}. Public ADS-B only; not affiliated
          with any government. Counts are what public receivers saw, not complete activity. Times UTC.
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
