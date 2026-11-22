// The query screen (SPEC 4, "Answer first: the referral note"): service and place in,
// up to three cited facilities or an honest abstention out, the map beside it, and the
// village simulator behind the teaser.
//
// The map is B's MapHost (app/src/map/index.ts) and the simulator D's Simulator
// (app/src/sim/index.ts). Both are found with import.meta.glob, so this file builds and
// runs before they land: without MapHost the old MiniMap is wrapped to the same calls;
// without Simulator the teaser shows its sentence but no Play button.

import './style.css';
import * as api from './api';
import * as minimap from './minimap';
import {
  abstainHtml, ambiguousHtml, backupsHtml, chipsHtml, errorHtml, footerHtml, furtherLine, howHtml,
  noteHtml, originHtml, plannersHtml, problemHtml, teaserHtml, whatIfTeaserHtml, whyNotHtml,
} from './render';
import { bannedIn, esc, previewAddSentence, servicePhrase, shortPlace } from './text';
import type {
  Answer, Candidate, Card, MapExample, MapHostCtor, MapHostLike, Query, Resolution, Service, SimMode, SimPayload,
  SimulatorCtor, SimulatorLike, SimOpenOptions,
} from './types';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

const els = {
  qline: $('qline'),
  chips: $('chips'),
  change: $<HTMLButtonElement>('change'),
  ask: $('ask'),
  form: $<HTMLFormElement>('query'),
  service: $<HTMLSelectElement>('service'),
  otherWrap: $('other-wrap'),
  other: $<HTMLInputElement>('other'),
  place: $<HTMLInputElement>('place'),
  go: $<HTMLButtonElement>('go'),
  examples: $('examples'),
  intro: $('intro'),
  layout: $('main'),
  origin: $('origin'),
  note: $('note'),
  backups: $('backups'),
  folds: $('folds'),
  footer: $('footer'),
  map: $('map'),
  teaser: $('planners'),
};

/** Dev only: a red FIXTURE badge when answers come from app/src/fixtures/real. Never in a production build. */
function showFixtureBadge(on: boolean): void {
  let b = document.getElementById('fixture-badge');
  if (!on) { if (b) b.hidden = true; return; }
  if (!b) {
    b = document.createElement('span');
    b.id = 'fixture-badge';
    b.className = 'fixture-badge';
    b.textContent = 'FIXTURE';
    document.querySelector('.top .brand')?.after(b);
  }
  b.hidden = false;
}

const OTHER = 'other';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const pageParams = new URLSearchParams(location.search);
const DEMO = pageParams.get('demo') === '1' ? '1' : null;

interface Example { service: string; q: string; text?: string; show: string }

let services: Service[] = [];
let map: MapHostLike;
let SimCtor: SimulatorCtor | null = null;
let simulator: SimulatorLike | null = null;
let current: { answer: Answer; sim: SimPayload | null } | null = null;
let candidates: Candidate[] = [];
let lastQuery: Query | null = null;
let runId = 0;
let examples: Example[] = [];

// ------------------------------------------------------------------ the map and the simulator

/** The pre-v3 MiniMap (app/src/minimap.ts), as main.ts used it before MapHost. */
interface OldMiniMap {
  onPick: (kind: 'result' | 'unverified', idx: number) => void;
  setAnswer(a: Answer, trip: null, sim: null): void;
  clear(): void;
  focus(idx: number | null): void;
}

/** The old MiniMap behind the MapHost calls, until B's MapHost lands. */
function miniMapHost(el: HTMLElement, Ctor: new (el: HTMLElement) => OldMiniMap): MapHostLike {
  el.classList.add('map-fallback');
  const mm = new Ctor(el);
  const host: MapHostLike = {
    onPick: () => {},
    showLanding: () => mm.clear(),
    showCandidates: () => mm.clear(),
    showAnswer: (a) => mm.setAnswer(a, null, null),
    highlight: (kind, key) => { if (kind === 'result') mm.focus(typeof key === 'number' ? key : null); },
    focus: (kind, idx) => { if (kind === 'result') mm.focus(idx); },
    expand: () => null,
    collapse: () => {},
    isExpanded: () => false,
  };
  mm.onPick = (kind, idx) => { if (kind === 'result') host.onPick('result', idx); };
  return host;
}

