"""The pip package (no GPU needed):   python tests/test_pip_package.py

  * pyproject.toml / setup.py take the version from the engine itself
  * `import heaven3d` loads nothing heavy; the engine is found next to the
    package (this repository, or heaven3d/_engine once installed)
  * every file and folder the installed engine needs is in the list setup.py copies
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


setup_py, pyproject = read("setup.py"), read("pyproject.toml")
ver = re.search(r'^HEAVEN_VERSION\s*=\s*"([^"]+)"', read("Heaven3D.py"), re.M).group(1)
ns = {"__file__": os.path.join(ROOT, "setup.py"), "__name__": "setup_check"}
exec(compile(setup_py.split("setup(version=")[0], "setup.py", "exec"), ns)
check("setup.py reads the version from Heaven3D.py", ns["version"]() == ver, ver)
check("pyproject.toml: version is dynamic, three commands", 'dynamic = ["version"]' in pyproject
      and all(c in pyproject for c in ("heaven3d._cli:editor", "heaven3d._cli:runtime", "heaven3d._cli:hub")))
missing = [n for n in ns["FILES"] + ns["FOLDERS"] if not os.path.exists(os.path.join(ROOT, n))]
check("everything setup.py copies exists", not missing, missing)
need = {"h3d_editor", "vitrail", "templates", "branding", "tutorials", "packages"}
check("the editor, templates, packages ... are all in the package", need <= set(ns["FOLDERS"]))

import heaven3d  # noqa: E402
check("import heaven3d loads no window library", "pygame" not in sys.modules and "moderngl" not in sys.modules)
check("the engine is found next to the package", os.path.samefile(heaven3d.engine_dir(), ROOT)
      and os.path.isfile(heaven3d.engine_file("Heaven3D_Hub.py")), heaven3d.engine_dir())
from heaven3d import _cli  # noqa: E402
check("the commands exist", all(callable(getattr(_cli, n)) for n in ("editor", "runtime", "hub")))
try:
    heaven3d.__nothing_like_this__
    ok = False
except AttributeError:
    ok = True
check("unknown dunder names don't load the engine", ok and "pygame" not in sys.modules)

print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
