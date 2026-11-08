# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Asset browser (a "Project" panel): folders, thumbnails, prefabs and
material balls, backed by a real directory on disk.

    browser = AssetBrowser("/path/to/project", fill=True)
    browser.selection_changed.connect(lambda assets: ...)
    browser.activated.connect(lambda asset: ...)     # double-click / Enter

* thumbnails: real image previews (lazy, budgeted), GPU-shaded material
  balls for ``.vmat`` files, prefab cubes, typed icons for everything else
* folder tree, breadcrumbs, recursive fuzzy search, type filter, tile-size
  slider (small sizes switch to a list)
* create folder / material / prefab, rename (F2), duplicate (Ctrl+D),
  delete (with confirmation), copy path, reveal in the OS file manager
* drag assets out (payload ``{"type": "asset", "assets": [...]}``) onto any
  widget that accepts them -- e.g. ``MaterialSlot`` or a scene TreeView --
  or onto a folder tile to move them

Materials are small JSON files (``.vmat``)::

    {"type": "material", "base_color": [r, g, b, a], "roughness": 0.5,
     "metallic": 0.0, "emission": [r, g, b], "emission_strength": 0.0}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pygame

from .core import CTRL, SHIFT, Signal, Widget
from .dialogs import InputDialog, MessageBox
from .fuzzy import fuzzy_filter
from .layout import Column, Row
from .menus import Menu, MenuItem, open_context_menu
from .theme import rgb, shade, with_alpha
from .tree import TreeItem, TreeView
from .widgets import Button, ColorSwatch, Dropdown, Label, NumberField, TextField, ValueWidget

# ---------------------------------------------------------------------------- kinds
KINDS = {
    # kind: (icon, colour, extensions)
    "folder": ("folder", rgb("#e8b84a"), ()),
    "material": ("material", rgb("#d07ad8"), ("vmat", "mat")),
    "prefab": ("prefab", rgb("#5ca8ff"), ("prefab",)),
    "image": ("image", rgb("#5fc77a"), ("png", "jpg", "jpeg", "bmp", "gif", "tga", "webp")),
    "mesh": ("cube", rgb("#8fb3d9"), ("obj", "fbx", "gltf", "glb", "blend", "stl", "ply", "dae")),
    "scene": ("scene", rgb("#f08a5d"), ("vscene", "scene", "unity", "tscn")),
    "anim": ("film", rgb("#e8605a"), ("vanim", "anim")),
    "audio": ("music", rgb("#4fd0c8"), ("wav", "mp3", "ogg", "flac", "aiff")),
    "video": ("video", rgb("#c46ad8"), ("mp4", "mkv", "webm", "mov", "avi")),
    "script": ("file-code", rgb("#9fd36a"), ("py", "lua", "cs", "js", "ts", "gd", "nim", "rs")),
    "shader": ("sparkle", rgb("#f2c94c"), ("glsl", "vert", "frag", "hlsl", "shader", "wgsl")),
    "font": ("font", rgb("#b0b8c8"), ("ttf", "otf", "woff", "woff2")),
    "text": ("file-text", rgb("#a7adb8"), ("txt", "md", "json", "csv", "xml", "yaml", "yml", "toml", "ini")),
    "other": ("file", rgb("#8a8f98"), ()),
}
_EXT_KIND = {ext: kind for kind, (_, _, exts) in KINDS.items() for ext in exts}
FILTERS = [("all", "All types"), ("material", "Materials"), ("prefab", "Prefabs"), ("image", "Textures"),
           ("mesh", "Models"), ("scene", "Scenes"), ("audio", "Audio"), ("script", "Scripts"),
           ("shader", "Shaders")]

DEFAULT_MATERIAL = {"type": "material", "base_color": [0.8, 0.8, 0.8, 1.0], "roughness": 0.5, "metallic": 0.0,
                    "emission": [0.0, 0.0, 0.0], "emission_strength": 0.0}
DEFAULT_PREFAB = {"type": "prefab", "color": [0.36, 0.66, 1.0, 1.0], "components": ["Transform", "Mesh Renderer"]}


def kind_of(path, is_dir=False):
    if is_dir:
        return "folder"
    return _EXT_KIND.get(os.path.splitext(path)[1][1:].lower(), "other")


def load_json(path, defaults):
    data = dict(defaults)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            data.update(d)
    except (OSError, ValueError):
        pass
    return data


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def unique_path(folder, stem, ext=""):
    path = os.path.join(folder, stem + ext)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{stem} {n}{ext}")
    return path


def create_material(folder, name="New Material", **values):
    data = dict(DEFAULT_MATERIAL)
    data.update(values)
    path = unique_path(folder, name, ".vmat")
    save_json(path, data)
    return path


def create_prefab(folder, name="New Prefab", **values):
    data = dict(DEFAULT_PREFAB, name=name)
    data.update(values)
    path = unique_path(folder, name, ".prefab")
    save_json(path, data)
    return path