async function makeMap(el: HTMLElement): Promise<MapHostLike> {
  // B may export MapHost from minimap.ts (SPEC 5.4: "minimap.ts becomes a re-export of map/index.ts")
  const mm = minimap as unknown as { MapHost?: MapHostCtor; MiniMap?: new (el: HTMLElement) => OldMiniMap };
  if (mm.MapHost) return new mm.MapHost(el);
  const mods = import.meta.glob('./map/index.ts');
  const load = mods['./map/index.ts'];
  if (load) {
    try {
      const m = (await load()) as { MapHost?: MapHostCtor };
      if (m.MapHost) return new m.MapHost(el);
    } catch (e) {
      console.warn('MapHost failed to load, using the old map', e);
    }
  }
  if (mm.MiniMap) return miniMapHost(el, mm.MiniMap);
  throw new Error('No map module in this build.');
}

async function loadSimCtor(): Promise<SimulatorCtor | null> {
  const mods = import.meta.glob('./sim/index.ts');
  const load = mods['./sim/index.ts'];
  if (!load) return null;
  try {
    const m = (await load()) as { Simulator?: SimulatorCtor };
    return m.Simulator ?? null;
  } catch (e) {
    console.warn('Simulator failed to load', e);
    return null;
  }
}

function openSim(opts: SimOpenOptions): void {
  if (!simulator) return;
  simulator.open({ ...opts, autoplay: reducedMotion ? false : opts.autoplay ?? true });
}

// ------------------------------------------------------------------ boot

async function boot(): Promise<void> {
  map = await makeMap(els.map);
  map.onPick = (kind, key) => onMapPick(kind, key);
  // Closing the large map (Escape, "Close map") also closes the simulation, so the next Play or what-if
  // opens the large map again with its controls; the swarm never runs in the small map.
  (map as MapHostLike & { onExpandChange?: (on: boolean) => void }).onExpandChange = (on: boolean) => {
    if (!on && simulator) simulator.close();
  };
  SimCtor = await loadSimCtor();
  if (import.meta.env.DEV) showFixtureBadge(api.usingFixtures());
  try {
    const b = await api.boot();
    services = b.services;
  } catch (e) {
    els.note.innerHTML = errorHtml(`The app could not reach its server. ${(e as Error).message}`);
    els.go.disabled = true;
    return;
  }
  els.service.innerHTML = services.map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('')
    + `<option value="${OTHER}">Something else (type it)</option>`;
  renderExamples();
  wire();
  const svc = pageParams.get('service'), q = pageParams.get('q');
  if (svc && q) {
    els.service.value = svc;
    els.other.value = pageParams.get('text') ?? '';
    els.place.value = q;
    syncOther();
    run({ service: svc, q, text: pageParams.get('text') ?? undefined, place_id: pageParams.get('place_id'), demo: DEMO });
  } else {
    showLanding();
  }
  (window as unknown as { __vf: Record<string, unknown> }).__vf = {
    ...((window as unknown as { __vf?: Record<string, unknown> }).__vf ?? {}),
    map, current: () => current, banned: () => bannedIn(document.body.innerText),
  };
}

function liveChips(): void {
  if (els.qline.hidden || els.ask.hidden) return;
  const service = els.service.value;
  setChips({ service, q: els.place.value.trim() || '...', text: service === OTHER ? els.other.value.trim() : undefined }, els.place.value.trim() || '...');
}

function wire(): void {
  els.service.addEventListener('change', syncOther);
  els.service.addEventListener('change', liveChips);
  els.place.addEventListener('input', liveChips);
  els.other.addEventListener('input', liveChips);
  els.form.addEventListener('submit', (ev) => { ev.preventDefault(); submit(); });
  els.change.addEventListener('click', () => {
    const open = els.ask.hidden;
    els.ask.hidden = !open;
    els.change.setAttribute('aria-expanded', String(open));
    els.change.textContent = open ? 'Close' : 'Change';
    if (open) els.place.focus();
  });
  els.examples.addEventListener('click', (ev) => {
    const b = (ev.target as HTMLElement).closest('button[data-ex]') as HTMLButtonElement | null;
    if (b) runExample(Number(b.dataset.ex));
  });
  const main = els.layout;
  main.addEventListener('click', (ev) => {
    const t = ev.target as HTMLElement;
    const r = t.closest('[data-focus-result]') as HTMLElement | null;
    if (r) { focusResult(Number(r.dataset.focusResult)); showMapOnPhone(); return; }
    const f = t.closest('[data-focus-further]') as HTMLElement | null;
    if (f) { map.focus('further', Number(f.dataset.focusFurther)); showMapOnPhone(); return; }
    const rv = t.closest('button[data-reveal]') as HTMLButtonElement | null;
    if (rv) { revealPhones(rv.dataset.reveal!); return; }
    const v = t.closest('button[data-verify]') as HTMLButtonElement | null;
    if (v) { openSim({ mode: 'evidence', edit: { type: 'verify', facility_id: v.dataset.verify! } }); return; }
    const s = t.closest('button[data-sim]') as HTMLButtonElement | null;
    if (s) { simButton(s.dataset.sim!); return; }
    const c = t.closest('button[data-cand]') as HTMLButtonElement | null;
    if (c) pickCandidate(Number(c.dataset.cand));
  });
  main.addEventListener('mouseover', (ev) => {
    const t = ev.target as HTMLElement;
    const wn = t.closest('[data-whynot]') as HTMLElement | null;
    if (wn) { map.highlight('whynot', wn.dataset.whynot!); return; }
    const card = t.closest('.note[data-idx], .bk[data-idx], .nearest[data-idx]') as HTMLElement | null;
    if (card) map.highlight('result', Number(card.dataset.idx));
  });
  main.addEventListener('mouseleave', () => { map.highlight('result', null); map.highlight('whynot', null); });
}

