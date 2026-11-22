// DOM builders. Every number shown comes from the data files or is computed
// from them in the browser; this file holds labels and units only.

import type { AppData, District, Facility, LineKey, Meta } from './data';
import {
  DIST_COLORS, DIST_LABELS, GRADE_COLORS, QUADRANT_ACTIONS, QUADRANT_COLORS, QUADRANT_LABELS,
  QUADRANT_ORDER, SWARM, normQuadrant, rgbaCss,
} from './colors';

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

export const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US');
export const fmtPct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export const fmtKm = (d: number) => (Number.isFinite(d) ? `${d < 10 ? d.toFixed(1) : Math.round(d)} km` : 'none in data');
const fmtNum = (x: number | null | undefined, d = 1) => (x == null || !Number.isFinite(x) ? 'n/a' : x.toFixed(d));

export function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} missing`);
  return e as T;
}

export function gradeLabel(meta: Meta, k: number): string {
  if (k === 0) return 'Any facility';
  const g = meta.grades?.find((x) => x.k === k)?.label ?? ['none', 'claimed', 'supported', 'corroborated'][k];
  return g.charAt(0).toUpperCase() + g.slice(1);
}

export function lineLabel(data: AppData, line: LineKey, long = false): string {
  const l = data.lines.find((x) => x.key === line);
  return l ? (long ? l.long : l.label) : line;
}

const sw = (c: string, shape: 'dot' | 'ring' = 'dot') =>
  `<span class="sw ${shape}" style="${shape === 'dot' ? `background:${c}` : `border-color:${c}`}"></span>`;

export function legendHTML(
  data: AppData, view: string, grade: number, R: number, quadMode: string, line: LineKey, swarmRatio: number,
): string {
  const meta = data.meta;
  if (view === 'quadrant') {
    const rows = [...QUADRANT_ORDER, 'unscored'].map(
      (q) => `<div class="lg-row">${sw(rgbaCss(QUADRANT_COLORS[q]))}<div><b>${esc(QUADRANT_LABELS[q])}</b><div class="muted">${esc(QUADRANT_ACTIONS[q])}</div></div></div>`,
    ).join('');
    return `<div class="lg-title">District quadrant, ${esc(lineLabel(data, line))}</div>
      <div class="seg small" id="quadmode">
        <button data-q="model" class="${quadMode === 'model' ? 'on' : ''}">Model</button>
        <button data-q="rule" class="${quadMode === 'rule' ? 'on' : ''}">Thresholds</button>
      </div>
      ${rows}
      <div class="muted note">Each dot is one post office, colored by its district. Click a dot for the district.</div>`;
  }
  if (view === 'swarm') {
    return `<div class="lg-title">Patient swarm, ${esc(lineLabel(data, line))}</div>
      <div class="lg-row">${sw(rgbaCss(SWARM.travel))}<div>In transit</div></div>
      <div class="lg-row">${sw(rgbaCss(SWARM.reached))}<div>Reached care within ${R} km</div></div>
      <div class="lg-row">${sw(rgbaCss(SWARM.stranded))}<div>Stranded at ${R} km (care desert)</div></div>
      <div class="lg-row">${sw(rgbaCss(SWARM.likely))}<div>Care likely exists, not in this data (est.)<div class="muted">Data-desert district: stays home and pulses</div></div></div>
      <div class="muted note">Each dot is one post office area, not a person. Target: nearest facility at ${esc(gradeLabel(meta, grade))}${grade > 0 ? ' or better' : ''}. Straight-line. At national zoom 1 in ${swarmRatio} dots move; zoom in to see all.</div>`;
  }
  const rows = DIST_LABELS.map((l, i) => `<div class="lg-chip">${sw(rgbaCss(DIST_COLORS[i]))}<span>${esc(l)}</span></div>`).join('');
  const fac = [1, 2, 3].map((k) => `<span class="lg-chip">${sw(rgbaCss(GRADE_COLORS[k]), 'ring')}${esc(gradeLabel(meta, k))}</span>`).join('');
  return `<div class="lg-title">Straight-line distance to nearest facility</div>
    <div class="lg-grid">${rows}</div>
    <div class="lg-title sub">Facility markers by evidence grade</div>
    <div class="lg-grid">${fac}</div>
    <div class="muted note">Each dot is one post office. Click a dot for one patient's trip; click a facility for its evidence.</div>`;
}

export interface NearRow {
  k: number;
  facIndex: number;
  dist: number;
  fac: Facility | null;
}

