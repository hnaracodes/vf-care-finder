// A small stand-in for B's MapHost, for the standalone dev page only
// (app/sim-dev.html, not shipped). It implements SimMapHost with one Deck:
// paper background, the village points, the 50 km ring, the searched place
// and the context labels from the payload. No tiles (D009).

import { Deck, MapView, WebMercatorViewport } from '@deck.gl/core';
import type { Layer, PickingInfo } from '@deck.gl/core';
import { PathLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import type { SimPayload } from './contract';
import type { ExpandSlots, SimMapHost } from './host';
import { h, swatchEl } from './panel';
import { C } from './tokens';

interface VS { longitude: number; latitude: number; zoom: number; pitch: number; bearing: number; transitionDuration?: number }

export class DevMapHost implements SimMapHost {
  readonly deck: Deck<MapView>;
  private slots: ExpandSlots;
  private base: Layer[] = [];
  private labels: Layer[] = [];
  private overlay: Layer[] = [];
  private legend: HTMLElement;
  private bannerSlot: HTMLElement;
  private click: ((ll: [number, number], picked: unknown) => void) | null = null;
  private cursor: 'default' | 'crosshair' = 'default';
  private vs: VS = { longitude: 80.9, latitude: 26.8, zoom: 8, pitch: 0, bearing: 0 };

  constructor(root: HTMLElement, phone: boolean) {
    root.classList.add('dev-root');
    if (phone) root.classList.add('dev-phone');
    const topbar = h('div', { class: 'dev-topbar' });
    this.bannerSlot = h('div', { class: 'dev-banner' });
    const mapEl = h('div', { class: 'dev-map' });
    this.legend = h('div', { class: 'dev-legend' });
    const rail = h('aside', { class: 'dev-rail' });
    const sheet = h('div', { class: 'dev-sheet' });
    const mapWrap = h('div', { class: 'dev-mapwrap' }, topbar, this.bannerSlot, mapEl);
    mapEl.append(this.legend);
    root.append(mapWrap, phone ? sheet : rail);
    this.slots = { rail, sheet, mapEl, topbar };
    const canvasHost = h('div', { class: 'dev-canvas' });
    mapEl.prepend(canvasHost);
    this.deck = new Deck<MapView>({
      parent: canvasHost as HTMLDivElement,
      views: new MapView({ repeat: false }),
      initialViewState: this.vs,
      controller: { scrollZoom: true, dragRotate: false, doubleClickZoom: true, keyboard: true },
      onViewStateChange: ({ viewState }) => { this.vs = viewState as VS; return viewState; },
      getCursor: ({ isDragging }) => (isDragging ? 'grabbing' : this.cursor === 'crosshair' ? 'crosshair' : 'grab'),
      onClick: (info: PickingInfo) => {
        if (this.click && info.coordinate) this.click([info.coordinate[0], info.coordinate[1]], info.object ?? null);
      },
      useDevicePixels: true,
      layers: [],
    });
  }

  showPayload(p: SimPayload): void {
    const { lat, lon } = p.origin;
    const r = p.villages.radius_km;
    const ring: [number, number][] = [];
    for (let k = 0; k <= 96; k++) {
      const a = (k / 96) * Math.PI * 2;
      ring.push([lon + (r / (111.2 * Math.cos((lat * Math.PI) / 180))) * Math.cos(a), lat + (r / 111.2) * Math.sin(a)]);
    }
    const vil = p.villages.lon.map((x, i) => [x, p.villages.lat[i]] as [number, number]);
    this.base = [
      new ScatterplotLayer<[number, number]>({ id: 'dev-villages', data: vil, getPosition: (d) => d, radiusUnits: 'pixels', getRadius: 1.6, getFillColor: [140, 132, 120, 110] }),
      new PathLayer<[number, number][]>({ id: 'dev-ring', data: [ring], getPath: (d) => d, widthUnits: 'pixels', getWidth: 1, getColor: [140, 132, 120, 160] }),
      new ScatterplotLayer({ id: 'dev-origin', data: [p.origin], getPosition: (d: SimPayload['origin']) => [d.lon, d.lat], radiusUnits: 'pixels', getRadius: 6, getFillColor: C.origin, stroked: true, getLineColor: [255, 255, 255, 255], lineWidthUnits: 'pixels', getLineWidth: 2 }),
    ];
    const labels = (p.context?.labels ?? []).filter((l) => l.prio <= 2 || l.kind === 'post_office').slice(0, 80);
    this.labels = [
      new TextLayer({
        id: 'dev-labels', data: labels, getPosition: (d: (typeof labels)[number]) => [d.lon, d.lat], getText: (d: (typeof labels)[number]) => d.name,
        getSize: (d: (typeof labels)[number]) => (d.prio === 1 ? 14 : d.prio === 2 ? 12 : 10), getColor: (d: (typeof labels)[number]) => (d.prio === 3 ? [110, 100, 88, 200] : [43, 38, 32, 230]),
        sizeUnits: 'pixels', characterSet: 'auto', fontFamily: 'system-ui, sans-serif', fontWeight: 600, outlineWidth: 3,
        outlineColor: [245, 240, 230, 255], fontSettings: { sdf: true }, getPixelOffset: [0, -10],
      }),
      new TextLayer({
        id: 'dev-origin-label', data: [p.origin], getPosition: (d: SimPayload['origin']) => [d.lon, d.lat], getText: (d: SimPayload['origin']) => d.short_name,
        getSize: 15, getColor: C.origin, sizeUnits: 'pixels', characterSet: 'auto', fontFamily: 'system-ui, sans-serif', fontWeight: 800,
        outlineWidth: 3, outlineColor: [245, 240, 230, 255], fontSettings: { sdf: true }, getPixelOffset: [0, 14],
      }),
    ];
    this.redraw();
  }

  private redraw(): void {
    this.deck.setProps({ layers: [...this.base, ...this.overlay, ...this.labels] });
  }

  expand(): ExpandSlots { return this.slots; }
  collapse(): void { /* always expanded on the dev page */ }
  isExpanded(): boolean { return false; }

  fit(bounds: [[number, number], [number, number]], padPx = 40, ms = 0): void {
    const el = this.slots.mapEl;
    const vp = new WebMercatorViewport({ width: Math.max(200, el.clientWidth), height: Math.max(200, el.clientHeight) });
    const bar = el.querySelector('.sim-bar') as HTMLElement | null;
    const take = el.querySelector('.sim-takeaway') as HTMLElement | null;
    const pad = { top: padPx + (bar?.offsetHeight ?? 0), bottom: padPx + (take?.offsetHeight ?? 0), left: padPx, right: padPx };
    const f = vp.fitBounds(bounds, { padding: pad });
    this.vs = { longitude: f.longitude, latitude: f.latitude, zoom: f.zoom, pitch: 0, bearing: 0, transitionDuration: ms };
    this.deck.setProps({ initialViewState: { ...this.vs } });
  }

  setOverlayLayers(layers: Layer[]): void { this.overlay = layers; this.redraw(); }

  setExtraLegend(rows: { swatch: string; label: string }[]): void {
    this.legend.replaceChildren(...rows.map((r) => h('div', { class: 'dev-leg-row' }, swatchEl(r.swatch), h('span', {}, r.label))));
    this.legend.hidden = rows.length === 0;
  }

  setBanner(el: HTMLElement | null): void {
    this.bannerSlot.replaceChildren(...(el ? [el] : []));
  }

  setMapClickHandler(fn: ((ll: [number, number], picked: unknown) => void) | null): void { this.click = fn; }
  setCursor(c: 'default' | 'crosshair'): void { this.cursor = c; this.deck.setProps({}); }
}
