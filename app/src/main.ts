// The query screen (tasks/WP8_databricks_app.md, "The screen"): service and
// place in, three cited cards or an honest abstention out, "How we decided",
// and the small map with the trip view.

import './style.css';
import * as api from './api';
import { MiniMap, type TripMode } from './minimap';
import { howHtml, locationLine, resultsHtml, withinRadiusFirst } from './render';
import { count, esc, pct, servicePhrase } from './text';
import type { Answer, Candidate, Query, Resolution, Service, Simulate, Trip } from './types';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

const els = {
  form: $<HTMLFormElement>('query'),
  service: $<HTMLSelectElement>('service'),
  otherWrap: $('other-wrap'),
  other: $<HTMLInputElement>('other'),
  place: $<HTMLInputElement>('place'),
  go: $<HTMLButtonElement>('go'),
  examples: $('examples'),
  loc: $('loc'),
  results: $('results'),
  mapEmpty: $('map-empty'),
  scale: $('scale'),
  controls: $('map-controls'),
  togTrips: $<HTMLButtonElement>('tog-trips'),
  togClaims: $<HTMLButtonElement>('tog-claims'),
  togSim: $<HTMLButtonElement>('tog-sim'),
  replay: $<HTMLButtonElement>('replay'),
  counter: $('trip-counter'),
  simPanel: $('sim-panel'),
};

const OTHER = 'other';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let services: Service[] = [];
let simulateAvailable = false;
let map: MiniMap;
let current: { answer: Answer; trip: Trip | null; sim: Simulate | null } | null = null;
let candidates: Candidate[] = [];
let lastQuery: Query | null = null;
let runId = 0;

// ------------------------------------------------------------------ boot

async function boot(): Promise<void> {
  const params = new URLSearchParams(location.search);
  map = new MiniMap($('map'));
  map.onPick = (kind, idx) => highlight(kind, idx, true);
  map.onView = drawScale;
  try {
    const boot = await api.boot();
    services = boot.services;
    simulateAvailable = boot.simulateAvailable;
  } catch (e) {
    els.results.innerHTML = `<div class="banner banner-error" role="alert"><b>The app could not reach its server.</b> ${esc((e as Error).message)}</div>`;
    els.go.disabled = true;
    return;
  }
  els.service.innerHTML = services.map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('')
    + `<option value="${OTHER}">Something else (type it)</option>`;
  renderExamples();
  renderIntro();

  els.service.addEventListener('change', syncOther);
  els.form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    submit();
  });
  els.examples.addEventListener('click', (ev) => {
    const b = (ev.target as HTMLElement).closest('button[data-ex]') as HTMLButtonElement | null;
    if (!b) return;
    els.service.value = b.dataset.service!;
    els.other.value = b.dataset.text ?? '';
    els.place.value = b.dataset.q!;
    syncOther();
    submit();
  });
  els.loc.addEventListener('click', (ev) => {
    const b = (ev.target as HTMLElement).closest('button[data-cand]') as HTMLButtonElement | null;
    if (!b || !lastQuery) return;
    const c = candidates[Number(b.dataset.cand)];
    if (!c) return;
    els.place.value = c.label;
    run({ ...lastQuery, q: c.place_id ? lastQuery.q : c.label, place_id: c.place_id ?? null });
  });
  els.results.addEventListener('click', (ev) => {
    const b = (ev.target as HTMLElement).closest('[data-focus]') as HTMLElement | null;
    if (b) highlight('result', Number(b.dataset.focus), false);
  });
  els.results.addEventListener('mouseover', (ev) => {
    const card = (ev.target as HTMLElement).closest('.card') as HTMLElement | null;
    if (card) map.focus(Number(card.dataset.idx));
  });
  els.results.addEventListener('mouseleave', () => map.focus(null));
  els.togTrips.addEventListener('click', () => toggle('trips'));
  els.togClaims.addEventListener('click', () => toggle('claims'));
  els.togSim.addEventListener('click', () => toggle('sim'));
  els.replay.addEventListener('click', () => { map.replay(); if (reducedMotion) map.settle(); });
  els.simPanel.addEventListener('click', (ev) => {
    const b = (ev.target as HTMLElement).closest('button[data-policy]') as HTMLButtonElement | null;
    if (b) playSim(b.dataset.policy!);
  });

  // Deep link: ?service=dialysis&q=Jaipur runs at once (used for recording and screenshots).
  const svc = params.get('service'), q = params.get('q');
  if (svc && q) {
    els.service.value = svc;
    els.other.value = params.get('text') ?? '';
    els.place.value = q;
    syncOther();
    run({ service: svc, q, text: params.get('text') ?? undefined, place_id: params.get('place_id') });
  }
  (window as unknown as { __vf: unknown }).__vf = { map, settle: () => map.settle(), seek: (t: number) => map.seek(t) };
}

