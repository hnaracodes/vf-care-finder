"""Static map renders for the compare mockup. Every mark comes from the bundle or the live API JSON.

Layers: India outline (vf.locate, Natural Earth admin-0 India POV), internal state borders (Natural
Earth admin-1), post office points (dbapp/bundle/post_offices.parquet), town labels (India Post head
offices from the raw PIN directory), district labels (dbapp/bundle/places.parquet), and the answer.
"""
import json, math, sys, html
from pathlib import Path
import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path("/Users/hrudaynara/Projects/CMU Datathon")
S = Path(__file__).parent
OUT = ROOT / "app/design/compare_side_by_side/assets"
sys.path.insert(0, str(ROOT / "src"))
from vf import io  # noqa

GJ = json.loads((OUT / "india_states.geojson").read_text())
OUTLINE = [np.array(p[0]) for p in GJ["features"][0]["geometry"]["coordinates"]]
BORDERS = [np.array(l) for l in GJ["features"][1]["geometry"]["coordinates"]]
STATE_LABELS = [(f["properties"]["name"], *f["geometry"]["coordinates"]) for f in GJ["features"][2:]]

po = pd.read_parquet(ROOT / "dbapp/bundle/post_offices.parquet").dropna(subset=["lat", "lon"])
PO = po.drop_duplicates(subset=["lat", "lon"])[["lon", "lat"]].to_numpy(float)
places = pd.read_parquet(ROOT / "dbapp/bundle/places.parquet")
DIST = places[places.kind == "district"][["name", "state", "lon", "lat"]]
raw = io.read_pincodes_raw()
ho = raw[raw.officetype == "HO"].copy()
ho["lat"] = pd.to_numeric(ho.latitude, errors="coerce"); ho["lon"] = pd.to_numeric(ho.longitude, errors="coerce")
ho = ho.dropna(subset=["lat", "lon"])
ho["name"] = ho.officename.str.replace(r"\s*H\.?O\.?$", "", regex=True).str.strip()
ho = ho[(ho.lat > 6) & (ho.lat < 37.5) & (ho.lon > 68) & (ho.lon < 98)]
TOWNS = ho[["name", "district", "lon", "lat"]].drop_duplicates("name")
SO = raw[raw.officetype == "PO"].copy()
SO["lat"] = pd.to_numeric(SO.latitude, errors="coerce"); SO["lon"] = pd.to_numeric(SO.longitude, errors="coerce")
SO = SO.dropna(subset=["lat", "lon"])
SO["name"] = SO.officename.str.replace(r"\s*S\.?O\.?$", "", regex=True).str.strip()
SO = SO[["name", "district", "lon", "lat"]].drop_duplicates("name")

INK, MUTE, PAPER = "#1d2a2e", "#6b767a", "#f4f1ea"
TEAL, AMBER, GREY = "#0f6b5c", "#b86a00", "#8a8f92"


def merc(lon, lat):
    lon = np.asarray(lon, float); lat = np.asarray(lat, float)
    return np.radians(lon), np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


class View:
    def __init__(self, lon0, lat0, width_km, W, H):
        self.W, self.H = W, H
        self.cx, self.cy = merc(lon0, lat0)
        self.k = W / (width_km / (6371.0 * math.cos(math.radians(lat0))))  # px per merc unit
        self.km_px = W / width_km

    def xy(self, lon, lat):
        x, y = merc(lon, lat)
        return self.W / 2 + (x - self.cx) * self.k, self.H / 2 - (y - self.cy) * self.k

    def inside(self, x, y, pad=0):
        return (x > -pad) & (x < self.W + pad) & (y > -pad) & (y < self.H + pad)

    def lonlat_box(self):
        xs = np.array([0, self.W]); ys = np.array([0, self.H])
        mx = (xs - self.W / 2) / self.k + self.cx
        my = -(ys - self.H / 2) / self.k + self.cy
        return np.degrees(mx), np.degrees(2 * np.arctan(np.exp(my)) - np.pi / 2)


