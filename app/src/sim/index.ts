// The agent simulator's public API (SPEC 7, 9):
//
//   const sim = new Simulator(map);          // map: B's MapHost (or the dev host)
//   sim.load(simPayload, answer, {fixture});
//   sim.open({ mode, autoplay, tool, edit });
//   sim.previewAdd(origin);                  // {before, after} without changing the world
//   sim.close();
//
// Patients decide at run time by the stated rule (agents.ts). What-if edits
// (verify, add, close) change the world; every edit re-decides the dots on
// the road, recomputes the counters (logged, budget 200 ms) and is labelled
// what-if, with undo and reset. Deterministic hooks: window.__vf.sim.

import { Engine, type Mode } from './agents';
import type { SimPayload } from './contract';
import type { ExpandSlots, SimMapHost } from './host';
import { SimLayers } from './layers';
import { paritySummary } from './parity';
import { displayName, h, legendRows, Panel, type CloseCandidate, type Tool, type VerifyCandidate } from './panel';
import { counters, previewAdd as previewAddRule, verifyFirst, type Counters, type VerifyFirst } from './rules';
import { haversineKm, ST_CLAIM_ONLY, ST_EVIDENCE, ST_HELD_BACK, World, type EditInput } from './world';

export type { Mode } from './agents';
export type { Counters, VerifyFirst } from './rules';
export type { EditInput } from './world';
export { paritySummary } from './parity';

export interface OpenOptions {
  mode?: Mode;
  autoplay?: boolean;
  tool?: Tool | null;
  /** an edit to apply before the run starts, for example from "What if it were verified?" */
  edit?: EditInput;
}

export interface SimState {
  time: number;
  speed: number;
  mode: Mode;
  playing: boolean;
  done: boolean;
  edits: { id: number; type: string; label: string }[];
  tally: ReturnType<Engine['tally']>;
  version: number;
  n_agents: number;
  k: number;
  fixture: boolean;
}

const PICK_PX = 12;
const SEED = 7;

export class Simulator {
  private map: SimMapHost;
  private p: SimPayload | null = null;
  private world: World | null = null;
  private engine: Engine | null = null;
  private panel: Panel | null = null;
  private layers = new SimLayers();
  private slots: ExpandSlots | null = null;
  private fixture = false;
  private real: Counters | null = null;
  private now: Counters | null = null;
  private mode: Mode = 'evidence';
  private tool: Tool | null = null;
  private playing = false;
  private raf = 0;
  private lastFrame = 0;
  private pulseSec = 0;
  private capacity: number | null = null;
  private highlight: number | null = null;
  private vf: VerifyFirst | null = null;
  private lastTickUi = 0;
  private resumeAfterPop = false;
  private lastLabelsKey = '';
  private lastLabelsAt = 0;
  private phone: boolean;
  private kOverride: number | null;
  /** frame-time log for the performance check (ms per frame, last 600) */
  readonly frameMs: number[] = [];
  /** recompute times of the last edits, ms */
  readonly editMs: number[] = [];

  constructor(map: SimMapHost, opts: { phone?: boolean; k?: number } = {}) {
    const need = ['expand', 'collapse', 'isExpanded', 'fit', 'setOverlayLayers', 'setExtraLegend', 'setBanner', 'setMapClickHandler', 'setCursor'] as const;
    const missing = need.filter((k) => typeof (map as unknown as Record<string, unknown>)[k] !== 'function');
    if (missing.length || !(map as { deck?: unknown }).deck) {
      throw new Error(`Simulator needs B's MapHost (SPEC 5.4); this map lacks: ${[...missing, ...((map as { deck?: unknown }).deck ? [] : ['deck'])].join(', ')}`);
    }
    this.map = map;
    this.phone = opts.phone ?? window.matchMedia('(max-width: 700px)').matches;
    this.kOverride = opts.k ?? null;
  }

  // ------------------------------------------------------------------ lifecycle

