// HTML for the location line, result cards, abstention banner, unverified
// list and the "How we decided" panel. Every claim is a quote or a server
// field; the only words added here are templates from text.ts.

import type { Answer, Card, Flag, Gate, HowWeDecided, Parts, Resolution } from './types';
import {
  CONFIDENCE_WORDS, FIELD_WORDS, PART_WORDS,
  capital, count, esc, fmt, kmText, levelText, looksLikeCoords, operatorWord, plainReason, serviceName, typeWord,
} from './text';

// ------------------------------------------------------------ location line

export function locationLine(r: Resolution, typed: string): string {
  switch (r.status) {
    case 'ok': {
      const across = /\bkm across\b/.test(r.label ?? '');
      const p = r.precision_km != null && !across
        ? ` <span class="loc-prec">(precise to about ${esc(kmText(r.precision_km, 0).replace('about ', ''))})</span>` : '';
      const coarse = r.precision_km != null && r.precision_km >= 10
        ? `<div class="loc-how loc-coarse">Distances below are straight-line km from this point. The patient may be up to about ${esc(Math.round(r.precision_km).toLocaleString('en-IN'))} km from it: type a PIN code or a town for a closer point.</div>` : '';
      const how = r.how ? `<div class="loc-how">${esc(r.how)}</div>` : '';
      return `<div class="loc loc-ok" role="status"><span class="loc-pin" aria-hidden="true"></span><div><div>Searching around <b>${esc(r.label)}</b>${p}</div>${how}${coarse}</div></div>`;
    }
    case 'ambiguous': {
      const btns = (r.candidates ?? []).map((c, i) =>
        `<button type="button" class="cand" data-cand="${i}">${esc(c.label)}</button>`).join('');
      const how = r.how ? `<div class="loc-how">${esc(r.how)}</div>` : '';
      return `<div class="loc loc-ask" role="status"><div><div class="loc-q">Which ${esc(typed)}?</div>${how}<div class="cands">${btns}</div></div></div>`;
    }
    case 'outside_india':
      return outsideIndia(r.how);
    case 'not_found':
    default:
      // Two numbers that the resolver did not read as a point in India: say so in the same words.
      if (looksLikeCoords(typed)) return outsideIndia('Those numbers are not a latitude and longitude inside India.');
      {
        const hint = /\btry\b/i.test(r.how ?? '') ? '' : ' Try a 6-digit PIN code, a district, a city, or coordinates such as 26.91, 75.79.';
        return `<div class="loc loc-bad" role="alert"><div><div><b>We could not find "${esc(typed)}".</b></div><div class="loc-how">${esc(r.how ?? '')}${hint}</div></div></div>`;
      }
  }
}

function outsideIndia(how: string | null | undefined): string {
  const h = how && !/outside india/i.test(how) ? `${esc(how)} ` : '';
  return `<div class="loc loc-bad" role="alert"><div><div><b>That place is outside India.</b></div><div class="loc-how">${h}This tool only covers facilities in India. Check the coordinates, or type a PIN code, district or city.</div></div></div>`;
}

// ------------------------------------------------------------------- cards

function confBars(level: string | null | undefined): string {
  const n = level === 'high' ? 3 : level === 'medium' ? 2 : 1;
  return `<span class="conf-bars" aria-hidden="true">${[0, 1, 2].map((i) => `<i class="${i < n ? 'on' : ''}"></i>`).join('')}</span>`;
}

function quote(text: string, field: string): string {
  return `<figure class="quote"><blockquote>${esc(text)}</blockquote><figcaption>${esc(FIELD_WORDS[field] ?? field)}</figcaption></figure>`;
}

function hasParts(p: Card['parts']): p is Parts {
  return !!p && typeof (p as Parts).evidence === 'number';
}

function bar(name: string, v: number, extra: string): string {
  return `<div class="bar-row"><span class="bar-name">${esc(name)}</span>
    <span class="bar" role="img" aria-label="${esc(name)} ${fmt(v)} out of 1"><span style="width:${Math.max(0, Math.min(1, v)) * 100}%"></span></span>
    <span class="bar-num">${fmt(v)}${extra}</span></div>`;
}

