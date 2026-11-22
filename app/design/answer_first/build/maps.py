"""SVG map builders for the answer-first mockup. Real geography only:
Natural Earth outline + admin-1 states, bundle post offices, gazetteer names."""
import json, math, html
import numpy as np
import pandas as pd

ROOT = "/Users/hrudaynara/Projects/CMU Datathon"
GJ = json.load(open(f"{ROOT}/app/design/answer_first/india_states.geojson"))
PLACES = pd.read_parquet(f"{ROOT}/dbapp/bundle/places.parquet")
PO = pd.read_parquet(f"{ROOT}/dbapp/bundle/post_offices.parquet").dropna(subset=["lat", "lon"])
PO = PO[~PO.placeholder]
POS = PLACES[PLACES.kind == "post_office"]
DIST = PLACES[PLACES.kind == "district"]

def esc(s): return html.escape(str(s), quote=True)

def title(s):
    return " ".join(w if w.isupper() and len(w) <= 3 and w not in ("AND",) else w.capitalize() for w in str(s).split())

class Proj:
    def __init__(self, lat0, lon0, half_w_km, W, H):
        self.lat0, self.lon0, self.W, self.H = lat0, lon0, W, H
        self.k = (W / 2) / half_w_km
        self.c = math.cos(math.radians(lat0))
    def xy(self, lat, lon):
        x = self.W / 2 + (np.asarray(lon) - self.lon0) * 111.32 * self.c * self.k
        y = self.H / 2 - (np.asarray(lat) - self.lat0) * 110.57 * self.k
        return x, y
    def inview(self, lat, lon, pad=0):
        x, y = self.xy(lat, lon)
        return (x > -pad) & (x < self.W + pad) & (y > -pad) & (y < self.H + pad)
    def km(self, px): return px / self.k
    def bounds(self):
        dlat = (self.H / 2) / self.k / 110.57
        dlon = (self.W / 2) / self.k / (111.32 * self.c)
        return self.lat0 - dlat, self.lat0 + dlat, self.lon0 - dlon, self.lon0 + dlon

def ring_path(P, ring):
    a = np.asarray(ring)
    x, y = P.xy(a[:, 1], a[:, 0])
    return "M" + "L".join(f"{u:.1f},{v:.1f}" for u, v in zip(x, y)) + "Z"

def ring_bbox_hits(P, ring, pad=200):
    a = np.asarray(ring)
    x, y = P.xy(a[:, 1], a[:, 0])
    return not (x.max() < -pad or x.min() > P.W + pad or y.max() < -pad or y.min() > P.H + pad)

def land_and_states(P, state_stroke="#8d8576", state_w=1.3, outline_w=1.6):
    land = "".join(ring_path(P, r) for r in GJ["outline"] if ring_bbox_hits(P, r))
    out = [f'<path d="{land}" fill="var(--land)" stroke="var(--coast)" stroke-width="{outline_w}" fill-rule="evenodd"/>']
    sp = []
    for f in GJ["features"]:
        for poly in f["geometry"]["coordinates"]:
            for r in poly:
                if ring_bbox_hits(P, r):
                    sp.append(ring_path(P, r))
    out.append(f'<path d="{"".join(sp)}" fill="none" stroke="{state_stroke}" stroke-width="{state_w}" '
               f'stroke-dasharray="6 3 1.5 3" stroke-linejoin="round"/>')
    # re-draw the coast on top so state dashes do not run along the sea edge
    out.append(f'<path d="{land}" fill="none" stroke="var(--coast)" stroke-width="{outline_w}"/>')
    return "\n".join(out)

def state_labels(P, size=11, only=None):
    out = []
    for f in GJ["features"]:
        name = "".join(ch for ch in str(f["properties"]["name"]) if ch.isprintable() and ch != "\ufffd").strip()
        if only and name not in only:
            continue
        rings = [np.asarray(r) for poly in f["geometry"]["coordinates"] for r in poly]
        big = max(rings, key=len)
        lon, lat = big[:, 0].mean(), big[:, 1].mean()
        if not P.inview(lat, lon, -20):
            continue
        x, y = P.xy(lat, lon)
        out.append(f'<text class="lbl-state" x="{x:.0f}" y="{y:.0f}" font-size="{size}">{esc(name.upper())}</text>')
    return "\n".join(out)

