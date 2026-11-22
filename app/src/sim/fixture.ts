// /api/sim with a fixture fallback, until the server's endpoint lands.
// The fixtures are real payloads built from the bundle by
// fixtures/make_fixtures.py with the same rules (SPEC 3.2, 7.3); the UI shows
// a FIXTURE badge whenever one is used. Each fixture loads as its own chunk,
// only when it is needed.

import type { SimPayload } from './contract';

const loaders = import.meta.glob('./fixtures/*.json', { import: 'default' }) as Record<string, () => Promise<unknown>>;

export const FIXTURE_CASES: Record<string, { service: string; q: string }> = {
  lucknow_emoc: { service: 'emoc', q: '226001' },
  jokihat_dialysis: { service: 'dialysis', q: 'jokihat' },
  jaipur_dialysis: { service: 'dialysis', q: 'jaipur' },
  kotputli_trauma: { service: 'trauma', q: 'kotputli' },
};

export function fixtureFor(service: string, q: string): string | null {
  const qq = q.trim().toLowerCase();
  for (const [name, c] of Object.entries(FIXTURE_CASES)) if (c.service === service && c.q === qq) return name;
  return null;
}

export async function loadFixture(name: string): Promise<SimPayload> {
  const load = loaders[`./fixtures/sim_${name}.json`];
  if (!load) throw new Error(`no fixture ${name}`);
  return (await load()) as SimPayload;
}

export interface SimQuery { service: string; q: string; state?: string; place_id?: string; demo?: boolean }

/**
 * The /api/sim payload for a query. Falls back to a fixture (fixture: true)
 * only when the endpoint is missing or fails and the query is one of the
 * fixture cases; otherwise null.
 */
export async function fetchSim(qy: SimQuery, base = 'api/'): Promise<{ payload: SimPayload; fixture: boolean } | null> {
  const sp = new URLSearchParams({ service: qy.service, q: qy.q });
  if (qy.state) sp.set('state', qy.state);
  if (qy.place_id) sp.set('place_id', qy.place_id);
  if (qy.demo) sp.set('demo', '1');
  try {
    const r = await fetch(`${base}sim?${sp}`);
    if (r.ok) {
      const j = (await r.json()) as SimPayload;
      if (j && (j.status === 'ok' || j.status === 'unavailable')) return { payload: j, fixture: false };
    }
  } catch {
    // fall through to the fixture
  }
  const name = fixtureFor(qy.service, qy.q);
  return name ? { payload: await loadFixture(name), fixture: true } : null;
}