function scoreBlock(c: Card, a: Answer): string {
  const p = c.parts;
  if (!hasParts(p) || c.score == null) return '';
  const labels = p.labels ?? {};
  const pens = (p.penalties ?? []).map((x) =>
    `<div class="bar-row pen"><span class="bar-name">${esc(capital(x.name.replace(/_/g, ' ')))}</span><span class="bar-pen">${esc(x.reason)}</span><span class="bar-num">-${fmt(x.value)}</span></div>`).join('');
  let rows = '';
  let how = '';
  if (p.combine === 'product') {
    rows = bar(capital(labels.evidence ?? 'keyword match strength'), p.evidence, '')
      + bar(capital(labels.distance ?? 'reach'), p.distance, '');
    how = `<div class="score-note">Score = ${esc(labels.evidence ?? 'match')} x ${esc(labels.distance ?? 'reach')}.</div>`;
  } else {
    const raw = p.raw ?? { evidence: p.evidence, distance: p.distance, trust: p.trust };
    const w = p.weights ?? a.how_we_decided?.weights ?? {};
    for (const k of ['evidence', 'distance', 'trust'] as const) {
      const v = raw[k];
      if (v == null) continue;
      const contrib = p[k];
      const extra = w[k] != null ? `<span class="bar-w"> x ${fmt(w[k], 1)} = ${fmt(contrib)}</span>` : '';
      rows += bar(PART_WORDS[k] ?? capital(labels[k] ?? k), v, extra);
    }
  }
  const prov = a.how_we_decided?.score_status === 'provisional'
    ? `<div class="score-note">Provisional score: the weights are stated defaults, not fitted. See "How we decided".</div>` : '';
  return `<section class="score"><h4>Score <span class="score-total">${fmt(c.score)}</span></h4>
    ${rows}${pens}${how}${prov}</section>`;
}

function sourcesLine(c: Card): string {
  const s = c.sources;
  const loc = c.location_note ? `<div class="loc-note">${esc(c.location_note)}</div>` : '';
  if (!s) return loc ? `<footer class="sources">${loc}</footer>` : '';
  const n = s.n_domains;
  const doms = (s.domains ?? []).map((d) => `<span class="dom">${esc(d)}</span>`).join(' ');
  const off = s.official_website ? ` Official website: <span class="dom">${esc(s.official_website)}</span>.` : ' No official website in the record.';
  return `<footer class="sources">From this facility's record, compiled from ${count(n)} web source${n === 1 ? '' : 's'}${doms ? `: ${doms}` : ''}.${off}${loc}</footer>`;
}

function flagItem(f: Flag): string {
  const head = f.severity === 'major' ? 'Blocking warning' : 'Warning';
  return `<li><b>${head}.</b> ${esc(f.reason)}${f.quote ? quote(f.quote, 'record') : ''}</li>`;
}

function norm(s: string | null | undefined): string {
  return (s ?? '').replace(/\s+/g, ' ').trim().replace(/[.]+$/, '').toLowerCase();
}

function cautions(c: Card): string {
  const items: string[] = (c.flags ?? []).map(flagItem);
  // could_be_wrong often repeats a flag's reason word for word: list it once.
  const said = new Set((c.flags ?? []).map((f) => norm(f.reason)));
  if (c.could_be_wrong && !said.has(norm(c.could_be_wrong))) items.push(`<li>${esc(c.could_be_wrong)}</li>`);
  const also = (c.sources?.also_listed_as ?? []).map((x) => typeof x === 'string' ? x : x.name ?? x.facility_id ?? '').filter(Boolean);
  if (also.length) items.push(`<li><b>Also listed as</b> ${also.map(esc).join(', ')}. We show it once.</li>`);
  if (!items.length) return '';
  return `<section class="careful"><h4><span class="warn-icon" aria-hidden="true">!</span>Be careful</h4><ul>${items.join('')}</ul></section>`;
}

/** One list item per quoted fact; requirements that the same fact satisfies are listed together. */
function evidenceList(c: Card): string {
  const byQuote = new Map<string, { reqs: string[]; field: string }>();
  for (const e of c.evidence ?? []) {
    if (!e.quote) continue;
    const key = `${e.field}\u0000${e.quote}`;
    const cur = byQuote.get(key) ?? { reqs: [], field: e.field };
    if (e.requirement && !cur.reqs.includes(e.requirement)) cur.reqs.push(e.requirement);
    byQuote.set(key, cur);
  }
  return [...byQuote.entries()].map(([key, v]) => {
    const q = key.split('\u0000')[1];
    const req = v.reqs.length ? `<div class="req">${esc(capital(v.reqs.join('; ')))}</div>` : '';
    return `<li>${req}${quote(q, v.field)}</li>`;
  }).join('');
}

/** A card further than the service radius from the searched point (the ranker may still return it). */
export function beyondRadius(c: Card, a: Answer): boolean {
  return c.distance_km != null && Number.isFinite(c.distance_km) && a.radius_km != null && c.distance_km > a.radius_km;
}

/** The answer with its cards inside the radius first, each group still best first. main.ts applies
 * this before the cards and the map are drawn, so card numbers and map markers stay in step. */
