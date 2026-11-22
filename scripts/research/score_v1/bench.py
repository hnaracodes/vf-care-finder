import numpy as np, time, sys
from skimage.graph import MCP_Geometric
S = sys.argv[1]
F = np.load(f"{S}/out/friction_india.npy")  # min per metre
LAT0, LON0, PX = 37.5, 68.0, 1/120
def rc(lat, lon):
    return int((LAT0 - lat) / PX), int((lon - LON0) / PX)
for name, (la, lo) in {"arabian_sea": (15, 70), "bay_bengal": (15, 88), "tibet": (32, 88), "jaipur": (26.91, 75.79), "nh48_highway?": (27.5, 76.6), "delhi": (28.61, 77.21), "ladakh_leh": (34.16, 77.58)}.items():
    r, c = rc(la, lo)
    f = F[r, c]
    print(f"{name:14s} friction {f:.5f} min/m = {0.06/f:.1f} km/h")
H = 927.67  # metres per pixel in latitude
def query(lat, lon, half_deg, max_min):
    r0, c0 = rc(lat, lon)
    h = int(half_deg / PX)
    r1, r2 = max(r0 - h, 0), min(r0 + h, F.shape[0])
    c1, c2 = max(c0 - h, 0), min(c0 + h, F.shape[1])
    crop = F[r1:r2, c1:c2] * H
    t = time.perf_counter()
    m = MCP_Geometric(crop, sampling=(1.0, np.cos(np.radians(lat))))
    cum, _ = m.find_costs([(r0 - r1, c0 - c1)], max_cumulative_cost=max_min)
    dt = time.perf_counter() - t
    return cum, dt, (r1, c1)
for half in [1.0, 2.0, 3.0, 4.0]:
    for mx in [120, 240, 600, None]:
        ts = []
        for _ in range(3):
            cum, dt, _ = query(26.91, 75.79, half, mx)
            ts.append(dt)
        reach = np.isfinite(cum).mean()
        print(f"crop +-{half} deg ({(2*half/PX):.0f}px square), max {mx} min: {min(ts)*1000:.0f} ms, finite frac {reach:.2f}, cells<=120min {np.sum(cum<=120)}")
