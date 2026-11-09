"""Exported games carry Vitrail (no GPU needed):

    python tests/test_export_vitrail.py

  * a Linux and a Windows export each get engine/vitrail/ next to
    engine/Heaven3D.py, file-for-file the same as the engine's own copy,
    without __pycache__, and inside the zip too
  * the editor (h3d_editor) is not shipped with games
  * exporting from an install that lost its vitrail folder stops with a
    clear message instead of making a game that would break later
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
P = H.H3DPortable
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def fake_engine(tmp, with_vitrail=True):
    ed = os.path.join(tmp, "engine_install")
    os.makedirs(ed)
    shutil.copy2(os.path.join(ROOT, "Heaven3D.py"), ed)
    if with_vitrail:
        shutil.copytree(os.path.join(ROOT, "vitrail"), os.path.join(ed, "vitrail"))
        os.makedirs(os.path.join(ed, "vitrail", "__pycache__"), exist_ok=True)
        open(os.path.join(ed, "vitrail", "__pycache__", "junk.pyc"), "wb").close()
    shutil.copytree(os.path.join(ROOT, "h3d_editor"), os.path.join(ed, "h3d_editor"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for plat in ("linux", "windows"):          # tiny stand-ins for the bundled Pythons
        d = os.path.join(ed, "runtime", "players", plat)
        os.makedirs(os.path.join(d, "Lib"))
        with open(os.path.join(d, "HEAVEN3D_PLATFORM"), "w") as f:
            f.write(plat)
    os.makedirs(os.path.join(ed, "runtime", "launchers"))
    open(os.path.join(ed, "runtime", "launchers", "heaven3d-game.exe"), "wb").write(b"MZ")
    return ed


def main():
    tmp = tempfile.mkdtemp(prefix="h3dexp_")
    try:
        proj = os.path.join(tmp, "MyGame")
        shutil.copytree(os.path.join(ROOT, "templates", "coin_collector"), proj)
        ed = fake_engine(tmp)
        out = os.path.join(tmp, "out")
        res_ = P.export_game(os.path.join(proj, "main.heaven3d"), out, ["linux", "windows"], ed,
                             game_name="MyGame", make_zip=True, log=lambda m: None)
        want = sorted(n for n in os.listdir(os.path.join(ROOT, "vitrail")) if n.endswith(".py"))
        for plat, stage, zp in res_:
            vd = os.path.join(stage, "engine", "vitrail")
            got = sorted(n for n in os.listdir(vd) if n.endswith(".py")) if os.path.isdir(vd) else []
            check(plat + ": engine/vitrail shipped next to Heaven3D.py",
                  got == want and os.path.isfile(os.path.join(stage, "engine", "Heaven3D.py")), len(got))
            same = all(open(os.path.join(vd, n), "rb").read() == open(os.path.join(ROOT, "vitrail", n), "rb").read()
                       for n in got)
            check(plat + ": every vitrail file identical to the engine's", same)
            check(plat + ": licence files kept", all(os.path.isfile(os.path.join(vd, n)) for n in ("LICENSE", "NOTICE")))
            check(plat + ": no __pycache__ shipped", not os.path.exists(os.path.join(vd, "__pycache__")))
            check(plat + ": the editor is not shipped", not os.path.exists(os.path.join(stage, "engine", "h3d_editor")))
            with zipfile.ZipFile(zp) as z:
                names = z.namelist()
            check(plat + ": vitrail inside the zip",
                  sum(1 for n in names if "/engine/vitrail/" in n and n.endswith(".py")) == len(want))
        broken = os.path.join(tmp, "broken")
        os.makedirs(broken)
        ed2 = fake_engine(broken, with_vitrail=False)
        try:
            P.export_game(os.path.join(proj, "main.heaven3d"), os.path.join(tmp, "out2"), ["linux"],
                          ed2, game_name="MyGame", make_zip=False, log=lambda m: None)
            check("export without vitrail is refused", False, "no error")
        except FileNotFoundError as ex:
            check("export without vitrail is refused with a clear message", "vitrail" in str(ex), str(ex)[:80])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
