#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FILEBURNER.py — Make Heaven3D game projects 100% portable.

Reads one or more ``.heaven3d`` save files, finds every referenced media
file (meshes / images / gifs / audio), copies them into a ``media/``
folder next to the game engine script, and writes a NEW
``*_portable.heaven3d`` file with all references retargeted to portable
``media/<name>`` paths.  Originals are NEVER modified.

Stdlib only.  Python 3.8+.  Windows / macOS / Linux.

Importing this module is side-effect free: no tkinter window is created
on import (the GUI only starts under the ``__main__`` guard).
"""

import filecmp
import json
import os
import queue
import re
import shutil
import threading
import traceback
import urllib.parse
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ---------------------------------------------------------------------------
# Core logic (importable without launching the GUI)
# ---------------------------------------------------------------------------

#: All recognised media extensions (lowercase, with leading dot).
MEDIA_EXTS = {
    # meshes
    ".glb", ".gltf", ".blend", ".obj", ".egg", ".ply", ".stl", ".fbx", ".dae",
    # images (incl. gif)
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp", ".dds", ".hdr",
    ".tif", ".tiff", ".gif",
    # audio
    ".wav", ".ogg", ".mp3", ".flac", ".m4a",
}

#: Sentinel string used by the engine for embedded textures — never a path.
EMBEDDED_SENTINEL = "__embedded__"

#: .mtl statement keywords whose last token is a texture filename.
_MTL_TEXTURE_KEYS = {"bump", "norm", "disp", "refl"}

#: URI scheme pattern (e.g. "data:", "http:", "file:").
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def looks_like_media_ref(s):
    """Return True if *s* is a string ending in a known media extension."""
    if not isinstance(s, str):
        return False
    s = s.strip().lower()
    if not s:
        return False
    for ext in MEDIA_EXTS:
        if s.endswith(ext):
            return True
    return False


def _path_variants(path_str):
    """Yield *path_str* with both separator styles so cross-OS saves resolve.

    Always try the raw string first, then the ``\\`` -> ``/`` conversion,
    and on Windows additionally the ``/`` -> ``\\`` conversion.
    """
    variants = [path_str]
    fwd = path_str.replace("\\", "/")
    if fwd not in variants:
        variants.append(fwd)
    if os.sep == "\\":
        back = path_str.replace("/", "\\")
        if back not in variants:
            variants.append(back)
    return variants


def resolve_asset(path_str, project_dir, home=None):
    """Resolve a path string from a save file to a real file on disk.

    Mirrors the engine's ``_from_portable_path`` steps 1-3:
      1) expanduser; if absolute and isfile -> normpath
      2) home/path_str if isfile
      3) project_dir/path_str if isfile
      else None.

    Both ``/`` and ``\\`` separator variants of the raw string are tried
    so saves written on another OS still resolve.
    """
    if not isinstance(path_str, str) or not path_str.strip():
        return None
    path_str = path_str.strip()
    if home is None:
        home = os.path.expanduser("~")
    for variant in _path_variants(path_str):
        try:
            expanded = os.path.expanduser(variant)
            # 1) absolute as-is
            if os.path.isabs(expanded) and os.path.isfile(expanded):
                return os.path.normpath(expanded)
            # 2) home-relative
            if home:
                cand = os.path.join(home, variant)
                if os.path.isfile(cand):
                    return os.path.normpath(cand)
            # 3) project-relative
            if project_dir:
                cand = os.path.join(project_dir, variant)
                if os.path.isfile(cand):
                    return os.path.normpath(cand)
        except (OSError, ValueError):
            # Weird path strings must never crash resolution.
            continue
    return None


def _read_text(path):
    """Read a text file tolerantly (utf-8, replacement on bad bytes).

    Newline translation is disabled so callers that rewrite a file can
    preserve its original line endings exactly.
    """
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _basename_of(ref):
    """Flat basename of a sidecar reference (handles both separators)."""
    ref = urllib.parse.unquote(ref).replace("\\", "/")
    return ref.rsplit("/", 1)[-1]


def _flattened_gltf(path):
    """Parse a .gltf with all relative URIs flattened to basenames.

    Returns None if unparseable.  Used to detect that an existing media/
    copy is just the rewritten version of a source .gltf.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            gltf = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(gltf, dict):
        return None
    for section in ("images", "buffers"):
        entries = gltf.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri")
            if (isinstance(uri, str) and uri and not uri.startswith("data:")
                    and not _SCHEME_RE.match(uri)):
                entry["uri"] = _basename_of(uri)
    return gltf