  /** Load one /api/sim payload. `answer` is accepted for the wiring in SPEC 9 and not read. */
  load(p: SimPayload, _answer?: unknown, opts: { fixture?: boolean } = {}): void {
    if (this.panel) this.close();
    void _answer;
    if (p.status !== 'ok') throw new Error(`sim unavailable: ${p.reason ?? 'no reason given'}`);
    this.p = p;
    this.fixture = !!opts.fixture;
    const t0 = performance.now();
    this.world = new World(p);
    const tWorld = performance.now() - t0;
    this.real = counters(this.world);
    this.now = null;
    this.engine = new Engine(this.world, { seed: SEED, k: this.kOverride ?? (this.phone ? 2 : 3), mode: this.mode, capacity: this.capacity });
    this.vf = null;
    this.layers.reset();
    const parity = paritySummary(this.real, p);
    console.info(`[sim] loaded ${p.villages.n} villages x ${p.facilities.length} facilities in ${tWorld.toFixed(1)} ms; ${this.engine.N} dots; parity with server summary: ${parity.ok ? 'equal' : 'DIFFERENT ' + JSON.stringify(parity.diff)}`);
    this.installHooks();
  }

  get loaded(): boolean {
    return !!this.world;
  }

  open(opts: OpenOptions = {}): void {
    if (!this.world || !this.engine || !this.p) throw new Error('Simulator.open before load');
    if (!this.panel) {
      // the layout (rail or phone sheet) follows the window when the run opens, not when it loaded
      this.phone = window.matchMedia('(max-width: 700px)').matches;
      this.slots = this.map.expand({ rail: true });
      this.panel = new Panel(this.p, this.handlers(), this.slots, { phone: this.phone, fixture: this.fixture });
      this.slots.mapEl.classList.add('sim-open');
      this.map.setMapClickHandler((ll, picked) => this.onMapClick(ll, picked));
    }
    if (opts.mode && opts.mode !== this.mode) {
      this.mode = opts.mode;
      this.engine.setMode(this.mode);
    }
    this.panel.setMode(this.mode);
    if (opts.edit) this.applyEdit(opts.edit);
    this.setTool(opts.tool ?? null);
    this.refreshAll();
    this.fitCamera();
    if (!this.raf) this.loop();
    if (opts.autoplay ?? true) this.play();
  }

  /** keepMap: the run's own close button leaves the large map open (without the rail); anything else closes it too. */
  close(keepMap = false): void {
    this.pause();
    this.map.setOverlayLabels?.([]);
    this.lastLabelsKey = '';
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.map.setOverlayLayers([]);
    this.map.setExtraLegend([]);
    this.map.setBanner(null);
    this.map.setMapClickHandler(null);
    this.map.setCursor('default');
    this.slots?.mapEl.classList.remove('sim-open', 'sim-banner-on');
    this.panel?.destroy();
    this.panel = null;
    this.slots = null;
    this.tool = null;
    if (this.map.isExpanded()) {
      if (keepMap) this.map.expand({ rail: false });
      else this.map.collapse();
    }
  }

  // ------------------------------------------------------------------ playback

  play(): void {
    const e = this.engine;
    if (!e) return;
    if (e.done) e.replay();
    this.playing = true;
    this.lastFrame = performance.now();
    this.panel?.setPlaying(true, false);
  }

  pause(): void {
    this.playing = false;
    this.panel?.setPlaying(false, !!this.engine?.done);
  }

  replay(): void {
    this.engine?.replay();
    this.layers.reset();
    this.afterWorldChange(false);
    this.play();
  }

  /** Re-run from t = 0 to sim-minute t with no rendering; pauses. Frame-exact. */
  seek(t: number): void {
    const e = this.engine;
    if (!e) return;
    this.pause();
    e.seek(t);
    this.pulseSec = t * 0.25;
    this.layers.reset();
    this.afterWorldChange(false);
    this.render(performance.now() / 1000);
  }

  setMode(m: Mode): void {
    if (!this.engine) return;
    this.mode = m;
    this.engine.setMode(m);
    this.layers.reset();
    this.panel?.setMode(m);
    this.afterWorldChange(false);
    if (this.panel) this.play();
  }

  // ------------------------------------------------------------------ what-if

  /** Apply an edit now (or at a sim-minute: re-runs to that minute first). */
  apply(edit: EditInput, atMinute?: number): void {
    if (atMinute !== undefined) this.seek(atMinute);
    this.applyEdit(edit);
  }

