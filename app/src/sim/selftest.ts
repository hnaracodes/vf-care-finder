// Unit checks for the simulator's rules and engine, run in Node:
//   cd app && node_modules/.bin/vite build -c src/sim/test/vite.selftest.config.ts && node node_modules/.sim-selftest/selftest.mjs
// 1. parity: the browser's counters on each fixture equal the payload's summary (numpy, same rules)
// 2. the decision rules on small hand-made worlds
// 3. verify-first rows equal applying the edit and recounting
// 4. engine: determinism (interactive edit == seek replay), claims holds, re-decide on a change
// 5. timings: world build, counters, a what-if recompute, the verify-first sweep

import { Engine, PH_ARRIVED, PH_HOLD, PH_MOVE, PH_STUCK } from './agents';
import type { SimFacility, SimPayload } from './contract';
import { paritySummary } from './parity';
import { base, chooseClaims, chooseEvidence, counters, previewAdd, verifyFirst, villageKm, villageMinutes } from './rules';
import { World } from './world';

const fixtures = import.meta.glob('./fixtures/*.json', { eager: true, import: 'default' }) as Record<string, SimPayload>;

let failures = 0, passes = 0;
const log: string[] = [];
function check(name: string, ok: boolean, detail = ''): void {
  if (ok) passes++; else failures++;
  log.push(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? `: ${detail}` : ''}`);
}

// ------------------------------------------------------------------ tiny worlds

function fac(i: number, lon: number, lat: number, status: SimFacility['status'], strong = false): SimFacility {
  return { i, facility_id: `f${i}`, name: `F${i}`, lat, lon, status, strong, grade: strong ? 3 : status === 'evidence' ? 2 : 1,
    approximate: false, operator: null, km_from_origin: 0, card_rank: null, why_not_letter: null, name_masked: false };
}

function tiny(facs: SimFacility[], villages: [number, number][], W = 60, A = 15): SimPayload {
  return {
    status: 'ok', version: 'sim_v1', service: 'dialysis', service_label: 'Dialysis', service_phrase: 'dialysis',
    origin: { lat: 0, lon: 0, short_name: 'Here', label: 'Here', precision_km: 1, state_display: 'X', factor: 1 },
    window: { minutes: W, allowance: A, hours_text: '1 hour', km_equivalent: 0, reach_km: 0, reach_minutes: 3 * W },
    travel: { a: 2.3, b: 0.842, text: '' },
    villages: { n: villages.length, radius_km: 50, lon: villages.map((v) => v[0]), lat: villages.map((v) => v[1]), pin: villages.map(() => ''), factor: villages.map(() => 1) },
    facilities: facs,
    summary: { n_villages: villages.length, within: 0, avg_minutes: null, n_no_evidence_in_reach: 0, first_no_evidence: 0, first_claim_only: 0,
      first_held_back: 0, extra_minutes_mean: null, n_facilities: facs.length, n_evidence: 0, n_claim_only: 0, n_held_back: 0,
      n_listing_records: facs.length, n_merged_duplicates: 0 },
    context: { labels: [{ name: 'Here', kind: 'city', lat: 0, lon: 0, prio: 1 }] }, notes: [],
  };
}

const KM_PER_DEG = 111.19; // at the equator, to place points at a given km
const at = (km: number): [number, number] => [km / KM_PER_DEG, 0];
const mins = (km: number) => 2.3 * Math.pow(km, 0.842);

function rulesTests(): void {
  // village at 0; evidence at 10 km (plain) and 14 km (strong): 14 km is about 21.9 min vs 15.9 min,
  // within the 15-minute allowance, so the strong record is chosen
  {
    const w = new World(tiny([fac(0, ...at(10), 'evidence'), fac(1, ...at(14), 'evidence', true), fac(2, ...at(2), 'claim_only')], [[0, 0]]));
    const c = chooseEvidence(w, villageMinutes(w, 0))!;
    check('evidence rule: strong record within the allowance wins', c.j === 1, `chose ${c.j}`);
    const cl = chooseClaims(w, villageKm(w, 0))!;
    check('claims rule: nearest place that lists it, any status', cl.j === 2, `chose ${cl.j}`);
    const cl2 = chooseClaims(w, villageKm(w, 0), [2], 1)!;
    check('claims rule: skips a place already visited', cl2.j === 0, `chose ${cl2.j}`);
  }
  // strong record beyond the allowance: plain nearer one wins
  {
    const w = new World(tiny([fac(0, ...at(10), 'evidence'), fac(1, ...at(30), 'evidence', true)], [[0, 0]]));
    const c = chooseEvidence(w, villageMinutes(w, 0))!;
    check('evidence rule: strong record more than the allowance further loses', c.j === 0, `chose ${c.j}; ${mins(30) - mins(10)} min apart`);
  }
  // nothing within the window: nearest by minutes, no allowance
  {
    const w = new World(tiny([fac(0, ...at(80), 'evidence'), fac(1, ...at(85), 'evidence', true)], [[0, 0]], 60));
    const c = chooseEvidence(w, villageMinutes(w, 0))!;
    check('evidence rule: none inside the window, nearest overall', c.j === 0 && c.m > 60, `chose ${c.j} at ${c.m.toFixed(1)} min`);
  }
  // no evidence at all: stuck (null)
  {
    const w = new World(tiny([fac(0, ...at(5), 'claim_only'), fac(1, ...at(6), 'held_back')], [[0, 0]]));
    check('evidence rule: no place with evidence gives null (stuck)', chooseEvidence(w, villageMinutes(w, 0)) === null);
    const c = counters(w);
    check('counters: no evidence anywhere', c.within === 0 && c.avg_minutes === null && c.n_no_evidence_in_reach === 1 && c.first_no_evidence === 1 && c.first_claim_only === 1);
  }
  // first(v) ties go to the lower index; first_no_evidence split by status; extra minutes
  {
    const p = tiny([fac(0, ...at(5), 'held_back'), fac(1, ...at(5), 'evidence'), fac(2, ...at(20), 'evidence')], [[0, 0]]);
    const w = new World(p);
    const b = base(w);
    check('first(v): ties to the lower index', b.first[0] === 0, `first ${b.first[0]}`);
    const c = counters(w, b);
    check('counters: first stop held back counted apart', c.first_no_evidence === 1 && c.first_held_back === 1 && c.first_claim_only === 0);
    // extra = MIN(v, first) + leg(first -> nearest evidence to first, 0 km here) - nearestEv(v)
    check('counters: no extra minutes when the nearest evidence sits at the first stop', c.extra_minutes_mean === 0, `${c.extra_minutes_mean}`);
  }
  // edits: verify, close, add, undo, reset
  {
    const p = tiny([fac(0, ...at(3), 'claim_only'), fac(1, ...at(40), 'evidence')], [[0, 0], at(1)]);
    const w = new World(p);
    const real = counters(w);
    w.apply({ type: 'verify', facility_id: 'f0' });
    const c1 = counters(w);
    check('verify: the record counts as evidence, never strong', w.ev(0) && !w.isStrong(0) && c1.first_no_evidence === 0 && c1.avg_minutes! < real.avg_minutes!);
    w.apply({ type: 'close', facility_id: 'f0' });
    const c2 = counters(w);
    check('close: the place leaves every rule', !w.ev(0) && c2.first_no_evidence === 0 && c2.avg_minutes === real.avg_minutes, JSON.stringify(c2));
    const v0 = w.version;
    const pa = previewAdd(w, ...at(0.5));
    check('previewAdd: does not change the world', w.version === v0 && w.F === 2 && JSON.stringify(counters(w)) === JSON.stringify(c2));
    check('previewAdd: after has the added unit', pa.after.within === 2 && pa.after.avg_minutes! < 5);
    w.apply({ type: 'add', lon: at(0.5)[0], lat: 0 });
    const c3 = counters(w);
    check('add: a what-if unit appends a column with evidence', w.F === 3 && w.ev(2) && JSON.stringify(c3) === JSON.stringify(pa.after));
    w.undoLast();
    check('undo: removes the last edit', JSON.stringify(counters(w)) === JSON.stringify(c2) && !w.open[2]);
    w.reset();
    check('reset: back to the real data', JSON.stringify(counters(w)) === JSON.stringify(real) && w.edits.length === 0);
  }
}

function parityTests(): void {
  for (const [path, p] of Object.entries(fixtures)) {
    const t0 = performance.now();
    const w = new World(p);
    const tW = performance.now() - t0;
    const t1 = performance.now();
    const c = counters(w);
    const tC = performance.now() - t1;
    const par = paritySummary(c, p);
    const name = path.replace('./fixtures/sim_', '').replace('.json', '');
    check(`parity ${name}`, par.ok, par.ok ? `${c.within}/${c.n} within, avg ${c.avg_minutes}, first no evidence ${c.first_no_evidence}, extra ${c.extra_minutes_mean}` : JSON.stringify(par.diff));
    log.push(`     ${name}: world ${w.V}x${w.F} built in ${tW.toFixed(1)} ms, counters in ${tC.toFixed(1)} ms`);

    // verify-first rows equal applying the edit
    const vf = verifyFirst(w, 3);
    let ok = true;
    for (const r of vf.rows) {
      w.apply({ type: 'verify', facility_id: r.facility_id });
      const a = counters(w);
      if (a.within !== r.after.within || a.first_no_evidence !== r.after.first_no_evidence || a.avg_minutes !== r.after.avg_minutes) {
        ok = false;
        log.push(`     mismatch ${r.name}: row ${JSON.stringify(r.after)} vs applied ${JSON.stringify(a)}`);
      }
      w.reset();
    }
    const best = vf.rows[0];
    check(`verify-first ${name}: rows equal applying the edit`, ok, best ? `${vf.n_candidates} candidates in ${vf.ms.toFixed(1)} ms; best ${best.name}: within ${best.before.within} -> ${best.after.within}, first no evidence ${best.before.first_no_evidence} -> ${best.after.first_no_evidence}, avg ${best.before.avg_minutes} -> ${best.after.avg_minutes}` : 'no candidates');
    check(`verify-first ${name}: sweep under 50 ms`, vf.ms < 50, `${vf.ms.toFixed(1)} ms`);

    // engine: evidence mode runs to the end, every dot arrives where the rule says
    const e = new Engine(w, { seed: 7, k: 3, mode: 'evidence' });
    e.advanceTo(100000);
    let wrong = 0, stuck = 0;
    for (let i = 0; i < e.N; i++) {
      if (e.phase[i] === PH_STUCK) { stuck++; continue; }
      const want = chooseEvidence(w, villageMinutes(w, e.vOf[i]))!.j;
      if (e.phase[i] !== PH_ARRIVED || e.target[i] !== want) wrong++;
    }
    check(`engine ${name} evidence mode: every dot ends at its rule target`, wrong === 0 && e.done, `${e.N} dots, ${wrong} wrong, ${stuck} stuck, speed ${e.speed} min/s`);

    // claims mode: dots whose first stop shows no evidence hold there, then go on once
    const ec = new Engine(w, { seed: 7, k: 3, mode: 'claims' });
    ec.advanceTo(100000);
    const t = ec.tally();
    check(`engine ${name} claims mode: stops with no evidence = k x first_no_evidence`, t.stoppedNoEvidence === 3 * c.first_no_evidence && ec.done,
      `${t.stoppedNoEvidence} stopped (k=3, first_no_evidence ${c.first_no_evidence}); ${t.arrived} arrived, ${t.stuck} stuck`);

    // determinism: an edit made at minute 12 while running == the same edit replayed by seek
    const cand = vf.rows[0]?.facility_id ?? null;
    const ea = new Engine(w, { seed: 7, k: 3, mode: 'evidence' });
    ea.advanceTo(12);
    const t2 = performance.now();
    w.apply(cand ? { type: 'verify', facility_id: cand } : { type: 'add', lon: p.origin.lon, lat: p.origin.lat });
    ea.worldChanged();
    const cw = counters(w);
    const tEdit = performance.now() - t2;
    ea.advanceTo(30);
    const snapA = snapshot(ea);
    ea.seek(30);
    const snapB = snapshot(ea);
    check(`engine ${name}: interactive edit at minute 12 equals the seek replay`, snapA === snapB);
    check(`engine ${name}: what-if recompute under 200 ms`, tEdit < 200, `${tEdit.toFixed(1)} ms (world, ${ea.N} dots re-decided, counters ${cw.within}/${cw.n})`);
    const e2 = new Engine(w, { seed: 7, k: 3, mode: 'evidence' });
    e2.timeline = ea.timeline.map((s) => ({ at: s.at, edits: s.edits.slice() }));
    e2.seek(30);
    check(`engine ${name}: a second engine with the same seed and edits gives the same state`, snapshot(e2) === snapA);
    w.reset();
  }
}

/** Jokihat: adding a unit at the origin mid-run turns dots on the road. */
function turnTest(): void {
  const p = fixtures['./fixtures/sim_jokihat_dialysis.json'];
  if (!p) return;
  const w = new World(p);
  const e = new Engine(w, { seed: 7, k: 3, mode: 'evidence' });
  e.advanceTo(20);
  const moving = e.tally().moving;
  w.apply({ type: 'add', lon: p.origin.lon, lat: p.origin.lat });
  e.worldChanged();
  const t = e.tally();
  let toAdded = 0;
  for (let i = 0; i < e.N; i++) if (e.turned[i] && e.target[i] === w.F - 1) toAdded++;
  check('jokihat: a unit added at the origin turns dots already on the road', t.turned > 0 && toAdded === t.turned, `${moving} moving at minute 20, ${t.turned} turned, ${toAdded} now heading to the what-if unit`);
  e.advanceTo(100000);
  let holds = 0;
  for (let i = 0; i < e.N; i++) if (e.phase[i] === PH_HOLD || e.phase[i] === PH_MOVE) holds++;
  check('jokihat: the run still ends', e.done && holds === 0);
}

/** A change that does not reach a dot leaves it on its way (no turn). */
function unrelatedTest(): void {
  const p = fixtures['./fixtures/sim_lucknow_emoc.json'];
  if (!p) return;
  const w = new World(p);
  for (const mode of ['evidence', 'claims'] as const) {
    const e = new Engine(w, { seed: 7, k: 3, mode });
    e.advanceTo(12);
    w.apply({ type: 'add', lon: p.origin.lon + 4, lat: p.origin.lat - 3 });
    e.worldChanged();
    check(`lucknow ${mode}: a what-if unit about 500 km away turns no dot`, e.tally().turned === 0, `${e.tally().turned} turned`);
    w.reset();
  }
}

function snapshot(e: Engine): string {
  const parts: number[] = [];
  for (let i = 0; i < e.N; i++) parts.push(e.phase[i], e.target[i], Math.round(e.t0[i] * 1e6), Math.round(e.dur[i] * 1e6), e.turned[i], e.referred[i]);
  return `${e.time}|${parts.join(',')}|${e.nRoad}`;
}

rulesTests();
parityTests();
turnTest();
unrelatedTest();
console.log(log.join('\n'));
console.log(`\n${passes} passed, ${failures} failed`);
if (failures) throw new Error(`${failures} simulator checks failed`);
