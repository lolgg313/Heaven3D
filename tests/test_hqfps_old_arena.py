"""An HQ FPS Arena project made BEFORE the guns and ammo were scene objects
(old scene, old arena script) gets them the first time it's opened in the
editor, and they're kept once saved (needs a GPU):

    python tests/test_hqfps_old_arena.py  path/to/old/main.heaven3d  [old/scripts/hqfps_arena.py]
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def phase(project, label):
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    import pygame
    eng = H.HeavenEngine(mode="editor", width=960, height=540, project_path=os.path.join(project, "main.heaven3d"))
    st = {"f": 0}
    flip = pygame.display.flip

    def bps(cls):
        return [b for n in eng.scene.root.traverse() for b in n.blueprints if b.__class__.__name__ == cls]

    def driver():
        st["f"] += 1
        if st["f"] == 20:
            picks, boxes = bps("HQWeaponPickup"), bps("HQAmmoPickup")
            arena = bps("HQFPSArena")
            check("%s: the old arena has its guns and ammo boxes, with models" % label,
                  len(picks) >= 10 and len(boxes) >= 10 and all(p.parts for p in picks + boxes),
                  "%d guns, %d boxes" % (len(picks), len(boxes)))
            check("%s: the player starts empty-handed" % label,
                  arena and arena[0].properties.get("weapons") == "none")
            if label == "first open":
                eng.save_project()
            os._exit(0 if all(res) else 1)
        flip()
    pygame.display.flip = driver
    eng.run()


def main():
    import subprocess
    old_scene = sys.argv[1]
    proj = tempfile.mkdtemp(prefix="h3d_oldarena_")
    shutil.copy(old_scene, os.path.join(proj, "main.heaven3d"))
    os.makedirs(os.path.join(proj, "scripts"))
    script = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "templates", "hqfps_arena", "scripts", "hqfps_arena.py")
    shutil.copy(script, os.path.join(proj, "scripts", "hqfps_arena.py"))
    for p in ("hqfps_weapons", "mh_characters"):
        shutil.copytree(os.path.join(ROOT, "packages", p), os.path.join(proj, "packages", p),
                        ignore=shutil.ignore_patterns("tools", "tests", "__pycache__"))
    ok = True
    for label in ("first open", "after saving"):
        r = subprocess.run([sys.executable, __file__, "--phase", proj, label], capture_output=True, text=True)
        print("\n".join(l for l in r.stdout.splitlines() if l.startswith(("PASS", "FAIL"))), flush=True)
        ok = ok and r.returncode == 0
    import json
    with open(os.path.join(proj, "main.heaven3d"), encoding="utf-8") as fh:
        scene = json.load(fh)["scene"]
    n = sum(1 for c in scene.get("children", [])
            if any(b.get("class") == "HQWeaponPickup" for b in c.get("blueprints", [])))
    check("the saved project keeps exactly one set of pickups", n == 12, "%d weapon pickups saved" % n)
    shutil.rmtree(proj, ignore_errors=True)
    print("\nRESULT", "PASS" if ok and all(res) else "FAIL", flush=True)
    return 0 if ok and all(res) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--phase":
        phase(sys.argv[2], sys.argv[3])
    else:
        sys.exit(main())