  private applyEdit(edit: EditInput): void {
    const w = this.world, e = this.engine;
    if (!w || !e) return;
    if (edit.type !== 'add') {
      const j = w.indexOf(edit.facility_id);
      if (j < 0) return;
      if (edit.type === 'verify' && (w.ev(j) || !w.open[j])) return;
      if (edit.type === 'close' && !w.open[j]) return;
    }
    const t0 = performance.now();
    const wasDone = e.done;
    w.apply(edit);
    e.worldChanged();
    this.afterWorldChange(true, t0);
    // An edit after the run ended would show nothing moving: play the run again on the changed map.
    if (wasDone && this.panel) this.replay();
    else if (this.resumeAfterPop) { this.resumeAfterPop = false; this.play(); }
  }

  /** Confirm popovers stop the clock, so the dots are still on the road when the change is made. */
  private pausedPopover(sx: number, sy: number, kids: Parameters<Panel['showPopover']>[2]): void {
    if (this.playing) { this.resumeAfterPop = true; this.pause(); }
    this.panel?.showPopover(sx, sy, kids);
  }

  private cancelPopover(): void {
    this.panel?.hidePopover();
    if (this.resumeAfterPop) { this.resumeAfterPop = false; this.play(); }
  }

  undo(id?: number): void {
    const w = this.world, e = this.engine;
    if (!w || !e) return;
    const t0 = performance.now();
    if (id === undefined) w.undoLast(); else w.remove(id);
    e.worldChanged();
    this.afterWorldChange(true, t0);
  }

  reset(): void {
    const w = this.world, e = this.engine;
    if (!w || !e) return;
    const t0 = performance.now();
    // "Reset to real data" starts the run over with the real data, so no dot
    // keeps a what-if turn from before the reset
    w.reset();
    e.replay();
    this.layers.reset();
    // reset also puts the tools down, so the next map click does not quietly add another place
    this.setTool(null);
    this.afterWorldChange(true, t0);
  }

  /** Counters before and after a what-if facility at the point, without changing the world. */
  previewAdd(at: { lat: number; lon: number }): { before: Counters; after: Counters } | null {
    return this.world ? previewAddRule(this.world, at.lon, at.lat) : null;
  }

  runVerifyFirst(): VerifyFirst | null {
    if (!this.world) return null;
    const r = verifyFirst(this.world);
    console.info(`[sim] verify-first sweep: ${r.n_candidates} candidates in ${r.ms.toFixed(1)} ms`);
    this.vf = r;
    this.panel?.setVerifyFirst(r, (fid) => this.applyEdit({ type: 'verify', facility_id: fid }), this.world.edits.length);
    return r;
  }

  counters(): { real: Counters | null; whatif: Counters | null; server: SimPayload['summary'] | null } {
    return { real: this.real, whatif: this.now, server: this.p?.summary ?? null };
  }

  state(): SimState | null {
    const e = this.engine, w = this.world;
    if (!e || !w) return null;
    return {
      time: e.time, speed: e.speed, mode: this.mode, playing: this.playing, done: e.done,
      edits: w.edits.map((x) => ({ id: x.id, type: x.type, label: w.editLabel(x) })),
      tally: e.tally(), version: w.version, n_agents: e.N, k: e.k, fixture: this.fixture,
    };
  }

  // ------------------------------------------------------------------ internals

  private afterWorldChange(edited: boolean, t0?: number): void {
    const w = this.world!, p = this.panel;
    this.now = w.edits.length ? counters(w) : null;
    if (edited && t0 !== undefined) {
      const ms = performance.now() - t0;
      this.editMs.push(ms);
      console.info(`[sim] what-if recompute (world, ${this.engine!.N} dots re-decided, counters) in ${ms.toFixed(1)} ms`);
    }
    if (edited && this.vf) this.runVerifyFirst();
    if (!p) return;
    this.refreshAll();
  }

