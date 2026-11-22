// MapHost: the one map of the app (app/design/SPEC.md section 5). Real geography with no tile
// service (D009): India's states filled in paper, the post office dots that draw where people
// live, state borders, India's outline, neighbour countries' edges, and zoom-tiered place names.
// One Deck, re-parented between the inline slot and a full-screen overlay.
//
// Public API (A and D call only these; see the method docs):
//   new MapHost(inlineEl)
//   showLanding(examples, onPick) / showCandidates(cands, onPick) / showAnswer(answer, sim) / setSim(sim)
//   highlight(kind, key) / focus(kind, idx)
//   expand({rail}) -> {rail, sheet, mapEl, topbar} / collapse() / isExpanded() / onExpandChange
//   fit(bounds, padPx, ms) / showIndia(ms) / zoomToPlace(ms) / viewState / viewport / onViewChange
//   setOverlayLayers(layers)  D's layers, drawn above the pins and below the labels
//   setExtraLegend(rows) / setBanner(el) / setMapClickHandler(fn) / setCursor(c)
//   onPick(kind, key)  'result' + index into results (on an abstention the beyond card is
//                      pin 1 and comes as ('result', 0)), 'whynot' + letter, 'further' + index
//   legendEl, deck, ready

import { Deck, FlyToInterpolator, MapView, WebMercatorViewport } from '@deck.gl/core';
import type { Layer, PickingInfo } from '@deck.gl/core';
import { IconLayer, LineLayer, PathLayer, ScatterplotLayer, SolidPolygonLayer, TextLayer } from '@deck.gl/layers';
import { BORDER, CLAIM, EVIDENCE, INK, NEUTRAL, ORIGIN, PAPER, rgb, type RGBA } from './colors';
import { loadGeo, type GeoAssets, type Ring } from './map_assets';
import { injectMapCss } from './map_css';
import { ICON_MAPPING, iconAtlas } from './map_icons';
import { Inset } from './map_inset';
import { aroundPoint, computeLabels, LABEL_SIZE, type LabelOut, type LabelStyle } from './map_labels';
import { cardShortName, cardTime, hoursText, shortName, titleCase } from './map_text';
import type {
  Bounds, ExpandedSlots, LegendRow, MapAnswer, MapCandidate, MapCard, MapExample, MapSim, MapSimLabel, PickKind, ViewState,
} from './map_types';

export type { Bounds, ExpandedSlots, LegendRow, MapAnswer, MapCandidate, MapExample, MapSim, PickKind, ViewState } from './map_types';

// ------------------------------------------------------------------ constants

export const MIN_ZOOM = 3.6;
export const MAX_ZOOM = 14;
const CLAMP = { lon0: 66, lon1: 99, lat0: 5, lat1: 38 };
const INDIA_BOUNDS: Bounds = [[68.1, 6.7], [97.4, 35.6]];
const INDIA_CENTER = { longitude: 80.5, latitude: 22.5 };
/** Below this zoom the answer collapses into one labelled marker at the origin. */
const COLLAPSE_Z = 7;
const DIAMOND_Z = 9;
const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
const LADDER: { label: string; zoom: number | 'india' }[] = [
  { label: 'All of India', zoom: 'india' }, { label: 'State', zoom: 6.5 }, { label: 'District', zoom: 9 }, { label: 'Neighbourhood', zoom: 11.5 },
];

const C = {
  paper: rgb(PAPER),
  po: [138, 127, 109, 107] as RGBA,
  border: rgb(BORDER, 0.7),
  outline: rgb(INK, 0.8),
  neighbour: [150, 141, 126, 200] as RGBA,
  ring: rgb(EVIDENCE, 0.5),
  precision: rgb(ORIGIN, 0.06),
  precisionLine: rgb(ORIGIN, 0.25),
  evidence: rgb(EVIDENCE),
  white: [255, 255, 255, 255] as RGBA,
  ink: rgb(INK),
  origin: rgb(ORIGIN),
  leader: rgb(EVIDENCE, 0.85),
  fan: rgb(INK, 0.55),
  townDot: rgb(INK, 0.7),
  landing: rgb(INK, 0.9),
};

const LABEL_COLOR: Record<LabelStyle, RGBA> = {
  search: rgb(INK),
  state: [122, 106, 85, 255],
  district: [95, 84, 70, 255],
  city: rgb(INK, 0.92),
  town: rgb(INK, 0.82),
  country: rgb(NEUTRAL),
  sea: [128, 140, 150, 255],
  note: rgb(INK),
  ring: rgb(EVIDENCE),
  claimnote: rgb(CLAIM),
  whatif: [106, 62, 161, 255],
};
const LABEL_WEIGHT: Record<LabelStyle, string | number> = {
  search: 700, state: 600, district: 600, city: 600, town: 400, country: 600, sea: 400, note: 600, ring: 600, claimnote: 600, whatif: 700,
};

// ------------------------------------------------------------------ helpers

interface Pin {
  kind: PickKind;
  key: number;
  num: string;
  lon: number;
  lat: number;
  /** Displayed position (fanned out when pins overlap on screen). */
  dlon: number;
  dlat: number;
  approx: boolean;
  name: string;
  time: string | null;
}

interface Diamond { letter: string; lon: number; lat: number; name: string }
interface Marker { i: number; lon: number; lat: number; label: string; num: string }

function circlePath(lon: number, lat: number, km: number, n = 128): [number, number][] {
  const R = 6371.0088, d = km / R, p1 = (lat * Math.PI) / 180, l1 = (lon * Math.PI) / 180;
  const out: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const b = (2 * Math.PI * i) / n;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    out.push([(l2 * 180) / Math.PI, (p2 * 180) / Math.PI]);
  }
  return out;
}

function kmBox(lon: number, lat: number, km: number): Bounds {
  const dLat = km / 111.2, dLon = km / (111.2 * Math.cos((lat * Math.PI) / 180));
  return [[lon - dLon, lat - dLat], [lon + dLon, lat + dLat]];
}

function extent(pts: [number, number][]): Bounds {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  return [[x0, y0], [x1, y1]];
}