export function withinRadiusFirst(a: Answer): Answer {
  const results = a.results ?? [];
  const inside = results.filter((c) => !beyondRadius(c, a));
  const outside = results.filter((c) => beyondRadius(c, a));
  return { ...a, results: [...inside, ...outside] };
}

export function cardHtml(c: Card, a: Answer, rankLabel: string, idx: number): string {
  const place = [c.city, c.state].filter(Boolean).join(', ');
  const name = serviceName(a);
  const ev = evidenceList(c);
  const level = a.method !== 'keyword' ? levelText(c.grade_label) : '';
  const missing = (c.missing ?? []).map((m) => `<li>${esc(capital(m))}.</li>`).join('');
  const conf = c.confidence ?? 'low';
  return `<article class="card" id="card-${idx}" data-idx="${idx}" tabindex="-1" aria-labelledby="card-${idx}-name">
    <header class="card-h">
      <button type="button" class="rank" data-focus="${idx}" title="Show on the map" aria-label="Show ${esc(c.name)} on the map">${esc(rankLabel)}</button>
      <div class="card-title">
        <h3 id="card-${idx}-name">${esc(c.name)}</h3>
        <div class="meta">${esc(typeWord(c.type))} <span class="dot">·</span> ${esc(operatorWord(c.operator))}${place ? ` <span class="dot">·</span> ${esc(place)}` : ''}</div>
      </div>
      <div class="dist"><b>${esc(kmText(c.distance_km, c.location_precision_km ?? 1))}</b><span>straight line from the searched place${beyondRadius(c, a) ? `, beyond the usual ${count(a.radius_km)} km` : ''}</span></div>
    </header>
    <div class="conf conf-${esc(conf)}">${confBars(conf)}<div><b>${esc(CONFIDENCE_WORDS[conf] ?? conf)}.</b> ${esc(plainReason(c.confidence_reason))}</div></div>
    ${c.why_here ? `<p class="why-here">${esc(c.why_here)}</p>` : ''}
    <section class="evidence"><h4>${a.method === 'keyword' ? `Where its record mentions ${esc(name)}` : `Why we think it offers ${esc(name)}`}</h4>
      ${level ? `<div class="level">${esc(level)}</div>` : ''}
      ${ev ? `<ul>${ev}</ul>` : `<p class="none">No fact in the record names it.</p>`}
    </section>
    ${missing ? `<section class="missing"><h4>What we could not find</h4><ul>${missing}</ul></section>` : ''}
    ${cautions(c)}
    ${scoreBlock(c, a)}
    ${sourcesLine(c)}
  </article>`;
}

function unverifiedList(list: Card[]): string {
  if (!list.length) return '';
  const rows = list.map((c, i) => {
    const major = (c.flags ?? []).filter((f) => f.severity === 'major');
    const q = major.find((f) => f.quote);
    return `<li class="unv" data-unv="${i}">
      <span class="hollow" aria-hidden="true"></span>
      <div><div class="unv-name">${esc(c.name)} <span class="meta">${esc(typeWord(c.type))} · ${esc(operatorWord(c.operator))} · ${esc(kmText(c.distance_km, c.location_precision_km ?? 1))}</span></div>
      <div class="unv-why">${esc(plainReason(c.confidence_reason) || 'Not recommended: the record does not support it.')}</div>
      ${q?.quote ? quote(q.quote, 'record') : ''}</div></li>`;
  }).join('');
  return `<section class="unverified"><h3>Also nearby, not verified</h3>
    <p class="sub">These facilities claim something related, but their records do not show reliable evidence. Hollow circles on the map.</p>
    <ul>${rows}</ul></section>`;
}

const COVERAGE = 'This dataset mostly covers private facilities with a web presence. A public hospital nearby may not appear here.';

