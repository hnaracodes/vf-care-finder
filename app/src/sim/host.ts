// The part of B's MapHost (SPEC 5.4) the simulator uses. B's class satisfies
// it structurally; the standalone dev page (sim-dev.html) has its own small
// host in devmap.ts with the same methods.

import type { Deck, Layer } from '@deck.gl/core';

export interface ExpandSlots { rail: HTMLElement; sheet: HTMLElement; mapEl: HTMLElement; topbar: HTMLElement; key?: HTMLElement }

export interface SimMapHost {
  expand(opts?: { rail?: boolean }): ExpandSlots;
  collapse(): void;
  isExpanded(): boolean;
  fit(bounds: [[number, number], [number, number]], padPx?: number, ms?: number): void;
  setOverlayLayers(layers: Layer[]): void;
  /** optional: names drawn through the map's label declutter (the counts at each place) */
  setOverlayLabels?(labels: { text: string; lon: number; lat: number; style: 'ring' | 'claimnote' | 'note' | 'whatif' }[]): void;
  /** swatch: "<shape> <css colour>", shape one of dot, ring, dash, diamond, plus (see sim/panel.ts LEGEND) */
  setExtraLegend(rows: { swatch: string; label: string }[]): void;
  setBanner(el: HTMLElement | null): void;
  setMapClickHandler(fn: ((lngLat: [number, number], picked: unknown) => void) | null): void;
  setCursor(c: 'default' | 'crosshair'): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly deck: Deck<any>;
}
