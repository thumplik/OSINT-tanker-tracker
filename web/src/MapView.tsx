import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { PolygonLayer } from "@deck.gl/layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useMemo, useRef } from "react";
import { bucket, type Cell, RAMP } from "./heat";
import type { Region } from "./types";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
maplibregl.setWorkerUrl(workerUrl); // MapLibre 6 ships its worker as a separate module

interface Props {
  cells: Cell[];
  unit: string;
  regions: Region[];
  selectedRegion: string | null;
  onRegion: (id: string | null) => void;
}

interface Box {
  region: Region;
  polygon: [number, number][];
}

function boxes(regions: Region[]): Box[] {
  return regions.flatMap((region) =>
    region.boxes.map(([la0, la1, lo0, lo1]) => ({
      region,
      polygon: [
        [lo0, la0],
        [lo1, la0],
        [lo1, la1],
        [lo0, la1],
        [lo0, la0],
      ] as [number, number][],
    })),
  );
}

export function regionBounds(region: Region): [[number, number], [number, number]] | null {
  if (!region.boxes.length) return null;
  const la0 = Math.min(...region.boxes.map((b) => b[0]));
  const la1 = Math.max(...region.boxes.map((b) => b[1]));
  const lo0 = Math.min(...region.boxes.map((b) => b[2]));
  const lo1 = Math.max(...region.boxes.map((b) => b[3]));
  return [
    [lo0, la0],
    [lo1, la1],
  ];
}

export function MapView({ cells, unit, regions, selectedRegion, onRegion }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const regionBoxes = useMemo(() => boxes(regions), [regions]);
  const max = useMemo(() => cells.reduce((m, c) => Math.max(m, c.n), 1), [cells]);

  useEffect(() => {
    if (!container.current) return;
    const m = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: [10, 35],
      zoom: 1.3,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    m.touchZoomRotate.disableRotation();
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const o = new MapboxOverlay({ interleaved: false, layers: [] });
    m.addControl(o);
    map.current = m;
    overlay.current = o;
    return () => {
      m.remove();
      map.current = null;
      overlay.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const region = regions.find((r) => r.id === selectedRegion);
    if (!m || !region) return;
    const b = regionBounds(region);
    if (b) m.fitBounds(b, { padding: 24, duration: 600 });
  }, [selectedRegion, regions]);

  useEffect(() => {
    overlay.current?.setProps({
      layers: [
        new PolygonLayer<Box>({
          id: "regions",
          data: regionBoxes,
          getPolygon: (d) => d.polygon,
          stroked: true,
          filled: true,
          getFillColor: (d) =>
            d.region.id === selectedRegion ? [42, 120, 214, 18] : [0, 0, 0, 0],
          getLineColor: (d) =>
            d.region.id === selectedRegion ? [42, 120, 214, 220] : [82, 81, 78, 90],
          getLineWidth: (d) => (d.region.id === selectedRegion ? 2 : 1),
          lineWidthUnits: "pixels",
          pickable: true,
          onClick: (info) => {
            const id = (info.object as Box | undefined)?.region.id ?? null;
            onRegion(id === selectedRegion ? null : id);
          },
          updateTriggers: {
            getFillColor: selectedRegion,
            getLineColor: selectedRegion,
            getLineWidth: selectedRegion,
          },
        }),
        new H3HexagonLayer<Cell>({
          id: "heat",
          data: cells,
          getHexagon: (d) => d.hex,
          getFillColor: (d) => [...RAMP[bucket(d.n, max)], 210] as [number, number, number, number],
          extruded: false,
          stroked: true, // an outline in the fill color keeps small cells visible at world zoom
          getLineColor: (d) => [...RAMP[bucket(d.n, max)], 230] as [number, number, number, number],
          lineWidthUnits: "pixels",
          getLineWidth: 1,
          lineWidthMinPixels: 1,
          pickable: true,
          updateTriggers: { getFillColor: max, getLineColor: max },
        }),
      ],
      getTooltip: ({ object, layer }) => {
        if (!object || layer?.id !== "heat") {
          const box = object as Box | undefined;
          return box?.region ? { text: box.region.name } : null;
        }
        const c = object as Cell;
        return { text: `${c.n} ${unit}` };
      },
    });
  }, [cells, max, unit, regionBoxes, selectedRegion, onRegion]);

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />
      <div className="legend" aria-label={`Color scale: ${unit}`}>
        <span>{unit}</span>
        <div className="ramp">
          {RAMP.map((c, i) => (
            <i key={i} style={{ background: `rgb(${c.join(",")})` }} />
          ))}
        </div>
        <span className="legend-ends">
          <b>1</b>
          <b>{max}</b>
        </span>
      </div>
    </div>
  );
}
