import numpy as np, sys, os, time, zlib
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from skimage.graph import MCP_Geometric
S = sys.argv[1]
Fm = np.load(f"{S}/out/friction_india_masked.npy")
ocean = Fm >= 1.0
land = Fm[~ocean]
lo, hi = land.min(), land.max()
q = np.full(Fm.shape, 255, np.uint8)
q[~ocean] = np.round(np.log(land / lo) / np.log(hi / lo) * 254).astype(np.uint8)
deq = np.where(q == 255, 1.0, lo * np.exp(q.astype(np.float32) / 254 * np.log(hi / lo))).astype(np.float32)
err = (deq[~ocean] / land - 1)
print("uint8 log quantization: max rel err %.3f, p99 %.3f" % (np.abs(err).max(), np.quantile(np.abs(err), .99)))
np.savez_compressed(f"{S}/out/friction_u8.npz", q=q, lo=lo, hi=hi)
np.savez_compressed(f"{S}/out/friction_f16.npz", f=Fm.astype(np.float16))
for f in ["friction_u8.npz", "friction_f16.npz"]:
    print(f, round(os.path.getsize(f"{S}/out/{f}") / 1e6, 2), "MB")
# effect of quantization on travel times from Jaipur
H = 927.67; LAT0, LON0, PX = 37.5, 68.0, 1/120
def run(F, lat, lon, half=3.0):
    r0, c0 = int((LAT0-lat)/PX), int((lon-LON0)/PX); h = int(half/PX)
    crop = F[r0-h:r0+h, c0-h:c0+h] * H
    cum, _ = MCP_Geometric(crop, sampling=(1.0, np.cos(np.radians(lat)))).find_costs([(h, h)])
    return cum, crop
a, crop = run(Fm, 26.91, 75.79); b, _ = run(deq, 26.91, 75.79)
m = (a > 1) & (a < 300)
print("quantized vs full travel time (cells < 300 min): median rel diff %.4f, max %.4f" % (np.median(np.abs(b[m]/a[m]-1)), np.abs(b[m]/a[m]-1).max()))
# scipy csgraph version on same crop (no new dependency)
def scipy_tt(crop, cosl, limit=None):
    n, m_ = crop.shape; idx = np.arange(n*m_).reshape(n, m_)
    rows, cols, w = [], [], []
    for dr, dc in [(0,1),(1,0),(1,1),(1,-1)]:
        a_ = idx[max(0,-dr):n-max(0,dr), max(0,-dc):m_-max(0,dc)]
        b_ = idx[max(0,dr):, max(0,dc):][:a_.shape[0], :a_.shape[1]] if dc >= 0 else idx[dr:, :m_+dc][:a_.shape[0], :a_.shape[1]]
        L = np.hypot(dr, dc*cosl)
        ww = (crop.ravel()[a_.ravel()] + crop.ravel()[b_.ravel()]) / 2 * L
        rows += [a_.ravel(), b_.ravel()]; cols += [b_.ravel(), a_.ravel()]; w += [ww, ww]
    G = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(n*m_, n*m_)).tocsr()
    return G
t = time.perf_counter(); G = scipy_tt(crop, np.cos(np.radians(26.91))); tb = time.perf_counter() - t
h = crop.shape[0]//2
t = time.perf_counter(); d = dijkstra(G, indices=h*crop.shape[1]+h, limit=240); td = time.perf_counter() - t
d = d.reshape(crop.shape); mm = np.isfinite(d) & (a > 1) & (a < 240)
print(f"scipy csgraph: build {tb*1000:.0f} ms, dijkstra(limit 240 min) {td*1000:.0f} ms; median rel diff vs skimage {np.median(np.abs(d[mm]/a[mm]-1)):.4f}")
