"""Build the map's geography assets (app/design/SPEC.md section 5.1). No tile service (D009).

Run from the repo root with the repo virtualenv:

    .venv/bin/python app/public/geo/build_geo_assets.py [--ne-shp PATH]

Writes, next to this script (app/public/geo/, which is not Vite's publicDir: the map imports
each file with ``?url`` so only these files are emitted into the build):

- india_states.json   Natural Earth 1:10m admin-1, India only, 36 states and UTs (copied from
                      app/design/answer_first/india_states.geojson, closed rings kept).
- india_outline.json  vf.locate._INDIA_OUTLINE_B85 decoded (Natural Earth 1:10m admin-0, India
                      point of view), as a list of rings.
- neighbours.json     Neighbouring countries from ne_10m_admin_0_countries_ind (public domain,
                      https://www.naturalearthdata.com, "Admin 0 - Countries, point of view: India"),
                      clipped to the map's box and simplified. Rebuilt only with --ne-shp; otherwise
                      the existing file is kept.
- po_points.bin       Every bundle post office point (placeholders and artefact latitudes
                      dropped, deduped), as uint16 (lon, lat) pairs quantised to the India box.
- labels_india.json   State label points (inside the polygon), district names and points,
                      neighbour country and sea labels, and town names thinned to one per grid cell.

Prints the sizes. The total must stay under 1.5 MB.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BUNDLE = ROOT / "dbapp" / "bundle"
STATES_SRC = ROOT / "app" / "design" / "answer_first" / "india_states.geojson"

# Quantisation box, shared with app/src/map_assets.ts.
LON0, LON1, LAT0, LAT1 = 68.0, 97.5, 6.5, 37.2
# Clip box for neighbours: the map clamps its centre to lon 66..99, lat 5..38.
CLIP = (58.0, -2.0, 104.0, 44.0)
NEIGHBOURS = {"NPL": "Nepal", "BGD": "Bangladesh", "BTN": "Bhutan", "PAK": "Pakistan", "CHN": "China",
              "MMR": "Myanmar", "LKA": "Sri Lanka", "AFG": "Afghanistan"}
SEAS = [("Arabian Sea", 66.0, 16.0), ("Bay of Bengal", 88.5, 15.5), ("Indian Ocean", 80.0, 3.5)]
ARTEFACT_N = 100      # an exact latitude shared by this many post office rows is an artefact (SPEC 3.2)
TOWN_CELL_DEG = 0.12  # thinning grid for the India-wide town labels


def title(s: str) -> str:
    out = []
    for w in str(s).split():
        if w.isupper() and len(w) <= 3 and not any(v in w for v in "AEIOU"):
            out.append(w)  # keep short abbreviations
        elif w.lower() in ("and", "of", "the") and out:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def clean(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isprintable() and ch != "�").strip()


# ----------------------------------------------------------------------------- polygons

def pip(x: np.ndarray, y: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Even-odd point in ring, vectorised over points."""
    x1, y1 = ring[:-1, 0], ring[:-1, 1]
    x2, y2 = ring[1:, 0], ring[1:, 1]
    dy = np.where(y2 == y1, 1e-12, y2 - y1)
    X, Y = x[:, None], y[:, None]
    cross = ((y1 > Y) != (y2 > Y)) & (X < (x2 - x1) * (Y - y1) / dy + x1)
    return (cross.sum(axis=1) % 2) == 1