def path_d(v, pts, close=False):
    x, y = v.xy(pts[:, 0], pts[:, 1])
    s = "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y))
    return s + ("Z" if close else "")


def esc(s):
    return html.escape(str(s), quote=True)


class Labeler:
    def __init__(self, W, H):
        self.boxes = []; self.W = W; self.H = H

    def place(self, x, y, text, size, anchor="middle", dy=0, pad=3):
        w = len(text) * size * 0.56; h = size * 1.1
        x0 = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        b = (x0 - pad, y + dy - h - pad / 2, x0 + w + pad, y + dy + pad)
        if b[0] < 2 or b[2] > self.W - 2 or b[1] < 2 or b[3] > self.H - 2:
            return False
        for c in self.boxes:
            if not (b[2] < c[0] or b[0] > c[2] or b[3] < c[1] or b[1] > c[3]):
                return False
        self.boxes.append(b); return True

    def block(self, x0, y0, x1, y1):
        self.boxes.append((x0, y0, x1, y1))


def base_layers(v, *, dots=True, towns=True, districts=True, states_text=True, dot_r=1.3, scale=1.0,
                town_limit=10, so_limit=0, lab=None, out_of_india=True):
    parts = []
    # sea / neighbours tint, then India land
    parts.append(f'<rect width="{v.W}" height="{v.H}" fill="#dfe3e1"/>')
    d = " ".join(path_d(v, r, True) for r in OUTLINE)
    parts.append(f'<path d="{d}" fill="{PAPER}" stroke="none" fill-rule="evenodd"/>')
    if dots:
        x, y = v.xy(PO[:, 0], PO[:, 1]); m = v.inside(x, y, 4)
        parts.append('<g fill="#a89c86">' + "".join(
            f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{dot_r}"/>' for a, b in zip(x[m], y[m])) + "</g>")
    bd = " ".join(path_d(v, L) for L in BORDERS)
    parts.append(f'<path d="{bd}" fill="none" stroke="#7d756a" stroke-width="{1.6*scale:.2f}" stroke-dasharray="{7*scale:.1f} {4*scale:.1f}" stroke-linejoin="round"/>')
    parts.append(f'<path d="{d}" fill="none" stroke="#4d5a5e" stroke-width="{1.8*scale:.2f}" fill-rule="evenodd"/>')
    return parts


def text(x, y, s, size, *, weight=500, fill=INK, anchor="middle", italic=False, spacing=0, halo=PAPER, hw=4):
    st = ' font-style="italic"' if italic else ""
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{st}{ls} stroke="{halo}" stroke-width="{hw}" paint-order="stroke" '
            f'stroke-linejoin="round">{esc(s)}</text>')


def label_layers(v, lab, *, scale=1.0, town_limit=10, district_limit=8, so_limit=0, state_text=True,
                 exclude=(), center=None):
    parts = []
    lo, la = v.lonlat_box()
    def within(df):
        return df[(df.lon > lo[0]) & (df.lon < lo[1]) & (df.lat > la[1]) & (df.lat < la[0])]
    ex = {e.lower() for e in exclude}
    if center is not None:
        cx, cy = center
    else:
        cx, cy = v.W / 2, v.H / 2
    # towns: nearest to centre first (head offices), then sub offices
    for df, lim, size, wt in [(within(TOWNS), town_limit, 15 * scale, 600), (within(SO), so_limit, 12 * scale, 500)]:
        if not len(df) or not lim: continue
        x, y = v.xy(df.lon.values, df.lat.values)
        order = np.argsort(np.hypot(x - cx, y - cy))
        n = 0
        for i in order:
            name = df.name.values[i]
            if name.lower() in ex or len(name) > 18: continue
            if lab.place(x[i], y[i], name, size, dy=-5 * scale):
                parts.append(f'<circle cx="{x[i]:.1f}" cy="{y[i]:.1f}" r="{2.6*scale:.1f}" fill="{INK}" stroke="{PAPER}" stroke-width="{1.2*scale:.1f}"/>')
                parts.append(text(x[i], y[i] - 5 * scale, name, size, weight=wt, hw=4 * scale))
                ex.add(name.lower()); n += 1
                if n >= lim: break
    df = within(DIST)
    if len(df) and district_limit:
        x, y = v.xy(df.lon.values, df.lat.values)
        order = np.argsort(np.hypot(x - cx, y - cy))
        n = 0
        for i in order:
            s = df.name.values[i].title() + " district"
            if lab.place(x[i], y[i] + 16 * scale, s.upper(), 11 * scale):
                parts.append(text(x[i], y[i] + 16 * scale, s.upper(), 11 * scale, weight=600, fill=MUTE, spacing=1.2 * scale, italic=False, hw=3 * scale))
                n += 1
                if n >= district_limit: break
    if state_text:
        for name, lon, lat in STATE_LABELS:
            x, y = v.xy(lon, lat)
            if v.inside(x, y, -20) and lab.place(x, y, name.upper(), 15 * scale):
                parts.append(text(x, y, name.upper(), 15 * scale, weight=700, fill="#9a8f7e", spacing=3 * scale, hw=0))
    return parts


def state_side_labels(v, items, scale=1.0):
    """Explicit state names placed on each side of a border, e.g. BIHAR / WEST BENGAL."""
    return [text(*v.xy(lon, lat), name, 14 * scale, weight=700, fill="#9a8f7e", spacing=3 * scale, hw=0) for name, lon, lat in items]


def ring(v, lon, lat, km, label=None, scale=1.0, color="#4d5a5e", dash=True):
    x, y = v.xy(lon, lat); r = km * v.km_px
    da = f' stroke-dasharray="{3*scale:.0f} {5*scale:.0f}"' if dash else ""
    s = [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="{1.4*scale:.1f}"{da}/>']
    if label:
        s.append(text(x, y - r - 6 * scale, label, 12.5 * scale, weight=600, fill=color, hw=3 * scale))
    return s


def searched(v, lon, lat, name, scale=1.0, lab=None, sub=None, precision_km=None):
    x, y = v.xy(lon, lat)
    s = []
    if precision_km and 14 * scale < precision_km * v.km_px < 0.6 * v.W:
        r = min(precision_km * v.km_px, max(v.W, v.H))
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="#2a5db0" fill-opacity="0.07" stroke="#2a5db0" stroke-opacity="0.35" stroke-width="{1*scale:.1f}"/>')
    s += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{11*scale:.1f}" fill="#2a5db0" fill-opacity="0.18"/>',
          f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5.5*scale:.1f}" fill="#2a5db0" stroke="#fff" stroke-width="{2.2*scale:.1f}"/>']
    s.append(text(x + 12 * scale, y - 9 * scale, name, 19 * scale, weight=800, anchor="start", hw=5 * scale))
    if sub:
        s.append(text(x + 12 * scale, y + 9 * scale, sub, 12 * scale, weight=600, fill="#2a5db0", anchor="start", hw=4 * scale))
    if lab:
        w = len(name) * 19 * scale * 0.6
        lab.block(x - 12 * scale, y - 30 * scale, x + 14 * scale + w, y + 14 * scale)
    return s


def spread(pts, min_px):
    """Offset markers that sit within min_px of each other onto a small ring; return new xy + moved flag."""
    pts = [list(p) for p in pts]; moved = [False] * len(pts)
    for i in range(len(pts)):
        grp = [j for j in range(len(pts)) if math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]) < min_px]
        if len(grp) > 1 and not any(moved[j] for j in grp):
            cx = sum(pts[j][0] for j in grp) / len(grp); cy = sum(pts[j][1] for j in grp) / len(grp)
            for n, j in enumerate(grp):
                a = -math.pi / 2 + 2 * math.pi * n / len(grp) + math.pi / 4
                pts[j] = [cx + math.cos(a) * min_px * 0.75, cy + math.sin(a) * min_px * 0.75]; moved[j] = True
    return pts, moved