  private refreshAll(): void {
    const p = this.panel, w = this.world, e = this.engine;
    if (!p || !w || !e || !this.real) return;
    p.setCounters(this.real, this.now, w.edits.length);
    p.setEdits(w.edits.map((x) => ({ id: x.id, label: w.editLabel(x) })));
    p.closeCands = this.closeCandidates();
    p.setTool(this.tool, this.candidates());
    const has = w.edits.length > 0;
    this.map.setBanner(has ? p.banner : null);
    this.slots?.mapEl.classList.toggle('sim-banner-on', has);
    this.map.setExtraLegend(legendRows(this.mode, has || e.tally().turned > 0, this.capacity !== null));
    p.setPlaying(this.playing, e.done);
    this.tickUi(true);
  }

  private candidates(): VerifyCandidate[] {
    const w = this.world;
    if (!w) return [];
    const out: VerifyCandidate[] = [];
    for (let j = 0; j < w.nReal; j++) {
      if (!w.open[j] || w.status[j] === ST_EVIDENCE) continue;
      const m = w.meta[j];
      out.push({ facility_id: m.facility_id, name: m.name, km_from_origin: m.km_from_origin, approximate: m.approximate,
        held_back: w.status[j] === ST_HELD_BACK, verified: !!w.verified[j] });
    }
    out.sort((a, b) => a.km_from_origin - b.km_from_origin);
    return out.slice(0, 10);
  }

  private closeCandidates(): CloseCandidate[] {
    const w = this.world;
    if (!w) return [];
    const out: CloseCandidate[] = [];
    for (let j = 0; j < w.nReal; j++) {
      if (!w.open[j]) continue;
      const m = w.meta[j];
      out.push({ facility_id: m.facility_id, name: m.name, km_from_origin: m.km_from_origin, approximate: m.approximate, evidence: w.ev(j) });
    }
    out.sort((a, b) => a.km_from_origin - b.km_from_origin);
    return out.slice(0, 10);
  }

  private setTool(t: Tool | null): void {
    this.tool = t;
    this.panel?.hidePopover();
    if (this.panel) this.panel.closeCands = this.closeCandidates();
    this.map.setCursor(t ? 'crosshair' : 'default');
    this.panel?.setTool(t, this.candidates());
    this.layers.reset();
  }

  private handlers() {
    return {
      onMode: (m: Mode) => this.setMode(m),
      onPlay: () => this.play(),
      onPause: () => this.pause(),
      onReplay: () => this.replay(),
      onTool: (t: Tool | null) => this.setTool(t),
      onUndo: (id: number) => this.undo(id),
      onUndoLast: () => this.undo(),
      onReset: () => this.reset(),
      onVerifyFirst: () => this.runVerifyFirst(),
      onVerifyId: (fid: string) => this.applyEdit({ type: 'verify', facility_id: fid }),
      onCapacity: (n: number | null) => {
        this.capacity = n;
        this.engine?.setCapacity(n);
        this.layers.reset();
        this.refreshAll();
        this.play();
      },
      onClose: () => this.close(true),
      onAddAt: (lon: number, lat: number) => this.applyEdit({ type: 'add', lon, lat }),
      onCloseId: (fid: string) => this.applyEdit({ type: 'close', facility_id: fid }),
      onHoverFacility: (fid: string | null) => {
        const w = this.world;
        this.highlight = fid && w ? w.indexOf(fid) : null;
        if (this.highlight !== null && this.highlight < 0) this.highlight = null;
        this.layers.reset();
      },
    };
  }

  /** The facility nearest the click on screen, within PICK_PX, filtered. */
  private pickFacility(lngLat: [number, number], ok: (j: number) => boolean): number {
    const w = this.world!;
    const vp = this.map.deck.getViewports()[0];
    if (!vp) return -1;
    const [cx, cy] = vp.project(lngLat);
    let best = -1, bd = PICK_PX * PICK_PX;
    for (let j = 0; j < w.F; j++) {
      if (!ok(j)) continue;
      const [x, y] = vp.project([w.flon[j], w.flat[j]]);
      const d = (x - cx) ** 2 + (y - cy) ** 2;
      if (d <= bd) { bd = d; best = j; }
    }
    return best;
  }

