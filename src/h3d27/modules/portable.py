# @heaven3d-part h3d27/modules/portable.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ------------------------------------------------ h3d_portable
"""
Portable projects, game save data and game export (stdlib only).

The FILE BURNER rules, built into the engine:

  * Every asset reference in a .heaven3d file is stored RELATIVE to the
    project folder with "/" separators ("assets/imported/car.glb"), so the
    same project opens on Windows and Linux, on any user account, from any
    folder or USB stick.
  * Assets that live OUTSIDE the project (Downloads, Desktop, another
    drive...) are copied into  <project>/assets/imported/  when the project
    is saved — together with their side files (.gltf buffers/textures,
    .obj .mtl + textures, .blend external images).  Files are only copied
    once; identical files are reused, different files with the same name
    get a _1, _2 ... suffix.  File names are made safe for every OS.
  * On load every reference is resolved robustly: either separator style,
    letter case differences (a Windows project on Linux), old home-relative
    and absolute paths from earlier versions, FILE BURNER "media/" output,
    and finally a search by file name inside the project.
  * Project files are written atomically (temp file + rename) and the
    previous version is kept as main.heaven3d.bak.

Game save data (SaveData) lives next to an exported game in  saves/  when
that folder is writable (the game and its saves travel together), otherwise
in the per-user data folder:
    Windows  %APPDATA%\\Heaven3D\\<Game>
    Linux    ~/.local/share/heaven3d/<Game>
    macOS    ~/Library/Application Support/Heaven3D/<Game>
"""
import json
import os
import re
import shutil
import stat
import sys
import time
import zipfile

MEDIA_EXTS = {
    ".glb", ".gltf", ".blend", ".obj", ".egg", ".ply", ".stl", ".fbx", ".dae", ".x", ".prefab", ".vmat",
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp", ".dds", ".hdr",
    ".tif", ".tiff", ".gif", ".ktx2",
    ".wav", ".ogg", ".mp3", ".flac", ".m4a",
}
EMBEDDED = "__embedded__"
IMPORT_DIR = "assets/imported"
SKIP_DIRS = {".git", "__pycache__", ".heaven3d_logs", "saves", ".venv", "venv", "exports",
             ".idea", ".vscode", "node_modules"}
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:(?![\\/])")      # data:, http: ... (not C:\)
_WIN_ABS = re.compile(r"^[a-zA-Z]:[\\/]")
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_WIN_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
_IMG_RE = re.compile(rb"((?://|[A-Za-z]:[\\/]|/)?[^\x00\r\n\"*?<>|]{1,900}?\.(?:png|jpe?g|tga|bmp|webp|exr|hdr|tiff?|dds))\x00",
                     re.IGNORECASE)


# --------------------------------------------------------------------------- basics
def is_media_ref(s):
    if not isinstance(s, str):
        return False
    t = s.strip()
    if not t or t == EMBEDDED or len(t) > 4096 or "\n" in t:
        return False
    if _SCHEME.match(t) and not _WIN_ABS.match(t):
        return False
    return os.path.splitext(t)[1].lower() in MEDIA_EXTS


def to_posix(p):
    return p.replace("\\", "/")


def safe_name(name):
    """A file name that is valid on Windows, Linux and macOS."""
    name = _BAD_CHARS.sub("_", name).replace("/", "_").replace("\\", "_").strip()
    stem, ext = os.path.splitext(name)
    stem = stem.rstrip(". ") or "asset"
    if stem.lower() in _WIN_RESERVED:
        stem += "_"
    return stem + ext.lower()


def inside(path, folder):
    try:
        a = os.path.normcase(os.path.realpath(path))
        b = os.path.normcase(os.path.realpath(folder))
        return os.path.commonpath([a, b]) == b
    except (ValueError, OSError):
        return False


def rel_posix(path, folder):
    return to_posix(os.path.relpath(os.path.abspath(path), os.path.abspath(folder)))


def ci_join(base, parts):
    """Case-insensitive path walk: base + parts -> existing file or None."""
    cur = base
    for i, part in enumerate(parts):
        nxt = os.path.join(cur, part)
        if os.path.exists(nxt):
            cur = nxt
            continue
        try:
            low = part.lower()
            match = next((e for e in os.listdir(cur) if e.lower() == low), None)
        except OSError:
            return None
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur if os.path.isfile(cur) else None


def atomic_write_text(path, text, backup=True):
    """Write text so a crash or power cut never leaves a half-written file."""
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    if backup and os.path.isfile(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    for attempt in range(5):           # Windows: a virus scanner may hold the file briefly
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.15 * (attempt + 1))
    os.replace(tmp, path)


def atomic_write_json(path, data, backup=True):
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False), backup)