def reveal_in_file_manager(path):
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if sys.platform.startswith("win"):
            if os.path.isdir(path):
                os.startfile(folder)  # noqa: S606 (Windows only)
            else:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if not os.path.isdir(path) else ["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return True
    except (OSError, AttributeError):
        return False


class Asset:
    __slots__ = ("path", "name", "kind", "is_dir", "size", "mtime", "_data")

    def __init__(self, path, is_dir=None, stat_result=None):
        self.path = os.path.abspath(path)
        self.name = os.path.basename(self.path.rstrip("/\\")) or self.path
        self.is_dir = os.path.isdir(path) if is_dir is None else is_dir
        self.kind = kind_of(path, self.is_dir)
        try:
            st = stat_result or os.stat(path)
            self.size, self.mtime = st.st_size, st.st_mtime
        except OSError:
            self.size, self.mtime = 0, 0.0
        self._data = None

    @property
    def stem(self):
        return self.name if self.is_dir else os.path.splitext(self.name)[0]

    @property
    def ext(self):
        return "" if self.is_dir else os.path.splitext(self.name)[1][1:].lower()

    @property
    def data(self):
        """Parsed JSON for materials / prefabs (cached)."""
        if self._data is None:
            if self.kind == "material":
                self._data = load_json(self.path, DEFAULT_MATERIAL)
            elif self.kind == "prefab":
                self._data = load_json(self.path, dict(DEFAULT_PREFAB, name=self.stem))
            else:
                self._data = {}
        return self._data

    def reload(self):
        self._data = None

    def __repr__(self):
        return f"<Asset {self.kind} {self.name!r}>"

    def __eq__(self, other):
        return isinstance(other, Asset) and other.path == self.path

    def __hash__(self):
        return hash(self.path)


def scan(folder, show_hidden=False):
    out = []
    with os.scandir(folder) as it:
        for e in it:
            if not show_hidden and e.name.startswith("."):
                continue
            try:
                is_dir = e.is_dir()
                st = e.stat()
            except OSError:
                continue
            out.append(Asset(e.path, is_dir, st))
    out.sort(key=lambda a: (not a.is_dir, a.name.lower()))
    return out


def walk(root, limit=5000):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(dirnames) + sorted(filenames):
            if name.startswith("."):
                continue
            yield os.path.join(dirpath, name)
            n += 1
            if n >= limit:
                return


# ---------------------------------------------------------------------------- thumbnails
def draw_material_ball(dl, x, y, size, mat, *, checker=True, radius=6.0):
    """Material-ball thumbnail: checker backdrop, contact shadow, lit sphere."""
    if checker:
        dl.checker(x, y, size, size, (0.25, 0.25, 0.27, 1), (0.19, 0.19, 0.21, 1), max(4.0, size / 10), radius)
    pad = size * 0.1
    d = size - 2 * pad
    dl.shadow(x + pad + d * 0.18, y + pad + d * 0.86, d * 0.64, d * 0.1, (0, 0, 0, 0.55), d * 0.05, d * 0.06)
    em = list(mat.get("emission", [0, 0, 0]))[:3] + [1.0]
    dl.material_ball(x + pad, y + pad, d, tuple(mat.get("base_color", (0.8, 0.8, 0.8, 1))),
                     float(mat.get("roughness", 0.5)), float(mat.get("metallic", 0.0)), tuple(em),
                     float(mat.get("emission_strength", 0.0)))


def draw_thumbnail(dl, ui, asset, x, y, size):
    """Thumbnail for any asset inside a size x size square. Returns False while loading."""
    t = ui.theme
    s = t.scale
    icon, color, _ = KINDS.get(asset.kind, KINDS["other"])
    r = max(3.0, size * 0.07)
    if asset.kind == "material":
        draw_material_ball(dl, x, y, size, asset.data, radius=r)
        return True
    if asset.kind == "image" and size >= 24:
        dl.checker(x, y, size, size, (0.3, 0.3, 0.32, 1), (0.24, 0.24, 0.26, 1), max(4.0, size / 10), r)
        tile = 64 if size <= 64 else (128 if size <= 128 else 256)
        if ui.image(dl, asset.path, x + 2, y + 2, size - 4, size - 4, max_px=tile):
            return True
        ui.icon_centered(dl, "image", x, y, size, size, max(14, size * 0.4) / s, with_alpha(color, 0.5))
        return False
    if asset.kind == "prefab":
        c = tuple(asset.data.get("color", DEFAULT_PREFAB["color"]))
        dl.box(x, y, size, size, with_alpha(c, 0.16), border=with_alpha(c, 0.45), radius=r)
        ui.icon_centered(dl, "prefab", x, y, size, size, max(14, size * 0.58) / s, shade(c, 0.15))
        return True
    if asset.kind == "folder":
        ui.icon_centered(dl, "folder", x, y, size, size, max(14, size * 0.78) / s, color)
        return True
    dl.box(x + size * 0.12, y + size * 0.04, size * 0.76, size * 0.92, with_alpha(color, 0.12), radius=r)
    ui.icon_centered(dl, icon, x, y - size * 0.04, size, size, max(14, size * 0.5) / s, color)
    if size >= 56 and asset.ext:
        badge = "." + asset.ext
        bw = ui.text_width(badge, size=10) + 8 * s
        dl.box(x + size - bw - 4 * s, y + size - 18 * s, bw, 14 * s, color, radius=3 * s)
        ui.text(dl, badge, x + size - bw - 4 * s, y + size - 18 * s, bw, 14 * s, (0.08, 0.08, 0.1, 1), "center", size=10)
    return True


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


# ---------------------------------------------------------------------------- grid
class AssetGrid(Widget):
    focusable = True
    wants_text_input = True
    hover_redraw = True

    def __init__(self, *, tile=88, **kw):
        super().__init__(**kw)
        self.assets = []
        self.tile = tile
        self.selection = []
        self.scroll = 0.0
        self._hot = None
        self._press = None
        self._box = None
        self._typed = ("", 0.0)
        self._cursor = None
        self.browser = None
        self.selection_changed = Signal()
        self.activated = Signal()
        self.context = Signal()       # (asset or None, x, y)
        self.dropped = Signal()       # (assets, target_asset)

    def set_assets(self, assets, keep_selection=True):
        old = {a.path for a in self.selection}
        self.assets = list(assets)
        self.selection = [a for a in self.assets if a.path in old] if keep_selection else []
        self.scroll = 0.0 if not keep_selection else self.scroll
        self.invalidate()

    @property
    def list_mode(self):
        return self.tile < 44

    def _cell(self, ui):
        s = ui.theme.scale
        if self.list_mode:
            return (self.w - 12 * s, round(max(22, self.tile * 0.7) * s))
        t = self.tile * s
        return (round(t + 16 * s), round(t + 30 * s))

    def _cols(self, ui):
        cw, _ = self._cell(ui)
        return 1 if self.list_mode else max(1, int((self.w - 8) // cw))

    def _rect(self, ui, i):
        cw, ch = self._cell(ui)
        cols = self._cols(ui)
        if self.list_mode:
            return (self.x + 6, self.y + 6 + i * ch - self.scroll, cw, ch)
        total = cols * cw
        ox = self.x + max(4.0, (self.w - total) / 2)
        return (ox + (i % cols) * cw, self.y + 6 + (i // cols) * ch - self.scroll, cw, ch)

    def index_at(self, ui, px, py):
        if not self.contains(px, py):
            return None
        for i in range(len(self.assets)):
            x, y, w, h = self._rect(ui, i)
            if x <= px < x + w and y <= py < y + h:
                return i
        return None

    def _max_scroll(self, ui):
        _, ch = self._cell(ui)
        rows = (len(self.assets) + self._cols(ui) - 1) // self._cols(ui)
        return max(0.0, rows * ch + 12 - self.h)

    def preferred_size(self, ui):
        return (240 * ui.theme.scale, 160 * ui.theme.scale)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        self.scroll = max(0.0, min(self.scroll, self._max_scroll(ui)))
        focused = ui.focus is self
        dl.box(self.x, self.y, self.w, self.h, shade(t.panel, -0.05), border=t.accent if focused else t.outline,
               radius=t.radius * s)
        dl.push_clip(self.x + 1, self.y + 1, self.w - 2, self.h - 2)
        sel = {a.path for a in self.selection}
        drop_target = ui.drag.target if ui.drag is not None else None
        loading = False
        for i, a in enumerate(self.assets):
            x, y, w, h = self._rect(ui, i)
            if y + h < self.y or y > self.y + self.h:
                continue
            selected = a.path in sel
            hot = i == self._hot and self.hovered
            if self.list_mode:
                if selected:
                    dl.box(x, y, w, h, with_alpha(t.accent, 0.35), radius=4 * s)
                elif hot:
                    dl.box(x, y, w, h, with_alpha(t.text, 0.05), radius=4 * s)
                isz = h - 6 * s
                if not draw_thumbnail(dl, ui, a, x + 4 * s, y + 3 * s, isz):
                    loading = True
                ui.text(dl, a.name, x + isz + 12 * s, y, w * 0.55, h, t.text)
                info = "Folder" if a.is_dir else f"{a.kind.title()}  •  {human_size(a.size)}"
                ui.text(dl, info, x + w * 0.6, y, w * 0.4 - 8 * s, h, t.text_dim, "right", size=11)
                continue
            if selected:
                dl.box(x + 3 * s, y + 3 * s, w - 6 * s, h - 6 * s, with_alpha(t.accent, 0.28),
                       border=with_alpha(t.accent, 0.8), radius=6 * s)
            elif hot:
                dl.box(x + 3 * s, y + 3 * s, w - 6 * s, h - 6 * s, with_alpha(t.text, 0.06), radius=6 * s)
            if drop_target is self and a.is_dir and i == self._hot:
                dl.box(x + 3 * s, y + 3 * s, w - 6 * s, h - 6 * s, (0, 0, 0, 0), border=t.accent, radius=6 * s,
                       border_width=2 * s)
            ts = self.tile * s
            if not draw_thumbnail(dl, ui, a, x + (w - ts) / 2, y + 6 * s, ts):
                loading = True
            ui.text(dl, a.stem if a.kind not in ("image", "other", "text") else a.name,
                    x + 4 * s, y + 8 * s + ts, w - 8 * s, 18 * s, t.text,
                    "center", size=12)
        if not self.assets:
            ui.text(dl, "This folder is empty", self.x, self.y, self.w, 60 * s, t.text_dim, "center")
        if self._box is not None:
            bx0, by0, bx1, by1 = self._box
            dl.box(min(bx0, bx1), min(by0, by1), abs(bx1 - bx0), abs(by1 - by0), with_alpha(t.accent, 0.12),
                   border=t.accent, radius=2)
        dl.pop_clip()
        ms = self._max_scroll(ui)
        if ms > 0:
            vh = self.h - 4
            th = max(24.0, vh * vh / (vh + ms))
            dl.box(self.x + self.w - 8 * s, self.y + 2 + (vh - th) * self.scroll / ms, 5 * s, th, t.scrollbar,
                   radius=2.5 * s)
        if loading:
            ui.request_repaint(self, 0.02)

    # -- selection -----------------------------------------------------------------------
    def select(self, assets, emit=True):
        self.selection = list(assets)
        if self.selection:
            self._cursor = self.assets.index(self.selection[-1]) if self.selection[-1] in self.assets else None
        self.invalidate()
        if emit:
            self.selection_changed.emit(list(self.selection))

    def _scroll_to(self, ui, i):
        x, y, w, h = self._rect(ui, i)
        if y < self.y:
            self.scroll -= self.y - y + 6
        elif y + h > self.y + self.h:
            self.scroll += y + h - (self.y + self.h) + 6
        self.invalidate()

    # -- mouse ------------------------------------------------------------------------------
    def on_mouse_move(self, ui, ev):
        i = self.index_at(ui, ev.x, ev.y)
        if i != self._hot:
            self._hot = i
            self.invalidate()
        if self._box is not None:
            self._box[2], self._box[3] = ev.x, ev.y
            self.invalidate()
            return
        if self._press is not None and ui.drag is None:
            x0, y0, item = self._press
            if abs(ev.x - x0) + abs(ev.y - y0) > 6:
                items = self.selection if item in self.selection else [item]
                label = items[0].name if len(items) == 1 else f"{len(items)} assets"
                icon, color, _ = KINDS.get(items[0].kind, KINDS["other"])
                ui.start_drag({"type": "asset", "assets": list(items)}, label, icon, source=self, color=color)

    def on_hover(self, ui, hovered):
        if not hovered:
            self._hot = None
        self.invalidate()

    def on_mouse_down(self, ui, ev):
        ui.set_focus(self)
        i = self.index_at(ui, ev.x, ev.y)
        a = self.assets[i] if i is not None else None
        if ev.button == 3:
            if a is not None and a not in self.selection:
                self.select([a])
            self.context.emit(a, ev.x, ev.y)
            return True
        if ev.button != 1:
            return False
        if a is None:
            self._box = [ev.x, ev.y, ev.x, ev.y]
            if not ev.ctrl and not ev.shift:
                self.select([])
            return True
        if ev.clicks >= 2:
            self.select([a])
            self.activated.emit(a)
            return True
        if ev.ctrl:
            sel = list(self.selection)
            sel.remove(a) if a in sel else sel.append(a)
            self.select(sel)
        elif ev.shift and self._cursor is not None:
            lo, hi = sorted((self._cursor, i))
            self.select(self.assets[lo:hi + 1])
        elif a not in self.selection:
            self.select([a])
        self._cursor = i
        self._press = (ev.x, ev.y, a)
        return True

    def on_mouse_up(self, ui, ev):
        if self._box is not None:
            bx0, by0, bx1, by1 = self._box
            self._box = None
            xa, xb = sorted((bx0, bx1))
            ya, yb = sorted((by0, by1))
            hit = []
            for i, a in enumerate(self.assets):
                x, y, w, h = self._rect(ui, i)
                if x < xb and x + w > xa and y < yb and y + h > ya:
                    hit.append(a)
            keep = [a for a in self.selection if a not in hit] if (ev.ctrl or ev.shift) else []
            self.select(keep + hit)
        elif self._press is not None and ui.drag is None and not ev.ctrl and not ev.shift:
            a = self._press[2]
            if len(self.selection) > 1 and a in self.selection:
                self.select([a])
        self._press = None
        self.invalidate()

    def on_wheel(self, ui, dx, dy):
        if ui._mods & CTRL:
            self.tile = max(24, min(192, self.tile + (12 if dy > 0 else -12)))
            self.invalidate()
            return True
        _, ch = self._cell(ui)
        self.scroll = max(0.0, min(self._max_scroll(ui), self.scroll - dy * ch * 0.6))
        self._hot = self.index_at(ui, *ui.mouse)
        self.invalidate()
        return True

    # drops onto folder tiles move files
    def accepts_drop(self, payload):
        return isinstance(payload, dict) and payload.get("type") == "asset"

    def on_drop(self, ui, payload, x, y):
        i = self.index_at(ui, x, y)
        target = self.assets[i] if i is not None else None
        moving = [a for a in payload.get("assets", []) if target is None or a.path != target.path]
        if target is not None and target.is_dir and moving:
            self.dropped.emit(moving, target)

    # -- keyboard -------------------------------------------------------------------------
    def on_key(self, ui, e):
        n = len(self.assets)
        cols = self._cols(ui)
        cur = self._cursor if self._cursor is not None else -1
        step = {pygame.K_LEFT: -1, pygame.K_RIGHT: 1, pygame.K_UP: -cols, pygame.K_DOWN: cols}.get(e.key)
        if step is not None and n:
            i = max(0, min(n - 1, cur + step if cur >= 0 else 0))
            if e.mod & SHIFT and self._cursor is not None:
                lo, hi = sorted((self._cursor, i))
                self.select(self.assets[lo:hi + 1])
            else:
                self.select([self.assets[i]])
            self._cursor = i
            self._scroll_to(ui, i)
            return True
        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.selection:
            self.activated.emit(self.selection[-1])
            return True
        if e.key == pygame.K_a and e.mod & CTRL:
            self.select(self.assets)
            return True
        if e.key == pygame.K_ESCAPE:
            ui.set_focus(None)
            return False
        if self.browser is not None and self.browser.handle_grid_key(ui, e):
            return True
        return bool(e.unicode) and e.unicode.isprintable() and not e.mod & CTRL

    def on_text(self, ui, text):
        prev, t0 = self._typed
        now = time.perf_counter()
        buf = (prev if now - t0 < 0.9 else "") + text
        self._typed = (buf, now)
        for i, a in enumerate(self.assets):
            if a.name.lower().startswith(buf.lower()):
                self.select([a])
                self._cursor = i
                self._scroll_to(ui, i)
                break
        return True

    def on_focus(self, ui, focused):
        self.invalidate()


# ---------------------------------------------------------------------------- breadcrumbs
class Breadcrumbs(Widget):
    hover_redraw = True

    def __init__(self, browser, **kw):
        super().__init__(**kw)
        self.browser = browser
        self._hot = None

    def preferred_size(self, ui):
        return (160 * ui.theme.scale, ui.theme.h)

    def _parts(self):
        b = self.browser
        if b.query:
            return [("Search results", None)]
        rel = os.path.relpath(b.cwd, b.root)
        parts = [(os.path.basename(b.root.rstrip("/\\")) or b.root, b.root)]
        if rel != ".":
            acc = b.root
            for seg in rel.split(os.sep):
                acc = os.path.join(acc, seg)
                parts.append((seg, acc))
        return parts

    def _rects(self, ui):
        s = ui.theme.scale
        x = self.x + 6 * s
        out = []
        for i, (name, path) in enumerate(self._parts()):
            w = ui.text_width(name) + 12 * s
            out.append((x, w, name, path))
            x += w + 14 * s
        return out

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        dl.box(self.x, self.y, self.w, self.h, shade(t.field, -0.1), border=t.outline, radius=t.radius * s)
        dl.push_clip(self.x, self.y, self.w, self.h)
        rects = self._rects(ui)
        for i, (x, w, name, path) in enumerate(rects):
            last = i == len(rects) - 1
            if i == self._hot and not last and self.hovered:
                dl.box(x, self.y + 3 * s, w, self.h - 6 * s, with_alpha(t.text, 0.08), radius=3 * s)
            ui.text(dl, name, x + 6 * s, self.y, w, self.h, t.text if last else t.text_dim, bold=last)
            if not last:
                a = round(6 * s)
                dl.triangle(x + w + 4 * s, self.y + (self.h - a) / 2, a, a, t.text_dim, 0)
        dl.pop_clip()

    def on_mouse_move(self, ui, ev):
        hot = None
        for i, (x, w, _, _) in enumerate(self._rects(ui)):
            if x <= ev.x < x + w:
                hot = i
        if hot != self._hot:
            self._hot = hot
            self.invalidate()

    def on_mouse_down(self, ui, ev):
        for x, w, name, path in self._rects(ui):
            if x <= ev.x < x + w and path:
                self.browser.clear_search()
                self.browser.navigate(path)
                return True
        return True


# ---------------------------------------------------------------------------- browser
class AssetBrowser(Column):
    def __init__(self, root, *, tile=88, show_tree=True, **kw):
        kw.setdefault("spacing", 6)
        super().__init__(**kw)
        self.root = os.path.abspath(root)
        self.cwd = self.root
        self.query = ""
        self.kind_filter = "all"
        self.selection_changed = Signal()
        self.activated = Signal()
        self.changed = Signal()       # files on disk changed through the browser

        bar = Row(spacing=4, parent=self)
        self.btn_up = Button(icon="arrow-up", tooltip="Parent folder (Backspace)", expand=0, parent=bar)
        self.btn_up.clicked.connect(self.go_up)
        self.crumbs = Breadcrumbs(self, parent=bar, expand=2)
        self.search = TextField("", placeholder="Search project...", parent=bar, expand=1.2)
        self.search.edited.connect(self._search)
        self.search.changed.connect(self._search)
        self.filter = Dropdown(FILTERS, "all", width=120, expand=0, parent=bar, searchable=False)
        self.filter.changed.connect(self._set_filter)
        self.btn_new = Button("Create", icon="plus", expand=0, parent=bar, tooltip="Create asset")
        self.btn_new.clicked.connect(lambda: self._create_menu(self.btn_new))
        self.btn_refresh = Button(icon="refresh", expand=0, parent=bar, tooltip="Refresh (F5)")
        self.btn_refresh.clicked.connect(self.refresh)

        main = Row(spacing=6, parent=self, fill=True)
        self.tree = TreeView(rows=6, width=200, multi_select=False, parent=main)
        self.tree.expand = 0
        self.tree.visible = show_tree
        self.tree.selection_changed.connect(self._tree_pick)
        self.grid = AssetGrid(tile=tile, parent=main)
        self.grid.browser = self
        self.grid.selection_changed.connect(self._grid_selection)
        self.grid.activated.connect(self._activate)
        self.grid.context.connect(self._context)
        self.grid.dropped.connect(self._move_into)
        self.tree.drop_filter = lambda payload: isinstance(payload, dict) and payload.get("type") == "asset"
        self.tree.payload_dropped.connect(self._drop_on_tree)

        foot = Row(spacing=8, parent=self)
        self.status = Label("", dim=True, parent=foot)
        self.size_slider = NumberField("Size", tile, min=24, max=192, step=4, slider=True, width=160, expand=0,
                                       parent=foot)
        self.size_slider.changed.connect(self._resize_tiles)
        self.refresh_tree()
        self.navigate(self.root)

    # -- folder tree ---------------------------------------------------------------------------
    def _dir_item(self, path, name=None):
        def load(item):
            try:
                return [self._dir_item(a.path) for a in scan(path) if a.is_dir]
            except OSError:
                return []
        has_sub = False
        try:
            has_sub = any(e.is_dir() and not e.name.startswith(".") for e in os.scandir(path))
        except OSError:
            pass
        it = TreeItem(name or os.path.basename(path), icon="folder", icon_color=KINDS["folder"][1], data=path,
                      lazy=load if has_sub else None)
        return it

    def refresh_tree(self):
        expanded = set()
        for item, _ in self.tree.rows():
            if item.expanded:
                expanded.add(item.data)
        root = self._dir_item(self.root, "Project")
        root.expanded = True
        self.tree.set_items([root])

        def restore(item):
            if item.data in expanded:
                self.tree.set_expanded(item, True)
            for c in item.children:
                restore(c)
        restore(root)
        self._sync_tree_selection()

    def _sync_tree_selection(self):
        for item, _ in self.tree.rows():
            if os.path.normpath(item.data) == os.path.normpath(self.cwd):
                self.tree.select([item], emit=False)
                return

    def _tree_pick(self, sel):
        if sel and os.path.normpath(sel[0].data) != os.path.normpath(self.cwd):
            self.clear_search()
            self.navigate(sel[0].data)

    def _drop_on_tree(self, payload, item, where):
        if item is not None:
            self._move_into(payload.get("assets", []), Asset(item.data, True))

    # -- listing ------------------------------------------------------------------------------------
    def _matches(self, a):
        return self.kind_filter == "all" or a.kind == self.kind_filter or a.is_dir and not self.query

    def navigate(self, path):
        path = os.path.abspath(path)
        try:
            assets = scan(path)
        except OSError as exc:
            self.status.text = f"Cannot open folder: {exc.strerror or exc}"
            return False
        self.cwd = path
        shown = [a for a in assets if self._matches(a)]
        self.grid.set_assets(shown, keep_selection=False)
        self.selection_changed.emit([])
        self.btn_up.enabled = os.path.normpath(path) != os.path.normpath(self.root)
        self._sync_tree_selection()
        self._status()
        self.crumbs.invalidate()
        return True

    def refresh(self):
        for a in self.grid.assets:
            a.reload()
        if self.query:
            self._search(self.query)
        else:
            sel = [a.path for a in self.grid.selection]
            self.navigate(self.cwd)
            self.grid.select([a for a in self.grid.assets if a.path in sel], emit=False)
        self.refresh_tree()

    def go_up(self):
        if os.path.normpath(self.cwd) != os.path.normpath(self.root):
            old = self.cwd
            self.navigate(os.path.dirname(self.cwd))
            self.grid.select([a for a in self.grid.assets if a.path == old], emit=False)

    def _search(self, text):
        self.query = text.strip()
        if not self.query:
            self.navigate(self.cwd)
            return
        found = [Asset(p) for p in walk(self.root)]
        found = [a for a in found if not a.is_dir and (self.kind_filter == "all" or a.kind == self.kind_filter)]
        found = fuzzy_filter(self.query, found, key=lambda a: a.name)
        self.grid.set_assets(found[:500], keep_selection=False)
        self.selection_changed.emit([])
        self._status()
        self.crumbs.invalidate()

    def clear_search(self):
        if self.query:
            self.query = ""
            self.search.set_value("", emit=False)

    def _set_filter(self, kind):
        self.kind_filter = kind
        self._search(self.query) if self.query else self.navigate(self.cwd)

    def _resize_tiles(self, v):
        self.grid.tile = int(v)
        self.grid.invalidate()

    def _status(self):
        n = len(self.grid.assets)
        sel = self.grid.selection
        txt = f"{n} item{'s' if n != 1 else ''}"
        if len(sel) == 1:
            txt += f"  •  {os.path.relpath(sel[0].path, self.root)}"
        elif sel:
            txt += f"  •  {len(sel)} selected"
        self.status.text = txt

    def _grid_selection(self, sel):
        self._status()
        self.selection_changed.emit(sel)

    def _activate(self, a):
        if a.is_dir:
            self.clear_search()
            self.navigate(a.path)
        else:
            self.activated.emit(a)

    @property
    def selected(self):
        return list(self.grid.selection)

    def select_path(self, path):
        for a in self.grid.assets:
            if os.path.normpath(a.path) == os.path.normpath(path):
                self.grid.select([a])
                return a
        return None

    # -- operations --------------------------------------------------------------------------------------
    def _after_create(self, path):
        self.refresh()
        self.select_path(path)
        self.changed.emit()

    def create_folder(self):
        def done(name):
            if name:
                p = unique_path(self.cwd, name)
                os.makedirs(p)
                self._after_create(p)
        InputDialog.ask(self.ui, "New Folder", "Name", done, default="New Folder")

    def create_material(self):
        self._after_create(create_material(self.cwd))

    def create_prefab(self):
        self._after_create(create_prefab(self.cwd))

    def _create_items(self):
        return [MenuItem("Folder", self.create_folder, icon="folder-plus"),
                None,
                MenuItem("Material", self.create_material, icon="material"),
                MenuItem("Prefab", self.create_prefab, icon="prefab")]

    def _create_menu(self, button):
        ui = self.ui
        ui.open_popup(Menu(self._create_items(), title="Create"),
                      anchor=(button.x, button.y, button.w, button.h))

    def rename(self, a):
        def done(name):
            if not name or name == a.stem:
                return
            new = os.path.join(os.path.dirname(a.path), name + ("" if a.is_dir else os.path.splitext(a.name)[1]))
            if os.path.exists(new):
                MessageBox.ask(self.ui, f"“{os.path.basename(new)}” already exists.", title="Rename",
                               icon="warning")
                return
            os.rename(a.path, new)
            self.ui.images.forget(a.path)
            self._after_create(new)
        InputDialog.ask(self.ui, "Rename", "Name", done, default=a.stem)

    def duplicate(self, assets):
        last = None
        for a in assets:
            stem, ext = os.path.splitext(a.name) if not a.is_dir else (a.name, "")
            dst = unique_path(os.path.dirname(a.path), stem + " copy", ext)
            (shutil.copytree if a.is_dir else shutil.copy2)(a.path, dst)
            last = dst
        if last:
            self._after_create(last)

    def delete(self, assets):
        if not assets:
            return
        names = ", ".join(a.name for a in assets[:3]) + (" ..." if len(assets) > 3 else "")

        def done(answer):
            if answer != "Delete":
                return
            for a in assets:
                try:
                    shutil.rmtree(a.path) if a.is_dir else os.remove(a.path)
                except OSError as exc:
                    self.status.text = f"Could not delete {a.name}: {exc.strerror or exc}"
                self.ui.images.forget(a.path)
            self.refresh()
            self.changed.emit()
        MessageBox.ask(self.ui, f"Delete {len(assets)} item{'s' if len(assets) > 1 else ''}?\n{names}\n"
                                "This cannot be undone.", done, title="Delete assets", icon="warning",
                       buttons=("Cancel", "Delete"), default="Delete", cancel="Cancel")

    def _move_into(self, assets, folder):
        moved = 0
        for a in assets:
            if os.path.normpath(os.path.dirname(a.path)) == os.path.normpath(folder.path):
                continue
            if a.is_dir and os.path.normpath(folder.path).startswith(os.path.normpath(a.path) + os.sep):
                continue
            dst = unique_path(folder.path, *(os.path.splitext(a.name) if not a.is_dir else (a.name, "")))
            try:
                shutil.move(a.path, dst)
                self.ui.images.forget(a.path)
                moved += 1
            except OSError as exc:
                self.status.text = f"Move failed: {exc.strerror or exc}"
        if moved:
            self.refresh()
            self.status.text = f"Moved {moved} item{'s' if moved > 1 else ''} to {folder.name}"
            self.changed.emit()

    def _context(self, a, x, y):
        ui = self.ui
        sel = self.selected
        items = [MenuItem("Create", icon="plus", submenu=self._create_items())]
        if a is not None:
            items += [None,
                      MenuItem("Open", lambda: self._activate(a), icon="folder-open" if a.is_dir else "external"),
                      MenuItem("Rename...", lambda: self.rename(a), icon="edit", shortcut="F2"),
                      MenuItem("Duplicate", lambda: self.duplicate(sel), icon="copy", shortcut="Ctrl+D"),
                      MenuItem("Delete...", lambda: self.delete(sel), icon="trash", shortcut="Del"),
                      None,
                      MenuItem("Copy Path", lambda: ui.clipboard.put(a.path), icon="link"),
                      MenuItem("Reveal in File Manager", lambda: reveal_in_file_manager(a.path), icon="external")]
        items += [None, MenuItem("Refresh", self.refresh, icon="refresh", shortcut="F5")]
        open_context_menu(ui, items, at=(x, y))

    def on_key(self, ui, e):
        return False

    # keyboard shortcuts while the grid has focus
    def handle_grid_key(self, ui, e):
        sel = self.selected
        if e.key == pygame.K_F2 and len(sel) == 1:
            self.rename(sel[0])
        elif e.key == pygame.K_DELETE and sel:
            self.delete(sel)
        elif e.key == pygame.K_d and e.mod & CTRL and sel:
            self.duplicate(sel)
        elif e.key == pygame.K_F5:
            self.refresh()
        elif e.key == pygame.K_BACKSPACE:
            self.go_up()
        else:
            return False
        return True


# ---------------------------------------------------------------------------- material widgets
class MaterialBall(Widget):
    """Large material preview. ``material`` is a dict like DEFAULT_MATERIAL."""

    def __init__(self, material=None, *, size=180, **kw):
        super().__init__(**kw)
        self.material = dict(material or DEFAULT_MATERIAL)
        self.size = size

    def preferred_size(self, ui):
        d = self.size * ui.theme.scale
        return (d, d)

    def draw(self, dl, ui):
        d = min(self.w, self.h)
        draw_material_ball(dl, self.x + (self.w - d) / 2, self.y, d, self.material, radius=8 * ui.theme.scale)


class MaterialEditor(Column):
    """Edits a ``.vmat`` file live: preview ball + base colour, roughness,
    metallic, emission. Saves on every change and emits ``changed(path)``."""

    def __init__(self, path=None, **kw):
        kw.setdefault("spacing", 4)
        super().__init__(**kw)
        self.path = None
        self.changed = Signal()
        self.ball = MaterialBall(size=190, parent=self)
        self.title = Label("", bold=True, align="center", parent=self)
        self.base = ColorSwatch((0.8, 0.8, 0.8, 1), label="Base Color", parent=self)
        self.rough = NumberField("Roughness", 0.5, min=0.0, max=1.0, slider=True, parent=self)
        self.metal = NumberField("Metallic", 0.0, min=0.0, max=1.0, slider=True, parent=self)
        self.emis = ColorSwatch((0, 0, 0, 1), label="Emission", alpha=False, parent=self)
        self.strength = NumberField("Strength", 0.0, min=0.0, soft_max=5.0, slider=True, parent=self)
        for w in (self.base, self.rough, self.metal, self.emis, self.strength):
            w.changed.connect(self._edited)
        if path:
            self.load(path)

    def load(self, path):
        self.path = path
        m = load_json(path, DEFAULT_MATERIAL)
        self.title.text = os.path.splitext(os.path.basename(path))[0]
        self.base.set_value(tuple(m["base_color"]), emit=False)
        self.rough.set_value(m["roughness"], emit=False)
        self.metal.set_value(m["metallic"], emit=False)
        self.emis.set_value(tuple(m["emission"])[:3] + (1.0,), emit=False)
        self.strength.set_value(m["emission_strength"], emit=False)
        self.ball.material = m
        self.ball.invalidate()

    def _edited(self, _v=None):
        m = dict(DEFAULT_MATERIAL, base_color=list(self.base.value), roughness=self.rough.value,
                 metallic=self.metal.value, emission=list(self.emis.value[:3]), emission_strength=self.strength.value)
        self.ball.material = m
        self.ball.invalidate()
        if self.path:
            try:
                save_json(self.path, m)
            except OSError:
                pass
            self.changed.emit(self.path)


class MaterialSlot(ValueWidget):
    """Holds a material asset path; drop a material from the AssetBrowser on it."""

    hover_redraw = True

    def __init__(self, value=None, *, label="Material", **kw):
        self.label = label
        super().__init__(value, **kw)

    def coerce(self, v):
        return None if v in (None, "") else os.path.abspath(v)

    def preferred_size(self, ui):
        return (200 * ui.theme.scale, 40 * ui.theme.scale)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        lw = ui.label_width(self, self.label)
        ui.text(dl, self.label, self.x + 2, self.y, lw - 4, self.h, t.text)
        x = self.x + lw
        w = self.w - lw
        drop = ui.drag is not None and ui.drag.target is self
        dl.box(x, self.y, w, self.h, shade(t.field, -0.1), border=t.accent if (drop or self.hovered) else t.outline,
               radius=t.radius * s)
        d = self.h - 6 * s
        if self._value and os.path.isfile(self._value):
            draw_material_ball(dl, x + 3 * s, self.y + 3 * s, d, load_json(self._value, DEFAULT_MATERIAL),
                               radius=4 * s)
            ui.text(dl, os.path.splitext(os.path.basename(self._value))[0], x + d + 12 * s, self.y, w - d - 18 * s,
                    self.h, t.text)
        else:
            dl.box(x + 3 * s, self.y + 3 * s, d, d, (0, 0, 0, 0), border=with_alpha(t.text_dim, 0.5),
                   radius=d / 2, border_width=1.5 * s)
            ui.text(dl, "Drop a material here", x + d + 12 * s, self.y, w - d - 18 * s, self.h, t.text_dim)

    def accepts_drop(self, payload):
        return (isinstance(payload, dict) and payload.get("type") == "asset"
                and any(a.kind == "material" for a in payload.get("assets", [])))

    def on_drop(self, ui, payload, x, y):
        for a in payload["assets"]:
            if a.kind == "material":
                self.set_value(a.path, undo=True)
                return

    def copy_text(self):
        return self._value or ""

    def paste_text(self, s):
        if s.strip().lower().endswith((".vmat", ".mat")):
            self.set_value(s.strip(), undo=True)