def _flattened_lines(path):
    """Text lines of an .obj/.mtl with sidecar refs flattened to basenames.

    Texture statements (map_*/bump/norm/disp/refl) have their final token
    flattened; mtllib lines become ``mtllib <basename>``; all other lines
    are kept verbatim.  Returns None if unreadable.
    """
    try:
        lines = _read_text(path).splitlines()
    except OSError:
        return None
    out = []
    for line in lines:
        tokens = line.split()
        if tokens:
            key = tokens[0].lower()
            if key.startswith("map_") or key in _MTL_TEXTURE_KEYS:
                out.append(" ".join(tokens[:-1] + [_basename_of(tokens[-1])]))
                continue
            if key == "mtllib":
                out.append("mtllib " + _basename_of(line.strip()[6:].strip()))
                continue
        out.append(line)
    return out


def _same_logical_copy(src, dst):
    """True if *dst* is the internally-rewritten version of *src*.

    Only meaningful for the rewritable text formats (.gltf/.obj/.mtl):
    the rewrite only flattens internal references to basenames, so the
    flattened forms of both files must match.
    """
    ext = os.path.splitext(str(src))[1].lower()
    try:
        if ext == ".gltf":
            s, d = _flattened_gltf(src), _flattened_gltf(dst)
            return s is not None and s == d
        if ext in (".obj", ".mtl"):
            s, d = _flattened_lines(src), _flattened_lines(dst)
            return s is not None and s == d
    except Exception:
        pass
    return False


def _resolve_ref(ref, base_dir):
    """Resolve a sidecar reference (mtllib / map_* / uri) against *base_dir*.

    Returns a normcase'd absolute path key if the file exists, else None.
    """
    if not ref:
        return None
    ref = urllib.parse.unquote(ref)
    for variant in _path_variants(ref.strip()):
        try:
            p = variant if os.path.isabs(variant) else os.path.join(base_dir, variant)
            p = os.path.normpath(p)
            if os.path.isfile(p):
                return os.path.normcase(os.path.abspath(p))
        except (OSError, ValueError):
            continue
    return None


def _mtl_texture_files(mtl_path):
    """Yield texture files referenced by a .mtl file (map_*/bump/norm/...)."""
    out = []
    mtl_dir = os.path.dirname(mtl_path)
    try:
        lines = _read_text(mtl_path).splitlines()
    except OSError:
        return out
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        key = tokens[0].lower()
        if key.startswith("map_") or key in _MTL_TEXTURE_KEYS:
            fname = tokens[-1]  # options may precede the filename
            if fname.startswith("-"):
                continue
            resolved = _resolve_ref(fname, mtl_dir)
            if resolved:
                out.append(resolved)
    return out