export function patientHTML(data: AppData, po: number, rows: NearRow[], line: LineKey): string {
  const di = data.district[po];
  const d = di !== data.noIndex ? data.districts[di] : null;
  const items = rows.map((r) => {
    const name = r.fac ? esc(r.fac.name) : 'No facility at this level in the data';
    return `<li class="near-row" data-fac="${r.facIndex}">
      <span class="sw ring" style="border-color:${rgbaCss(GRADE_COLORS[r.k])}"></span>
      <div><div class="near-k">${esc(gradeLabel(data.meta, r.k))}${r.k > 0 ? ' or better' : ''}</div>
      <div class="near-name">${name}</div></div>
      <div class="near-d">${esc(fmtKm(r.dist))}</div></li>`;
  }).join('');
  return `<div class="panel-kicker">One patient's trip</div>
    <h2>Post office in ${esc(d ? `${d.name}, ${d.state}` : 'an unmatched district')}</h2>
    <div class="muted">${data.lat[po].toFixed(3)}° N, ${data.lon[po].toFixed(3)}° E. Nearest facility of any kind, then nearest ${esc(lineLabel(data, line, true).toLowerCase())} at each evidence level:</div>
    <ul class="near">${items}</ul>
    <div class="muted note">Straight-line km, not travel time. The facility table is a sample: the true nearest facility may be closer.</div>`;
}

export function facilityHTML(data: AppData, f: Facility, line: LineKey, effGrade: number, simulated: boolean): string {
  const meta = data.meta;
  const ev = f.evidence?.[line] ?? [];
  const tags = f.tags?.[line] ?? [];
  const grades = data.lines.map((l) => {
    const g = l.key === line ? effGrade : f.grades?.[l.key] ?? 0;
    return `<div class="kv"><span>${esc(l.label)}</span><b>${g > 0 ? esc(gradeLabel(meta, g)) : 'No claim or evidence in this data'}</b></div>`;
  }).join('');
  const quotes = ev.length
    ? ev.map((q) => `<blockquote>${esc(q)}</blockquote>`).join('')
    : `<div class="muted">No evidence of ${esc(lineLabel(data, line, true).toLowerCase())} in this data.</div>`;
  return `<div class="panel-kicker">Facility evidence</div>
    <h2>${esc(f.name)}</h2>
    <div class="muted">${esc([f.type, f.operator, f.city, f.state].filter(Boolean).join(' · '))}</div>
    ${simulated ? `<div class="sim-flag">Simulated: treated as ${esc(gradeLabel(meta, effGrade))} for ${esc(lineLabel(data, line))}. A modeling assumption, not a fact.</div>` : ''}
    <div class="kvs">${grades}</div>
    <h3>Quoted facts, ${esc(lineLabel(data, line))}</h3>
    ${quotes}
    ${tags.length ? `<h3>Evidence tags</h3><div class="chips">${tags.map((t) => `<span class="chip">${esc(t)}</span>`).join('')}</div>` : ''}
    <div class="kv"><span>Independent web domains mentioning it</span><b>${f.n_domains ?? 'n/a'}</b></div>`;
}

const NFHS_LABELS: Record<string, string> = {
  institutional_birth_5y_pct: 'Births in an institution',
  institutional_birth_in_public_facility_5y_pct: 'Births in a public facility',
  births_delivered_by_csection_5y_pct: 'Births by C-section',
  hh_member_covered_health_insurance_pct: 'Households with health insurance',
  women_age_15_49_who_are_literate_pct: 'Women 15 to 49 who are literate',
};

