"""Static mockup for direction "answer first" (a referral note).

Inputs, all real:
  data/a_*.json, data/t_*.json   /api/answer and /api/trip from the live server on port 8810
                                 (Lucknow PIN 226001 emoc, Jokihat dialysis, Jaipur dialysis)
  data/extras2.json              the same answers plus why_not, funnel, questions, call_info, public_list
                                 (src/vf modules, run on the API answers; not wired into /api/answer yet)
  sim.py                         prototype of the browser swarm rules on the bundle (what-if numbers)
  ../india_states.geojson        Natural Earth 1:10m admin-1 (India only, simplified) + the vf.locate outline
  dbapp/bundle/post_offices.parquet, places.parquet

Run: .venv/bin/python app/design/answer_first/build/build_mockup.py
Writes app/design/answer_first/index.html, style.css is hand-written next to it.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
sys.path.insert(0, str(HERE))
import maps as M  # noqa: E402
import sim  # noqa: E402

esc = M.esc
X = json.loads((HERE / "data" / "extras2.json").read_text())
T = {k: json.loads((HERE / "data" / f"t_{k}.json").read_text()) for k in ("lko", "jok", "jpr")}
(OUT / "img").mkdir(exist_ok=True)

# ------------------------------------------------------------------ plain words
FIELD = {"capability": "from its list of services", "procedure": "from its list of procedures",
         "equipment": "from its equipment list", "description": "from its description",
         "specialties": "from its specialty list"}
CONF = {"high": ("Strong record evidence", 3, "c-high"), "medium": ("Some record evidence", 2, "c-med"),
        "low": ("Thin record evidence", 1, "c-low")}
REQ_PLAIN = {"caesarean section": "C-section", "blood bank or transfusion": "a blood bank",
             "anaesthesia or operating theatre": "an operating theatre", "dialysis": "dialysis",
             "dialysis unit, machines or nephrologist": "a dialysis unit or machines"}
HILITE = {"emoc": r"caesarean|cesarean|lscs|c-section|blood bank|operation theatre|operating theatre",
          "dialysis": r"dialysis|nephrolog"}


def short_name(n):
    return n.split(",")[0].strip()


def mins_word(c):
    """The API's own rounded figure, e.g. 'about 7 min' or 'about 1 h 30 min'."""
    m = re.match(r"(about [^e]*?min)", c.get("minutes_label") or "")
    return m.group(1).strip() if m else None


def band_word(c):
    lo, hi = c.get("minutes_low"), c.get("minutes_high")
    if lo is None or hi is None:
        return ""
    return f"range {max(1, round(lo))} to {round(hi)} min"


def km_word(km):
    return f"{km:.1f} km" if km < 10 else f"{km:.0f} km"


def highlight(text, line, limit=150):
    """Trim long quotes around the first matching phrase and mark the words that name the service."""
    pat = re.compile(HILITE[line], re.I)
    m = pat.search(text)
    if len(text) > limit and m:
        a = max(0, m.start() - 55)
        b = min(len(text), m.end() + 70)
        a = text.rfind(" ", 0, a) + 1 if a > 0 else 0
        bb = text.find(" ", b)
        b = bb if bb != -1 else len(text)
        text = ("..." if a > 0 else "") + text[a:b] + ("..." if b < len(text) else "")
    elif len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "..."
    out, last = [], 0
    for mm in pat.finditer(text):
        out.append(esc(text[last:mm.start()]))
        out.append(f"<mark>{esc(mm.group(0))}</mark>")
        last = mm.end()
    out.append(esc(text[last:]))
    return "".join(out)


def quotes(c, line, n=None, cls="q"):
    seen = {}
    for e in c["evidence"]:
        d = seen.setdefault(e["quote"], {"field": e["field"], "reqs": []})
        for r in e.get("requirements") or [e["requirement"]]:
            d["reqs"].append(REQ_PLAIN.get(r, r))
    items = list(seen.items())[: n or None]
    return "".join(f'<blockquote class="{cls}"><span class="qt">&ldquo;{highlight(q, line)}&rdquo;</span>'
                   f'<span class="qf">{FIELD.get(v["field"], v["field"])}</span></blockquote>' for q, v in items)


def conf_reason(c, label):
    """The one reason that applies, in plain words (fixes the 'one fact, or one web source' contradiction)."""
    if c["confidence"] == "high":
        reqs = list(dict.fromkeys(REQ_PLAIN.get(r, r) for e in c["evidence"]
                                  for r in (e.get("requirements") or [e["requirement"]])))
        what = reqs[0] if len(reqs) == 1 else ", ".join(reqs[:-1]) + " and " + reqs[-1]
        return f"Its record names {what}, and its map position matches its PIN."
    if c.get("position_approximate") and "location" in c["confidence_reason"]:
        m = re.search(r"within about (\d+) km", c["confidence_reason"])
        return f"Its record names {label}, but its position is known only to within about {m.group(1)} km." if m else c["confidence_reason"]
    if c["confidence"] == "medium" and c.get("flags"):
        f = c["flags"][0]
        if f["flag"] == "possible_merge":
            place = re.search(r"places it in ([^,]+)", f["reason"])
            return (f"Its record names {label}, but its own text also places it in {place.group(1)}, "
                    "so it may mix two facilities.") if place else f["reason"]
    for q in c.get("questions") or []:
        if "single fact in the record names the service" in q.get("why", ""):
            return f"Only one sentence in its record names {label}."
    return c["confidence_reason"]


def conf(c, label, big=False):
    word, bars, cls = CONF[c["confidence"]]
    b = "".join(f'<i class="{"on" if i < bars else ""}"></i>' for i in range(3))
    return (f'<p class="conf {cls}{" big" if big else ""}"><span class="bars" aria-hidden="true">{b}</span>'
            f'<b>{word}.</b> {esc(conf_reason(c, label))}</p>')


PHONE_SVG = ('<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6.6 10.8a15.1 15.1 0 0 0 '
             '6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.5c.6 '
             '0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1z"/></svg>')
PLAY_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 4v16l13-8z"/></svg>'
PAUSE_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>'
EXPAND_SVG = ('<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" '
              'd="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/></svg>')
CLOSE_SVG = ('<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" '
             'd="M10 4v6H4M14 20v-6h6M4 10l7-7M20 14l-7 7"/></svg>')


