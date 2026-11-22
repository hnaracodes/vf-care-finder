// The simulator's controls and read-outs (SPEC 6): the sim bar and the
// takeaway over the map, the rail (or the phone sheet), the WHAT-IF banner
// and the click popover. Every sentence is a template from the spec filled
// with numbers from /api/sim or from the browser's run of the stated rules.

import './sim.css';
import type { Mode, Tally } from './agents';
import type { SimPayload } from './contract';
import type { ExpandSlots } from './host';
import type { Counters, VerifyFirst, VerifyRow } from './rules';
import { HEX } from './tokens';
import { haversineKm } from './world';

export type Tool = 'verify' | 'add' | 'close';

export interface VillageTally { n: number; arrived: number; onWay: number; notLeft: number; stuck: number; stoppedFirst: number; turned: number }

export interface PanelHandlers {
  onAddAt(lon: number, lat: number, label: string): void;
  onCloseId(facility_id: string): void;
  onHoverFacility(facility_id: string | null): void;
  onMode(m: Mode): void;
  onPlay(): void;
  onPause(): void;
  onReplay(): void;
  onTool(t: Tool | null): void;
  onUndo(id: number): void;
  onUndoLast(): void;
  onReset(): void;
  onVerifyFirst(): void;
  onVerifyId(facility_id: string): void;
  onCapacity(n: number | null): void;
  onClose(): void;
}

export interface EditRow { id: number; label: string }
export interface VerifyCandidate { facility_id: string; name: string; km_from_origin: number; approximate: boolean; held_back: boolean; verified: boolean }
export interface CloseCandidate { facility_id: string; name: string; km_from_origin: number; approximate: boolean; evidence: boolean }

/** A record whose name is only a place name ("Jaipur") reads like a bug: say what it is. */
export function displayName(name: string, p: SimPayload): string {
  const n = (name ?? '').trim().replace(/([A-Za-z])['\u2019]S\b/g, "$1's");
  const places = new Set([p.origin.short_name, ...(p.context?.labels ?? []).map((l) => l.name)].map((x) => (x ?? '').toLowerCase()));
  if (!n || places.has(n.toLowerCase())) return `Unnamed record in ${n || p.origin.short_name}`;
  return n;
}

type Child = Node | string | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string | boolean | ((e: Event) => void)> = {}, ...kids: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (typeof v === 'function') el.addEventListener(k.replace(/^on/, ''), v as EventListener);
    else if (v === true) el.setAttribute(k, '');
    else if (v !== false) el.setAttribute(k, v);
  }
  for (const c of kids) if (c !== null && c !== undefined && c !== false) el.append(c);
  return el;
}

/** The on-map legend rows (SPEC 6), only those relevant now. Swatch strings are "<shape> <colour>". */
export function legendRows(mode: Mode, edits: boolean, capacity: boolean): { swatch: string; label: string }[] {
  const rows: { swatch: string; label: string }[] = [];
  rows.push({ swatch: `dot ${HEX.EVIDENCE}`, label: 'Solid dot: a patient from a nearby village, heading to a place whose record shows it' });
  if (mode === 'claims') {
    rows.push({ swatch: `hollowdot ${HEX.CLAIM}`, label: 'Hollow dot: a patient heading to a place that only lists it (no evidence in its record)' });
    rows.push({ swatch: `ring ${HEX.CLAIM}`, label: "Waiting there: that place's record shows no evidence" });
    rows.push({ swatch: `dash ${HEX.CLAIM}`, label: 'The extra trip on, if that place turns the patient away' });
  }
  rows.push({ swatch: `smalldot ${HEX.EVIDENCE}`, label: 'Small green dot: a place whose record shows it' });
  rows.push({ swatch: `diamond ${HEX.CLAIM}`, label: 'Diamond: a place that lists it, no evidence in its record' });
  rows.push({ swatch: `villring ${HEX.NEUTRAL}`, label: 'Grey ring: a village with no place with evidence within the window' });
  if (edits) {
    rows.push({ swatch: `dot ${HEX.WHATIF}`, label: 'Purple dot: on the road when you made the change, so it turned' });
    rows.push({ swatch: `plus ${HEX.WHATIF}`, label: 'What-if facility' });
  }
  if (capacity) rows.push({ swatch: `ring ${HEX.NEUTRAL}`, label: 'Waiting at a full place (assumption, not from data)' });
  return rows;
}