def facility_markers(v, cards, scale=1.0, lab=None, names=True, approx_ids=()):
    raw_xy = [v.xy(c["lon"], c["lat"]) for c in cards]
    new, moved = spread(raw_xy, 26 * scale)
    s = []
    for n, (c, (x0, y0), (x, y), mv) in enumerate(zip(cards, raw_xy, new, moved), start=1):
        if mv:
            s.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{TEAL}" stroke-width="{1.2*scale:.1f}"/>')
            s.append(f'<circle cx="{x0:.1f}" cy="{y0:.1f}" r="{2.4*scale:.1f}" fill="{TEAL}"/>')
        dash = f' stroke-dasharray="{3*scale:.0f} {2*scale:.0f}"' if c["facility_id"] in approx_ids else ""
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{12*scale:.1f}" fill="{TEAL}" stroke="#fff" stroke-width="{2.4*scale:.1f}"{dash}/>')
        s.append(f'<text x="{x:.1f}" y="{y + 5*scale:.1f}" font-size="{14*scale:.1f}" font-weight="800" fill="#fff" text-anchor="middle">{n}</text>')
        if lab: lab.block(x - 14 * scale, y - 14 * scale, x + 14 * scale, y + 14 * scale)
        if names:
            nm = c["name"].split(",")[0]
            if len(nm) > 34: nm = nm[:32].rstrip() + "…"
            wpx = len(nm) * 12.5 * scale * 0.58
            if x + 16 * scale + wpx > v.W - 4:
                s.append(text(x - 16 * scale, y + 5 * scale, nm, 12.5 * scale, weight=700, fill=TEAL, anchor="end", hw=4 * scale))
            else:
                s.append(text(x + 16 * scale, y + 5 * scale, nm, 12.5 * scale, weight=700, fill=TEAL, anchor="start", hw=4 * scale))
            if lab: lab.block(x + 14 * scale, y - 10 * scale, x + 16 * scale + len(nm) * 12.5 * scale * 0.56, y + 9 * scale)
    return s


