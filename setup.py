"""Builds the pip package (the settings are in pyproject.toml).

The engine finds its editor, templates and packages NEXT TO Heaven3D.py, and
an exported game is a copy of those files.  So the wheel keeps them together
exactly as in this repository, in  heaven3d/_engine/  inside site-packages:

    Heaven3D.py  Heaven3D_runtime.py  Heaven3D_Hub.py  Play_HQFPS_Arena.py
    h3d_editor/  vitrail/  templates/  branding/  tutorials/  packages/

HEAVEN3D_PIP_LITE=1 leaves the big asset packs (about 400 MB) out; the small
code packages stay in.  This is the build published on PyPI (PyPI allows 100 MB
per file).  The asset packs are .h3dpkg files on the GitHub Releases page:
    heaven3d-hub --install-package hqfps_weapons-0.4.1.h3dpkg
    HEAVEN3D_PIP_LITE=1 pip install git+https://github.com/lolgg313/Heaven3D
"""
import os
import re
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ("Heaven3D.py", "Heaven3D_runtime.py", "Heaven3D_Hub.py", "Heaven3D_Hub.pyw", "Play_HQFPS_Arena.py",
         "LICENSE")
FOLDERS = ("h3d_editor", "vitrail", "templates", "branding", "tutorials", "packages")
ASSET_PACKS = ("farscream", "hqfps_weapons", "mh_characters", "tropical_islands")   # left out of the lite build
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.heaven3d.bak", ".heaven3d_logs")


def version():
    with open(os.path.join(HERE, "Heaven3D.py"), encoding="utf-8") as fh:
        m = re.search(r'^HEAVEN_VERSION\s*=\s*"([^"]+)"', fh.read(), re.M)
    if not m:
        raise SystemExit("setup.py: HEAVEN_VERSION not found in Heaven3D.py")
    return m.group(1)


class BuildWithEngine(build_py):
    def run(self):
        super().run()
        if getattr(self, "editable_mode", False):
            return                      # pip install -e: the package reads this folder directly
        dst = os.path.join(self.build_lib, "heaven3d", "_engine")
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(dst)
        lite = os.environ.get("HEAVEN3D_PIP_LITE", "") in ("1", "true", "yes")
        for name in FILES:
            shutil.copy2(os.path.join(HERE, name), os.path.join(dst, name))
        for name in FOLDERS:
            src = os.path.join(HERE, name)
            if not os.path.isdir(src):
                continue
            ignore = SKIP
            if lite and name == "packages":
                def ignore(d, names, _src=src):
                    out = set(SKIP(d, names))
                    if os.path.abspath(d) == os.path.abspath(_src):
                        out |= {n for n in names if n in ASSET_PACKS}
                    return out
            shutil.copytree(src, os.path.join(dst, name), ignore=ignore)


setup(version=version(), cmdclass={"build_py": BuildWithEngine})
