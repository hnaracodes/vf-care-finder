// The simulator's world (SPEC 7.2): villages, facilities, the V x F km and
// minute matrices, and the what-if edits. Matrices are facility-major
// (KM[j * V + v]) so a what-if facility appends one column without moving the
// others. Float64 so the browser's counters match the server's numpy ones.

import type { SimFacility, SimLabel, SimPayload } from './contract';

export const R_EARTH_KM = 6371.0088;
const DEG = Math.PI / 180;

/** Same operation order as vf.geo.haversine_km (numpy): radians first, then differences. */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = lat1 * DEG, p2 = lat2 * DEG, l1 = lon1 * DEG, l2 = lon2 * DEG;
  const s1 = Math.sin((p2 - p1) / 2), s2 = Math.sin((l2 - l1) / 2);
  const a = s1 * s1 + Math.cos(p1) * Math.cos(p2) * s2 * s2;
  return 2 * R_EARTH_KM * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
}

export const ST_EVIDENCE = 0;
export const ST_CLAIM_ONLY = 1;
export const ST_HELD_BACK = 2;
const STATUS_CODE: Record<string, number> = { evidence: 0, claim_only: 1, held_back: 2 };

export type Edit =
  | { type: 'verify'; facility_id: string; id: number }
  | { type: 'close'; facility_id: string; id: number }
  | { type: 'add'; lon: number; lat: number; id: number; facility_id?: string };

export type EditInput =
  | { type: 'verify'; facility_id: string }
  | { type: 'close'; facility_id: string }
  | { type: 'add'; lon: number; lat: number };

export interface FacMeta {
  facility_id: string;
  name: string;
  km_from_origin: number;
  approximate: boolean;
  card_rank: number | null;
  why_not_letter: string | null;
  grade: number;
  /** for a what-if facility: the edit that made it */
  addId: number | null;
  /** for a what-if facility: the nearest place name from context.labels */
  near: string | null;
}

export class World {
  readonly p: SimPayload;
  readonly V: number;
  readonly vlon: Float64Array;
  readonly vlat: Float64Array;
  readonly vfac: Float64Array;
  readonly pin: string[];
  readonly a: number;
  readonly b: number;
  readonly W: number;
  readonly A: number;
  readonly nReal: number;

  /** facilities, real ones first, then every what-if facility ever added (inactive when undone) */
  F = 0;
  private cap = 0;
  flon = new Float64Array(0);
  flat = new Float64Array(0);
  status = new Uint8Array(0);
  strong = new Uint8Array(0);
  open = new Uint8Array(0);
  verified = new Uint8Array(0);
  added = new Uint8Array(0);
  meta: FacMeta[] = [];
  KM = new Float64Array(0);
  MIN = new Float64Array(0);

  edits: Edit[] = [];
  version = 0;
  private nextId = 1;
  private idToJ = new Map<string, number>();
  private addCol = new Map<number, number>();

  constructor(p: SimPayload) {
    this.p = p;
    const vs = p.villages;
    this.V = vs.lon.length;
    this.vlon = Float64Array.from(vs.lon);
    this.vlat = Float64Array.from(vs.lat);
    this.vfac = Float64Array.from(vs.factor.map((f) => (f == null || !isFinite(f) ? p.origin.factor : f)));
    this.pin = vs.pin.slice();
    this.a = p.travel.a;
    this.b = p.travel.b;
    this.W = p.window.minutes;
    this.A = p.window.allowance;
    this.nReal = p.facilities.length;
    this.grow(Math.max(8, this.nReal + 8));
    for (const f of p.facilities) this.push(f.lon, f.lat, STATUS_CODE[f.status] ?? ST_CLAIM_ONLY, f.strong ? 1 : 0, metaOf(f));
    p.facilities.forEach((f, j) => this.idToJ.set(f.facility_id, j));
  }

  // ------------------------------------------------------------------ storage

  private grow(cap: number): void {
    if (cap <= this.cap) return;
    const V = this.V;
    const g8 = (old: Uint8Array) => { const n = new Uint8Array(cap); n.set(old); return n; };
    const g64 = (old: Float64Array, len: number) => { const n = new Float64Array(len); n.set(old); return n; };
    this.flon = g64(this.flon, cap);
    this.flat = g64(this.flat, cap);
    this.status = g8(this.status);
    this.strong = g8(this.strong);
    this.open = g8(this.open);
    this.verified = g8(this.verified);
    this.added = g8(this.added);
    this.KM = g64(this.KM, cap * V);
    this.MIN = g64(this.MIN, cap * V);
    this.cap = cap;
  }

  private push(lon: number, lat: number, status: number, strong: number, meta: FacMeta): number {
    if (this.F >= this.cap) this.grow(this.cap * 2);
    const j = this.F++;
    this.flon[j] = lon;
    this.flat[j] = lat;
    this.status[j] = status;
    this.strong[j] = strong;
    this.open[j] = 1;
    this.meta[j] = meta;
    const V = this.V, a = this.a, b = this.b, base = j * V;
    for (let v = 0; v < V; v++) {
      const km = haversineKm(this.vlat[v], this.vlon[v], lat, lon);
      this.KM[base + v] = km;
      this.MIN[base + v] = this.vfac[v] * a * Math.pow(km, b);
    }
    return j;
  }

  /** Temporarily append a facility column (previewAdd); pop it with popTemp. Does not bump version. */
  pushTemp(lon: number, lat: number): number {
    const j = this.push(lon, lat, ST_EVIDENCE, 0, { ...blankMeta(), name: 'preview' });
    this.added[j] = 1;
    return j;
  }