def collect_sidecars(asset_path):
    """Return extra files that must travel with *asset_path*.

    .obj  -> mtllib targets found on disk, plus every texture those .mtl
             files name (map_* / bump / norm / disp / refl).
    .gltf -> images[].uri / buffers[].uri that are relative file URIs
             (data: URIs skipped), resolved against the .gltf's directory.
    anything else -> []
    """
    sidecars = []
    try:
        ext = os.path.splitext(str(asset_path))[1].lower()
        asset_dir = os.path.dirname(os.path.abspath(asset_path))

        if ext == ".obj":
            try:
                lines = _read_text(asset_path).splitlines()
            except OSError:
                lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped.lower().startswith("mtllib"):
                    continue
                rest = stripped[6:].strip()
                if not rest:
                    continue
                # Whole remainder first; fall back to whitespace tokens
                # (some exporters list several .mtl files on one line).
                candidates = [rest] + [t for t in rest.split() if t != rest]
                for cand in candidates:
                    key = _resolve_ref(cand, asset_dir)
                    if key:
                        sidecars.append(key)
                        sidecars.extend(_mtl_texture_files(key))
                        break

        elif ext == ".gltf":
            try:
                with open(asset_path, "r", encoding="utf-8", errors="replace") as fh:
                    gltf = json.load(fh)
            except (OSError, ValueError):
                gltf = None
            if isinstance(gltf, dict):
                for section in ("images", "buffers"):
                    entries = gltf.get(section)
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        uri = entry.get("uri")
                        if not isinstance(uri, str) or not uri:
                            continue
                        if uri.startswith("data:") or _SCHEME_RE.match(uri):
                            continue  # embedded or remote — nothing to copy
                        key = _resolve_ref(uri, asset_dir)
                        if key:
                            sidecars.append(key)
    except Exception:
        # A malformed asset must never break the burn.
        pass

    # Dedupe preserving order; never include the asset itself.
    seen = set()
    out = []
    self_key = os.path.normcase(os.path.abspath(asset_path))
    for s in sidecars:
        if s not in seen and s != self_key:
            seen.add(s)
            out.append(s)
    return out


def find_engine_dir(save_path, engine_script_path=None):
    """Locate the directory the ``media/`` folder should live in.

    1. If *engine_script_path* is given and is a file -> its dirname.
    2. Else look for Heaven3D.py / Heaven3D_runtime.py next to the save.
    3. Else the save file's own directory.
    """
    if engine_script_path:
        try:
            if os.path.isfile(engine_script_path):
                return os.path.dirname(os.path.abspath(engine_script_path))
        except (OSError, ValueError):
            pass
    save_dir = os.path.dirname(os.path.abspath(str(save_path)))
    for name in ("Heaven3D.py", "Heaven3D_runtime.py"):
        try:
            if os.path.isfile(os.path.join(save_dir, name)):
                return save_dir
        except (OSError, ValueError):
            pass
    return save_dir