export function swatchEl(swatch: string): HTMLElement {
  const [shape, color] = swatch.split(' ');
  const el = h('span', { class: `sim-sw sim-sw-${shape}` });
  el.style.setProperty('--c', color);
  return el;
}

const fmtMin = (m: number | null) => (m === null ? 'none' : `${Math.round(m)} min`);
const signed = (d: number, unit = '') => `${d > 0 ? '+' : d < 0 ? '-' : '+'}${Math.abs(d)}${unit}`;

export class Panel {
  readonly p: SimPayload;
  private hd: PanelHandlers;
  private slots: ExpandSlots;
  private phone: boolean;
  readonly bar: HTMLElement;
  readonly takeaway: HTMLElement;
  private tkObserver: ResizeObserver | null = null;
  readonly banner: HTMLElement;
  readonly body: HTMLElement;
  private popover: HTMLElement;
  private modeBtns: Record<Mode, HTMLButtonElement>;
  private playBtn: HTMLButtonElement;
  private progress: HTMLElement;
  private clock: HTMLElement;
  private speedEl: HTMLElement;
  private rule: HTMLElement;
  private tallyEl: HTMLElement;
  private table: HTMLElement;
  private editsEl: HTMLElement;
  private toolBtns: Record<Tool, HTMLButtonElement>;
  private toolHint: HTMLElement;
  private verifyList: HTMLElement;
  private vfOut: HTMLElement;
  private bannerCount: HTMLElement;
  private phoneLine: HTMLElement;
  private progressBar: HTMLElement;
  private key: HTMLElement | null = null;
  private closeList: HTMLElement;
  private addList: HTMLElement;
  private mode: Mode = 'evidence';
  private tool: Tool | null = null;

