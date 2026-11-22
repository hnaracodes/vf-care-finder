"""Build the static "map story" mockup (index.html + img/*.png) from real data.

Inputs (all real, nothing invented):
  data/a_*.json, data/t_*.json      /api/answer and /api/trip responses from the live server (port 8812)
  data/extras_from_src_vf.json      why_not / questions / call_info / public_list run on the same queries
                                    with src/vf (these modules are not wired into /api/answer yet)
  data/india_states_ne50m.geojson   Natural Earth 1:50m admin-1, India only (public domain)
  src/vf/locate.py                  Natural Earth India outline (IND point of view), already in the repo
  dbapp/bundle/post_offices.parquet, places.parquet

Run: .venv/bin/python app/design/map_story/build_mockup.py
"""
from __future__ import annotations

import base64
import html
import json
import math
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
from vf import locate  # noqa: E402

D = HERE / "data"
IMG = HERE / "img"
IMG.mkdir(exist_ok=True)


def J(name):
    return json.loads((D / name).read_text())


A = {k: J(f"a_{k}.json") for k in ("lko", "jok", "jpr")}
T = {k: J(f"t_{k}.json") for k in ("lko", "jok", "jpr")}
X = J("extras_from_src_vf.json")
STATES = J("india_states_ne50m.geojson")["features"]

# ---------------------------------------------------------------- geography

raw = zlib.decompress(base64.b85decode("".join(locate._INDIA_OUTLINE_B85))).decode()
OUTLINE = []
for ring in raw.split(";"):
    xy = np.cumsum(np.array(ring.split(","), dtype=np.int64).reshape(-1, 2), axis=0) / 1000.0
    OUTLINE.append(xy)

PO = pd.read_parquet(ROOT / "dbapp/bundle/post_offices.parquet").dropna(subset=["lat", "lon"])
# drop placeholder points and a band of rows that share one latitude (745 offices at 15.59 N draw a fake line)
_lat_n = PO["lat"].round(2).map(PO["lat"].round(2).value_counts())
PO = PO[~PO["placeholder"] & (_lat_n < 200)]
PO_LON, PO_LAT = PO["lon"].to_numpy(float), PO["lat"].to_numpy(float)
PL = pd.read_parquet(ROOT / "dbapp/bundle/places.parquet")
DISTRICTS = PL[PL.kind == "district"]
POFF = PL[(PL.kind == "post_office") & PL.note.isna() & (PL.precision_km < 6)]

STATE_SHAPES = []  # (name, [rings as np arrays lon,lat])
for f in STATES:
    g = f["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    rings = [np.array(r, float) for p in polys for r in p]
    STATE_SHAPES.append((f["properties"]["name"], rings))


def point_in_rings(lon, lat, rings):
    inside = False
    for r in rings:
        x, y = r[:, 0], r[:, 1]
        x2, y2 = np.roll(x, -1), np.roll(y, -1)
        c = ((y > lat) != (y2 > lat)) & (lon < (x2 - x) * (lat - y) / np.where(y2 == y, 1e-12, y2 - y) + x)
        inside ^= bool(c.sum() % 2)
    return inside


def state_of(lon, lat):
    for name, rings in STATE_SHAPES:
        if point_in_rings(lon, lat, rings):
            return name
    return None


NEIGHBOURS = [("NEPAL", 84.1, 28.2), ("BANGLADESH", 90.2, 23.9), ("PAKISTAN", 69.5, 28.5), ("CHINA", 84.5, 33.5),
              ("BHUTAN", 90.4, 27.45), ("MYANMAR", 95.5, 21.5), ("SRI LANKA", 80.7, 7.6)]


def merc(lon, lat):
    lon = np.asarray(lon, float)
    lat = np.clip(np.asarray(lat, float), -85, 85)
    s = np.sin(np.radians(lat))
    return (lon + 180) / 360, 0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)


class View:
    def __init__(self, lon, lat, km_across, w, h):
        """Centre and the ground distance across the narrower side."""
        self.w, self.h = w, h
        self.cx, self.cy = merc(lon, lat)
        # unit-square span per km at this latitude
        circ = 40075.0 * math.cos(math.radians(lat))
        self.k = min(w, h) / (km_across / circ)  # px per unit
        self.lat = lat

    @classmethod
    def fit(cls, pts, w, h, pad_px=60, min_km=6, top=0, bottom=0, left=0, right=0):
        pts = np.asarray(pts, float)
        x, y = merc(pts[:, 0], pts[:, 1])
        uw, uh = max(x.max() - x.min(), 1e-9), max(y.max() - y.min(), 1e-9)
        aw, ah = w - 2 * pad_px - left - right, h - 2 * pad_px - top - bottom
        k = min(aw / uw, ah / uh)
        lat = float(pts[:, 1].mean())
        circ = 40075.0 * math.cos(math.radians(lat))
        kmax = min(w, h) / (min_km / circ)
        k = min(k, kmax)
        v = cls.__new__(cls)
        v.w, v.h, v.k, v.lat = w, h, k, lat
        # centre so that the content box sits in the free area
        v.cx = (x.min() + x.max()) / 2 - (left - right) / 2 / k
        v.cy = (y.min() + y.max()) / 2 - (top - bottom) / 2 / k
        return v

    def p(self, lon, lat):
        x, y = merc(lon, lat)
        return (x - self.cx) * self.k + self.w / 2, (y - self.cy) * self.k + self.h / 2

    def km_px(self, km):
        circ = 40075.0 * math.cos(math.radians(self.lat))
        return km / circ * self.k

    def bbox_lonlat(self, margin=0.2):
        xs = np.array([0, self.w]) / self.k
        x0, x1 = self.cx - self.w / 2 / self.k, self.cx + self.w / 2 / self.k
        y0, y1 = self.cy - self.h / 2 / self.k, self.cy + self.h / 2 / self.k
        dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
        lon0, lon1 = (x0 - dx) * 360 - 180, (x1 + dx) * 360 - 180

        def ilat(y):
            return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
        return lon0, lon1, ilat(y1 + dy), ilat(y0 - dy)