# --------------------------------------------------------------------------- resolve
class Resolver:
    """Turns any stored asset reference into an existing file."""

    def __init__(self, project_dir, extra_dirs=()):
        self.project_dir = os.path.abspath(project_dir or os.getcwd())
        self.extra = [os.path.abspath(d) for d in extra_dirs if d]
        self._index = None
        self.home = os.path.expanduser("~")

    def _by_name(self):
        if self._index is None:
            idx = {}
            n = 0
            for base in [self.project_dir] + self.extra:
                for dp, dns, fns in os.walk(base):
                    dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
                    for f in fns:
                        idx.setdefault(f.lower(), []).append(os.path.join(dp, f))
                        n += 1
                    if n > 50000:
                        break
            self._index = idx
        return self._index

    def resolve(self, ref):
        if not isinstance(ref, str) or not ref.strip() or ref == EMBEDDED:
            return None
        raw = ref.strip()
        if raw.lower().startswith("file://"):
            try:
                import urllib.parse
                raw = urllib.parse.unquote(raw[7:])
                if _WIN_ABS.match(raw.lstrip("/")):
                    raw = raw.lstrip("/")
            except Exception:
                pass
        n = to_posix(raw)
        parts = [p for p in n.split("/") if p not in ("", ".")]
        # 1. absolute and present
        for cand in (raw, n):
            if os.path.isabs(cand) and os.path.isfile(cand):
                return os.path.normpath(cand)
        absolute = os.path.isabs(n) or bool(_WIN_ABS.match(n))
        if not absolute:
            # 2. project-relative (any separator, any letter case)
            for base in [self.project_dir] + self.extra:
                cand = os.path.join(base, *parts) if parts else None
                if cand and os.path.isfile(cand):
                    return os.path.normpath(cand)
                hit = ci_join(base, parts) if parts else None
                if hit:
                    return os.path.normpath(hit)
            # 3. Heaven3D <= 2.6 stored paths relative to the user's home
            cand = os.path.join(self.home, *parts) if parts else None
            if cand and os.path.isfile(cand):
                return os.path.normpath(cand)
        # 4. FILE BURNER output: media/<name> next to the project / engine
        base_name = parts[-1] if parts else ""
        if not base_name:
            return None
        for d in [self.project_dir, os.path.dirname(self.project_dir)] + self.extra:
            cand = os.path.join(d, "media", base_name)
            if os.path.isfile(cand):
                return os.path.normpath(cand)
        # 5. last resort: a file with that name anywhere in the project
        hits = self._by_name().get(base_name.lower())
        if hits:
            want = [p.lower() for p in parts]

            def score(h):
                hp = [p.lower() for p in to_posix(h).split("/")]
                k = 0
                while k < min(len(hp), len(want)) and hp[-1 - k] == want[-1 - k]:
                    k += 1
                return k
            return os.path.normpath(max(hits, key=score))
        return None


# --------------------------------------------------------------------------- sidecars
def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _gltf_uris(path):
    try:
        g = json.loads(_read_text(path))
    except (OSError, ValueError):
        return []
    out = []
    for sec in ("images", "buffers"):
        for e in g.get(sec, []) or []:
            u = e.get("uri") if isinstance(e, dict) else None
            if isinstance(u, str) and u and not u.startswith("data:") and not _SCHEME.match(u):
                out.append(u)
    return out


def _obj_refs(path):
    """mtllib targets of an .obj, and texture names of an .mtl."""
    out = []
    try:
        for line in _read_text(path).splitlines():
            t = line.strip().split()
            if not t:
                continue
            k = t[0].lower()
            if k == "mtllib" and len(t) > 1:
                out.append(line.strip()[6:].strip())
            elif k.startswith("map_") or k in ("bump", "norm", "disp", "refl"):
                out.append(t[-1])
    except OSError:
        pass
    return out