def whynot_markers(v, items, scale=1.0, lab=None, letters=True):
    s = []
    for n, it in enumerate(items):
        if it.get("lat") is None: continue
        x, y = v.xy(it["lon"], it["lat"]); r = 6.5 * scale
        dash = f' stroke-dasharray="{2.5*scale:.1f} {2*scale:.1f}"' if it.get("position_approximate") else ""
        s.append(f'<rect x="{x-r:.1f}" y="{y-r:.1f}" width="{2*r:.1f}" height="{2*r:.1f}" fill="#fff" stroke="{GREY}" stroke-width="{1.8*scale:.1f}"{dash} transform="rotate(45 {x:.1f} {y:.1f})"/>')
        if letters:
            s.append(f'<text x="{x:.1f}" y="{y+3.6*scale:.1f}" font-size="{9.5*scale:.1f}" font-weight="800" fill="#5d6366" text-anchor="middle">{chr(65+n)}</text>')
        if lab: lab.block(x - r - 2, y - r - 2, x + r + 2, y + r + 2)
    return s


def scalebar(v, km, scale=1.0, x=None, y=None):
    px = km * v.km_px
    x = 18 * scale if x is None else x; y = v.H - 18 * scale if y is None else y
    return [f'<rect x="{x-6*scale:.1f}" y="{y-22*scale:.1f}" width="{px+52*scale:.1f}" height="{30*scale:.1f}" rx="{4*scale:.1f}" fill="#fff" fill-opacity="0.85"/>',
            f'<path d="M{x:.1f},{y-6*scale:.1f} V{y:.1f} H{x+px:.1f} V{y-6*scale:.1f}" fill="none" stroke="{INK}" stroke-width="{2*scale:.1f}"/>',
            f'<text x="{x+px+6*scale:.1f}" y="{y:.1f}" font-size="{12*scale:.1f}" font-weight="600" fill="{INK}">{km:g} km</text>']


