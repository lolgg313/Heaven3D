"""The first-person arms never show their hollow inside (no GPU needed):

    python tests/test_hqfps_arms.py

For every weapon, every animation clip and 16 moments in each, the arms are
skinned exactly as the viewmodel shows them and checked for the two ways
you could see into the arm:

  * an OPEN EDGE of the arm mesh inside the camera's view;
  * the camera's near plane SLICING through the arm inside the view.

Both must never happen with the arms the package actually uses (cut
continued and capped by hq_arms) and the near plane it plays with.
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "packages", "hqfps_weapons")
sys.path.insert(0, PKG)
import hq_arms  # noqa: E402
import hq_skin  # noqa: E402

OX, OY, OZ = (0.0, -1.6, 0.33)           # engine.HAND_OFFSET
VFOV, ASPECT = 60.0, 16.0 / 9.0          # the camera the arena plays with (widest view tested)
NEAR = float(os.environ.get("H3D_NEAR", "0.03"))   # engine.VIEWMODEL_NEAR
TY = math.tan(math.radians(VFOV) / 2.0)
TX = TY * ASPECT
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def skin_all(pr, P):
    v, j, w = pr["positions"], pr["joints"].astype(int), pr["weights"]
    out = np.zeros_like(v)
    for k in range(4):
        M = P[j[:, k]]
        out += w[:, k:k + 1] * (np.einsum("nij,nj->ni", M[:, :3, :3], v) + M[:, :3, 3])
    # rig space -> camera space (+right, +up, +forward), as Viewmodel.update
    return np.stack([-out[:, 0] + OX, out[:, 1] + OY, out[:, 2] + OZ], 1)


def on_screen(cs, near):
    f = cs[:, 2]
    return (f > near) & (np.abs(cs[:, 0]) < f * TX) & (np.abs(cs[:, 1]) < f * TY)


def slices(cs, tri, near):
    f = cs[:, 2]
    n = 0
    for a, b in ((0, 1), (1, 2), (2, 0)):
        p, q = tri[:, a], tri[:, b]
        cross = (f[p] - near) * (f[q] - near) < 0
        if cross.any():
            t = (near - f[p[cross]]) / (f[q[cross]] - f[p[cross]])
            pt = cs[p[cross]] + (cs[q[cross]] - cs[p[cross]]) * t[:, None]
            n += int(((np.abs(pt[:, 0]) < near * TX) & (np.abs(pt[:, 1]) < near * TY)).sum())
    return n


def main():
    models = os.path.join(PKG, "assets", "models")
    names = sorted(f[:-4] for f in os.listdir(models) if f.endswith(".glb"))
    if os.environ.get("H3D_WEAPONS"):
        names = os.environ["H3D_WEAPONS"].split(",")
    worst_before = 0
    for name in names:
        m = hq_skin.SkinnedModel(os.path.join(models, name + ".glb"))
        raw = [p for p in m.primitives() if hq_arms.is_arm(p)]
        fixed, closed, root = hq_arms.close_arms(m, raw, (OX, OY, OZ))
        cut_loops = [[l for l in hq_arms.open_loops(p)
                      if np.ptp(p["positions"][l], axis=0).max() >= hq_arms.CUT_MIN_SIZE] for p in fixed]
        open_seen = slice_seen = before = 0
        samples = 0
        for clip in sorted(m.clips):
            for t in np.linspace(0.0, m.clips[clip].duration, 16):
                P = hq_arms.palette_with_root(m, root, clip, t)
                samples += 1
                for p0, p, loops in zip(raw, fixed, cut_loops):
                    cs0 = skin_all(p0, P)
                    big = [l for l in hq_arms.open_loops(p0)
                           if np.ptp(p0["positions"][l], axis=0).max() >= hq_arms.CUT_MIN_SIZE]
                    if any(on_screen(cs0[l], 0.1).any() for l in big) or slices(cs0, p0["indices"].reshape(-1, 3), 0.1):
                        before += 1
                    cs = skin_all(p, P)
                    if any(on_screen(cs[l], NEAR).any() for l in loops):
                        open_seen += 1
                    if slices(cs, p["indices"].reshape(-1, 3), NEAR):
                        slice_seen += 1
        worst_before += before
        check("%-20s arms closed (%d cut%s), never open or sliced on screen in %d frames"
              % (name, closed, "" if closed == 1 else "s", samples),
              closed >= 1 and open_seen == 0 and slice_seen == 0,
              "before the fix: hollow visible in %d frame-arm pairs; now open %d, sliced %d"
              % (before, open_seen, slice_seen))
    print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
