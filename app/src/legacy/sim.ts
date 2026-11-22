// In-browser what-if simulator. Same coverage rule as WP7: a post office is
// covered for a line when a facility at grade >= k is within R km,
// straight-line, with distances stored as float32. One new or upgraded site
// means one pass over the post office array.

import type { AppData, Facility, LineKey } from './data';
import { DEFAULT_EARTH_R_KM, haversineKm, nearestAll } from '../geo';

export interface Site {
  facIndex: number;
  kind: 'upgrade' | 'new';
  lon: number;
  lat: number;
  prevGrade: number;
  newGrade: number;
}

export interface ApplyResult {
  site: Site;
  /** post offices within R km of the site (R = the radius passed to apply) */
  withinR: Int32Array;
  /** post offices whose distance at the simulator grade changed */
  changed: number;
}

export class Simulator {
  readonly data: AppData;
  readonly earthR: number;
  readonly upgradeTo: number;
  line: LineKey;
  /** index k = 0..3; k = 0 is "any facility" */
  dist: Float32Array[] = [];
  near: Uint16Array[] = [];
  sites: Site[] = [];
  extra: Facility[] = [];
  upgraded = new Map<number, number>();
  version = 0;
  private anyDist: Float32Array;
  private anyNear: Uint16Array;

  constructor(data: AppData, line: LineKey) {
    this.data = data;
    this.earthR = data.meta.simulator?.earth_radius_km ?? data.scenarios?.earth_radius_km ?? DEFAULT_EARTH_R_KM;
    this.upgradeTo = data.meta.simulator?.upgrade_to_grade ?? data.scenarios?.upgrade_to_grade ?? 3;
    const pts = data.facilities.map((f) =>
      f.lat != null && f.lon != null ? { lat: f.lat, lon: f.lon } : null,
    );
    const any = nearestAll(data.lat, data.lon, pts, data.noIndex, this.earthR);
    this.anyDist = any.dist;
    this.anyNear = any.near;
    this.line = line;
    this.reset();
  }

  setLine(line: LineKey): void {
    this.line = line;
    this.reset();
  }

  reset(): void {
    const d = this.data;
    this.dist = [this.anyDist.slice()];
    this.near = [this.anyNear.slice()];
    for (let k = 1; k <= 3; k++) {
      const dk = d.dist[`${this.line}_g${k}`];
      const nk = d.near[`${this.line}_g${k}`];
      this.dist.push(dk ? dk.slice() : new Float32Array(d.n).fill(Infinity));
      this.near.push(nk ? nk.slice() : new Uint16Array(d.n).fill(d.noIndex));
    }
    this.sites = [];
    this.extra = [];
    this.upgraded.clear();
    this.version++;
  }

  facCount(): number {
    return this.data.facilities.length + this.extra.length;
  }

  facility(i: number): Facility | null {
    const n0 = this.data.facilities.length;
    if (i < n0) return this.data.facilities[i] ?? null;
    return this.extra[i - n0] ?? null;
  }

  /** Grade of facility i for the current line, including simulated upgrades. */
  grade(i: number): number {
    const up = this.upgraded.get(i);
    if (up !== undefined) return up;
    const f = this.facility(i);
    return f?.grades?.[this.line] ?? 0;
  }

  isSimulated(i: number): boolean {
    return this.upgraded.has(i) || i >= this.data.facilities.length;
  }

  upgrade(facIndex: number, R: number): ApplyResult | null {
    const f = this.facility(facIndex);
    if (!f || f.lat == null || f.lon == null) return null;
    const prev = this.grade(facIndex);
    if (prev >= this.upgradeTo) return null;
    this.upgraded.set(facIndex, this.upgradeTo);
    const site: Site = { facIndex, kind: 'upgrade', lon: f.lon, lat: f.lat, prevGrade: prev, newGrade: this.upgradeTo };
    return this.applySite(site, R);
  }

  addSite(lon: number, lat: number, R: number): ApplyResult {
    const idx = this.facCount();
    const label = `New site ${this.extra.length + 1} (simulated)`;
    const grades: Partial<Record<LineKey, number>> = { [this.line]: this.upgradeTo };
    this.extra.push({ id: `sim-new-${this.extra.length + 1}`, name: label, lat, lon, grades, n_domains: 0 });
    this.upgraded.set(idx, this.upgradeTo);
    const site: Site = { facIndex: idx, kind: 'new', lon, lat, prevGrade: -1, newGrade: this.upgradeTo };
    return this.applySite(site, R);
  }

  private applySite(site: Site, R: number): ApplyResult {
    const { lat, lon, n } = this.data;
    const kMin = site.kind === 'new' ? 0 : Math.max(1, site.prevGrade + 1);
    const within: number[] = [];
    let changed = 0;
    const Rf = Math.fround(R);
    for (let i = 0; i < n; i++) {
      const d = Math.fround(haversineKm(lat[i], lon[i], site.lat, site.lon, this.earthR));
      if (d <= Rf) within.push(i);
      for (let k = kMin; k <= site.newGrade; k++) {
        if (d < this.dist[k][i]) {
          this.dist[k][i] = d;
          this.near[k][i] = site.facIndex;
          if (k === site.newGrade) changed++;
        }
      }
    }
    this.sites.push(site);
    this.version++;
    return { site, withinR: Int32Array.from(within), changed };
  }

  /** Number of post offices with a grade >= k facility within R km. */
  covered(k: number, R: number): number {
    const d = this.dist[k];
    const Rf = Math.fround(R);
    let c = 0;
    for (let i = 0; i < d.length; i++) if (d[i] <= Rf) c++;
    return c;
  }
}