def po_dots(P, r=1.2, fill="var(--po)", cap=6000):
    m = P.inview(PO.lat.values, PO.lon.values, 4)
    sub = PO[m]
    if len(sub) > cap:
        sub = sub.sample(cap, random_state=0)
    x, y = P.xy(sub.lat.values, sub.lon.values)
    return f'<g fill="{fill}">' + "".join(f'<circle cx="{u:.1f}" cy="{v:.1f}" r="{r}"/>' for u, v in zip(x, y)) + "</g>"

class Labeler:
    def __init__(self, P): self.P, self.boxes = P, []
    def free(self, b):
        x0, y0, x1, y1 = b
        if x0 < 4 or y0 < 4 or x1 > self.P.W - 4 or y1 > self.P.H - 4: return False
        return all(x1 < a or x0 > c or y1 < b_ or y0 > d for a, b_, c, d in self.boxes)
    def reserve(self, b): self.boxes.append(b)
    def place(self, x, y, text, cls, size, dx=0, dy=0, anchor="middle", force=False):
        w = len(text) * size * 0.58 + 6; h = size + 4
        if anchor == "middle": x0 = x + dx - w / 2
        elif anchor == "start": x0 = x + dx
        else: x0 = x + dx - w
        b = (x0, y + dy - size, x0 + w, y + dy + 4)
        if force or self.free(b):
            self.reserve(b)
            return f'<text class="{cls}" x="{x+dx:.0f}" y="{y+dy:.0f}" font-size="{size}" text-anchor="{anchor}">{esc(text)}</text>'
        return ""

def hq_towns(P, pad=0):
    """District HQ towns: post offices named after their own district, located to within 15 km.
    Districts without one get a district label at the district centre."""
    lat0, lat1, lon0, lon1 = P.bounds()
    po = POS[(POS.lat.between(lat0 - 1, lat1 + 1)) & (POS.lon.between(lon0 - 1, lon1 + 1))]
    hq = po[(po.name_norm == po.district.str.upper()) & (po.precision_km <= 15)]
    hq = hq.sort_values("precision_km").drop_duplicates("district")
    d = DIST[(DIST.lat.between(lat0 - 1, lat1 + 1)) & (DIST.lon.between(lon0 - 1, lon1 + 1))]
    d_no = d[~d.district.str.upper().isin(hq.district.str.upper())]
    return hq, d_no

def scale_bar(P, x=16, y=None):
    y = y or P.H - 18
    target = P.km(110)
    nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500]
    km = max([n for n in nice if n <= target] or [1])
    px = km * P.k
    return (f'<g class="scale"><rect x="{x-6}" y="{y-17}" width="{px+52}" height="26" rx="4" fill="var(--chip)" opacity=".85"/>'
            f'<path d="M{x},{y-4}V{y}H{x+px:.0f}V{y-4}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>'
            f'<text x="{x+px+6:.0f}" y="{y}" font-size="12" class="lbl-scale">{km} km</text></g>')

def north(P):
    x, y = P.W - 24, P.H - 30
    return (f'<g class="north"><path d="M{x},{y-14}L{x+6},{y+2}L{x},{y-2}L{x-6},{y+2}Z" fill="var(--ink)"/>'
            f'<text x="{x}" y="{y+16}" font-size="11" text-anchor="middle" class="lbl-scale">N</text></g>')

def pin(x, y, n, big=False):
    r = 15 if big else 12
    return (f'<g class="pin"><circle cx="{x:.1f}" cy="{y:.1f}" r="{r+2}" fill="#fff"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="var(--rec)"/>'
            f'<text x="{x:.1f}" y="{y+ (5.5 if big else 4.5):.1f}" text-anchor="middle" font-size="{16 if big else 13}" '
            f'font-weight="700" fill="#fff">{n}</text></g>')

def searched(x, y):
    return (f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="none" stroke="var(--ink)" stroke-width="2.5"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--ink)"/></g>')