def north(v, scale=1.0):
    x, y = v.W - 26 * scale, 30 * scale
    return [f'<path d="M{x:.1f},{y-14*scale:.1f} L{x+7*scale:.1f},{y+6*scale:.1f} L{x:.1f},{y+2*scale:.1f} L{x-7*scale:.1f},{y+6*scale:.1f}Z" fill="{INK}"/>',
            f'<text x="{x:.1f}" y="{y+20*scale:.1f}" font-size="{11*scale:.1f}" font-weight="700" fill="{INK}" text-anchor="middle">N</text>']


def svg(v, parts, extra_defs=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {v.W} {v.H}" width="{v.W}" height="{v.H}" '
            f'font-family="Avenir Next, Segoe UI, Helvetica Neue, Arial, sans-serif">{extra_defs}'
            + "".join(parts) + "</svg>")


def inset(lon, lat, box=None, W=180, H=200, name=None, scale=1.0):
    """India locator: outline, states, a red box for the current view (box = lon0, lat0, lon1, lat1)."""
    v = View(82.8, 22.4, 3350, W, H)
    p = [f'<rect width="{W}" height="{H}" fill="#fff" fill-opacity="0"/>']
    d = " ".join(path_d(v, r, True) for r in OUTLINE)
    p.append(f'<path d="{d}" fill="{PAPER}" stroke="#4d5a5e" stroke-width="1" fill-rule="evenodd"/>')
    bd = " ".join(path_d(v, L) for L in BORDERS)
    p.append(f'<path d="{bd}" fill="none" stroke="#a39a8c" stroke-width="0.6"/>')
    x, y = v.xy(lon, lat)
    if box is not None:
        x0, y0 = v.xy(box[0], box[3]); x1, y1 = v.xy(box[2], box[1])
        w, h = max(x1 - x0, 7), max(y1 - y0, 7)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        p.append(f'<rect x="{cx-w/2:.1f}" y="{cy-h/2:.1f}" width="{w:.1f}" height="{h:.1f}" fill="#c0392b" fill-opacity="0.15" stroke="#c0392b" stroke-width="1.6"/>')
    p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#c0392b"/>')
    if name:
        p.append(text(x + 7, y + 4, name, 11, weight=800, anchor="start", hw=3, halo="#fff"))
    return svg(v, p)


def country_png(v, path, dpr=2):
    W, H = int(v.W * dpr), int(v.H * dpr)
    x, y = v.xy(PO[:, 0], PO[:, 1]); x = (x * dpr).astype(int); y = (y * dpr).astype(int)
    m = (x >= 0) & (x < W) & (y >= 0) & (y < H)
    acc = np.zeros((H, W), float)
    np.add.at(acc, (y[m], x[m]), 1.0)
    a = np.clip(acc / 3.0, 0, 1) ** 0.6
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = 110, 94, 72
    img[..., 3] = (a * 210).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(path, optimize=True)


def load(name):
    return json.loads((S / name).read_text())


EX = load("extras_k.json")


def local_map(key, *, W, H, width_km, center=None, fit=None, scale=1.0, towns=8, districts=4, so=0,
              ring_km=None, trip=None, whynot=True, names=True, state_sides=(), exclude=(), searched_name="",
              searched_sub=None, extra_cards=None, out_name=None, scalebar_km=10, title=None, ring_label=None):
    ans = load(f"ans_{key}.json"); loc = ans["location"]
    lon0, lat0 = center or (loc["lon"], loc["lat"])
    v = View(lon0, lat0, width_km, W, H)
    lab = Labeler(W, H)
    cards = list(ans["results"]) + (list(ans.get("further_away") or []) or ([ans["beyond_radius"]] if ans.get("beyond_radius") else []))
    approx = {c["facility_id"] for c in cards if c.get("position_approximate")}
    p = base_layers(v, scale=scale, dot_r=1.25 * scale)
    if ring_km:
        p += ring(v, loc["lon"], loc["lat"], ring_km, ring_label or f"{ring_km:g} km", scale=scale)
    top = []
    if trip is not None:
        top += trip_layer(v, key, mode=trip, scale=scale)
    if whynot:
        top += whynot_markers(v, EX[key]["why_not"]["items"], scale=scale, lab=lab)
    top += facility_markers(v, cards, scale=scale, lab=lab, names=names, approx_ids=approx)
    top += searched(v, loc["lon"], loc["lat"], searched_name, scale=scale, lab=lab, sub=searched_sub,
                    precision_km=loc.get("precision_km") if loc.get("kind") not in ("pin",) else None)
    sx, sy = v.xy(loc["lon"], loc["lat"])
    labels = label_layers(v, lab, scale=scale, town_limit=towns, district_limit=districts, so_limit=so,
                          exclude=exclude, center=(sx, sy))
    p += labels + state_side_labels(v, state_sides, scale) + top
    p += scalebar(v, scalebar_km, scale) + north(v, scale)
    (OUT / out_name).write_text(svg(v, p))
    lo, la = v.lonlat_box()
    return (lo[0], la[1], lo[1], la[0])