  popTemp(j: number): void {
    if (j !== this.F - 1) throw new Error('popTemp: not the last column');
    this.F--;
    this.open[j] = this.added[j] = this.verified[j] = 0;
    this.meta.length = this.F;
  }

  // ------------------------------------------------------------------ queries

  /** Effective evidence: open and (evidence in the record, or verified or added in the what-if). */
  ev(j: number): boolean {
    return this.open[j] === 1 && (this.status[j] === ST_EVIDENCE || this.verified[j] === 1 || this.added[j] === 1);
  }

  /** A strong record gets the allowance; verified and added ones never do. */
  isStrong(j: number): boolean {
    return this.strong[j] === 1 && this.status[j] === ST_EVIDENCE;
  }

  isAdded(j: number): boolean {
    return j >= this.nReal;
  }

  /** Minutes from a point with terrain factor f to facility j. */
  minutesFrom(lon: number, lat: number, f: number, j: number): number {
    return f * this.a * Math.pow(haversineKm(lat, lon, this.flat[j], this.flon[j]), this.b);
  }

  minutesForKm(km: number, f: number): number {
    return f * this.a * Math.pow(km, this.b);
  }

  indexOf(facility_id: string): number {
    return this.idToJ.get(facility_id) ?? -1;
  }

  nearestLabel(lon: number, lat: number): string | null {
    let best: SimLabel | null = null, bd = Infinity;
    for (const l of this.p.context?.labels ?? []) {
      const d = haversineKm(lat, lon, l.lat, l.lon);
      if (d < bd) { bd = d; best = l; }
    }
    return best ? best.name : null;
  }

  // ------------------------------------------------------------------ edits

  /** Apply one edit; returns it with its id. Bumps version. */
  apply(e: EditInput): Edit {
    const id = this.nextId++;
    let edit: Edit;
    if (e.type === 'add') {
      const n = 1 + this.edits.filter((x) => x.type === 'add').length;
      const near = this.nearestLabel(e.lon, e.lat);
      const fid = `whatif-${id}`;
      const j = this.push(e.lon, e.lat, ST_EVIDENCE, 0, {
        ...blankMeta(), facility_id: fid, name: `What-if unit ${n}`, addId: id, near,
        km_from_origin: round1(haversineKm(this.p.origin.lat, this.p.origin.lon, e.lat, e.lon)),
      });
      this.idToJ.set(fid, j);
      this.addCol.set(id, j);
      edit = { type: 'add', lon: e.lon, lat: e.lat, id, facility_id: fid };
    } else {
      edit = { ...e, id };
    }
    this.edits.push(edit);
    this.rebuildFlags();
    return edit;
  }

  /** Remove one edit by id (Undo on its row). */
  remove(id: number): void {
    this.edits = this.edits.filter((e) => e.id !== id);
    this.rebuildFlags();
  }

  undoLast(): void {
    if (!this.edits.length) return;
    this.edits = this.edits.slice(0, -1);
    this.rebuildFlags();
  }

  reset(): void {
    this.edits = [];
    this.rebuildFlags();
  }

  /** Replace the edit list (seek replays a snapshot). Added columns are reused by edit id. */
  setEdits(edits: Edit[]): void {
    for (const e of edits) {
      if (e.type === 'add' && !this.addCol.has(e.id)) {
        // a snapshot from another World instance: recreate the column
        const j = this.push(e.lon, e.lat, ST_EVIDENCE, 0, {
          ...blankMeta(), facility_id: e.facility_id ?? `whatif-${e.id}`, name: `What-if unit ${this.addCol.size + 1}`,
          addId: e.id, near: this.nearestLabel(e.lon, e.lat),
        });
        this.addCol.set(e.id, j);
        this.idToJ.set(this.meta[j].facility_id, j);
        this.nextId = Math.max(this.nextId, e.id + 1);
      }
    }
    this.edits = edits.slice();
    this.rebuildFlags();
  }

  private rebuildFlags(): void {
    for (let j = 0; j < this.F; j++) {
      this.open[j] = j < this.nReal ? 1 : 0;
      this.verified[j] = 0;
      this.added[j] = 0;
    }
    for (const e of this.edits) {
      if (e.type === 'add') {
        const j = this.addCol.get(e.id);
        if (j !== undefined) { this.open[j] = 1; this.added[j] = 1; }
      } else {
        const j = this.indexOf(e.facility_id);
        if (j < 0) continue;
        if (e.type === 'verify') this.verified[j] = 1;
        else this.open[j] = 0;
      }
    }
    this.version++;
  }

  /** The display line of an edit. */
  editLabel(e: Edit): string {
    if (e.type === 'add') {
      const j = this.addCol.get(e.id);
      const near = j !== undefined ? this.meta[j].near : null;
      return `What-if: new ${this.p.service_phrase} unit${near ? ` near ${near}` : ''}`;
    }
    const j = this.indexOf(e.facility_id);
    const name = j >= 0 ? this.meta[j].name : 'a record';
    return e.type === 'verify' ? `What-if: verified ${name}` : `What-if: closed ${name}`;
  }
}

function blankMeta(): FacMeta {
  return { facility_id: '', name: '', km_from_origin: 0, approximate: false, card_rank: null, why_not_letter: null,
    grade: 0, addId: null, near: null };
}

function metaOf(f: SimFacility): FacMeta {
  return { facility_id: f.facility_id, name: f.name, km_from_origin: f.km_from_origin, approximate: f.approximate,
    card_rank: f.card_rank, why_not_letter: f.why_not_letter, grade: f.grade, addId: null, near: null };
}

export function round1(x: number): number {
  return Math.round(x * 10) / 10;
}
