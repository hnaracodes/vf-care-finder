// HTML for the results screen (SPEC 4): header chips, origin line, the note, backups,
// further away, the two folds, the trip teaser, the abstention, landing and ambiguous
// screens. Every claim is a quote or a server field; every other word comes from text.ts.

import type { Answer, Card, Candidate, CallInfo, OutsideLine, OutsideNearby, Quote, Resolution, SimPayload, WhyNotItem } from './types';
import {
  CITATION_LINE, FOOTER_LINE, HONESTY_LINE, INTRO_LINE, WHYNOT_CLOSING,
  abstainHeadline, abstainNextStep, careChip, emergencyLine, isIsland, cautions, count, distanceLine, esc, fieldWords, fixName,
  funnelLabel, gapLine, headline, hoursText, leadQuote, looksLikeCoords, normCard, operatorWord, orderSentence,
  originText, otherQuotes, placeChip, reachText, plainQuestion, plainServer, quoteHtml, ruleParagraph, serviceName, shortPlace, sourcesLine,
  strongerNearby, sureLevel, sureLine, teaserSentence, topOrderLine, typeWord, windowLine, type CardView,
} from './text';

// ------------------------------------------------------------------ header and origin

export function chipsHtml(a: Answer): string {
  return `<span class="qchip"><em>Care</em>${esc(careChip(a))}</span>`
    + `<span class="qchip"><em>Near</em>${esc(placeChip(a))}</span>`;
}

export function originHtml(a: Answer): string {
  const o = originText(a);
  return `<p class="origin${o.tint ? ' tint' : ''}">${esc(o.text)}</p>`;
}

// ------------------------------------------------------------------ pieces