def trip_layer(v, key, mode="claims", scale=1.0):
    t = load(f"trip_{key}.json")
    F = t["facilities"]; pts = np.array(t["points"])
    px, py = v.xy(pts[:, 0], pts[:, 1])
    fx, fy = v.xy(np.array([f["lon"] for f in F]), np.array([f["lat"] for f in F]))
    rec = t["nearest_recommended"]; cl = t["nearest_claim"]
    s = []
    on, first = [], []
    for j in range(len(pts)):
        r = rec[j]
        if mode == "evidence":
            if r >= 0: first.append((px[j], py[j], fx[r], fy[r], "e"))
        else:
            c = cl[j]
            if c < 0: continue
            if not F[c]["supported"]:
                first.append((px[j], py[j], fx[c], fy[c], "c"))
                if r >= 0: on.append((fx[c], fy[c], fx[r], fy[r]))
            else:
                first.append((px[j], py[j], fx[c], fy[c], "e"))
    for a, b, c, d, k in first:
        col = AMBER if k == "c" else TEAL
        s.append(f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d:.1f}" stroke="{col}" stroke-opacity="0.3" stroke-width="{1.0*scale:.1f}"/>')
    seen = set()
    for a, b, c, d in on:
        if (round(a), round(b), round(c), round(d)) in seen: continue
        seen.add((round(a), round(b), round(c), round(d)))
        s.append(f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d:.1f}" stroke="{AMBER}" stroke-width="{2.2*scale:.1f}" stroke-dasharray="{6*scale:.0f} {4*scale:.0f}"/>')
    # village dots at start
    s.append('<g>' + "".join(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{2.6*scale:.1f}" fill="#fff" stroke="{INK}" stroke-width="{0.9*scale:.1f}"/>' for a, b, *_ in first) + '</g>')
    if mode == "claims":
        cnt = {}
        for j in range(len(pts)):
            c = cl[j]
            if c >= 0 and not F[c]["supported"]: cnt[c] = cnt.get(c, 0) + 1
        for c, n in cnt.items():
            if not v.inside(fx[c], fy[c]): continue
            r = (6 + 1.2 * math.sqrt(n)) * scale
            s.append(f'<circle cx="{fx[c]:.1f}" cy="{fy[c]:.1f}" r="{r:.1f}" fill="#fff" fill-opacity="0.8" stroke="{AMBER}" stroke-width="{2.4*scale:.1f}"/>')
            if n >= 25:
                s.append(text(fx[c], fy[c] - r - 4 * scale, f"{n}", 12 * scale, weight=800, fill=AMBER, hw=3 * scale))
    return s


if __name__ == "__main__":
    boxes = {}
    # Lucknow small map: city and neighbouring area
    boxes["luck"] = local_map("luck", W=800, H=600, width_km=24, scale=1.0, towns=9, districts=3, so=4,
                              searched_name="Lucknow", searched_sub="PIN 226001", out_name="map_luck.svg",
                              scalebar_km=5, exclude=("Lucknow", "Lucknow GPO"))
    boxes["luck_phone"] = local_map("luck", W=716, H=440, width_km=22, scale=1.15, towns=6, districts=2, so=3,
                                    searched_name="Lucknow", searched_sub="PIN 226001", out_name="map_luck_phone.svg",
                                    scalebar_km=5, names=False, exclude=("Lucknow", "Lucknow GPO"))
    # Lucknow expanded, trip view claims mode, fitted to the 50 km ring
    boxes["luck_trip"] = local_map("luck", W=1000, H=760, width_km=124, scale=1.05, towns=10, districts=5,
                                   ring_km=50, ring_label="Villages within 50 km", trip="claims", whynot=False, names=False,
                                   searched_name="Lucknow", searched_sub="PIN 226001", out_name="map_luck_trip.svg",
                                   scalebar_km=20, exclude=("Lucknow", "Lucknow GPO"))
    # Jokihat: both points, Bihar / West Bengal border
    a = load("ans_jok.json"); b = a["beyond_radius"]; L = a["location"]
    c = ((L["lon"] + b["lon"]) / 2, (L["lat"] + b["lat"]) / 2)
    boxes["jok"] = local_map("jok", W=800, H=600, width_km=205, center=(87.98, 26.17), scale=1.0, towns=11, districts=4,
                             ring_km=47, ring_label="60 min drive estimate (about 47 km)", so=3, searched_name="Jokihat", searched_sub="Araria district, Bihar",
                             state_sides=[("BIHAR", 87.05, 26.0), ("WEST BENGAL", 87.95, 26.08), ("BANGLADESH", 88.5, 25.72), ("NEPAL", 87.55, 26.36)],
                             out_name="map_jok.svg", scalebar_km=20, exclude=("Jokihat",))
    boxes["jok_phone"] = local_map("jok", W=716, H=440, width_km=215, center=(87.98, 26.17), scale=1.15, towns=5, districts=2,
                                   ring_km=47, ring_label="60 min (about 47 km)", searched_name="Jokihat", names=False,
                                   state_sides=[("BIHAR", 87.05, 26.0), ("WEST BENGAL", 87.95, 26.08)],
                                   out_name="map_jok_phone.svg", scalebar_km=20, exclude=("Jokihat",))
    # Jaipur
    boxes["jai"] = local_map("jai", W=800, H=600, width_km=30, scale=1.0, towns=7, districts=2, so=8,
                             searched_name="Jaipur", searched_sub="city centre, PIN 302001", out_name="map_jai.svg",
                             scalebar_km=5, exclude=("Jaipur", "Jaipur City", "Jaipur GPO"))
    # map panels shown at 392x340 css px: render at 2x with larger type
    boxes["luck_panel"] = local_map("luck", W=784, H=680, width_km=19, center=(80.975, 26.855), scale=1.7, towns=6, districts=1, so=5,
                                    searched_name="Lucknow", searched_sub="PIN 226001", out_name="panel_luck.svg",
                                    scalebar_km=2, names=True, exclude=("Lucknow", "Lucknow GPO"))
    boxes["jok_panel"] = local_map("jok", W=784, H=680, width_km=200, center=(87.93, 26.15), scale=1.7, towns=6, districts=2,
                                   ring_km=47, ring_label="1-hour drive", searched_name="Jokihat", names=True,
                                   state_sides=[("BIHAR", 87.25, 25.9), ("WEST BENGAL", 88.05, 25.95), ("NEPAL", 87.45, 26.75)],
                                   out_name="panel_jok.svg", scalebar_km=20, exclude=("Jokihat",))
    boxes["jai_panel"] = local_map("jai", W=784, H=680, width_km=12, center=(75.815, 26.912), scale=1.7, towns=5, districts=1, so=6,
                                   searched_name="Jaipur", searched_sub="city centre", out_name="panel_jai.svg",
                                   scalebar_km=2, names=True, exclude=("Jaipur", "Jaipur City", "Jaipur GPO"))
    # Country view (zoomed out): PO dots draw India, states, the searched place and a box for the local view
    v = View(82.8, 22.6, 3300, 1000, 760)
    country_png(v, OUT / "india_po_1000x760.png")
    p = [f'<rect width="{v.W}" height="{v.H}" fill="#dfe3e1"/>']
    d = " ".join(path_d(v, r, True) for r in OUTLINE)
    p.append(f'<path d="{d}" fill="{PAPER}" fill-rule="evenodd"/>')
    import base64 as _b64
    _png = _b64.b64encode((OUT / "india_po_1000x760.png").read_bytes()).decode()
    p.append(f'<image href="data:image/png;base64,{_png}" x="0" y="0" width="{v.W}" height="{v.H}"/>')
    p.append(f'<path d="{" ".join(path_d(v, Lb) for Lb in BORDERS)}" fill="none" stroke="#7d756a" stroke-width="1" stroke-dasharray="4 3"/>')
    p.append(f'<path d="{d}" fill="none" stroke="#4d5a5e" stroke-width="1.4" fill-rule="evenodd"/>')
    lab = Labeler(v.W, v.H)
    for name, lon, lat in STATE_LABELS:
        if name in ("Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Maharashtra", "Bihar", "Gujarat", "Karnataka",
                    "Odisha", "West Bengal", "Tamil Nadu", "Andhra Pradesh", "Telangana", "Chhattisgarh", "Jharkhand", "Assam"):
            x, y = v.xy(lon, lat)
            if name == "Uttar Pradesh": y += 18
            p.append(text(x, y, name.upper(), 11, weight=700, fill="#857a69", spacing=1.6, hw=0))
    L = load("ans_luck.json")["location"]
    bx = boxes["luck_trip"]
    x0, y0 = v.xy(bx[0], bx[3]); x1, y1 = v.xy(bx[2], bx[1])
    p.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1-x0:.1f}" height="{y1-y0:.1f}" fill="#c0392b" fill-opacity="0.12" stroke="#c0392b" stroke-width="2"/>')
    x, y = v.xy(L["lon"], L["lat"])
    p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#2a5db0" stroke="#fff" stroke-width="2"/>')
    p.append(text(x + 22, y - 10, "Lucknow", 20, weight=800, anchor="start", hw=5))
    p.append(text(x + 22, y + 8, "PIN 226001, Uttar Pradesh", 12, weight=600, fill="#2a5db0", anchor="start", hw=4))
    for nm in ("New Delhi", "Mumbai", "Kolkata", "Chennai", "Bengaluru", "Hyderabad"):
        pass
    for nm, lon, lat in [("Delhi", 77.21, 28.61), ("Mumbai", 72.88, 19.08), ("Kolkata", 88.36, 22.57),
                         ("Chennai", 80.27, 13.08), ("Bengaluru", 77.59, 12.97), ("Hyderabad", 78.49, 17.39)]:
        x, y = v.xy(lon, lat)
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{INK}"/>')
        p.append(text(x + 6, y + 4, nm, 12, weight=600, anchor="start", hw=3))
    p += scalebar(v, 500, 1.0) + north(v)
    (OUT / "map_india.svg").write_text(svg(v, p))
    # insets
    for k, (lon, lat, nm) in {"luck": (L["lon"], L["lat"], "Lucknow"),
                              "jok": (load("ans_jok.json")["location"]["lon"], load("ans_jok.json")["location"]["lat"], "Jokihat"),
                              "jai": (load("ans_jai.json")["location"]["lon"], load("ans_jai.json")["location"]["lat"], "Jaipur")}.items():
        (OUT / f"inset_{k}.svg").write_text(inset(lon, lat, boxes[k], name=nm))
    for k in ("luck_panel", "jok_panel", "jai_panel"):
        b = boxes[k]; base = k.split("_")[0]
        L0 = load(f"ans_{base}.json")["location"]
        (OUT / f"inset_{k}.svg").write_text(inset(L0["lon"], L0["lat"], b, name=None))
    (OUT / "inset_luck_trip.svg").write_text(inset(L["lon"], L["lat"], boxes["luck_trip"], name="Lucknow"))
    print(json.dumps(boxes))
    for f in sorted(OUT.iterdir()): print(f.name, f.stat().st_size)