export function resultsHtml(a: Answer): string {
  const out: string[] = [];
  const name = serviceName(a);
  if (a.method === 'keyword') {
    out.push(`<div class="banner banner-kw"><b>Keyword match, no service recipe.</b> These facilities mention ${esc(name)} in their records. We have no rule that checks this service, so confidence is never above low.</div>`);
  }
  if (a.status === 'no_reliable_option') {
    const what = a.method === 'keyword' ? `a fact matching ${name}` : `reliable evidence of ${name}`;
    out.push(`<div class="banner banner-abstain" role="alert">
      <div class="ab-title">No reliable option nearby</div>
      <p>No facility in this dataset shows ${esc(what)} within ${count(a.radius_km)} km of ${esc(a.location?.label ?? 'this place')}.</p>
      <p class="ab-cov">${COVERAGE}</p></div>`);
    if (a.beyond_radius) {
      out.push(`<h3 class="list-h">Nearest ${a.method === 'keyword' ? 'match' : 'reliable option'}, beyond ${count(a.radius_km)} km</h3>`);
      out.push(cardHtml(a.beyond_radius, a, '1', 0));
    }
  } else if (a.results.length) {
    // Cards are numbered in the order given (main.ts puts those inside the radius first), so the
    // numbers match the map markers. Cards beyond the radius go under their own heading.
    const what = a.method === 'keyword' ? 'matching records' : `evidence of ${esc(name)}`;
    const nIn = a.results.filter((c) => !beyondRadius(c, a)).length;
    let further = false;
    if (nIn > 0) out.push(`<h3 class="list-h">${nIn === 1 ? 'One facility' : `${count(nIn)} facilities`} with ${what} within ${count(a.radius_km)} km, best first</h3>`);
    a.results.forEach((c, i) => {
      if (beyondRadius(c, a) && !further) {
        further = true;
        const nOut = a.results.length - nIn;
        out.push(`<h3 class="list-h list-h-further">Further away, beyond ${count(a.radius_km)} km</h3>
          <p class="sub">${nOut === 1 ? 'This facility is' : 'These facilities are'} outside the usual radius for ${esc(name)}.</p>`);
      }
      out.push(cardHtml(c, a, String(i + 1), i));
    });
  }
  out.push(unverifiedList(a.unverified_nearby ?? []));
  if (a.status !== 'no_reliable_option') out.push(`<p class="coverage">${COVERAGE}</p>`);
  return out.join('');
}

// --------------------------------------------------------- how we decided

function gateRow(g: Gate, i: number): string {
  const n = g.n != null ? count(g.n) : '';
  const of = g.of != null ? ` <span class="g-of">of ${count(g.of)}</span>` : '';
  const blocked = g.n_blocked ? ` <span class="g-d">(${count(g.n_blocked)} more held back by a blocking warning)</span>` : '';
  return `<li><span class="g-n">${i + 1}</span><div><b>${esc(capital(g.gate))}</b>${g.rule ? ` <span class="g-d">${esc(g.rule)}</span>` : ''}${blocked}</div><span class="g-c">${n}${of}</span></li>`;
}

export function howHtml(a: Answer): string {
  const h: HowWeDecided = a.how_we_decided ?? {};
  const recipe = Array.isArray(h.recipe) ? h.recipe : h.recipe ? [h.recipe] : [];
  const gates = (h.gates ?? []).map(gateRow).join('');
  const weights = h.weights ? Object.entries(h.weights).map(([k, v]) => `<li><b>${esc(PART_WORDS[k] ?? k)}</b> weight ${fmt(v, 1)}</li>`).join('') : '';
  const pens = h.penalties ? Object.entries(h.penalties).map(([k, v]) => `<li><b>${esc(capital(k.replace(/_/g, ' ')))}</b> penalty ${fmt(v)}</li>`).join('') : '';
  const limits = (h.limits ?? []).map((l) => `<li>${esc(l)}</li>`).join('');
  const notes = (h.notes ?? []).map((l) => `<li>${esc(l)}</li>`).join('');
  const reach = h.reach_km != null ? ` Candidates are looked for out to ${count(h.reach_km)} km; only a facility inside the radius can stop the "no reliable option" answer.` : '';
  return `<details class="how" id="how">
    <summary><span>How we decided</span><span class="how-sub">recipe, filters, score, limits</span></summary>
    <div class="how-body">
      ${h.headline ? `<p class="how-head">${esc(h.headline)}</p>` : ''}
      ${recipe.length ? `<section><h4>What counts as evidence for ${esc(serviceName(a))}</h4><ul>${recipe.map((r) => `<li>${esc(r)}</li>`).join('')}</ul></section>` : ''}
      ${gates ? `<section><h4>From every facility to these results</h4><ol class="gates">${gates}</ol></section>` : ''}
      ${h.formula ? `<section><h4>The score</h4><p class="formula">${esc(h.formula)}</p>${weights || pens ? `<ul>${weights}${pens}</ul>` : ''}${h.tie_rule ? `<p>${esc(h.tie_rule)}</p>` : ''}</section>` : ''}
      ${h.radius_km != null ? `<section><h4>Search radius: ${count(h.radius_km)} km</h4><p>A stated default for this service, in straight-line km, not clinical guidance.${esc(reach)}</p></section>` : ''}
      ${notes ? `<section><h4>Notes</h4><ul>${notes}</ul></section>` : ''}
      ${limits ? `<section><h4>What this cannot tell you</h4><ul>${limits}</ul></section>` : ''}
    </div>
  </details>`;
}