/** On a phone the map is below the note: bring it into view so a tap on a name visibly does something. */
function showMapOnPhone(): void {
  if (!window.matchMedia('(max-width: 760px)').matches) return;
  const r = els.map.getBoundingClientRect();
  if (r.top < 0 || r.bottom > window.innerHeight) els.map.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
  els.map.classList.remove('pulse');
  void els.map.offsetWidth;
  els.map.classList.add('pulse');
}

/** "Show number to call": ask the server once for the tel: links (never given in demo mode), then re-draw the cards. */
async function revealPhones(fid: string): Promise<void> {
  if (!current || !lastQuery) return;
  const btns = els.layout.querySelectorAll<HTMLButtonElement>('button[data-reveal]');
  btns.forEach((b) => { b.disabled = true; });
  try {
    const r = normAnswer(await api.answer({ ...lastQuery, reveal: '1' }));
    const tel = new Map<string, Card['call']>();
    for (const c of [...r.results, ...(r.beyond_radius ? [r.beyond_radius] : []), ...(r.further_away ?? [])]) if (c.call) tel.set(c.facility_id, c.call);
    const a = current.answer;
    for (const c of [...a.results, ...(a.beyond_radius ? [a.beyond_radius] : []), ...(a.further_away ?? [])]) {
      const t = tel.get(c.facility_id);
      if (t) c.call = t;
    }
    renderAnswer(a, current.sim);
    els.layout.querySelector<HTMLElement>(`a[data-call="${CSS.escape(fid)}"]`)?.focus();
  } catch (e) {
    btns.forEach((b) => { b.disabled = false; });
    els.note.insertAdjacentHTML('beforeend', errorHtml(`The number could not be loaded. ${(e as Error).message}`));
  }
}

function syncOther(): void {
  const other = els.service.value === OTHER;
  els.otherWrap.hidden = !other;
  els.other.required = other;
}

function renderExamples(): void {
  const label = (id: string) => services.find((s) => s.id === id)?.label ?? id;
  // The brief's questions (docs/00_brief.md) with the places the scenario suite uses.
  examples = [
    { service: 'emoc', q: '226001', show: 'Emergency C-section near Lucknow, PIN 226001' },
    { service: 'dialysis', q: 'Jokihat, Bihar', show: `${label('dialysis')} from Jokihat, rural Bihar` },
    { service: 'dialysis', q: 'Jaipur', show: `${label('dialysis')} near Jaipur` },
    { service: 'trauma', q: 'Kotputli', show: 'Trauma care near Kotputli on NH48' },
  ].filter((e) => services.some((s) => s.id === e.service));
  els.examples.innerHTML = `<span class="ex-label">Or try one:</span>` + examples.map((e, i) =>
    `<button type="button" class="ex" data-ex="${i}">${esc(e.show)}</button>`).join('');
}

function runExample(i: number): void {
  const e = examples[i];
  if (!e) return;
  els.service.value = e.service;
  els.other.value = e.text ?? '';
  els.place.value = e.q;
  syncOther();
  submit();
}