export function districtHTML(data: AppData, d: District, line: LineKey, quadMode: string): string {
  const q = d.quadrant?.[line] ?? null;
  const qk = normQuadrant(quadMode === 'rule' ? q?.quadrant_rule : q?.quadrant_model);
  const other = normQuadrant(quadMode === 'rule' ? q?.quadrant_model : q?.quadrant_rule);
  const acc = d.access?.[line] ?? null;
  const nf = Object.entries(d.nfhs ?? {}).map(([k, v]) => {
    const flag = d.nfhs_flags?.[k];
    const flagTxt = flag && flag !== 'ok' ? ` <span class="flag">${esc(flag.replace('_', ' '))}</span>` : '';
    const isPct = k.endsWith('_pct');
    return `<div class="kv"><span>${esc(NFHS_LABELS[k] ?? k)}</span><b>${v == null ? 'suppressed' : `${fmtNum(v)}${isPct ? '%' : ''}`}${flagTxt}</b></div>`;
  }).join('');
  const accRows = acc ? `
    <div class="kv"><span>Median distance, ${esc(gradeLabel(data.meta, Number(acc.min_grade ?? 2)))} or better</span><b>${esc(fmtKm(Number(acc.dist_km_p50)))}</b></div>
    <div class="kv"><span>Post offices within 30 km</span><b>${acc.share_within_30km != null ? fmtPct(Number(acc.share_within_30km)) : 'n/a'}</b></div>
    <div class="kv"><span>Post offices within 60 km</span><b>${acc.share_within_60km != null ? fmtPct(Number(acc.share_within_60km)) : 'n/a'}</b></div>` : '<div class="muted">No access figures for this district.</div>';
  return `<div class="panel-kicker">District</div>
    <h2>${esc(d.name)}, ${esc(d.state)}</h2>
    <div class="quad-tag" style="--qc:${rgbaCss(QUADRANT_COLORS[qk])}"><span class="sw dot" style="background:${rgbaCss(QUADRANT_COLORS[qk])}"></span>${esc(QUADRANT_LABELS[qk])} <span class="muted">(${quadMode === 'rule' ? 'thresholds' : 'model'})</span></div>
    ${q ? `<div class="muted">${quadMode === 'rule' ? 'Model' : 'Thresholds'} say: ${esc(QUADRANT_LABELS[other])}</div>` : ''}
    <h3>Access, ${esc(lineLabel(data, line))}</h3>${accRows}
    ${q ? `<h3>Outcome model</h3>
      <div class="kv"><span>Outcome, actual</span><b>${fmtNum(q.outcome_value)}${q.outcome_flag && q.outcome_flag !== 'ok' ? ` <span class="flag">${esc(q.outcome_flag)}</span>` : ''}</b></div>
      <div class="kv"><span>Outcome, predicted</span><b>${fmtNum(q.predicted_outcome)}</b></div>
      <div class="kv"><span>Residual (actual minus predicted)</span><b>${fmtNum(q.residual ?? d.residual, 2)}</b></div>
      <div class="kv"><span>Chance the gap is real (est.)</span><b>${q.p_real_gap != null ? fmtPct(q.p_real_gap, 0) : 'n/a'}</b></div>` : `<div class="muted">No outcome comparison for this district and line.</div>`}
    <h3>Survey (NFHS-5, 2019 to 2021)</h3>${nf}
    <div class="kv"><span>How the district was matched</span><b>${esc(d.crosswalk_method ?? 'n/a')}</b></div>`;
}

export function drawerHTML(data: AppData): string {
  const m = data.meta;
  const c = m.counts ?? {};
  const ft = m.models?.fact_tagger;
  const om = m.models?.outcome_model;
  const mockTag = (x?: { mock?: boolean }) => (x?.mock || m.mock ? ' <span class="flag">mock values</span>' : '');
  let ftHTML = '<div class="muted">Not available yet.</div>';
  if (ft?.per_tag?.length) {
    const systems = ft.systems ?? ['regex', 'teacher', 'student'];
    const head = `<tr><th>Tag</th>${systems.map((s) => `<th colspan="3">${esc(s)}</th>`).join('')}</tr>
      <tr><th></th>${systems.map(() => '<th>P</th><th>R</th><th>F1</th>').join('')}</tr>`;
    const body = ft.per_tag.map((row) => `<tr><td>${esc(row.tag)}</td>${systems.map((s) => {
      const v = row[s] as { p?: number; r?: number; f1?: number } | undefined;
      return `<td>${fmtNum(v?.p, 2)}</td><td>${fmtNum(v?.r, 2)}</td><td><b>${fmtNum(v?.f1, 2)}</b></td>`;
    }).join('')}</tr>`).join('');
    ftHTML = `<table class="metrics">${head}${body}</table>
      <div class="muted">Gold set: ${ft.gold_set?.n_facts ?? 'n/a'} facts, labeled by ${esc(ft.gold_set?.labeled_by ?? 'n/a')}.</div>`;
  }
  let omHTML = '<div class="muted">Not available yet.</div>';
  if (om?.with_access && om?.without_access) {
    omHTML = `<div class="kv"><span>Target</span><b>${esc(om.target ?? '')}</b></div>
      <div class="kv"><span>Error without access features (${esc(om.metric ?? '')})</span><b>${fmtNum(om.without_access.mean, 2)}${om.without_access.sd != null ? ` ± ${fmtNum(om.without_access.sd, 2)}` : ''}</b></div>
      <div class="kv"><span>Error with access features</span><b>${fmtNum(om.with_access.mean, 2)}${om.with_access.sd != null ? ` ± ${fmtNum(om.with_access.sd, 2)}` : ''}</b></div>
      <div class="kv"><span>Cross-validation</span><b>${esc(om.cv_scheme ?? '')}</b></div>`;
  }
  const matched = c.districts_matched
    ? Object.entries(c.districts_matched).map(([k, v]) => `${esc(k.replace('_', ' '))} ${fmtInt(v)}`).join(', ')
    : 'n/a';
  return `<button class="close" id="drawer-close" aria-label="Close">×</button>
    <div class="panel-kicker">How we know</div>
    <h2>Two models and their numbers</h2>
    <h3>Fact tagger (Model A)${mockTag(ft)}</h3>${ftHTML}
    <h3>Outcome model (Model B)${mockTag(om)}</h3>${omHTML}
    <h3>Data${m.mock ? ' <span class="flag">mock</span>' : ''}</h3>
    <div class="kv"><span>Facilities</span><b>${c.facilities != null ? fmtInt(c.facilities) : 'n/a'}${c.facilities_private_share != null ? `, ${fmtPct(c.facilities_private_share, 0)} private` : ''}</b></div>
    <div class="kv"><span>Geocoded post offices</span><b>${c.post_offices_geocoded != null ? fmtInt(c.post_offices_geocoded) : fmtInt(data.n)}${c.post_offices_total != null ? ` of ${fmtInt(c.post_offices_total)}` : ''}</b></div>
    <div class="kv"><span>Districts</span><b>${c.districts != null ? fmtInt(c.districts) : 'n/a'}${c.nfhs_districts != null ? ` (NFHS: ${fmtInt(c.nfhs_districts)})` : ''}</b></div>
    <div class="kv"><span>District matching</span><b>${matched}</b></div>
    <h3>Limits</h3>
    <ul class="limits">${(m.limits ?? []).map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
    <div class="muted note">Generated ${esc(m.generated_at ?? 'n/a')} · commit ${esc((m.git_commit ?? '').slice(0, 8))}${m.versions ? ` · ${Object.entries(m.versions).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(', ')}` : ''}</div>`;
}

