"""heaven3d-packages: get the big asset packs for a pip-installed Heaven3D.

The package on PyPI holds the engine, the editor, the Hub, the templates and
the small code packages.  The four asset packs (HQ FPS Weapons, MakeHuman
Characters, Tropical Islands, FarScream: about 450 MB together) are over
PyPI's file size limit, so they come from GitHub:

    heaven3d-packages list                 what there is, what is installed
    heaven3d-packages install all          download and install all four
    heaven3d-packages install farscream    one or more by id (and what they need)

They go into your package library (~/.heaven3d/packages), exactly as the Hub's
Packages > Install does, so the Hub, the editor and the templates find them.

Where from: the .h3dpkg files attached to the GitHub release of your engine
version (one download per pack).  If that release has none (an engine
installed from main), the repository archive is read as a stream instead and
only the asked-for folders are written to disk.  --ref TAG picks another
release / branch.
"""
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

from . import engine_dir

REPO = "lolgg313/Heaven3D"
ASSET_PACKAGES = {
    "hqfps_weapons": "HQ FPS Weapons: 20 first-person weapons with animations and sounds (about 200 MB)",
    "mh_characters": "MakeHuman Characters: people, Character Creator, NPCs and enemies (about 80 MB)",
    "tropical_islands": "Tropical Islands: one-click procedural islands (about 75 MB)",
    "farscream": "FarScream: open-island shooter map generator (about 100 MB; needs the three above)",
}
# known requirements, so everything comes in one go (package.json is read afterwards too)
REQUIRES = {"farscream": ["tropical_islands", "hqfps_weapons", "mh_characters"]}


def _hub():
    import importlib.util
    path = os.path.join(engine_dir(), "Heaven3D_Hub.py")
    spec = importlib.util.spec_from_file_location("heaven3d_hub_lib", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _version():
    with open(os.path.join(engine_dir(), "Heaven3D.py"), encoding="utf-8") as fh:
        m = re.search(r'^HEAVEN_VERSION\s*=\s*"([^"]+)"', fh.read(), re.M)
    return m.group(1) if m else None


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "heaven3d-packages"})
    return urllib.request.urlopen(req, timeout=timeout)


class _Progress:
    def __init__(self, f, label):
        self.f, self.n, self.shown, self.label = f, 0, 0, label

    def read(self, size=-1):
        b = self.f.read(size)
        self.n += len(b)
        if self.n - self.shown > (25 << 20):
            self.shown = self.n
            print("  %s: %d MB" % (self.label, self.n >> 20), flush=True)
        return b


# ----------------------------------------------------------------- release packs
def release_assets(ref):
    """{pack id: download url} of the .h3dpkg files attached to the release tagged `ref`."""
    try:
        with _get("https://api.github.com/repos/%s/releases/tags/%s" % (REPO, ref)) as r:
            rel = json.load(r)
    except (urllib.error.URLError, ValueError, OSError):
        return {}
    out = {}
    for a in rel.get("assets", []):
        m = re.match(r"(.+?)-[0-9][^-]*\.h3dpkg$", a.get("name", ""))
        if m:
            out[m.group(1)] = a["browser_download_url"]
    return out


def _download_file(url, dst, label):
    with _get(url, timeout=120) as r, open(dst, "wb") as fh:
        shutil.copyfileobj(_Progress(r, label), fh, 1 << 20)
    return dst


# ----------------------------------------------------------------- repository archive
def from_archive(ids, ref, out):
    """Extract packages/<id>/ of the repository archive at `ref` into `out`.  -> {id: folder}"""
    url = "https://codeload.github.com/%s/tar.gz/%s" % (REPO, ref)
    found, want = {}, set(ids)
    root = os.path.abspath(out)
    with _get(url, timeout=120) as resp:
        print("Downloading the %s archive @ %s (only the packs are kept) ..." % (REPO, ref), flush=True)
        with tarfile.open(fileobj=_Progress(resp, "archive"), mode="r|gz") as tar:
            for m in tar:
                parts = m.name.split("/")
                if len(parts) < 3 or parts[1] != "packages" or parts[2] not in want:
                    continue
                if "__pycache__" in parts or ".." in parts or m.issym() or m.islnk():
                    continue
                dst = os.path.join(out, *parts[2:])
                if not os.path.abspath(dst).startswith(root + os.sep):
                    continue
                if m.isdir():
                    os.makedirs(dst, exist_ok=True)
                elif m.isfile():
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as fh:
                        shutil.copyfileobj(tar.extractfile(m), fh)
                    found[parts[2]] = os.path.join(out, parts[2])
    return found


def fetch(ids, ref, out):
    """{id: .h3dpkg file or folder} for the asked-for packs."""
    refs = [ref] if ref else ["v%s" % _version(), "main"]
    for r in refs:
        assets = release_assets(r) if r != "main" else {}
        if assets and all(i in assets for i in ids):
            got = {}
            for pid in ids:
                print("Downloading %s from release %s ..." % (pid, r), flush=True)
                got[pid] = _download_file(assets[pid], os.path.join(out, pid + ".h3dpkg"), pid)
            return got
    for k, r in enumerate(refs):              # no release packs: the archive of the release, else main
        try:
            return from_archive(ids, r, out)
        except urllib.error.HTTPError as ex:
            if ex.code != 404 or k == len(refs) - 1:
                raise
    return {}


def install(ids, ref=None):
    hub = _hub()
    tmp = tempfile.mkdtemp(prefix="h3dpkgs_")
    try:
        todo = list(dict.fromkeys(ids))
        todo += [r for pid in list(todo) for r in REQUIRES.get(pid, [])
                 if r not in todo and hub.find_package(r) is None]
        todo = list(dict.fromkeys(todo))
        got = fetch(todo, ref, tmp)
        done = []
        for pid in todo:
            if pid in got:
                m = hub.install_package(got[pid], enable=True)
                done.append(m.get("id", pid))
                print("installed %s %s -> %s" % (m.get("name", pid), m.get("version", ""), m.get("dir", "")),
                      flush=True)
        need = []                               # anything else their package.json asks for
        for pid in done:
            m = hub.find_package(pid)
            need += [r for r in (m or {}).get("requires", []) or [] if hub.find_package(r) is None]
        need = list(dict.fromkeys(need))
        if need:
            print("Also getting what they need: %s" % ", ".join(need), flush=True)
            return install(need, ref)
        missing = [p for p in todo if p not in got]
        if missing:
            print("not found: %s" % ", ".join(missing), flush=True)
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ref = None
    if "--ref" in argv:
        i = argv.index("--ref")
        ref = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    cmd = argv[0] if argv else "list"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "list":
        hub = _hub()
        have = {m["id"]: m for m in hub.library_packages() + hub.bundled_packages()}
        print("Asset packs (heaven3d-packages install <id> | all):")
        for pid, desc in ASSET_PACKAGES.items():
            mark = "installed %s" % have[pid].get("version", "") if pid in have else "not installed"
            print("  %-17s %-18s %s" % (pid, mark, desc))
        small = [m["id"] for m in hub.bundled_packages() if m["id"] not in ASSET_PACKAGES]
        if small:
            print("Included with the engine: %s" % ", ".join(small))
        return 0
    if cmd == "install":
        ids = argv[1:] or ["all"]
        if "all" in ids:
            ids = list(ASSET_PACKAGES)
        return install(ids, ref)
    print("unknown command %r (list, install)" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