async function showLanding(): Promise<void> {
  els.layout.className = 'layout is-landing';
  els.qline.hidden = true;
  els.ask.hidden = false;
  els.intro.hidden = false;
  for (const el of [els.origin, els.note, els.backups, els.folds, els.teaser]) el.innerHTML = '';
  els.footer.innerHTML = footerHtml();
  const pts: (MapExample | null)[] = await Promise.all(examples.map(async (e) => {
    try {
      const r = await api.resolve(e.q);
      return r.status === 'ok' && r.lat != null && r.lon != null ? { label: e.show, lat: r.lat, lon: r.lon } : null;
    } catch { return null; }
  }));
  const ok = pts.map((p, i) => ({ p, i })).filter((x) => x.p) as { p: MapExample; i: number }[];
  map.showLanding(ok.map((x) => x.p), (k) => runExample(ok[k]?.i ?? -1));
}

// ------------------------------------------------------------------ run

function submit(): void {
  const service = els.service.value;
  const q = els.place.value.trim();
  if (!q) { els.place.focus(); return; }
  const text = service === OTHER ? els.other.value.trim() : undefined;
  if (service === OTHER && !text) { els.other.focus(); return; }
  run({ service, q, text, place_id: null, demo: DEMO });
}

function setUrl(q: Query): void {
  const p = new URLSearchParams(location.search);
  for (const k of ['service', 'q', 'text', 'place_id']) p.delete(k);
  p.set('service', q.service);
  p.set('q', q.q);
  if (q.text) p.set('text', q.text);
  if (q.place_id) p.set('place_id', q.place_id);
  history.replaceState(null, '', `?${p.toString()}`);
}

function serviceLabel(q: Query): string {
  return q.service === OTHER ? `"${q.text ?? ''}"` : servicePhrase(services.find((s) => s.id === q.service)?.label ?? '');
}

function setChips(q: Query, place: string): void {
  const care = q.service === OTHER ? `"${q.text ?? ''}"` : (services.find((s) => s.id === q.service)?.label ?? q.service)
    .replace(/\s+and high-risk delivery$/i, '').replace(/\s*\([^)]*\)\s*$/, '');
  els.chips.innerHTML = `<span class="qchip"><em>Care</em>${esc(care)}</span><span class="qchip"><em>Near</em>${esc(place)}</span>`;
}

async function run(q: Query): Promise<void> {
  const id = ++runId;
  lastQuery = q;
  setUrl(q);
  simulator?.close();
  simulator = null;
  current = null;
  els.go.disabled = true;
  els.layout.className = 'layout is-results';
  els.ask.hidden = true;
  els.intro.hidden = true;
  els.change.textContent = 'Change';
  els.change.setAttribute('aria-expanded', 'false');
  els.qline.hidden = false;
  setChips(q, q.q);
  els.note.setAttribute('aria-busy', 'true');
  els.origin.innerHTML = '';
  els.note.innerHTML = `<div class="loading"><span class="spinner" aria-hidden="true"></span>Looking for ${esc(serviceLabel(q))}</div>`;
  for (const el of [els.backups, els.folds, els.teaser]) el.innerHTML = '';
  try {
    if (!q.place_id) {
      const r = await api.resolve(q.q);
      if (id !== runId) return;
      if (r.status !== 'ok') return showLocationProblem(r, q.q);
    }
    const simP = q.service === OTHER ? Promise.resolve(null) : api.simOrNull(q);
    const a0 = await api.answer(q);
    if (id !== runId) return;
    if (a0.status === 'needs_location_choice' || a0.status === 'location_not_found') return showLocationProblem(a0.location, q.q);
    const a = normAnswer(a0);
    current = { answer: a, sim: null };
    if (import.meta.env.DEV) showFixtureBadge(api.usingFixtures() || !!a.fixture);
    renderAnswer(a, null);
    map.showAnswer(a, null);
    // keyboard and screen-reader users land on the answer, not back at the top of the page
    els.note.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
    const s = await simP;
    if (id !== runId) return;
    current = { answer: a, sim: s };
    if (s) {
      if (SimCtor) {
        try {
          simulator = new SimCtor(map);
          simulator.load(s, a);
        } catch (e) {
          console.warn('Simulator failed', e);
          simulator = null;
        }
      }
      renderAnswer(a, s);
      map.showAnswer(a, s);
    }
  } catch (e) {
    if (id !== runId) return;
    els.note.innerHTML = errorHtml((e as Error).message);
    map.showLanding([], () => {});
  } finally {
    if (id === runId) {
      els.go.disabled = false;
      els.note.removeAttribute('aria-busy');
    }
  }
}

/** One answer shape for the screen and the map: the beyond card is listed once (SPEC 3.1). */
function normAnswer(a: Answer): Answer {
  const bid = a.beyond_radius?.facility_id;
  const further = (a.further_away ?? []).filter((c) => c.facility_id !== bid);
  return { ...a, results: a.results ?? [], further_away: further };
}

