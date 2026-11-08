"""The single-file engine is built from src/ (no GPU needed):

    python tests/test_build.py

  * Heaven3D.py and Heaven3D_runtime.py match what build.py makes from src/
  * every part listed in src/BUILD_ORDER exists, and every part file is listed
  * the indented parts survive a dedent / indent round trip
  * both built files compile, and the runtime file starts in play mode
"""
import importlib.util
import os
import py_compile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def main():
    spec = importlib.util.spec_from_file_location("h3d_build", os.path.join(ROOT, "build.py"))
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    listed = [p for p, _i in b.parts()]
    on_disk = sorted(os.path.relpath(os.path.join(dp, f), b.SRC).replace(os.sep, "/")
                     for dp, _d, fs in os.walk(b.SRC) for f in fs if f.endswith(".py"))
    check("every part in BUILD_ORDER exists", all(os.path.isfile(os.path.join(b.SRC, p)) for p in listed))
    check("every part file is in BUILD_ORDER", sorted(listed) == on_disk,
          "unlisted: %s" % sorted(set(on_disk) - set(listed)))
    check("no part is listed twice", len(listed) == len(set(listed)))
    out = b.build()
    for name, text in out.items():
        with open(os.path.join(ROOT, name), encoding="utf-8", newline="") as fh:
            have = fh.read()
        check("%s is up to date with src/ (run build.py)" % name, have == text,
              "%d vs %d chars" % (len(have), len(text)))
    lines = out["Heaven3D.py"].count("\n") + 1
    check("--where maps the first and last line", b.where(1)[0] == listed[0] and b.where(lines)[0] == listed[-1])
    for name in out:
        try:
            py_compile.compile(os.path.join(ROOT, name), doraise=True,
                               cfile=os.path.join(os.path.dirname(HERE), ".h3d_build_check.pyc"))
            ok = True
        except py_compile.PyCompileError as ex:
            ok, err = False, str(ex)
        check("%s compiles" % name, ok)
    try:
        os.remove(os.path.join(os.path.dirname(HERE), ".h3d_build_check.pyc"))
    except OSError:
        pass
    check("Heaven3D.py starts the editor, Heaven3D_runtime.py the game",
          '_H3D_DEFAULT_MODE = "editor"' in out["Heaven3D.py"]
          and '_H3D_DEFAULT_MODE = "runtime"' in out["Heaven3D_runtime.py"])
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
