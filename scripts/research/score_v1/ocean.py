import numpy as np, sys
S = sys.argv[1]
F = np.load(f"{S}/out/friction_india.npy")
v, c = np.unique(F, return_counts=True)
o = np.argsort(-c)[:12]
for i in o: print(f"{v[i]:.6f} min/m ({0.06/v[i]:.1f} km/h): {c[i]} px ({c[i]/F.size:.1%})")
m = np.isclose(F, 0.00316, atol=1e-7)
# coarse ascii map of where the constant value is
step = 120
for r in range(0, F.shape[0], step):
    row = ''.join('#' if m[r:r+step, cc:cc+step].mean() > 0.9 else ('+' if m[r:r+step, cc:cc+step].mean() > 0.1 else '.') for cc in range(0, F.shape[1], step))
    print(f"{37.5 - r/120:5.1f} {row}")