function renderAnswer(a: Answer, s: SimPayload | null): void {
  const canRun = !!simulator;
  els.chips.innerHTML = chipsHtml(a);
  els.origin.innerHTML = originHtml(a);
  if (a.status === 'no_reliable_option' || !a.results.length) {
    els.layout.classList.add('is-abstain');
    els.note.innerHTML = abstainHtml(a);
    els.backups.innerHTML = '';
  } else {
    els.layout.classList.remove('is-abstain');
    els.note.innerHTML = noteHtml(a);
    els.backups.innerHTML = backupsHtml(a) + furtherLine(a, a.further_away ?? []);
  }
  els.folds.innerHTML = whyNotHtml(a, canRun) + howHtml(a);
  els.footer.innerHTML = footerHtml();
  const isAbstain = a.status === 'no_reliable_option' || !a.results.length;
  els.teaser.innerHTML = plannersHtml(teaserHtml(s, canRun), isAbstain ? whatIfTeaser(a, s) : '');
}

/** SPEC 4.9 item 8: only when the simulator says a unit at the origin would bring villages within the window. */
function whatIfTeaser(a: Answer, s: SimPayload | null): string {
  if (!s || !simulator) return '';
  try {
    const r = simulator.previewAdd({ lat: s.origin.lat, lon: s.origin.lon });
    if (!r || !(r.after.within > r.before.within)) return '';
    return whatIfTeaserHtml(previewAddSentence(s, a.origin?.short_name ?? shortPlace(a), r.before, r.after));
  } catch {
    return '';
  }
}

function simButton(kind: string): void {
  const s = current?.sim;
  if (!s) return;
  // The run opens on the rule the answer itself uses; the "specialty claims alone" rule is the comparison.
  if (kind === 'play') openSim({ mode: 'evidence' as SimMode, autoplay: true });
  else if (kind === 'whatif') openSim({ mode: 'evidence', tool: 'verify', autoplay: false });
  else if (kind === 'add') openSim({ mode: 'evidence', autoplay: true, edit: { type: 'add', lon: s.origin.lon, lat: s.origin.lat } });
}

function showLocationProblem(r: Resolution, typed: string): void {
  candidates = r.candidates ?? [];
  current = null;
  els.origin.innerHTML = '';
  els.backups.innerHTML = els.folds.innerHTML = els.teaser.innerHTML = '';
  els.footer.innerHTML = '';
  if (r.status === 'ambiguous' && candidates.length) {
    els.note.innerHTML = ambiguousHtml(r, typed);
    map.showCandidates(candidates, (i) => pickCandidate(i));
    (els.note.querySelector('h1') as HTMLElement | null)?.focus({ preventScroll: true });
  } else {
    els.note.innerHTML = problemHtml(r, typed);
    els.ask.hidden = false;
    els.note.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
    map.showLanding([], () => {});
  }
}

function pickCandidate(i: number): void {
  const c = candidates[i];
  if (!c || !lastQuery) return;
  els.place.value = c.label;
  run({ ...lastQuery, q: c.place_id ? lastQuery.q : c.label, place_id: c.place_id ?? null });
}

// ------------------------------------------------------------------ list <-> map

function focusResult(i: number): void {
  map.focus('result', i);
  mark(els.layout.querySelector<HTMLElement>(`.note[data-idx="${i}"], .bk[data-idx="${i}"], .nearest[data-idx="${i}"]`), false);
}

function onMapPick(kind: 'result' | 'whynot' | 'further', key: number | string): void {
  let node: HTMLElement | null = null;
  if (kind === 'result') node = els.layout.querySelector(`.note[data-idx="${key}"], .bk[data-idx="${key}"], .nearest[data-idx="${key}"]`);
  else if (kind === 'further') node = els.layout.querySelector(`[data-further="${key}"], [data-focus-further="${key}"]`);
  else {
    const d = els.folds.querySelector<HTMLDetailsElement>('details.whynot');
    if (d) d.open = true;
    node = els.folds.querySelector(`[data-whynot="${key}"]`);
  }
  mark(node, true);
}

function mark(node: HTMLElement | null, scroll: boolean): void {
  els.layout.querySelectorAll('.is-focus').forEach((n) => n.classList.remove('is-focus'));
  if (!node) return;
  node.classList.add('is-focus');
  if (scroll) node.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'nearest' });
}

boot();