def burn_project(save_path, engine_dir, log=print):
    """Burn one .heaven3d save into a portable copy.  See SPEC.md.

    Returns a report dict; never raises (one bad save must not crash the
    batch).  The source file is only read, never written.
    """
    save_path = str(save_path)
    engine_dir = str(engine_dir)
    media_dir = os.path.join(engine_dir, "media")
    report = {
        "save": save_path,
        "ok": False,
        "error": None,
        "output_save": None,
        "media_dir": media_dir,
        "copied": [],      # [(src, dst), ...]
        "retargeted": 0,   # number of JSON strings rewritten
        "missing": [],     # unresolved original path strings
        "skipped": [],     # human-readable notes / warnings
    }
    try:
        # -- 1. Load & validate -------------------------------------------
        project_dir = os.path.dirname(os.path.abspath(save_path))
        try:
            with open(save_path, "r", encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except ValueError as exc:
            raise ValueError("unparseable JSON in %s: %s" % (save_path, exc))

        ptype = data.get("type") if isinstance(data, dict) else None
        if ptype is None:
            note = "no 'type' key; assuming heaven3d_project"
            report["skipped"].append(note)
            log("warning: %s" % note)
        elif ptype != "heaven3d_project":
            raise ValueError("not a heaven3d project (type=%r)" % (ptype,))

        # -- 2. media dir --------------------------------------------------
        media_abs = os.path.normcase(os.path.abspath(media_dir))
        try:
            os.makedirs(media_dir, exist_ok=True)
        except OSError as exc:
            raise OSError("cannot create media dir %s: %s" % (media_dir, exc))

        # -- per-burn dedupe state ----------------------------------------
        src_to_name = {}   # normcase abs src -> media basename
        used_names = {}    # media basename -> normcase abs src
        copied_this_burn = set()  # normcase abs dst already written

        def note(msg):
            report["skipped"].append(msg)

        def allocate_name(src):
            """Pick a collision-free media basename for *src*.

            Returns (basename, needs_copy).  A pre-existing media/ file
            from an earlier burn is reused (needs_copy=False) when it is
            byte-identical to *src* — or, for the rewritable text formats,
            when it is exactly the internally-rewritten version of *src*.
            A slot taken by DIFFERENT content is never clobbered: the name
            gets a _1, _2, ... suffix instead.
            """
            src_key = os.path.normcase(os.path.abspath(src))
            base = os.path.basename(src)
            stem, ext = os.path.splitext(base)
            name, n = base, 0
            while True:
                owner = used_names.get(name)
                if owner is not None and owner != src_key:
                    n += 1
                    name = "%s_%d%s" % (stem, n, ext)
                    continue
                if owner is None:
                    dst = os.path.join(media_dir, name)
                    if os.path.normcase(os.path.abspath(dst)) in copied_this_burn:
                        n += 1
                        name = "%s_%d%s" % (stem, n, ext)
                        continue
                    if os.path.exists(dst):
                        try:
                            identical = filecmp.cmp(src, dst, shallow=False)
                        except OSError:
                            identical = False
                        if identical or _same_logical_copy(src, dst):
                            used_names[name] = src_key
                            return name, False
                        n += 1
                        name = "%s_%d%s" % (stem, n, ext)
                        continue
                used_names[name] = src_key
                return name, True

        def copy_into_media(src):
            """Copy *src* into media/ (deduped); return its media basename."""
            src_key = os.path.normcase(os.path.abspath(src))
            if src_key in src_to_name:
                return src_to_name[src_key]
            name, needs_copy = allocate_name(src)
            if needs_copy:
                dst = os.path.join(media_dir, name)
                shutil.copy2(src, dst)
                copied_this_burn.add(os.path.normcase(os.path.abspath(dst)))
                report["copied"].append((src, dst))
            src_to_name[src_key] = name
            return name

        # -- sidecar internal-reference rewriting --------------------------
        def rewrite_mtl_copy(src, dst, name_map):
            """Rewrite map_*/bump/... refs in the copied .mtl to basenames.

            Only touches lines that actually change; if nothing needs
            rewriting the copy stays byte-identical to the source.
            """
            src_dir = os.path.dirname(src)
            lines = _read_text(dst).splitlines(keepends=True)
            changed = False
            for i, raw in enumerate(lines):
                body = raw.rstrip("\r\n")
                tokens = body.split()
                if tokens:
                    key = tokens[0].lower()
                    if key.startswith("map_") or key in _MTL_TEXTURE_KEYS:
                        ref_key = _resolve_ref(tokens[-1], src_dir)
                        if (ref_key and ref_key in name_map
                                and tokens[-1] != name_map[ref_key]):
                            tokens[-1] = name_map[ref_key]
                            lines[i] = " ".join(tokens) + raw[len(body):]
                            changed = True
            if changed:
                with open(dst, "w", encoding="utf-8", newline="") as fh:
                    fh.write("".join(lines))

        def rewrite_obj_copy(src, dst, name_map):
            """Rewrite mtllib refs in the copied .obj to flat basenames.

            Only touches lines that actually change; if nothing needs
            rewriting the copy stays byte-identical to the source.
            """
            src_dir = os.path.dirname(src)
            lines = _read_text(dst).splitlines(keepends=True)
            changed = False
            for i, raw in enumerate(lines):
                body = raw.rstrip("\r\n")
                stripped = body.strip()
                if stripped.lower().startswith("mtllib"):
                    ref = stripped[6:].strip()
                    ref_key = _resolve_ref(ref, src_dir)
                    if (ref_key and ref_key in name_map
                            and ref != name_map[ref_key]):
                        lines[i] = "mtllib " + name_map[ref_key] + raw[len(body):]
                        changed = True
            if changed:
                with open(dst, "w", encoding="utf-8", newline="") as fh:
                    fh.write("".join(lines))

        def rewrite_gltf_copy(src, dst, name_map):
            """Rewrite images[].uri / buffers[].uri in the copied .gltf."""
            src_dir = os.path.dirname(src)
            with open(dst, "r", encoding="utf-8", errors="replace") as fh:
                gltf = json.load(fh)
            changed = False
            if isinstance(gltf, dict):
                for section in ("images", "buffers"):
                    entries = gltf.get(section)
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        uri = entry.get("uri")
                        if not isinstance(uri, str) or not uri:
                            continue
                        if uri.startswith("data:") or _SCHEME_RE.match(uri):
                            continue
                        ref_key = _resolve_ref(uri, src_dir)
                        if (ref_key and ref_key in name_map
                                and uri != name_map[ref_key]):
                            entry["uri"] = name_map[ref_key]
                            changed = True
            if changed:
                with open(dst, "w", encoding="utf-8") as fh:
                    json.dump(gltf, fh, indent=2, ensure_ascii=False)

        def handle_asset(resolved):
            """Copy asset + sidecars, rewrite internals, return basename."""
            src_key = os.path.normcase(os.path.abspath(resolved))
            if src_key in src_to_name:
                return src_to_name[src_key]

            name = copy_into_media(resolved)
            sidecars = collect_sidecars(resolved)
            # local map: normcase abs sidecar src -> media basename
            local_map = {src_key: name}
            sidecar_pairs = []  # (src, dst, basename)
            for sc in sidecars:
                try:
                    sc_name = copy_into_media(sc)
                except OSError as exc:
                    note("sidecar copy failed: %s (%s)" % (sc, exc))
                    log("warning: could not copy sidecar %s: %s" % (sc, exc))
                    continue
                local_map[os.path.normcase(os.path.abspath(sc))] = sc_name
                sidecar_pairs.append((sc, os.path.join(media_dir, sc_name)))

            # Rewrite internal references inside the COPIES only.
            try:
                ext = os.path.splitext(resolved)[1].lower()
                main_dst = os.path.join(media_dir, name)
                if ext == ".obj":
                    rewrite_obj_copy(resolved, main_dst, local_map)
                elif ext == ".gltf":
                    rewrite_gltf_copy(resolved, main_dst, local_map)
                for sc_src, sc_dst in sidecar_pairs:
                    if sc_src.lower().endswith(".mtl"):
                        rewrite_mtl_copy(sc_src, sc_dst, local_map)
            except Exception as exc:
                note("sidecar rewrite issue for %s: %s" % (resolved, exc))
                log("warning: sidecar rewrite issue: %s" % exc)
            return name

        # -- 3. recursive walk ---------------------------------------------
        def process_string(value):
            if value == EMBEDDED_SENTINEL:
                return value
            if not looks_like_media_ref(value):
                return value
            resolved = resolve_asset(value, project_dir)
            if resolved is None:
                report["missing"].append(value)
                log("missing: %s" % value)
                return value
            src_abs = os.path.normcase(os.path.abspath(resolved))
            if os.path.dirname(src_abs) == media_abs:
                # Already inside media/: retarget only, no copy.
                name = os.path.basename(resolved)
                note("already in media/: %s" % value)
            else:
                name = handle_asset(resolved)
            report["retargeted"] += 1
            return "media/" + name

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str):
                        node[k] = process_string(v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    if isinstance(v, str):
                        node[i] = process_string(v)
                    else:
                        walk(v)

        walk(data)

        # -- 4. write portable save ----------------------------------------
        base = os.path.basename(save_path)
        if base.lower().endswith(".heaven3d"):
            stem = base[:-len(".heaven3d")]
        else:
            stem = os.path.splitext(base)[0]
        output_save = os.path.join(engine_dir, stem + "_portable.heaven3d")
        if os.path.normcase(os.path.abspath(output_save)) == \
                os.path.normcase(os.path.abspath(save_path)):
            raise RuntimeError(
                "refusing to overwrite the source save: %s" % save_path)
        with open(output_save, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        report["output_save"] = output_save
        report["ok"] = True
        log("burned: %s -> %s (%d media copied, %d retargeted, %d missing)"
            % (save_path, output_save, len(report["copied"]),
               report["retargeted"], len(report["missing"])))

    except Exception as exc:  # noqa: BLE001 - one bad save must never crash
        report["ok"] = False
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            log("ERROR burning %s: %s" % (save_path, report["error"]))
        except Exception:
            pass
    return report


# ---------------------------------------------------------------------------
# GUI (only constructed under the __main__ guard)
# ---------------------------------------------------------------------------

class FileBurnerApp:
    """Dark, friendly tkinter front-end for the burner."""

    BG = "#1E1E1E"
    PANEL = "#252526"
    ACCENT = "#6B9EBF"
    BURN = "#C74E4E"
    BURN_HOVER = "#DA5959"
    TEXT = "#CCCCCC"

    def __init__(self, root):
        self.root = root
        self.saves = []           # chosen .heaven3d paths (deduped)
        self._q = None            # worker -> UI message queue
        self._burning = False

        root.title("FILE BURNER")
        root.geometry("760x700")
        root.minsize(560, 520)
        root.configure(bg=self.BG)

        self._build_ui()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 14}

        tk.Label(self.root, text="FILE BURNER", bg=self.BG, fg=self.ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(pady=(16, 0))
        tk.Label(self.root, text="Make your Heaven3D game 100% portable",
                 bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 11)).pack(pady=(0, 12))

        # -- save files row --
        files_frame = tk.Frame(self.root, bg=self.PANEL)
        files_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self.listbox = tk.Listbox(
            files_frame, bg=self.PANEL, fg=self.TEXT, height=7,
            selectbackground=self.ACCENT, selectforeground=self.BG,
            highlightthickness=0, bd=0, font=("Segoe UI", 10),
            activestyle="none")
        lb_scroll = ttk.Scrollbar(files_frame, orient="vertical",
                                  command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=lb_scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True,
                          padx=(8, 0), pady=8)
        lb_scroll.pack(side="left", fill="y", pady=8, padx=(0, 8))

        btns = tk.Frame(files_frame, bg=self.PANEL)
        btns.pack(side="left", fill="y", padx=8, pady=8)
        self._button(btns, "Add Save File(s)\u2026",
                     self._add_files).pack(fill="x", pady=(0, 6))
        self._button(btns, "Clear", self._clear_files).pack(fill="x")

        # -- engine row --
        engine_frame = tk.Frame(self.root, bg=self.BG)
        engine_frame.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(engine_frame, text="Engine script (optional):",
                 bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 10)).pack(anchor="w")
        row = tk.Frame(engine_frame, bg=self.BG)
        row.pack(fill="x", pady=(2, 0))
        self.engine_var = tk.StringVar()
        self.engine_entry = tk.Entry(
            row, textvariable=self.engine_var, bg=self.PANEL, fg=self.TEXT,
            insertbackground=self.TEXT, relief="flat",
            font=("Segoe UI", 10))
        self.engine_entry.pack(side="left", fill="x", expand=True,
                               ipady=4, padx=(0, 6))
        self._button(row, "Browse\u2026", self._browse_engine).pack(side="left")
        tk.Label(
            engine_frame,
            text=("The media folder is created next to the engine script "
                  "(Heaven3D.py / Heaven3D_runtime.py). Leave empty to "
                  "auto-detect next to each save file."),
            bg=self.BG, fg="#8A8A8A", font=("Segoe UI", 9),
            wraplength=720, justify="left").pack(anchor="w", pady=(2, 0))

        # -- burn button --
        self.burn_button = tk.Button(
            self.root, text="\U0001F525 BURN \U0001F525",
            command=self._start_burn, bg=self.BURN, fg="white",
            activebackground=self.BURN_HOVER, activeforeground="white",
            font=("Segoe UI", 16, "bold"), relief="flat", bd=0,
            cursor="hand2", disabledforeground="#7A5555")
        self.burn_button.pack(fill="x", padx=14, pady=(4, 8), ipady=8)
        self.burn_button.bind("<Enter>", lambda e: self.burn_button.configure(
            bg=self.BURN_HOVER if self.burn_button["state"] != "disabled"
            else self.BURN))
        self.burn_button.bind("<Leave>", lambda e: self.burn_button.configure(
            bg=self.BURN))

        # -- progress bar --
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("burn.Horizontal.TProgressbar",
                        troughcolor=self.PANEL, background=self.ACCENT,
                        bordercolor=self.BG, lightcolor=self.ACCENT,
                        darkcolor=self.ACCENT)
        self.progress = ttk.Progressbar(
            self.root, style="burn.Horizontal.TProgressbar",
            mode="determinate", maximum=1, value=0)
        self.progress.pack(fill="x", padx=14, pady=(0, 8))

        # -- log --
        self.log_widget = scrolledtext.ScrolledText(
            self.root, bg=self.PANEL, fg=self.TEXT, state="disabled",
            height=10, wrap="word", relief="flat", bd=0,
            font=("Consolas", 9))
        self.log_widget.pack(fill="both", expand=True, padx=14,
                             pady=(0, 14))

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command,
                         bg=self.ACCENT, fg=self.BG,
                         activebackground="#7FB0D1", activeforeground=self.BG,
                         font=("Segoe UI", 10), relief="flat", bd=0,
                         cursor="hand2", padx=10, pady=4)

    # -- UI helpers (main thread only) ---------------------------------------

    def _append_log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", "[%s] %s\n" % (stamp, message))
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _add_files(self):
        try:
            paths = filedialog.askopenfilenames(
                parent=self.root,
                title="Choose Heaven3D save file(s)",
                filetypes=[("Heaven3D saves", "*.heaven3d"),
                           ("All files", "*.*")])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("FILE BURNER",
                                 "Could not open the file dialog:\n%s" % exc)
            return
        added = 0
        for p in paths:
            if p and p not in self.saves:
                self.saves.append(p)
                self.listbox.insert("end", p)
                added += 1
        if added:
            self._append_log("added %d save file(s)" % added)

    def _clear_files(self):
        self.saves = []
        self.listbox.delete(0, "end")
        self._append_log("cleared save file list")

    def _browse_engine(self):
        try:
            path = filedialog.askopenfilename(
                parent=self.root,
                title="Choose the Heaven3D engine script",
                filetypes=[("Python scripts", "*.py"),
                           ("All files", "*.*")])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("FILE BURNER",
                                 "Could not open the file dialog:\n%s" % exc)
            return
        if path:
            self.engine_var.set(path)

    # -- burn orchestration ---------------------------------------------------

    def _start_burn(self):
        if self._burning:
            return
        if not self.saves:
            messagebox.showinfo(
                "FILE BURNER",
                "No save files chosen yet.\n\n"
                "Click \"Add Save File(s)\u2026\" to pick one or more "
                ".heaven3d files first.")
            return
        saves = list(self.saves)
        engine_entry = self.engine_var.get().strip()
        self._burning = True
        self.burn_button.configure(state="disabled", bg=self.BURN)
        self.progress.configure(maximum=len(saves), value=0)
        self._append_log("starting burn of %d save file(s)\u2026" % len(saves))
        self._q = queue.Queue()
        worker = threading.Thread(
            target=self._worker, args=(saves, engine_entry, self._q),
            daemon=True)
        worker.start()
        self.root.after(100, self._pump)

    def _worker(self, saves, engine_entry, q):
        """Background thread: burns each save; only talks to the UI via *q*."""
        reports = []
        warned_engine = False
        for i, save in enumerate(saves, 1):
            def logfn(msg, _save=save):
                q.put(("log", "%s: %s" % (os.path.basename(_save), msg)))
            try:
                if engine_entry and os.path.isfile(engine_entry):
                    engine_dir = find_engine_dir(save, engine_entry)
                else:
                    if engine_entry and not warned_engine:
                        q.put(("log", "engine script not found (%s); falling "
                                      "back to auto-detect" % engine_entry))
                        warned_engine = True
                    engine_dir = find_engine_dir(save, None)
                q.put(("log", "burning %s \u2192 %s" % (save, engine_dir)))
                report = burn_project(save, engine_dir, log=logfn)
            except Exception as exc:  # noqa: BLE001 - belt & braces
                report = {
                    "save": save, "ok": False,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "output_save": None,
                    "media_dir": os.path.join(
                        find_engine_dir(save, None), "media"),
                    "copied": [], "retargeted": 0, "missing": [],
                    "skipped": [],
                }
                q.put(("log", "unexpected error for %s: %s"
                              % (save, report["error"])))
            reports.append(report)
            q.put(("progress", i))
        q.put(("done", reports))

    def _pump(self):
        """Main-thread pump: drain the worker queue and update widgets."""
        done_reports = None
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self.progress.configure(value=payload)
                elif kind == "done":
                    done_reports = payload
        except queue.Empty:
            pass
        except Exception as exc:  # noqa: BLE001
            self._append_log("UI update error: %s" % exc)

        if done_reports is not None:
            self._burning = False
            self.burn_button.configure(state="normal")
            self._finish(done_reports)
            return
        if self._burning:
            self.root.after(100, self._pump)

    def _finish(self, reports):
        burned = sum(1 for r in reports if r.get("ok"))
        copied = sum(len(r.get("copied", [])) for r in reports)
        missing = [m for r in reports for m in r.get("missing", [])]
        failures = [r for r in reports if not r.get("ok")]

        self._append_log("done: %d/%d save(s) burned, %d media file(s) "
                         "copied, %d missing reference(s)"
                         % (burned, len(reports), copied, len(missing)))

        lines = ["%d of %d save file(s) burned successfully."
                 % (burned, len(reports)),
                 "%d media file(s) copied." % copied,
                 "%d missing reference(s)." % len(missing)]
        if missing:
            show = missing[:15]
            lines.append("\nMissing files (references kept as-is):")
            lines.extend("  \u2022 " + m for m in show)
            if len(missing) > len(show):
                lines.append("  \u2026 and %d more" % (len(missing) - len(show)))
        if failures:
            lines.append("\nErrors:")
            for r in failures[:10]:
                lines.append("  \u2022 %s: %s"
                             % (os.path.basename(r.get("save", "?")),
                                r.get("error", "unknown error")))

        if failures:
            messagebox.showerror("FILE BURNER \u2014 finished with errors",
                                 "\n".join(lines))
        elif missing:
            messagebox.showwarning("FILE BURNER \u2014 finished",
                                   "\n".join(lines))
        else:
            messagebox.showinfo("FILE BURNER \u2014 all done!",
                                "\n".join(lines))


# ---------------------------------------------------------------------------
# Entry point — the ONLY place a tkinter root window is created.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        _root = tk.Tk()
        _app = FileBurnerApp(_root)
        _root.mainloop()
    except Exception:  # noqa: BLE001 - never die with a bare traceback dialog
        traceback.print_exc()
        try:
            messagebox.showerror(
                "FILE BURNER",
                "FILE BURNER hit an unexpected problem and must close.\n\n"
                + traceback.format_exc())
        except Exception:
            pass
