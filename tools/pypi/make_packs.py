#!/usr/bin/env python3
"""Write the big asset packs as .h3dpkg files (they are left out of the PyPI package).

    python tools/pypi/make_packs.py --out dist/packs

Users install them with  heaven3d-hub --install-package <file>.h3dpkg
(or Packages > Install in the Hub).  Uses the Hub's own export_package().
"""
import argparse
import importlib.util
import os
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ASSET_PACKS = ("hqfps_weapons", "mh_characters", "tropical_islands", "farscream")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "dist", "packs"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp()   # don't touch the real library
    sys.argv = [sys.argv[0]]
    spec = importlib.util.spec_from_file_location("h3d_hub", os.path.join(REPO, "Heaven3D_Hub.py"))
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    for pid in ASSET_PACKS:
        m = hub.find_package(pid)
        if m is None:
            raise SystemExit(f"package {pid} not found")
        out = os.path.join(args.out, f"{pid}-{m['version']}.h3dpkg")
        hub.export_package(pid, out)
        print(f"{out}  {os.path.getsize(out) / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