def next_step(c, n_q=2, compact=False):
    ci = c["call_info"]
    qs = (c.get("questions") or [])[:n_q]
    if ci.get("has_phone"):
        call = (f'<button class="btn-call" type="button">{PHONE_SVG}Call {esc(ci["phone"])}</button>'
                '<span class="fine">Number from its record. Digits hidden for the demo.</span>')
    else:
        call = '<span class="fine">No phone number in its record. Ask a local contact or visit.</span>'
    asks = "".join(f'<li>{esc(q["question"])}</li>' for q in qs)
    return (f'<div class="next{" compact" if compact else ""}"><div class="call">{call}</div>'
            f'<p class="ask-h">Before you go, ask</p><ol class="asks">{asks}</ol></div>')


def caution(c):
    """Only what is specific to this facility; page-wide caveats live in the footer."""
    out = []
    for f in c.get("flags") or []:
        if f["flag"] == "possible_merge":
            place = re.search(r"places it in ([^,]+, [^,]+?),", f["reason"])
            out.append(f"Its own text also places it in {place.group(1)}. Ask if the service is at this address."
                       if place else f["reason"])
        elif f["flag"] == "location_conflict" and c.get("position_approximate"):
            out.append("Its map position is approximate: we placed it at the centre of its PIN area.")
    for m in c.get("missing") or []:
        what = REQ_PLAIN.get(m.replace("no fact in the record mentions ", ""), m)
        out.append(f"Not found in its record: {what}.")
    return "".join(f"<li>{esc(t)}</li>" for t in dict.fromkeys(out))


def sources_line(c):
    s = c["sources"]
    ow = s.get("official_website") or ""
    if ow and ("goo.gl" in ow or "maps.app" in ow):
        return f'Found on {s["n_domains"]} websites. Its record gives a maps link, not an official website.'
    if ow:
        return f'Found on {s["n_domains"]} websites, including its own ({esc(ow)}).'
    return f'Found on {s["n_domains"]} websites. No official website in its record.'


def dist_line(c, loose=False):
    ap = ", position approximate" if c.get("position_approximate") else ""
    if loose or not mins_word(c):
        return f'<b>{km_word(c["distance_km"])}</b><span>straight line{ap}</span>'
    return f'<b>{mins_word(c)}</b><span>{km_word(c["distance_km"])}{ap}</span>'


def backup(c, i, label, same_as=None, loose=False, line="emoc"):
    cau = caution(c)
    same = (f'<p class="tie">About the same distance as {esc(short_name(same_as))}; listed after it because its record '
            'shows less.</p>' if same_as else "")
    return f"""<li class="bk">
  <span class="num{' approx' if c.get('position_approximate') else ''}">{i}</span>
  <div class="bk-main">
    <div class="bk-top"><h3>{esc(short_name(c["name"]))}</h3><div class="d">{dist_line(c, loose)}</div></div>
    <p class="meta">{esc(c["type"].capitalize())}, {esc(c["operator"] or "ownership not stated")}, {esc(c["city"])}</p>
    {quotes(c, line, n=1, cls="q sm")}
    {conf(c, label)}
    {f'<ul class="careful">{cau}</ul>' if cau else ''}{same}
    <details class="more"><summary>Call, questions and sources</summary>{next_step(c, n_q=3, compact=True)}<p class="fine">{sources_line(c)}</p></details>
  </div>
</li>"""


def why_not(w, sim_href="#sim-lko", verify=True):
    if not w["items"]:
        return f'<p class="whynot none">{esc(w["heading"])}</p>'
    rows = []
    for it in w["items"]:
        r = it["reason_plain"].replace("Recommendable, but it scored below the cards shown: ",
                                       "Has some evidence, listed lower: ")
        d = it["distance_label"]
        v = (f'<a class="wi-link" href="{sim_href}">What if it were verified?</a>' if verify else "")
        rows.append(f'<li><span class="wn-d">{esc(d)}</span><div><b>{esc(short_name(it["name"]))}</b>'
                    f'<em>{esc(it["operator"] or "")}</em><p>{esc(r)}</p>{v}</div></li>')
    n = len(w["items"])
    return (f'<details class="whynot"><summary><b>Why not the nearer ones?</b> <span>{n} closer records were not '
            f'recommended. See why.</span></summary><ul>{"".join(rows)}</ul></details>')


def funnel(f):
    lab = {"all_records": "facility records in this dataset", "usable_location": "with a location we can use",
           "claim_within_reach": "list or claim this care, within reach", "recommendable":
           "have a sentence naming it, no serious warning", "within_radius": "inside the travel-time window",
           "shown": "shown here, nearest first"}
    cells = "".join(f'<li><b>{s["n"]:,}</b><span>{esc(lab.get(s["step"], s["label"]))}</span></li>' for s in f["steps"])
    return f'<ol class="funnel">{cells}</ol>'


def how(a, extra=""):
    w = a["window"]
    return f"""<details class="how"><summary>How we decided</summary>
  {funnel(a["funnel"])}
  <p>We keep a facility only if a sentence in its own record names the service and nothing serious is wrong with the
  record. Then we list them nearest first by estimated drive time. A record whose evidence names everything the care
  needs may go first if it is at most {w["allowance"]:.0f} minutes further.</p>
  <p>The window for this care is {w["minutes"]/60:.0f} hour{'s' if w['minutes'] > 60 else ''} of estimated drive. {esc(w["note"])}</p>
  {extra}
  <p class="fine">Every quote is the exact text of a fact in the facility's record, with the list it came from.
  We cannot tell which web page each fact came from.</p>
</details>"""


FOOT = ("Drive times are estimates for free-flowing roads from the straight-line distance (minutes = state factor x 2.30 x "
        "km<sup>0.842</sup>); real trips take longer. Records come from web pages and can be out of date. Most records here "
        "are private facilities, so a public hospital nearby may be missing or thinly described.")


def header(service, place):
    return f"""<header class="top">
  <a class="brand" href="#landing"><span class="mark" aria-hidden="true"></span>Find care you can trust</a>
  <div class="qline" role="search"><span class="qchip"><em>Care</em>{esc(service)}</span>
    <span class="qchip"><em>Near</em>{esc(place)}</span><button class="btn-change" type="button">Change</button></div>
</header>"""


# ------------------------------------------------------------------ maps
def inset(center, box=None):
    P = M.Proj(22.8, 82.6, 1700, 150, 170)
    return M.india_map(P, marker=center, box=box, cls="inset-svg", state_lbl=False)


