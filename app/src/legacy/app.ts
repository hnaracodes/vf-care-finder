import { Deck, MapView } from '@deck.gl/core';
import type { PickingInfo } from '@deck.gl/core';
import { LineLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';

import type { AppData, Camera, LineKey, ScenarioSetting, ScenarioStep } from './data';
import {
  ACCENT, DIM_PO, DIST_COLORS, GRADE_COLORS, GRADE_RADIUS_PX, QUADRANT_COLORS, distClass, normQuadrant, rgbaCss,
} from '../colors';
import { Simulator } from './sim';
import { Swarm } from './swarm';
import { SwarmLayer } from '../swarm-layer';
import {
  districtHTML, drawerHTML, el, esc, facilityHTML, fmtInt, fmtKm, fmtPct, gradeLabel, legendHTML, limitsHTML,
  lineLabel, listsHTML, patientHTML, type NearRow,
} from './ui';

export type View = 'distance' | 'quadrant' | 'swarm';
export type Plan = 'naive' | 'desert_aware';

export const INDIA: Camera = { longitude: 80.5, latitude: 22.6, zoom: 4.1 };
const FULL_SWARM_ZOOM = 6.5;

interface ViewState extends Camera {
  pitch: number;
  bearing: number;
  [k: string]: unknown;
}

/**
 * App clock in seconds. 'real' follows wall time. 'quantized' (story
 * autoplay) advances in whole 1/60 s steps, so every animation is a function
 * of the step count and a take can be redone frame for frame.
 */
export class Clock {
  t = 0;
  fixed = false;
  paused = false;
  private n = 0;
  private acc = 0;
  private last = -1;
  tick(nowMs: number): void {
    const dt = this.last < 0 ? 0 : Math.min(0.1, (nowMs - this.last) / 1000);
    this.last = nowMs;
    if (this.paused) return;
    if (!this.fixed) {
      this.t += dt;
      return;
    }
    this.acc += dt;
    const steps = Math.floor(this.acc * 60 + 1e-6);
    this.acc -= steps / 60;
    this.step(steps);
  }
  /** Advance by whole 1/60 s steps (quantized mode). */
  step(k = 1): void {
    if (this.n === 0 && this.t > 0) this.n = Math.ceil(this.t * 60);
    this.n += k;
    this.t = this.n / 60;
  }
  setFixed(on: boolean): void {
    this.fixed = on;
    this.acc = 0;
    this.n = Math.ceil(this.t * 60);
    if (on) this.t = this.n / 60;
  }
}

interface PlayedPoint {
  plan: Plan;
  step: number;
  browser: number;
  file: number;
  match: boolean;
}

interface Playback {
  setting: ScenarioSetting;
  plans: Plan[];
  planIdx: number;
  step: number;
  steps: number;
  stepDur: number;
  nextAt: number;
}

export class App {
  readonly data: AppData;
  readonly clock = new Clock();
  readonly sim: Simulator;
  readonly swarm: Swarm;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  deck!: Deck<any>;
  line: LineKey;
  grade = 2;
  R: number;
  view: View = 'distance';
  quadMode: 'model' | 'rule' = 'model';
  highlight: string | null = null;
  patientPo: number | null = null;
  selectedFac: number | null = null;
  selectedDistrict: number | null = null;
  pulse: { lon: number; lat: number } | null = null;
  plan: Plan = 'naive';
  showSwarm = true;
  transitions = true;
  playback: Playback | null = null;
  played: PlayedPoint[] = [];
  prevShare: { grade: number; share: number } | null = null;
  storyActive = false;
  viewState: ViewState;
  private tween: { from: ViewState; to: ViewState; t0: number; dur: number } | null = null;
  private dirty = true;
  private poColors: Uint8Array;
  private poPosDesc: { value: Float32Array; size: number };
  private poData: { length: number; attributes: Record<string, unknown> };
  private facVisible: number[] = [];
  private likelyByDistrict = new Uint8Array(0);
  private frames: number[] = [];
  private swarmEnd = 0;

  constructor(data: AppData) {
    this.data = data;
    this.line = data.lines[0]?.key ?? 'emoc';
    this.R = data.meta.radii_km?.[0] ?? 30;
    this.grade = data.meta.simulator?.min_grade ?? 2;
    this.sim = new Simulator(data, this.line);
    this.swarm = new Swarm(data, 5);
    const pos = new Float32Array(data.n * 2);
    for (let i = 0; i < data.n; i++) {
      pos[i * 2] = data.lon[i];
      pos[i * 2 + 1] = data.lat[i];
    }
    this.poPosDesc = { value: pos, size: 2 };
    this.poColors = new Uint8Array(data.n * 4);
    this.poData = { length: data.n, attributes: { getPosition: this.poPosDesc } };
    this.viewState = { ...INDIA, pitch: 0, bearing: 0 };
  }

  mount(parent: HTMLDivElement): void {
    this.deck = new Deck({
      parent,
      views: new MapView({ repeat: false }),
      viewState: this.viewState,
      controller: { dragRotate: false, touchRotate: false, doubleClickZoom: true },
      onViewStateChange: ({ viewState, interactionState }) => {
        const vs = viewState as unknown as ViewState;
        if (this.tween && (interactionState?.isDragging || interactionState?.isZooming || interactionState?.isPanning)) {
          this.tween = null;
        }
        this.viewState = vs;
        this.dirty = true;
      },
      onClick: (info) => this.onClick(info),
      getCursor: ({ isDragging, isHovering }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : this.view === 'swarm' ? 'crosshair' : 'grab'),
      layers: [],
    });
    this.recomputeLikely();
    this.recolor();
    this.refreshFacilities();
    this.renderUI();
    const loop = (now: number) => {
      this.frame(now);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------------ state

  setLine(line: LineKey): void {
    if (line === this.line) return;
    this.stopPlayback();
    this.line = line;
    this.sim.setLine(line);
    this.patientPo = null;
    this.selectedFac = null;
    this.prevShare = null;
    this.recomputeLikely();
    this.afterSimChange(true);
  }

  setGrade(k: number): void {
    k = Math.max(0, Math.min(3, Math.round(k)));
    if (k === this.grade) return;
    this.prevShare = { grade: this.grade, share: this.share() };
    this.grade = k;
    this.afterSimChange(true);
  }

  setRadius(R: number): void {
    if (R === this.R) return;
    this.stopPlayback();
    this.R = R;
    this.prevShare = null;
    this.afterSimChange(true);
  }

  setView(v: View): void {
    if (v === this.view) return;
    this.view = v;
    if (v !== 'quadrant') this.highlight = null;
    if (v === 'swarm') this.releaseSwarm();
    else this.stopPlayback();
    this.selectedDistrict = null;
    this.selectedFac = null;
    this.recolor();
    this.refreshFacilities();
    this.renderUI();
  }

  setHighlight(q: string | null): void {
    this.highlight = q;
    this.recolor();
  }

  share(): number {
    return this.sim.covered(this.grade, this.R) / this.data.n;
  }

  private recomputeLikely(): void {
    const ds = this.data.districts;
    this.likelyByDistrict = new Uint8Array(ds.length);
    ds.forEach((d, i) => {
      if (normQuadrant(d.quadrant?.[this.line]?.quadrant_model) === 'data_desert') this.likelyByDistrict[i] = 1;
    });
  }

  releaseSwarm(settled = false): void {
    this.swarm.run(this.sim, this.grade, this.R, this.likelyByDistrict, this.clock.t + 0.2, false, settled);
    this.swarmEnd = settled ? this.clock.t : this.swarm.endTime();
    this.dirty = true;
  }

  private afterSimChange(full: boolean): void {
    if (full && this.view === 'swarm') this.releaseSwarm();
    this.recolor();
    this.refreshFacilities();
    this.renderUI();
  }

  recolor(): void {
    const n = this.data.n;
    const c = this.poColors;
    if (this.view === 'distance') {
      const d = this.sim.dist[this.grade];
      const lut = DIST_COLORS;
      for (let i = 0; i < n; i++) c.set(lut[distClass(d[i])], i * 4);
    } else if (this.view === 'quadrant') {
      const ds = this.data.districts;
      const pal = ds.map((d) => {
        const q = d.quadrant?.[this.line];
        const k = normQuadrant(this.quadMode === 'rule' ? q?.quadrant_rule : q?.quadrant_model);
        const col = QUADRANT_COLORS[k];
        if (this.highlight && k !== this.highlight) return [col[0] * 0.22 + 8, col[1] * 0.22 + 10, col[2] * 0.22 + 14, 255];
        return col;
      });
      const none = QUADRANT_COLORS.unscored;
      for (let i = 0; i < n; i++) {
        const di = this.data.district[i];
        c.set(di === this.data.noIndex || !pal[di] ? none : pal[di], i * 4);
      }
    } else {
      for (let i = 0; i < n; i++) c.set(DIM_PO, i * 4);
    }
    // New descriptor object: deck re-uploads colours only, positions stay on the GPU.
    this.poData = { length: n, attributes: { getPosition: this.poPosDesc, getFillColor: { value: c, size: 4, normalized: true } } };
    this.dirty = true;
  }

  refreshFacilities(): void {
    // In the simulator, grade 1 (claimed) sites stay visible: they are the upgrade candidates.
    const k = this.view === 'swarm' ? Math.min(1, this.grade) : this.grade;
    const out: number[] = [];
    const nf = this.sim.facCount();
    for (let i = 0; i < nf; i++) {
      const f = this.sim.facility(i);
      if (!f || f.lat == null || f.lon == null) continue;
      if (this.sim.grade(i) >= k) out.push(i);
    }
    // draw higher grades on top
    out.sort((a, b) => this.sim.grade(a) - this.sim.grade(b));
    this.facVisible = out;
    this.dirty = true;
  }

  // ------------------------------------------------------------ simulator

  upgradeFacility(i: number): boolean {
    const res = this.sim.upgrade(i, this.R);
    if (!res) return false;
    this.onSiteApplied(res.withinR);
    return true;
  }

  addSite(lon: number, lat: number): void {
    const res = this.sim.addSite(lon, lat, this.R);
    this.onSiteApplied(res.withinR);
  }

  private onSiteApplied(withinR: Int32Array): void {
    if (this.view === 'swarm') {
      this.swarm.replay(this.sim, this.grade, this.R, this.likelyByDistrict, withinR, this.clock.t);
      this.swarmEnd = Math.max(this.swarmEnd, this.clock.t + 0.6 + this.R / this.swarm.speedKmS);
    }
    this.recolor();
    this.refreshFacilities();
    this.renderUI();
  }

  resetSim(): void {
    this.stopPlayback();
    this.sim.reset();
    this.played = [];
    this.afterSimChange(true);
  }

  scenarioSetting(): ScenarioSetting | null {
    return this.data.scenarios?.settings.find((s) => s.service_line === this.line && s.radius_km === this.R) ?? null;
  }

  private applyStep(e: ScenarioStep): boolean {
    if (e.kind === 'new') {
      if (e.lon == null || e.lat == null) return false;
      this.addSite(e.lon, e.lat);
      return true;
    }
    const idx = e.facility_id != null ? this.data.facIndexById.get(e.facility_id) : undefined;
    if (idx === undefined) return false;
    return this.upgradeFacility(idx);
  }

  startPlayback(plans: Plan[], steps = 10, stepDur = 1.2): void {
    const setting = this.scenarioSetting();
    if (!setting) return;
    this.stopPlayback();
    const mg = setting.min_grade ?? 2;
    const gradeChanged = this.grade !== mg;
    if (gradeChanged) {
      this.prevShare = null;
      this.grade = mg;
    }
    // Already released at baseline in this view: let the opening release finish, then pick.
    const fresh = !gradeChanged && this.view === 'swarm' && this.sim.sites.length === 0 && this.swarm.baseline !== null;
    const wasSwarm = this.view === 'swarm' && this.swarm.baseline !== null;
    if (this.view !== 'swarm') this.view = 'swarm';
    if (!fresh) {
      this.sim.reset();
      this.releaseSwarm(wasSwarm && !gradeChanged);
    }
    this.played = this.played.filter((p) => !plans.includes(p.plan));
    this.plan = plans[0];
    this.recolor();
    this.refreshFacilities();
    const t = this.clock.t;
    const firstAt = fresh ? Math.max(t + 1.2, this.swarmEnd + 0.5)
      : wasSwarm && !gradeChanged ? t + 1.2 : this.swarmEnd + 0.5;
    this.playback = {
      setting, plans, planIdx: 0, step: 0,
      steps: Math.min(steps, setting[plans[0]].length), stepDur,
      nextAt: firstAt,
    };
    this.renderUI();
  }

  stopPlayback(): void {
    if (!this.playback) return;
    this.playback = null;
    this.renderUI();
  }

  private updatePlayback(t: number): void {
    const p = this.playback;
    if (!p || t < p.nextAt) return;
    const plan = p.plans[p.planIdx];
    if (p.step < p.steps) {
      const e = p.setting[plan][p.step];
      const ok = this.applyStep(e);
      const covered = this.sim.covered(p.setting.min_grade ?? 2, p.setting.radius_km);
      const browser = covered / this.data.n;
      const match = ok && (e.covered != null ? e.covered === covered : Math.abs(browser - e.cumulative_coverage) < 1e-6 + 0.5 / this.data.n);
      if (!match) console.warn(`coverage mismatch at ${plan} step ${p.step + 1}: browser ${browser}, scenarios.json ${e.cumulative_coverage}`);
      this.played.push({ plan, step: p.step + 1, browser, file: e.cumulative_coverage, match });
      p.step++;
      p.nextAt = t + p.stepDur;
      this.renderUI();
      return;
    }
    if (p.planIdx + 1 < p.plans.length) {
      p.planIdx++;
      p.step = 0;
      const next = p.plans[p.planIdx];
      p.steps = Math.min(p.steps, p.setting[next].length);
      this.plan = next;
      this.sim.reset();
      this.releaseSwarm(true);
      this.recolor();
      this.refreshFacilities();
      p.nextAt = t + 1.2;
      this.renderUI();
      return;
    }
    this.playback = null;
    this.renderUI();
  }

  /** Replays every scenario in the browser without animation and compares with scenarios.json. */
  checkAllScenarios(): { setting: string; plan: string; steps: number; mismatches: number; worst: number }[] {
    const sc = this.data.scenarios;
    if (!sc) return [];
    const out: { setting: string; plan: string; steps: number; mismatches: number; worst: number }[] = [];
    const saveLine = this.line;
    for (const s of sc.settings) {
      for (const plan of ['naive', 'desert_aware'] as Plan[]) {
        this.sim.setLine(s.service_line);
        let mism = 0, worst = 0;
        const base = this.sim.covered(s.min_grade ?? 2, s.radius_km);
        if (s.baseline_covered != null && base !== s.baseline_covered) mism++;
        for (const e of s[plan]) {
          let ok = false;
          if (e.kind === 'new' && e.lon != null && e.lat != null) { this.sim.addSite(e.lon, e.lat, s.radius_km); ok = true; }
          else {
            const idx = e.facility_id != null ? this.data.facIndexById.get(e.facility_id) : undefined;
            ok = idx !== undefined && this.sim.upgrade(idx, s.radius_km) !== null;
          }
          const cov = this.sim.covered(s.min_grade ?? 2, s.radius_km);
          const diff = Math.abs(cov / this.data.n - e.cumulative_coverage);
          worst = Math.max(worst, diff);
          if (!ok || (e.covered != null ? cov !== e.covered : diff > 1e-6 + 0.5 / this.data.n)) mism++;
        }
        out.push({ setting: `${s.service_line} R=${s.radius_km}`, plan, steps: s[plan].length, mismatches: mism, worst });
      }
    }
    this.sim.setLine(saveLine);
    this.afterSimChange(true);
    return out;
  }

  // ------------------------------------------------------------- camera

  flyTo(cam: Camera, dur: number): void {
    const to: ViewState = { ...this.viewState, ...cam, pitch: 0, bearing: 0 };
    if (dur <= 0) {
      this.viewState = to;
      this.tween = null;
      this.dirty = true;
      return;
    }
    this.tween = { from: { ...this.viewState }, to, t0: this.clock.t, dur };
  }

  private updateTween(t: number): void {
    const tw = this.tween;
    if (!tw) return;
    const p = Math.min(1, (t - tw.t0) / tw.dur);
    const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
    const merc = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
    const inv = (y: number) => (Math.atan(Math.exp(y)) * 360) / Math.PI - 90;
    const lon = tw.from.longitude + (tw.to.longitude - tw.from.longitude) * e;
    const lat = inv(merc(tw.from.latitude) + (merc(tw.to.latitude) - merc(tw.from.latitude)) * e);
    const span = Math.hypot(tw.to.longitude - tw.from.longitude, tw.to.latitude - tw.from.latitude);
    const bump = Math.min(1.4, span / 6) * Math.sin(Math.PI * e);
    const zoom = tw.from.zoom + (tw.to.zoom - tw.from.zoom) * e - bump;
    this.viewState = { ...this.viewState, longitude: lon, latitude: lat, zoom };
    if (p >= 1) this.tween = null;
    this.dirty = true;
  }

  // ------------------------------------------------------------- picking

  private onClick(info: PickingInfo): void {
    const id = info.layer?.id;
    if (this.view === 'swarm') {
      if (this.playback) return;
      if (id === 'fac' && typeof info.object === 'number') {
        if (!this.upgradeFacility(info.object)) this.showFacility(info.object);
        return;
      }
      if (info.coordinate) this.addSite(info.coordinate[0], info.coordinate[1]);
      return;
    }
    if (id === 'fac' && typeof info.object === 'number') {
      this.showFacility(info.object);
      return;
    }
    if ((id === 'po' || id === 'po-static') && info.index >= 0) {
      if (this.view === 'quadrant') {
        const di = this.data.district[info.index];
        this.selectedDistrict = di === this.data.noIndex ? null : di;
        this.selectedFac = null;
        this.renderUI();
      } else {
        this.setPatient(info.index);
      }
      return;
    }
    this.patientPo = null;
    this.selectedFac = null;
    this.selectedDistrict = null;
    this.dirty = true;
    this.renderUI();
  }

  setPatient(po: number | null): void {
    this.patientPo = po;
    this.selectedFac = null;
    this.dirty = true;
    this.renderUI();
  }

  showFacility(i: number): void {
    this.selectedFac = i;
    this.renderUI();
  }

  nearestPo(lon: number, lat: number): number {
    let best = -1, bd = Infinity;
    const { lon: L, lat: A, n } = this.data;
    for (let i = 0; i < n; i++) {
      const dx = (L[i] - lon) * Math.cos((lat * Math.PI) / 180), dy = A[i] - lat;
      const d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  nearRows(po: number): NearRow[] {
    const rows: NearRow[] = [];
    for (let k = 0; k <= 3; k++) {
      const f = this.sim.near[k][po];
      rows.push({ k, facIndex: f, dist: this.sim.dist[k][po], fac: f !== this.data.noIndex ? this.sim.facility(f) : null });
    }
    return rows;
  }

  // -------------------------------------------------------------- layers

  private buildLayers() {
    const t = this.clock.t;
    const zoom = this.viewState.zoom;
    const layers: unknown[] = [];
    layers.push(new ScatterplotLayer({
      // Separate ids: switching colour transitions on a live layer breaks its buffer layout in deck.gl 9.4.
      id: this.transitions ? 'po' : 'po-static',
      data: this.poData as never,
      radiusUnits: 'meters',
      getRadius: 1400,
      radiusMinPixels: zoom < 5 ? 1.05 : 1.3,
      radiusMaxPixels: 4,
      pickable: this.view !== 'swarm',
      transitions: this.transitions ? { getFillColor: 450 } : undefined,
    }));
    if (this.view === 'swarm' && this.showSwarm) {
      const all = zoom >= FULL_SWARM_ZOOM;
      layers.push(new SwarmLayer({
        id: all ? 'swarm-all' : 'swarm-sub',
        data: this.swarm.layerData(all) as never,
        time: t,
        radiusUnits: 'pixels',
        getRadius: all ? 2.2 : 1.9,
        pickable: false,
      }));
    }
    if (this.view !== 'quadrant') {
      const sim = this.sim;
      const fv = sim.version;
      layers.push(new ScatterplotLayer<number>({
        id: 'fac',
        data: this.facVisible,
        getPosition: (i: number) => {
          const f = sim.facility(i)!;
          return [f.lon as number, f.lat as number];
        },
        getFillColor: (i: number) => GRADE_COLORS[Math.max(0, Math.min(3, sim.grade(i)))],
        getRadius: (i: number) => GRADE_RADIUS_PX[Math.max(0, Math.min(3, sim.grade(i)))],
        radiusUnits: 'pixels',
        stroked: true,
        getLineColor: [11, 14, 20, 230],
        lineWidthUnits: 'pixels',
        getLineWidth: 1,
        pickable: true,
        updateTriggers: { getPosition: fv, getFillColor: [this.line, fv], getRadius: [this.line, fv] },
      }));
    }
    if (this.sim.sites.length && this.view !== 'quadrant') {
      layers.push(new ScatterplotLayer({
        id: 'sim-sites',
        data: this.sim.sites,
        getPosition: (s: { lon: number; lat: number }) => [s.lon, s.lat],
        radiusUnits: 'pixels',
        getRadius: 9,
        filled: false,
        stroked: true,
        lineWidthUnits: 'pixels',
        getLineWidth: 2.5,
        getLineColor: ACCENT,
        updateTriggers: { getPosition: this.sim.version },
      }));
    }
    if (this.patientPo != null && this.view === 'distance') {
      const po = this.patientPo;
      const from: [number, number] = [this.data.lon[po], this.data.lat[po]];
      const rows = this.nearRows(po).filter((r) => r.fac && r.fac.lon != null);
      // one label per facility, listing every level it is nearest for
      const byFac = new Map<number, NearRow[]>();
      rows.forEach((r) => byFac.set(r.facIndex, [...(byFac.get(r.facIndex) ?? []), r]));
      const segs = [...byFac.values()].map((rs) => {
        const top = rs[rs.length - 1];
        const to: [number, number] = [top.fac!.lon as number, top.fac!.lat as number];
        const right = to[0] >= from[0];
        return {
          from, to, k: top.k, anchor: right ? 'start' : 'end', dx: right ? 12 : -12,
          label: `${rs.map((r) => gradeLabel(this.data.meta, r.k)).join(', ')}: ${fmtKm(top.dist)}`,
        };
      });
      layers.push(new LineLayer({
        id: 'patient-lines',
        data: segs,
        getSourcePosition: (s: { from: [number, number] }) => s.from,
        getTargetPosition: (s: { to: [number, number] }) => s.to,
        getColor: (s: { k: number }) => GRADE_COLORS[s.k],
        getWidth: 2.5,
        widthUnits: 'pixels',
      }));
      layers.push(new TextLayer({
        id: 'patient-labels',
        data: segs,
        getPosition: (s: { to: [number, number] }) => s.to,
        getText: (s: { label: string }) => s.label,
        getSize: 17,
        getColor: [240, 244, 248, 255],
        getPixelOffset: (s: { dx: number }) => [s.dx, 0],
        getTextAnchor: (s: { anchor: 'start' | 'end' }) => s.anchor,
        getAlignmentBaseline: 'center',
        fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
        fontWeight: 600,
        background: true,
        getBackgroundColor: [11, 14, 20, 225],
        backgroundPadding: [6, 3, 6, 3],
        characterSet: 'auto',
      }));
      layers.push(new ScatterplotLayer({
        id: 'patient-origin',
        data: [from],
        getPosition: (p: [number, number]) => p,
        radiusUnits: 'pixels',
        getRadius: 7,
        filled: true,
        getFillColor: [11, 14, 20, 255],
        stroked: true,
        getLineColor: ACCENT,
        lineWidthUnits: 'pixels',
        getLineWidth: 2.5,
      }));
    }
    if (this.pulse) {
      const ph = (t % 1.6) / 1.6;
      const ph2 = ((t + 0.8) % 1.6) / 1.6;
      layers.push(new ScatterplotLayer({
        id: 'pulse',
        data: [ph, ph2],
        getPosition: () => [this.pulse!.lon, this.pulse!.lat],
        radiusUnits: 'pixels',
        getRadius: (p: number) => 6 + 34 * p,
        filled: false,
        stroked: true,
        lineWidthUnits: 'pixels',
        getLineWidth: 2.5,
        getLineColor: (p: number) => [ACCENT[0], ACCENT[1], ACCENT[2], Math.round(255 * (1 - p))],
        updateTriggers: { getRadius: t, getLineColor: t, getPosition: [this.pulse.lon, this.pulse.lat] },
      }));
      layers.push(new ScatterplotLayer({
        id: 'pulse-core',
        data: [0],
        getPosition: () => [this.pulse!.lon, this.pulse!.lat],
        radiusUnits: 'pixels',
        getRadius: 5,
        getFillColor: ACCENT,
        updateTriggers: { getPosition: [this.pulse.lon, this.pulse.lat] },
      }));
    }
    return layers;
  }

  // --------------------------------------------------------------- frame

  storyUpdate: ((t: number) => void) | null = null;

  /** Everything that depends on the clock, without drawing. */
  update(t: number): void {
    this.storyUpdate?.(t);
    this.updateTween(t);
    this.updatePlayback(t);
  }

  private frame(now: number): void {
    this.clock.tick(now);
    const t = this.clock.t;
    this.frames.push(now);
    while (this.frames.length && now - this.frames[0] > 2000) this.frames.shift();
    this.update(t);
    const animating = this.tween !== null || this.pulse !== null || this.playback !== null ||
      (this.view === 'swarm' && this.showSwarm && (t < this.swarmEnd + 0.1 || this.swarm.counts.likely > 0));
    if (animating || this.dirty) {
      this.deck.setProps({ layers: this.buildLayers() as never, viewState: this.viewState as never });
      this.dirty = false;
    }
    if (this.fpsEl) this.fpsEl.textContent = `${this.fps().toFixed(0)} fps`;
  }

  fpsEl: HTMLElement | null = null;

  fps(): number {
    const f = this.frames;
    if (f.length < 2) return 0;
    return ((f.length - 1) * 1000) / (f[f.length - 1] - f[0]);
  }

  // ------------------------------------------------------------------ UI

  renderUI(): void {
    const d = this.data;
    const meta = d.meta;
    el('mock-badge').hidden = !meta.mock;
    // service line tabs
    el('line-tabs').innerHTML = d.lines.map((l) =>
      `<button data-line="${l.key}" class="${l.key === this.line ? 'on' : ''}">${esc(l.label)}</button>`).join('');
    el('view-tabs').innerHTML = ([['distance', 'Distance'], ['quadrant', 'Care or data gap'], ['swarm', 'Simulator']] as const)
      .map(([v, label]) => `<button data-view="${v}" class="${v === this.view ? 'on' : ''}">${label}</button>`).join('');
    // slider
    const slider = el<HTMLInputElement>('grade-slider');
    if (Number(slider.value) !== this.grade) slider.value = String(this.grade);
    el('grade-stops').innerHTML = [0, 1, 2, 3].map((k) =>
      `<button data-grade="${k}" class="${k === this.grade ? 'on' : ''}">${esc(gradeLabel(meta, k))}</button>`).join('');
    const nAt = this.facilitiesAt(this.grade);
    el('grade-note').innerHTML = this.grade === 0
      ? `${fmtInt(nAt)} facilities of any kind shown`
      : `${fmtInt(nAt)} facilities ${esc(gradeLabel(meta, this.grade).toLowerCase())} or better for ${esc(lineLabel(d, this.line).toLowerCase())}`;
    // radius
    el('radius-seg').innerHTML = (meta.radii_km ?? [30, 60]).map((r) =>
      `<button data-r="${r}" class="${r === this.R ? 'on' : ''}">${r} km</button>`).join('');
    // counter
    const share = this.share();
    const levelTxt = this.grade === 0 ? 'any facility in the data'
      : `a facility at ${esc(gradeLabel(meta, this.grade).toLowerCase())} or better for ${esc(lineLabel(d, this.line, true).toLowerCase())}`;
    el('counter').innerHTML = `<div class="big">${fmtPct(share)}</div>
      <div class="counter-sub">of ${fmtInt(d.n)} post offices are within <b>${this.R} km</b> of ${levelTxt} (straight-line)</div>
      ${this.prevShare ? `<div class="counter-prev">Before: ${fmtPct(this.prevShare.share)} at ${esc(gradeLabel(meta, this.prevShare.grade))}</div>` : ''}
      ${this.sim.sites.length ? `<div class="counter-prev">Includes ${this.sim.sites.length} simulated site${this.sim.sites.length > 1 ? 's' : ''}</div>` : ''}`;
    el('legend').innerHTML = legendHTML(d, this.view, this.grade, this.R, this.quadMode, this.line, this.swarm.ratio);
    this.renderRight();
  }

  facilitiesAt(k: number): number {
    let c = 0;
    const nf = this.sim.facCount();
    for (let i = 0; i < nf; i++) {
      const f = this.sim.facility(i);
      if (f && f.lat != null && f.lon != null && this.sim.grade(i) >= k) c++;
    }
    return c;
  }

  private renderRight(): void {
    const right = el('right');
    const d = this.data;
    let html = '';
    if (this.selectedFac != null) {
      const f = this.sim.facility(this.selectedFac);
      if (f) html = `<button class="close" data-close="fac" aria-label="Close">×</button>` +
        facilityHTML(d, f, this.line, this.sim.grade(this.selectedFac), this.sim.isSimulated(this.selectedFac));
    } else if (this.view === 'swarm') {
      html = this.simPanelHTML();
    } else if (this.view === 'quadrant' && this.selectedDistrict != null) {
      html = `<button class="close" data-close="district" aria-label="Close">×</button>` +
        districtHTML(d, d.districts[this.selectedDistrict], this.line, this.quadMode);
    } else if (this.view === 'distance' && this.patientPo != null) {
      html = `<button class="close" data-close="patient" aria-label="Close">×</button>` +
        patientHTML(d, this.patientPo, this.nearRows(this.patientPo), this.line);
    }
    right.innerHTML = html;
    right.hidden = html === '';
  }

  private simPanelHTML(): string {
    const d = this.data;
    const meta = d.meta;
    const s = this.scenarioSetting();
    const c = this.swarm.counts;
    const b = this.swarm.baseline;
    const share = this.share();
    const up = gradeLabel(meta, this.sim.upgradeTo).toLowerCase();
    const plans: [Plan, string][] = [['naive', 'Naive'], ['desert_aware', 'Desert-aware']];
    const steps = s ? Math.min(10, s.naive.length, s.desert_aware.length) : 0;
    const wasted = (plan: Plan) => s ? s[plan].slice(0, steps).filter((e) => normQuadrant(e.quadrant_model ?? this.districtQuadrantAt(e)) === 'data_desert').length : 0;
    const last = this.played.length ? this.played[this.played.length - 1] : null;
    const running = this.playback ? `${this.playback.plans[this.playback.planIdx] === 'naive' ? 'Naive' : 'Desert-aware'} plan, pick ${this.playback.step} of ${this.playback.steps}` : '';
    return `<div class="panel-kicker">Simulator</div>
      <h2>Where should the next upgrades go?</h2>
      <div class="muted">Click a facility to upgrade it to ${esc(up)}, or click empty space to add a site. Covered: a ${esc(gradeLabel(meta, this.grade).toLowerCase())}-or-better facility within ${this.R} km.</div>
      <div class="stat-row">
        <div class="stat"><div class="stat-v">${fmtPct(share)}</div><div class="stat-l">post offices covered</div></div>
        <div class="stat"><div class="stat-v">${s?.baseline_coverage != null ? fmtPct(s.baseline_coverage) : 'n/a'}</div><div class="stat-l">before any upgrade</div></div>
      </div>
      <div class="swarm-counts">
        <div class="kv"><span><span class="sw dot" style="background:${rgbaCss([201, 133, 0, 255])}"></span>Stranded at ${this.R} km</span><b>${fmtInt(c.stranded)}${b ? `<span class="was">was ${fmtInt(b.stranded)}</span>` : ''}</b></div>
        <div class="kv"><span><span class="sw ring" style="border-color:${rgbaCss([57, 135, 229, 255])}"></span>Care likely exists, not in this data (est.)</span><b>${fmtInt(c.likely)}${b ? `<span class="was">was ${fmtInt(b.likely)}</span>` : ''}</b></div>
        <div class="kv"><span><span class="sw dot" style="background:#f2f5f8"></span>Reached</span><b>${fmtInt(c.reached)}</b></div>
        <div class="kv"><span>Rescued this run: stranded / care likely exists (est.)</span><b>${fmtInt(this.swarm.rescued.stranded)} / ${fmtInt(this.swarm.rescued.likely)}</b></div>
        <div class="muted">Counts are post offices, not people.</div>
      </div>
      <div class="seg" id="plan-seg">${plans.map(([p, l]) => `<button data-plan="${p}" class="${this.plan === p ? 'on' : ''}">${l}</button>`).join('')}</div>
      <div class="btn-row">
        <button class="primary" id="play-btn" ${s ? '' : 'disabled'}>${this.playback ? 'Stop' : `Play ${steps} picks`}</button>
        <button id="release-btn">Release patients</button>
        <button id="reset-btn">Reset</button>
      </div>
      ${running ? `<div class="running">${esc(running)}</div>` : ''}
      ${s ? `<div class="muted">Greedy plan from scenarios.json. Top ${steps} picks in data-desert districts: naive <b>${wasted('naive')}</b>, desert-aware <b>${wasted('desert_aware')}</b>.</div>` : `<div class="muted">No scenario for ${esc(lineLabel(d, this.line))} at ${this.R} km in scenarios.json.</div>`}
      ${s ? this.curveSVG(s, steps) : ''}
      ${last ? `<div class="match ${last.match ? 'ok' : 'bad'}">Pick ${last.step}: in-browser ${fmtPct(last.browser, 2)}, scenarios.json ${fmtPct(last.file, 2)} · ${last.match ? 'match' : 'MISMATCH'}</div>` : ''}
      <label class="check"><input type="checkbox" id="swarm-toggle" ${this.showSwarm ? 'checked' : ''}/> Show patient swarm</label>`;
  }

  private districtQuadrantAt(e: ScenarioStep): string | null {
    if (e.district_key) {
      const i = this.data.districtIndexByKey.get(e.district_key);
      if (i !== undefined) return this.data.districts[i].quadrant?.[this.line]?.quadrant_model ?? null;
    }
    if (e.lon != null && e.lat != null) {
      const po = this.nearestPo(e.lon, e.lat);
      const di = this.data.district[po];
      if (di !== this.data.noIndex) return this.data.districts[di].quadrant?.[this.line]?.quadrant_model ?? null;
    }
    return null;
  }

  private curveSVG(s: ScenarioSetting, steps: number): string {
    const W = 340, H = 150, L = 44, Rt = 10, T = 12, B = 26;
    const series: [Plan, string, string][] = [['naive', 'Naive', '#9aa5b1'], ['desert_aware', 'Desert-aware', rgbaCss(ACCENT)]];
    const base = s.baseline_coverage ?? 0;
    const ys: number[] = [base];
    series.forEach(([p]) => s[p].slice(0, steps).forEach((e) => ys.push(e.cumulative_coverage)));
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi - lo < 0.002) { hi += 0.001; lo -= 0.001; }
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;
    const x = (i: number) => L + ((W - L - Rt) * i) / Math.max(1, steps);
    const y = (v: number) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
    const lines = series.map(([p, , col]) => {
      const pts = [base, ...s[p].slice(0, steps).map((e) => e.cumulative_coverage)];
      return `<polyline fill="none" stroke="${col}" stroke-width="2" ${p === 'naive' ? 'stroke-dasharray="5 4"' : ''} points="${pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')}"/>`;
    }).join('');
    const dots = this.played.filter((p) => p.step <= steps).map((p) =>
      `<circle cx="${x(p.step).toFixed(1)}" cy="${y(p.browser).toFixed(1)}" r="4" fill="${p.plan === 'naive' ? '#9aa5b1' : rgbaCss(ACCENT)}" stroke="#0b0e14" stroke-width="1.5"><title>${p.plan} pick ${p.step}: ${fmtPct(p.browser, 2)}</title></circle>`).join('');
    const ticks = [lo + pad, hi - pad].map((v) => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmtPct(v)}</text>`).join('');
    return `<svg class="curve" viewBox="0 0 ${W} ${H}" role="img" aria-label="Coverage curve">
      <line x1="${L}" y1="${H - B}" x2="${W - Rt}" y2="${H - B}" stroke="#39404d"/>
      ${ticks}
      <text x="${L}" y="${H - 6}">0</text><text x="${W - Rt}" y="${H - 6}" text-anchor="end">${steps} picks</text>
      ${lines}${dots}
    </svg>
    <div class="curve-legend">${series.map(([p, l, col]) => `<span><i style="border-color:${col};${p === 'naive' ? 'border-top-style:dashed' : ''}"></i>${l}</span>`).join('')}<span class="muted">Share of post offices covered</span></div>`;
  }

  // --------------------------------------------------------- overlays

  openDrawer(open: boolean): void {
    const dr = el('drawer');
    if (open) dr.innerHTML = drawerHTML(this.data);
    dr.classList.toggle('open', open);
    document.body.classList.toggle('drawer-open', open);
  }

  showOverlay(kind: 'lists' | 'limits' | null): void {
    const ov = el('overlay');
    if (!kind) { ov.hidden = true; ov.innerHTML = ''; return; }
    ov.innerHTML = kind === 'lists' ? listsHTML(this.data, this.line) : limitsHTML(this.data);
    ov.hidden = false;
  }
}
