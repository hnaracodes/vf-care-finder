// Story mode: reads story.json, spacebar advances, ?autoplay plays every beat
// with fixed timings on a frame-stepped clock, so two takes are identical.

import type { App } from './app';
import { INDIA } from './app';
import type { Beat, Story } from './data';
import { el, esc } from './ui';

export class StoryPlayer {
  readonly app: App;
  readonly story: Story;
  idx = -1;
  autoplay = false;
  private beatStart = 0;
  private lastSweep = -1;
  private counterPhase = -1;
  private scenarioStarted = false;
  onEnd: (() => void) | null = null;

  constructor(app: App, story: Story) {
    this.app = app;
    this.story = story;
  }

  get active(): boolean {
    return this.idx >= 0;
  }

  start(autoplay: boolean): void {
    this.autoplay = autoplay;
    this.app.clock.setFixed(autoplay);
    this.trace = [];
    this.app.transitions = !autoplay;
    this.app.storyActive = true;
    document.body.classList.add('story');
    this.app.storyUpdate = (t) => this.update(t);
    this.enter(0);
  }

  stop(): void {
    this.idx = -1;
    this.app.storyActive = false;
    this.app.pulse = null;
    this.app.storyUpdate = null;
    this.snapshot('end');
    this.app.clock.setFixed(false);
    this.app.transitions = true;
    this.app.showOverlay(null);
    this.app.openDrawer(false);
    this.app.stopPlayback();
    document.body.classList.remove('story');
    el('caption').hidden = true;
    this.onEnd?.();
  }

  next(): void {
    if (this.idx + 1 < this.story.beats.length) this.enter(this.idx + 1);
    else this.stop();
  }

  prev(): void {
    if (this.idx > 0) this.enter(this.idx - 1);
  }

  /** State at each beat boundary, for checking that two takes are identical. */
  trace: Record<string, unknown>[] = [];

  private snapshot(label: string): void {
    const a = this.app;
    const last = a.played[a.played.length - 1];
    this.trace.push({
      at: label, beat: this.idx >= 0 ? this.story.beats[this.idx]?.id : null, t: Number(a.clock.t.toFixed(4)),
      view: a.view, line: a.line, grade: a.grade, R: a.R, share: Number(a.share().toFixed(6)),
      sites: a.sim.sites.length, played: a.played.length, lastPlayed: last ? Number(last.browser.toFixed(6)) : null,
      allMatch: a.played.every((p) => p.match), swarm: { ...a.swarm.counts }, rescued: { ...a.swarm.rescued },
      cam: [a.viewState.longitude, a.viewState.latitude, a.viewState.zoom].map((v) => Number(v.toFixed(4))),
    });
  }

  /** Runs the whole autoplay take synchronously (no drawing) and returns the trace. */
  simulate(maxSeconds = 600): Record<string, unknown>[] {
    const clock = this.app.clock;
    clock.paused = true;
    this.start(true);
    let guard = maxSeconds * 60;
    while (this.active && guard-- > 0) {
      clock.step(1);
      this.app.update(clock.t);
    }
    clock.paused = false;
    return this.trace;
  }

  /** Plays the take synchronously up to `seconds` and freezes the clock there (for screenshots). */
  seek(seconds: number): void {
    const clock = this.app.clock;
    clock.paused = true;
    this.start(true);
    const end = clock.t + seconds;
    while (this.active && clock.t < end - 1e-9) {
      clock.step(1);
      this.app.update(clock.t);
    }
  }

  private enter(i: number): void {
    const app = this.app;
    if (this.idx >= 0) this.snapshot('before');
    const b = this.story.beats[i];
    const def = this.story.defaults ?? {};
    this.idx = i;
    this.beatStart = app.clock.t;
    this.lastSweep = -1;
    this.counterPhase = -1;
    this.scenarioStarted = false;
    app.stopPlayback();
    app.showOverlay(null);
    app.openDrawer(false);
    // simulated sites belong to the scenario beat only
    if (!b.scenario && app.sim.sites.length) app.resetSim();
    app.setLine(b.line ?? def.line ?? app.line);
    app.setRadius(b.radius_km ?? def.radius_km ?? app.R);
    app.setView(b.view ?? 'distance');
    const startGrade = b.show_counter_change ? b.show_counter_change.from_grade : b.sweep?.length ? b.sweep[0] : b.grade;
    if (startGrade != null) app.setGrade(startGrade);
    if (b.show_counter_change == null) app.prevShare = null;
    app.setHighlight(b.highlight ?? null);
    app.pulse = b.pulse ?? null;
    if (b.patient) app.setPatient(app.nearestPo(b.patient.lon, b.patient.lat));
    else app.setPatient(null);
    app.flyTo(b.camera ?? INDIA, this.transition(b));
    if (b.panel === 'drawer') app.openDrawer(true);
    if (b.panel === 'lists' || b.panel === 'limits') app.showOverlay(b.panel);
    const cap = el('caption');
    cap.hidden = !b.caption;
    cap.innerHTML = `<div class="cap-text">${esc(b.caption ?? '')}</div><div class="cap-meta">${i + 1} / ${this.story.beats.length}${this.autoplay ? '' : ' · space for next'}</div>`;
    app.renderUI();
  }

  private transition(b: Beat): number {
    return Math.min(b.duration_s ?? 10, this.story.defaults?.transition_s ?? 2.5);
  }

  private update(t: number): void {
    if (this.idx < 0) return;
    const b = this.story.beats[this.idx];
    const app = this.app;
    const local = t - this.beatStart;
    const dur = b.duration_s ?? 10;
    const tr = this.transition(b);
    if (b.sweep?.length) {
      const span = Math.max(1, dur - tr - 2);
      const per = span / b.sweep.length;
      const j = Math.min(b.sweep.length - 1, Math.max(0, Math.floor((local - tr) / per)));
      if (local >= tr && j !== this.lastSweep) {
        this.lastSweep = j;
        app.setGrade(b.sweep[j]);
      }
    }
    if (b.show_counter_change) {
      const phase = local < tr + (dur - tr) * 0.35 ? 0 : 1;
      if (phase !== this.counterPhase) {
        this.counterPhase = phase;
        app.setGrade(phase === 0 ? b.show_counter_change.from_grade : b.show_counter_change.to_grade);
      }
    }
    if (b.scenario && !this.scenarioStarted && local >= tr * 0.6) {
      this.scenarioStarted = true;
      const plans = b.scenario.plans.length;
      const steps = b.scenario.steps;
      // leave time for each plan's opening release and a closing pause
      // the first pick waits for the opening release (0.2 s delay, 1.5 s stagger, R / speed, 0.5 s pause);
      // each later plan rewinds in 1.2 s; keep 2 s at the end
      const firstPick = 0.2 + 1.5 + app.R / app.swarm.speedKmS + 0.5;
      const stepDur = Math.max(0.5, (dur - firstPick - (plans - 1) * 1.2 - 2) / (plans * steps));
      app.startPlayback(b.scenario.plans, steps, stepDur);
    }
    if (this.autoplay && local >= dur) this.next();
  }
}