def dp(pts, tol):
    """Douglas-Peucker on pixel coordinates."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p0, p1 = pts[a], pts[b]
        seg = p1 - p0
        L = float(np.hypot(*seg))
        if L < 1e-6:  # closed ring: first == last, so measure from the shared point
            d = np.hypot(pts[a + 1:b, 0] - p0[0], pts[a + 1:b, 1] - p0[1])
        else:
            d = np.abs(seg[0] * (pts[a + 1:b, 1] - p0[1]) - seg[1] * (pts[a + 1:b, 0] - p0[0])) / L
        i = int(np.argmax(d))
        if d[i] > tol:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pts[keep]


def path_d(v, rings, tol=0.6, close=True):
    out = []
    for r in rings:
        x, y = v.p(r[:, 0], r[:, 1])
        if x.max() < -50 or x.min() > v.w + 50 or y.max() < -50 or y.min() > v.h + 50:
            continue
        pts = dp(np.column_stack([x, y]), tol)
        if len(pts) < 2:
            continue
        out.append("M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in pts) + ("Z" if close else ""))
    return "".join(out)


def esc(s):
    return html.escape(str(s), quote=True)


def dots_png(v, name, scale=2, color=(150, 168, 190), alpha=70, radius=1.0, max_alpha=255):
    lon0, lon1, lat0, lat1 = v.bbox_lonlat(0.02)
    m = (PO_LON > lon0) & (PO_LON < lon1) & (PO_LAT > lat0) & (PO_LAT < lat1)
    x, y = v.p(PO_LON[m], PO_LAT[m])
    W, H = v.w * scale, v.h * scale
    acc = np.zeros((H, W), float)
    xi, yi = (x * scale).astype(int), (y * scale).astype(int)
    ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    np.add.at(acc, (yi[ok], xi[ok]), 1.0)
    # splat to radius
    r = max(1, int(round(radius * scale)))
    from numpy.lib.stride_tricks import sliding_window_view  # noqa: F401
    k = np.zeros_like(acc)
    for dy in range(-r + 1, r):
        for dx in range(-r + 1, r):
            if dx * dx + dy * dy < r * r:
                k += np.roll(np.roll(acc, dy, 0), dx, 1)
    a = np.clip(1 - (1 - alpha / 255) ** k, 0, max_alpha / 255)
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = color
    img[..., 3] = (a * 255).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(IMG / f"{name}.png", optimize=True)
    return f"img/{name}.png", int(m.sum())


# ---------------------------------------------------------------- label placement

class Labeler:
    def __init__(self, w, h, reserved=()):
        self.boxes = list(reserved)
        self.w, self.h = w, h
        self.out = []

    def add(self, x, y, text, cls, size, upper=False, anchor="middle", dy=0, force=False, ls=0.0):
        cw = size * (0.62 if upper else 0.54) + ls
        tw = len(text) * cw
        if anchor == "middle":
            x0 = x - tw / 2
        elif anchor == "start":
            x0 = x
        else:
            x0 = x - tw
        y0 = y + dy - size
        box = (x0 - 3, y0 - 2, x0 + tw + 3, y0 + size + 4)
        if not force:
            if box[0] < 4 or box[2] > self.w - 4 or box[1] < 4 or box[3] > self.h - 4:
                return False
            for b in self.boxes:
                if not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]):
                    return False
        self.boxes.append(box)
        self.out.append(f'<text x="{x:.1f}" y="{y + dy:.1f}" class="{cls}" text-anchor="{anchor}">{esc(text)}</text>')
        return True

    def svg(self):
        return "".join(self.out)


def title(s):
    return " ".join(w if w.isupper() and len(w) <= 3 and w not in ("NEW",) else w.capitalize() for w in s.split())


# ---------------------------------------------------------------- map component

ICON_EXPAND = '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'
ICON_HOME = '<svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="8" cy="8" r="1.6" fill="currentColor"/></svg>'
ICON_INDIA = '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 3l5 1 5-1-1 5 1 5-5-1-5 1 1-5z" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>'
ICON_PLAY = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>'
ICON_REPLAY = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M3 8a5 5 0 1 0 1.6-3.7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M2.5 1.8v3.6h3.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'
ICON_CLOSE = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.8"/></svg>'
ICON_PHONE = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M4 1.5h2.2l1 3.2-1.6 1.2a8 8 0 0 0 4.5 4.5l1.2-1.6 3.2 1V12a2 2 0 0 1-2 2A11.5 11.5 0 0 1 2 3.5a2 2 0 0 1 2-2z" fill="currentColor"/></svg>'


def sym_pin(x, y, n, sel=False, small=False):
    r = 9 if small else (14 if sel else 12)
    ring = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 5}" class="pin-halo"/>' if sel else ""
    return (f'{ring}<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" class="pin"/>'
            f'<text x="{x:.1f}" y="{y + (4 if small else 5):.1f}" class="pin-n{" s" if small else ""}" text-anchor="middle">{n}</text>')


def sym_claim(x, y, s=6):
    return f'<path d="M{x:.1f},{y - s:.1f}L{x + s:.1f},{y:.1f}L{x:.1f},{y + s:.1f}L{x - s:.1f},{y:.1f}Z" class="claim"/>'


def sym_public(x, y, s=5):
    return f'<rect x="{x - s:.1f}" y="{y - s:.1f}" width="{2 * s}" height="{2 * s}" class="silent"/>'


def sym_lower(x, y):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="lower"/>'


def sym_place(x, y):
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" class="place-halo"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" class="place"/>')


def base_layers(v, name, dot_alpha=60, dot_color=(140, 160, 186), dot_r=1.0, state_labels=True, neighbours=True,
                outline_w=1.3, country=False):
    href, n = dots_png(v, name, alpha=dot_alpha, color=dot_color, radius=dot_r)
    land = path_d(v, OUTLINE, tol=0.5)
    states = "".join(f'<path d="{path_d(v, rings, tol=0.7)}" class="state"/>' for _, rings in STATE_SHAPES)
    s = [f'<rect width="{v.w}" height="{v.h}" class="sea"/>',
         f'<path d="{land}" class="land" fill-rule="evenodd"/>',
         f'<image href="{href}" x="0" y="0" width="{v.w}" height="{v.h}" preserveAspectRatio="none"/>',
         states,
         f'<path d="{land}" class="outline" style="stroke-width:{outline_w}" fill="none"/>']
    return "".join(s), n


def state_label_pts(v):
    out = []
    for name, rings in STATE_SHAPES:
        big = max(rings, key=len)
        lon, lat = big[:, 0].mean(), big[:, 1].mean()
        out.append((name, lon, lat))
    return out


def scale_bar(v, x, y, cls=""):
    for km in (1, 2, 5, 10, 20, 50, 100, 200, 500):
        px = v.km_px(km)
        if px > 60:
            break
    return (f'<g class="scale {cls}"><path d="M{x},{y - 5}V{y}H{x + px:.1f}V{y - 5}"/>'
            f'<text x="{x + px + 6:.1f}" y="{y}">{km} km</text></g>')


def inset(view_main, w=132, h=150, label=None):
    """India locator: outline + states + a box at the main view."""
    v = View.fit([[68.2, 6.8], [97.4, 35.6]], w, h, pad_px=6, min_km=10)
    land = path_d(v, OUTLINE, tol=0.8)
    states = "".join(f'<path d="{path_d(v, rings, tol=0.9)}" class="i-state"/>' for _, rings in STATE_SHAPES)
    lon0, lon1, lat0, lat1 = view_main.bbox_lonlat(0)
    x0, y1 = v.p(lon0, lat0)
    x1, y0 = v.p(lon1, lat1)
    bw, bh = max(x1 - x0, 7), max(y1 - y0, 7)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lab = f'<text x="{w / 2}" y="{h + 14}" class="i-lab" text-anchor="middle">{esc(label)}</text>' if label else ""
    return (f'<svg class="inset" viewBox="0 0 {w} {h + (18 if label else 0)}" width="{w}" height="{h + (18 if label else 0)}">'
            f'<path d="{land}" class="i-land" fill-rule="evenodd"/>{states}'
            f'<rect x="{cx - bw / 2:.1f}" y="{cy - bh / 2:.1f}" width="{bw:.1f}" height="{bh:.1f}" class="i-box"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2" class="i-dot"/>{lab}</svg>')


def label_places(v, lab, searched=None, districts=True, pos=True, states=True, max_po=14, neighbours=True,
                 district_size=12, skip=()):
    lon0, lon1, lat0, lat1 = v.bbox_lonlat(0)
    if districts:
        d = DISTRICTS[(DISTRICTS.lon > lon0) & (DISTRICTS.lon < lon1) & (DISTRICTS.lat > lat0) & (DISTRICTS.lat < lat1)]
        for _, r in d.iterrows():
            if r["name"].upper() in skip:
                continue
            x, y = v.p(r.lon, r.lat)
            if lab.add(x, y, title(r["name"]), "l-town", district_size, dy=-7):
                lab.out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" class="town-dot"/>')
    if pos:
        p = POFF[(POFF.lon > lon0) & (POFF.lon < lon1) & (POFF.lat > lat0) & (POFF.lat < lat1)]
        p = p.drop_duplicates("name").sample(frac=1.0, random_state=3)
        k = 0
        for _, r in p.iterrows():
            if k >= max_po:
                break
            nm = title(r["name"])
            if len(nm) > 16 or any(ch.isdigit() for ch in nm) or nm.upper() in skip:
                continue
            x, y = v.p(r.lon, r.lat)
            if lab.add(x, y, nm, "l-po", 11):
                k += 1
    if states:
        for name, lon, lat in state_label_pts(v):
            x, y = v.p(lon, lat)
            lab.add(x, y, name.upper(), "l-state", 12, upper=True, ls=1.5)
    if neighbours:
        for name, lon, lat in NEIGHBOURS:
            x, y = v.p(lon, lat)
            lab.add(x, y, name, "l-country", 12, upper=True, ls=1.5)


# ---------------------------------------------------------------- copy helpers

FIELD = {"capability": "its services list", "procedure": "its procedures list", "equipment": "its equipment list",
         "specialties": "its specialties list", "description": "its description"}
REQ = {"caesarean section": "C-section", "blood bank or transfusion": "Blood for transfusion",
       "anaesthesia or operating theatre": "Operating theatre", "dialysis": "Dialysis",
       "dialysis unit, machines or nephrologist": "Dialysis unit or machines"}
CONF = {"high": ("Strong record evidence", 3), "medium": ("Some record evidence", 2), "low": ("Thin record evidence", 1)}
SHORT = {"emoc": "C-section", "dialysis": "dialysis"}


def bars(n):
    return '<span class="bars">' + "".join(f'<i class="{"on" if i < n else ""}"></i>' for i in range(3)) + "</span>"


def merged_evidence(c):
    """Duplicate quotes merged; requirement names in plain words."""
    out = []
    for e in c.get("evidence") or []:
        req = REQ.get(e["requirement"], e["requirement"].capitalize())
        for o in out:
            if o["quote"] == e["quote"]:
                o["reqs"].append(req.lower())
                break
        else:
            out.append({"quote": e["quote"], "field": e["field"], "reqs": [req]})
    return out


def conf_reason(c, line):
    """The actual reason only, never the generic 'one fact, or one web source'."""
    lvl = c["confidence"]
    ev = merged_evidence(c)
    svc = SHORT[line]
    if lvl == "high":
        reqs = [r for e in ev for r in e["reqs"]]
        names = ", ".join(r.lower() for r in reqs[:-1]) + (" and " if len(reqs) > 1 else "") + reqs[-1].lower()
        return f"Its record names {names.replace('c-section', 'C-section')}."
    if lvl == "medium":
        miss = [m.replace("no fact in the record mentions ", "") for m in c.get("missing") or []]
        return f"Its record names {svc}, but not {' or '.join(miss)}."
    if len(ev) == 1:
        return f"Only one sentence in its record names {svc}."
    return "Its support is thin."


def short_name(n):
    return n.split(",")[0].strip()


def km(c):
    d = c.get("distance_km")
    if d is None:
        return "position approximate"
    if c.get("location_precision_km", 0) and c["location_precision_km"] >= d * 2 and c["location_precision_km"] > 2:
        return "position approximate"
    return f"{d:.1f} km" if d < 10 else f"{d:.0f} km"


def official(c):
    w = (c.get("sources") or {}).get("official_website")
    if not w or "goo.gl" in w or "maps.app" in w or w.startswith("http"):
        return None
    return w


def sources_line(c):
    s = c.get("sources") or {}
    n = s.get("n_domains") or 0
    w = official(c)
    return f"Found on {n} websites" + (f", including its own ({esc(w)})." if w else ". No official website in the record.")


def why_not_rows(items, n=3):
    rows = []
    for it in items[:n]:
        sym = {"claim_only": "claim", "public_record_silent": "silent", "ranked_lower": "lower"}[it["reason_code"]]
        q = it.get("claim_quote")
        quote = ""
        if it["reason_code"] == "ranked_lower" and it["quote_or_absence"]["kind"] == "quote":
            t = it["quote_or_absence"]["text"]
            quote = f'<q>{esc(t if len(t) < 70 else t[:66] + "...")}</q>'
        elif q and q.get("field") != "specialties":
            quote = f'<q>{esc(q["text"])}</q>'
        rows.append(f'''<li><span class="k k-{sym}"></span><div><b>{esc(short_name(it["name"]))}</b>
<span class="mut">{esc(it["operator"] or "")} {esc(it["type"])} &middot; {esc(it["distance_label"])}</span>
<p>{esc(plain_reason(it))}</p>{quote}</div></li>''')
    return "".join(rows)


def plain_reason(it):
    r = it["reason_plain"]
    return (r.replace("Recommendable, but it scored below the cards shown: its record", "Has some evidence, listed lower: its record")
             .replace("dialysis unit, machines or nephrologist", "a dialysis unit or machines"))


# ---------------------------------------------------------------- page chrome

def header(service, place, compact=False):
    return f'''<header class="top">
  <div class="brand"><span class="logo"></span><div><b>Find care you can trust</b><small>Indian health facilities, judged by what their own records say</small></div></div>
  <form class="search" onsubmit="return false">
    <label><span>Care needed</span><div class="sel">{esc(service)}<i></i></div></label>
    <label class="place"><span>Patient's place</span><div class="inp">{esc(place)}</div></label>
    <button class="go">Find</button>
  </form>
  <a class="ex">Examples &#9662;</a>
</header>'''


def map_controls(expanded=False):
    first = (f'<button class="mc wide">{ICON_CLOSE}<span>Close map</span></button>' if expanded
             else f'<button class="mc wide primary">{ICON_EXPAND}<span>Expand map</span></button>')
    return f'''<div class="mapctl">{first}
<div class="zoom"><button class="mc">+</button><button class="mc">&minus;</button></div>
<button class="mc wide">{ICON_HOME}<span>Back to results</span></button>
<button class="mc wide">{ICON_INDIA}<span>All of India</span></button></div>'''


# ================================================================ FRAME BUILDERS

def facility_layer(v, ans, line, sel=1, show_whynot=True, extra=None, small=False):
    out = []
    loc = ans["location"]
    wn = X[extra]["why_not"]["items"] if extra and show_whynot else []
    for it in wn:
        x, y = v.p(it["lon"], it["lat"])
        f = {"claim_only": sym_claim, "public_record_silent": sym_public, "ranked_lower": lambda a, b: sym_lower(a, b)}[it["reason_code"]]
        g = f(x, y)
        if it.get("position_approximate"):
            g = f'<g class="approx">{g}</g>'
        out.append(g)
    cards = ans["results"] or ([ans["beyond_radius"]] if ans.get("beyond_radius") else [])
    placed = []
    for i, c in enumerate(cards, 1):
        x, y = v.p(c["lon"], c["lat"])
        # spread pins that sit on top of each other (e.g. two cards 5.8 km away, 120 m apart)
        for (px, py) in placed:
            if math.hypot(x - px, y - py) < 22:
                ang = -0.6 + 0.9 * len(placed)
                nx, ny = px + 26 * math.cos(ang), py + 26 * math.sin(ang)
                out.append(f'<path d="M{x:.1f},{y:.1f}L{nx:.1f},{ny:.1f}" class="leader"/><circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" class="true-pos"/>')
                x, y = nx, ny
        placed.append((x, y))
        out.append(sym_pin(x, y, i, sel=(i == sel), small=small))
    px, py = v.p(loc["lon"], loc["lat"])
    out.append(sym_place(px, py))
    return "".join(out), placed, (px, py)


def card_rows(ans, line, extra, open_first=True, phone=False):
    cards = ans["results"]
    rows = []
    for i, c in enumerate(cards, 1):
        word, nb = CONF[c["confidence"]]
        ev = merged_evidence(c)
        e0 = ev[0]
        qtext = e0["quote"]
        long_q = len(qtext) > 90
        if long_q:
            qtext = qtext[:86].rsplit(" ", 1)[0] + "..."
        why = ""
        if i > 1 and "Tied with" in (c.get("why_here") or ""):
            why = ""
        meta = f'{esc((c.get("operator") or "").replace("None", ""))} {esc(c["type"])} &middot; {esc(c["city"])}'.strip()
        body = f'''<li class="row{" open" if (i == 1 and open_first) else ""}">
<div class="rhead"><span class="num">{i}</span><div class="rname"><b>{esc(short_name(c["name"]))}</b><span class="mut">{meta}</span></div>
<div class="dist"><b>{km(c)}</b><span>straight line</span></div></div>
<div class="conf c-{c["confidence"]}">{bars(nb)}<b>{word}.</b> {esc(conf_reason(c, line))}</div>
<blockquote><q>{esc(qtext)}</q><cite>from {FIELD.get(e0["field"], e0["field"])}</cite></blockquote>'''
        if i == 1 and open_first:
            qs = X[extra]["questions"].get(c["name"], [])[:2]
            ci = X[extra]["call_info"].get(c["name"], {})
            more_q = "".join(f'<blockquote class="sm"><span class="req">{esc(e["reqs"][0])}</span><q>{esc(e["quote"])}</q><cite>from {FIELD.get(e["field"], e["field"])}</cite></blockquote>' for e in ev[1:])
            body += f'''{more_q}
<div class="before"><div class="bhead"><b>Before you go, call</b>
<button class="call">{ICON_PHONE}<span>{esc(ci.get("phone", ""))}</span></button></div>
<p class="mut sm">Number from its record. It may be out of date.</p>
<ol class="ask">{"".join(f"<li>{esc(q['question'])}</li>" for q in qs)}</ol></div>
<details class="more"><summary>Details: sources, location, why it is listed here</summary></details>'''
        body += "</li>"
        rows.append(body)
    return "".join(rows)


def layout_laptop(fid, title_txt, header_html, list_html, map_html, note=""):
    return f'''<section class="frame laptop" id="{fid}"><div class="fcap">{esc(title_txt)}</div><div class="screen">
{header_html}
<main class="split"><aside class="list">{list_html}</aside><div class="mapwrap">{map_html}</div></main>
</div>{note}</section>'''


def legend(items, cls=""):
    sw = {"pin": '<svg width="22" height="22"><circle cx="11" cy="11" r="9" class="pin"/><text x="11" y="15" class="pin-n s" text-anchor="middle">1</text></svg>',
          "place": '<svg width="22" height="22"><circle cx="11" cy="11" r="9" class="place-halo"/><circle cx="11" cy="11" r="4.5" class="place"/></svg>',
          "claim": '<svg width="22" height="22"><path d="M11,5L17,11L11,17L5,11Z" class="claim"/></svg>',
          "silent": '<svg width="22" height="22"><rect x="6" y="6" width="10" height="10" class="silent"/></svg>',
          "lower": '<svg width="22" height="22"><circle cx="11" cy="11" r="5" class="lower"/></svg>',
          "village": '<svg width="22" height="22"><path d="M3,17L16,8" class="tr-teal" style="opacity:.8"/><circle cx="16" cy="8" r="2.6" class="hd-teal"/></svg>',
          "dots": '<svg width="22" height="22"><circle cx="6" cy="8" r="1.3" class="podot"/><circle cx="12" cy="13" r="1.3" class="podot"/><circle cx="16" cy="7" r="1.3" class="podot"/><circle cx="9" cy="16" r="1.3" class="podot"/></svg>',
          "leg1": '<svg width="22" height="22"><path d="M2,16L17,7" class="tr-amber" style="opacity:.9"/><circle cx="17" cy="7" r="2.6" class="hd-amber"/></svg>',
          "leg2": '<svg width="22" height="22"><path d="M2,16L20,6" class="tr-onward" style="opacity:1"/></svg>',
          "go": '<svg width="22" height="22"><path d="M2,16L17,7" class="tr-teal" style="opacity:.9"/><circle cx="17" cy="7" r="2.6" class="hd-teal"/></svg>',
          "ring": '<svg width="22" height="22"><circle cx="11" cy="11" r="8" class="ring"/></svg>',
          "border": '<svg width="22" height="22"><path d="M2,11H20" class="state" /></svg>',
          "evdot": '<svg width="22" height="22"><circle cx="11" cy="11" r="5" class="ev-dot"/></svg>',
          "wi": '<svg width="22" height="22"><circle cx="11" cy="11" r="8" class="wi-ring"/><path d="M7,11H15M11,7V15" class="wi-plus"/></svg>',
          "wihead": '<svg width="22" height="22"><path d="M2,18L10,10L19,6" class="tr-whatif" style="opacity:.9"/><circle cx="19" cy="6" r="2.6" class="hd-whatif"/></svg>',
          "approx": '<svg width="22" height="22"><g class="approx"><path d="M11,5L17,11L11,17L5,11Z" class="claim"/></g></svg>'}
    return f'<div class="legend {cls}">' + "".join(f'<div>{sw[k]}<span>{t}</span></div>' for k, t in items) + "</div>"


# ---------------------------------------------------------------- frame: Lucknow results (laptop)

MW, MH = 860, 744  # laptop map size


def frame_lucknow():
    ans, ex = A["lko"], "lko"
    loc = ans["location"]
    pts = [[loc["lon"], loc["lat"]]] + [[c["lon"], c["lat"]] for c in ans["results"]] + \
          [[i["lon"], i["lat"]] for i in X[ex]["why_not"]["items"]]
    v = View.fit(pts, MW, MH, pad_px=70, min_km=14, top=60, bottom=40)
    base, n = base_layers(v, "lko_local", dot_alpha=110, dot_color=(120, 138, 160), dot_r=1.3)
    lab = Labeler(MW, MH, reserved=[(MW - 190, 0, MW, 200), (0, MH - 200, 160, MH), (MW - 300, MH - 190, MW, MH), (0, 0, 560, 64)])
    fac, placed, (px, py) = facility_layer(v, ans, "emoc", extra=ex)
    for (x, y) in placed:
        lab.boxes.append((x - 18, y - 18, x + 18, y + 18))
    lab.add(px, py, "PIN 226001, Lucknow", "l-searched", 16, dy=30, force=True)
    lab.boxes.append((px - 16, py - 16, px + 16, py + 16))
    # names for the result pins
    for i, ((x, y), c) in enumerate(zip(placed, ans["results"]), 1):
        lab.add(x + 18, y + 4, short_name(c["name"]), "l-fac", 12.5, anchor="start", force=True)
    # nearest why-not item gets a name
    it = X[ex]["why_not"]["items"][0]
    x, y = v.p(it["lon"], it["lat"])
    lab.add(x + 10, y + 4, "Civil hospital, 0.8 km", "l-why", 11.5, anchor="start")
    label_places(v, lab, max_po=16, district_size=13, skip={"LUCKNOW", "BALRAMPUR HOSPITAL"})
    sb = scale_bar(v, 180, MH - 22)
    svg = f'<svg class="map" viewBox="0 0 {MW} {MH}" width="{MW}" height="{MH}">{base}{fac}{lab.svg()}{sb}</svg>'
    over = f'''{map_controls()}
<div class="maptitle"><b>Emergency C-section near PIN 226001</b><span>Lucknow, Uttar Pradesh &middot; 3 options within 6 km</span></div>
<div class="tripcta"><div><b>Why not the nearest place that says it does C-sections?</b>
<span>Watch where patients from {T["lko"]["summary"]["n_points"]} nearby villages would go under each rule, then try what-ifs.</span></div><button class="play">{ICON_PLAY}<span>Run the village simulation</span></button></div>
<div class="insetbox">{inset(v, label="Uttar Pradesh")}</div>
{legend([("pin", "Recommended: its record shows C-section"), ("claim", "Nearer, claims it, no evidence in record"), ("silent", "Nearer public hospital, record silent"), ("place", "Searched place"), ("dots", "Villages (post office areas)")], "lg-map")}
<div class="hint">Scroll to zoom &middot; drag to move</div>'''
    m = f'<div class="mapbox">{svg}{over}</div>'

    c1 = ans["results"][0]
    wn = X[ex]["why_not"]
    lst = f'''<div class="answer"><span class="eyebrow">Best option with clear evidence</span>
<h1><span class="num big">1</span>BNK Hospital</h1>
<p class="ans-line"><b>3.5 km</b> straight line &middot; <span class="c-high">Strong record evidence</span> &middot; <b>Call before you go</b></p></div>
<p class="placeline">Searching around <b>PIN 226001, Lucknow, Uttar Pradesh</b>. Position good to about 2 km.</p>
<h2 class="lh">3 options with evidence of emergency C-section within 50 km</h2>
<ol class="rows">{card_rows(ans, "emoc", ex)}</ol>
<div class="whynot"><h3>Why not the nearer ones?</h3>
<p>{wn["n_nearer"]} places are nearer than BNK Hospital. We found no fact naming C-section in any of their records.</p>
<ul>{why_not_rows(wn["items"], 2)}</ul><a class="lnk">Show all {wn["n_nearer"]}, and {wn["n_approx_nearer"]} with an approximate position</a></div>
<div class="foot"><a class="lnk">How we decided</a><p>Distances are straight-line km. Web pages can be out of date. This dataset is mostly private facilities.</p></div>'''
    return layout_laptop("lucknow", "1. Lucknow PIN 226001, emergency C-section: results (laptop 1280x800)",
                         header("Emergency C-section and high-risk delivery", "226001"), lst, m)


# ---------------------------------------------------------------- frame: expanded map, zoomed out to India

def frame_expanded():
    ans = A["lko"]
    loc = ans["location"]
    W, H = 1280, 800
    v = View.fit([[68.2, 6.8], [97.4, 35.6]], W, H, pad_px=24, min_km=50, right=330, top=40)
    base, n = base_layers(v, "india_full", dot_alpha=80, dot_color=(150, 172, 200), dot_r=0.9, outline_w=1.4)
    lab = Labeler(W, H, reserved=[(W - 340, 0, W, H), (0, 0, 420, 70), (0, H - 60, 400, H)])
    px, py = v.p(loc["lon"], loc["lat"])
    mark = (f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{v.km_px(50):.1f}" class="ring ring-c"/>{sym_place(px, py)}'
            f'<g class="cluster"><rect x="{px + 16:.1f}" y="{py - 44:.1f}" rx="13" width="186" height="26"/>'
            f'<text x="{px + 28:.1f}" y="{py - 26:.1f}">3 options within 6 km</text></g>')
    lab.boxes.append((px - 20, py - 50, px + 210, py + 40))
    lab.add(px + 18, py + 6, "PIN 226001, Lucknow", "l-searched", 16, anchor="start", force=True)
    for nm, lon, lat in [("Delhi", 77.21, 28.61), ("Mumbai", 72.88, 19.08), ("Kolkata", 88.36, 22.57), ("Chennai", 80.27, 13.08),
                         ("Bengaluru", 77.59, 12.97), ("Hyderabad", 78.47, 17.38), ("Ahmedabad", 72.57, 23.02), ("Patna", 85.14, 25.6),
                         ("Jaipur", 75.79, 26.91), ("Bhopal", 77.41, 23.26), ("Guwahati", 91.74, 26.14)]:
        x, y = v.p(lon, lat)
        if lab.add(x, y, nm, "l-city", 12.5, dy=-7):
            lab.out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" class="town-dot"/>')
    for name, lon, lat in state_label_pts(v):
        x, y = v.p(lon, lat)
        lab.add(x, y, name.upper(), "l-state", 10.5, upper=True, ls=1.2)
    for name, lon, lat in NEIGHBOURS:
        x, y = v.p(lon, lat)
        lab.add(x, y, name, "l-country", 12, upper=True, ls=1.5)
    svg = f'<svg class="map" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{base}{mark}{lab.svg()}{scale_bar(v, 24, H - 24)}</svg>'
    rows = "".join(f'<li><span class="num">{i}</span><div><b>{esc(short_name(c["name"]))}</b><span class="mut">{km(c)} &middot; {CONF[c["confidence"]][0]}</span></div></li>'
                   for i, c in enumerate(ans["results"], 1))
    rail = f'''<div class="rail"><div class="rail-h"><b>Emergency C-section</b><span>near PIN 226001, Lucknow</span></div>
<ol class="mini">{rows}</ol>
<div class="zoomladder"><b>Zoom level</b><ol><li class="on">All of India</li><li>State</li><li>District</li><li>Neighbourhood</li></ol>
<p class="mut sm">Scroll, pinch or use + and &minus;. Results show as one marker until you zoom in to the district.</p></div>
<button class="play wide">{ICON_HOME}<span>Zoom to the 3 options</span></button>
<button class="play wide ghost">{ICON_PLAY}<span>Run the village simulation</span></button>
{legend([("place", "Searched place"), ("ring", "50 km around it"), ("dots", "Post offices: one dot per area"), ("border", "State border")], "lg-rail")}</div>'''
    over = f'''<div class="xtop"><button class="mc wide">{ICON_CLOSE}<span>Close map (Esc)</span></button><div class="zoom"><button class="mc">+</button><button class="mc">&minus;</button></div></div>{rail}
<div class="xnote">Every dot is a post office. Together they draw India. The map has no street tiles: it is built only from public data.</div>'''
    return f'''<section class="frame laptop" id="expanded"><div class="fcap">3. Expanded map (full screen), scrolled out to all of India (laptop)</div><div class="screen mapfull">{svg}{over}</div></section>'''


# ---------------------------------------------------------------- frame: Jokihat abstention

def jok_map(W, H, name, phone=False, reserved=()):
    ans, t = A["jok"], T["jok"]
    loc = ans["location"]
    c = ans["beyond_radius"]
    pts = [[loc["lon"], loc["lat"]], [c["lon"], c["lat"]]]
    # include the 50 km ring
    for ang in range(0, 360, 30):
        dlat = 50 / 111 * math.sin(math.radians(ang))
        dlon = 50 / (111 * math.cos(math.radians(loc["lat"]))) * math.cos(math.radians(ang))
        pts.append([loc["lon"] + dlon, loc["lat"] + dlat])
    if phone:
        v = View.fit(pts, W, H, pad_px=18, min_km=50, top=34)
    else:
        v = View.fit(pts, W, H, pad_px=30, min_km=50, top=80, bottom=150, right=60, left=150)
    base, n = base_layers(v, name, dot_alpha=120, dot_color=(130, 150, 175), dot_r=1.3 if not phone else 1.0)
    px, py = v.p(loc["lon"], loc["lat"])
    cx, cy = v.p(c["lon"], c["lat"])
    rr = v.km_px(50)
    prec = v.km_px(loc["precision_km"])
    g = (f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{rr:.1f}" class="ring ring-empty"/>'
         f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{prec:.1f}" class="prec"/>'
         f'<path d="M{px:.1f},{py:.1f}L{cx:.1f},{cy:.1f}" class="reach"/>')
    mx, my = (px + cx) / 2, (py + cy) / 2
    lab = Labeler(W, H, reserved=list(reserved))
    lab.boxes += [(px - 14, py - 14, px + 14, py + 14), (cx - 16, cy - 16, cx + 16, cy + 16)]
    lab.add(mx + 10, my - 6, "76 km", "l-km", 14 if not phone else 12, anchor="start", force=True)
    lab.add(px, py - 18, "Jokihat", "l-searched", 16 if not phone else 14, force=True)
    if phone:
        lab.add(cx - 8, cy - 12, "Raiganj", "l-fac", 11.5, anchor="end", force=True)
    else:
        lab.add(cx + 16, cy + 5, "Raiganj (West Bengal)", "l-fac", 12.5, anchor="start", force=True)
    lab.add(px - rr * 0.7, py - rr * 0.72 - 8, "50 km: no record here shows dialysis", "l-ring", 11.5 if not phone else 10.5, force=True)
    label_places(v, lab, pos=False, district_size=12 if not phone else 10.5, skip={"UTTAR DINAJPUR"})
    pin = sym_pin(cx, cy, 1, sel=not phone, small=phone)
    svg = (f'<svg class="map" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{base}{g}{pin}{sym_place(px, py)}{lab.svg()}'
           f'{scale_bar(v, 16 if phone else 180, H - (14 if phone else 22))}</svg>')
    return svg, v


def frame_jokihat():
    ans = A["jok"]
    c = ans["beyond_radius"]
    ex = "jok"
    t = T["jok"]["summary"]
    svg, v = jok_map(MW, MH, "jok_local", reserved=[(MW - 190, 0, MW, 200), (0, MH - 200, 160, MH), (MW - 300, MH - 160, MW, MH), (0, 0, 600, 70)])
    st = state_of(c["lon"], c["lat"])
    over = f'''{map_controls()}
<div class="maptitle"><b>Dialysis near Jokihat, Araria district, Bihar</b><span>Nothing with evidence inside 50 km &middot; nearest is across the state border</span></div>
<div class="insetbox">{inset(v, label="Bihar and West Bengal")}</div>
{legend([("place", "Searched place (good to about 11 km)"), ("ring", "50 km: the usual distance for dialysis"), ("pin", "Nearest with evidence"), ("border", "State border")], "lg-map")}
<div class="hint">Scroll to zoom &middot; drag to move</div>'''
    m = f'<div class="mapbox">{svg}{over}</div>'
    ev = merged_evidence(c)
    q = X[ex]["questions"][c["name"]][:3]
    ci = X[ex]["call_info"][c["name"]]
    pl = X[ex]["public_list"][c["name"]]
    lst = f'''<div class="answer abst"><span class="eyebrow">No clear option nearby</span>
<h1 class="h-abst">No clear evidence of dialysis within 50 km of Jokihat in this dataset.</h1>
<p class="gap">This dataset is mostly private facilities. A public hospital nearby may offer dialysis and not appear here.</p></div>
<p class="placeline">Searching around <b>Jokihat post office area (PIN 854329), Araria district, Bihar</b>. Position good to about 11 km.</p>
<h2 class="lh">Nearest facility with some evidence, 76 km away</h2>
<ol class="rows"><li class="row open"><div class="rhead"><span class="num">1</span><div class="rname"><b>Raiganj Government Medical College and Hospital</b>
<span class="mut">public hospital &middot; Raiganj, {esc(st)} <em class="xstate">other state</em></span></div><div class="dist"><b>76 km</b><span>straight line</span></div></div>
<div class="conf c-low">{bars(1)}<b>Thin record evidence.</b> {esc(conf_reason(c, "dialysis"))}</div>
<blockquote><q>Specialized units: Critical Care Unit (CCU), Special New Born Care Unit (SNCU), Thalassemia Control Unit (TCU), <mark>Dialysis Unit</mark>, Blood Bank and ...</q><cite>from {FIELD[ev[0]["field"]]}</cite></blockquote>
<div class="outside"><b>Government list (outside this dataset)</b> {esc(pl["text"].replace("Government list ", "").replace(" Outside data, not from this record.", ""))}</div>
<div class="before"><div class="bhead"><b>Before you go, call</b><button class="call">{ICON_PHONE}<span>{esc(ci["phone"])}</span></button></div>
<ol class="ask">{"".join(f"<li>{esc(x['question'])}</li>" for x in q)}</ol></div></li></ol>
<div class="whynot"><h3>If we trusted claims alone</h3><p><b>{t["n_first_claim_only"]} of {t["n_points"]}</b> villages within 50 km would first reach a place with no evidence of dialysis in its record. If it cannot help, the trip is about <b>{t["extra_km_mean"]:.0f} km longer</b> on average (straight line).</p>
<button class="play sm">{ICON_PLAY}<span>Run the village simulation</span></button></div>'''
    return layout_laptop("jokihat", "4. Jokihat, dialysis: honest 'no clear option nearby' (laptop)", header("Dialysis", "Jokihat"), lst, m)


# ---------------------------------------------------------------- frame: Jaipur (district centre)

def frame_jaipur():
    ans, ex = A["jpr"], "jpr"
    loc = ans["location"]
    prec = loc["precision_km"]
    pts = [[loc["lon"], loc["lat"]]]
    for ang in range(0, 360, 30):
        dlat = prec / 111 * math.sin(math.radians(ang))
        dlon = prec / (111 * math.cos(math.radians(loc["lat"]))) * math.cos(math.radians(ang))
        pts.append([loc["lon"] + dlon, loc["lat"] + dlat])
    v = View.fit(pts, MW, MH, pad_px=30, min_km=50, top=60)
    base, n = base_layers(v, "jpr_district", dot_alpha=70, dot_color=(120, 138, 160), dot_r=1.0)
    px, py = v.p(loc["lon"], loc["lat"])
    pr = v.km_px(prec)
    g = f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{pr:.1f}" class="prec big"/>'
    # result pins sit within 5 km: draw one cluster callout instead of three overlapping pins
    cl = (f'<g class="cluster"><rect x="{px + 18:.1f}" y="{py - 46:.1f}" rx="13" width="214" height="26"/>'
          f'<text x="{px + 30:.1f}" y="{py - 28:.1f}">3 options in Jaipur city: zoom in</text></g>'
          + sym_pin(px - 6, py - 4, 1, small=True) + sym_pin(px + 2, py - 12, 2, small=True) + sym_pin(px + 9, py + 3, 3, small=True))
    lab = Labeler(MW, MH, reserved=[(MW - 190, 0, MW, 200), (0, MH - 200, 160, MH), (MW - 300, MH - 160, MW, MH), (0, 0, 560, 64),
                                    (px - 30, py - 50, px + 240, py + 50)])
    lab.add(px, py + 42, "Centre of Jaipur district", "l-searched", 15, force=True)
    lab.add(px, py - pr + 22, "The patient could be anywhere in this circle", "l-ring", 12, force=True)
    label_places(v, lab, pos=False, district_size=12.5, skip={"JAIPUR"})
    svg = f'<svg class="map" viewBox="0 0 {MW} {MH}" width="{MW}" height="{MH}">{base}{g}{cl}{lab.svg()}{scale_bar(v, 180, MH - 22)}</svg>'
    over = f'''{map_controls()}
<div class="maptitle"><b>Dialysis near Jaipur</b><span>Measured from the centre of Jaipur district, about 144 km across</span></div>
<div class="insetbox">{inset(v, label="Rajasthan")}</div>
{legend([("pin", "Recommended: its record shows dialysis"), ("place", "Centre of the district you typed"), ("ring", "Where the patient could be")], "lg-map")}'''
    m = f'<div class="mapbox">{svg}{over}</div>'
    lst = f'''<div class="warnplace"><b>You typed a district, so we measured from its centre.</b> The patient could be up to about 72 km from there. <a class="lnk">Type the patient's PIN or village</a> for real distances.</div>
<div class="answer"><span class="eyebrow">Best option with clear evidence</span>
<h1><span class="num big">1</span>Soni Hospital</h1>
<p class="ans-line"><b>3 km from the district centre</b> &middot; <span class="c-high">Strong record evidence</span> &middot; <b>Call before you go</b></p></div>
<h2 class="lh">3 options with evidence of dialysis in Jaipur city</h2>
<ol class="rows">{card_rows(ans, "dialysis", ex)}</ol>'''
    lst = lst.replace("<span>straight line</span>", "<span>from centre</span>")
    return layout_laptop("jaipur", "5. Jaipur, dialysis: district typed, honest about precision (laptop)", header("Dialysis", "Jaipur"), lst, m)


# ---------------------------------------------------------------- frame: landing

def frame_landing():
    W, H = MW, MH
    v = View.fit([[68.2, 6.8], [97.4, 35.6]], W, H, pad_px=20, min_km=50)
    base, n = base_layers(v, "india_landing", dot_alpha=80, dot_color=(150, 172, 200), dot_r=0.9)
    lab = Labeler(W, H, reserved=[(W - 190, 0, W, 60), (0, 0, 380, 60)])
    ex = [("Jaipur", "Dialysis near Jaipur", 75.79, 26.92), ("PIN 226001, Lucknow", "C-section", 80.944, 26.85),
          ("Jokihat", "Dialysis, rural Bihar", 87.648, 26.152), ("Nashik", "High-risk delivery", 73.79, 19.99),
          ("Kotputli", "Trauma on NH48", 76.2, 27.7)]
    marks = ""
    for nm, sub, lon, lat in ex:
        x, y = v.p(lon, lat)
        marks += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" class="exdot"/>'
        lab.boxes.append((x - 8, y - 8, x + 8, y + 8))
    for nm, sub, lon, lat in ex:
        x, y = v.p(lon, lat)
        anchor = "end" if nm in ("Jaipur", "Nashik", "Kotputli") else "start"
        dx = -12 if anchor == "end" else 12
        lab.add(x + dx, y + (12 if nm == "Jaipur" else (-4 if nm == "Kotputli" else 4)), f"{nm}: {sub}", "l-ex", 12.5, anchor=anchor, force=True)
    for name, lon, lat in state_label_pts(v):
        x, y = v.p(lon, lat)
        lab.add(x, y, name.upper(), "l-state", 10, upper=True, ls=1.2)
    svg = f'<svg class="map" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{base}{marks}{lab.svg()}</svg>'
    over = f'''<div class="maptitle"><b>{len(PO_LAT):,} post offices draw India</b><span>Tap an example place, or search for your own</span></div>{map_controls().replace(f'<button class="mc wide">{ICON_HOME}<span>Back to results</span></button>', "")}'''
    m = f'<div class="mapbox">{svg}{over}</div>'
    lst = '''<div class="intro"><h1 class="h-intro">Which hospital can actually help, and how do we know?</h1>
<p>Pick the care and the patient's place. We show up to 3 facilities, quote their own records, and say plainly when nothing reliable is near.</p>
<ol class="how3"><li><b>Pick the care</b> 12 kinds, or type your own</li><li><b>Type the place</b> PIN, village, town or district</li><li><b>Read the answer</b> nearest with evidence, what to ask on the phone</li></ol>
<h3 class="lh">Try an example</h3><ul class="exlist"><li>Dialysis near Jaipur</li><li>Emergency C-section at PIN 226001, Lucknow</li><li>Dialysis from Jokihat, rural Bihar</li><li>High-risk delivery in Nashik</li><li>Trauma care near Kotputli on NH48</li></ul></div>'''
    return layout_laptop("landing", "0. Landing: India is on screen from the first second (laptop)", header("Choose the care needed", "PIN, village, town or district"), lst, m)


# ---------------------------------------------------------------- phone frames

def phone_lucknow():
    ans, ex = A["lko"], "lko"
    loc = ans["location"]
    W, H = 390, 236
    pts = [[loc["lon"], loc["lat"]]] + [[c["lon"], c["lat"]] for c in ans["results"]]
    v = View.fit(pts, W, H, pad_px=26, min_km=9, top=8)
    base, n = base_layers(v, "lko_phone", dot_alpha=110, dot_color=(120, 138, 160), dot_r=1.2)
    lab = Labeler(W, H, reserved=[(W - 130, 0, W, 44), (0, H - 74, 70, H)])
    fac, placed, (px, py) = facility_layer(v, ans, "emoc", extra=ex, show_whynot=False, small=True)
    for (x, y) in placed:
        lab.boxes.append((x - 12, y - 12, x + 12, y + 12))
    lab.add(px + 14, py - 12, "PIN 226001", "l-searched", 13, anchor="start", force=True)
    label_places(v, lab, max_po=6, district_size=11, states=False, neighbours=False, skip={"LUCKNOW", "BALRAMPUR HOSPITAL"})
    svg = f'<svg class="map" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{base}{fac}{lab.svg()}{scale_bar(v, 90, H - 10)}</svg>'
    ins = inset(v, w=56, h=64)
    c1 = ans["results"][0]
    rows = card_rows(ans, "emoc", ex, open_first=False, phone=True)
    qs = X[ex]["questions"][c1["name"]][:2]
    ci = X[ex]["call_info"][c1["name"]]
    return f'''<section class="frame phone" id="p-lucknow"><div class="fcap">6. Phone: Lucknow PIN 226001 results, answer above the fold (390x844)</div><div class="screen pscreen">
<div class="pbar"><span class="logo"></span><div><b>Emergency C-section</b><span>near PIN 226001, Lucknow</span></div><a class="lnk">Change</a></div>
<div class="answer p"><span class="eyebrow">Best option with clear evidence</span>
<h1><span class="num big">1</span>BNK Hospital</h1>
<p class="ans-line"><b>3.5 km</b> straight line &middot; <span class="c-high">Strong record evidence</span></p>
<div class="pcall"><button class="call">{ICON_PHONE}<span>Call {esc(ci["phone"])}</span></button><span class="mut sm">Ask: {esc(qs[0]["question"])}</span></div></div>
<div class="pmap">{svg}<button class="mc wide primary pexp">{ICON_EXPAND}<span>Expand</span></button><div class="pinset">{ins}</div></div>
<h2 class="lh p">All 3 options within 50 km</h2><ol class="rows p">{rows}</ol></div></section>'''


def phone_jokihat():
    ans = A["jok"]
    c = ans["beyond_radius"]
    ex = "jok"
    svg, v = jok_map(390, 230, "jok_phone", phone=True, reserved=[(390 - 110, 0, 390, 40)])
    ci = X[ex]["call_info"][c["name"]]
    st = state_of(c["lon"], c["lat"])
    return f'''<section class="frame phone" id="p-jokihat"><div class="fcap">9. Phone: Jokihat, dialysis, no clear option nearby (390x844)</div><div class="screen pscreen">
<div class="pbar"><span class="logo"></span><div><b>Dialysis</b><span>near Jokihat, Araria, Bihar</span></div><a class="lnk">Change</a></div>
<div class="answer abst p"><span class="eyebrow">No clear option nearby</span>
<h1 class="h-abst">No clear evidence of dialysis within 50 km of Jokihat in this dataset.</h1>
<p class="near">Nearest with some evidence: <b>Raiganj Government Medical College</b>, <b>76 km</b>, in {esc(st)}.</p></div>
<div class="pmap">{svg}<button class="mc wide primary pexp">{ICON_EXPAND}<span>Expand</span></button></div>
<ol class="rows p"><li class="row open"><div class="rhead"><span class="num">1</span><div class="rname"><b>Raiganj Government Medical College and Hospital</b><span class="mut">public hospital &middot; {esc(st)}</span></div><div class="dist"><b>76 km</b><span>straight line</span></div></div>
<div class="conf c-low">{bars(1)}<b>Thin record evidence.</b> Only one sentence in its record names dialysis.</div>
<blockquote><q>... Thalassemia Control Unit (TCU), <mark>Dialysis Unit</mark>, Blood Bank and ...</q><cite>from its services list</cite></blockquote>
<div class="before"><div class="bhead"><b>Before you go, call</b><button class="call">{ICON_PHONE}<span>{esc(ci["phone"])}</span></button></div></div></li></ol>
<p class="gap p">This dataset is mostly private facilities. A public hospital nearby may offer dialysis and not appear here.</p></div></section>'''




# ================================================================ SIMULATOR (agent swarm + what-if)
# The swarm is computed by sim.py with the same rules the browser runs; nothing is drawn by hand.

import sim as SIM  # noqa: E402
from vf import travel as TRAVEL  # noqa: E402
from vf import services as SERVICES  # noqa: E402

ICON_PAUSE = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>'
ICON_CHECK = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M2.5 8.5l3.5 3.5 7.5-8" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
ICON_PLUS = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M8 2.5v11M2.5 8h11" stroke="currentColor" stroke-width="2"/></svg>'
ICON_X = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" stroke-width="2"/></svg>'
ICON_UNDO = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M5 3L2 6l3 3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M2.5 6H10a3.5 3.5 0 0 1 0 7H6" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>'
ICON_STAR = '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M8 1.8l1.9 4 4.3.5-3.2 3 .9 4.3L8 11.5l-3.9 2.1.9-4.3-3.2-3 4.3-.5z" fill="currentColor"/></svg>'


def sim_inputs(key, line):
    """Per-village state factor (the village's state, from the admin-1 borders), the world, the window."""
    t = T[key]
    pts = np.array(t["points"], float)
    names = [state_of(a, b) for a, b in pts]
    dom = max({n for n in names if n}, key=names.count)
    f = np.array([TRAVEL.state_factor(n or dom).value for n in names], float)
    w = SERVICES.WINDOWS[line]
    return pts, f, SIM.World.from_trip(t), float(w.minutes), float(w.allowance)


def display_name(n):
    """A record whose name is a single word (e.g. a city) is shown as such, not as if it were a hospital."""
    n = short_name(n)
    return f'A record named only "{n}"' if len(n.split()) == 1 else n


def hours(m):
    return "1 hour" if m == 60 else ("2 hours" if m == 120 else f"{m:.0f} minutes")


def swarm_layer(v, agents, T_now, world, policy, changed=frozenset()):
    """Traveled paths (faint), live heads, and arrival tallies, read from each agent's legs."""
    faint, heads, flashes = [], [], []
    arrived = {}
    for a in agents:
        pos = SIM.position(a, T_now)
        if pos is None:
            continue
        lon, lat, n = pos
        legs = a.legs if n is None else a.legs[:n + 1]
        x0, y0 = v.p(*a.legs[0][:2]) if a.legs else (None, None)
        # the road so far, very faint: flows emerge as agents travel, nothing is drawn ahead of them
        for k, (ax, ay, bx, by, t0, t1, j, kind) in enumerate(legs):
            if kind == "hold":
                continue
            ex, ey = (bx, by) if (n is None or k < n) else (lon, lat)
            sx, sy = v.p(ax, ay)
            tx, ty = v.p(ex, ey)
            faint.append(f'<path d="M{sx:.1f},{sy:.1f}L{tx:.1f},{ty:.1f}" class="tr-past"/>')
        # a short bright trail: where the agent was over the last 6 minutes
        if n is not None and a.legs[n][7] != "hold":
            trail = [SIM.position(a, max(T_now - d, a.depart)) for d in (6, 4, 2, 0)]
            trail = [p for p in trail if p is not None]
            if len(trail) > 1:
                j = a.legs[n][6]
                no_ev = not world.evidence[j] if j < len(world.evidence) else False
                if a in changed:
                    cls = "tr-whatif"
                elif policy == "claims" and a.stops_without_evidence:
                    cls = "tr-onward"
                elif policy == "claims" and no_ev:
                    cls = "tr-amber"
                else:
                    cls = "tr-teal"
                d = "M" + "L".join("{:.1f},{:.1f}".format(*v.p(p[0], p[1])) for p in trail)
                faint.append(f'<path d="{d}" class="{cls}"/>')
        hx, hy = v.p(lon, lat)
        if n is None:
            j = a.legs[-1][6]
            arrived[j] = arrived.get(j, 0) + 1
            continue
        kind, j = a.legs[n][7], a.legs[n][6]
        if kind == "hold":
            flashes.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="7" class="flash"/>')
            continue
        no_ev = not world.evidence[j]
        hc = "hd-amber" if (policy == "claims" and no_ev) else ("hd-whatif" if a in changed else "hd-teal")
        heads.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="2.6" class="{hc}"/>')
    return "".join(faint), "".join(flashes) + "".join(heads), arrived


def facility_marks(v, world, cards, policy, lab=None, show_claims=True, used=None, only_whatif=False):
    out = []
    for i in range(len(world.lon)):
        if only_whatif and not world.whatif[i]:
            continue
        x, y = v.p(world.lon[i], world.lat[i])
        if not (-20 < x < v.w + 20 and -20 < y < v.h + 20):
            continue
        tag = world.whatif[i]
        if tag == "added":
            out.append(f'<g class="wi"><circle cx="{x:.1f}" cy="{y:.1f}" r="13" class="wi-ring"/>'
                       f'<path d="M{x - 6:.1f},{y:.1f}H{x + 6:.1f}M{x:.1f},{y - 6:.1f}V{y + 6:.1f}" class="wi-plus"/></g>')
        elif tag == "verified":
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" class="wi-ring"/><circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" class="ev-dot"/>')
        elif tag == "closed":
            out.append(f'<path d="M{x - 6:.1f},{y - 6:.1f}L{x + 6:.1f},{y + 6:.1f}M{x + 6:.1f},{y - 6:.1f}L{x - 6:.1f},{y + 6:.1f}" class="wi-x"/>')
        elif world.evidence[i]:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="ev-dot"/>')
        elif show_claims and (used is None or i in used):
            out.append(sym_claim(x, y, 5.5))
    return "".join(out)


def counter_tiles_num(before, after, window_min, svc_short):
    """Same table, with numeric deltas computed here so the arrows are exact."""
    n = before["n"]

    def cell(b, a, up_is_good, unit=""):
        d = a - b
        if abs(d) < 0.5:
            return f'<td class="wi-col">{a:.0f}{unit} <em class="eq">no change</em></td>'
        good = (d > 0) == up_is_good
        return f'<td class="wi-col">{a:.0f}{unit} <em class="{"up" if good else "dn"}">{"+" if d > 0 else "&minus;"}{abs(d):.0f}</em></td>'

    rows = [(f"Villages within {hours(window_min)} of a place whose record shows {svc_short}", before["within"], after and after["within"], True, f" of {n}" if False else ""),
            ("Average estimated drive to the nearest such place, minutes", before["avg_min"], after and after["avg_min"], False, ""),
            (f"Villages whose nearest place listing {svc_short} has no evidence of it", before["first_no_evidence"], after and after["first_no_evidence"], False, "")]
    head = ('<tr><td></td><td class="h">Now</td><td class="h wi-col">What-if</td></tr>' if after else '<tr><td></td><td class="h">Now</td></tr>')
    body = ""
    for label, b, a, up, _ in rows:
        body += f'<tr><th>{label}</th><td class="now">{b:.0f}</td>' + (cell(b, a, up) if after else "") + "</tr>"
    return f'<table class="counters">{head}{body}</table><p class="ctot">Out of {n} villages (post office areas) within 50 km.</p>'


def sim_controls(policy, t_now, t_total, playing=True, phone=False):
    seg = (f'<div class="seg"><button class="s1{" on" if policy == "evidence" else ""}"><i>1</i>Trust the record\'s evidence</button>'
           f'<button class="s2{" on" if policy == "claims" else ""}"><i>2</i>Trust specialty claims alone</button></div>')
    pct = min(100, 100 * t_now / t_total)
    play = (f'<div class="playrow"><button class="play sm">{ICON_PAUSE if playing else ICON_PLAY}<span>{"Pause" if playing else "Play"}</span></button>'
            f'<button class="mc sq">{ICON_REPLAY}</button><div class="prog wide"><i style="width:{pct:.0f}%"></i></div>'
            f'<span class="clock">minute {t_now:.0f}</span><span class="speed">1 s = 5 min</span></div>')
    return seg, play


RULES = {
    "evidence": "Each patient heads for the place with the shortest estimated drive whose own record shows {svc}. A place with stronger evidence wins if it is at most {allow:.0f} minutes further.",
    "claims": "Each patient heads for the nearest place that lists {svc}, whatever its record shows. If that place shows no evidence of {svc}, the patient goes on to the nearest place whose record shows it: the cost if that place cannot help.",
}


def sim_legend(policy, whatif=False):
    items = [("village", "A patient from a village (post office area)")]
    if policy == "claims":
        items += [("leg1", "Heading to a place with no evidence in its record"), ("claim", "Lists it, no evidence of it in its record"),
                  ("leg2", "Onward, if that place cannot help"), ("evdot", "Record shows it")]
    else:
        items += [("go", "Heading to a place whose record shows it"), ("evdot", "Record shows it"), ("claim", "Lists it, no evidence (skipped)")]
    if whatif:
        items += [("wi", "What-if change (yours, not data)"), ("wihead", "On the road when you made the change, so it turned")]
    return items


def wi_badge(txt="What-if"):
    return f'<span class="wib">{txt}</span>'


def sim_map_common(v, key, policy, agents, T_now, world, cards, changed=frozenset(), show_claims=True, used=None,
                   searched_label=None, reserved=(), extra_labels=()):
    t = T[key]
    loc = A[key]["location"]
    W, H = v.w, v.h
    base, n = base_layers(v, f"sim_{key}_{W}", dot_alpha=45, dot_color=(120, 138, 160), dot_r=1.0)
    px, py = v.p(loc["lon"], loc["lat"])
    ring = f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{v.km_px(t["radius_km"]):.1f}" class="ring"/>'
    faint, heads, arrived = swarm_layer(v, agents, T_now, world, policy, changed)
    fm = facility_marks(v, world, cards, policy, show_claims=show_claims, used=used)
    pins = ""
    lab = Labeler(W, H, reserved=list(reserved))
    for i, c in enumerate(cards, 1):
        x, y = v.p(c["lon"], c["lat"])
        pins += sym_pin(x, y, i, small=True)
        lab.boxes.append((x - 12, y - 12, x + 12, y + 12))
    # arrival tallies at the busiest places
    tl = ""
    for j, nn in sorted(arrived.items(), key=lambda z: -z[1])[:4]:
        if nn < 8:
            continue
        x, y = v.p(world.lon[j], world.lat[j])
        cls = "l-tally" if (policy == "claims" and not world.evidence[j]) else "l-tally ok"
        word = "stopped here" if (policy == "claims" and not world.evidence[j]) else "arrived"
        lab.add(x + 10, y - 9, f"{nn} {word}", cls, 11.5, anchor="start")
    r50 = v.km_px(t["radius_km"])
    lab.add(px + r50 * 0.71 + 4, py - r50 * 0.71 - 4, f"{t['radius_km']:.0f} km: villages taking part", "l-ring", 11.5, anchor="start")
    for (txt, lon, lat, cls, size, anchor, dx, dy) in extra_labels:
        x, y = v.p(lon, lat)
        lab.add(x + dx, y + dy, txt, cls, size, anchor=anchor, force=True)
    lab.add(px, py + 30, searched_label, "l-searched", 15, force=True)
    lab.boxes.append((px - 14, py - 14, px + 14, py + 14))
    label_places(v, lab, pos=True, max_po=8, district_size=12.5, skip={"LUCKNOW", "JAIPUR"})
    svg = (f'<svg class="map" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{base}{ring}<g class="swarm">{faint}</g>{fm}'
           f'<g class="swarm">{heads}</g>{facility_marks(v, world, cards, policy, show_claims=False, only_whatif=True)}{pins}{sym_place(px, py)}{lab.svg()}</svg>')
    return svg


def sim_panel(key, line, svc_short, policy, base_c, after_c, edits, priority=None, priority_note=None, window_min=60,
              allowance=15, takeaway=None, phone=False):
    tools = (f'<div class="tools"><button class="tool">{ICON_CHECK}<span>Verify a record</span></button>'
             f'<button class="tool">{ICON_PLUS}<span>Add a facility</span></button>'
             f'<button class="tool">{ICON_X}<span>Close a facility</span></button></div>')
    ed = ""
    if edits:
        ed = '<ul class="edits">' + "".join(
            f'<li>{wi_badge()}<span>{e}</span><button class="undo">{ICON_UNDO}Undo</button></li>' for e in edits) + \
            '</ul><button class="lnk reset">Reset: back to the real data</button>'
    else:
        ed = '<p class="mut sm nomargin">No changes yet. Tap a place on the map to verify or close it, or tap empty ground to add one.</p>'
    pr = ""
    if priority is not None:
        pr = f'''<div class="prio"><div class="prio-h">{ICON_STAR}<b>Which record should be verified first?</b></div>{priority}
<p class="mut xs">A checking order for Virtue Foundation, not a claim that any of these places offers {svc_short}.</p></div>'''
    else:
        pr = f'<button class="prio-btn">{ICON_STAR}<span>Which record should be verified first?</span></button>'
    return f'''<div class="simpanel">
<div class="sp-h"><h2><span class="eyebrow">Village simulation.</span> What if a record were checked, or a facility opened?</h2></div>
<div class="sp-sec"><b class="sp-k">Your changes</b> {wi_badge("What-if, not real data")}{tools}{ed}</div>
<div class="sp-sec">{counter_tiles_num(base_c, after_c, window_min, svc_short)}</div>
{pr}
<div class="sp-sec assume"><label class="tog"><i></i><span><b>Limit how many patients each place takes</b><br><span class="mut xs">Assumption, not from data. Off.</span></span></label></div>
<p class="mut xs foot2"><a class="lnk xs">Back to the answer</a> &middot; Straight-line paths; times from the drive-time estimate (state factor x 2.30 x km<sup>0.842</sup>), free-flow: real trips take longer. Same seed, same run, every time.</p>
</div>'''


def map_overlay_sim(policy, svc, allowance, t_now, t_total, caption, legend_items, takeaway, inset_html, phone=False):
    seg, play = sim_controls(policy, t_now, t_total)
    rule = RULES[policy].format(svc=svc, allow=allowance)
    return f'''{map_controls()}
<div class="simbar"><div class="sb-top"><b>Where would patients from nearby villages go?</b>{play}</div>{seg}
<p class="cap"><b>Rule {1 if policy == "evidence" else 2}:</b> {rule}</p>
{f'<p class="cap cap2">{caption}</p>' if caption else ''}</div>
{legend(legend_items, "lg-sim")}
<div class="insetbox">{inset_html}</div>
<div class="takeaway t2">{takeaway}</div>'''


# ---------------------------------------------------------------- frame: Lucknow swarm, rule 2, mid-run

def frame_sim_lucknow():
    key, line, svc = "lko", "emoc", "C-section"
    pts, f, world, Wm, allow = sim_inputs(key, line)
    ans = A[key]
    loc = ans["location"]
    T_now = 26
    agents = SIM.run(pts, f, world, "claims", T_now, agents_per_village=3, seed=7)
    v = View(loc["lon"], loc["lat"], 118, MW, MH)
    v.cy -= 30 / v.k
    base_c = SIM.counters(pts, f, world, Wm)
    co = SIM.claims_outcomes(pts, f, world)
    rer = [e for h, e in co if h > 0]
    s = T[key]["summary"]
    svg = sim_map_common(v, key, "claims", agents, T_now, world, ans["results"], used=None,
                         searched_label="PIN 226001, Lucknow",
                         reserved=[(0, 0, 680, 200), (0, MH - 230, 170, MH), (MW - 330, MH - 230, MW, MH), (0, MH - 80, MW, MH), (MW - 150, 0, MW, 250)])
    take = (f'<b>{s["n_first_claim_only"]} of {s["n_points"]}</b> villages would first reach a place with <b>no evidence of C-section</b> in its record. '
            f'If that place cannot help, the trip is about <b>{np.mean(rer):.0f} minutes longer</b> on average (estimated drive).')
    over = map_overlay_sim("claims", svc, allow, T_now, 75,
                           "Each moving dot is a patient from a village (post office area) within 50 km. No route is drawn in advance: each dot picks its target by the rule, and picks again if the map changes.",
                           sim_legend("claims"), take, inset(v, label="Uttar Pradesh"))
    m = f'<div class="mapbox">{svg}{over}</div>'
    lst = sim_panel(key, line, svc, "claims", base_c, None, [], priority=None, window_min=Wm, allowance=allow)
    return layout_laptop("sim-lucknow", "2. Lucknow, emergency C-section: the village simulation, rule 2 running (minute 26). Paths emerge from agents, none are pre-drawn (laptop)",
                         header("Emergency C-section and high-risk delivery", "226001"), lst, m)


# ---------------------------------------------------------------- frame: Jaipur, verify-first result applied

def frame_sim_jaipur():
    key, line, svc = "jpr", "dialysis", "dialysis"
    pts, f, world, Wm, allow = sim_inputs(key, line)
    ans = A[key]
    loc = ans["location"]
    base_c, top, ncand = SIM.verify_priority(pts, f, world, Wm, top=3)
    best = top[0]
    w2 = world.verify(best["i"])
    T_ev, T_now = 9, 24
    agents = SIM.run(pts, f, world, "evidence", T_now, agents_per_village=3, seed=7, events=[(T_ev, w2)])
    # which agents changed course: their target before and after the event differ
    changed = set()
    for a in agents:
        if any(k == "cut" for *_, k in a.legs):
            js = [l[6] for l in a.legs if l[7] != "hold"]
            if len(set(js)) > 1:
                changed.add(a)
    after_c = SIM.counters(pts, f, w2, Wm)
    v = View(loc["lon"], loc["lat"], 112, MW, MH)
    v.cy -= 30 / v.k
    bx = world.lon[best["i"]]; by = world.lat[best["i"]]
    nm = short_name(best["name"])
    svg = sim_map_common(v, key, "evidence", agents, T_now, w2, ans["results"], changed=frozenset(changed), show_claims=True,
                         searched_label="Centre of Jaipur district",
                         reserved=[(0, 0, 680, 200), (0, MH - 230, 170, MH), (MW - 330, MH - 230, MW, MH), (0, MH - 80, MW, MH), (MW - 150, 0, MW, 250)],
                         extra_labels=[(f"WHAT-IF: {nm} verified", bx, by, "l-wi", 12, "start", 16, 4)])
    rows = ""
    for k, r in enumerate(top, 1):
        a_ = r["after"]
        rows += (f'<li class="{"picked" if k == 1 else ""}"><span class="rk">{k}</span><div><b>{esc(display_name(r["name"]))}</b>'
                 f'<span>{r["gain_within"]:+d} village{"" if abs(r["gain_within"]) == 1 else "s"} within 1 hour ({base_c["within"]} to {a_["within"]}) &middot; '
                 f'first stop without evidence {base_c["first_no_evidence"]} to {a_["first_no_evidence"]}</span></div>'
                 f'{"<em>applied</em>" if k == 1 else "<button class=try>Try</button>"}</li>')
    prio = f'<p class="xs mut">Tried each of the {ncand} nearby places that list dialysis without evidence. Most helpful first:</p><ol class="prio-l">{rows}</ol>'
    take = (f'<span class="wib">What-if</span> If <b>{esc(nm)}</b>\'s record were verified for dialysis, <b>{base_c["first_no_evidence"] - after_c["first_no_evidence"]}</b> more villages would find evidence at their first stop, '
            f'and <b>{after_c["within"] - base_c["within"]}</b> more would be within 1 hour of a place whose record shows it ({base_c["within"]} to {after_c["within"]} of {base_c["n"]}).')
    over = map_overlay_sim("evidence", svc, allow, T_now, 60,
                           None,
                           sim_legend("evidence", whatif=True), take, inset(v, label="Rajasthan"))
    m = f'<div class="mapbox">{svg}{over}</div>'
    lst = sim_panel(key, line, svc, "evidence", base_c, after_c,
                    [f"Verified {esc(nm)} for dialysis"], priority=prio, window_min=Wm, allowance=allow)
    return layout_laptop("sim-jaipur", "3. Jaipur, dialysis: 'Which record should be verified first?' result, applied as a what-if; agents re-decide en route (laptop)",
                         header("Dialysis", "Jaipur"), lst, m), (base_c, top, after_c, ncand, len(changed))


# ---------------------------------------------------------------- frame: Jokihat, add a facility

def frame_sim_jokihat():
    key, line, svc = "jok", "dialysis", "dialysis"
    pts, f, world, Wm, allow = sim_inputs(key, line)
    ans = A[key]
    loc = ans["location"]
    base_c, top, ncand = SIM.verify_priority(pts, f, world, Wm, top=3)
    ar = DISTRICTS[DISTRICTS.name.str.upper() == "ARARIA"].iloc[0]
    w2 = world.add(float(ar.lon), float(ar.lat), "New dialysis unit (what-if)")
    after_c = SIM.counters(pts, f, w2, Wm)
    T_ev, T_now = 14, 40
    agents = SIM.run(pts, f, world, "evidence", T_now, agents_per_village=3, seed=7, events=[(T_ev, w2)])
    changed = {a for a in agents if any(l[7] == "cut" for l in a.legs) and len({l[6] for l in a.legs}) > 1}
    c = ans["beyond_radius"]
    pts_fit = [[loc["lon"], loc["lat"]], [c["lon"], c["lat"]], [float(ar.lon), float(ar.lat)]]
    for ang in range(0, 360, 30):
        pts_fit.append([loc["lon"] + 50 / (111 * math.cos(math.radians(loc["lat"]))) * math.cos(math.radians(ang)),
                        loc["lat"] + 50 / 111 * math.sin(math.radians(ang))])
    v = View.fit(pts_fit, MW, MH, pad_px=16, min_km=50, top=200, bottom=110, right=350, left=150)
    svg = sim_map_common(v, key, "evidence", agents, T_now, w2, [c], changed=frozenset(changed),
                         searched_label="Jokihat",
                         reserved=[(0, 0, 680, 200), (0, MH - 230, 170, MH), (MW - 330, MH - 230, MW, MH), (0, MH - 80, MW, MH), (MW - 150, 0, MW, 250)],
                         extra_labels=[("WHAT-IF: new dialysis unit near Araria", float(ar.lon), float(ar.lat), "l-wi", 12, "middle", 0, -20),
                                       ("Raiganj (West Bengal)", c["lon"], c["lat"], "l-fac", 12, "end", -16, 4)])
    # the verify-first answer for Jokihat: nothing near enough to verify
    near_claim_km = min(float(SIM.hav(loc["lat"], loc["lon"], world.lat[i], world.lon[i])) for i in np.flatnonzero(~world.evidence))
    best = top[0]
    prio = (f'<p class="xs">We tried verifying each of the {ncand} places that list dialysis without evidence. '
            f'<b>None would bring a single village within 1 hour</b>: the nearest is {near_claim_km:.0f} km from Jokihat. '
            f'Checking records will not close this gap. Only a new or newly found facility would.</p>')
    take = (f'<span class="wib">What-if</span> A dialysis unit near Araria would put <b>{after_c["within"]} of {after_c["n"]}</b> villages within 1 hour of dialysis '
            f'(now {base_c["within"]}). The average estimated drive would fall from <b>{base_c["avg_min"]:.0f}</b> to <b>{after_c["avg_min"]:.0f} minutes</b>.')
    over = map_overlay_sim("evidence", svc, allow, T_now, 100,
                           None,
                           sim_legend("evidence", whatif=True), take, inset(v, label="Bihar and West Bengal"))
    m = f'<div class="mapbox">{svg}{over}</div>'
    lst = sim_panel(key, line, svc, "evidence", base_c, after_c,
                    ["Added a dialysis unit near Araria (map click)"], priority=prio, window_min=Wm, allowance=allow)
    return layout_laptop("sim-jokihat", "4. Jokihat, dialysis: verifying records cannot help here, so the user adds a facility (what-if); a care gap, not a data gap (laptop)",
                         header("Dialysis", "Jokihat"), lst, m), (base_c, after_c, near_claim_km)


# ---------------------------------------------------------------- phone: swarm + what-if sheet

def phone_sim():
    key, line, svc = "jpr", "dialysis", "dialysis"
    pts, f, world, Wm, allow = sim_inputs(key, line)
    ans = A[key]
    loc = ans["location"]
    base_c, top, ncand = SIM.verify_priority(pts, f, world, Wm, top=1)
    best = top[0]
    w2 = world.verify(best["i"])
    after_c = SIM.counters(pts, f, w2, Wm)
    T_now = 24
    agents = SIM.run(pts, f, world, "evidence", T_now, agents_per_village=2, seed=7, events=[(9, w2)])
    changed = {a for a in agents if any(l[7] == "cut" for l in a.legs) and len({l[6] for l in a.legs}) > 1}
    W, H = 390, 844
    v = View(loc["lon"], loc["lat"], 100, W, H)
    v.cy += (H * 0.2) / v.k
    nm = short_name(best["name"])
    svg = sim_map_common(v, key, "evidence", agents, T_now, w2, ans["results"], changed=frozenset(changed),
                         searched_label="Jaipur district centre", reserved=[(0, 0, W, 120), (0, H - 470, W, H)],
                         extra_labels=[("WHAT-IF: verified", float(world.lon[best["i"]]), float(world.lat[best["i"]]), "l-wi", 11, "start", 14, 4)])
    seg, play = sim_controls("evidence", T_now, 60)
    return f'''<section class="frame phone" id="p-sim"><div class="fcap">9. Phone: expanded map, village simulation with a what-if applied (390x844)</div><div class="screen pscreen mapfull">{svg}
<div class="ptop"><button class="mc wide">{ICON_CLOSE}<span>Close map</span></button><div class="zoom"><button class="mc">+</button><button class="mc">&minus;</button></div></div>
<div class="pcap">Each dot is a patient from a village. It goes where the rule says.</div><div class="pinset" style="top:104px;bottom:auto;left:12px">{inset(v, w=56, h=64)}</div>
<div class="sheet"><div class="grab"></div>{seg}<p class="cap xs" style="margin:0 0 4px"><b>Rule 1:</b> each patient heads for the shortest estimated drive to a place whose record shows dialysis.</p>{play}
<p class="take"><span class="wib">What-if</span> Verifying <b>{esc(nm)}</b>: villages within 1 hour of dialysis evidence <b>{base_c["within"]} &rarr; {after_c["within"]}</b> of {base_c["n"]}; first stop without evidence <b>{base_c["first_no_evidence"]} &rarr; {after_c["first_no_evidence"]}</b>.</p>
<ul class="edits p"><li><span class="wib">What-if</span><span>Verified {esc(nm)}</span><button class="undo">{ICON_UNDO}Undo</button></li></ul>
<div class="tools p"><button class="tool">{ICON_CHECK}<span>Verify</span></button><button class="tool">{ICON_PLUS}<span>Add</span></button><button class="tool">{ICON_X}<span>Close</span></button><button class="tool star">{ICON_STAR}<span>Verify first?</span></button></div>
<p class="mut xs" style="color:#8fa0b2;margin:6px 0 0">Straight-line paths; times from the drive-time estimate, free-flow. <a class="lnk" style="font-size:11px;color:var(--teal)">Reset to real data</a></p></div></div></section>'''


# ================================================================ assemble

def main():
    sim_j, JPR_NUM = frame_sim_jaipur()
    sim_k, JOK_NUM = frame_sim_jokihat()
    frames = [frame_landing(), frame_lucknow(), frame_sim_lucknow(), sim_j, sim_k, frame_expanded(), frame_jokihat(), frame_jaipur(),
              phone_lucknow(), phone_sim(), phone_jokihat()]
    print("jaipur verify-first:", JPR_NUM[0], [(r["name"], r["gain_within"], r["gain_first"]) for r in JPR_NUM[1]], JPR_NUM[2], "candidates", JPR_NUM[3], "changed course", JPR_NUM[4])
    print("jokihat add:", JOK_NUM)
    css = (HERE / "style.css").read_text()
    page = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design: map story</title><style>{css}</style></head>
<body><div class="index-head"><h1>Direction: map story</h1><p>Static mockup. Every number, name and quote is copied from the live API on port 8812
(/api/answer, /api/trip) for the same queries, or from src/vf/why_not.py, questions.py and public_list.py run on those queries
(they are not wired into /api/answer yet). Open a frame alone with #landing, #lucknow, #sim-lucknow, #sim-jaipur, #sim-jokihat, #expanded, #jokihat, #jaipur, #p-lucknow, #p-sim, #p-jokihat. Simulator counters are computed by sim.py (the browser's rules, in Python) from the /api/trip payload: real coordinates and evidence status, the D048 drive-time model and the service windows in src/vf/services.py.</p></div>
{"".join(frames)}
</body></html>'''
    (HERE / "index.html").write_text(page)
    print("wrote", HERE / "index.html", f"{len(page) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
