# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Modal dialogs and a cross-platform file dialog (Linux, Windows, macOS).

    FileDialog.open_file(ui, on_done, filters=[("Images", "*.png;*.jpg"), ("All files", "*")])
    FileDialog.save_file(ui, on_done, default_name="scene.json")
    FileDialog.choose_folder(ui, on_done)
    MessageBox.ask(ui, "Delete 3 files?", on_done, buttons=("Delete", "Cancel"), icon="warning")
    InputDialog.ask(ui, "New folder", "Name", on_done, default="untitled")

``on_done`` receives the result (a path, a list of paths for multi-select,
the clicked button label, the typed text) or ``None`` when cancelled.

The file dialog is drawn by Vitrail itself, so it looks the same everywhere
and never blocks your game loop.  It only uses ``os``/``pathlib``: drive
letters and the hidden attribute on Windows, dot-files and XDG user folders
(localised Desktop / Documents ...) on Linux, ``/Volumes`` on macOS.
"""
from __future__ import annotations

import fnmatch
import os
import re
import stat
import string
import sys
import time

import pygame

from .core import Popup, Signal
from .layout import Column, Row, Spacer
from .theme import shade
from .tree import TreeItem, TreeView
from .widgets import Button, Checkbox, Dropdown, Label, TextField


# ============================================================================ Dialog
class Dialog(Popup, Column):
    """Modal window with a draggable title bar. Subclass or fill ``body``."""

    modal = True
    close_on_outside = False

    def __init__(self, title="", *, width=None, height=None, on_result=None, closable=True, icon=None,
                 padding=12, spacing=8):
        super().__init__(padding=padding, spacing=spacing)
        self.title = title
        self.icon = icon
        self.dialog_size = (width, height)
        self.closable = closable
        self.result = None
        self.finished = Signal()
        if on_result is not None:
            self.finished.connect(on_result)
        self._pos = None
        self._drag = None
        self._close_hot = False
        self.default_button = None

    # -- structure helpers ------------------------------------------------------------
    def add_buttons(self, *labels, default=None, cancel=None):
        """Right-aligned footer buttons; clicking one finishes with its label
        (``cancel`` finishes with None)."""
        row = Row(spacing=6, parent=self)
        Spacer(parent=row)
        made = []
        for lab in labels:
            b = Button(lab, primary=(lab == default), expand=0, parent=row)
            b.clicked.connect(lambda lab=lab: self.done(None if lab == cancel else lab))
            made.append(b)
            if lab == default:
                self.default_button = b
        return made

    # -- geometry -----------------------------------------------------------------------
    def title_h(self, ui):
        return ui.theme.h + 12 * ui.theme.scale

    def preferred_size(self, ui):
        w, h = Column.preferred_size(self, ui)
        h += self.title_h(ui)
        w = max(w, ui.text_width(self.title, bold=True) + 90 * ui.theme.scale, 260 * ui.theme.scale)
        fw, fh = self.dialog_size
        if fw:
            w = max(w, fw * ui.theme.scale)
        if fh:
            h = max(h, fh * ui.theme.scale)
        sw, sh = ui.size
        return (min(w, sw - 16), min(h, sh - 16))

    def content_box(self):
        th = self.title_h(self.ui)
        return self.x, self.y + th, self.w, self.h - th

    def place(self, ui):
        mw, mh = self.measure(ui)
        sw, sh = ui.size
        if self._pos is None:
            x, y = (sw - mw) / 2, (sh - mh) / 2.4
        else:
            x, y = self._pos
        x = max(0, min(x, sw - mw))
        y = max(0, min(y, sh - mh))
        self.arrange(round(x), round(y), round(mw), round(mh))

    def _close_rect(self, ui):
        th = self.title_h(ui)
        sz = th - 10 * ui.theme.scale
        return (self.x + self.w - sz - 6 * ui.theme.scale, self.y + 5 * ui.theme.scale, sz, sz)

    # -- painting -----------------------------------------------------------------------
    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = (t.radius + 2) * s
        th = self.title_h(ui)
        dl.shadow(self.x, self.y, self.w, self.h, (0, 0, 0, 0.6), r, 22 * s)
        dl.box(self.x, self.y, self.w, self.h, t.panel, border=t.outline, radius=r)
        dl.box(self.x, self.y, self.w, th, shade(t.panel_header_bg, t.emboss), shade(t.panel_header_bg, -t.emboss),
               t.outline, (r, r, 0, 0))
        tx = self.x + 12 * s
        if self.icon:
            tx += ui.icon(dl, self.icon, tx, self.y + (th - ui.icon_size()) / 2, None, t.accent) + 8 * s
        ui.text(dl, self.title, tx, self.y, self.w - (tx - self.x) - 44 * s, th, t.text, bold=True)
        if self.closable:
            cx, cy, cw, ch = self._close_rect(ui)
            if self._close_hot:
                dl.box(cx, cy, cw, ch, shade(t.panel_header_bg, 0.12), radius=t.radius * s)
            ui.icon_centered(dl, "close", cx, cy, cw, ch, 14, t.text if self._close_hot else t.text_dim)
        self.draw_children(dl, ui)

    # -- input -----------------------------------------------------------------------------
    def widget_at(self, px, py):
        if not self.contains(px, py):
            return None
        if py < self.y + self.title_h(self.ui):
            return self
        return Column.widget_at(self, px, py)

    def on_mouse_down(self, ui, ev):
        if ev.button != 1 or ev.y >= self.y + self.title_h(ui):
            return False
        cx, cy, cw, ch = self._close_rect(ui)
        if self.closable and cx <= ev.x < cx + cw and cy <= ev.y < cy + ch:
            self.done(None)
            return True
        self._drag = (ev.x - self.x, ev.y - self.y)
        return True

    def on_mouse_move(self, ui, ev):
        if self._drag is not None:
            self._pos = (ev.x - self._drag[0], ev.y - self._drag[1])
            self.invalidate(layout=True)
            return
        cx, cy, cw, ch = self._close_rect(ui)
        hot = cx <= ev.x < cx + cw and cy <= ev.y < cy + ch
        if hot != self._close_hot:
            self._close_hot = hot
            self.invalidate()

    def on_mouse_up(self, ui, ev):
        self._drag = None

    def on_hover(self, ui, hovered):
        if not hovered and self._close_hot:
            self._close_hot = False
            self.invalidate()

    def on_key(self, ui, e):
        if e.key == pygame.K_ESCAPE and self.closable:
            self.done(None)
            return True
        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.default_button is not None:
            self.default_button.clicked.emit()
            return True
        return False

    # -- lifecycle --------------------------------------------------------------------------
    def open(self, ui):
        ui.ensure_text_input()
        ui.open_popup(self)
        return self

    def done(self, result=None):
        self.result = result
        ui = self.ui
        if ui is not None:
            ui.close_popup(self)
        self.finished.emit(result)


class MessageBox(Dialog):
    def __init__(self, text, title="", *, buttons=("OK",), icon="info", default=None, cancel=None, **kw):
        super().__init__(title or "Message", icon=icon, **kw)
        for line in str(text).split("\n"):
            Label(line, parent=self)
        Spacer(4, parent=self)
        self.add_buttons(*buttons, default=default or buttons[0], cancel=cancel)

    @classmethod
    def ask(cls, ui, text, on_result=None, *, title="", buttons=("OK",), icon="info", default=None, cancel=None):
        return cls(text, title, buttons=buttons, icon=icon, default=default, cancel=cancel,
                   on_result=on_result).open(ui)


class InputDialog(Dialog):
    def __init__(self, title, label="", default="", *, placeholder="", ok="OK", **kw):
        super().__init__(title, icon="edit", **kw)
        self.field = TextField(default, label=label, placeholder=placeholder, parent=self)
        Spacer(2, parent=self)
        self.add_buttons("Cancel", ok, default=ok, cancel="Cancel")
        self.ok = ok

    def done(self, result=None):
        if result == self.ok:
            if self.field.editing:
                self.field._finish(self.ui, True)
            result = self.field.value
        super().done(result)

    def open(self, ui):
        super().open(ui)
        ui.layout()
        self.field.begin_text_edit(ui)
        return self

    def on_key(self, ui, e):
        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.done(self.ok)
            return True
        return super().on_key(ui, e)

    @classmethod
    def ask(cls, ui, title, label, on_result=None, *, default="", placeholder=""):
        return cls(title, label, default, placeholder=placeholder, on_result=on_result).open(ui)


# ============================================================================ file system helpers
_EXT_ICONS = {
    "file-image": "png jpg jpeg gif bmp tga webp svg ico tif tiff psd exr hdr dds",
    "file-audio": "wav mp3 ogg flac aac m4a opus mid midi",
    "file-video": "mp4 mkv avi mov webm wmv flv m4v",
    "file-code": "py pyw pyi c h cpp hpp cc cs java js ts jsx tsx rs go rb php lua glsl vert frag hlsl sh bat ps1 "
                 "json toml yaml yml xml html css nim kt swift zig sql",
    "file-archive": "zip rar 7z tar gz bz2 xz tgz whl jar",
    "file-text": "txt md rst log ini cfg conf csv tsv rtf doc docx odt pdf",
    "cube": "obj fbx gltf glb blend stl ply dae 3ds usd usdz",
}
_EXT_MAP = {ext: icon for icon, exts in _EXT_ICONS.items() for ext in exts.split()}


def icon_for(path, is_dir=False):
    if is_dir:
        return "folder"
    ext = os.path.splitext(path)[1][1:].lower()
    return _EXT_MAP.get(ext, "file")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _is_hidden(entry):
    if entry.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes
            return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
        except (OSError, AttributeError):
            return False
    return False


def list_drives():
    """Filesystem roots: drive letters on Windows, '/' (+ volumes) elsewhere."""
    if os.name == "nt":
        try:
            return list(os.listdrives())          # Python 3.12+
        except AttributeError:
            pass
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]
        except Exception:
            return [f"{c}:\\" for c in "CDEFGH" if os.path.exists(f"{c}:\\")]
    return ["/"]


def _xdg_dirs(home):
    """Localised user folders from ~/.config/user-dirs.dirs (Linux)."""
    out = {}
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")), "user-dirs.dirs")
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'\s*XDG_(\w+)_DIR\s*=\s*"(.*)"', line)
                if m:
                    out[m.group(1)] = m.group(2).replace("$HOME", home)
    except OSError:
        pass
    return out


def user_places():
    """[(label, path, icon)] for the sidebar."""
    home = os.path.expanduser("~")
    places = [("Home", home, "home")]
    xdg = _xdg_dirs(home) if sys.platform.startswith("linux") else {}
    for key, label, icon in (("DESKTOP", "Desktop", "monitor"), ("DOCUMENTS", "Documents", "file-text"),
                             ("DOWNLOAD", "Downloads", "download"), ("PICTURES", "Pictures", "image"),
                             ("MUSIC", "Music", "music"), ("VIDEOS", "Videos", "video")):
        path = xdg.get(key) or os.path.join(home, label)
        if os.path.isdir(path) and os.path.normpath(path) != os.path.normpath(home):
            places.append((os.path.basename(path.rstrip("/\\")) or label, path, icon))
    for d in list_drives():
        places.append((d if os.name == "nt" else "Computer", d, "drive"))
    vol_roots = ["/Volumes"] if sys.platform == "darwin" else []
    if sys.platform.startswith("linux"):
        user = os.environ.get("USER", "")
        vol_roots += [f"/media/{user}", f"/run/media/{user}", "/mnt"]
    for root in vol_roots:
        try:
            for e in os.scandir(root):
                if e.is_dir():
                    places.append((e.name, e.path, "drive"))
        except OSError:
            pass
    return places


def _parent(path):
    """Parent folder; a filesystem root ('/', 'C:\\') is its own parent."""
    parent = os.path.dirname(os.path.normpath(path))
    return parent or path


def parse_filter(patterns):
    return [p.strip().lower() for p in re.split(r"[;,\s]+", patterns) if p.strip()]


# ============================================================================ FileDialog
class FileDialog(Dialog):
    """Vitrail's own file browser. modes: "open", "open_multiple", "save", "folder"."""

    last_dir = None
    bookmarks = []   # extra (label, path) shown in the sidebar

    def __init__(self, mode="open", *, title=None, start_dir=None, filters=None, default_name="",
                 confirm_overwrite=True, show_hidden=False, on_result=None):
        titles = {"open": "Open File", "open_multiple": "Open Files", "save": "Save File", "folder": "Choose Folder"}
        icons = {"open": "folder-open", "open_multiple": "folder-open", "save": "save", "folder": "folder"}
        super().__init__(title or titles[mode], width=820, height=520, on_result=on_result, icon=icons[mode],
                         padding=10, spacing=8)
        self.mode = mode
        self.filters = filters or [("All files", "*")]
        self.filter_index = 0
        self.confirm_overwrite = confirm_overwrite
        self.show_hidden = show_hidden
        self.history, self.future = [], []
        self.cwd = None

        # toolbar
        bar = Row(spacing=4, parent=self)
        self.btn_back = Button(icon="arrow-left", tooltip="Back (Alt+Left)", expand=0, parent=bar)
        self.btn_fwd = Button(icon="arrow-right", tooltip="Forward (Alt+Right)", expand=0, parent=bar)
        self.btn_up = Button(icon="arrow-up", tooltip="Parent folder (Backspace)", expand=0, parent=bar)
        self.btn_refresh = Button(icon="refresh", tooltip="Reload (F5)", expand=0, parent=bar)
        self.path_field = TextField("", placeholder="path", parent=bar)
        self.path_field.submit_on_enter = False
        self.btn_newdir = Button(icon="folder-plus", tooltip="New folder", expand=0, parent=bar)
        self.hidden_box = Checkbox("Hidden", show_hidden, expand=0, parent=bar,
                                   tooltip="Show hidden files (Ctrl+H)")
        self.btn_back.clicked.connect(self.go_back)
        self.btn_fwd.clicked.connect(self.go_forward)
        self.btn_up.clicked.connect(self.go_up)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_newdir.clicked.connect(self._new_folder)
        self.path_field.changed.connect(self._path_typed)
        self.hidden_box.changed.connect(self._toggle_hidden)

        # main area: places | files
        main = Row(spacing=8, parent=self, fill=True)
        self.places = TreeView(rows=6, width=180, multi_select=False, show_root_lines=False, parent=main)
        self.places.expand = 0
        self.files = TreeView(columns=[("Name", None), ("Size", 90), ("Modified", 140)], rows=8,
                              multi_select=(mode == "open_multiple"), parent=main)
        self.files.sorter = self._sorter
        self.files.sort_by(0)
        self.files.selection_changed.connect(self._selection_changed)
        self.files.activated.connect(self._activated)
        self.places.selection_changed.connect(self._place_picked)
        self._fill_places()

        # name + filter row
        bottom = Row(spacing=8, parent=self)
        self.name_field = TextField(default_name, label="Folder" if mode == "folder" else "File name",
                                    placeholder="" if mode != "folder" else "(current folder)", parent=bottom)
        if mode == "folder":
            self.name_field.visible = False
        self.filter_box = Dropdown([(i, lab + (f"  ({pat})" if pat != "*" else "")) for i, (lab, pat) in
                                    enumerate(self.filters)], 0, width=230, expand=0, parent=bottom)
        if mode == "folder":
            self.filter_box.visible = False
        self.filter_box.changed.connect(self._filter_changed)
        self.status = Label("", dim=True, parent=self)
        ok = {"save": "Save", "folder": "Select Folder"}.get(mode, "Open")
        self.ok_label = ok
        row = Row(spacing=6, parent=self)
        Spacer(parent=row)
        b_cancel = Button("Cancel", expand=0, width=90, parent=row)
        self.btn_ok = Button(ok, primary=True, expand=0, width=110, parent=row)
        b_cancel.clicked.connect(lambda: self.done(None))
        self.btn_ok.clicked.connect(self.accept)
        self.default_button = self.btn_ok

        start = start_dir or FileDialog.last_dir or os.getcwd()
        self.navigate(os.path.expanduser(start), record=False)

    # -- convenience constructors -------------------------------------------------------
    @classmethod
    def open_file(cls, ui, on_result, *, filters=None, start_dir=None, multiple=False, title=None):
        return cls("open_multiple" if multiple else "open", filters=filters, start_dir=start_dir,
                   title=title, on_result=on_result).open(ui)

    @classmethod
    def save_file(cls, ui, on_result, *, filters=None, start_dir=None, default_name="", title=None,
                  confirm_overwrite=True):
        return cls("save", filters=filters, start_dir=start_dir, default_name=default_name, title=title,
                   confirm_overwrite=confirm_overwrite, on_result=on_result).open(ui)

    @classmethod
    def choose_folder(cls, ui, on_result, *, start_dir=None, title=None):
        return cls("folder", start_dir=start_dir, title=title, on_result=on_result).open(ui)

    # -- places -----------------------------------------------------------------------------
    def _fill_places(self):
        items = [TreeItem(label, icon=icon, data=path) for label, path, icon in user_places()]
        items += [TreeItem(label, icon="bookmark", data=path) for label, path in self.bookmarks]
        self.places.set_items(items)

    def _place_picked(self, sel):
        if sel and sel[0].data and os.path.normpath(sel[0].data) != os.path.normpath(self.cwd or ""):
            self.navigate(sel[0].data)

    # -- listing -----------------------------------------------------------------------------
    @staticmethod
    def _sorter(items, col, desc):
        def key(it):
            k = it.sort_keys[col] if it.sort_keys else it.text.lower()
            return k
        dirs = sorted([i for i in items if i.data[1]], key=key, reverse=desc)
        files = sorted([i for i in items if not i.data[1]], key=key, reverse=desc)
        return dirs + files

    def _patterns(self):
        return parse_filter(self.filters[self.filter_index][1])

    def _list(self, path):
        pats = self._patterns()
        match_all = not pats or "*" in pats or "*.*" in pats
        items = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    is_dir = e.is_dir()
                except OSError:
                    continue
                if not self.show_hidden and _is_hidden(e):
                    continue
                if not is_dir and (self.mode == "folder" or
                                   (not match_all and not any(fnmatch.fnmatch(e.name.lower(), p) for p in pats))):
                    continue
                try:
                    st = e.stat()
                    size, mtime = (0 if is_dir else st.st_size), st.st_mtime
                except OSError:
                    size, mtime = 0, 0.0
                values = ("" if is_dir else human_size(size),
                          time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else "")
                items.append(TreeItem(e.name, icon=icon_for(e.name, is_dir), data=(e.path, is_dir), values=values,
                                      sort_keys=(e.name.lower(), size, mtime),
                                      icon_color=self._icon_tint(is_dir)))
        return items

    def _icon_tint(self, is_dir):
        ui = self.ui
        if ui is None:
            return (0.95, 0.75, 0.35, 1.0) if is_dir else None
        return (0.95, 0.75, 0.35, 1.0) if is_dir else ui.theme.text_dim

    def navigate(self, path, record=True):
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        try:
            items = self._list(path)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError) as exc:
            self._set_status(f"Cannot open {path}: {exc.strerror or exc}", error=True)
            self.path_field.set_value(self.cwd or "", emit=False)
            return False
        if record and self.cwd and self.cwd != path:
            self.history.append(self.cwd)
            self.future.clear()
        self.cwd = path
        FileDialog.last_dir = path
        self.files.set_items(items)
        self.path_field.set_value(path, emit=False)
        n_dirs = sum(1 for i in items if i.data[1])
        self._set_status(f"{n_dirs} folders, {len(items) - n_dirs} files")
        self.btn_back.enabled = bool(self.history)
        self.btn_fwd.enabled = bool(self.future)
        self.btn_up.enabled = _parent(path) != path
        return True

    def _set_status(self, text, error=False):
        self.status.text = text
        self.status.color = (0.95, 0.45, 0.4, 1.0) if error else None
        self.status.invalidate()

    def reload(self):
        sel = [i.data[0] for i in self.files.selection]
        self.navigate(self.cwd, record=False)
        keep = [i for i in self.files.items if i.data[0] in sel]
        if keep:
            self.files.select(keep)

    def go_up(self):
        parent = _parent(self.cwd)
        if parent != self.cwd:
            old = self.cwd
            if self.navigate(parent):
                for it in self.files.items:
                    if os.path.normpath(it.data[0]) == os.path.normpath(old):
                        self.files.select([it], emit=False)
                        self.files.reveal(it)

    def go_back(self):
        if self.history:
            self.future.append(self.cwd)
            self.navigate(self.history.pop(), record=False)

    def go_forward(self):
        if self.future:
            self.history.append(self.cwd)
            self.navigate(self.future.pop(), record=False)

    def _path_typed(self, text):
        if text and os.path.normpath(os.path.expanduser(text)) != os.path.normpath(self.cwd or ""):
            p = os.path.expanduser(text)
            if os.path.isfile(p) and self.mode != "folder":
                self.navigate(os.path.dirname(p))
                self.name_field.set_value(os.path.basename(p))
            else:
                self.navigate(p)

    def _toggle_hidden(self, on):
        self.show_hidden = on
        self.reload()

    def _filter_changed(self, i):
        self.filter_index = i
        self.reload()

    def _new_folder(self):
        def made(name):
            if not name:
                return
            path = os.path.join(self.cwd, name)
            try:
                os.makedirs(path, exist_ok=False)
            except OSError as exc:
                self._set_status(f"Could not create folder: {exc.strerror or exc}", error=True)
                return
            self.reload()
            for it in self.files.items:
                if it.data[0] == path:
                    self.files.select([it])
                    self.files.reveal(it)
        base, n = "New Folder", 1
        name = base
        while os.path.exists(os.path.join(self.cwd, name)):
            n += 1
            name = f"{base} {n}"
        InputDialog.ask(self.ui, "New Folder", "Name", made, default=name)

    # -- selection / accept ------------------------------------------------------------------
    def _selection_changed(self, sel):
        files = [i for i in sel if not i.data[1]]
        if self.mode == "open_multiple" and len(files) > 1:
            self.name_field.set_value(" ".join(f'"{i.text}"' for i in files), emit=False)
        elif files and self.mode != "folder":
            self.name_field.set_value(files[0].text, emit=False)

    def _activated(self, item):
        if item.data[1]:
            self.navigate(item.data[0])
        else:
            self.files.select([item])
            self.accept()

    def _with_default_ext(self, path):
        if os.path.splitext(path)[1]:
            return path
        for p in self._patterns():
            m = re.fullmatch(r"\*\.([\w]+)", p)
            if m:
                return path + "." + m.group(1)
        return path

    def accept(self):
        if self.name_field.editing:
            self.name_field._finish(self.ui, True)
        if self.mode == "folder":
            sel = [i for i in self.files.selection if i.data[1]]
            self.done(sel[0].data[0] if sel else self.cwd)
            return
        typed = self.name_field.value.strip()
        names = re.findall(r'"([^"]+)"', typed) if '"' in typed else ([typed] if typed else [])
        if not names:
            sel = [i for i in self.files.selection]
            if len(sel) == 1 and sel[0].data[1]:
                self.navigate(sel[0].data[0])
                return
            names = [i.text for i in sel if not i.data[1]]
        if not names:
            self._set_status("Choose a file first", error=True)
            return
        paths = [n if os.path.isabs(os.path.expanduser(n)) else os.path.join(self.cwd, n) for n in names]
        paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self.navigate(paths[0])
            self.name_field.set_value("", emit=False)
            return
        if self.mode == "save":
            path = self._with_default_ext(paths[0])
            if not os.path.isdir(os.path.dirname(path)):
                self._set_status("That folder does not exist", error=True)
                return
            if os.path.exists(path) and self.confirm_overwrite:
                def confirm(answer):
                    if answer == "Replace":
                        self.done(path)
                MessageBox.ask(self.ui, f"“{os.path.basename(path)}” already exists.\nDo you want to replace it?",
                               confirm, title="Replace file?", buttons=("Cancel", "Replace"), icon="warning",
                               default="Replace", cancel="Cancel")
                return
            self.done(path)
            return
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            self._set_status(f"Not found: {os.path.basename(missing[0])}", error=True)
            return
        self.done(paths if self.mode == "open_multiple" else paths[0])

    def on_key(self, ui, e):
        alt = e.mod & pygame.KMOD_ALT
        ctrl = e.mod & (pygame.KMOD_CTRL | pygame.KMOD_META)
        if (e.key == pygame.K_BACKSPACE and not ctrl) or (alt and e.key == pygame.K_UP):
            self.go_up()
        elif alt and e.key == pygame.K_LEFT:
            self.go_back()
        elif alt and e.key == pygame.K_RIGHT:
            self.go_forward()
        elif e.key == pygame.K_F5:
            self.reload()
        elif ctrl and e.key == pygame.K_h:
            self.hidden_box.set_value(not self.hidden_box.value)
        elif ctrl and e.key == pygame.K_l:
            self.path_field.begin_text_edit(ui)
        else:
            return super().on_key(ui, e)
        return True

    def open(self, ui):
        super().open(ui)
        ui.set_focus(self.files)
        return self