def _blend_images(path):
    """External image paths referenced by a .blend (relative '//' ones)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:2] == b"\x1f\x8b" or data[:4] == b"\x28\xb5\x2f\xfd":
            data = _decompress(data, path)                      # noqa: F821 (from h3d_blendfile)
    except Exception:
        return []
    out = []
    for m in _IMG_RE.finditer(data):
        s = m.group(1).decode("utf-8", "replace")
        if s.startswith("//"):
            out.append(s)
    return list(dict.fromkeys(out))


def sidecars(path):
    """[(stored reference, resolved file)] the asset needs next to it."""
    ext = os.path.splitext(path)[1].lower()
    d = os.path.dirname(os.path.abspath(path))
    refs = []
    if ext == ".gltf":
        import urllib.parse
        refs = [(u, urllib.parse.unquote(u)) for u in _gltf_uris(path)]
    elif ext == ".obj":
        for m in _obj_refs(path):
            refs.append((m, m))
            mp = os.path.join(d, *to_posix(m).split("/"))
            if os.path.isfile(mp):
                mdir = os.path.dirname(mp)
                for t in _obj_refs(mp):
                    full = os.path.normpath(os.path.join(mdir, *to_posix(t).split("/")))
                    r = to_posix(os.path.relpath(full, d)) if inside(full, d) else os.path.basename(full)
                    refs.append((r, r))
    elif ext == ".blend":
        refs = [(r, r[2:]) for r in _blend_images(path)]
    elif ext in (".fbx", ".x"):
        refs = list(model_texture_files(path))
    out = []
    for stored, rel in refs:
        p = to_posix(rel)
        cand = os.path.join(d, *[x for x in p.split("/") if x])
        if not os.path.isfile(cand):
            cand = ci_join(d, [x for x in p.split("/") if x]) or os.path.join(d, os.path.basename(p))
        if os.path.isfile(cand):
            out.append((stored, os.path.normpath(cand)))
    return out


# --------------------------------------------------------------------------- collect
class Collector:
    """Copies assets that live outside the project into assets/imported/."""

    def __init__(self, project_dir, log=None, dest_root=None):
        self.project_dir = os.path.abspath(project_dir)
        self.dest_root = os.path.abspath(dest_root or project_dir)
        self.dest = os.path.join(self.dest_root, *IMPORT_DIR.split("/"))
        self.log = log or (lambda m: None)
        self.done = {}            # normcase(src) -> stored relative path
        self.copied = []

    def _same(self, a, b):
        try:
            if os.path.getsize(a) != os.path.getsize(b):
                return False
            import filecmp
            return filecmp.cmp(a, b, shallow=False)
        except OSError:
            return False

    def _place(self, src, folder, name):
        """Copy src to folder/name (deduplicated); returns the final path."""
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(safe_name(name))
        k = 0
        while True:
            cand = os.path.join(folder, f"{stem}{'_' + str(k) if k else ''}{ext}")
            taken = [e for e in os.listdir(folder) if e.lower() == os.path.basename(cand).lower()]
            if not taken:
                shutil.copy2(src, cand)
                self.copied.append((src, cand))
                return cand
            existing = os.path.join(folder, taken[0])
            if self._same(src, existing):
                return existing
            k += 1

    def add(self, src):
        key = os.path.normcase(os.path.abspath(src))
        try:
            st = os.stat(src)
            sig = (key, st.st_size, int(st.st_mtime))
        except OSError:
            sig = (key, 0, 0)
        hit = self.done.get(sig)
        if hit and os.path.isfile(os.path.join(self.dest_root, *hit.split("/"))):
            return hit
        ext = os.path.splitext(src)[1].lower()
        scs = sidecars(src) if ext in (".gltf", ".obj", ".blend", ".fbx", ".x") else []
        if scs:
            # Assets with side files get their own folder so their internal
            # relative references keep working.
            stem = safe_name(os.path.splitext(os.path.basename(src))[0])
            fname = safe_name(os.path.basename(src))
            folder, k = os.path.join(self.dest, stem), 1
            while os.path.isfile(os.path.join(folder, fname)) and not self._same(src, os.path.join(folder, fname)):
                folder, k = os.path.join(self.dest, f"{stem}_{k}"), k + 1
            main = self._place(src, folder, os.path.basename(src))
            rewrite = {}
            src_dir = os.path.dirname(os.path.abspath(src))
            for stored, f in scs:
                if inside(f, src_dir) and ".." not in to_posix(os.path.relpath(f, src_dir)).split("/"):
                    rel = to_posix(os.path.relpath(f, src_dir))
                    sub = os.path.join(folder, *[safe_name(x) for x in rel.split("/")[:-1]])
                    placed = self._place(f, sub, os.path.basename(f))
                else:
                    placed = self._place(f, folder, os.path.basename(f))
                new_ref = rel_posix(placed, folder)
                if new_ref != to_posix(stored):
                    rewrite[stored] = new_ref
            if rewrite:
                self._rewrite(main, ext, rewrite)
        else:
            main = self._place(src, self.dest, os.path.basename(src))
        rel = rel_posix(main, self.dest_root)
        self.done[sig] = rel
        self.log(f"[Portable] copied into the project: {os.path.basename(src)} -> {rel}")
        return rel

    def _rewrite(self, path, ext, mapping):
        try:
            if ext == ".gltf":
                g = json.loads(_read_text(path))
                import urllib.parse
                for sec in ("images", "buffers"):
                    for e in g.get(sec, []) or []:
                        u = e.get("uri") if isinstance(e, dict) else None
                        if u in mapping:
                            e["uri"] = urllib.parse.quote(mapping[u])
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(g, f, indent=1, ensure_ascii=False)
            elif ext == ".obj":
                lines = _read_text(path).splitlines(True)
                for i, ln in enumerate(lines):
                    t = ln.strip()
                    if t.lower().startswith("mtllib") and t[6:].strip() in mapping:
                        lines[i] = "mtllib " + mapping[t[6:].strip()] + "\n"
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write("".join(lines))
            # .blend references are resolved by the importer's search
            # (relative path, then the blend folder) - never edited.
        except Exception as ex:
            self.log(f"[Portable] could not rewrite references in {os.path.basename(path)}: {ex}")


# --------------------------------------------------------------------------- project data
def _walk_strings(node, fn):
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str):
                node[k] = fn(v)
            else:
                _walk_strings(v, fn)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                node[i] = fn(v)
            else:
                _walk_strings(v, fn)
    return node


def make_portable(data, project_dir, log=None, collect=True, resolver=None, collector=None):
    """Rewrite every asset reference in a project dict to a project-relative
    '/' path, copying outside assets into the project.  Returns a report."""
    log = log or (lambda m: None)
    resolver = resolver or Resolver(project_dir)
    collector = collector or (Collector(project_dir, log) if collect else None)
    report = {"relative": 0, "copied": 0, "missing": []}

    def fix(s):
        if not is_media_ref(s):
            return s
        hit = resolver.resolve(s)
        if hit is None:
            report["missing"].append(s)
            return to_posix(s) if not (os.path.isabs(s) or _WIN_ABS.match(s)) else s
        if inside(hit, resolver.project_dir):
            report["relative"] += 1
            return rel_posix(hit, resolver.project_dir)
        if collector is None:
            return hit
        before = len(collector.copied)
        rel = collector.add(hit)
        report["copied"] += len(collector.copied) - before
        return rel

    _walk_strings(data, fix)
    for m in report["missing"][:20]:
        log(f"[Portable] asset not found (kept as is): {m}")
    return report


def resolve_project_data(data, project_dir, log=None, extra_dirs=()):
    """Replace every asset reference with the absolute path of the file it
    points to on THIS machine (unresolvable ones are left unchanged)."""
    log = log or (lambda m: None)
    r = Resolver(project_dir, extra_dirs)
    missing = []

    def fix(s):
        if not is_media_ref(s):
            return s
        hit = r.resolve(s)
        if hit is None:
            missing.append(s)
            return s
        return hit
    _walk_strings(data, fix)
    for m in missing[:20]:
        log(f"[Portable] missing asset: {m}")
    return data


# --------------------------------------------------------------------------- save data
def user_data_root(app="Heaven3D"):
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, app)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", app)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, app.lower())


def _writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, f".write_test_{os.getpid()}")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


class SaveData:
    """Game saves:  eng.saves.save("slot1", {...});  eng.saves.load("slot1").

    Data is JSON, written atomically (never corrupted by a crash), with the
    previous version kept as <slot>.json.bak and used automatically if the
    main file is damaged."""

    def __init__(self, game_name, portable_root=None):
        self.game = safe_name(game_name or "Game") or "Game"
        self.folder = None
        if portable_root and _writable(os.path.join(portable_root, "saves")):
            self.folder = os.path.join(portable_root, "saves")
        else:
            self.folder = os.path.join(user_data_root(), self.game)
            os.makedirs(self.folder, exist_ok=True)

    def path(self, slot="save"):
        return os.path.join(self.folder, safe_name(str(slot)) + ".json")

    def save(self, slot="save", data=None):
        atomic_write_json(self.path(slot), {"heaven3d_save": 1, "game": self.game,
                                            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "data": data})
        return self.path(slot)

    def load(self, slot="save", default=None):
        for p in (self.path(slot), self.path(slot) + ".bak"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return d.get("data", default) if isinstance(d, dict) and "heaven3d_save" in d else d
            except (OSError, ValueError):
                continue
        return default

    def exists(self, slot="save"):
        return os.path.isfile(self.path(slot))

    def delete(self, slot="save"):
        for p in (self.path(slot), self.path(slot) + ".bak"):
            try:
                os.remove(p)
            except OSError:
                pass

    def slots(self):
        try:
            return sorted(f[:-5] for f in os.listdir(self.folder) if f.endswith(".json"))
        except OSError:
            return []


# --------------------------------------------------------------------------- export
PLATFORMS = ("windows", "linux", "macos")
PLATFORM_TITLES = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
_PRUNE_DIRS = {"__pycache__", "test", "tests", "idlelib", "turtledemo", "ensurepip", "lib2to3",
               "pydoc_data", "tkinter", "tcl", "tcl8", "tcl8.6", "tk8.6", "tcl9.0", "tk9.0", "include",
               "Tools", "Doc", "man", "conda-meta", "pkgconfig", "pip", "setuptools", "wheel",
               "_distutils_hack", "venv", "Scripts"}
_PRUNE_FILES = re.compile(r"(^tcl.*\.dll$|^tk.*\.dll$|^_tkinter.*|^libtcl.*|^libtk.*|.*\.a$|.*\.lib$|.*\.pdb$"
                          r"|^distutils-precedence\.pth$)", re.I)


def current_platform():
    return "windows" if sys.platform.startswith("win") else ("macos" if sys.platform == "darwin" else "linux")


def find_player(engine_dir, platform):
    """Folder holding a bundled Python for `platform` (see tools/standalone)."""
    rt = os.path.join(engine_dir, "runtime")
    for cand in (os.path.join(rt, "players", platform), os.path.join(rt, "python")):
        marker = os.path.join(cand, "HEAVEN3D_PLATFORM")
        try:
            with open(marker) as f:
                if f.read().strip() == platform:
                    return cand
        except OSError:
            continue
    return None


def _copy_python(src, dst, log):
    """Copy a bundled Python, leaving out what a game never uses (tests,
    Tcl/Tk, pip, headers ...)."""
    def ignore(d, names):
        out = set()
        for n in names:
            full = os.path.join(d, n)
            if os.path.isdir(full):
                if n in _PRUNE_DIRS:
                    out.add(n)
            elif _PRUNE_FILES.match(n) or n == "HEAVEN3D_PLATFORM":
                out.add(n)
        return out
    shutil.copytree(src, dst, ignore=ignore, symlinks=False)


# --------------------------------------------------------------------------- game icons
ICON_EXTS = (".png", ".jpg", ".jpeg", ".bmp")


def _load_image_square(path):
    """-> ("pil", image) or ("pygame", surface): RGBA, square (padded)."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        return "pil", sq
    except ImportError:
        pass
    import pygame
    surf = pygame.image.load(path)
    w, h = surf.get_size()
    side = max(w, h)
    sq = pygame.Surface((side, side), pygame.SRCALPHA, 32)
    sq.fill((0, 0, 0, 0))
    sq.blit(surf, ((side - w) // 2, (side - h) // 2))
    return "pygame", sq


def icon_png(img, size, limit=None):
    """PNG bytes of the square image at size x size (smaller if `limit` bytes
    is exceeded: palette-quantised first when Pillow is available)."""
    import io
    kind, im = img
    if kind == "pil":
        from PIL import Image
        small = im.resize((size, size), getattr(Image, "LANCZOS", 1))
        buf = io.BytesIO()
        small.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        if limit and len(data) > limit:
            buf = io.BytesIO()
            small.quantize(colors=256, method=getattr(Image, "FASTOCTREE", 2)).save(buf, "PNG", optimize=True)
            data = buf.getvalue()
        return data
    import pygame
    small = pygame.transform.smoothscale(im, (size, size))
    buf = io.BytesIO()
    pygame.image.save(small, buf, "icon.png")
    return buf.getvalue()


def set_exe_icon(exe_path, image_path, log=None):
    """Put a game's own icon into a Heaven3D game launcher (.exe), in place.

    The launcher is built with roomy icon slots (build_standalone.padded_ico),
    so each RT_ICON entry is overwritten by a new PNG of the same size and its
    length fields are updated - no Windows tools needed, works on Linux too.
    Returns the number of icon sizes replaced."""
    import struct
    log = log or (lambda m: None)
    img = _load_image_square(image_path)
    with open(exe_path, "rb") as f:
        pe = bytearray(f.read())
    e_lfanew = struct.unpack_from("<I", pe, 0x3C)[0]
    if pe[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError("not a Windows executable")
    nsec, opt_size = struct.unpack_from("<H", pe, e_lfanew + 6)[0], struct.unpack_from("<H", pe, e_lfanew + 20)[0]
    opt = e_lfanew + 24
    magic = struct.unpack_from("<H", pe, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)
    rsrc_rva = struct.unpack_from("<I", pe, dd + 2 * 8)[0]
    sec = opt + opt_size
    sections = []
    for i in range(nsec):
        o = sec + 40 * i
        vsize, va, rsize, raw = struct.unpack_from("<IIII", pe, o + 8)
        sections.append((va, max(vsize, rsize), raw))

    def off(rva):
        for va, size, raw in sections:
            if va <= rva < va + size:
                return raw + (rva - va)
        raise ValueError("bad RVA")
    base = off(rsrc_rva)

    def entries(dir_off):
        named, ids = struct.unpack_from("<HH", pe, dir_off + 12)
        for k in range(named + ids):
            name, target = struct.unpack_from("<II", pe, dir_off + 16 + 8 * k)
            yield name, target

    def leaves(type_id):
        out = {}
        for name, t in entries(base):
            if name != type_id or not (t & 0x80000000):
                continue
            for nid, t2 in entries(base + (t & 0x7FFFFFFF)):
                for _lang, t3 in entries(base + (t2 & 0x7FFFFFFF)):
                    if not (t3 & 0x80000000):
                        out[nid] = base + t3          # IMAGE_RESOURCE_DATA_ENTRY
        return out
    icons = leaves(3)                                  # RT_ICON
    groups = leaves(14)                                # RT_GROUP_ICON
    done = 0
    for _gid, gentry in groups.items():
        g_rva, g_size = struct.unpack_from("<II", pe, gentry)
        g = off(g_rva)
        count = struct.unpack_from("<H", pe, g + 4)[0]
        for k in range(count):
            e = g + 6 + 14 * k
            w = pe[e] or 256
            icon_id = struct.unpack_from("<H", pe, e + 12)[0]
            if icon_id not in icons:
                continue
            d_rva, cap = struct.unpack_from("<II", pe, icons[icon_id])
            # capacity = the space the slot occupies (its original length)
            slot = getattr(set_exe_icon, "_caps", {}).get((exe_path, icon_id)) or cap
            data = icon_png(img, w, slot)
            if len(data) > slot:
                log(f"[Export] icon {w}px too detailed for its slot ({len(data)} > {slot} bytes), kept the default")
                continue
            o = off(d_rva)
            pe[o:o + slot] = data + b"\0" * (slot - len(data))
            struct.pack_into("<I", pe, icons[icon_id] + 4, len(data))
            struct.pack_into("<I", pe, e + 8, len(data))
            done += 1
    tmp = exe_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(pe)
    os.replace(tmp, exe_path)
    return done


# --------------------------------------------------------------------------- macOS apps
def make_icns(img):
    """Apple .icns (PNG flavour) from a square image - pure Python."""
    import struct
    parts = b""
    for code, size in ((b"ic11", 32), (b"ic12", 64), (b"ic07", 128), (b"ic13", 256),
                       (b"ic08", 256), (b"ic14", 512), (b"ic09", 512)):
        data = icon_png(img, size)
        parts += code + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(parts) + 8) + parts


def _plist_escape(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def info_plist(name, exe, bundle_id, version="1.0", icon="icon"):
    e = _plist_escape
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{e(name)}</string>
  <key>CFBundleDisplayName</key><string>{e(name)}</string>
  <key>CFBundleExecutable</key><string>{e(exe)}</string>
  <key>CFBundleIdentifier</key><string>{e(bundle_id)}</string>
  <key>CFBundleIconFile</key><string>{e(icon)}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>{e(version)}</string>
  <key>CFBundleVersion</key><string>{e(version)}</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.games</string>
  <key>NSHighResolutionCapable</key><false/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
  <key>NSHumanReadableCopyright</key><string>Made with Heaven3D</string>
</dict>
</plist>
"""


def bundle_id_for(name):
    return "org.heaven3d.game." + (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "game")


def _mac_launcher(game):
    # The real program is Contents/MacOS/python3 (Python's home is Contents/),
    # so macOS shows the game's name and icon in the Dock and menu bar.
    return f"""#!/bin/sh
# {game} - made with Heaven3D
C="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONNOUSERSITE=1
exec "$C/MacOS/python3" -X utf8 -E -s "$C/Resources/engine/Heaven3D.py" --game "$C/Resources" "$@"
"""


def _copy_mac_python(player, contents, log):
    """Lay a macOS Python out as an app bundle: bin/python3 -> MacOS/python3,
    everything else (lib/, ssl/ ...) straight into Contents/."""
    tmp = os.path.join(contents, "_py")
    _copy_python(player, tmp, log)
    os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
    shutil.move(os.path.join(tmp, "bin", "python3"), os.path.join(contents, "MacOS", "python3"))
    os.chmod(os.path.join(contents, "MacOS", "python3"), 0o755)
    shutil.rmtree(os.path.join(tmp, "bin"), ignore_errors=True)
    for n in os.listdir(tmp):
        shutil.move(os.path.join(tmp, n), os.path.join(contents, n))
    os.rmdir(tmp)


def _game_dir_name(name):
    return re.sub(r"\s+", " ", safe_name(name)).strip() or "Game"


def _linux_launcher(game):
    return f"""#!/bin/sh
# {game} - made with Heaven3D
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$HERE/engine/python/bin/python3" -X utf8 -E -s "$HERE/engine/Heaven3D.py" --game "$HERE" "$@"
"""


def _install_shortcut_sh(game):
    # The game's name goes into a shell here-document and a .desktop Exec=
    # line: `$`, backquotes and backslashes in it would be expanded by the
    # shell (a name like "Money$Maker" lost "$Maker", a backquote even ran a
    # command), and the .desktop spec wants `"` `$` `` ` `` `\` escaped
    # inside a quoted Exec argument and `%` doubled.
    def heredoc(s):                                 # literal text inside <<EOF
        return s.replace("\\", "\\\\").replace("$", "\\$").replace("`", "\\`")

    def exec_arg(s):
        # desktop-entry quoting rule, then its general string escape (which
        # doubles every backslash), then field codes; finally the shell level
        q = "".join("\\" + c if c in '"`$\\' else c for c in s)
        return heredoc(q.replace("\\", "\\\\").replace("%", "%%"))

    def sq(s):                                      # one shell word, single-quoted
        return "'" + s.replace("'", "'\"'\"'") + "'"
    slug = re.sub(r'[^a-z0-9]+', '-', game.lower()).strip('-') or bundle_id_for(game).rsplit(".", 1)[-1]
    # The folder the game sits in usually carries the game's name too, so
    # the same escaping is applied to it at install time (X: inside the
    # quoted Exec argument, P: plain Path= / Icon= values).
    return f"""#!/bin/sh
# Adds this game to your applications menu (with its icon).
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
X="$(printf '%s' "$HERE" | sed -e 's/\\\\/\\\\\\\\\\\\\\\\/g' -e 's/["`$]/\\\\\\\\&/g' -e 's/%/%%/g')"
P="$(printf '%s' "$HERE" | sed -e 's/\\\\/\\\\\\\\/g')"
APPS="${{XDG_DATA_HOME:-$HOME/.local/share}}/applications"
mkdir -p "$APPS"
cat > "$APPS/heaven3d-{slug}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name={heredoc(game)}
Comment=Made with Heaven3D
Exec="$X/{exec_arg(game)}"
Path=$P
Icon=$P/icon.png
Terminal=false
Categories=Game;
EOF
echo "Added "{sq(game)}" to the applications menu."
"""


def export_game(project_file, out_dir, targets, engine_dir, game_name=None, width=1600, height=900,
                make_zip=True, log=print, icon=None):
    """Build stand-alone copies of a project for each target platform.

    <out>/<Game>-Windows/<Game>.exe   double-click to play (Heaven3D logo)
    <out>/<Game>-Linux/<Game>         ./<Game>  (+ install-shortcut.sh)
    <out>/<Game>-macOS/<Game>.app     double-click (game + engine inside the app)
    Both contain  game/ (the project, every asset inside),  engine/
    (Heaven3D + its own Python),  saves/  (game saves travel with the game).
    Returns [(platform, folder, zip or None)]."""
    project_file = os.path.abspath(project_file)
    project_dir = os.path.dirname(project_file)
    engine_dir = os.path.abspath(engine_dir)
    out_dir = os.path.abspath(out_dir)
    meta = {}
    try:
        with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        pass
    game = _game_dir_name(game_name or meta.get("name") or os.path.basename(project_dir))
    os.makedirs(out_dir, exist_ok=True)
    icon_img = None
    if icon:
        icon = os.path.abspath(os.path.expanduser(icon))
        if os.path.splitext(icon)[1].lower() not in ICON_EXTS:
            raise ValueError("The game icon must be a .png, .jpg or .bmp image.")
        if not os.path.isfile(icon):
            raise FileNotFoundError(f"Game icon not found: {icon}")
        icon_img = _load_image_square(icon)
        log(f"[Export] game icon: {os.path.basename(icon)}")
    results = []
    for target in targets:
        target = target.lower()
        if target not in PLATFORMS:
            raise ValueError(f"unknown platform {target!r}")
        player = find_player(engine_dir, target)
        if player is None:
            raise FileNotFoundError(
                f"No {PLATFORM_TITLES[target]} player found in {os.path.join(engine_dir, 'runtime')}.\n"
                + ("Exporting for macOS needs the Heaven3D macOS edition."
                   if target == "macos" else
                   "Exporting needs the Heaven3D standalone edition (it ships the Windows and Linux players)."))
        title = PLATFORM_TITLES[target]
        stage = os.path.join(out_dir, f"{game}-{title}")
        log(f"[Export] {title}: {stage}")
        if os.path.isdir(stage):
            shutil.rmtree(stage)
        os.makedirs(stage)
        # game/ and engine/ live next to the launcher - inside the .app on macOS
        if target == "macos":
            contents = os.path.join(stage, f"{game}.app", "Contents")
            base = os.path.join(contents, "Resources")
            os.makedirs(base)
        else:
            base = stage
        # 1. the game (project folder, then every outside asset pulled in)
        gdir = os.path.join(base, "game")

        def ignore(d, names):
            skip = {n for n in names if n in SKIP_DIRS or n.endswith((".bak", ".tmp", ".pyc"))
                    or n in (".heaven3d_thumb.png", "Thumbs.db", ".DS_Store")}
            for n in names:
                if inside(os.path.join(d, n), out_dir):
                    skip.add(n)
            return skip
        shutil.copytree(project_dir, gdir, ignore=ignore)
        with open(project_file, encoding="utf-8") as f:
            data = json.load(f)
        rep = make_portable(data, project_dir, log, collect=True, resolver=Resolver(project_dir),
                            collector=Collector(project_dir, log, dest_root=gdir))
        atomic_write_json(os.path.join(gdir, "main.heaven3d"), data, backup=False)
        if os.path.basename(project_file) != "main.heaven3d":
            try:
                os.remove(os.path.join(gdir, os.path.basename(project_file)))
            except OSError:
                pass
        log(f"[Export]   game files ready ({rep['copied']} outside assets pulled in, "
            f"{len(rep['missing'])} missing)")
        # 2. the engine + its Python
        edir = os.path.join(base, "engine")
        os.makedirs(edir)
        shutil.copy2(os.path.join(engine_dir, "Heaven3D.py"), edir)
        _copy_engine_packages(engine_dir, edir, log)
        for extra in ("LICENSE",):
            if os.path.isfile(os.path.join(engine_dir, extra)):
                shutil.copy2(os.path.join(engine_dir, extra), edir)
        gcfg = {"name": game, "width": int(width), "height": int(height),
                "project": "game/main.heaven3d", "portable_saves": target != "macos",
                "engine": globals().get("HEAVEN_VERSION", "")}
        if icon_img is not None:
            with open(os.path.join(edir, "icon.png"), "wb") as f:
                f.write(icon_png(icon_img, 256))
            gcfg["icon"] = "engine/icon.png"
        atomic_write_json(os.path.join(edir, "game.json"), gcfg, backup=False)
        log("[Export]   copying the Python runtime...")
        if target == "macos":
            _copy_mac_python(player, contents, log)
        else:
            _copy_python(player, os.path.join(edir, "python"), log)
        # 3. launcher stamped with the Heaven3D logo
        brand = os.path.join(engine_dir, "branding")
        if target == "windows":
            exe = os.path.join(engine_dir, "runtime", "launchers", "heaven3d-game.exe")
            if not os.path.isfile(exe):
                raise FileNotFoundError(f"missing launcher {exe}")
            shutil.copy2(exe, os.path.join(stage, f"{game}.exe"))
            if icon:
                n = set_exe_icon(os.path.join(stage, f"{game}.exe"), icon, log)
                log(f"[Export]   {game}.exe carries your icon ({n} sizes)")
        elif target == "macos":
            img = icon_img or _load_image_square(os.path.join(brand, "heaven3d_logo.png"))
            with open(os.path.join(base, "icon.icns"), "wb") as f:
                f.write(make_icns(img))
            exe_name = re.sub(r"[^A-Za-z0-9 _.-]+", "", game).strip() or "Game"
            lp = os.path.join(contents, "MacOS", exe_name)
            with open(lp, "w", encoding="utf-8", newline="\n") as f:
                f.write(_mac_launcher(game))
            os.chmod(lp, 0o755)
            with open(os.path.join(contents, "Info.plist"), "w", encoding="utf-8", newline="\n") as f:
                f.write(info_plist(game, exe_name, bundle_id_for(game), str(meta.get("version") or "1.0")))
            with open(os.path.join(contents, "PkgInfo"), "w") as f:
                f.write("APPL????")
            log(f"[Export]   {game}.app (icon: {'yours' if icon_img is not None else 'Heaven3D'})")
        else:
            lp = os.path.join(stage, game)
            with open(lp, "w", encoding="utf-8", newline="\n") as f:
                f.write(_linux_launcher(game))
            with open(os.path.join(stage, "install-shortcut.sh"), "w", encoding="utf-8", newline="\n") as f:
                f.write(_install_shortcut_sh(game))
            for p in (lp, os.path.join(stage, "install-shortcut.sh")):
                os.chmod(p, 0o755)
            if icon_img is not None:
                shutil.copy2(os.path.join(edir, "icon.png"), os.path.join(stage, "icon.png"))
            elif os.path.isfile(os.path.join(brand, "icons", "logo_256.png")):
                shutil.copy2(os.path.join(brand, "icons", "logo_256.png"), os.path.join(stage, "icon.png"))
        if target != "macos":
            os.makedirs(os.path.join(stage, "saves"), exist_ok=True)
        with open(os.path.join(stage, "README.txt"), "w", encoding="utf-8",
                  newline="\r\n" if target == "windows" else "\n") as f:
            if target == "macos":
                f.write(f"{game}\nMade with Heaven3D.\n\n"
                        f"Play: double-click {game}.app (drag it to Applications if you like).\n\n"
                        "First start: this game isn't from the App Store, so macOS asks first.\n"
                        f"  Right-click (or Control-click) {game}.app > Open > Open.\n"
                        "  If macOS says the app is damaged, open Terminal and run:\n"
                        f"    xattr -dr com.apple.quarantine \"{game}.app\"\n"
                        "  (drag the app onto the Terminal window to type its path).\n\n"
                        f"Saved games: ~/Library/Application Support/Heaven3D/{safe_name(game)}\n")
            else:
                f.write(f"{game}\nMade with Heaven3D.\n\n"
                        + (f"Play: double-click {game}.exe\n" if target == "windows" else
                           f"Play: ./{game}   (or double-click it)\n"
                           f"Menu entry with icon: ./install-shortcut.sh\n")
                        + "\nYour saved games are kept in the saves folder, so you can copy the whole\n"
                          "folder to another computer (or a USB stick) and keep playing.\n")
        zpath = None
        if make_zip:
            log("[Export]   zipping...")
            zpath = stage + ".zip"
            zip_folder(stage, zpath, unix=(target != "windows"))
        results.append((target, stage, zpath))
        log(f"[Export] {title} done.")
    return results


# Python packages a game's engine folder must carry next to Heaven3D.py
# (the in-game UI is drawn with Vitrail).  The editor (h3d_editor) stays out.
GAME_PACKAGES = ("vitrail",)


def _copy_engine_packages(engine_dir, edir, log=print):
    for pkg in GAME_PACKAGES:
        src = os.path.join(engine_dir, pkg)
        if not os.path.isfile(os.path.join(src, "__init__.py")):
            raise FileNotFoundError(
                f"The '{pkg}' folder is missing next to Heaven3D.py ({src}).\n"
                f"Exported games need it - reinstall Heaven3D or copy the {pkg} folder back.")
        dst = os.path.join(edir, pkg)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".*"))
        want = sorted(n for n in os.listdir(src) if n.endswith(".py") or n in ("LICENSE", "NOTICE"))
        got = sorted(n for n in os.listdir(dst) if n.endswith(".py") or n in ("LICENSE", "NOTICE"))
        if want != got:
            raise OSError(f"copying {pkg} into the game failed (missing: {sorted(set(want) - set(got))})")
        log(f"[Export]   {pkg} ({len(got)} files) included")


def _is_unix_exec(rel):
    r = to_posix(rel)
    name = r.rsplit("/", 1)[-1]
    return ("/bin/" in "/" + r or name.endswith((".sh", ".command", ".dylib")) or ".so" in name
            or "/Contents/MacOS/" in "/" + r or ("/" not in r and "." not in name))


def zip_folder(folder, zip_path, unix=False):
    """Zip `folder` (as a top-level directory).  With unix=True the Linux
    executable bits are stored, so it unpacks runnable even when the zip was
    made on Windows."""
    folder = os.path.abspath(folder)
    top = os.path.basename(folder)
    tmp = zip_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, dns, fns in os.walk(folder):
            dns.sort()
            if not dns and not fns:                         # keep empty folders (saves/)
                zd = zipfile.ZipInfo(to_posix(os.path.join(top, os.path.relpath(dp, folder))) + "/")
                zd.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
                z.writestr(zd, b"")
            for fn in sorted(fns):
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, folder)
                arc = to_posix(os.path.join(top, rel))
                zi = zipfile.ZipInfo.from_file(full, arc)
                zi.compress_type = zipfile.ZIP_DEFLATED
                if unix:
                    mode = 0o755 if (_is_unix_exec(rel) or os.access(full, os.X_OK) and not sys.platform.startswith("win")) else 0o644
                    zi.external_attr = ((stat.S_IFREG | mode) << 16)
                    zi.create_system = 3
                with open(full, "rb") as src, z.open(zi, "w") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, zip_path)
    return zip_path