function syncOther(): void {
  const other = els.service.value === OTHER;
  els.otherWrap.hidden = !other;
  els.other.required = other;
}

function renderExamples(): void {
  const label = (id: string) => services.find((s) => s.id === id)?.label ?? id;
  // The brief's five questions (docs/00_brief.md), with the places the scenario suite uses
  // (data/labels/scenarios.csv): Jokihat is a rural block in Araria, Bihar; Kotputli is on NH48.
  const ex: { service: string; q: string; text?: string; show: string }[] = [
    { service: 'dialysis', q: 'Jaipur', show: `${label('dialysis')} near Jaipur` },
    { service: 'emoc', q: 'Lucknow', show: 'Emergency C-section near Lucknow' },
    { service: 'dialysis', q: 'Jokihat, Bihar', show: `${label('dialysis')} from Jokihat, rural Bihar` },
    { service: 'emoc', q: 'Nashik', show: 'High-risk delivery in Nashik, Maharashtra' },
    { service: 'trauma', q: 'Kotputli', show: 'Trauma care near Kotputli on NH48' },
  ].filter((e) => services.some((s) => s.id === e.service));
  els.examples.innerHTML = `<span class="ex-label">Try</span>` + ex.map((e) =>
    `<button type="button" class="ex" data-ex="1" data-service="${esc(e.service)}" data-q="${esc(e.q)}"${e.text ? ` data-text="${esc(e.text)}"` : ''}>${esc(e.show)}</button>`).join('');
}

function renderIntro(): void {
  els.loc.innerHTML = '';
  els.results.innerHTML = `<div class="intro">
    <h2>Which facility should the patient go to, and why should anyone believe it?</h2>
    <ol>
      <li>Pick the care that is needed and type where the patient is.</li>
      <li>We show up to three facilities whose own records name that service, and quote the words that show it.</li>
      <li>Each one says how sure we are and what could make it wrong. If nothing reliable is near, we say so instead of guessing.</li>
    </ol>
  </div>`;
}

// ------------------------------------------------------------------- run

