// The small map (docs/09_presentation.md, "The small map"). No basemap tiles
// (D009): context comes from nearby post offices as faint dots. The searched
// point has its precision circle and the service radius ring; results are
// numbered like the cards; unverified facilities are hollow. Trip view and
// simulation animate post office dots with RouteSwarm.

import { Deck, MapView, WebMercatorViewport } from '@deck.gl/core';
import type { Layer, PickingInfo } from '@deck.gl/core';
import { LineLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import { MAP, SWARM } from './colors';
import { hash32 } from './geo';
import { RouteSwarm, type Route, type Segment } from './swarm';
import { SwarmLayer } from './swarm-layer';
import type { Answer, Card, Simulate, Trip, TripFacility } from './types';

export type TripMode = 'none' | 'trips' | 'claims' | 'sim';

interface Marker {
  kind: 'result' | 'unverified';
  idx: number;
  label: string;
  name: string;
  lon: number;
  lat: number;
}

interface ViewState {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch: number;
  bearing: number;
}

const DT = 1 / 60;

export class MiniMap {
  private deck: Deck<MapView>;
  private el: HTMLElement;
  private answer: Answer | null = null;
  private trip: Trip | null = null;
  private sim: Simulate | null = null;
  private markers: Marker[] = [];
  private focused: number | null = null;
  private mode: TripMode = 'none';
  private simPolicy = '';
  private swarm: RouteSwarm | null = null;
  private time = 0;
  private raf = 0;
  private viewState: ViewState = { longitude: 78.9, latitude: 22.5, zoom: 3.6, pitch: 0, bearing: 0 };
  onPick: (kind: 'result' | 'unverified', idx: number) => void = () => {};
  onView: (vs: ViewState, width: number) => void = () => {};

  constructor(container: HTMLElement) {
    this.el = container;
    this.deck = new Deck<MapView>({
      parent: container as HTMLDivElement,
      views: new MapView({ repeat: false }),
      initialViewState: this.viewState,
      controller: { doubleClickZoom: true, scrollZoom: true, dragRotate: false, keyboard: false },
      useDevicePixels: true,
      onViewStateChange: ({ viewState }) => {
        this.viewState = viewState as ViewState;
        this.onView(this.viewState, this.el.clientWidth);
        return viewState;
      },
      getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
      getTooltip: (info: PickingInfo) => {
        const m = info.object as Marker | undefined;
        if (!m || !m.name) return null;
        return { text: m.kind === 'result' ? `${m.label}. ${m.name}` : `${m.name} (not verified)`, className: 'map-tip' };
      },
      onClick: (info: PickingInfo) => {
        const m = info.object as Marker | undefined;
        if (m && m.name) this.onPick(m.kind, m.idx);
      },
      layers: [],
    });
    new ResizeObserver(() => this.onView(this.viewState, this.el.clientWidth)).observe(container);
  }

  /** Show an answer. trip may be null (no context dots then). */
  setAnswer(a: Answer, trip: Trip | null, sim: Simulate | null): void {
    this.answer = a;
    this.trip = trip && trip.status === 'ok' ? trip : null;
    this.sim = sim;
    this.focused = null;
    this.mode = 'none';
    this.swarm = null;
    const cards: Card[] = a.status === 'no_reliable_option' ? (a.beyond_radius ? [a.beyond_radius] : []) : a.results;
    this.markers = [];
    cards.forEach((c, i) => {
      if (c.lat != null && c.lon != null) this.markers.push({ kind: 'result', idx: i, label: String(i + 1), name: c.name, lon: c.lon, lat: c.lat });
    });
    (a.unverified_nearby ?? []).forEach((c, i) => {
      if (c.lat != null && c.lon != null) this.markers.push({ kind: 'unverified', idx: i, label: '', name: c.name, lon: c.lon, lat: c.lat });
    });
    this.fit();
    this.render();
  }

  clear(): void {
    this.answer = null;
    this.trip = null;
    this.sim = null;
    this.markers = [];
    this.swarm = null;
    this.mode = 'none';
    cancelAnimationFrame(this.raf);
    this.render();
  }

  focus(idx: number | null): void {
    this.focused = idx;
    this.render();
  }

  private fit(): void {
    const a = this.answer;
    if (!a || a.location?.lat == null || a.location?.lon == null) return;
    const pts: [number, number][] = [[a.location.lon, a.location.lat]];
    for (const m of this.markers) pts.push([m.lon, m.lat]);
    // Fit the markers plus about 12 km around the point. A coarse location's precision
    // circle may then fill the view, which is the honest picture; fitting the whole
    // circle would stack the markers on top of each other.
    const padKm = Math.min(12, Math.max(6, a.location.precision_km ?? 0));
    const dLat = padKm / 111.2, dLon = padKm / (111.2 * Math.cos((a.location.lat * Math.PI) / 180));
    pts.push([a.location.lon - dLon, a.location.lat - dLat], [a.location.lon + dLon, a.location.lat + dLat]);
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const [x, y] of pts) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
    const w = Math.max(200, this.el.clientWidth), h = Math.max(200, this.el.clientHeight);
    const vp = new WebMercatorViewport({ width: w, height: h });
    const pad = Math.min(56, Math.floor(Math.min(w, h) / 6));
    const { longitude, latitude, zoom } = vp.fitBounds([[minX, minY], [maxX, maxY]], { padding: pad });
    this.viewState = { longitude, latitude, zoom: Math.min(zoom, 12), pitch: 0, bearing: 0 };
    this.deck.setProps({ initialViewState: { ...this.viewState } });
    this.onView(this.viewState, this.el.clientWidth);
  }

  // --------------------------------------------------------------- trips

  /** Start a playback. Returns false when the mode cannot run with the data we have. */
  setMode(mode: TripMode, policy = ''): boolean {
    cancelAnimationFrame(this.raf);
    this.mode = mode;
    this.simPolicy = policy;
    this.swarm = null;
    if (mode === 'none') { this.render(); return true; }
    const routes = mode === 'sim' ? this.simRoutes(policy) : this.tripRoutes(mode);
    if (!routes) { this.mode = 'none'; this.render(); return false; }
    this.swarm = new RouteSwarm(routes, { t0: 0.3 });
    this.time = 0;
    const tick = () => {
      this.time += DT;
      this.render();
      if (this.swarm && this.time < this.swarm.endTime + 0.5) this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
    return true;
  }

  replay(): void {
    this.setMode(this.mode, this.simPolicy);
  }

  /** Jump to the end of the playback (for screenshots and reduced motion). */
  settle(): void {
    if (!this.swarm) return;
    cancelAnimationFrame(this.raf);
    this.time = this.swarm.endTime + 1;
    this.render();
  }

  /** Freeze the playback at animation time t, in seconds (deterministic frames for recording and screenshots). */
  seek(t: number): void {
    if (!this.swarm) return;
    cancelAnimationFrame(this.raf);
    this.time = t;
    this.render();
  }

  private tripRoutes(mode: 'trips' | 'claims'): Route[] | null {
    const t = this.trip;
    if (!t || !t.points.length) return null;
    const F = t.facilities;
    const routes: Route[] = [];
    t.points.forEach((p, i) => {
      const r = t.nearest_recommended[i] ?? -1;
      if (mode === 'trips') {
        if (r >= 0) routes.push({ path: [p, [F[r].lon, F[r].lat]], end: 'reached' });
        return;
      }
      const c = t.nearest_claim[i] ?? -1;
      if (c < 0) return;
      const cf = F[c];
      if (cf.kind === 'recommended') routes.push({ path: [p, [cf.lon, cf.lat]], end: 'reached' });
      else if (r >= 0) routes.push({ path: [p, [cf.lon, cf.lat], [F[r].lon, F[r].lat]], end: 'reached' });
      else routes.push({ path: [p, [cf.lon, cf.lat]], end: 'stranded' });
    });
    return routes.length ? routes : null;
  }

  /**
   * Simulation (D038): at each facility a dot stops when the service is found,
   * else moves to the next one in the policy's order. Whether it is found is
   * decided once per dot and stop from a hash, with chance p_offers, so every
   * playback is identical. The panel's numbers come from /api/simulate, not
   * from this animation.
   */
  private simRoutes(policy: string): Route[] | null {
    const s = this.sim;
    const orders = s?.orders?.[policy];
    if (!s || !s.facilities || !s.origins || !orders) return null;
    const F = s.facilities;
    const routes: Route[] = [];
    s.origins.forEach((o, i) => {
      const order = orders[i] ?? [];
      if (!order.length) return;
      const path: [number, number][] = [o];
      let found = false;
      for (let j = 0; j < order.length; j++) {
        const f = F[order[j]];
        path.push([f.lon, f.lat]);
        const u = hash32((i * 7919 + j * 104729) >>> 0) / 4294967296;
        if (u < f.p_offers) { found = true; break; }
      }
      routes.push({ path, end: found ? 'reached' : 'stranded' });
    });
    return routes.length ? routes : null;
  }

  // -------------------------------------------------------------- layers

  private render(): void {
    const a = this.answer;
    const layers: Layer[] = [];
    if (!a || a.location?.lat == null || a.location?.lon == null) {
      this.deck.setProps({ layers });
      return;
    }
    const here: [number, number] = [a.location.lon, a.location.lat];
    if (this.trip) {
      layers.push(new ScatterplotLayer<[number, number]>({
        id: 'po', data: this.trip.points, getPosition: (d) => d, getRadius: 1.6, radiusUnits: 'pixels',
        getFillColor: MAP.po, pickable: false,
      }));
    }
    layers.push(new ScatterplotLayer({
      id: 'radius', data: [0], getPosition: () => here, getRadius: a.radius_km * 1000, radiusUnits: 'meters',
      filled: false, stroked: true, getLineColor: MAP.radiusLine, lineWidthUnits: 'pixels', getLineWidth: 1,
    }));
    const radLabelLat = a.location.lat + a.radius_km / 111.2;
    layers.push(new TextLayer({
      id: 'radius-label', data: [0], getPosition: () => [here[0], radLabelLat], getText: () => `${a.radius_km} km`,
      getColor: MAP.label, getSize: 12, getPixelOffset: [0, -9], fontFamily: 'system-ui, sans-serif',
    }));
    if (a.location.precision_km) {
      layers.push(new ScatterplotLayer({
        id: 'precision', data: [0], getPosition: () => here, getRadius: a.location.precision_km * 1000, radiusUnits: 'meters',
        filled: true, stroked: true, getFillColor: MAP.precisionFill, getLineColor: MAP.precisionLine,
        lineWidthUnits: 'pixels', getLineWidth: 1.5,
      }));
    }
    layers.push(new ScatterplotLayer({
      id: 'here', data: [0], getPosition: () => here, getRadius: 4, radiusUnits: 'pixels',
      getFillColor: MAP.precisionLine, stroked: true, getLineColor: [16, 20, 28, 255], lineWidthUnits: 'pixels', getLineWidth: 2,
    }));

    if (this.swarm) {
      layers.push(new LineLayer<Segment>({
        id: 'trip-lines', data: this.swarm.segments,
        getSourcePosition: (d) => d.from, getTargetPosition: (d) => d.to,
        getColor: (d) => (d.toDeadEnd ? MAP.tripDeadEnd : MAP.tripLine), getWidth: 1, widthUnits: 'pixels',
        updateTriggers: { getColor: this.swarm },
      }));
      this.swarm.legs.forEach((leg, k) => {
        layers.push(new SwarmLayer({
          id: `swarm-${k}`, data: leg, time: this.time, hideBefore: leg.hideBefore,
          getRadius: 2.2, radiusUnits: 'pixels', radiusMinPixels: 1.5, pickable: false,
          updateTriggers: { getFillColor: this.swarm },
        }));
      });
    }

    // Trip targets with evidence that are not among the numbered results (the server sends them as
    // kind "recommended" with no rank): a small solid dot, so dots never stop at an unmarked point.
    if (this.trip && this.mode !== 'none') {
      const shown = new Set(this.markers.map((m) => `${m.lon.toFixed(5)},${m.lat.toFixed(5)}`));
      const others = this.trip.facilities.filter((f) => f.kind === 'recommended' && f.rank == null
        && !shown.has(`${f.lon.toFixed(5)},${f.lat.toFixed(5)}`));
      layers.push(new ScatterplotLayer<TripFacility>({
        id: 'other-supported', data: others, getPosition: (f) => [f.lon, f.lat], getRadius: 5, radiusUnits: 'pixels',
        getFillColor: MAP.result, stroked: true, getLineColor: [16, 20, 28, 255], lineWidthUnits: 'pixels', getLineWidth: 1.5,
      }));
      if (this.mode === 'claims') {
        // Claim-only first stops: hollow, like the "not verified" markers, so a dot never stops at an unmarked point.
        const claimOnly = this.trip.facilities.filter((f) => f.kind === 'claim_only'
          && !shown.has(`${f.lon.toFixed(5)},${f.lat.toFixed(5)}`));
        layers.push(new ScatterplotLayer<TripFacility>({
          id: 'claim-only', data: claimOnly, getPosition: (f) => [f.lon, f.lat], getRadius: 5, radiusUnits: 'pixels',
          filled: true, getFillColor: [16, 20, 28, 200], stroked: true, getLineColor: MAP.unverified,
          lineWidthUnits: 'pixels', getLineWidth: 2,
        }));
      }
    }
    const unv = this.markers.filter((m) => m.kind === 'unverified');
    const res = this.markers.filter((m) => m.kind === 'result');
    layers.push(new ScatterplotLayer<Marker>({
      id: 'unverified', data: unv, getPosition: (m) => [m.lon, m.lat], getRadius: 7, radiusUnits: 'pixels',
      filled: true, getFillColor: [16, 20, 28, 200], stroked: true, getLineColor: MAP.unverified, lineWidthUnits: 'pixels', getLineWidth: 2.5,
      pickable: true,
    }));
    layers.push(new ScatterplotLayer<Marker>({
      id: 'results', data: res, getPosition: (m) => [m.lon, m.lat],
      getRadius: (m) => (m.idx === this.focused ? 15 : 12), radiusUnits: 'pixels',
      getFillColor: (m) => (m.idx === this.focused ? MAP.resultFocus : MAP.result),
      stroked: true, getLineColor: [16, 20, 28, 255], lineWidthUnits: 'pixels', getLineWidth: 2,
      pickable: true, updateTriggers: { getRadius: this.focused, getFillColor: this.focused },
    }));
    layers.push(new TextLayer<Marker>({
      id: 'result-num', data: res, getPosition: (m) => [m.lon, m.lat], getText: (m) => m.label,
      getColor: MAP.resultText, getSize: 14, fontWeight: 700, fontFamily: 'system-ui, sans-serif',
      getTextAnchor: 'middle', getAlignmentBaseline: 'center',
    }));
    this.deck.setProps({ layers });
  }
}

export const SWARM_CSS = {
  reached: `rgb(${SWARM.reached.slice(0, 3).join(',')})`,
  stranded: `rgb(${SWARM.stranded.slice(0, 3).join(',')})`,
};