  private onMapClick(lngLat: [number, number], _picked: unknown): void {
    void _picked;
    const w = this.world, p = this.panel, pp = this.p;
    if (!w || !p || !pp) return;
    const vp = this.map.deck.getViewports()[0];
    const [sx, sy] = vp ? vp.project(lngLat) : [0, 0];
    const visible = (j: number) => !!w.open[j];
    if (this.tool === 'verify') {
      const j = this.pickFacility(lngLat, (k) => visible(k) && !w.ev(k) && !w.isAdded(k));
      if (j < 0) { p.showPopover(sx, sy, [h('p', {}, 'Click an amber diamond: a place that lists it with no evidence in its record. Or pick one from the list.')]); return; }
      p.hidePopover();
      this.applyEdit({ type: 'verify', facility_id: w.meta[j].facility_id });
      return;
    }
    if (this.tool === 'add') {
      const near = this.pickFacility(lngLat, visible);
      if (near >= 0) { p.showPopover(sx, sy, [h('p', {}, 'Click empty ground, away from the places already on the map.')]); return; }
      this.pausedPopover(sx, sy, [
        h('p', {}, h('strong', {}, `Add a what-if ${pp.service_phrase} unit here?`)),
        h('p', { class: 'sim-sub' }, `Service: ${pp.service_label} (the one you searched). The run waits while you decide.`),
        h('div', { class: 'sim-pop-actions' },
          h('button', { type: 'button', class: 'sim-btn sim-btn-primary', onclick: () => { p.hidePopover(); this.applyEdit({ type: 'add', lon: lngLat[0], lat: lngLat[1] }); } }, 'Add'),
          h('button', { type: 'button', class: 'sim-btn', onclick: () => this.cancelPopover() }, 'Cancel')),
      ]);
      return;
    }
    const j = this.pickFacility(lngLat, visible);
    if (j < 0) { this.cancelPopover(); return; }
    const m = w.meta[j];
    const nm = displayName(m.name, pp);
    if (this.tool === 'close') {
      this.pausedPopover(sx, sy, [
        h('p', {}, h('strong', {}, `Close ${nm} in this what-if?`)),
        h('p', { class: 'sim-sub' }, 'The run waits while you decide.'),
        h('div', { class: 'sim-pop-actions' },
          h('button', { type: 'button', class: 'sim-btn sim-btn-primary', onclick: () => { p.hidePopover(); this.applyEdit({ type: 'close', facility_id: m.facility_id }); } }, 'Close it'),
          h('button', { type: 'button', class: 'sim-btn', onclick: () => this.cancelPopover() }, 'Cancel')),
      ]);
      return;
    }
    // no tool: what this place is, with the two what-if actions
    const status = w.isAdded(j) ? `What-if: new ${pp.service_phrase} unit, not real data.`
      : w.verified[j] ? `What-if: treated as verified. Its real record shows no evidence of ${pp.service_phrase}.`
      : w.status[j] === ST_EVIDENCE ? `Its record shows ${pp.service_phrase}.`
      : w.status[j] === ST_CLAIM_ONLY ? `Lists ${pp.service_phrase}, no evidence in its record.`
      : `Lists ${pp.service_phrase}; its evidence is held back by a warning in its record.`;
    const where = m.approximate ? 'position approximate' : `${m.km_from_origin} km from ${pp.origin.short_name}`;
    const canVerify = !w.ev(j) && !w.isAdded(j);
    this.pausedPopover(sx, sy, [
      h('p', {}, h('strong', {}, nm)),
      h('p', { class: 'sim-sub' }, `${where}. ${status}`),
      h('div', { class: 'sim-pop-actions' },
        canVerify ? h('button', { type: 'button', class: 'sim-btn', onclick: () => { p.hidePopover(); this.applyEdit({ type: 'verify', facility_id: m.facility_id }); } }, 'What if it were checked?') : null,
        h('button', { type: 'button', class: 'sim-btn', onclick: () => { p.hidePopover(); this.applyEdit({ type: 'close', facility_id: m.facility_id }); } }, 'Close it (what-if)'),
        h('button', { type: 'button', class: 'sim-btn', onclick: () => this.cancelPopover() }, 'Cancel')),
    ]);
  }