def map_chrome(svg, ins, href, legend="", hint=True, cls=""):
    return f"""<div class="mapbox {cls}">
  {svg}
  <div class="map-tools">
    <a class="btn-map" href="{href}">{EXPAND_SVG}Expand map</a>
    <div class="zoom"><button type="button" aria-label="Zoom in">+</button><button type="button" aria-label="Zoom out">&minus;</button></div>
  </div>
  <div class="inset" title="Where this is in India">{ins}<span>India</span></div>
  {'<p class="hint">Scroll or pinch to zoom. Drag to move.</p>' if hint else ''}
  {legend}
</div>"""


def legend(rows, cls="legend"):
    return f'<ul class="{cls}">' + "".join(f'<li><span class="sw {k}">{n}</span>{esc(t)}</li>' for k, n, t in rows) + "</ul>"


LEG_ANSWER = [("sw-search", "", "Where you searched"), ("sw-rec", "1", "Our answer and backups"),
              ("sw-rec approx", "2", "Dashed: position approximate"),
              ("sw-not", "", "Closer, but no sentence naming the care")]


def cards_xy(a):
    return [(c["lat"], c["lon"], i + 1, short_name(c["name"]) if i == 0 else "") for i, c in enumerate(a["results"])]


def approx_rings(P, cards_src, newpos=None):
    out = []
    for c in cards_src:
        if c.get("position_approximate"):
            x, y = P.xy(c["lat"], c["lon"])
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="17" fill="none" stroke="var(--ev)" stroke-width="1.6" stroke-dasharray="3 3"/>')
    return "".join(out)


def lko_small(W=600, H=430, half=8.6, labels=True):
    a = X["lko"]
    loc = a["location"]
    cen = (loc["lat"], loc["lon"])
    P = M.Proj(26.861, 80.972, half, W, H)
    notrec = [(it["lat"], it["lon"], it["position_approximate"]) for it in a["why_not"]["items"]
              if not it["position_approximate"]]
    cards = cards_xy(a) if labels else [(c[0], c[1], c[2], "") for c in cards_xy(a)]
    return M.local_map(P, cen, "PIN 226001", cards, notrec, neighbourhoods=6 if labels else 3,
                       extra_layers=approx_rings(P, a["results"]))


def jok_small(W=600, H=430, labels=True):
    a = X["jok"]
    loc = a["location"]
    c1 = a["beyond_radius"]
    c2 = a["further_away"][1]
    cen = (loc["lat"], loc["lon"])
    P = M.Proj((cen[0] + c1["lat"]) / 2 + 0.02, (cen[1] + c1["lon"]) / 2, 72 if labels else 80, W, H)
    km_ring = a["window"]["km_equivalent"]
    return M.local_map(P, cen, "Jokihat", [(c1["lat"], c1["lon"], 1, "Raiganj GMCH" if labels else ""),
                                           (c2["lat"], c2["lon"], 2, "")], (),
                       ring_km=km_ring, ring_label="1 hour estimated drive" if labels else None, neighbourhoods=0,
                       line_to=((c1["lat"], c1["lon"]), mins_word(c1).replace("about ", "")),
                       state_names={"Bihar", "West Bengal"}, edge=False)


def jpr_small(W=600, H=430, labels=True):
    a = X["jpr"]
    loc = a["location"]
    cen = (loc["lat"], loc["lon"])
    P = M.Proj(26.905, 75.812, 8.5 if labels else 7, W, H)
    cards = cards_xy(a) if labels else [(c[0], c[1], c[2], "") for c in cards_xy(a)]
    notrec = [(it["lat"], it["lon"], it["position_approximate"]) for it in a["why_not"]["items"]
              if not it["position_approximate"]]
    return M.local_map(P, cen, "Jaipur head post office", cards, notrec, precision_km=loc["precision_km"],
                       neighbourhoods=5 if labels else 2)