function metersPerPixel(lat: number, zoom: number): number {
  return (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** zoom);
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function lowerFirst(s: string): string {
  return s.length > 1 && s[1] === s[1].toLowerCase() ? s[0].toLowerCase() + s.slice(1) : s;
}

function swatchSvg(row: LegendRow): string {
  // "<shape> <css colour>" (the simulator's format) or a bare keyword with an optional row.color.
  const [shape0, color0] = row.swatch.trim().split(/\s+/, 2);
  const shape = shape0 === 'dash' ? 'dashed' : shape0;
  const c = row.color ?? color0;
  switch (shape) {
    case 'pin': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="8" fill="${c ?? EVIDENCE}" stroke="#fff" stroke-width="1.5"/><text x="9" y="12.5" font-size="10" font-weight="700" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif">1</text></svg>`;
    case 'hollow': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="7.5" fill="#fff" stroke="${c ?? EVIDENCE}" stroke-width="2"/></svg>`;
    case 'area': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="8" fill="${ORIGIN}" fill-opacity="0.12" stroke="${ORIGIN}" stroke-opacity="0.45" stroke-width="1"/></svg>`;
    case 'diamond': return `<svg viewBox="0 0 18 18"><path d="M9 1.5L16.5 9L9 16.5L1.5 9Z" fill="${c ?? NEUTRAL}" stroke="#fff" stroke-width="1.3"/></svg>`;
    case 'ring': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="7" fill="none" stroke="${c ?? EVIDENCE}" stroke-width="1.6"/></svg>`;
    case 'line': return `<svg viewBox="0 0 18 18"><path d="M1 9H17" stroke="${c ?? BORDER}" stroke-width="1.6"/></svg>`;
    case 'dashed': return `<svg viewBox="0 0 18 18"><path d="M1 9H17" stroke="${c ?? CLAIM}" stroke-width="2" stroke-dasharray="3 2.5"/></svg>`;
    case 'plus': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="8" fill="${c ?? '#6a3ea1'}"/><path d="M9 4.5V13.5M4.5 9H13.5" stroke="#fff" stroke-width="2.2"/></svg>`;
    case 'dots': return `<svg viewBox="0 0 18 18" fill="${c ?? '#8a7f6d'}"><circle cx="4" cy="5" r="1.3"/><circle cx="10" cy="4" r="1.3"/><circle cx="14" cy="9" r="1.3"/><circle cx="6" cy="11" r="1.3"/><circle cx="11" cy="14" r="1.3"/><circle cx="3" cy="15" r="1.3"/></svg>`;
    case 'hollowdot': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="5" fill="${c ?? CLAIM}" stroke="#fff" stroke-width="1"/><circle cx="9" cy="9" r="2.3" fill="#fffdf8"/></svg>`;
    case 'smalldot': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="3.5" fill="${c ?? EVIDENCE}" stroke="#fff" stroke-width="1"/></svg>`;
    case 'villring': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="5.5" fill="none" stroke="#5a544c" stroke-width="1.6"/></svg>`;
    case 'dot': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="4.5" fill="${c ?? INK}" stroke="#fff" stroke-width="1.2"/></svg>`;
    case 'origin': return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="7.5" fill="#fff" stroke="${ORIGIN}" stroke-width="2"/><circle cx="9" cy="9" r="3.5" fill="${ORIGIN}"/></svg>`;
    default: return `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="5" fill="${c ?? shape0}"/></svg>`;
  }
}

// ------------------------------------------------------------------ MapHost

export class MapHost {
  readonly deck: Deck<MapView>;
  /** The legend list. Placed right after the inline map; A may move it anywhere. */
  readonly legendEl: HTMLUListElement;
  /** Resolves once the geography assets are loaded and drawn. */
  readonly ready: Promise<void>;

  onPick: (kind: PickKind, key: number | string) => void = () => {};
  onViewChange: (vs: ViewState) => void = () => {};
  onExpandChange: (expanded: boolean) => void = () => {};

  private inlineEl: HTMLElement;
  private stage: HTMLElement;
  private bannerSlot: HTMLElement;
  private scaleLine: HTMLElement;
  private scaleText: HTMLElement;
  private hint: HTMLElement;
  private ladder: HTMLElement;
  private inset: Inset;
  private geo: GeoAssets | null = null;
  private vs: ViewState = { longitude: INDIA_CENTER.longitude, latitude: INDIA_CENTER.latitude, zoom: 4, pitch: 0, bearing: 0 };
  private nonce = 0;

  private mode: 'empty' | 'landing' | 'candidates' | 'answer' = 'empty';
  private answer: MapAnswer | null = null;
  private sim: MapSim | null = null;
  private pins: Pin[] = [];
  private diamonds: Diamond[] = [];
  private markers: Marker[] = [];
  private markerPick: ((i: number) => void) | null = null;
  private furtherPin: Pin | null = null;
  private hl: { kind: 'result' | 'whynot'; key: number | string } | null = null;
  private labels: LabelOut[] = [];
  private leaderSegs: [number, number][][] = [];
  private overlay: Layer[] = [];
  private overlayLabels: LabelOut[] = [];
  private extraLegend: LegendRow[] = [];
  private returnFocus: HTMLElement | null = null;
  private clickHandler: ((lngLat: [number, number], picked: unknown) => void) | null = null;
  private cursor: 'default' | 'crosshair' = 'default';
  private tierKey = '';
  /** Bumped whenever fanned pin positions change, so deck re-reads them. */
  private fanVersion = 0;
  /** Zoom of the last automatic answer fit; the answer collapses only well below it. */
  private answerFitZoom = 99;
  /** The last automatic fit, re-run when the map box changes size until the user moves the map. */
  private autoFit: (() => void) | null = null;
  private lastSize = '';
  private debounce = 0;
  private hinted = false;

  private overlayEl: HTMLElement | null = null;
  private topTitle: HTMLElement | null = null;
  private zoomPlaceBtn: HTMLButtonElement | null = null;
  private body: HTMLElement | null = null;
  private mapArea: HTMLElement | null = null;
  private rail: HTMLElement | null = null;
  private topbar: HTMLElement | null = null;
  private legendFloat: HTMLElement | null = null;
  private railKey: HTMLDetailsElement | null = null;
  private expanded = false;
  private escHandler = (e: KeyboardEvent) => { if (e.key === 'Escape' && this.expanded) this.collapse(); };

  constructor(inlineEl: HTMLElement) {
    injectMapCss();
    this.inlineEl = inlineEl;
    inlineEl.classList.add('vfm-inline');
    this.stage = el('div', 'vfm-stage');
    const canvasHost = el('div', 'vfm-canvas');
    this.stage.appendChild(canvasHost);
    inlineEl.appendChild(this.stage);

    // Controls
    this.bannerSlot = el('div', 'vfm-banner');
    const zoom = el('div', 'vfm-zoom');
    const zin = el('button', 'vfm-btn', '+'); zin.type = 'button'; zin.title = 'Zoom in'; zin.setAttribute('aria-label', 'Zoom in');
    const zout = el('button', 'vfm-btn', '−'); zout.type = 'button'; zout.title = 'Zoom out'; zout.setAttribute('aria-label', 'Zoom out');
    zin.onclick = () => this.zoomBy(1);
    zout.onclick = () => this.zoomBy(-1);
    this.ladder = el('div', 'vfm-ladder');
    for (const step of LADDER) {
      const b = el('button', 'vfm-btn', step.label); b.type = 'button';
      b.onclick = () => (step.zoom === 'india' ? this.showIndia() : this.zoomTo(step.zoom));
      this.ladder.appendChild(b);
    }
    zoom.append(zin, zout, this.ladder);
    const expand = el('button', 'vfm-btn vfm-expand', 'Expand map'); expand.type = 'button';
    expand.title = 'Open a large map you can zoom and move';
    expand.onclick = () => this.expand();
    this.inset = new Inset(() => this.showIndia());
    const scale = el('div', 'vfm-scale');
    const bar = el('div', 'vfm-scale-bar');
    this.scaleLine = el('div', 'vfm-scale-line');
    this.scaleText = el('span');
    bar.append(this.scaleText, this.scaleLine);
    const north = el('span', 'vfm-north', 'N ↑'); north.title = 'North is up';
    scale.append(bar, north);
    this.hint = el('div', 'vfm-hint', 'Scroll or pinch to zoom, drag to move');
    this.stage.append(this.bannerSlot, zoom, expand, this.inset.el, scale, this.hint);
    this.stage.addEventListener('pointerenter', () => this.showHint());

    this.legendEl = el('ul', 'vfm-legend');
    this.legendEl.setAttribute('aria-label', 'Map legend');
    inlineEl.after(this.legendEl);

    this.deck = new Deck<MapView>({
      parent: canvasHost as HTMLDivElement,
      width: '100%',
      height: '100%',
      views: new MapView({ repeat: false }),
      initialViewState: { ...this.vs, minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM },
      controller: { scrollZoom: true, dragPan: true, touchZoom: true, doubleClickZoom: true, dragRotate: false, touchRotate: false, keyboard: false },
      useDevicePixels: true,
      onViewStateChange: ({ viewState, interactionState }) => {
        const is = interactionState as { isDragging?: boolean; isPanning?: boolean; isZooming?: boolean } | undefined;
        if (is && (is.isDragging || is.isPanning || is.isZooming)) this.autoFit = null;
        const v = this.clamp(viewState as ViewState);
        this.vs = v;
        this.viewChanged();
        return v as never;
      },
      getCursor: ({ isHovering, isDragging }) =>
        this.cursor === 'crosshair' ? 'crosshair' : isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab',
      getTooltip: (info: PickingInfo) => this.tooltip(info),
      onClick: (info: PickingInfo) => this.click(info),
      layers: [],
    });
    new ResizeObserver(() => {
      const { w, h } = this.size();
      const key = `${w}x${h}`;
      if (key === this.lastSize) return;
      this.lastSize = key;
      // Until the user moves the map, keep the last automatic fit as the box changes size.
      if (this.autoFit) this.autoFit(); else this.setView(this.vs, 0);
      this.updateChrome();
      this.schedule();
    }).observe(this.stage);
    (window as unknown as { __vfMap: MapHost }).__vfMap = this;
    // The canvas is a picture for keyboard and screen-reader users: no tab stop without a name. The
    // zoom buttons, the legend and the lists beside the map carry what it shows.
    const quietCanvas = () => {
      const cv = canvasHost.querySelector('canvas');
      if (cv) { cv.tabIndex = -1; cv.setAttribute('role', 'img'); cv.setAttribute('aria-label', 'Map. The legend and the lists on the page say what it shows.'); }
    };
    quietCanvas();
    setTimeout(quietCanvas, 0);

    this.ready = loadGeo().then((g) => {
      this.geo = g;
      this.inset.draw(g);
      if (this.mode === 'empty') this.vs = this.indiaView();
      this.setView(this.vs, 0);
      this.recompute();
    }).catch((e) => { console.error('map assets failed', e); });
  }

  // ---------------------------------------------------------------- public API

  get viewState(): ViewState { return { ...this.vs }; }

  /** The current viewport, for project/unproject. */
  get viewport(): WebMercatorViewport {
    const { w, h } = this.size();
    return new WebMercatorViewport({ width: w, height: h, longitude: this.vs.longitude, latitude: this.vs.latitude, zoom: this.vs.zoom });
  }

  /** Landing: all of India with the example places as clickable dots. */
  showLanding(examples: MapExample[], onPick: (i: number) => void): void {
    this.reset('landing');
    this.markers = examples.map((e, i) => ({ i, lon: e.lon, lat: e.lat, label: e.label, num: '' }));
    this.markerPick = onPick;
    this.autoFit = () => this.setView(this.indiaView(), 0);
    this.autoFit();
    this.recompute();
  }

  /** Ambiguous place: numbered candidates, fitted. */
  showCandidates(cands: MapCandidate[], onPick: (i: number) => void): void {
    this.reset('candidates');
    this.markers = [];
    cands.forEach((c, i) => {
      if (c.lat != null && c.lon != null) this.markers.push({ i, lon: c.lon, lat: c.lat, label: c.label.split(',').slice(0, 2).join(','), num: String(i + 1) });
    });
    this.markerPick = onPick;
    this.autoFit = this.markers.length
      ? () => this.fit(extent(this.markers.map((m) => [m.lon, m.lat])), 60, 0, 10, true)
      : () => this.setView(this.indiaView(), 0);
    this.autoFit();
    this.recompute();
  }

  /**
   * Show an answer: window ring, precision circle, result pins, why-not diamonds, the origin and
   * labels, then fit. Calling it again with the same answer object (for example when /api/sim
   * arrives) only adds the sim's context labels and does not move the view.
   */
  showAnswer(a: MapAnswer, sim: MapSim | null): void {
    const same = this.answer === a && this.mode === 'answer';
    this.sim = sim && sim.status === 'ok' ? sim : null;
    if (same) { this.recompute(); return; }
    this.reset('answer');
    this.answer = a;
    const cards: MapCard[] = this.isAbstention() ? (a.beyond_radius ? [a.beyond_radius] : []) : (a.results ?? []);
    this.pins = [];
    cards.forEach((c, i) => {
      if (c.lat == null || c.lon == null) return;
      this.pins.push({ kind: 'result', key: i, num: String(i + 1), lon: c.lon, lat: c.lat, dlon: c.lon, dlat: c.lat,
        approx: !!c.position_approximate, name: cardShortName(c), time: cardTime(c, !!a.origin?.too_coarse) });
    });
    const items = a.why_not?.items;
    if (items && items.length) {
      this.diamonds = items.filter((d) => d.lat != null && d.lon != null).map((d) => ({ letter: d.letter, lon: d.lon!, lat: d.lat!, name: d.name }));
    } else {
      this.diamonds = (a.unverified_nearby ?? []).filter((c) => c.lat != null && c.lon != null)
        .map((c) => ({ letter: '', lon: c.lon!, lat: c.lat!, name: cardShortName(c) }));
    }
    this.autoFit = () => this.fitAnswer(0);
    this.autoFit();
    this.recompute();
  }

  /** Add or replace the /api/sim payload (context labels) without moving the view. */
  setSim(sim: MapSim | null): void {
    this.sim = sim && sim.status === 'ok' ? sim : null;
    this.recompute();
  }

  /** Emphasise one pin or diamond (bigger, with its name). null clears. */
  highlight(kind: 'result' | 'whynot', key: number | string | null): void {
    this.hl = key == null ? null : { kind, key };
    this.recompute();
  }

  /** Fly to a card's pin: 'result' = index into results, 'further' = index into further_away. */
  focus(kind: 'result' | 'further', idx: number): void {
    this.autoFit = null;
    const a = this.answer, o = this.origin();
    if (!a) return;
    let c: MapCard | undefined;
    if (kind === 'result') {
      c = this.isAbstention() ? a.beyond_radius ?? undefined : a.results?.[idx];
      this.furtherPin = null;
      this.hl = { kind: 'result', key: idx };
    } else {
      c = a.further_away?.[idx];
      this.furtherPin = c && c.lat != null && c.lon != null
        ? { kind: 'further', key: idx, num: '', lon: c.lon, lat: c.lat, dlon: c.lon, dlat: c.lat, approx: !!c.position_approximate, name: cardShortName(c), time: cardTime(c, !!this.answer?.origin?.too_coarse) }
        : null;
    }
    if (!c || c.lat == null || c.lon == null) { this.recompute(); return; }
    const pts: [number, number][] = [[c.lon, c.lat]];
    if (o) pts.push(o);
    this.fit(extent(pts), 70, 900, 13);
    this.recompute();
  }

  /** Fit [[west, south], [east, north]] with padding in px; ms > 0 flies there. */
  fit(bounds: Bounds, padPx = 48, ms = 0, maxZoom = 13, clearInset = false): void {
    // The latest fit is the one the box keeps when it changes size (expanding, the simulator's rail
    // opening), until the user moves the map. Without this, a caller's fit (the simulator's camera)
    // was replaced by the answer's fit as soon as the expanded box resized.
    this.autoFit = () => this.fitNow(bounds, padPx, 0, maxZoom, clearInset);
    this.fitNow(bounds, padPx, ms, maxZoom, clearInset);
  }

  private fitNow(bounds: Bounds, padPx: number, ms: number, maxZoom: number, clearInset: boolean): void {
    const { w, h } = this.size();
    const pad = Math.max(0, Math.min(padPx, Math.floor(Math.min(w, h) / 2) - 20));
    const [[x0, y0], [x1, y1]] = bounds;
    const vp = new WebMercatorViewport({ width: w, height: h });
    // In the large map the floating legend (bottom) and the on-map bars (top) cover part of the view.
    const ins = this.expanded
      ? { top: Math.max(this.bannerSlot.childElementCount ? 110 : 64, this.coveredTop() + 8), bottom: Math.min(h * 0.45, this.coveredBottom() + 12) }
      : { top: 0, bottom: 0 };
    const top = Math.min(pad + ins.top, h * 0.32), bottom = Math.min(pad + ins.bottom, h * 0.45);
    // clearInset: keep markers out from under the India inset (bottom-left), for spread-out
    // candidates whose corner points would otherwise sit behind it.
    const insetW = clearInset ? (this.inset.el.offsetWidth || 0) + 10 : 0;
    const left = this.expanded ? Math.min(pad + 44 + insetW, w * 0.3) : Math.min(pad + insetW, w * 0.3);
    const right = this.expanded ? Math.min(pad + this.coveredRight(), w * 0.4) : pad;
    const f = vp.fitBounds([[x0, y0], [x1, y1]], { padding: { top, bottom, left, right }, maxZoom, minExtent: 0.01 });
    this.setView({ longitude: f.longitude, latitude: f.latitude, zoom: f.zoom }, ms);
  }

  /** Fly out to the whole of India (1200 ms by default). */
  showIndia(ms = 1200): void {
    this.autoFit = null;
    this.setView(this.indiaView(), ms);
  }

  /** Fit the window ring around the searched place, plus the beyond card on an abstention. */
  zoomToPlace(ms = 1000): void {
    this.autoFit = null;
    const o = this.origin();
    if (!o) { this.showIndia(ms); return; }
    const km = this.ringKm() ?? 25;
    const b = kmBox(o[0], o[1], km * 1.05);
    const pts: [number, number][] = [b[0], b[1]];
    const br = this.answer?.beyond_radius;
    if (this.isAbstention() && br?.lat != null && br.lon != null) pts.push([br.lon, br.lat]);
    this.fit(extent(pts), 24, ms, 13);
  }

  /** Layers drawn above the pins and below the labels (the simulator's dots, trails, marks). */
  setOverlayLayers(layers: Layer[]): void {
    const had = this.overlay.length > 0;
    this.overlay = layers;
    if (had !== layers.length > 0) { this.tierKey = ''; this.recompute(); return; }
    this.render();
  }

  /** Names the overlay wants on the map (the simulator's counts). They go through the same declutter as
   *  every other name, after the searched place and the option names, so nothing prints over anything. */
  setOverlayLabels(labels: { text: string; lon: number; lat: number; style: 'ring' | 'claimnote' | 'note' | 'whatif' }[]): void {
    // what-if marks first (they are what the user just did), then the counts
    this.overlayLabels = labels.map((l, k) => ({ text: l.text, lon: l.lon, lat: l.lat, style: l.style, anchor: 'start', offset: [12, 0],
      dot: false, prio: (l.style === 'whatif' ? 0.55 : 0.9) + k * 0.001, alts: aroundPoint(12) }));
    this.recompute();
  }

  setExtraLegend(rows: LegendRow[]): void {
    this.extraLegend = rows;
    this.renderLegend();
  }

  /** A full-width element over the map top (the WHAT-IF banner). null removes it. */
  setBanner(e: HTMLElement | null): void {
    this.bannerSlot.replaceChildren(...(e ? [e] : []));
    this.stage.classList.toggle('has-banner', !!e);
  }

  /**
   * While set, every click on the map goes here (pins included) instead of onPick:
   * fn([lon, lat], pickedObjectOrNull). Pass null to restore normal picking.
   */
  setMapClickHandler(fn: ((lngLat: [number, number], picked: unknown) => void) | null): void {
    this.clickHandler = fn;
  }

  setCursor(c: 'default' | 'crosshair'): void {
    this.cursor = c;
    this.deck.setProps({});
    this.deck.redraw('cursor');
  }

  isExpanded(): boolean { return this.expanded; }

  /** Open the full-screen map. The same Deck moves into it. */
  expand(opts: { rail?: boolean } = {}): ExpandedSlots {
    this.buildOverlay();
    const ov = this.overlayEl!;
    this.body!.classList.toggle('with-rail', !!opts.rail);
    // With a rail the key is docked in it (it covered the dots when it floated); without, it floats.
    if (opts.rail) {
      this.railKey!.appendChild(this.legendEl);
      if (this.railKey!.parentElement !== this.rail) this.rail!.appendChild(this.railKey!);
      this.legendFloat!.style.display = 'none';
    } else {
      this.legendFloat!.appendChild(this.legendEl);
      this.legendFloat!.style.display = '';
      this.railKey!.remove();
    }
    const name = this.answer ? shortName(this.answer) : null;
    this.topTitle!.textContent = name ? `Map of ${name}` : 'Map of India';
    this.zoomPlaceBtn!.textContent = name ? `Zoom to ${name}` : 'Zoom to place';
    this.zoomPlaceBtn!.style.display = name ? '' : 'none';
    if (!this.expanded) {
      ov.style.display = '';
      this.mapArea!.appendChild(this.stage);
      this.stage.classList.add('vfm-expanded');
      document.body.style.overflow = 'hidden';
      document.addEventListener('keydown', this.escHandler);
      // a modal dialog: the page behind is inert, focus moves in and comes back on close
      const act = document.activeElement;
      this.returnFocus = act instanceof HTMLElement && act !== document.body ? act : null;
      for (const ch of Array.from(document.body.children)) if (ch !== ov && ch instanceof HTMLElement) ch.inert = true;
      this.expanded = true;
      this.afterReparent();
      this.onExpandChange(true);
      requestAnimationFrame(() => this.topTitle?.focus({ preventScroll: true }));
    }
    return { rail: this.rail!, sheet: this.rail!, mapEl: this.mapArea!, topbar: this.topbar!, key: opts.rail ? this.railKey! : undefined };
  }

  collapse(): void {
    if (!this.expanded) return;
    this.inlineEl.appendChild(this.stage);
    this.inlineEl.after(this.legendEl);
    this.stage.classList.remove('vfm-expanded');
    this.overlayEl!.style.display = 'none';
    this.railKey?.remove();
    document.body.style.overflow = '';
    document.removeEventListener('keydown', this.escHandler);
    for (const ch of Array.from(document.body.children)) if (ch instanceof HTMLElement) ch.inert = false;
    this.expanded = false;
    this.afterReparent();
    this.onExpandChange(false);
    const back = this.returnFocus;
    this.returnFocus = null;
    if (back && document.contains(back)) back.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- internals: state

  private reset(mode: MapHost['mode']): void {
    this.mode = mode;
    this.answer = null;
    this.pins = [];
    this.diamonds = [];
    this.markers = [];
    this.markerPick = null;
    this.furtherPin = null;
    this.hl = null;
    this.leaderSegs = [];
    this.answerFitZoom = 99;
  }

  private isAbstention(): boolean { return this.answer?.status === 'no_reliable_option'; }

  private origin(): [number, number] | null {
    const l = this.answer?.location;
    return l && l.lat != null && l.lon != null ? [l.lon, l.lat] : null;
  }

  private ringKm(): number | null {
    const a = this.answer;
    if (!a) return null;
    // a place too large to drive from shows straight-line distance only: no drive-time ring
    if (a.origin?.too_coarse) return null;
    if (a.window && typeof a.window.km_equivalent === 'number') return a.window.km_equivalent;
    return typeof a.radius_km === 'number' && !a.window ? a.radius_km : null;
  }

  private ringText(): string | null {
    const a = this.answer;
    if (!a) return null;
    const ht = hoursText(a);
    if (ht) {
      if (this.isAbstention()) {
        const phrase = this.sim?.service_phrase ?? (a.service_label ? lowerFirst(a.service_label) : 'it');
        return `${ht}: no record here shows ${phrase}`;
      }
      return `${ht} estimated drive`;
    }
    return typeof a.radius_km === 'number' ? `${a.radius_km} km` : null;
  }

  private collapseText(): string | null {
    const a = this.answer;
    if (!a) return null;
    const name = shortName(a);
    if (this.isAbstention()) return `${name}: no clear evidence nearby`;
    const n = (a.results ?? []).length;
    if (!n) return `${name}: no clear evidence nearby`;
    const ht = hoursText(a);
    const within = ht ? `within ${ht}` : typeof a.radius_km === 'number' ? `within ${a.radius_km} km` : 'nearby';
    return `${name}: ${n} ${n === 1 ? 'option' : 'options'} ${within}`;
  }

  private fitAnswer(ms: number): void {
    const o = this.origin();
    if (!o) { this.setView(this.indiaView(), ms); return; }
    const pts: [number, number][] = [o, ...this.pins.map((p) => [p.lon, p.lat] as [number, number])];
    let b = extent(pts);
    if (this.pins.length && !this.isAbstention()) {
      // 5 km padded box of the extent
      const lat = (b[0][1] + b[1][1]) / 2;
      const dLat = 5 / 111.2, dLon = 5 / (111.2 * Math.cos((lat * Math.PI) / 180));
      b = [[b[0][0] - dLon, b[0][1] - dLat], [b[1][0] + dLon, b[1][1] + dLat]];
    } else if (!this.pins.length) {
      b = kmBox(o[0], o[1], Math.min(60, (this.ringKm() ?? 40) * 0.6));
    }
    this.fit(b, 56, ms, 12.5);
    this.answerFitZoom = this.vs.zoom;
  }

  /** Below this zoom the answer collapses into one marker: 7, or lower when the fit itself is lower. */
  private collapseZ(): number {
    // while the simulator draws, the options stay as numbered pins down to state zoom: the run is
    // fitted wider than the answer, and a collapsed marker would hide the places the dots go to
    if (this.overlay.length) return Math.min(5, this.answerFitZoom - 1);
    return Math.min(COLLAPSE_Z, this.answerFitZoom - 1);
  }

  // ---------------------------------------------------------------- internals: view

  /** Pixels at the top of the expanded map covered by a bar drawn over it (the simulator's sim bar). */
  private coveredTop(): number {
    const bar = this.mapArea?.querySelector('.sim-bar') as HTMLElement | null;
    if (!bar || !bar.offsetHeight) return 0;
    const st = this.stage.getBoundingClientRect().top, bb = bar.getBoundingClientRect().bottom;
    return Math.max(0, bb - st);
  }

  /** Pixels at the bottom of the expanded map covered by the floating legend (when it sits at the
   *  bottom-left) or by the simulator's takeaway bar. */
  private coveredBottom(): number {
    const sr = this.stage.getBoundingClientRect();
    let cover = 0;
    const els = [this.legendFloat, this.mapArea?.querySelector('.sim-takeaway') as HTMLElement | null];
    for (const e of els) {
      if (!e || !e.offsetHeight) continue;
      const r = e.getBoundingClientRect();
      if (e === this.legendFloat && r.left > sr.left + sr.width / 2) continue; // on the right: see coveredRight
      cover = Math.max(cover, sr.bottom - r.top);
    }
    return Math.max(0, Math.min(cover, sr.height));
  }

  /** Pixels at the right of the expanded map covered by the floating legend when it sits on the right. */
  private coveredRight(): number {
    const lf = this.legendFloat;
    if (!lf || !lf.offsetHeight) return 0;
    const sr = this.stage.getBoundingClientRect(), r = lf.getBoundingClientRect();
    return r.left > sr.left + sr.width / 2 ? Math.max(0, sr.right - r.left) : 0;
  }

  private size(): { w: number; h: number } {
    return { w: Math.max(120, this.stage.clientWidth || 600), h: Math.max(120, this.stage.clientHeight || 430) };
  }

  private indiaView(): ViewState {
    const { w, h } = this.size();
    const vp = new WebMercatorViewport({ width: w, height: h });
    const f = vp.fitBounds(INDIA_BOUNDS, { padding: Math.min(16, Math.floor(Math.min(w, h) / 8)) });
    return { longitude: INDIA_CENTER.longitude, latitude: INDIA_CENTER.latitude, zoom: Math.max(2, Math.min(MIN_ZOOM + 1, f.zoom)) };
  }

  /** 3.6, or lower when the map box is too small to show all of India at 3.6. */
  private minZoom(): number {
    return Math.min(MIN_ZOOM, this.indiaView().zoom);
  }

  private clamp(v: ViewState): ViewState {
    return {
      ...v,
      longitude: Math.min(CLAMP.lon1, Math.max(CLAMP.lon0, v.longitude)),
      latitude: Math.min(CLAMP.lat1, Math.max(CLAMP.lat0, v.latitude)),
      zoom: Math.min(MAX_ZOOM, Math.max(this.minZoom(), v.zoom)),
      pitch: 0,
      bearing: 0,
    };
  }

  private setView(v: ViewState, ms: number): void {
    const t = this.clamp(v);
    this.vs = t;
    this.nonce = this.nonce ? 0 : 1; // initialViewState is deep-compared: make every call a change
    this.deck.setProps({
      initialViewState: {
        ...t, longitude: t.longitude + this.nonce * 1e-9, minZoom: this.minZoom(), maxZoom: MAX_ZOOM,
        ...(ms > 0 ? { transitionDuration: ms, transitionInterpolator: new FlyToInterpolator() } : {}),
      },
    });
    this.viewChanged();
  }

  private zoomBy(dz: number): void {
    this.autoFit = null;
    this.setView({ ...this.vs, zoom: this.vs.zoom + dz }, 300);
  }

  private zoomTo(z: number): void {
    this.autoFit = null;
    const o = this.origin();
    this.setView({ longitude: o ? o[0] : this.vs.longitude, latitude: o ? o[1] : this.vs.latitude, zoom: z }, 900);
  }

  private afterReparent(): void {
    this.deck.setProps({ width: '100%', height: '100%' });
    requestAnimationFrame(() => {
      this.setView(this.vs, 0); // the box changed size: refresh the zoom floor
      this.deck.redraw('reparent');
      this.updateChrome();
      this.recompute();
    });
  }

  private viewChanged(): void {
    this.onViewChange(this.viewState);
    this.updateChrome();
    const z = this.vs.zoom;
    const tier = `${z < 3.6 ? -2 : z < 4.6 ? -1 : z < 6 ? 0 : z < 9 ? 1 : 2}|${z < this.collapseZ()}|${z >= DIAMOND_Z}`;
    if (tier !== this.tierKey) { this.tierKey = tier; this.render(); }
    this.schedule();
  }

  private schedule(): void {
    clearTimeout(this.debounce);
    this.debounce = window.setTimeout(() => this.recompute(), 80);
  }

  private updateChrome(): void {
    this.stage.classList.toggle('vfm-small', this.size().h < 330);
    const vp = this.viewport;
    const b = vp.getBounds() as [number, number, number, number];
    this.inset.update(b, this.origin() ?? (this.markers.length === 1 ? [this.markers[0].lon, this.markers[0].lat] : null));
    // Scale bar
    const mpp = metersPerPixel(this.vs.latitude, this.vs.zoom);
    const maxM = mpp * 90;
    const steps = [100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6];
    let m = steps[0];
    for (const s of steps) if (s <= maxM) m = s;
    this.scaleLine.style.width = `${Math.round(m / mpp)}px`;
    this.scaleText.textContent = m >= 1000 ? `${m / 1000} km` : `${m} m`;
    // Ladder state
    const z = this.vs.zoom;
    const iz = this.indiaView().zoom;
    Array.from(this.ladder.children).forEach((b2, i) => {
      const lz = LADDER[i].zoom === 'india' ? iz : (LADDER[i].zoom as number);
      b2.classList.toggle('on', Math.abs(lz - z) < 0.6);
    });
  }

  private showHint(): void {
    if (this.hinted) return;
    this.hinted = true;
    this.hint.classList.add('on');
    setTimeout(() => this.hint.classList.remove('on'), 3000);
  }

  // ---------------------------------------------------------------- internals: screen-space work

  /** Fan out overlapping pins, dash the leader, place labels; then draw. */
  private recompute(): void {
    clearTimeout(this.debounce);
    const vp = this.viewport;
    const { w, h } = this.size();
    const z = this.vs.zoom;
    const collapsed = this.mode === 'answer' && z < this.collapseZ();
    const allPins = this.furtherPin ? [...this.pins, this.furtherPin] : this.pins;

    // Fan: pins within 14px of each other spread on an 18px ring around their centre.
    const scr = allPins.map((p) => vp.project([p.lon, p.lat]) as [number, number]);
    const done = new Array(allPins.length).fill(false);
    // Pins on top of the origin dot move out onto a ring around it, so neither hides the other.
    const og = this.mode === 'answer' ? this.origin() : null;
    if (og && !collapsed) {
      const [ox, oy] = vp.project(og) as [number, number];
      const grp: number[] = [];
      for (let j = 0; j < allPins.length; j++) {
        const near = Math.hypot(scr[j][0] - ox, scr[j][1] - oy) < 14
          || grp.some((k) => Math.hypot(scr[k][0] - scr[j][0], scr[k][1] - scr[j][1]) < 14);
        if (near) grp.push(j);
      }
      grp.forEach((k, n) => {
        done[k] = true;
        const ang = -Math.PI / 2 + (2 * Math.PI * n) / Math.max(grp.length, 3);
        const [lon, lat] = vp.unproject([ox + 20 * Math.cos(ang), oy + 20 * Math.sin(ang)]) as [number, number];
        allPins[k].dlon = lon; allPins[k].dlat = lat;
      });
    }
    for (let i = 0; i < allPins.length; i++) {
      if (done[i]) continue;
      const grp = [i];
      for (let j = i + 1; j < allPins.length; j++) {
        if (!done[j] && grp.some((k) => Math.hypot(scr[k][0] - scr[j][0], scr[k][1] - scr[j][1]) < 14)) grp.push(j);
      }
      grp.forEach((k) => { done[k] = true; });
      if (grp.length === 1) { allPins[i].dlon = allPins[i].lon; allPins[i].dlat = allPins[i].lat; continue; }
      const cx = grp.reduce((s, k) => s + scr[k][0], 0) / grp.length;
      const cy = grp.reduce((s, k) => s + scr[k][1], 0) / grp.length;
      grp.forEach((k, n) => {
        const ang = -Math.PI / 2 + (2 * Math.PI * n) / grp.length;
        const [lon, lat] = vp.unproject([cx + 18 * Math.cos(ang), cy + 18 * Math.sin(ang)]) as [number, number];
        allPins[k].dlon = lon; allPins[k].dlat = lat;
      });
    }

    this.fanVersion++;

    // Leader line (abstention): dashes about 7px on, 7px off at this zoom.
    this.leaderSegs = [];
    const o = this.origin();
    const br = this.answer?.beyond_radius;
    if (this.isAbstention() && o && br?.lat != null && br.lon != null && !collapsed) {
      const a = vp.project(o) as [number, number], b = vp.project([br.lon, br.lat]) as [number, number];
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const n = Math.min(600, Math.floor(L / 14));
      for (let k = 0; k < n; k++) {
        const f0 = (k * 14) / L, f1 = Math.min(1, (k * 14 + 7) / L);
        this.leaderSegs.push([
          vp.unproject([a[0] + (b[0] - a[0]) * f0, a[1] + (b[1] - a[1]) * f0]) as [number, number],
          vp.unproject([a[0] + (b[0] - a[0]) * f1, a[1] + (b[1] - a[1]) * f1]) as [number, number],
        ]);
      }
    }

    // Labels
    if (this.geo) {
      const reserved: [number, number, number, number][] = [];
      const box = (x: number, y: number, r: number) => reserved.push([x - r, y - r, x + r, y + r]);
      if (!collapsed) {
        for (const p of allPins) { const [x, y] = vp.project([p.dlon, p.dlat]); box(x, y, 13); }
        if (z >= DIAMOND_Z) for (const d of this.diamonds) { const [x, y] = vp.project([d.lon, d.lat]); box(x, y, 10); }
      }
      if (o) { const [x, y] = vp.project(o); box(x, y, 8); }
      // Names never hide under the map's own controls (zoom buttons, inset, scale, expand).
      const sr = this.stage.getBoundingClientRect();
      this.stage.querySelectorAll<HTMLElement>('.vfm-zoom > *, .vfm-inset, .vfm-scale > *, .vfm-expand').forEach((c) => {
        const r = c.getBoundingClientRect();
        if (r.width && r.height) reserved.push([r.left - sr.left - 3, r.top - sr.top - 3, r.right - sr.left + 3, r.bottom - sr.top + 3]);
      });
      for (const m of this.markers) { const [x, y] = vp.project([m.lon, m.lat]); box(x, y, 9); }

      const fixed: LabelOut[] = [];
      const extra: LabelOut[] = [...this.overlayLabels];
      let search: { text: string; lon: number; lat: number } | null = null;
      if (this.mode === 'answer' && this.answer && o) {
        if (collapsed) {
          const t = this.collapseText();
          if (t) fixed.push({ text: t, lon: o[0], lat: o[1], style: 'note', anchor: 'start', offset: [11, 0], dot: false, force: true, alts: aroundPoint(11) });
        } else {
          search = { text: this.searchLabel(), lon: o[0], lat: o[1] };
          const km = this.ringKm(), rt = this.ringText();
          if (km && rt) {
            const top = circlePath(o[0], o[1], km, 4)[0];
            fixed.push({ text: rt, lon: top[0], lat: top[1], style: this.isAbstention() ? 'claimnote' : 'ring', anchor: 'middle', offset: [0, 11], dot: false });
          }
          if (this.isAbstention() && br?.lat != null && br.lon != null) {
            const t = cardTime(br, !!this.answer?.origin?.too_coarse);
            const [ax, ay] = vp.project(o), [bx, by] = vp.project([br.lon, br.lat]);
            const [mlon, mlat] = vp.unproject([(ax + bx) / 2, (ay + by) / 2]) as [number, number];
            if (t) fixed.push({ text: t, lon: mlon, lat: mlat, style: 'note', anchor: 'start', offset: [8, -10], dot: false, alts: aroundPoint(10) });
          }
          const hp = this.highlightedPin();
          if (hp) fixed.push({ text: hp.time ? `${hp.name}, ${hp.time}` : hp.name, lon: hp.dlon, lat: hp.dlat, style: 'note', anchor: 'start', offset: [16, 0], dot: false, force: true, alts: aroundPoint(16) });
          const hd = this.highlightedDiamond();
          if (hd && z >= DIAMOND_Z) fixed.push({ text: hd.name, lon: hd.lon, lat: hd.lat, style: 'note', anchor: 'start', offset: [13, 0], dot: false, force: true });
          if (this.furtherPin) {
            const f = this.furtherPin;
            fixed.push({ text: f.time ? `${f.name}, ${f.time}` : f.name, lon: f.dlon, lat: f.dlat, style: 'note', anchor: 'start', offset: [14, 0], dot: false, force: true, alts: aroundPoint(14) });
          }
          // every option is named on the map when there is room (the highlighted one already is)
          for (const p of this.pins) {
            if (p === hp) continue;
            const nm = p.name.length > 28 ? `${p.name.slice(0, 26).replace(/\s+\S*$/, '')}...` : p.name;
            extra.push({ text: nm, lon: p.dlon, lat: p.dlat, style: 'note', anchor: 'start', offset: [14, 0], dot: false,
              prio: 0.6 + p.key * 0.01, alts: aroundPoint(14) });
          }
        }
      }
      for (const m of this.markers) {
        fixed.push({ text: m.label, lon: m.lon, lat: m.lat, style: 'note', anchor: 'start', offset: [11, 0], dot: false });
      }
      const simLabels: MapSimLabel[] = this.sim?.context?.labels ?? [];
      this.labels = computeLabels({
        geo: this.geo, vp, zoom: z, width: w, height: h, search, fixed,
        originDistrict: this.answer?.location?.district ?? null, simLabels, reserved, extra,
      });
    }
    this.render();
    this.renderLegend();
  }

  /** The searched place's map label: the short name, with the district after a bare PIN. */
  private searchLabel(): string {
    const a = this.answer!;
    const n = shortName(a);
    const d = a.location?.district;
    return /^PIN \d{6}$/.test(n) && d ? `${n}, ${titleCase(d)}` : n;
  }

  private landCache: Ring[][] | null = null;
  /** India's land: the state polygons plus the (finer) outline rings, so no sliver shows between them. */
  private landPolys(g: GeoAssets): Ring[][] {
    if (!this.landCache) this.landCache = [...g.states.flatMap((s) => s.polygons), ...g.outline.map((r) => [r])];
    return this.landCache;
  }

  private highlightedPin(): Pin | null {
    const hl = this.hl;
    if (!hl || hl.kind !== 'result') return null;
    return this.pins.find((p) => p.key === hl.key) ?? null;
  }

  private highlightedDiamond(): Diamond | null {
    const hl = this.hl;
    if (!hl || hl.kind !== 'whynot') return null;
    return this.diamonds.find((d) => d.letter === hl.key) ?? null;
  }

  // ---------------------------------------------------------------- internals: drawing

  private render(): void {
    const layers: Layer[] = [];
    const g = this.geo;
    const z = this.vs.zoom;
    if (g) {
      layers.push(new SolidPolygonLayer<Ring[]>({
        id: 'land', data: this.landPolys(g), getPolygon: (p) => p as never, getFillColor: C.paper, pickable: false,
      }));
      layers.push(new ScatterplotLayer({
        id: 'po', data: { length: g.nPo, attributes: { getPosition: { value: g.po, size: 2 } } } as never,
        getRadius: 1, radiusUnits: 'pixels', radiusScale: z < 3.6 ? 0.6 : z < 6 ? 0.9 : z < 9 ? 1.6 : 2.4, opacity: z < 3.6 ? 0.5 : z < 4.6 ? 0.75 : 1,
        getFillColor: C.po, stroked: false, pickable: false,
      }));
      layers.push(new PathLayer<Ring>({
        id: 'neighbours', data: g.neighbourRings, getPath: (r) => r as never, getColor: C.neighbour, getWidth: 0.9, widthUnits: 'pixels', pickable: false,
      }));
      layers.push(new PathLayer<Ring>({
        id: 'states', data: g.stateRings, getPath: (r) => r as never, getColor: C.border, getWidth: 1, widthUnits: 'pixels', pickable: false,
      }));
      layers.push(new PathLayer<Ring>({
        id: 'outline', data: g.outline, getPath: (r) => r as never, getColor: C.outline, getWidth: 1.2, widthUnits: 'pixels', pickable: false,
      }));
    }

    const o = this.origin();
    const collapsed = this.mode === 'answer' && z < this.collapseZ();
    // The simulator's dots and trails go above the geography and the window ring, but under the option
    // pins and their numbers, so a pile of arrived dots never hides "1", "2", "3".
    let insertAt = layers.length;
    if (this.mode === 'answer' && o && !collapsed) {
      const km = this.ringKm();
      if (km) {
        layers.push(new PathLayer({
          id: 'ring', data: [circlePath(o[0], o[1], km)], getPath: (p) => p as never, getColor: C.ring, getWidth: 1.6, widthUnits: 'pixels',
        }));
      }
      const pk = this.answer?.origin?.precision_km ?? this.answer?.location?.precision_km ?? null;
      if (pk != null && pk >= 2) {
        layers.push(new ScatterplotLayer({
          id: 'precision', data: [o], getPosition: (d) => d as never, getRadius: pk * 1000, radiusUnits: 'meters',
          filled: true, stroked: true, getFillColor: C.precision, getLineColor: C.precisionLine, lineWidthUnits: 'pixels', getLineWidth: 1,
        }));
      }
      if (this.leaderSegs.length) {
        layers.push(new PathLayer({
          id: 'leader', data: this.leaderSegs, getPath: (p) => p as never, getColor: C.leader, getWidth: 2, widthUnits: 'pixels',
        }));
      }
      insertAt = layers.length;
      if (z >= DIAMOND_Z && this.diamonds.length) {
        const hd = this.highlightedDiamond();
        layers.push(new IconLayer<Diamond>({
          id: 'diamonds', data: this.diamonds, iconAtlas: iconAtlas(), iconMapping: ICON_MAPPING,
          getIcon: () => 'diamond-neutral', getPosition: (d) => [d.lon, d.lat], sizeUnits: 'pixels',
          getSize: (d) => (d === hd ? 26 : 20), pickable: true, updateTriggers: { getSize: hd?.letter ?? '' },
        }));
        layers.push(new TextLayer<Diamond>({
          id: 'diamond-letters', data: this.diamonds.filter((d) => d.letter), getPosition: (d) => [d.lon, d.lat], getText: (d) => d.letter,
          getColor: C.white, getSize: 10.5, fontWeight: 700, fontFamily: FONT, getTextAnchor: 'middle', getAlignmentBaseline: 'center',
        }));
      }
      const allPins = this.furtherPin ? [...this.pins, this.furtherPin] : this.pins;
      const fanned = allPins.filter((p) => p.dlon !== p.lon || p.dlat !== p.lat);
      if (fanned.length) {
        layers.push(new LineLayer<Pin>({
          id: 'fan', data: fanned, getSourcePosition: (p) => [p.lon, p.lat], getTargetPosition: (p) => [p.dlon, p.dlat],
          getColor: C.fan, getWidth: 1, widthUnits: 'pixels', updateTriggers: { getTargetPosition: this.fanVersion },
        }));
        layers.push(new ScatterplotLayer<Pin>({
          id: 'fan-true', data: fanned, getPosition: (p) => [p.lon, p.lat], getRadius: 2, radiusUnits: 'pixels', getFillColor: C.ink,
        }));
      }
      const hp = this.highlightedPin();
      const hk = hp ? hp.key : -1;
      layers.push(new ScatterplotLayer<Pin>({
        id: 'pins', data: allPins, getPosition: (p) => [p.dlon, p.dlat],
        getRadius: (p) => (p === hp ? 14 : p.kind === 'further' ? 9 : 11), radiusUnits: 'pixels',
        getFillColor: (p) => (p.approx || p.kind === 'further' ? C.white : C.evidence),
        stroked: true, getLineColor: (p) => (p === hp ? C.ink : p.approx || p.kind === 'further' ? C.evidence : C.white),
        lineWidthUnits: 'pixels', getLineWidth: (p) => (p === hp ? 3 : 2), pickable: true,
        updateTriggers: { getPosition: this.fanVersion, getRadius: [hk, allPins.length], getLineColor: hk, getLineWidth: hk, getFillColor: allPins.length },
      }));
      layers.push(new TextLayer<Pin>({
        id: 'pin-num', data: allPins.filter((p) => p.num), getPosition: (p) => [p.dlon, p.dlat], getText: (p) => p.num,
        getColor: (p) => (p.approx ? C.evidence : C.white), getSize: 12.5, fontWeight: 700, fontFamily: FONT,
        getTextAnchor: 'middle', getAlignmentBaseline: 'center', updateTriggers: { getPosition: this.fanVersion },
      }));
    }
    if (this.mode === 'answer' && o) {
      // a target mark (red ring, white, red centre): told apart from every dot by shape, not only colour
      layers.push(new ScatterplotLayer({
        id: 'origin', data: [o], getPosition: (d) => d as never, getRadius: collapsed ? 9 : 8, radiusUnits: 'pixels',
        getFillColor: C.white, stroked: true, getLineColor: C.origin, lineWidthUnits: 'pixels', getLineWidth: 2.5,
        updateTriggers: { getRadius: collapsed },
      }));
      layers.push(new ScatterplotLayer({
        id: 'origin-core', data: [o], getPosition: (d) => d as never, getRadius: 3.5, radiusUnits: 'pixels',
        getFillColor: C.origin, stroked: false,
      }));
    }
    if (this.markers.length) {
      layers.push(new ScatterplotLayer<Marker>({
        id: 'markers', data: this.markers, getPosition: (m) => [m.lon, m.lat], getRadius: (m) => (m.num ? 10 : 6), radiusUnits: 'pixels',
        radiusMinPixels: 6, getFillColor: C.landing, stroked: true, getLineColor: C.white, lineWidthUnits: 'pixels', getLineWidth: 2, pickable: true,
      }));
      layers.push(new TextLayer<Marker>({
        id: 'marker-num', data: this.markers.filter((m) => m.num), getPosition: (m) => [m.lon, m.lat], getText: (m) => m.num,
        getColor: C.white, getSize: 12, fontWeight: 700, fontFamily: FONT, getTextAnchor: 'middle', getAlignmentBaseline: 'center',
      }));
    }

    layers.splice(insertAt, 0, ...this.overlay);

    // Labels, grouped by style
    const dots = this.labels.filter((l) => l.dot);
    if (dots.length) {
      layers.push(new ScatterplotLayer<LabelOut>({
        id: 'label-dots', data: dots, getPosition: (l) => [l.lon, l.lat], getRadius: 2.3, radiusUnits: 'pixels',
        getFillColor: C.townDot, stroked: true, getLineColor: C.paper, lineWidthUnits: 'pixels', getLineWidth: 1,
      }));
    }
    const byStyle = new Map<LabelStyle, LabelOut[]>();
    for (const l of this.labels) {
      const arr = byStyle.get(l.style) ?? [];
      arr.push(l);
      byStyle.set(l.style, arr);
    }
    for (const [style, data] of byStyle) {
      layers.push(new TextLayer<LabelOut>({
        id: `labels-${style}`, data, getPosition: (l) => [l.lon, l.lat], getText: (l) => l.text,
        getColor: LABEL_COLOR[style], getSize: LABEL_SIZE[style], sizeUnits: 'pixels',
        fontFamily: FONT, fontWeight: LABEL_WEIGHT[style], characterSet: 'auto',
        fontSettings: { sdf: true, fontSize: 64, buffer: 10, radius: 16, cutoff: 0.22 },
        outlineWidth: style === 'search' || style === 'note' ? 0.42 : 0.34, outlineColor: C.paper,
        getTextAnchor: (l) => l.anchor, getAlignmentBaseline: 'center', getPixelOffset: (l) => l.offset,
        pickable: false,
      }));
    }
    this.deck.setProps({ layers });
  }

  private renderLegend(): void {
    const rows: LegendRow[] = [];
    if (this.mode === 'answer' && this.answer) {
      if (this.pins.some((p) => !p.approx)) rows.push({ swatch: 'pin', label: 'Option with evidence in its record (number = order)' });
      if (this.pins.some((p) => p.approx)) rows.push({ swatch: 'hollow', label: 'White circle: option whose map position is approximate' });
      if (this.diamonds.length) {
        const strip = !!this.answer.why_not?.items?.length;
        const zoomIn = this.vs.zoom < DIAMOND_Z ? ', zoom in to see' : '';
        rows.push({ swatch: 'diamond', label: strip ? `Nearer record not recommended (see Why not the nearer ones?)${zoomIn}` : `Nearby record that lists it, not recommended${zoomIn}` });
      }
      if (this.isAbstention() && this.pins.length) rows.push({ swatch: 'dashed', color: EVIDENCE, label: 'Straight line to the nearest place with evidence' });
      if (this.origin()) rows.push({ swatch: 'origin', label: 'Searched place' });
      const pk = this.answer.origin?.precision_km ?? this.answer.location?.precision_km ?? null;
      if (pk != null && pk >= 2) rows.push({ swatch: 'area', label: `Shaded circle: the patient may be anywhere in here (about ${Math.round(pk)} km)` });
      const rt = this.ringText();
      if (this.ringKm() && rt) rows.push({ swatch: 'ring', label: this.isAbstention() ? `${hoursText(this.answer) ?? ''} estimated drive`.trim() : rt });
    }
    if (this.geo && this.mode !== 'empty') {
      rows.push({ swatch: 'line', label: 'State border' });
      rows.push({ swatch: 'dots', label: 'Small dots: post offices, where people live' });
    }
    rows.push(...this.extraLegend);
    this.legendEl.innerHTML = rows.map((r) => `<li><span class="vfm-sw" aria-hidden="true">${swatchSvg(r)}</span><span>${escapeHtml(r.label)}</span></li>`).join('');
  }

  private tooltip(info: PickingInfo): { text: string; className: string } | null {
    const id = info.layer?.id;
    const obj = info.object as unknown;
    if (!obj) return null;
    if (id === 'pins') {
      const p = obj as Pin;
      const bits = [p.num ? `${p.num}. ${p.name}` : p.name];
      if (p.time) bits.push(p.time);
      if (p.approx) bits.push('position approximate');
      return { text: bits.join(', '), className: 'vfm-tip' };
    }
    if (id === 'diamonds') {
      const d = obj as Diamond;
      return { text: `${d.letter ? d.letter + '. ' : ''}${d.name}: nearer, not recommended`, className: 'vfm-tip' };
    }
    if (id === 'markers') return { text: (obj as Marker).label, className: 'vfm-tip' };
    return null;
  }

  private click(info: PickingInfo): void {
    if (this.clickHandler) {
      if (info.coordinate) this.clickHandler([info.coordinate[0], info.coordinate[1]], info.object ?? null);
      return;
    }
    const id = info.layer?.id, obj = info.object as unknown;
    if (!obj) return;
    if (id === 'pins') { const p = obj as Pin; this.onPick(p.kind, p.key); }
    else if (id === 'diamonds') this.onPick('whynot', (obj as Diamond).letter);
    else if (id === 'markers') this.markerPick?.((obj as Marker).i);
  }

  private buildOverlay(): void {
    if (this.overlayEl) return;
    const ov = el('div', 'vfm-overlay');
    ov.setAttribute('role', 'dialog');
    ov.setAttribute('aria-modal', 'true');
    ov.setAttribute('aria-labelledby', 'vfm-title');
    ov.style.display = 'none';
    const top = el('div', 'vfm-top');
    this.topTitle = el('h2');
    this.topTitle.id = 'vfm-title';
    this.topTitle.tabIndex = -1;
    const actions = el('div', 'vfm-top-actions');
    const india = el('button', 'vfm-btn', 'Show all of India'); india.type = 'button';
    india.onclick = () => this.showIndia();
    this.zoomPlaceBtn = el('button', 'vfm-btn', 'Zoom to place'); this.zoomPlaceBtn.type = 'button';
    this.zoomPlaceBtn.onclick = () => this.zoomToPlace();
    const close = el('button', 'vfm-btn vfm-close', 'Close map'); close.type = 'button';
    close.onclick = () => this.collapse();
    actions.append(india, this.zoomPlaceBtn, close);
    top.append(this.topTitle, actions);
    this.body = el('div', 'vfm-body');
    this.mapArea = el('div', 'vfm-map');
    this.legendFloat = el('div', 'vfm-legend-float');
    const key = el('button', 'vfm-btn vfm-key-btn', 'Map key'); key.type = 'button';
    key.onclick = () => {
      const open = this.legendFloat!.classList.toggle('open');
      key.textContent = open ? 'Hide map key' : 'Map key';
    };
    this.legendFloat.appendChild(key);
    this.mapArea.appendChild(this.legendFloat);
    this.railKey = el('details', 'vfm-railkey') as HTMLDetailsElement;
    this.railKey.open = true;
    const ks = el('summary', undefined, 'Map key: what the marks mean');
    this.railKey.appendChild(ks);
    this.rail = el('div', 'vfm-rail');
    this.body.append(this.mapArea, this.rail);
    ov.append(top, this.body);
    document.body.appendChild(ov);
    this.overlayEl = ov;
    this.topbar = top;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}