  /** Fit the 50 km village ring plus every place a dot targets in the first decision pass. */
  private fitCamera(): void {
    const w = this.world, e = this.engine, pp = this.p;
    if (!w || !e || !pp) return;
    const { lat, lon } = pp.origin;
    const r = pp.villages.radius_km;
    const dLat = r / 111.2, dLon = r / (111.2 * Math.cos((lat * Math.PI) / 180));
    let x0 = lon - dLon, x1 = lon + dLon, y0 = lat - dLat, y1 = lat + dLat;
    const targets = new Set<number>();
    // the first decision of every village under the current mode, read from the rules (no run needed)
    const probe = new Engine(w, { seed: SEED, k: 1, mode: this.mode });
    probe.advanceTo(30);
    for (const j of probe.firstTargets) targets.add(j);
    for (const j of e.firstTargets) targets.add(j);
    for (const j of targets) {
      if (j < 0) continue;
      x0 = Math.min(x0, w.flon[j]); x1 = Math.max(x1, w.flon[j]);
      y0 = Math.min(y0, w.flat[j]); y1 = Math.max(y1, w.flat[j]);
    }
    this.map.fit([[x0, y0], [x1, y1]], this.phone ? 24 : 70, 800);
  }

  private loop = (): void => {
    const now = performance.now();
    const e = this.engine;
    if (e && this.playing) {
      const dt = Math.min(0.1, (now - this.lastFrame) / 1000);
      e.advanceTo(e.time + dt * e.speed);
      this.pulseSec += dt;
      if (e.done) { this.pause(); this.tickUi(true); }
    }
    this.lastFrame = now;
    this.render(now / 1000);
    const ms = performance.now() - now;
    this.frameMs.push(ms);
    if (this.frameMs.length > 600) this.frameMs.shift();
    this.tickUi(false);
    this.raf = requestAnimationFrame(this.loop);
  };

  private render(realSec: number): void {
    const e = this.engine, w = this.world;
    if (!e || !w) return;
    this.map.setOverlayLayers(this.layers.build(e, w, realSec, this.pulseSec, {
      phone: this.phone, highlight: this.highlight, verifyTool: this.tool === 'verify',
    }));
    // the counts at each place go through the map's label declutter (at most 4 times a second)
    const now = performance.now();
    const labs = this.layers.mapLabels;
    const key = labs.map((l) => l.text).join('|');
    if (key !== this.lastLabelsKey && now - this.lastLabelsAt > 250) {
      this.lastLabelsKey = key;
      this.lastLabelsAt = now;
      this.map.setOverlayLabels?.(labs);
    }
  }

  private tickUi(force: boolean): void {
    const now = performance.now();
    if (!force && now - this.lastTickUi < 100) return;
    this.lastTickUi = now;
    const e = this.engine, p = this.panel;
    if (!e || !p) return;
    const progress = e.done ? 1 : e.time / Math.max(1, e.speed * 12 / 0.9);
    p.setTick(e.time, e.speed, progress, e.tally(), e.villageTally(), this.capacity !== null);
  }

  private installHooks(): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = window as any;
    g.__vf = g.__vf || {};
    g.__vf.sim = {
      seek: (t: number) => this.seek(t),
      play: () => this.play(),
      pause: () => this.pause(),
      setMode: (m: Mode) => this.setMode(m),
      apply: (edit: EditInput, atMinute?: number) => this.apply(edit, atMinute),
      undo: (id?: number) => this.undo(id),
      reset: () => this.reset(),
      counters: () => this.counters(),
      state: () => this.state(),
      verifyFirst: () => this.runVerifyFirst(),
      previewAdd: (at: { lat: number; lon: number }) => this.previewAdd(at),
      perf: () => ({ frameMs: this.frameMs.slice(), editMs: this.editMs.slice() }),
      facilities: () => this.world ? this.world.meta.slice(0, this.world.F).map((m, j) => ({
        j, facility_id: m.facility_id, name: m.name, km: m.km_from_origin, status: this.world!.status[j],
        open: !!this.world!.open[j], ev: this.world!.ev(j),
        km_to_origin: haversineKm(this.p!.origin.lat, this.p!.origin.lon, this.world!.flat[j], this.world!.flon[j]),
      })) : [],
    };
  }
}
