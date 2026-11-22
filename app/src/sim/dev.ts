// Entry of the standalone dev page app/sim-dev.html (not shipped).
// URL: sim-dev.html?case=lucknow_emoc&mode=claims&k=3&phone=1&autoplay=0
// Tries /api/sim first; falls back to the fixture of the same case (FIXTURE badge).

import { FIXTURE_CASES, fetchSim } from './fixture';
import { DevMapHost } from './devmap';
import { h } from './panel';
import { Simulator, type Mode } from './index';

async function main(): Promise<void> {
  const u = new URL(location.href);
  const name = u.searchParams.get('case') ?? 'lucknow_emoc';
  const c = FIXTURE_CASES[name] ?? FIXTURE_CASES.lucknow_emoc;
  const phone = u.searchParams.get('phone') === '1';
  const k = u.searchParams.get('k');
  const mode = (u.searchParams.get('mode') as Mode) || 'evidence';
  const root = document.getElementById('app')!;
  const host = new DevMapHost(root, phone);
  const got = await fetchSim({ service: c.service, q: c.q, demo: u.searchParams.get('demo') === '1' });
  if (!got || got.payload.status !== 'ok') {
    root.append(h('p', { class: 'dev-err' }, got ? `Simulation unavailable: ${got.payload.reason}` : 'No payload.'));
    return;
  }
  host.showPayload(got.payload);
  const pick = h('select', { class: 'dev-case', onchange: (e: Event) => {
    u.searchParams.set('case', (e.target as HTMLSelectElement).value);
    location.href = u.toString();
  } });
  for (const key of Object.keys(FIXTURE_CASES)) {
    const o = h('option', { value: key }, `${FIXTURE_CASES[key].q}, ${key.split('_')[1] === 'emoc' ? 'C-section' : key.split('_')[1]}`);
    if (key === name) o.selected = true;
    pick.append(o);
  }
  const sim = new Simulator(host, { phone, k: k ? Number(k) : undefined });
  host.expand().topbar.append(h('span', { class: 'dev-tag' }, 'sim dev page'), pick,
    h('button', { type: 'button', class: 'sim-btn', onclick: () => sim.open({ autoplay: true }) }, 'Where would villages go?'),
    h('span', { class: 'dev-place' }, got.payload.origin.label));
  sim.load(got.payload, null, { fixture: got.fixture });
  sim.open({ mode, autoplay: u.searchParams.get('autoplay') !== '0' });
}

void main();
