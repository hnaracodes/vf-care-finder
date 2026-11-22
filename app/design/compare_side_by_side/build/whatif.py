"""What-if frame for Jokihat: an evidence-backed dialysis facility added at Araria post office (user's map click).
Agents follow the 'trust the record's evidence' rule: nearest facility with evidence by estimated minutes."""
import json, math, sys
sys.argv = [sys.argv[0]]
import render_maps as R
import numpy as np
from sim import hav, mins
a = R.load("ans_jok.json"); t = R.load("trip_jok.json"); L = a["location"]; f = a["window"]["factor"]; W = a["window"]["minutes"]
F = [dict(x) for x in t["facilities"]] + [dict(name="What-if facility", lon=87.559361, lat=26.186239, supported=True, kind="whatif")]
ev = [i for i, x in enumerate(F) if x["supported"]]
br = a["beyond_radius"]
c = ((L["lon"] + br["lon"]) / 2, (L["lat"] + br["lat"]) / 2)
v = R.View(87.98, 26.17, 205, 800, 600); lab = R.Labeler(800, 600)
p = R.base_layers(v, dot_r=1.25)
p += R.ring(v, L["lon"], L["lat"], 47, "60 min drive estimate (about 47 km)")
P = np.array(t["points"]); px, py = v.xy(P[:, 0], P[:, 1])
lines = []; dots = []; n_in = 0
for j, q in enumerate(P):
    d = {i: mins(hav(q, (F[i]["lon"], F[i]["lat"])), f) for i in ev}
    i = min(d, key=d.get); inside = d[i] <= W; n_in += inside
    fx, fy = v.xy(F[i]["lon"], F[i]["lat"])
    col = R.TEAL if inside else R.GREY
    lines.append(f'<line x1="{px[j]:.1f}" y1="{py[j]:.1f}" x2="{fx:.1f}" y2="{fy:.1f}" stroke="{col}" stroke-opacity="0.35" stroke-width="1"/>')
    dots.append(f'<circle cx="{px[j]:.1f}" cy="{py[j]:.1f}" r="2.8" fill="{"#fff" if inside else "#d9d4ca"}" stroke="{col}" stroke-width="1.1"/>')
p += lines + dots
x, y = v.xy(87.559361, 26.186239)
p.append(f'<rect x="{x-11:.1f}" y="{y-11:.1f}" width="22" height="22" rx="4" fill="#6b3fa0" stroke="#fff" stroke-width="2.4"/>')
p.append(f'<text x="{x:.1f}" y="{y+5:.1f}" font-size="14" font-weight="800" fill="#fff" text-anchor="middle">+</text>')
p.append(R.text(x + 8, y + 30, "WHAT-IF: dialysis added here", 13, weight=800, fill="#6b3fa0", anchor="end", hw=4))
lab.block(x - 200, y - 14, x + 14, y + 36)
p += R.facility_markers(v, [br], lab=lab, names=True)
p += R.searched(v, L["lon"], L["lat"], "Jokihat", lab=lab, sub="Araria district, Bihar")
sx, sy = v.xy(L["lon"], L["lat"])
p += R.label_layers(v, lab, town_limit=10, district_limit=3, so_limit=3, exclude=("Jokihat",), center=(sx, sy))
p += R.state_side_labels(v, [("BIHAR", 87.05, 26.0), ("WEST BENGAL", 87.95, 26.08), ("NEPAL", 87.55, 26.36)])
p += R.scalebar(v, 20) + R.north(v)
(R.OUT / "map_jok_whatif.svg").write_text(R.svg(v, p))
print("villages inside window after what-if:", n_in, "of", len(P))