function bars(n: number): string {
  return `<span class="bars" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= n ? 'on' : ''}"></i>`).join('')}</span>`;
}

/** "poisoningSnakeBite" -> "poisoning snake bite": a taxonomy label, not record text. */
function humanLabel(t: string): string {
  return t.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
}

function quoteBlock(q: Quote, lead = false): string {
  // a listed specialty is a label from a taxonomy, not a sentence: no quote marks
  if ((q.field === 'specialties' || q.field === 'specialty') && /^[a-z]+[A-Z][A-Za-z]*$/.test(q.text.trim())) {
    return `<p class="q-label${lead ? ' lead' : ''}">Listed specialty: <b>${esc(humanLabel(q.text))}</b> <span class="qf">(a category label, not text from the record)</span></p>`;
  }
  if (q.negated) {
    return `<blockquote class="q${lead ? ' lead' : ''} neg"><span class="qt">Its record says: &ldquo;${quoteHtml(q)}&rdquo;</span><span class="qf">${esc(fieldWords(q.field))}. This names your words only to exclude them.</span></blockquote>`;
  }
  return `<blockquote class="q${lead ? ' lead' : ''}"><span class="qt">&ldquo;${quoteHtml(q)}&rdquo;</span><span class="qf">${esc(fieldWords(q.field))}</span></blockquote>`;
}

function alsoLine(a: Answer, v: CardView): string {
  const qs = otherQuotes(a, v, 2);
  if (!qs.length) return '';
  const items = qs.map((q) => `&ldquo;${quoteHtml(q, 90)}&rdquo; <span class="qf">${esc(fieldWords(q.field))}</span>`).join('; ');
  return `<p class="also">Also in its record: ${items}.</p>`;
}

function sureHtml(a: Answer, v: CardView, big = false): string {
  const lv = a.method === 'keyword' ? { word: 'Word match only.', bars: 1, cls: 'c-low' } : sureLevel(v.c);
  return `<p class="conf ${lv.cls}${big ? ' big' : ''}">${bars(lv.bars)}<span><b>${esc(lv.word)}</b> ${esc(sureLine(a, v))}</span></p>`;
}

function publicLines(c: { public_list?: { text: string } | null; outside_lines?: OutsideLine[] }): string {
  const out: string[] = [];
  // public_list (LaQshya) and outside_lines can carry the same LaQshya line: show each text once.
  const seen = new Set<string>();
  const add = (t: string | undefined | null) => {
    const k = (t ?? '').trim().toLowerCase();
    if (k && !seen.has(k)) { seen.add(k); out.push(t!.trim()); }
  };
  add(c.public_list?.text);
  for (const l of c.outside_lines ?? []) add(l.text);
  if (!out.length) return '';
  return out.map((t) => `<div class="gov"><span class="gov-tag">Outside this dataset</span><span>${esc(t)}</span></div>`).join('');
}

/** "tel:+919876543210" -> "+91 98765 43210" (the number the reveal gave; shown so it can be dialled by hand too). */
function telText(tel: string): string {
  const d = tel.replace(/^tel:/i, '').replace(/[^\d+]/g, '');
  const m = /^\+91(\d{5})(\d{5})$/.exec(d);
  return m ? `+91 ${m[1]} ${m[2]}` : d;
}

function callHtmlFor(a: Answer, fid: string, name: string, call: CallInfo | null | undefined): string {
  if (!call) return '';
  if (call.has_phone && call.phone) {
    if (call.tel) {
      return `<div class="call"><a class="btn-call" href="${esc(call.tel)}" data-call="${esc(fid)}" aria-label="Call ${esc(telText(call.tel))}: ${esc(name)}">${phoneIcon()}Call ${esc(telText(call.tel))}</a>`
        + `<span class="fine">Number from its record. It may be out of date.</span></div>`;
    }
    if (a.demo_masked) {
      return `<div class="call"><p class="phone-off">${phoneIcon()}<span>Phone in its record: <b>${esc(call.phone)}</b></span></p>`
        + `<span class="fine">Digits hidden for this demo.</span></div>`;
    }
    return `<div class="call"><button type="button" class="btn-call" data-reveal="${esc(fid)}" aria-label="Show number to call: ${esc(name)}">${phoneIcon()}Show number to call</button>`
      + `<span class="fine">Number from its record (ends ${esc(call.phone.slice(-2))}). It may be out of date.</span></div>`;
  }
  return `<div class="call"><span class="fine nophone">No phone number in its record.</span></div>`;
}

function callBlock(a: Answer, c: Card, compact = false): string {
  const callHtml = callHtmlFor(a, c.facility_id, normCard(c, a).nameShort, c.call);
  const qs = (c.questions ?? []).slice(0, 3);
  const ask = qs.length
    ? `<div class="ask"><p class="ask-h">Before you go, ask:</p><ol class="asks">${qs.map((q) => `<li>${esc(plainQuestion(q.question))}${q.why ? `<span class="why">Why ask: ${esc(plainQuestion(q.why))}</span>` : ''}</li>`).join('')}</ol></div>`
    : '';
  if (!callHtml && !ask) return '';
  return `<div class="next${compact ? ' compact' : ''}">${callHtml}${ask}</div>`;
}

function phoneIcon(): string {
  return '<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6.6 10.8a15.5 15.5 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1z"/></svg>';
}

function cautionsHtml(v: CardView): string {
  const cs = cautions(v);
  if (!cs.length) return '';
  return `<ul class="careful">${cs.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>`;
}

/** SPEC 4.3 items 2 to 9: everything in the note under the headline. */
function cardBody(a: Answer, v: CardView, top = false): string {
  const d = distanceLine(v, a);
  const lead = leadQuote(a, v);
  const also = alsoLine(a, v);
  const src = sourcesLine(v.c);
  const order = top ? topOrderLine(a, v) : '';
  const more = also + (src ? `<details class="more phone-only"><summary>Sources</summary><p class="fine src">${esc(src)}</p></details>` : '');
  const emerg = top ? emergencyLine(a) : '';
  return `<p class="d-line">${d.lead ? `<b>${esc(d.lead)}</b>` : ''}${esc(d.rest)}</p>`
    + (emerg ? `<p class="emerg">${esc(emerg)}</p>` : '')
    + (order ? `<p class="tie">${esc(order)}</p>` : '')
    + (lead ? quoteBlock(lead, true) : '')
    + more
    + sureHtml(a, v, true)
    + publicLines(v.c)
    + callBlock(a, v.c)
    + (src ? `<p class="fine src lap-only">${esc(src)}</p>` : '')
    + cautionsHtml(v);
}

// ------------------------------------------------------------------ the note (SPEC 4.3)

export function noteHtml(a: Answer): string {
  const c = a.results[0];
  const v = normCard(c, a);
  const h = headline(a, v).replace(v.nameShort, '\u0000');
  const [h1, h2] = h.split('\u0000');
  return `<section class="note" aria-labelledby="note-h" data-idx="0">
    <h1 id="note-h" tabindex="-1">${esc(h1)}<button type="button" class="fac" data-focus-result="0" aria-label="${esc(v.nameShort)}: show it on the map" title="Show it on the map">${esc(v.nameShort)}${esc((/^[,.]/.exec(h2 ?? '') ?? [''])[0])}</button>${esc((h2 ?? '').replace(/^[,.]/, ''))}</h1>
    ${v.nameRest ? `<p class="name-rest">${esc(v.nameRest)}</p>` : ''}
    ${cardBody(a, v, true)}
  </section>`;
}

// ------------------------------------------------------------------ backups (SPEC 4.4)

function backupRow(a: Answer, c: Card, i: number, prev: Card | null, kind: 'result' | 'further'): string {
  const v = normCard(c, a);
  const p = prev ? normCard(prev, a) : null;
  const lead = leadQuote(a, v);
  const where = [typeWord(c.type), operatorWord(c.operator), c.city].filter(Boolean).join(', ');
  const time = v.time ? v.time.short : '';
  const sub = v.approx ? (time ? 'position approximate, so the time is rough' : 'position approximate')
    : v.time?.loose ? `from the middle; ${v.time.range} across ${shortPlace(a)}` : v.distanceLabel;
  const order = p && kind === 'result' ? `<p class="tie">${esc(orderSentence(a, p, v))}</p>` : '';
  const src = sourcesLine(c);
  // the call button stays visible; the questions and sources fold away
  const callVis = callHtmlFor(a, c.facility_id, v.nameShort, c.call);
  const qs = (c.questions ?? []).slice(0, 3);
  const askQ = qs.length ? `<div class="ask"><p class="ask-h">Before you go, ask:</p><ol class="asks">${qs.map((q) => `<li>${esc(plainQuestion(q.question))}${q.why ? `<span class="why">Why ask: ${esc(plainQuestion(q.why))}</span>` : ''}</li>`).join('')}</ol></div>` : '';
  const fold = askQ || src
    ? `<details class="more"><summary>Questions to ask, and sources</summary>${askQ}${src ? `<p class="fine src">${esc(src)}</p>` : ''}</details>` : '';
  const callRow = callVis ? `<div class="next compact">${callVis}</div>` : '';
  const num = kind === 'result' ? String(i + 1) : '';
  const attr = kind === 'result' ? `data-idx="${i}"` : `data-further="${i}"`;
  return `<li class="bk" ${attr}>
    <span class="num${v.approx ? ' approx' : ''}${kind === 'further' ? ' far' : ''}" aria-hidden="true">${esc(num)}</span>
    <div class="bk-main">
      <div class="bk-top">
        <div class="bk-name"><h3><button type="button" class="fac-link" data-focus-${kind}="${i}" aria-label="${esc(v.nameShort)}: show it on the map" title="Show it on the map">${esc(v.nameShort)}</button></h3>
          ${v.nameRest ? `<p class="name-rest">${esc(v.nameRest)}</p>` : ''}
          <p class="meta">${esc(where)}${v.crossesState && v.stateDisplay ? (isIsland(a) ? `. In ${esc(v.stateDisplay)}: across the sea, and sea or air travel is not modelled` : `. In ${esc(v.stateDisplay)}: across the state border`) : ''}</p></div>
        <div class="d">${time ? `<b>${esc(time)}</b>` : ''}<span>${esc(sub)}</span></div>
      </div>
      ${order}
      ${lead ? quoteBlock(lead) : ''}
      ${sureHtml(a, v)}
      ${publicLines(c)}
      ${cautionsHtml(v)}
      ${callRow}
      ${fold}
    </div>
  </li>`;
}

export function backupsHtml(a: Answer): string {
  const rows = a.results.slice(1, 3);
  if (!rows.length) return '';
  return `<section class="backups" aria-label="Other options"><h2>Other options</h2>
    <ol>${rows.map((c, k) => backupRow(a, c, k + 1, a.results[k], 'result')).join('')}</ol></section>`;
}

// ------------------------------------------------------------------ further away (SPEC 4.5)

export function furtherLine(a: Answer, list: Card[], offset = 0): string {
  const items = list.slice(0, 3);
  if (!items.length) return '';
  const parts = items.map((c, k) => {
    const v = normCard(c, a);
    const t = v.time ? `, ${v.time.short}` : `, ${v.approx ? '' : v.distanceLabel}`;
    return `<button type="button" class="fac-link" data-focus-further="${k + offset}">${esc(v.nameShort)}</button>${esc(t.replace(/, $/, ''))}${v.approx ? ' (position approximate)' : ''}`;
  });
  return `<p class="further">Further away (beyond ${esc(hoursText(a))}): ${parts.join('; ')}.</p>`;
}

// ------------------------------------------------------------------ why not (SPEC 4.6)

function whyItem(a: Answer, it: WhyNotItem, simOk: boolean): string {
  const qa = it.quote_or_absence;
  let ev = '';
  if (qa?.kind === 'quote') {
    ev = `<blockquote class="q sm grey"><span class="qt">${qa.negated ? 'Its record says: ' : ''}&ldquo;${esc(qa.text.length > 160 ? qa.text.slice(0, 157).replace(/\s+\S*$/, '') + '...' : qa.text)}&rdquo;</span>${qa.field ? `<span class="qf">${esc(fieldWords(qa.field))}</span>` : ''}</blockquote>`;
  } else if (qa?.kind === 'absence' && !/found no fact/i.test(it.reason_plain)) {
    ev = `<p class="absence"><em>${esc(qa.text)}</em></p>`;
  }
  const nm = fixName(it.name);
  const wi = it.in_sim && simOk
    ? `<button type="button" class="wi-link" data-verify="${esc(it.facility_id)}" aria-label="For planners: what if ${esc(nm)} were verified?">For planners: what if it were verified?</button>` : '';
  // the strip speaks in minutes like the cards when the server sent them; approximate positions say so
  const d = it.time_short ? `${it.time_short}${a.origin?.loose ? ' from the middle' : ''}` : it.distance_label;
  const pub = (it.operator ?? '').toLowerCase() === 'public' || it.reason_code === 'public_record_silent';
  const reason = plainServer(it.reason_plain);
  const act = pub && !/ask the hospital/i.test(reason)
    ? `<p class="wn-act"><b>Public hospital nearby:</b> call and ask whether they can provide ${esc(serviceName(a))} today.</p>`
    : pub ? `<p class="wn-act"><b>Public hospital nearby:</b> call and ask.</p>` : '';
  const capped = it.reason_code === 'ranked_lower' ? `<p class="wn-act">Not shown above only because we list at most 3.</p>` : '';
  const gov = publicLines({ outside_lines: it.outside_lines });
  const call = pub && it.call ? `<div class="next compact">${callHtmlFor(a, it.facility_id, nm, it.call)}</div>` : '';
  return `<li data-whynot="${esc(it.letter)}">
    <span class="dia" aria-hidden="true"><b>${esc(it.letter)}</b></span>
    <div><p class="wn-top"><b>${esc(nm)}</b> <span class="wn-d">${esc(d)}</span></p>
      <p class="wn-why">${esc(reason)}</p>${ev}${gov}${act}${call}${capped}${wi}</div>
  </li>`;
}

/** The strip's heading, counted from the rows it shows (the server's heading counts records the strip may leave out). */
function whyNotHeading(a: Answer, items: WhyNotItem[]): string {
  const first = a.results[0] ? normCard(a.results[0], a).nameShort : a.beyond_radius ? normCard(a.beyond_radius, a).nameShort : 'the first option';
  const exact = items.filter((it) => it.distance_km != null).length;
  const approx = items.length - exact;
  const m = /(\d+) more records? (?:has|have) an approximate position/.exec(a.why_not?.heading ?? '');
  const hidden = m ? Math.max(0, Number(m[1]) - approx) : 0;
  const parts: string[] = [];
  if (exact) parts.push(`${count(exact)} ${exact === 1 ? 'is' : 'are'} closer than ${first}`);
  if (approx) parts.push(`${count(approx)} ${approx === 1 ? 'has' : 'have'} an approximate map position that looks closer`);
  let more = '';
  if (hidden) {
    // name them from unverified_nearby (records that list the service nearby but were not recommended)
    const shown = new Set(items.map((it) => it.facility_id));
    const res = new Set(a.results.map((c) => c.facility_id));
    const names = (a.unverified_nearby ?? []).filter((c) => !shown.has(c.facility_id) && !res.has(c.facility_id))
      .slice(0, hidden).map((c) => {
        const n = fixName(c.name.split(',')[0]);
        const p = c.location_precision_km ?? 0;
        const d = c.distance_km != null ? `${c.distance_km < 10 ? c.distance_km.toFixed(1) : count(c.distance_km)} km` : '';
        return p >= 10 && d ? `${n} (its map point is ${d} away, but its position is known only to about ${count(p)} km)` : `${n} (position approximate)`;
      });
    more = ` ${count(hidden)} more with an approximate position ${hidden === 1 ? 'is' : 'are'} not listed${names.length ? `, among them ${names.join('; ')}` : ''}.`;
  }
  return `Showing ${count(items.length)} ${items.length === 1 ? 'record' : 'records'} that list or claim ${serviceName(a)}, or are public hospitals, but were not recommended: ${parts.join('; ')}.${more}`;
}

/** Public hospitals in the why-not strip, named once under the options: for many patients the public
 * hospital is the default referral (free delivery under JSSK), even when its web record is silent. */
export function publicNearbyHtml(a: Answer): string {
  const pubs = (a.why_not?.items ?? []).filter((it) => (it.operator ?? '').toLowerCase() === 'public' || it.reason_code === 'public_record_silent');
  if (!pubs.length) return '';
  const parts = pubs.slice(0, 3).map((it) => `${fixName(it.name)}${it.time_short ? `, ${it.time_short}` : it.position_approximate ? ' (position approximate)' : ''}`);
  return `<p class="pubnear"><b>Public hospitals nearby:</b> ${esc(parts.join('; '))}. Their records do not name ${esc(serviceName(a))}, so they are not listed above; call and ask. <button type="button" class="fac-link" data-open-whynot="1">Numbers and details</button></p>`;
}

export function whyNotHtml(a: Answer, simOk: boolean): string {
  const w = a.why_not;
  if (!w || (!w.items?.length && !w.heading)) return '';
  if (!w.items.length) return `<p class="whynot none">${esc(plainServer(w.heading))}</p>`;
  const heading = whyNotHeading(a, w.items);
  return `<details class="fold whynot"><summary><b>Why not the nearer ones?</b> <span>${esc(heading)}</span></summary>
    <ul>${w.items.map((it) => whyItem(a, it, simOk)).join('')}</ul>
    <p class="fine closing">${esc(WHYNOT_CLOSING)}</p></details>`;
}

// ------------------------------------------------------------------ how we decided (SPEC 4.7)

/** Technical detail is still read by people: no file paths, tool names or internal words. */
function techPlain(t: string): string {
  return plainServer(t)
    .replace(/\s*\([^)]*(?:\.csv|\.py|\.parquet|\/)[^)]*\)/g, '')
    .replace(/\b[\w./-]+\.(?:csv|py|parquet|json)\b/g, 'a reference table')
    .replace(/\bOSRM\b/g, 'a road-routing comparison')
    .replace(/\b(?:the )?FRU 20\d\d s[\d.]+/g, 'the government first-referral-unit guidance')
    .replace(/\bno-traffic \(no traffic, no waiting\)/g, 'no traffic and no waiting')
    .replace(/\b([Gg])rades\b/g, (_m, g: string) => (g === 'G' ? 'Record levels' : 'record levels'))
    .replace(/\b([Gg])rade\b/g, (_m, g: string) => (g === 'G' ? 'Record level' : 'record level'));
}

/** Sentences with decimals or formulas are left out of the plain fold (SPEC 4.7 item 5). */
function plainMethod(text: string): string {
  const sentences = text.split(/(?<=\.)\s+(?=[A-Z])/).map((s) => s.trim()).filter(Boolean);
  const keep = sentences.filter((s) => !/\d\.\d|\^|=|\be\^/.test(s));
  const lead = sentences.length && keep[0] !== sentences[0]
    ? ['Estimated drive time comes from the straight-line distance, with a factor for the terrain of the state.'] : [];
  return [...lead, ...keep].join(' ');
}

function keywordHow(a: Answer): string {
  const h = a.how_we_decided ?? {};
  const gates = (h.gates ?? []).filter((g) => g.n != null);
  const glist = gates.length
    ? `<ol class="funnel" style="--n:${gates.length}">${gates.map((g) => `<li><b>${esc(count(g.n))}</b><span>${esc(plainServer(g.rule ?? g.gate))}</span></li>`).join('')}</ol>` : '';
  const sim = Math.max(0, ...(a.results.concat(a.beyond_radius ? [a.beyond_radius] : []))
    .flatMap((c) => (c.evidence ?? []).map((e) => Number((e as { similarity?: number }).similarity ?? 0))));
  const limits = h.limits ?? [];
  return `<details class="fold how"><summary><b>How we decided</b></summary>
    ${glist}
    <p>${esc(ruleParagraph(a))}</p>
    <p>${esc(windowLine(a))}</p>
    ${sim > 0 ? `<p>The best match shown has a text similarity of ${esc(sim.toFixed(2))} (1 would be the same words).</p>` : ''}
    <p>${esc(CITATION_LINE)}</p>
    <details class="tech"><summary>Technical detail</summary>${h.formula ? `<p>${esc(plainServer(String(h.formula)))}</p>` : ''}${limits.length ? `<p class="t-k">Limits</p><ul>${limits.map((r) => `<li>${esc(techPlain(r))}</li>`).join('')}</ul>` : ''}</details>
  </details>`;
}

export function howHtml(a: Answer): string {
  if (a.method === 'keyword') return keywordHow(a);
  const steps = a.funnel?.steps ?? [];
  const funnel = steps.length
    ? `<ol class="funnel" style="--n:${steps.length}">${steps.map((s, i) => `<li><b>${esc(count(s.n))}</b><span>${esc(funnelLabel(s, a, i === steps.length - 1))}${s.n_distinct != null && s.n_distinct !== s.n ? ` (${esc(count(s.n_distinct))} distinct facilities; some records are duplicates)` : ''}</span></li>`).join('')}</ol>`
    : '';
  const w = a.window;
  const h = a.how_we_decided ?? {};
  const tm = (h.travel_model as { text?: string } | undefined)?.text;
  const recipe = Array.isArray(h.recipe) ? h.recipe : h.recipe ? [h.recipe] : [];
  const limits = h.limits ?? [];
  const tech = [
    tm ? `<p>${esc(techPlain(plainMethod(tm)))}</p>` : '',
    recipe.length ? `<p class="t-k">How a record is read for ${esc(serviceName(a))}</p><ul>${recipe.map((r) => `<li>${esc(techPlain(r))}</li>`).join('')}</ul>` : '',
    limits.length ? `<p class="t-k">Limits</p><ul>${limits.map((r) => `<li>${esc(techPlain(r))}</li>`).join('')}</ul>` : '',
  ].join('');
  return `<details class="fold how"><summary><b>How we decided</b></summary>
    ${funnel}
    <p>${esc(ruleParagraph(a))}</p>
    ${w ? `<p>${esc(windowLine(a))}${w.note ? ` ${esc(plainServer(w.note))}` : ''}</p>` : ''}
    ${w?.reach_text || w?.reach_minutes ? `<p>To explain what is missing, we also look further: up to about ${esc(reachText(a))} away. That wider search is only used for the counts above, never to recommend a place.</p>` : ''}
    <p>${esc(CITATION_LINE)}</p>
    ${tech ? `<details class="tech"><summary>Technical detail</summary>${tech}</details>` : ''}
  </details>`;
}

export function footerHtml(): string {
  return `<p class="foot">${esc(FOOTER_LINE)}</p>`;
}

// ------------------------------------------------------------------ the trip teaser (SPEC 4.8)

export function teaserHtml(s: SimPayload | null, canRun: boolean): string {
  if (!s || s.status !== 'ok') return '';
  const btns = canRun
    ? `<div class="t-act"><button type="button" class="btn-play" data-sim="play">&#9654; Play it on the map</button>
       <button type="button" class="btn-ghost" data-sim="whatif">Try a what-if: add, close or check a place</button></div>`
    : `<p class="fine">The village simulator is not part of this build yet.</p>`;
  return `<section class="teaser" aria-labelledby="teaser-h">
    <h3 class="t-k" id="teaser-h">Where would nearby villages go?</h3>
    <p class="t-big">${esc(teaserSentence(s))}</p>
    <p class="fine">Each moving dot is a patient from a nearby village. Nobody draws their route: each one picks a place by a rule, and picks again if you add, close or check a place.</p>
    ${btns}
  </section>`;
}

/** The planner blocks, after every option, the why-not strip and How we decided (never between options). */
export function plannersHtml(teaser: string, whatIf: string): string {
  if (!teaser && !whatIf) return '';
  return `<section class="planners" aria-labelledby="planners-h">
    <h2 id="planners-h">For planners</h2>
    <p class="fine">Not about this patient: how the area around this place is served, and what would change it.</p>
    ${whatIf}${teaser}
  </section>`;
}

// ------------------------------------------------------------------ abstention (SPEC 4.9)

/** One lane row: name, what the list says, the list and its page and row, and where it is (D057 derived facts). */
function outsideRow(o: OutsideNearby): string {
  const ref = /\((Government list, [^)]*?page \d+ row \d+)\)/.exec(o.text)?.[1]
    ?? /\(([^()]*(?:page|row)[^()]*)\)/.exec(o.text)?.[1] ?? '';
  const name = o.name ? `${o.name}${o.district ? `, ${titleCaseName(o.district)}` : ''}` : '';
  const what = o.what ?? '';
  const districtOnly = /district/i.test(o.position_note ?? '');
  const where = districtOnly ? 'Position: district only, so no distance is given.'
    : o.km != null ? `About ${count(o.km)} km in a straight line${o.position_note ? ` (position approximate: ${o.position_note})` : ''}.` : '';
  if (!name || !what) return `<li><span class="gov-tag">${esc(o.source_name ?? 'Government list')}</span> ${esc(o.text)}</li>`;
  return `<li><span class="gov-tag">${esc(o.source_name ?? 'Government list')}</span> <b>${esc(name)}</b>: ${esc(what)}.${ref ? ` <span class="fine">${esc(ref)}.</span>` : ''} <span class="fine">${esc(where)}</span></li>`;
}

function titleCaseName(s: string): string {
  return s.toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
}

export function outsideLane(list: OutsideNearby[] | undefined): string {
  if (!list?.length) return '';
  return `<section class="outside"><h2>Also on government or registry lists nearby (not matched to a record in this dataset)</h2>
    <p class="fine">Outside data, cited to the list. Not checked against our records' text, and never used to pick the options above.</p>
    <ul>${list.map(outsideRow).join('')}</ul></section>`;
}

export function abstainHtml(a: Answer): string {
  const b = a.beyond_radius;
  const further = (a.further_away ?? []).filter((c) => !b || c.facility_id !== b.facility_id);
  const about = further.slice(0, 2);
  const rest = further.slice(2);
  const gap = gapLine(a);
  let nested = '';
  if (b) {
    const v = normCard(b, a);
    nested = `<section class="nearest" data-idx="0">
      <h2 class="nest-k">${a.method === 'keyword' ? `Nearest record with a word match for ${esc(serviceName(a))} (not checked)` : `Nearest place whose record names ${esc(serviceName(a))}`}${isIsland(a) && v.crossesState ? ': on the mainland, across the sea (sea or air travel is not modelled)' : ''}</h2>
      <div class="nest-head"><span class="num${v.approx ? ' approx' : ''}" aria-hidden="true">1</span>
        <h3><button type="button" class="fac-link" data-focus-result="0">${esc(v.nameShort)}</button></h3></div>
      ${v.nameRest ? `<p class="name-rest">${esc(v.nameRest)}</p>` : ''}
      ${cardBody(a, v)}
      ${(() => { const t = strongerNearby(a, v, about); return t ? `<p class="compare"><b>Also consider:</b> ${esc(t)}</p>` : ''; })()}
    </section>`;
  }
  const aboutHtml = about.length
    ? `<section class="backups" aria-label="Further away, about the same distance"><h2>Further away, about the same distance (beyond ${esc(hoursText(a))})</h2><ol>${about.map((c, k) => backupRow(a, c, k, null, 'further')).join('')}</ol></section>`
    : '';
  return `<section class="note abstain" aria-labelledby="note-h">
      <h1 id="note-h">${esc(abstainHeadline(a))}</h1>
      <p class="honest">${esc(HONESTY_LINE)}</p>
      <p class="nextstep"><b>Next step:</b> ${esc(abstainNextStep(a))}</p>
      ${gap ? `<p class="gap">${esc(gap)}</p>` : ''}
      ${nested}
    </section>
    ${aboutHtml}
    ${furtherLine(a, rest, 2)}
    ${outsideLane(a.outside_nearby)}`;
}

export function whatIfTeaserHtml(sentence: string): string {
  return `<section class="teaser wi" aria-label="Imagined what-if, not real"><p><span class="tag-wi">What-if: imagined, not real</span></p>
    <p class="t-big">${esc(sentence)}</p>
    <div class="t-act"><button type="button" class="btn-wi" data-sim="add">See the imagined unit on the map</button></div></section>`;
}

// ------------------------------------------------------------------ landing, ambiguous, errors (SPEC 4.10)

export function landingHtml(): string {
  return `<section class="landing"><p class="intro">${esc(INTRO_LINE)}</p></section>`;
}

export function ambiguousHtml(r: Resolution, typed: string): string {
  const cands = r.candidates ?? [];
  const btns = cands.map((c: Candidate, i) => {
    const kind = c.kind === 'district' ? 'district' : c.kind === 'post_office' ? 'post office' : (c.kind ?? '').replace(/_/g, ' ');
    const st = (c as Candidate & { state?: string }).state;
    const label = st && !c.label.toLowerCase().includes(st.toLowerCase()) ? `${c.label}, ${st}` : c.label;
    return `<button type="button" class="cand" data-cand="${i}">${esc(label)}${kind ? ` <span class="cand-kind">${esc(kind)}</span>` : ''}</button>`;
  }).join('');
  return `<section class="ask-place"><h1 tabindex="-1">${esc(count(cands.length))} places are called ${esc(typed)}. Which one?</h1><div class="cands">${btns}</div></section>`;
}

export function problemHtml(r: Resolution, typed: string): string {
  if (r.status === 'outside_india') return outsideIndia(r.how);
  const script = /[^\u0000-\u024f]/.test(typed) ? ' Please type the place name in English letters (for example Jokihat), or a 6-digit PIN code.' : '';
  const coords = looksLikeCoords(typed) ? ' Those numbers were not read as a place in India: check them, or write them as latitude, longitude (for example 26.91, 75.79).' : '';
  const hint = script || coords || (/\btry\b/i.test(r.how ?? '') ? '' : ' Try a 6-digit PIN code, a district, a city, or coordinates such as 26.91, 75.79.');
  return `<div class="problem" role="alert"><h1 tabindex="-1">We could not find "${esc(typed)}".</h1><p>${esc(plainServer(r.how ?? ''))}${hint}</p></div>`;
}

function outsideIndia(how: string | null | undefined): string {
  const h = how && !/outside india/i.test(how) ? `${esc(how)} ` : '';
  return `<div class="problem" role="alert"><h1 tabindex="-1">That place is outside India.</h1><p>${h}This tool only covers facilities in India. Check the coordinates, or type a PIN code, district or city.</p></div>`;
}

export function errorHtml(msg: string, retry = false): string {
  return `<div class="problem" role="alert"><p><b>No answer.</b> ${esc(msg)}</p>${retry ? '<p><button type="button" class="btn-ghost" data-retry="1">Try again</button></p>' : ''}</div>`;
}

/** Keyword answers whose words name a listed service: offer the checked answer. */
export function didYouMeanHtml(a: Answer): string {
  const d = a.did_you_mean;
  if (a.method !== 'keyword' || !d) return '';
  return `<p class="dym">Your words name a service we check record by record. <button type="button" class="btn-ghost" data-service="${esc(d.service)}">Show the checked answer for ${esc(d.label)}</button></p>`;
}

/** Shown while names and phone digits are masked, so a recording can be checked before it starts. */
export function demoBadgeHtml(a: Answer): string {
  return a.demo_masked ? '<span class="qchip demo"><em>Demo</em>names and phone digits hidden</span>' : '';
}

export { shortPlace };