export function listsHTML(data: AppData, line: LineKey, n = 8): string {
  const pick = (q: string, sort: (a: District, b: District) => number) =>
    data.districts.filter((d) => normQuadrant(d.quadrant?.[line]?.quadrant_model) === q).sort(sort).slice(0, n);
  const acc = (d: District) => d.quadrant?.[line]?.access_value ?? -Infinity;
  const care = pick('care_desert', (a, b) => (b.quadrant?.[line]?.p_real_gap ?? 0) - (a.quadrant?.[line]?.p_real_gap ?? 0) || acc(b) - acc(a));
  const dd = pick('data_desert', (a, b) => (b.quadrant?.[line]?.residual ?? 0) - (a.quadrant?.[line]?.residual ?? 0));
  const cost = pick('cost_barrier', (a, b) => (a.quadrant?.[line]?.outcome_value ?? 0) - (b.quadrant?.[line]?.outcome_value ?? 0));
  const col = (title: string, q: string, rows: District[], val: (d: District) => string) => `
    <div class="list-col"><div class="list-h"><span class="sw dot" style="background:${rgbaCss(QUADRANT_COLORS[q])}"></span>${esc(title)}</div>
    <ol>${rows.map((d, i) => `<li><span class="n">${i + 1}</span><span class="nm">${esc(d.name)}<span class="st">${esc(d.state)}</span></span><b>${val(d)}</b></li>`).join('') || '<li class="muted">None</li>'}</ol></div>`;
  return `<div class="lists">
    ${col('Send care', 'care_desert', care, (d) => esc(fmtKm(acc(d))))}
    ${col('Send data collectors', 'data_desert', dd, (d) => `${fmtNum(d.quadrant?.[line]?.residual, 1)} pts`)}
    ${col('Fix cost or quality', 'cost_barrier', cost, (d) => `${fmtNum(d.quadrant?.[line]?.outcome_value, 1)}%`)}
  </div>
  <div class="muted note">Care deserts ranked by chance the gap is real (est.); data deserts by residual (outcome above prediction, points); cost barriers by outcome. Median straight-line distance to ${esc(gradeLabel(data.meta, 2).toLowerCase())} care shown for care deserts.</div>`;
}

export function limitsHTML(data: AppData): string {
  return `<div class="panel-kicker">What this cannot tell you</div>
    <ul class="limits big">${(data.meta.limits ?? []).map((l) => `<li>${esc(l)}</li>`).join('')}</ul>`;
}