def diamond(x, y, s=6.5, stroke="var(--notrec)", dashed=False):
    da = ' stroke-dasharray="2 2"' if dashed else ""
    return (f'<path d="M{x:.1f},{y-s:.1f}L{x+s:.1f},{y:.1f}L{x:.1f},{y+s:.1f}L{x-s:.1f},{y:.1f}Z" '
            f'fill="var(--land)" stroke="{stroke}" stroke-width="2"{da}/>')

def declutter(pts, min_px=26):
    """Spread markers closer than min_px around their shared centre; return new positions and leader flags."""
    out = [list(p) for p in pts]
    moved = [False] * len(pts)
    for i in range(len(out)):
        for j in range(i):
            dx, dy = out[i][0] - out[j][0], out[i][1] - out[j][1]
            if math.hypot(dx, dy) < min_px:
                ang = math.atan2(dy, dx) if (dx or dy) else -0.6
                out[i][0] = out[j][0] + math.cos(ang) * min_px
                out[i][1] = out[j][1] + math.sin(ang) * min_px
                moved[i] = True
    return out, moved

def svg_open(P, cls=""):
    return (f'<svg class="mapsvg {cls}" viewBox="0 0 {P.W} {P.H}" preserveAspectRatio="xMidYMid slice" '
            f'xmlns="http://www.w3.org/2000/svg" role="img">'
            f'<rect width="{P.W}" height="{P.H}" fill="var(--sea)"/>')

def edge_labels(P, L, towns, center_ll, max_n=5):
    """Neighbouring towns outside the view: a label pinned to the edge, with direction and km."""
    out = []
    lat_c, lon_c = center_ll
    cand = []
    for _, t in towns.iterrows():
        if P.inview(t.lat, t.lon, -10):
            continue
        dkm = math.hypot((t.lon - lon_c) * 111.32 * P.c, (t.lat - lat_c) * 110.57)
        if dkm > 140:
            continue
        cand.append((dkm, t))
    cand.sort(key=lambda z: z[0])
    for dkm, t in cand[:max_n]:
        x, y = P.xy(t.lat, t.lon)
        cx, cy = P.W / 2, P.H / 2
        dx, dy = x - cx, y - cy
        s = min((P.W / 2 - 18) / abs(dx) if dx else 1e9, (P.H / 2 - 18) / abs(dy) if dy else 1e9)
        ex, ey = cx + dx * s, cy + dy * s
        ang = math.degrees(math.atan2(dy, dx))
        name = title(t["name"])
        text = f"{name} {dkm:.0f} km"
        anchor = "end" if ex > P.W - 60 else ("start" if ex < 60 else "middle")
        tx = ex - 14 if anchor == "end" else (ex + 14 if anchor == "start" else ex)
        ty = ey + (4 if anchor != "middle" else (-10 if ey > P.H / 2 else 22))
        lbl = L.place(tx, ty, text, "lbl-edge", 12, anchor=anchor)
        if lbl:
            out.append(f'<g class="edge"><path d="M0,-6L10,0L0,6Z" transform="translate({ex:.0f},{ey:.0f}) rotate({ang:.0f}) translate(-8,0)" fill="var(--ink-2)"/>{lbl}</g>')
    return "\n".join(out)

