"""Headless checks for the weapon skinning runtime -- no GPU, no engine.

Run:  python packages/hqfps_weapons/tests/test_skin.py
"""
import glob
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)

from hq_skin import SkinnedModel, quat_slerp, quat_to_mat4  # noqa: E402

MODELS = os.path.join(PKG, "assets", "models")
I4 = np.eye(4)
fails = []


def check(cond, label):
    if not cond:
        fails.append(label)
    print("  %-58s %s" % (label, "ok" if cond else "FAIL"))


def test_quat():
    print("quaternion math")
    q = np.array([0.0, 0.0, 0.0, 1.0])
    check(np.abs(quat_to_mat4(q) - I4).max() < 1e-12, "identity quat -> identity matrix")
    a = np.array([0.0, 0.0, 0.0, 1.0])
    # build the target exactly: a 90-degree turn about +Y
    h = np.sqrt(0.5)
    b = np.array([0.0, h, 0.0, h])
    check(np.abs(quat_slerp(a, b, 0.0) - a).max() < 1e-12, "slerp(t=0) == a")
    check(np.abs(quat_slerp(a, b, 1.0) - b).max() < 1e-12, "slerp(t=1) == b")
    mid = quat_slerp(a, b, 0.5)
    check(abs(np.linalg.norm(mid) - 1.0) < 1e-12, "slerp midpoint stays unit length")
    # halfway between 0 and 90 degrees about +Y is 45 degrees
    want = np.array([0.0, np.sin(np.pi / 8), 0.0, np.cos(np.pi / 8)])
    check(np.abs(mid - want).max() < 1e-12, "slerp midpoint is the 45-degree rotation")
    # shortest arc: a negated target represents the same rotation, so the
    # result must match the un-negated case rather than taking the long way
    far = quat_slerp(a, -b, 0.5)
    check(abs(np.linalg.norm(far) - 1.0) < 1e-12, "slerp handles opposite-sign quats")
    check(np.abs(far - mid).max() < 1e-12, "slerp takes the shortest arc")


def test_models():
    files = sorted(glob.glob(os.path.join(MODELS, "*.glb")))
    print("\n%d weapon models" % len(files))
    check(len(files) > 0, "models present")
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        m = SkinnedModel(f)
        # bind pose must reconstruct to identity -- validates inverse-bind,
        # node hierarchy, TRS composition and FK all at once
        err = np.abs(m.palette(None, 0.0) - I4).max()
        ok_rest = err < 1e-3
        ok_clips = True
        for cn, c in m.clips.items():
            for frac in (0.0, 0.5, 1.0):
                P = m.palette(cn, c.duration * frac)
                if not np.isfinite(P).all():
                    ok_clips = False
                    break
                d = np.linalg.det(P[:, :3, :3])
                if d.min() < 0.3 or d.max() > 3.0:
                    ok_clips = False
                    break
        check(ok_rest and ok_clips and len(m.clips) > 0,
              "%-22s %d bones, %d clips, rest_err=%.1e" % (name, len(m.joints), len(m.clips), err))


def test_sampling():
    print("\nclip sampling")
    f = os.path.join(MODELS, "M1911.glb")
    if not os.path.exists(f):
        check(False, "M1911.glb present")
        return
    m = SkinnedModel(f)
    c = m.clips["Fire"]
    check(c.duration > 0.0, "Fire clip has non-zero duration")
    # clamping outside the range must not explode
    for t in (-5.0, 0.0, c.duration, c.duration + 5.0):
        P = m.palette("Fire", t)
        if not np.isfinite(P).all():
            check(False, "palette finite at t=%.1f" % t)
            return
    check(True, "palette finite outside clip range (clamped)")
    a = m.palette("Fire", 0.0)
    b = m.palette("Fire", c.duration * 0.5)
    check(np.abs(a - b).max() > 1e-4, "clip actually animates")


if __name__ == "__main__":
    test_quat()
    test_models()
    test_sampling()
    print()
    if fails:
        print("%d FAILED:" % len(fails))
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all checks passed")
