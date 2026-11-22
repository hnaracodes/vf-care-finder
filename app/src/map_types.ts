// Input shapes the map reads. They are structural subsets of the API types in types.ts
// (app/design/SPEC.md section 3.4), so an Answer or SimPayload from types.ts can be passed
// straight in, and the map keeps working while the server still sends the older answer
// (no window, no origin, no why_not). Every field the map does not strictly need is optional.

export interface MapTime { short: string; range?: string; loose?: boolean }

export interface MapCard {
  facility_id?: string;
  name: string;
  name_short?: string | null;
  lat: number | null;
  lon: number | null;
  distance_km?: number | null;
  minutes?: number | null;
  time?: MapTime | null;
  position_approximate?: boolean | null;
}

export interface MapLocation {
  label?: string;
  lat: number | null;
  lon: number | null;
  precision_km?: number | null;
  kind?: string | null;
  district?: string | null;
  state?: string | null;
}

export interface MapWindow {
  minutes?: number;
  hours_text?: string;
  km_equivalent?: number;
  origin_loose?: boolean;
}

export interface MapOrigin {
  short_name?: string;
  label?: string;
  precision_km?: number | null;
  loose?: boolean;
  too_coarse?: boolean;
  state_display?: string;
}

export interface MapWhyNotItem {
  letter: string;
  facility_id?: string;
  name: string;
  lat: number | null;
  lon: number | null;
  position_approximate?: boolean;
}

export interface MapAnswer {
  status: string;
  service?: string;
  service_label?: string;
  radius_km?: number;
  location?: MapLocation | null;
  results?: MapCard[];
  beyond_radius?: MapCard | null;
  further_away?: MapCard[] | null;
  unverified_nearby?: MapCard[] | null;
  window?: MapWindow | null;
  origin?: MapOrigin | null;
  why_not?: { items: MapWhyNotItem[] } | null;
}

export interface MapSimLabel { name: string; kind: string; lat: number; lon: number; prio: number }

export interface MapSim {
  status: string;
  service_phrase?: string;
  origin?: { short_name?: string; lat?: number; lon?: number };
  window?: { hours_text?: string; km_equivalent?: number };
  context?: { labels: MapSimLabel[] } | null;
}

export interface MapCandidate { label: string; lat?: number | null; lon?: number | null; precision_km?: number | null }

export interface MapExample { label: string; lat: number; lon: number }

export interface ViewState {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch?: number;
  bearing?: number;
}

/** [[west, south], [east, north]] in degrees. */
export type Bounds = [[number, number], [number, number]];

/**
 * A legend row. `swatch` is a symbol keyword or any CSS colour (drawn as a dot).
 * Keywords: 'pin', 'hollow', 'diamond', 'dot', 'ring', 'line', 'dashed', 'plus', 'dots'.
 * `color` tints the keyword symbols (defaults per keyword).
 */
export interface LegendRow { swatch: string; label: string; color?: string }

export type PickKind = 'result' | 'whynot' | 'further';

/** What expand() hands back: slots inside the full-screen map. */
export interface ExpandedSlots {
  /** Right rail (400px on laptop), empty unless expand({rail: true}). On phone it is the sheet. */
  rail: HTMLElement;
  /** Bottom sheet on phone (under the 46vh map); the same element as rail on phone. */
  sheet: HTMLElement;
  /** The map area itself (position: relative). Append on-map bars here (sim bar, takeaway). */
  mapEl: HTMLElement;
  /** The top bar; its `.vfm-top-actions` child takes extra buttons. */
  topbar: HTMLElement;
  /** With a rail: the map key (a <details>), docked in the rail instead of floating over the map. The
   *  rail's owner may move it to where it reads best; the map takes it back on collapse. */
  key?: HTMLElement;
}