def local_map(P, center, center_label, cards, notrec=(), towns_mode="hq", ring_km=None, ring_label=None,
              precision_km=None, extra_layers="", neighbourhoods=6, cls="", edge=True, hide_pins=False,
              line_to=None, state_names=None):
    L = Labeler(P)
    parts = [svg_open(P, cls), land_and_states(P)]
    parts.append(po_dots(P))
    if precision_km and precision_km * P.k > 14:
        x, y = P.xy(*center)
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{precision_km*P.k:.0f}" fill="var(--prec)" stroke="none"/>')
    if ring_km:
        x, y = P.xy(*center)
        r = ring_km * P.k
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="none" stroke="var(--ink-2)" stroke-width="1.6" stroke-dasharray="7 5"/>')
        if ring_label:
            parts.append(f'<text class="lbl-ring" x="{x:.0f}" y="{y - r - 6:.0f}" text-anchor="middle" font-size="12">{esc(ring_label)}</text>')
            L.reserve((x - 80, y - r - 22, x + 80, y - r - 2))
    if line_to:
        x0, y0 = P.xy(*center); x1, y1 = P.xy(*line_to[0])
        parts.append(f'<path d="M{x0:.0f},{y0:.0f}L{x1:.0f},{y1:.0f}" stroke="var(--rec)" stroke-width="2" stroke-dasharray="2 5" stroke-linecap="round"/>')
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        parts.append(f'<g><rect x="{mx-42:.0f}" y="{my-12:.0f}" width="84" height="22" rx="11" fill="var(--rec)"/>'
                     f'<text x="{mx:.0f}" y="{my+4:.0f}" text-anchor="middle" font-size="12.5" font-weight="700" fill="#fff">{esc(line_to[1])}</text></g>')
        L.reserve((mx - 44, my - 14, mx + 44, my + 12))
    parts.append(extra_layers)
    # markers
    marks = []
    cx, cy = P.xy(*center)
    L.reserve((cx - 12, cy - 12, cx + 12, cy + 12))
    for (lat, lon, approx) in notrec:
        x, y = P.xy(lat, lon)
        marks.append(diamond(x, y, dashed=approx))
        L.reserve((x - 7, y - 7, x + 7, y + 7))
    pos = [P.xy(c[0], c[1]) for c in cards]
    if not hide_pins:
        newpos, moved = declutter([(float(a), float(b)) for a, b in pos])
        for i, ((x0, y0), (x, y), mv) in enumerate(zip(pos, newpos, moved)):
            if mv:
                marks.append(f'<path d="M{x0:.1f},{y0:.1f}L{x:.1f},{y:.1f}" stroke="var(--rec)" stroke-width="1.5"/>'
                             f'<circle cx="{x0:.1f}" cy="{y0:.1f}" r="2.5" fill="var(--rec)"/>')
        for i, (x, y) in enumerate(newpos):
            L.reserve((x - 15, y - 15, x + 15, y + 15))
        pin_svg = [pin(x, y, cards[i][2], big=(cards[i][2] == 1)) for i, (x, y) in enumerate(newpos)]
        card_labels = []
        for i, (x, y) in enumerate(newpos):
            if len(cards[i]) > 3 and cards[i][3]:
                lab = (L.place(x, y, cards[i][3], "lbl-card", 12.5, dx=19, dy=4, anchor="start")
                       or L.place(x, y, cards[i][3], "lbl-card", 12.5, dx=-19, dy=4, anchor="end")
                       or L.place(x, y, cards[i][3], "lbl-card", 12.5, dy=-20))
                card_labels.append(lab)
    # searched label (forced)
    sl = (L.place(cx, cy, center_label, "lbl-search", 15, dy=-18, force=False)
          or L.place(cx, cy, center_label, "lbl-search", 15, dy=30, force=False)
          or L.place(cx, cy, center_label, "lbl-search", 15, dx=16, dy=5, anchor="start", force=True))
    labels = [sl]
    if not hide_pins:
        labels += card_labels
    # towns
    hq, dno = hq_towns(P)
    for _, t in hq.iterrows():
        if P.inview(t.lat, t.lon, -6):
            x, y = P.xy(t.lat, t.lon)
            s = L.place(x, y, title(t["name"]), "lbl-town", 13.5, dy=-7)
            if s:
                labels.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3.2" fill="var(--ink-2)"/>' + s)
    for _, d in dno.iterrows():
        if P.inview(d.lat, d.lon, -30):
            x, y = P.xy(d.lat, d.lon)
            labels.append(L.place(x, y, title(d["name"]).upper() + " DISTRICT", "lbl-district", 11))
    if state_names:
        labels.append(state_labels(P, 12, only=state_names))
    # neighbourhood names: precise post offices, one per grid cell, nearest the cell centre
    if neighbourhoods:
        lat0, lat1, lon0, lon1 = P.bounds()
        po = POS[(POS.lat.between(lat0, lat1)) & (POS.lon.between(lon0, lon1)) & (POS.precision_km <= 3)]
        if len(po):
            x, y = P.xy(po.lat.values, po.lon.values)
            po = po.assign(x=x, y=y)
            gx, gy = 3, 3
            picked = 0
            for i in range(gx):
                for j in range(gy):
                    if picked >= neighbourhoods: break
                    ccx, ccy = (i + .5) * P.W / gx, (j + .5) * P.H / gy
                    cell = po[(po.x // (P.W / gx) == i) & (po.y // (P.H / gy) == j)]
                    if not len(cell): continue
                    cell = cell.assign(d=np.hypot(cell.x - ccx, cell.y - ccy)).sort_values("d")
                    for _, t in cell.head(6).iterrows():
                        nm = title(t["name"])
                        if len(nm) > 18: continue
                        s = L.place(t.x, t.y, nm, "lbl-hood", 11.5, dy=-5)
                        if s:
                            labels.append(f'<circle cx="{t.x:.0f}" cy="{t.y:.0f}" r="1.8" fill="var(--ink-3)"/>' + s)
                            picked += 1
                            break
    if edge:
        towns = pd.concat([hq[["name", "lat", "lon"]], dno.assign(name=dno["name"] + " district")[["name", "lat", "lon"]]])
        labels.append(edge_labels(P, L, towns, center))
    parts += marks
    if not hide_pins:
        parts += pin_svg
    parts.append(searched(cx, cy))
    parts += labels
    parts.append(scale_bar(P))
    parts.append(north(P))
    parts.append("</svg>")
    return "\n".join(p for p in parts if p)

def india_map(P, marker=None, marker_label=None, box=None, places=(), dots_href=None, cls="", state_lbl=True):
    """Country view: land, states, every post office (raster), the searched place."""
    parts = [svg_open(P, cls)]
    parts.append(land_and_states(P, state_w=0.9, outline_w=1.2))
    if dots_href:
        lat0, lat1, lon0, lon1 = DOTS_EXTENT
        x0, y0 = P.xy(lat1, lon0); x1, y1 = P.xy(lat0, lon1)
        parts.append(f'<image href="{dots_href}" x="{x0:.1f}" y="{y0:.1f}" width="{x1-x0:.1f}" height="{y1-y0:.1f}" preserveAspectRatio="none"/>')
    if state_lbl:
        parts.append(state_labels(P, 10))
    if box:
        (la0, lo0), (la1, lo1) = box
        x0, y0 = P.xy(la1, lo0); x1, y1 = P.xy(la0, lo1)
        w, h = max(x1 - x0, 10), max(y1 - y0, 10)
        parts.append(f'<rect x="{(x0+x1)/2-w/2:.1f}" y="{(y0+y1)/2-h/2:.1f}" width="{w:.1f}" height="{h:.1f}" fill="none" stroke="var(--here)" stroke-width="2.5"/>')
    for (lat, lon, name, sub) in places:
        x, y = P.xy(lat, lon)
        parts.append(f'<g class="ex-place"><circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="var(--rec)" stroke="#fff" stroke-width="2"/>'
                     f'<text class="lbl-town" x="{x+10:.1f}" y="{y+4:.1f}" font-size="14">{esc(name)}</text>'
                     + (f'<text class="lbl-hood" x="{x+10:.1f}" y="{y+19:.1f}" font-size="11.5">{esc(sub)}</text>' if sub else "")
                     + '</g>')
    if marker:
        x, y = P.xy(*marker)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="var(--here)" opacity=".18"/>'
                     f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6.5" fill="var(--here)" stroke="#fff" stroke-width="2"/>')
        if marker_label:
            parts.append(f'<text class="lbl-search" x="{x+14:.1f}" y="{y-8:.1f}" font-size="16">{esc(marker_label)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)

DOTS_EXTENT = (6.0, 37.6, 67.5, 98.0)

def render_dots_png(path, px_w=1800):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lat0, lat1, lon0, lon1 = DOTS_EXTENT
    c = math.cos(math.radians((lat0 + lat1) / 2))
    # the india_map projection uses cos(lat0 of the view); we draw in a plain lon/lat grid stretched to the box,
    # which is what <image preserveAspectRatio="none"> expects
    w_in = px_w / 200
    h_in = w_in * (lat1 - lat0) / ((lon1 - lon0) * c)
    fig = plt.figure(figsize=(w_in, h_in), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(lon0, lon1); ax.set_ylim(lat0, lat1); ax.axis("off")
    ax.scatter(PO.lon.values, PO.lat.values, s=0.05, c="#7d7466", alpha=0.55, linewidths=0, rasterized=True)
    fig.savefig(path, transparent=True, dpi=200)
    plt.close(fig)