  constructor(p: SimPayload, hd: PanelHandlers, slots: ExpandSlots, opts: { phone: boolean; fixture: boolean }) {
    this.p = p;
    this.hd = hd;
    this.slots = slots;
    this.phone = opts.phone;

    // ---- sim bar
    const seg = (m: Mode, label: string) => h('button', { type: 'button', class: 'sim-seg-btn', 'aria-pressed': 'false', onclick: () => hd.onMode(m) }, label);
    this.modeBtns = { evidence: seg('evidence', "Trust the record's evidence"), claims: seg('claims', 'Trust listed specialties alone') };
    this.playBtn = h('button', { type: 'button', class: 'sim-btn sim-play', onclick: () => (this.playBtn.dataset.playing === '1' ? hd.onPause() : hd.onPlay()) }, '▶ Play');
    const replay = h('button', { type: 'button', class: 'sim-btn', onclick: () => hd.onReplay() }, '↺ Replay');
    this.progress = h('span', { class: 'sim-progress-fill' });
    this.progressBar = h('span', { class: 'sim-progress', role: 'progressbar', 'aria-label': 'Run progress', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' }, this.progress);
    this.clock = h('span', { class: 'sim-clock' }, 'minute 0');
    this.speedEl = h('span', { class: 'sim-speed' });
    this.rule = h('p', { class: 'sim-rule' });
    const caption = h('p', { class: 'sim-caption' },
      'Each moving dot is a patient from a nearby village (one village per post office area). It starts at its village and ends at the place it picks. No route is drawn in advance: each dot picks its place by the rule above, and picks again if you add, close or check a place. Paths are straight lines; times use the drive-time estimate with no traffic.');
    const badge = opts.fixture ? h('span', { class: 'sim-fixture', title: 'The live /api/sim is not available yet. This is a saved payload built from the same bundle with the same rules.' }, 'FIXTURE') : null;
    const closeBtn = h('button', { type: 'button', class: 'sim-x', 'aria-label': 'Close the simulation', onclick: () => hd.onClose() }, '×');
    this.bar = h('div', { class: 'sim-bar', role: 'region', 'aria-label': 'Where would nearby villages go?' },
      h('div', { class: 'sim-bar-row' },
        h('strong', { class: 'sim-title' }, 'Where would nearby villages go?'), badge,
        h('div', { class: 'sim-seg', role: 'group', 'aria-label': 'Which rule the patients follow' }, this.modeBtns.evidence, this.modeBtns.claims),
        h('div', { class: 'sim-player' }, this.playBtn, replay, this.progressBar, this.clock, this.speedEl),
        closeBtn),
      this.rule, caption);

    this.takeaway = h('div', { class: 'sim-takeaway', 'aria-live': 'polite' });

    // ---- banner
    this.bannerCount = h('span');
    this.banner = h('div', { class: 'sim-banner' },
      h('span', { class: 'sim-banner-text' }, this.bannerCount),
      h('button', { type: 'button', class: 'sim-btn sim-btn-w', onclick: () => hd.onUndoLast() }, 'Undo last'),
      h('button', { type: 'button', class: 'sim-btn sim-btn-w', onclick: () => hd.onReset() }, 'Reset to real data'));

    // ---- rail / sheet
    this.tallyEl = h('p', { class: 'sim-tally' });
    this.table = h('div', { class: 'sim-table' });
    const tb = (t: Tool, label: string, aria: string) => h('button', { type: 'button', class: 'sim-btn sim-tool', 'aria-pressed': 'false', 'aria-label': aria, onclick: () => hd.onTool(this.tool === t ? null : t) }, label);
    this.toolBtns = {
      verify: tb('verify', 'Check a record', 'What-if: check a record that lists it, as if it were verified'),
      add: tb('add', 'Add a facility', 'What-if: add a facility'),
      close: tb('close', 'Close a facility', 'What-if: close a facility'),
    };
    this.toolHint = h('p', { class: 'sim-hint' });
    this.verifyList = h('div', { class: 'sim-vlist' });
    this.closeList = h('div', { class: 'sim-vlist' });
    this.addList = h('div', { class: 'sim-vlist' });
    this.editsEl = h('div', { class: 'sim-edits' });
    const vfBtn = h('button', { type: 'button', class: 'sim-btn sim-vf-btn', onclick: () => hd.onVerifyFirst() }, 'Which record should be checked first?');
    this.vfOut = h('div', { class: 'sim-vf' });
    this.phoneLine = h('div', { class: 'sim-phone-line' });
    const capIn = h('input', { type: 'number', min: '1', max: '200', value: '6', class: 'sim-cap-n', 'aria-label': 'patients per hour' }) as HTMLInputElement;
    const capCb = h('input', { type: 'checkbox', class: 'sim-cap-cb' }) as HTMLInputElement;
    const capChange = () => hd.onCapacity(capCb.checked ? Math.max(1, Math.round(Number(capIn.value) || 6)) : null);
    capCb.addEventListener('change', capChange);
    capIn.addEventListener('change', () => { if (capCb.checked) capChange(); });
    const capacity = h('div', { class: 'sim-cap' },
      h('label', {}, capCb, ' Assume each place handles at most ', capIn, ' patients per hour'),
      h('span', { class: 'sim-assume' }, 'Assumption, not from data'));
    const seed = h('p', { class: 'sim-seed' }, 'Same seed, same run, every time (seed 7).');

    const toolRow = h('div', { class: 'sim-tools' }, this.toolBtns.verify, this.toolBtns.add, this.toolBtns.close);
    const whatif = h('section', { class: 'sim-whatif', 'aria-labelledby': 'sim-wi-h' },
      h('span', { class: 'sim-tag' }, 'WHAT-IF'),
      h('h3', { class: 'sim-wi-h', id: 'sim-wi-h' }, 'Try a what-if: add, close or check a place'),
      h('p', { class: 'sim-whatif-intro' }, 'Change the map and watch the dots decide again. Nothing here changes the real data.'),
      toolRow, this.toolHint, this.verifyList, this.addList, this.closeList, this.editsEl);
    const jump = h('button', { type: 'button', class: 'sim-btn sim-jump', onclick: () => {
      whatif.scrollIntoView({ behavior: 'smooth', block: 'start' });
      this.toolBtns.add.focus({ preventScroll: true });
    } }, 'What-if ↓');

    this.body = h('div', { class: `sim-body${this.phone ? ' sim-phone' : ''}` });
    if (this.phone) {
      if (badge) badge.remove();
      this.bar.classList.add('sim-bar-phone');
      toolRow.append(vfBtn);
      this.body.append(
        h('div', { class: 'sim-sheet-head' }, h('h2', { class: 'sim-sheet-title' }, 'Where would nearby villages go?', opts.fixture ? h('span', { class: 'sim-fixture' }, 'FIXTURE') : null), jump),
        h('div', { class: 'sim-seg' }, this.modeBtns.evidence, this.modeBtns.claims),
        this.rule,
        h('div', { class: 'sim-player' }, this.playBtn, replay, this.progressBar, this.clock, this.speedEl),
        this.takeaway,
        this.tallyEl, whatif, this.phoneLine, this.vfOut, this.table, caption, capacity, seed);
      closeBtn.textContent = '× Close the simulation';
      this.bar.replaceChildren(closeBtn);
    } else {
      this.body.append(this.tallyEl, whatif, h('div', { class: 'sim-vf-wrap' }, vfBtn, this.vfOut), this.table, capacity, seed);
    }
    // the map key, docked in the rail by the map: after the counts, before the what-if tools
    if (slots.key) {
      slots.key.classList.add('sim-key');
      this.tallyEl.after(slots.key);
      this.key = slots.key;
    }
    this.popover = h('div', { class: 'sim-pop', hidden: true });

    const host = this.phone ? slots.sheet : slots.rail;
    host.append(this.body);
    slots.mapEl.append(this.bar, this.popover);
    if (!this.phone) {
      slots.mapEl.append(this.takeaway);
      // The map's floating legend sits above the takeaway (sim.css reads --sim-tk-h), so the
      // two never cover each other however many lines the takeaway has.
      this.tkObserver = new ResizeObserver(() => slots.mapEl.style.setProperty('--sim-tk-h', `${this.takeaway.offsetHeight}px`));
      this.tkObserver.observe(this.takeaway);
    }
    this.setMode('evidence');
    this.speedEl.textContent = '';
  }

  destroy(): void {
    if (this.key) { this.key.classList.remove('sim-key'); this.key.remove(); this.key = null; }
    this.tkObserver?.disconnect();
    this.tkObserver = null;
    this.slots.mapEl.style.removeProperty('--sim-tk-h');
    this.bar.remove();
    this.takeaway.remove();
    this.body.remove();
    this.popover.remove();
    this.banner.remove();
  }

  // ------------------------------------------------------------------ state

  setMode(m: Mode): void {
    this.mode = m;
    for (const k of ['evidence', 'claims'] as Mode[]) {
      this.modeBtns[k].setAttribute('aria-pressed', String(k === m));
      this.modeBtns[k].classList.toggle('on', k === m);
    }
    const p = this.p;
    this.rule.textContent = m === 'evidence'
      ? `The rule the answer uses: each patient goes to the nearest place whose record shows ${p.service_phrase} (within ${p.window.hours_text} if there is one). A place whose record names more of what the care needs may be chosen if it is at most ${Math.round(p.window.allowance)} minutes further.`
      : `For comparison: each patient goes to the nearest place that lists ${p.service_phrase} as a specialty. If that place's record shows no evidence of it, the patient waits there, and if it turns them away, goes on to the nearest place whose record does show it.`;
    this.rule.classList.toggle('sim-rule-claims', m === 'claims');
  }

  setPlaying(on: boolean, done: boolean): void {
    this.playBtn.dataset.playing = on ? '1' : '0';
    this.playBtn.textContent = on ? '❚❚ Pause' : done ? '▶ Play again' : '▶ Play';
  }

  setTick(time: number, speed: number, progress: number, t: Tally, v: VillageTally, capacity: boolean): void {
    this.clock.textContent = `minute ${Math.floor(time)}`;
    this.speedEl.textContent = `1 second = ${speed} minutes`;
    const pct = Math.round(Math.min(1, progress) * 100);
    this.progress.style.width = `${pct}%`;
    this.progressBar.setAttribute('aria-valuenow', String(pct));
    // counted in villages only, so the numbers add up: arrived + on the way + not yet left (+ no place) = all
    let s = `${v.n} villages: ${v.arrived} reached care, ${v.onWay} on the way`;
    if (v.notLeft) s += `, ${v.notLeft} not yet left`;
    if (v.stuck) s += `, ${v.stuck} with no place with evidence to go to`;
    s += '.';
    if (this.mode === 'claims' || v.stoppedFirst) s += ` Of all ${v.n}, ${v.stoppedFirst} stopped first at a place with no evidence.`;
    if (v.turned) s += ` ${v.turned} turned on the road after your what-if change.`;
    if (capacity) s += ` ${t.queued} patients waiting at a full place (assumption).`;
    this.tallyEl.textContent = s;
  }

  /** The three counters, the takeaway and the banner. `now` is null when there are no edits. */
  setCounters(real: Counters, now: Counters | null, nEdits: number): void {
    const p = this.p;
    const cur = now ?? real;
    const changed = (a: number | null, b: number | null) => now !== null && a !== b;
    const num = (v: string, hot: boolean) => h('span', { class: hot ? 'sim-num sim-hot' : 'sim-num' }, v);
    // takeaway
    const tk = this.takeaway;
    tk.replaceChildren();
    if (now) tk.append(h('span', { class: 'sim-hot' }, 'What-if: '));
    if (this.mode === 'claims') {
      tk.append('If people trusted listed specialties alone, ', num(String(cur.first_no_evidence), changed(real.first_no_evidence, cur.first_no_evidence)),
        ` of ${cur.n} nearby villages would go first to a place with no evidence of ${p.service_phrase} in its record.`);
      if (cur.extra_minutes_mean !== null && cur.first_no_evidence > 0) {
        tk.append(' If that place turns them away, the trip is about ', num(String(Math.round(cur.extra_minutes_mean)), changed(real.extra_minutes_mean, cur.extra_minutes_mean)), ' min longer on average.');
      }
    } else {
      tk.append(num(String(cur.within), changed(real.within, cur.within)),
        ` of ${cur.n} nearby villages are within ${p.window.hours_text} of a place whose record shows ${p.service_phrase}.`);
      if (cur.avg_minutes !== null) {
        tk.append(` Averaged over all ${cur.n} nearby villages (here a village is one India Post office area within about ${Math.round(p.villages.radius_km)} km), the drive to the nearest one is about `, num(String(Math.round(cur.avg_minutes)), changed(real.avg_minutes, cur.avg_minutes)),
          ` min (the cards give the time from ${p.origin.short_name} itself).`);
      } else {
        tk.append(` We found no place in this dataset whose record shows ${p.service_phrase} in reach.`);
      }
      const out = cur.n - cur.within;
      if (out > 0 && cur.avg_minutes !== null) tk.append(` ${out} ${out === 1 ? 'village' : 'villages'} (grey rings) ${out === 1 ? 'has' : 'have'} none within ${p.window.hours_text}.`);
    }
    // table
    const rows: [string, string, string | null, string | null][] = [];
    const wn = (c: Counters) => `${c.within} of ${c.n}`;
    rows.push([`Villages within ${p.window.hours_text} of a place with evidence`, wn(real), now ? wn(now) : null, now ? signed(now.within - real.within) : null]);
    const avgD = now && real.avg_minutes !== null && now.avg_minutes !== null ? Math.round(now.avg_minutes - real.avg_minutes) : null;  // from unrounded values
    rows.push(['Average estimated drive to the nearest one', fmtMin(real.avg_minutes), now ? fmtMin(now.avg_minutes) : null, now ? (avgD === null ? '' : signed(avgD, ' min')) : null]);
    rows.push(['Villages whose first stop has no usable evidence (none in its record, or held back by a warning), if they trust listed specialties', String(real.first_no_evidence), now ? String(now.first_no_evidence) : null, now ? signed(now.first_no_evidence - real.first_no_evidence) : null]);
    const t = h('table', { class: 'sim-counters' });
    const head = h('tr', {}, h('th', {}, ''), h('th', {}, 'Real data'));
    if (now) head.append(h('th', { class: 'sim-hot' }, 'What-if'), h('th', { class: 'sim-hot' }, 'Change'));
    t.append(h('thead', {}, head));
    const body = h('tbody');
    for (const [label, a, b, c] of rows) {
      const tr = h('tr', {}, h('th', { scope: 'row' }, label), h('td', {}, a));
      if (now) tr.append(h('td', { class: 'sim-hot' }, b ?? ''), h('td', { class: 'sim-hot' }, c ?? ''));
      body.append(tr);
    }
    t.append(body);
    this.table.replaceChildren(t);
    if (cur.n_no_evidence_in_reach > 0) this.table.append(h('p', { class: 'sim-note' }, `${cur.n_no_evidence_in_reach} villages have no place with evidence in reach.`));
    if (now && now.within === real.within && now.first_no_evidence === real.first_no_evidence && now.avg_minutes === real.avg_minutes) {
      this.table.append(h('p', { class: 'sim-note sim-hot' }, 'No change: every village already has a place at least as near as this one, so no patient would pick differently.'));
    }
    // banner
    this.bannerCount.textContent = `WHAT-IF VIEW · ${nEdits} edit${nEdits === 1 ? '' : 's'} · not real data`;
  }

  setEdits(rows: EditRow[]): void {
    this.editsEl.replaceChildren();
    if (!rows.length) { this.phoneLine.replaceChildren(); return; }
    const ul = h('ul', { class: 'sim-edit-list' });
    for (const r of rows) {
      ul.append(h('li', {}, h('span', {}, r.label), h('button', { type: 'button', class: 'sim-link', onclick: () => this.hd.onUndo(r.id) }, 'Undo')));
    }
    this.editsEl.append(ul, h('div', { class: 'sim-edit-actions' },
      h('button', { type: 'button', class: 'sim-btn', onclick: () => this.hd.onUndoLast() }, 'Undo last'),
      h('button', { type: 'button', class: 'sim-btn', onclick: () => this.hd.onReset() }, 'Reset to real data')));
    const last = rows[rows.length - 1];
    this.phoneLine.replaceChildren(h('span', { class: 'sim-hot' }, last.label), h('button', { type: 'button', class: 'sim-link', onclick: () => this.hd.onUndo(last.id) }, 'Undo'));
  }

  setTool(t: Tool | null, candidates: VerifyCandidate[]): void {
    this.tool = t;
    for (const k of ['verify', 'add', 'close'] as Tool[]) {
      this.toolBtns[k].setAttribute('aria-pressed', String(k === t));
      this.toolBtns[k].classList.toggle('on', k === t);
    }
    const p = this.p;
    this.toolHint.textContent = t === 'verify' ? 'Click an amber diamond on the map, or pick a record below. What if someone checked it and its record showed the service?'
      : t === 'add' ? `Click empty ground on the map to add a what-if ${p.service_phrase} unit there, or pick a place below.`
      : t === 'close' ? 'Click a place on the map to close it in this what-if, or pick one below.' : '';
    this.verifyList.replaceChildren();
    this.addList.replaceChildren();
    this.closeList.replaceChildren();
    if (t === 'add') {
      const spots = this.addSpots();
      const ul = h('ul', { class: 'sim-vlist-ul' });
      for (const sp of spots) {
        ul.append(h('li', {}, h('span', {}, sp.label),
          h('button', { type: 'button', class: 'sim-btn', 'aria-label': `Add a what-if ${p.service_phrase} unit at ${sp.label}`, onclick: () => this.hd.onAddAt(sp.lon, sp.lat, sp.label) }, 'Add here')));
      }
      this.addList.append(h('p', { class: 'sim-sub-h' }, 'Or add one at a named place'), ul);
      return;
    }
    if (t === 'close') {
      const ul = h('ul', { class: 'sim-vlist-ul' });
      for (const c of this.closeCands) {
        const where = c.approximate ? 'position approximate' : `${c.km_from_origin} km from ${p.origin.short_name}`;
        const li = h('li', {}, h('span', {}, `${displayName(c.name, p)}, ${where}`, h('span', { class: 'sim-sub' }, c.evidence ? ' (record shows it)' : ' (lists it, no evidence)')),
          h('button', { type: 'button', class: 'sim-btn', 'aria-label': `Close ${displayName(c.name, p)} in this what-if`, onclick: () => this.hd.onCloseId(c.facility_id) }, 'Close'));
        li.addEventListener('mouseenter', () => this.hd.onHoverFacility(c.facility_id));
        li.addEventListener('mouseleave', () => this.hd.onHoverFacility(null));
        ul.append(li);
      }
      this.closeList.append(h('p', { class: 'sim-sub-h' }, 'Places nearby'), ul);
      return;
    }
    if (t !== 'verify') return;
    const list = h('ul', { class: 'sim-vlist-ul' });
    for (const c of candidates) {
      const where = c.approximate ? 'position approximate' : `${c.km_from_origin} km from ${p.origin.short_name}`;
      list.append(h('li', {},
        h('span', {}, `${displayName(c.name, p)}, ${where}`, c.held_back ? h('span', { class: 'sim-sub' }, ' (its evidence is held back by a warning in its record)') : null),
        c.verified ? h('span', { class: 'sim-hot' }, 'checked') : h('button', { type: 'button', class: 'sim-btn', 'aria-label': `What if ${displayName(c.name, p)} were checked`, onclick: () => this.hd.onVerifyId(c.facility_id) }, 'Check')));
    }
    this.verifyList.append(h('p', { class: 'sim-sub-h' }, 'Records nearby that list it without evidence'), list);
  }

  /** Close-tool candidates, set by the simulator before setTool. */
  closeCands: CloseCandidate[] = [];

  /** Named places to add a what-if unit at, without a mouse: the searched place, then nearby towns. */
  private addSpots(): { label: string; lon: number; lat: number }[] {
    const p = this.p;
    const out = [{ label: `${p.origin.short_name} (the searched place)`, lon: p.origin.lon, lat: p.origin.lat }];
    const seen = new Set([p.origin.short_name.toLowerCase()]);
    // only places inside the village area, nearest first
    const R = p.villages.radius_km;
    const km = (l: { lat: number; lon: number }) => haversineKm(p.origin.lat, p.origin.lon, l.lat, l.lon);
    const labs = (p.context?.labels ?? []).filter((l) => l.kind !== 'district' && km(l) > 2 && km(l) <= R)
      .slice().sort((a, b) => km(a) - km(b));
    for (const l of labs) {
      const k = l.name.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ label: `${l.name}, ${Math.round(km(l))} km away`, lon: l.lon, lat: l.lat });
      if (out.length >= 7) break;
    }
    return out;
  }

