// Talks to dbapp/server.py. Every endpoint takes the same query parameters:
//   service   one of the 12 service ids, or "other" for the keyword fallback
//   text      the free-text service, only with service=other
//   q         what the user typed in "Where is the patient?"
//   place_id  optional, a candidate the user picked after an ambiguous place
//   demo      optional, demo=1 asks the server to mask names and phones
//
// Every answer comes from the server, which runs vf.rank and vf.locate on the bundle.
// The one exception is a dev build opened with ?fixtures=1: then answers come from
// app/src/fixtures/real/ (made from the real bundle by make_fixtures_v2.py, in the SPEC 3
// contract shape) and the screen shows a FIXTURE badge. Production builds have no fixture path.

import type { Answer, Query, Resolution, Service, SimPayload, Simulate, Trip } from './types';

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

function params(q: Partial<Query>): URLSearchParams {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v != null && v !== '') p.set(k, String(v));
  return p;
}

// ------------------------------------------------------------------ fixtures (dev only)

const wantFixtures = import.meta.env.DEV && new URLSearchParams(location.search).has('fixtures');

/** True when this page reads fixtures instead of the server (dev only, ?fixtures=1). */
export function usingFixtures(): boolean {
  return wantFixtures;
}

type Loader = () => Promise<unknown>;
let fixtureFiles: Record<string, Loader> | null = null;

async function fixture(path: string, p: URLSearchParams): Promise<unknown | null> {
  if (!import.meta.env.DEV) return null;
  if (!fixtureFiles) {
    const raw = import.meta.glob('./fixtures/real/*.json', { import: 'default' }) as Record<string, Loader>;
    fixtureFiles = {};
    for (const [k, v] of Object.entries(raw)) fixtureFiles[k.replace(/^.*\//, '').replace(/\.json$/, '')] = v;
  }
  const files = fixtureFiles;
  const load = async (name: string) => (files[name] ? await files[name]() : null);
  if (path === 'services') return load('services');
  const cases = ((await load('cases')) ?? {}) as Record<string, { service: string; q: string }>;
  const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');
  const q = norm(p.get('q') ?? '');
  const service = p.get('service') ?? '';
  if (path === 'resolve') {
    if (q.startsWith('aurangabad')) return load('resolve_aurangabad');
    const key = Object.keys(cases).find((k) => norm(cases[k].q) === q || norm(cases[k].q).split(',')[0] === q);
    return key ? load(`resolve_${key}`) : { status: 'not_found', label: '', lat: null, lon: null, precision_km: null,
      how: `No fixture for "${p.get('q')}". Fixtures: ${Object.values(cases).map((c) => `${c.service} near ${c.q}`).join('; ')}.` };
  }
  const key = Object.keys(cases).find((k) => cases[k].service === service
    && (norm(cases[k].q) === q || norm(cases[k].q).split(',')[0] === q));
  if (!key) {
    if (path === 'sim') return { status: 'unavailable', reason: 'No fixture for this query.' };
    throw new ApiError(`No fixture for ${service} near "${p.get('q')}". Fixtures: ${Object.values(cases).map((c) => `${c.service} near ${c.q}`).join('; ')}.`);
  }
  return load(`${path}_${key}`);
}

// ------------------------------------------------------------------ requests

async function get<T>(path: string, p: URLSearchParams): Promise<T> {
  if (wantFixtures) {
    const f = await fixture(path, p);
    if (f != null) return f as T;
    throw new ApiError(`No fixture for /api/${path}.`, 404);
  }
  const qs = p.toString();
  let res: Response;
  try {
    res = await fetch(`./api/${path}${qs ? `?${qs}` : ''}`, { headers: { Accept: 'application/json' } });
  } catch {
    throw new ApiError('The server did not answer. Check the connection and try again.');
  }
  if (!res.ok) {
    let msg = `The server returned an error (${res.status}).`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') msg = body.detail;
    } catch { /* not JSON */ }
    throw new ApiError(msg, res.status);
  }
  return (await res.json()) as T;
}

/** The service list, and whether /api/simulate can answer (only once WP13 probabilities exist). */
export async function boot(): Promise<{ services: Service[]; simulateAvailable: boolean }> {
  const r = await get<{ services: Service[]; simulate_available?: boolean }>('services', new URLSearchParams());
  const list = r.services;
  if (!Array.isArray(list) || list.length === 0) throw new ApiError('The server returned no services.');
  return { services: list, simulateAvailable: r.simulate_available === true };
}

export const resolve = (q: string) => get<Resolution>('resolve', params({ q }));
export const answer = (q: Query) => get<Answer>('answer', params(q));
/** Retired by SPEC 3.2: the UI no longer calls it. Kept for the old map code until B's MapHost lands. */
export const trip = (q: Query) => get<Trip>('trip', params(q));
export const sim = (q: Query) => get<SimPayload>('sim', params(q));

/** The world for the agent simulator, or null when it is unavailable. An error never blocks the answer. */
export async function simOrNull(q: Query): Promise<SimPayload | null> {
  try {
    const s = await sim(q);
    return s && s.status === 'ok' && Array.isArray(s.facilities) ? s : null;
  } catch {
    return null;
  }
}

/** null when simulation is not available (no p_offers yet, or the endpoint is missing). */
export async function simulate(q: Query): Promise<Simulate | null> {
  try {
    const s = await get<Simulate>('simulate', params(q));
    if (!s || s.status !== 'ok' || !Array.isArray(s.policies) || s.policies.length === 0) return null;
    return s;
  } catch {
    return null;
  }
}