# ------------------------------------------------------------------ swarm (prototype of the browser rule)
def h01(*k):
    return int(hashlib.md5("|".join(map(str, k)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def swarm_state(S, mode, Tmin, ev=None, extra=None, per_village=3, spawn=24.0, hold=5.0, seed="v1"):
    """Agents decide at run time from the current world: evidence rule (window + allowance) or nearest claim.
    Under 'claims', an agent that reaches a record with no evidence goes on to the nearest facility with evidence
    from where it stands (the counterfactual 'if that place cannot help')."""
    F = S["F"]
    flat, flon = F.lat.values.copy(), F.lon.values.copy()
    evv = F.evidence.values.copy() if ev is None else ev.copy()
    grade = np.where(evv & (F.grade.values < 2), 2, F.grade.values)
    km = S["KM"]
    if extra:
        km = np.hstack([km] + [sim.hav(S["plat"], S["plon"], la, lo)[:, None] for la, lo in extra])
        flat = np.concatenate([flat, [e[0] for e in extra]])
        flon = np.concatenate([flon, [e[1] for e in extra]])
        evv = np.concatenate([evv, np.ones(len(extra), bool)])
        grade = np.concatenate([grade, np.full(len(extra), 2)])
    fac = S["fac"]
    mins = fac[:, None] * 2.30 * np.power(km, 0.842)
    m_ev = np.where(evv, mins, np.inf)
    order = m_ev - np.where(grade == 3, S["allow"], 0)[None, :]
    within = m_ev.min(axis=1) <= S["W"]
    choice = np.where(within, np.where(m_ev <= S["W"], order, np.inf).argmin(axis=1), m_ev.argmin(axis=1))
    first = mins.argmin(axis=1)
    evi = np.flatnonzero(evv)
    agents = []
    for v in range(len(fac)):
        x0, y0 = S["plat"][v], S["plon"][v]
        for k in range(per_village):
            s = h01(seed, v, k) * spawn
            t = Tmin - s
            if t < 0:
                continue
            if mode == "evidence":
                j = choice[v]
                leg = mins[v, j]
                p = min(1.0, t / leg) if leg > 0 else 1.0
                agents.append(dict(v=v, legs=[((x0, y0), (flat[j], flon[j]), p)], at=j if p >= 1 else None,
                                   phase="arrived" if p >= 1 else "moving", ok=True))
            else:
                j = first[v]
                leg1 = mins[v, j]
                p1 = min(1.0, t / leg1) if leg1 > 0 else 1.0
                if evv[j] or p1 < 1:
                    agents.append(dict(v=v, legs=[((x0, y0), (flat[j], flon[j]), p1)], at=j if p1 >= 1 else None,
                                       phase="arrived" if p1 >= 1 else "moving", ok=bool(evv[j])))
                    continue
                t2 = t - leg1
                if t2 < hold:
                    agents.append(dict(v=v, legs=[((x0, y0), (flat[j], flon[j]), 1.0)], at=None, phase="hold", ok=False,
                                       hold_at=j))
                    continue
                k2 = sim.hav(flat[j], flon[j], flat[evi], flon[evi])
                m2 = fac[v] * 2.30 * np.power(k2, 0.842)
                q = evi[int(np.argmin(m2))]
                leg2 = float(m2.min())
                p2 = min(1.0, (t2 - hold) / leg2) if leg2 > 0 else 1.0
                agents.append(dict(v=v, legs=[((x0, y0), (flat[j], flon[j]), 1.0), ((flat[j], flon[j]), (flat[q], flon[q]), p2)],
                                   at=q if p2 >= 1 else None, phase="arrived2" if p2 >= 1 else "moving2", ok=False,
                                   first=j))
    return agents, dict(flat=flat, flon=flon, ev=evv)


def swarm_svg(P, agents, world, mode):
    trails, trails2, dots, holds = [], [], [], []
    for a in agents:
        for n, ((la0, lo0), (la1, lo1), p) in enumerate(a["legs"]):
            x0, y0 = P.xy(la0, lo0)
            x1, y1 = P.xy(la1, lo1)
            xe, ye = x0 + (x1 - x0) * p, y0 + (y1 - y0) * p
            seg = f"M{x0:.1f},{y0:.1f}L{xe:.1f},{ye:.1f}"
            (trails if n == 0 else trails2).append(seg)
            if n == len(a["legs"]) - 1 and p < 1:
                dots.append((xe, ye))
        if a["phase"] == "hold":
            x, y = P.xy(*a["legs"][0][1])
            holds.append((x, y))
    c1 = "var(--ev)" if mode == "evidence" else "var(--claim)"
    out = [f'<path d="{"".join(trails)}" stroke="{c1}" stroke-width="0.8" opacity=".30" fill="none"/>']
    if trails2:
        out.append(f'<path d="{"".join(trails2)}" stroke="var(--claim)" stroke-width="1.1" opacity=".55" '
                   'stroke-dasharray="4 3" fill="none"/>')
    out.append('<g class="agents">' + "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.3"/>' for x, y in dots) + "</g>")
    for x, y in dict.fromkeys((round(x), round(y)) for x, y in holds):
        out.append(f'<circle cx="{x}" cy="{y}" r="9" fill="none" stroke="var(--claim)" stroke-width="2.4"/>')
    # claim-only stops that agents reached
    if mode == "claims":
        stops = Counter(a.get("first", a.get("hold_at")) for a in agents if not a["ok"] and a["phase"] != "moving")
        for j, n in stops.most_common():
            if j is None:
                continue
            x, y = P.xy(world["flat"][j], world["flon"][j])
            out.append(M.diamond(x, y, s=5.5 + 3 * math.sqrt(n / 30), stroke="var(--claim)"))
        for j, n in stops.most_common(3):
            if j is None:
                continue
            x, y = P.xy(world["flat"][j], world["flon"][j])
            out.append(f'<g><rect x="{x+10:.0f}" y="{y-25:.0f}" rx="9" width="{30 + 7*len(str(n)):.0f}" height="18" fill="var(--claim)"/>'
                       f'<text x="{x+18:.0f}" y="{y-12:.0f}" font-size="11.5" font-weight="700" fill="#fff">{n} &#8635;</text></g>')
    arr = Counter(a["at"] for a in agents if a["at"] is not None) if mode == "evidence" else Counter()
    for j, n in arr.most_common(3):
        x, y = P.xy(world["flat"][j], world["flon"][j])
        out.append(f'<g><rect x="{x+15:.0f}" y="{y+6:.0f}" rx="9" width="{56 + 7*len(str(n)):.0f}" height="18" fill="var(--ev)"/>'
                   f'<text x="{x+23:.0f}" y="{y+19:.0f}" font-size="11.5" font-weight="700" fill="#fff">{n} arrived</text></g>')
    return "\n".join(out)


def counts(agents):
    c = Counter(a["phase"] for a in agents)
    return dict(moving=c["moving"] + c["moving2"], arrived=c["arrived"] + c["arrived2"], hold=c["hold"],
                rerouted=c["moving2"] + c["arrived2"] + c["hold"], total=len(agents))


# ------------------------------------------------------------------ simulator screen
SIMS = {}
for key, line in (("lko", "emoc"), ("jok", "dialysis"), ("jpr", "dialysis")):
    S = sim.setup(key, line)
    SIMS[key] = S


def verify_rank(S):
    F = S["F"]
    base = sim.run(S)
    rows = []
    a = S["a"]
    near = sim.hav(a["location"]["lat"], a["location"]["lon"], F.lat.values, F.lon.values) <= a["window"]["km_equivalent"]
    for j in np.flatnonzero(~F.evidence.values & ~F.blocked.values & near):
        ev = F.evidence.values.copy()
        ev[j] = True
        r = sim.run(S, evidence=ev)
        rows.append((r["n_within"] - base["n_within"], -(r["mean_min"] - base["mean_min"]), j, r))
    rows.sort(key=lambda z: (-z[0], -z[1]))
    return base, rows


def rail(key, mode, Tmin, agents, edits, svc_word, place, verify_box="", before=None, after=None, step_txt=""):
    a = X[key]
    t = T[key]["summary"]
    S = SIMS[key]
    W = S["W"]
    wtxt = f"{W/60:.0f} hour{'s' if W > 60 else ''}"
    ev = mode == "evidence"
    c = counts(agents)
    base = before or sim.run(S)
    seg = f"""<div class="seg" role="radiogroup" aria-label="Rule the villages follow">
    <a role="radio" aria-checked="{str(ev).lower()}" class="{'on' if ev else ''}" href="#sim-{key}-ev">Trust the record's evidence</a>
    <a role="radio" aria-checked="{str(not ev).lower()}" class="{'' if ev else 'on'}" href="#sim-{key}">Trust specialty claims alone</a></div>"""
    rule = (f"Each village goes to the nearest facility whose own record shows evidence of {svc_word}, within "
            f"{wtxt} if it can. A record that names everything the care needs may be chosen if it is at most "
            f"{S['allow']:.0f} minutes further." if ev else
            f"Each village goes to the nearest facility that lists the specialty, whatever its record shows. "
            f"If that place has no evidence of {svc_word} in its record, the village goes on to the nearest one that does: "
            f"the extra trip if that place cannot help.")
    live = (f'<p class="live"><b>{c["arrived"]:,}</b> arrived &middot; <b>{c["moving"]:,}</b> on the way'
            + (f' &middot; <b class="am">{c["hold"] + c["rerouted"]:,}</b> stopped first at a place with no evidence' if not ev else "")
            + f'<span>{c["total"]:,} patient dots from {base["n"]} villages (3 each)</span></p>')
    if ev and after:
        take = (f'With your what-if edit, {after["n_within"]} of {base["n"]} villages would be within {wtxt} of a facility '
                f'with evidence of {svc_word}, up from {base["n_within"]} in the real data. Average estimated drive: '
                f'{base["mean_min"]:.0f} &rarr; {after["mean_min"]:.0f} min.')
    elif ev:
        take = (f'{base["n_within"]} of {base["n"]} villages are within {wtxt} of a facility whose record shows evidence of '
                f'{svc_word}. The average estimated drive to it is {base["mean_min"]:.0f} min.')
    else:
        take = (f'{base["n_first_noev"]} of {base["n"]} villages would go first to a place with no evidence of {svc_word} '
                f'in its record. If that place cannot help, they travel on to one that has it.')
    def row(lbl, b, a_=None, unit="", of=None):
        if a_ is None:
            return f'<tr><th>{lbl}</th><td class="now">{b}{unit}{f" of {of}" if of else ""}</td><td></td></tr>'
        cls = "up" if a_ != b else ""
        return (f'<tr><th>{lbl}</th><td class="was">{b}{unit}</td><td class="now {cls}">&rarr; {a_}{unit}'
                f'{f" of {of}" if of else ""}</td></tr>')
    A = after
    tab = f"""<table class="counters"><caption>{'Before and after your what-if edits' if A else 'This area now'}</caption>
    {row(f"Villages within {wtxt} of a facility with evidence", base["n_within"], A and A["n_within"], of=base["n"])}
    {row("Average estimated drive to it", f'{base["mean_min"]:.0f}', A and f'{A["mean_min"]:.0f}', " min")}
    {row("Villages whose first stop has no evidence, if they trust claims", base["n_first_noev"], A and A["n_first_noev"], of=base["n"])}
    </table>"""
    ed = ("".join(f'<li class="edit"><span class="tag-wi">what-if</span>{e}<button type="button" class="btn-ghost sm">Undo</button></li>'
                  for e in edits) if edits else '<li class="empty">No what-if edits yet. Real data only.</li>')
    return f"""<aside class="rail">
  <a class="btn-back" href="#{dict(lko='lucknow', jok='jokihat', jpr='jaipur')[key]}">{CLOSE_SVG}Back to the answer</a>
  <h2>Where would nearby villages go?</h2>
  <p class="cap">Each moving dot is a patient from a village near {esc(place)}, shown at its post office. Patients pick a
  facility by the rule below, move at the estimated drive speed, and choose again if the map changes while they travel.</p>
  {seg}
  <p class="rule">{rule}</p>
  <div class="player"><button type="button" class="btn-play">{PAUSE_SVG}Pause</button>
    <button type="button" class="btn-ghost sm">Replay</button>
    <div class="prog" aria-label="minute {Tmin:.0f} of the run"><i style="width:{min(100, Tmin/ (90 if key=='lko' else 150) * 100):.0f}%"></i></div>
    <span class="clock">minute {Tmin:.0f}</span></div>
  {live}
  <p class="take{" wi" if after else ""}">{take}</p>
  {tab}
  <section class="whatif"><h3>What if <span class="tag-wi">what-if</span></h3>
    <div class="tools"><button type="button" class="tool">Verify a record</button><button type="button" class="tool">Add a facility</button><button type="button" class="tool">Close a facility</button></div>
    <ul class="edits">{ed}</ul>
    {'<div class="edit-actions"><button type="button" class="btn-ghost sm">Undo last</button><button type="button" class="btn-ghost sm">Reset to real data</button></div>' if edits else ''}
    {verify_box}
  </section>
  <p class="fine">Straight-line paths; times from the drive-time estimate, free-flow. Nothing here is random: what
  happens at each stop comes from the record's evidence. {step_txt}</p>
</aside>"""


def sim_screen(key, mode, Tmin, place, svc_word, half, center=None, edits=(), extra=None, ev=None,
               verify_box="", before=None, after=None, legend_rows=None, whatif_pin=None):
    a = X[key]
    loc = a["location"]
    S = SIMS[key]
    cen = (loc["lat"], loc["lon"])
    W_, H_ = 900, 800
    P = M.Proj(*(center or cen), half, W_, H_)
    agents, world = swarm_state(S, mode, Tmin, ev=ev, extra=extra)
    layer = swarm_svg(P, agents, world, mode)
    if whatif_pin:
        x, y = P.xy(*whatif_pin[0])
        layer += (f'<g class="wi-pin"><circle cx="{x:.1f}" cy="{y:.1f}" r="15" fill="var(--wi)" stroke="#fff" stroke-width="2.5"/>'
                  f'<text x="{x:.1f}" y="{y+5:.1f}" text-anchor="middle" font-size="15" font-weight="700" fill="#fff">+</text>'
                  f'<rect x="{x+20:.0f}" y="{y-13:.0f}" rx="4" width="{len(whatif_pin[1])*7+16}" height="24" fill="var(--wi)"/>'
                  f'<text x="{x+28:.0f}" y="{y+4:.0f}" font-size="12.5" font-weight="700" fill="#fff">{esc(whatif_pin[1])}</text></g>')
    cards = [(c["lat"], c["lon"], i + 1, "") for i, c in enumerate(a["results"])] or \
            [(a["beyond_radius"]["lat"], a["beyond_radius"]["lon"], 1, "")]
    ring_km = S["W"] and a["window"]["km_equivalent"]
    svg = M.local_map(P, cen, place, cards, (), ring_km=min(50, ring_km) if key != "jok" else 50,
                      ring_label="50 km: the villages in this run", extra_layers=layer, neighbourhoods=0)
    la0, la1, lo0, lo1 = P.bounds()
    ins = f'<div class="inset big">{inset(cen, ((la0, lo0), (la1, lo1)))}<span>India</span></div>'
    leg = legend(legend_rows, cls="legend on-map")
    return f"""<div class="overlay">
<div class="ov-map">
  {svg}
  <div class="map-tools"><a class="btn-map" href="#{dict(lko='lucknow', jok='jokihat', jpr='jaipur')[key]}">{CLOSE_SVG}Close map</a>
    <div class="zoom"><button type="button" aria-label="Zoom in">+</button><button type="button" aria-label="Zoom out">&minus;</button></div>
    <a class="btn-map light" href="#india">Show all of India</a></div>
  {ins}
  {leg}
  <p class="map-cap">Straight-line paths; times from the drive-time estimate, free-flow.</p>
</div>
{rail(key, mode, Tmin, agents, list(edits), svc_word, place, verify_box, before, after)}
</div>"""


LEG_CLAIMS = [("sw-dot", "", "A patient from a nearby village"), ("sw-line-c", "", "First trip: nearest place that lists the specialty"),
              ("sw-not am", "", "Lists it, no evidence in its record"), ("sw-dash-c", "", "Extra trip if that place cannot help"),
              ("sw-rec", "1", "Our answer and backups")]
LEG_EV = [("sw-dot", "", "A patient from a nearby village"), ("sw-line-e", "", "Trip to the nearest place with evidence"),
          ("sw-rec", "1", "Our answer and backups"), ("sw-wi", "+", "What-if facility (not real data)")]


def verify_box_html(key, svc_word):
    S = SIMS[key]
    base, rows = verify_rank(S)
    W = S["W"]
    wtxt = f"{W/60:.0f} hour{'s' if W > 60 else ''}"
    n_c = len(rows)
    if not rows:
        return (f'<div class="verify"><button type="button" class="btn-verify">Which record should be verified first?</button>'
                f'<p class="vf-res">No record within the {W/60:.0f}-hour window lists {svc_word}, so there is nothing nearby to verify. '
                f'The gap here is distance, not a missing record: try <b>Add a facility</b> to test where a new unit would help most.</p>'
                f'<p class="fine">A verification priority for Virtue Foundation, never a claim that a facility offers {svc_word}.</p></div>')
    d_in, neg_dmin, j, r = rows[0]
    F = S["F"]
    nm = short_name(F.name.iloc[j])
    op = F.op.iloc[j] if isinstance(F.op.iloc[j], str) else "ownership not stated"
    if d_in > 0:
        res = (f'<p class="vf-res">Verify <b>{esc(nm)}</b> ({esc(op)}) first. If its record were confirmed, '
               f'<b>{r["n_within"]} of {base["n"]}</b> villages would be within {wtxt} of a facility with evidence, '
               f'up from {base["n_within"]}. First stops with no evidence: {base["n_first_noev"]} &rarr; {r["n_first_noev"]}.</p>')
    elif -neg_dmin < -0.5:
        res = (f'<p class="vf-res">All {base["n"]} villages are already within {wtxt} of a facility with evidence. '
               f'Confirming <b>{esc(nm)}</b> ({esc(op)}) would cut the average estimated drive the most: '
               f'<b>{base["mean_min"]:.0f} &rarr; {r["mean_min"]:.0f} min</b>. First stops with no evidence: '
               f'{base["n_first_noev"]} &rarr; {r["n_first_noev"]}.</p>')
    else:
        res = (f'<p class="vf-res">Confirming any one of the {n_c} records that list it would not bring a single village '
               f'within {wtxt} of evidence-backed care. The gap here is distance, not missing records.</p>')
    others = "".join(f'<li>{esc(short_name(F.name.iloc[jj]))}: {rr["n_within"]} within {wtxt}, {rr["mean_min"]:.0f} min average</li>'
                     for _, _, jj, rr in rows[1:3])
    return f"""<div class="verify">
  <button type="button" class="btn-verify">Which record should be verified first?</button>
  <p class="vf-how">Tested: confirming, one at a time, each of the {n_c} records within the {W/60:.0f}-hour window that list {svc_word} with no evidence in their record.</p>
  {res}
  {f'<details class="more"><summary>Next best</summary><ul class="vf-others">{others}</ul></details>' if others else ''}
  <p class="fine">A verification priority for Virtue Foundation, not a claim that the facility offers {svc_word}.</p>
</div>"""


# ------------------------------------------------------------------ result screens
def screen_lucknow():
    a = X["lko"]
    c1, c2, c3 = a["results"]
    label = "C-section"
    loc = a["location"]
    ins = inset((loc["lat"], loc["lon"]))
    t = T["lko"]["summary"]
    others = [e for e in c1["evidence"][1:]]
    also = "; ".join(f'&ldquo;{highlight(e["quote"], "emoc")}&rdquo; <span class="qf">{FIELD[e["field"]]}</span>' for e in others)
    return f"""
{header("Emergency C-section", "PIN 226001, Lucknow")}
<p class="where">From the middle of <b>PIN 226001, Lucknow, Uttar Pradesh</b> (good to about {loc["precision_km"]:.0f} km).</p>
<main class="grid">
<div class="col-text">
  <section class="answer">
    <h1>Go to <span class="fac">{esc(c1["name"])}</span>, {mins_word(c1)} away. Its record says it performs C-sections.</h1>
    <p class="d-line"><b>{mins_word(c1)}</b> estimated drive ({band_word(c1)}), {km_word(c1["distance_km"])} in a straight line.
      {esc(c1["type"].capitalize())}, {esc(c1["operator"])}, {esc(c1["city"])}.</p>
    {quotes(c1, "emoc", n=1, cls="q lead")}
    <p class="also">Also in its record: {also}.</p>
    {conf(c1, label, big=True)}
    {next_step(c1, n_q=2)}
    <p class="fine src">{sources_line(c1)}</p>
  </section>
  <div class="m-only">{map_chrome(lko_small(390, 230, 7.5, labels=False), ins, "#sim-lko-ev", hint=False, cls="phone")}</div>
  <section class="backups"><h2>If {esc(short_name(c1["name"]))} cannot help</h2>
    <ol>{backup(c2, 2, label)}{backup(c3, 3, label)}</ol></section>
  {why_not(a["why_not"], "#sim-lko")}
  {how(a)}
  <p class="foot">{FOOT}</p>
</div>
<aside class="col-map d-only">
  {map_chrome(lko_small(), ins, "#sim-lko-ev", legend(LEG_ANSWER))}
  <section class="teaser">
    <p class="t-k">What if patients trusted specialty claims alone?</p>
    <p class="t-big">{t["n_first_unsupported"]} of {t["n_points"]} nearby villages would go first to a place with no evidence of C-section in its record.</p>
    <div class="t-act"><a class="btn-play" href="#sim-lko">{PLAY_SVG}Play it on the map</a><a class="btn-ghost" href="#sim-lko">Try a what-if</a></div>
  </section>
</aside>
</main>"""


def screen_jokihat():
    a = X["jok"]
    c = a["beyond_radius"]
    c2 = a["further_away"][1]
    loc = a["location"]
    ins = inset((loc["lat"], loc["lon"]))
    S = SIMS["jok"]
    add = sim.run(S, extra=[(loc["lat"], loc["lon"])])
    base = sim.run(S)
    fa = a["further_away"][2]
    return f"""
{header("Dialysis", "Jokihat")}
<p class="where">From <b>Jokihat post office area (PIN 854329), Araria district, Bihar</b> (good to about {loc["precision_km"]:.0f} km).</p>
<main class="grid">
<div class="col-text">
  <section class="answer abstain">
    <h1>No clear evidence of dialysis within 1 hour of Jokihat in this dataset.</h1>
    <p class="d-line">This is not proof that no care exists nearby. Most records here are private facilities, and public hospitals are often thinly described online.</p>
    <div class="nearest">
      <p class="n-k">Nearest place whose record names dialysis</p>
      <h2><span class="num">1</span>{esc(c["name"])}</h2>
      <p class="d-line"><b>{mins_word(c)}</b> estimated drive ({band_word(c)}), {km_word(c["distance_km"])} in a straight line.
        Public medical college in Raiganj, <b>West Bengal</b>: across the state border.</p>
      {quotes(c, "dialysis", cls="q lead")}
      {conf(c, "dialysis", big=True)}
      {next_step(c, n_q=3)}
    </div>
  </section>
  <div class="m-only">{map_chrome(jok_small(390, 230, labels=False), ins, "#sim-jok", hint=False, cls="phone")}</div>
  <section class="backups"><h2>About as far</h2>
    <ol>{backup(c2, 2, "dialysis", line="dialysis")}</ol>
    <p class="further">Further away: {esc(short_name(fa["name"]))}, {mins_word(fa)} (position approximate).</p></section>
  {why_not(a["why_not"], verify=False)}
  {how(a)}
  <p class="foot">{FOOT}</p>
</div>
<aside class="col-map d-only">
  {map_chrome(jok_small(), ins, "#sim-jok", legend([("sw-search", "", "Jokihat, where you searched"), ("sw-rec", "1", "Nearest places whose record names dialysis"), ("sw-ring", "", "1 hour estimated drive from Jokihat"), ("sw-state", "", "State border")]))}
  <section class="teaser wi">
    <p class="t-k">What if a dialysis unit opened in Jokihat? <span class="tag-wi">what-if</span></p>
    <p class="t-big">{add["n_within"]} of {add["n"]} nearby villages would be within 1 hour of one, up from {base["n_within"]}.</p>
    <div class="t-act"><a class="btn-play" href="#sim-jok">{PLAY_SVG}See it on the map</a></div>
  </section>
</aside>
</main>"""


def screen_jaipur():
    a = X["jpr"]
    c1, c2, c3 = a["results"]
    loc = a["location"]
    ins = inset((loc["lat"], loc["lon"]))
    t = T["jpr"]["summary"]
    return f"""
{header("Dialysis", "Jaipur")}
<p class="where warn">From <b>Jaipur city</b>, using its head post office (PIN 302001). The patient could be anywhere within about {loc["precision_km"]:.0f} km. <a href="#">Type the patient's PIN</a> for one drive time.</p>
<main class="grid">
<div class="col-text">
  <section class="answer">
    <h1>Go to <span class="fac">{esc(c1["name"])}</span>, {km_word(c1["distance_km"])} from Jaipur's head post office. Its record says it runs a dialysis centre.</h1>
    <p class="d-line">Estimated drive: 1 to 45 min, depending on where in Jaipur the patient starts. {esc(c1["type"].capitalize())}, {esc(c1["operator"])}.</p>
    {quotes(c1, "dialysis", n=1, cls="q lead")}
    {conf(c1, "dialysis", big=True)}
    {next_step(c1, n_q=2)}
    <p class="fine src">{sources_line(c1)}</p>
  </section>
  <div class="m-only">{map_chrome(jpr_small(390, 230, labels=False), ins, "#sim-jpr", hint=False, cls="phone")}</div>
  <section class="backups"><h2>If {esc(c1["name"])} cannot help</h2>
    <ol>{backup(c2, 2, "dialysis", same_as=c1["name"], loose=True, line="dialysis")}{backup(c3, 3, "dialysis", loose=True, line="dialysis")}</ol></section>
  {why_not(a["why_not"], "#sim-jpr")}
  {how(a)}
  <p class="foot">{FOOT}</p>
</div>
<aside class="col-map d-only">
  {map_chrome(jpr_small(), ins, "#sim-jpr", legend(LEG_ANSWER[:2] + [("sw-prec", "", "Where in Jaipur the patient may be"), LEG_ANSWER[3]]))}
  <section class="teaser">
    <p class="t-k">What if patients trusted specialty claims alone?</p>
    <p class="t-big">{t["n_first_unsupported"]} of {t["n_points"]} nearby villages would go first to a place with no evidence of dialysis in its record.</p>
    <div class="t-act"><a class="btn-play" href="#sim-jpr">{PLAY_SVG}Play it on the map</a><a class="btn-ghost" href="#sim-jpr">Which record should be verified first?</a></div>
  </section>
</aside>
</main>"""


def india_view(W, H, marker=None, label=None, places=(), half=1650, lat0=22.6, lon0=82.6, cls=""):
    P = M.Proj(lat0, lon0, half, W, H)
    return M.india_map(P, marker=marker, marker_label=label, places=places, dots_href="img/india_dots.png", cls=cls)


def screen_landing():
    pl = M.PLACES

    def pt(pid):
        r = pl[pl.place_id == pid].iloc[0]
        return float(r.lat), float(r.lon)
    ex = [("Jaipur", "dialysis", (X["jpr"]["location"]["lat"], X["jpr"]["location"]["lon"]), "jaipur"),
          ("Lucknow, PIN 226001", "emergency C-section", pt("pin:226001"), "lucknow"),
          ("Jokihat, Bihar", "dialysis", (X["jok"]["location"]["lat"], X["jok"]["location"]["lon"]), "jokihat")]
    svg = india_view(620, 660, places=[(p[0], p[1], n, s) for n, s, p, _ in ex], half=1500, lat0=22.4, lon0=81.5)
    chips = "".join(f'<a class="ex" href="#{h}"><b>{esc(s.capitalize())}</b> near {esc(n)}</a>' for n, s, p, h in ex)
    return f"""<header class="top tall">
  <a class="brand" href="#landing"><span class="mark" aria-hidden="true"></span>Find care you can trust</a>
</header>
<main class="grid landing">
<div class="col-text">
  <h1 class="intro">Find a facility for a patient, with the facility's own words as proof.</h1>
  <form class="qform" onsubmit="return false">
    <label><span>What care is needed?</span><select><option>Emergency C-section and high-risk delivery</option><option>Dialysis</option></select></label>
    <label><span>Where is the patient?</span><input placeholder="PIN, village, town or district"></label>
    <button class="btn-find" type="button">Find care</button>
  </form>
  <p class="try">Or try one:</p>
  <div class="exs">{chips}</div>
  <p class="foot">We name up to 3 facilities, quote their own records, and say plainly when nothing nearby has clear evidence.
  {X["lko"]["funnel"]["steps"][0]["n"]:,} facility records from web pages, and every post office in India.</p>
</div>
<aside class="col-map"><div class="mapbox tall">{svg}<p class="hint">Each grey dot is a post office: together they draw India. Click a place to try it.</p></div></aside>
</main>"""


def screen_india():
    loc = X["lko"]["location"]
    svg = india_view(1280, 800, (loc["lat"], loc["lon"]), "PIN 226001, Lucknow", half=1500, lat0=23.0, lon0=81.0)
    return f"""<div class="overlay india"><div class="ov-map full">{svg}
  <div class="map-tools"><a class="btn-map" href="#lucknow">{CLOSE_SVG}Close map</a>
  <div class="zoom"><button type="button">+</button><button type="button">&minus;</button></div>
  <a class="btn-map light" href="#sim-lko">Zoom to Lucknow</a></div>
  {legend([("sw-search red", "", "Where you searched"), ("sw-po", "", "A post office (137,748 points draw India)"), ("sw-state", "", "State border")], cls="legend on-map")}
</div></div>"""


# ------------------------------------------------------------------ assemble
def main():
    M.render_dots_png(OUT / "img" / "india_dots.png") if not (OUT / "img" / "india_dots.png").exists() else None
    lko = X["lko"]["location"]
    jok = X["jok"]["location"]
    S_j = SIMS["jok"]
    add_pt = (jok["lat"], jok["lon"])
    jok_before = sim.run(S_j)
    jok_after = sim.run(S_j, extra=[add_pt])
    screens = {
        "landing": screen_landing(),
        "lucknow": screen_lucknow(),
        "sim-lko": screen_lucknow() + sim_screen("lko", "claims", 34, "PIN 226001", "C-section", 64,
                                                 center=None, legend_rows=LEG_CLAIMS,
                                                 verify_box=verify_box_html("lko", "C-section")),
        "sim-lko-ev": screen_lucknow() + sim_screen("lko", "evidence", 34, "PIN 226001", "C-section", 64,
                                                    center=None, legend_rows=LEG_EV[:3],
                                                    verify_box=verify_box_html("lko", "C-section")),
        "jokihat": screen_jokihat(),
        "sim-jok": screen_jokihat() + sim_screen(
            "jok", "evidence", 48, "Jokihat", "dialysis", 88, center=(25.98, 87.62), extra=[add_pt],
            edits=["Added a dialysis facility at Jokihat (clicked on the map)"], before=jok_before, after=jok_after,
            legend_rows=LEG_EV, whatif_pin=(add_pt, "What-if: new dialysis unit"),
            verify_box=verify_box_html("jok", "dialysis")),
        "jaipur": screen_jaipur(),
        "sim-jpr": screen_jaipur() + sim_screen("jpr", "claims", 30, "Jaipur", "dialysis", 46,
                                                legend_rows=LEG_CLAIMS, verify_box=verify_box_html("jpr", "dialysis")),
        "india": screen_lucknow() + screen_india(),
    }
    nav = "".join(f'<a href="#{k}">{k}</a>' for k in screens)
    body = "".join(f'<section class="screen" id="s-{k}" data-screen="{k}">{v}</section>' for k, v in screens.items())
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Answer first: design mockup</title>
<link rel="stylesheet" href="style.css"></head>
<body>
<nav class="mock-nav" aria-label="Mockup screens"><b>Mockup: answer first.</b> Real data from /api/answer and /api/trip (port 8810), the why_not, questions and call_info modules, and a prototype of the browser swarm rule run on the bundle. Screens: {nav}</nav>
{body}
<script>
function show(){{var h=(location.hash||'#lucknow').slice(1);var ok=document.getElementById('s-'+h)?h:'lucknow';
document.querySelectorAll('.screen').forEach(function(s){{s.hidden=s.dataset.screen!==ok}});
document.body.classList.toggle('shot',location.search.indexOf('shot')>=0);
document.body.classList.toggle('has-overlay',!!document.querySelector('#s-'+ok+' .overlay'));window.scrollTo(0,0)}}
addEventListener('hashchange',show);show();
</script>
</body></html>"""
    (OUT / "index.html").write_text(doc)
    print("wrote", OUT / "index.html", len(doc) // 1024, "KB")
    for k in ("lko", "jok", "jpr"):
        b, rows = verify_rank(SIMS[k])
        print(k, "base", b["n_within"], round(b["mean_min"], 1), b["n_first_noev"], "of", b["n"],
              "| verify-first", SIMS[k]["F"].name.iloc[rows[0][2]] if rows else None,
              rows[0][3]["n_within"] if rows else None, round(rows[0][3]["mean_min"], 1) if rows else None,
              rows[0][3]["n_first_noev"] if rows else None)
    print("jok add at Jokihat", jok_after["n_within"], round(jok_after["mean_min"], 1), jok_after["n_first_noev"])


if __name__ == "__main__":
    main()