  setVerifyFirst(r: VerifyFirst | null, onTry: (fid: string) => void, whatIfEdits = 0): void {
    this.vfOut.replaceChildren();
    if (!r) return;
    const p = this.p;
    const best0 = r.rows[0];
    const best = best0 ? { ...best0, name: displayName(best0.name, p) } : best0;
    let verdict: string;
    if (!best || (best.gain_within === 0 && best.gain_first === 0)) {
      verdict = `We tried verifying each of the ${r.n_candidates} nearby records that list ${p.service_phrase} without evidence. None would bring a village within ${p.window.hours_text}. The gap here is distance, not checking.`;
    } else if (best.before.within === p.villages.n) {
      verdict = `All ${p.villages.n} villages already have a place with evidence within ${p.window.hours_text}. Checking ${best.name} first would cut first stops with no evidence from ${best.before.first_no_evidence} to ${best.after.first_no_evidence}.`;
    } else if (best.gain_within === 0) {
      verdict = `We tried verifying each of the ${r.n_candidates} nearby records that list ${p.service_phrase} without evidence. None would bring a village within ${p.window.hours_text}: the gap here is distance, not checking. Checking ${best.name} first would still cut first stops with no evidence from ${best.before.first_no_evidence} to ${best.after.first_no_evidence}.`;
    } else {
      verdict = `Checking ${best.name} first would bring ${best.gain_within} more village${best.gain_within === 1 ? '' : 's'} within ${p.window.hours_text} of a place with evidence.`;
    }
    if (whatIfEdits) this.vfOut.append(h('p', { class: 'sim-hot' }, `On your what-if map (${whatIfEdits} edit${whatIfEdits === 1 ? '' : 's'}), not the real data:`));
    this.vfOut.append(h('p', { class: 'sim-verdict' }, verdict));
    if (best && (best.gain_within > 0 || best.gain_first > 0)) {
      const ol = h('ol', { class: 'sim-vf-rows' });
      r.rows.forEach((row: VerifyRow) => {
        const nm = displayName(row.name, p);
        const where = row.approximate ? `${nm}, position approximate` : `${nm}, ${row.km_from_origin} km from ${p.origin.short_name}`;
        const li = h('li', {},
          h('span', {}, `${where}: within ${p.window.hours_text} ${row.before.within} → ${row.after.within}; first stops with no evidence ${row.before.first_no_evidence} → ${row.after.first_no_evidence}; average drive ${fmtMin(row.before.avg_minutes).replace(' min', '')} → ${fmtMin(row.after.avg_minutes)}`),
          h('button', { type: 'button', class: 'sim-btn sim-try', 'aria-label': `Try checking ${nm}`, onclick: () => onTry(row.facility_id),
            onfocus: () => this.hd.onHoverFacility(row.facility_id), onblur: () => this.hd.onHoverFacility(null) }, 'Try it'));
        // pointing at a row rings the place on the map, so the reason it helps can be seen
        li.addEventListener('mouseenter', () => this.hd.onHoverFacility(row.facility_id));
        li.addEventListener('mouseleave', () => this.hd.onHoverFacility(null));
        ol.append(li);
      });
      this.vfOut.append(ol);
    }
    this.vfOut.append(h('p', { class: 'sim-foot' }, `A priority for checking records, not a claim that any of these places offers ${p.service_phrase}. Point at a row to see the place on the map.`));
  }

  // ------------------------------------------------------------------ popover

  showPopover(x: number, y: number, kids: Child[]): void {
    const el = this.popover;
    el.replaceChildren(...kids.filter((k): k is Node | string => !!k));
    el.hidden = false;
    const W = this.slots.mapEl.clientWidth, H = this.slots.mapEl.clientHeight;
    el.style.left = `${Math.max(8, Math.min(W - 280, x + 12))}px`;
    el.style.top = `${Math.max(8, Math.min(H - 160, y + 12))}px`;
  }

  hidePopover(): void {
    this.popover.hidden = true;
  }

  get popoverOpen(): boolean {
    return !this.popover.hidden;
  }
}