function submit(): void {
  const service = els.service.value;
  const q = els.place.value.trim();
  if (!q) { els.place.focus(); return; }
  const text = service === OTHER ? els.other.value.trim() : undefined;
  if (service === OTHER && !text) { els.other.focus(); return; }
  run({ service, q, text, place_id: null });
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

async function run(q: Query): Promise<void> {
  const id = ++runId;
  lastQuery = q;
  setUrl(q);
  els.go.disabled = true;
  els.results.setAttribute('aria-busy', 'true');
  els.results.innerHTML = `<div class="loading"><span class="spinner" aria-hidden="true"></span>Looking for ${esc(q.service === OTHER ? `"${q.text}"` : servicePhrase(services.find((s) => s.id === q.service)?.label ?? ''))}</div>`;
  resetMapUi();
  try {
    if (!q.place_id) {
      const r = await api.resolve(q.q);
      if (id !== runId) return;
      if (r.status !== 'ok') return showLocationProblem(r, q.q);
      els.loc.innerHTML = locationLine(r, q.q);
      // place_id is sent only when the user picked a candidate; otherwise the server resolves q itself.
    }
    const a0 = await api.answer(q);
    if (id !== runId) return;
    const a = a0.results ? withinRadiusFirst(a0) : a0;
    if (a.status === 'needs_location_choice' || a.status === 'location_not_found') return showLocationProblem(a.location, q.q);
    els.loc.innerHTML = locationLine(a.location, q.q);
    els.results.innerHTML = resultsHtml(a) + howHtml(a);
    current = { answer: a, trip: null, sim: null };
    map.setAnswer(a, null, null);
    els.mapEmpty.hidden = true;
    const [t, s] = await Promise.all([
      q.service === OTHER ? Promise.resolve(null) : api.trip(q).catch(() => null),
      simulateAvailable && q.service !== OTHER ? api.simulate(q) : Promise.resolve(null),
    ]);
    if (id !== runId) return;
    current = { answer: a, trip: t && t.status === 'ok' ? t : null, sim: s };
    map.setAnswer(a, current.trip, s);
    renderMapControls();
  } catch (e) {
    if (id !== runId) return;
    els.results.innerHTML = `<div class="banner banner-error" role="alert"><b>No answer.</b> ${esc((e as Error).message)}</div>`;
    map.clear();
    els.mapEmpty.hidden = false;
  } finally {
    if (id === runId) {
      els.go.disabled = false;
      els.results.removeAttribute('aria-busy');
    }
  }
}

function showLocationProblem(r: Resolution, typed: string): void {
  candidates = r.candidates ?? [];
  els.loc.innerHTML = locationLine(r, typed);
  els.results.innerHTML = r.status === 'ambiguous'
    ? `<p class="hint">Pick one of the places above to see results.</p>` : '';
  map.clear();
  current = null;
  els.mapEmpty.hidden = false;
  const first = els.loc.querySelector('button') as HTMLButtonElement | null;
  first?.focus();
}

// -------------------------------------------------------------- map UI

function resetMapUi(): void {
  els.controls.hidden = true;
  els.counter.innerHTML = '';
  els.simPanel.hidden = true;
  els.simPanel.innerHTML = '';
  for (const b of [els.togTrips, els.togClaims, els.togSim]) b.setAttribute('aria-pressed', 'false');
  els.replay.hidden = true;
}

function renderMapControls(): void {
  const t = current?.trip;
  const s = current?.sim;
  const hasTrip = !!t && t.points.length > 0;
  els.controls.hidden = !hasTrip && !s;
  els.togTrips.hidden = !hasTrip || !t!.nearest_recommended.some((x) => x >= 0);
  els.togClaims.hidden = !hasTrip;
  els.togSim.hidden = !s;
  if (!hasTrip && !s) {
    els.counter.innerHTML = current ? `<p class="muted">No post office data for a trip view here.</p>` : '';
  }
}

function toggle(mode: TripMode): void {
  const btn = mode === 'trips' ? els.togTrips : mode === 'claims' ? els.togClaims : els.togSim;
  const on = btn.getAttribute('aria-pressed') !== 'true';
  for (const b of [els.togTrips, els.togClaims, els.togSim]) b.setAttribute('aria-pressed', 'false');
  els.simPanel.hidden = true;
  if (!on) {
    map.setMode('none');
    els.counter.innerHTML = '';
    els.replay.hidden = true;
    return;
  }
  btn.setAttribute('aria-pressed', 'true');
  if (mode === 'sim') {
    renderSimPanel();
    const first = current?.sim?.policies[0]?.policy;
    if (first) playSim(first);
    return;
  }
  const ok = map.setMode(mode);
  if (ok && reducedMotion) map.settle();
  els.replay.hidden = !ok;
  els.counter.innerHTML = ok && mode !== 'none' ? tripCounter(mode) : `<p class="muted">The trip view has nothing to show here.</p>`;
}

function tripCounter(mode: 'trips' | 'claims'): string {
  const t = current!.trip!;
  const phrase = servicePhrase(current!.answer.service_label);
  const sm = t.summary;
  const n = sm?.n_points ?? t.points.length;
  const notes = (t.notes ?? []).map((x) => `<li>${esc(x)}</li>`).join('');
  if (mode === 'trips') {
    const where = sm?.recommended_beyond_radius
      ? `the reliable option shown, beyond ${count(t.radius_km)} km`
      : 'their nearest recommended facility';
    return `<p><span class="sw sw-reached" aria-hidden="true"></span><b>${count(n)}</b> post office areas within ${count(t.radius_km)} km travel to ${where}.</p>${notes ? `<ul class="notes">${notes}</ul>` : ''}`;
  }
  if (!sm) return `<p>Dots first go to the nearest facility that claims ${esc(phrase)}.</p>`;
  const extra = sm.extra_km_mean;
  const tail = extra != null && Number.isFinite(extra)
    ? `, then travel <b>${esc(extra.toFixed(1))} km</b> more on average (straight line)`
    : '';
  const lead = sm.n_first_unsupported > 0
    ? `<b>${count(sm.n_first_unsupported)}</b> of ${count(n)} post office areas would first go to a facility without reliable evidence of ${esc(phrase)}${tail}.`
    : `None of the ${count(n)} post office areas would first go to a facility without reliable evidence of ${esc(phrase)}.`;
  return `<p><span class="sw sw-stranded" aria-hidden="true"></span>${lead}</p>${unsupportedSplit(sm, phrase)}${notes ? `<ul class="notes">${notes}</ul>` : ''}`;
}

/** Name the two kinds of first stop apart: a record that only claims the service has no evidence
 * of it; a record held back by a blocking warning does quote evidence, so it must not be called
 * "no evidence". */
function unsupportedSplit(sm: NonNullable<Trip['summary']>, phrase: string): string {
  const co = sm.n_first_claim_only, hb = sm.n_first_held_back;
  if (co == null || hb == null || sm.n_first_unsupported <= 0) return '';
  const parts: string[] = [];
  if (co > 0) parts.push(`<b>${count(co)}</b> go to a facility that only claims it, with no evidence of ${esc(phrase)} in its record`);
  if (hb > 0) parts.push(`<b>${count(hb)}</b> go to a facility whose evidence is held back by a blocking warning`);
  return `<p class="muted">Of these, ${parts.join('; ')}.</p>`;
}

function renderSimPanel(): void {
  const s = current?.sim;
  if (!s) return;
  const rows = s.policies.map((p) => `<tr>
      <th scope="row"><button type="button" class="pol" data-policy="${esc(p.policy)}" aria-pressed="false">${esc(p.label ?? p.policy)}</button></th>
      <td>${p.expected_km != null && Number.isFinite(p.expected_km) ? `${esc(p.expected_km.toFixed(1))} km` : 'n/a'}</td><td>${p.share_second_trip != null && Number.isFinite(p.share_second_trip) ? esc(pct(p.share_second_trip)) : 'n/a'}</td></tr>`).join('');
  const notes = (s.notes ?? []).map((x) => `<li>${esc(x)}</li>`).join('');
  els.simPanel.innerHTML = `<h4>Simulated arrivals${s.n_origins != null ? ` from ${count(s.n_origins)} post office areas` : ''}</h4>
    <table><thead><tr><th scope="col">Where patients go first</th><th scope="col">Expected km to real care</th><th scope="col">Need a second trip</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted">A dot stops where the service is found and moves on where it is not. Straight-line km.</p>
    ${notes ? `<ul class="notes">${notes}</ul>` : ''}`;
  els.simPanel.hidden = false;
  els.counter.innerHTML = '';
}

function playSim(policy: string): void {
  els.simPanel.querySelectorAll('button[data-policy]').forEach((b) =>
    b.setAttribute('aria-pressed', String((b as HTMLElement).dataset.policy === policy)));
  const ok = map.setMode('sim', policy);
  if (ok && reducedMotion) map.settle();
  els.replay.hidden = !ok;
}

// ----------------------------------------------------- card <-> marker

function highlight(kind: 'result' | 'unverified', idx: number, fromMap: boolean): void {
  els.results.querySelectorAll('.is-focus').forEach((n) => n.classList.remove('is-focus'));
  const node = kind === 'result'
    ? els.results.querySelector<HTMLElement>(`.card[data-idx="${idx}"]`)
    : els.results.querySelector<HTMLElement>(`[data-unv="${idx}"]`);
  if (!node) return;
  node.classList.add('is-focus');
  if (kind === 'result') map.focus(idx);
  if (fromMap) {
    node.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'nearest' });
    if (node.classList.contains('card')) node.focus({ preventScroll: true });
  }
}

// ------------------------------------------------------------ scale bar

function drawScale(vs: { latitude: number; zoom: number }, _width: number): void {
  if (!current) { els.scale.innerHTML = ''; return; }
  const mPerPx = (40075016.686 * Math.cos((vs.latitude * Math.PI) / 180)) / (512 * 2 ** vs.zoom);
  const nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500];
  let km = nice[0];
  for (const k of nice) if ((k * 1000) / mPerPx <= 110) km = k;
  const px = (km * 1000) / mPerPx;
  els.scale.innerHTML = `<span class="scale-bar" style="width:${px.toFixed(0)}px"></span><span>${km} km</span>`;
}

boot();