def seg_dist(x: np.ndarray, y: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Distance (degrees, with longitude scaled by cos lat) from points to the ring's edges."""
    k = math.cos(math.radians(float(np.mean(ring[:, 1]))))
    ax, ay = ring[:-1, 0] * k, ring[:-1, 1]
    bx, by = ring[1:, 0] * k, ring[1:, 1]
    px, py = x[:, None] * k, y[:, None]
    ex, ey = bx - ax, by - ay
    t = np.clip(((px - ax) * ex + (py - ay) * ey) / np.maximum(ex * ex + ey * ey, 1e-18), 0, 1)
    return np.min(np.hypot(ax + t * ex - px, ay + t * ey - py), axis=1)


def label_point(rings: list[np.ndarray]) -> tuple[float, float, float]:
    """A point well inside the largest ring (grid polylabel), and that ring's area in deg^2."""
    def area(r):
        return 0.5 * abs(np.dot(r[:-1, 0], r[1:, 1]) - np.dot(r[1:, 0], r[:-1, 1]))
    big = max(rings, key=area)
    x0, y0 = big[:, 0].min(), big[:, 1].min()
    x1, y1 = big[:, 0].max(), big[:, 1].max()
    n = 48
    gx, gy = np.meshgrid(np.linspace(x0, x1, n), np.linspace(y0, y1, n))
    gx, gy = gx.ravel(), gy.ravel()
    ins = pip(gx, gy, big)
    if not ins.any():
        return float(big[:, 0].mean()), float(big[:, 1].mean()), float(area(big))
    gx, gy = gx[ins], gy[ins]
    d = seg_dist(gx, gy, big)
    j = int(np.argmax(d))
    return float(gx[j]), float(gy[j]), float(area(big))


def dp(points: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker on an open polyline (iterative)."""
    n = len(points)
    if n < 3:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        seg = points[i + 1:j]
        ab = b - a
        L = math.hypot(*ab)
        if L < 1e-12:
            d = np.hypot(*(seg - a).T)
        else:
            d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return points[keep]


def clip_ring(ring: np.ndarray, box) -> np.ndarray:
    """Sutherland-Hodgman clip of a closed ring to an axis-aligned box."""
    xmin, ymin, xmax, ymax = box
    pts = [tuple(p) for p in ring[:-1]]
    for edge in range(4):
        if not pts:
            break
        def inside(p):
            return (p[0] >= xmin, p[1] >= ymin, p[0] <= xmax, p[1] <= ymax)[edge]

        def inter(p, q):
            (x1, y1), (x2, y2) = p, q
            if edge in (0, 2):
                xv = xmin if edge == 0 else xmax
                t = (xv - x1) / (x2 - x1)
                return (xv, y1 + t * (y2 - y1))
            yv = ymin if edge == 1 else ymax
            t = (yv - y1) / (y2 - y1)
            return (x1 + t * (x2 - x1), yv)
        out = []
        for k in range(len(pts)):
            p, q = pts[k - 1], pts[k]
            if inside(q):
                if not inside(p):
                    out.append(inter(p, q))
                out.append(q)
            elif inside(p):
                out.append(inter(p, q))
        pts = out
    if len(pts) < 3:
        return np.zeros((0, 2))
    a = np.array(pts + [pts[0]])
    return a


def read_shp_polygons(shp: Path) -> list[list[np.ndarray]]:
    """Minimal ESRI shapefile reader for polygon records (type 5): rings per record."""
    b = shp.read_bytes()
    pos, out = 100, []
    while pos + 8 <= len(b):
        _, clen = struct.unpack(">ii", b[pos:pos + 8])
        rec = b[pos + 8: pos + 8 + clen * 2]
        pos += 8 + clen * 2
        st = struct.unpack("<i", rec[:4])[0]
        if st != 5:
            out.append([])
            continue
        nparts, npts = struct.unpack("<ii", rec[36:44])
        parts = list(struct.unpack(f"<{nparts}i", rec[44:44 + 4 * nparts])) + [npts]
        xy = np.frombuffer(rec[44 + 4 * nparts: 44 + 4 * nparts + 16 * npts], dtype="<f8").reshape(-1, 2)
        out.append([xy[parts[k]:parts[k + 1]].copy() for k in range(nparts)])
    return out


def read_dbf(dbf: Path, fields: list[str]) -> list[dict]:
    b = dbf.read_bytes()
    nrec, hlen, rlen = struct.unpack("<IHH", b[4:12])
    defs, off = [], 32
    while b[off] != 0x0D:
        name = b[off:off + 11].split(b"\0")[0].decode()
        flen = b[off + 16]
        defs.append((name, flen))
        off += 32
    rows = []
    for r in range(nrec):
        rec = b[hlen + r * rlen: hlen + (r + 1) * rlen]
        p, row = 1, {}
        for name, flen in defs:
            if name in fields:
                row[name] = rec[p:p + flen].decode("latin-1").strip()
            p += flen
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- builders

def build_states() -> tuple[dict, list[dict]]:
    g = json.loads(STATES_SRC.read_text())
    feats, labels = [], []
    for f in g["features"]:
        name = clean(f["properties"]["name"])
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        fixed = []
        for poly in polys:
            rings = []
            for r in poly:
                r = [[round(x, 4), round(y, 4)] for x, y in r]
                if r[0] != r[-1]:
                    r.append(r[0])
                rings.append(r)
            fixed.append(rings)
        feats.append({"type": "Feature", "properties": {"name": name},
                      "geometry": {"type": "MultiPolygon", "coordinates": fixed}})
        lon, lat, area = label_point([np.asarray(r) for poly in fixed for r in poly])
        # Small states and UTs get their name only once the map is close enough to read it.
        minz = 3.6 if area > 6 else 4.6 if area > 1.5 else 5.6 if area > 0.2 else 7.0
        labels.append({"name": name, "lon": round(lon, 3), "lat": round(lat, 3), "minz": minz})
    return {"type": "FeatureCollection", "features": feats, "source": g.get("source", "")}, labels


def build_outline() -> list[list[list[float]]]:
    sys.path.insert(0, str(ROOT / "src"))
    from vf import locate  # noqa: E402
    raw = zlib.decompress(base64.b85decode("".join(locate._INDIA_OUTLINE_B85))).decode()
    rings = []
    for ring in raw.split(";"):
        xy = np.cumsum(np.array(ring.split(","), dtype=np.int64).reshape(-1, 2), axis=0) / 1000.0
        r = [[float(x), float(y)] for x, y in xy]
        if r[0] != r[-1]:
            r.append(r[0])
        rings.append(r)
    return rings


def build_neighbours(shp: Path | None) -> tuple[list[dict], list[dict]]:
    # Fixed label points (SPEC 5.1), checked to fall inside each country. Written even without the shapefile.
    fixed = {"Nepal": (84.1, 28.3), "Bangladesh": (90.2, 23.9), "Pakistan": (69.4, 29.3), "China": (86.0, 32.0),
             "Bhutan": (90.45, 27.45), "Myanmar": (95.8, 21.8), "Sri Lanka": (80.7, 7.75), "Afghanistan": (65.5, 33.5)}
    fixed_labels = [{"name": n, "lon": x, "lat": y} for n, (x, y) in fixed.items()]
    if shp is None or not shp.exists():
        print("  neighbours: shapefile not found, neighbours.json left as it is (pass --ne-shp to rebuild it)")
        return [], fixed_labels
    polys = read_shp_polygons(shp)
    rows = read_dbf(shp.with_suffix(".dbf"), ["ADM0_A3", "NAME"])
    feats = []
    for rec, row in zip(polys, rows):
        code = row.get("ADM0_A3")
        if code not in NEIGHBOURS:
            continue
        rings = []
        for r in rec:
            c = clip_ring(r, CLIP)
            if len(c) < 4:
                continue
            s = dp(c, 0.02)
            if len(s) < 4:
                continue
            if not np.allclose(s[0], s[-1]):
                s = np.vstack([s, s[:1]])
            area = 0.5 * abs(np.dot(s[:-1, 0], s[1:, 1]) - np.dot(s[1:, 0], s[:-1, 1]))
            if area < 0.01:
                continue
            rings.append([[round(float(x), 3), round(float(y), 3)] for x, y in s])
        if not rings:
            continue
        feats.append({"name": NEIGHBOURS[code], "code": code, "rings": rings})
    return feats, fixed_labels


def post_office_points() -> np.ndarray:
    po = pd.read_parquet(BUNDLE / "post_offices.parquet", columns=["lat", "lon", "placeholder"])
    po = po[~po.placeholder.astype(bool)].dropna(subset=["lat", "lon"])
    vc = po.lat.value_counts()
    po = po[~po.lat.isin(vc[vc >= ARTEFACT_N].index)]
    po = po[(po.lon >= LON0) & (po.lon <= LON1) & (po.lat >= LAT0) & (po.lat <= LAT1)]
    qx = np.round((po.lon.values - LON0) / (LON1 - LON0) * 65535).astype(np.uint16)
    qy = np.round((po.lat.values - LAT0) / (LAT1 - LAT0) * 65535).astype(np.uint16)
    q = np.unique(np.stack([qx, qy], axis=1), axis=0)
    return q


def build_districts_and_towns(states_geo: dict) -> tuple[list[dict], list[list]]:
    p = pd.read_parquet(BUNDLE / "places.parquet", columns=["kind", "name", "state", "district", "lat", "lon",
                                                             "n_offices", "precision_km"])
    d = p[p.kind == "district"].dropna(subset=["lat", "lon"])
    districts = [{"name": title(r.name), "state": title(r.state), "lon": round(float(r.lon), 3),
                  "lat": round(float(r.lat), 3)} for r in d.itertuples()]
    t = p[p.kind.isin(["post_office", "city"])].dropna(subset=["lat", "lon"]).copy()
    vc = t.lat.round(6).value_counts()
    t = t[~t.lat.round(6).isin(vc[vc >= 20].index)]
    t = t[t.name.str.len().between(3, 22) & ~t.name.str.contains(r"\d", regex=True)]
    t["rank"] = np.where(t.kind == "city", 1000, t.n_offices.fillna(1))
    t["prec"] = t.precision_km.fillna(99)
    t["cx"] = np.floor(t.lon / TOWN_CELL_DEG).astype(int)
    t["cy"] = np.floor(t.lat / TOWN_CELL_DEG).astype(int)
    t = t.sort_values(["rank", "prec"], ascending=[False, True]).drop_duplicates(["cx", "cy"])
    towns = [[title(r.name), round(float(r.lon), 3), round(float(r.lat), 3), int(min(r.rank, 1000))]
             for r in t.itertuples()]
    return districts, towns


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ne-shp", type=Path, default=None,
                    help="path to ne_10m_admin_0_countries_ind.shp (with its .dbf) for the neighbours")
    a = ap.parse_args()
    states_geo, state_labels = build_states()
    outline = build_outline()
    neigh, country_labels = build_neighbours(a.ne_shp)
    q = post_office_points()
    districts, towns = build_districts_and_towns(states_geo)
    labels = {"states": state_labels, "districts": districts, "countries": country_labels,
              "seas": [{"name": n, "lon": x, "lat": y} for n, x, y in SEAS], "towns": towns,
              "town_cell_deg": TOWN_CELL_DEG}

    def dump(name: str, obj) -> None:
        (HERE / name).write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))

    dump("india_states.json", states_geo)
    dump("india_outline.json", {"rings": outline, "source": "vf.locate._INDIA_OUTLINE_B85 (Natural Earth 1:10m "
                                                            "admin-0, India point of view, public domain)"})
    if neigh:
        dump("neighbours.json", {"countries": neigh, "source": "Natural Earth 1:10m admin-0 countries, India point "
                                                                "of view (public domain), clipped and simplified"})
    (HERE / "po_points.bin").write_bytes(q.astype("<u2").tobytes())
    dump("labels_india.json", labels)
    total = 0
    for f in ["india_states.json", "india_outline.json", "neighbours.json", "po_points.bin", "labels_india.json"]:
        fp = HERE / f
        if fp.exists():
            total += fp.stat().st_size
            print(f"  {f:22s} {fp.stat().st_size / 1024:8.1f} KB")
    print(f"  post office points {len(q):,}; districts {len(districts)}; towns {len(towns)}; "
          f"neighbours {len(neigh)}; states {len(state_labels)}")
    print(f"  total {total / 1024 / 1024:.2f} MB (limit 1.5 MB)")
    if total > 1.5 * 1024 * 1024:
        sys.exit("over the 1.5 MB budget")


if __name__ == "__main__":
    main()
