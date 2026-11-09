# SPDX-License-Identifier: MIT
"""The Heaven3D editor window, built with Vitrail.

    menu bar   File Edit Create View Play Tools Packages Help
    toolbar    Play | Save | Move Rotate Scale | + Cube Sphere Plane Light | Import
    left       Outliner (scene tree, search, drag to re-parent)
    centre     the 3D viewport (the engine draws it; clicks fall through)
    right      Inspector (every property of the selection, live)
    bottom     Console + Python | Assets | Script | Graph | Animation | API
    status     hints, selection, object count, FPS

How it sits on the engine (see attach()):
  * pygame.event.get is wrapped: Vitrail sees each event first and the
    engine gets only what the editor didn't use.  A mouse press belongs to
    whoever got it until it is released, so camera / gizmo drags that
    start in the viewport are never stolen by a panel and vice versa.
  * the classic UI renderer still draws the tool panels (terrain, building
    editor, particle studio); Vitrail draws on top of it, once per frame,
    in one instanced draw call, and only rebuilds when something changed.
"""
import collections
import json
import os
import subprocess
import sys
import time

import pygame

import vitrail as vt
from vitrail import (AssetBrowser, Button, Canvas, FileDialog, InputDialog, Label, MenuBar, MenuItem, MessageBox,
                     Tabs, Theme)

from .animation_tab import AnimationTab
from .graphs import GraphTab
from .inspector import Inspector
from .outliner import Outliner
from .scripting import ApiTab, Console, ScriptsTab, project_dir
from .widgets import Bar, Dock, PassColumn, PassRow

MODEL_EXT = (".glb", ".gltf", ".obj", ".blend", ".fbx", ".x")
MODEL_FILTERS = [("3D models", "*.glb;*.gltf;*.obj;*.blend;*.fbx;*.x"), ("glTF", "*.glb;*.gltf"), ("FBX", "*.fbx"),
                 ("DirectX / GameGuru", "*.x"), ("OBJ", "*.obj"),
                 ("Blender", "*.blend"), ("All files", "*")]
PROJECT_FILTERS = [("Heaven3D project", "*.heaven3d"), ("All files", "*")]
PREFS_FILE = os.path.join(os.path.expanduser("~"), ".heaven3d", "editor.json")


def load_prefs():
    try:
        with open(PREFS_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class Editor:
    LEFT_W, RIGHT_W, BOTTOM_H = 250, 340, 250

    def __init__(self, h3d, engine):
        self.h3d = h3d
        self.engine = engine
        self.prefs = load_prefs()
        self.ui = None
        self._installed = False
        self._pending_log = collections.deque()
        self._owner = {}                       # mouse button -> "ui" / "engine"
        self._last_sel = object()
        self._next_sync = 0.0
        self._was_playing = False
        self._gizmo_drag = None
        self._toast_until = 0.0
        self._fps_at = 0.0
        self._pkg_count = -1
        self._display_hints = []
        self._restore = []                     # undo actions for uninstall()

    # =================================================================== install / uninstall
    def install(self):
        e = self.engine
        theme = Theme(scale=float(self.prefs.get("scale", 1.0)))
        self.ui = vt.UI(e.ctx, (e.width, e.height), theme)
        self.build()
        # events
        orig_get = pygame.event.get

        def get(*a, **kw):
            evs = orig_get(*a, **kw)
            if a or kw or not self._installed:
                return evs
            return self.route(evs)
        pygame.event.get = get
        self._restore.append(lambda: setattr(pygame.event, "get", orig_get))
        # drawing: after the classic UI pass (tool panels), before the flip
        orig_render = e.ui.render

        def render():
            orig_render()
            self.frame()
        e.ui.render = render
        self._restore.append(lambda: e.ui.__dict__.pop("render", None))
        # The classic layout isn't drawn while engine._vt is set (see
        # src/world/procedural_building.py); the tool panels and anything a
        # package draws around _build_ui still are.
        # the view gizmo sits in the viewport's corner, not the window's
        vg = getattr(e, "view_gizmo", None)
        if vg is not None:
            cls = type(vg)
            old = (cls._VIEWPORT_RIGHT, cls._VIEWPORT_TOP)
            self._restore.append(lambda: (setattr(cls, "_VIEWPORT_RIGHT", old[0]),
                                          setattr(cls, "_VIEWPORT_TOP", old[1])))
        # the log
        Log = self.h3d.Log
        prev_cb = Log._console_cb

        def log_cb(line):
            if prev_cb is not None:
                try:
                    prev_cb(line)
                except Exception:
                    pass
            self._pending_log.append(line)
        Log.set_console(log_cb)
        self._restore.append(lambda: Log.set_console(prev_cb))
        for line in list(getattr(e, "_console_lines", []))[-60:]:
            self._pending_log.append(line)
        e._vt = self
        self._installed = True
        Log.info("[Editor] Vitrail editor ready (python Heaven3D.py --classic-ui for the old one)")
        return self

    def uninstall(self):
        self._installed = False
        for fn in reversed(self._restore):
            try:
                fn()
            except Exception:
                pass
        self._restore.clear()
        if getattr(self.engine, "_vt", None) is self:
            self.engine._vt = None

    # =================================================================== layout
    def build(self):
        ui, e = self.ui, self.engine
        root = PassColumn(spacing=0)
        ui.set_root(root)
        self.menubar = MenuBar(self.menus(), parent=root)
        self.toolbar = self.build_toolbar(root)
        mid = PassRow(spacing=0, fill=True, parent=root)
        self.left = Dock(width=self.LEFT_W, expand=0, parent=mid)
        Label("Outliner", bold=True, icon="list", parent=self.left)
        self.outliner = Outliner(self)
        self.left.add(self.outliner.box)
        self.viewport = Canvas(parent=mid)
        self.play_pill = Bar(color=(0.08, 0.09, 0.1, 0.82))
        Button("Stop", self.toggle_play, icon="stop", primary=True, width=80, expand=0, parent=self.play_pill,
               tooltip="Stop the game (Ctrl+P / F5)")
        self.pill_label = Label("Esc frees the mouse", dim=True, width=150, expand=0, parent=self.play_pill)
        self.viewport.place(self.play_pill, 8, 8)
        self.right = Dock(width=self.RIGHT_W, expand=0, parent=mid)
        Label("Inspector", bold=True, icon="sliders", parent=self.right)
        self.inspector = Inspector(self)
        self.right.add(self.inspector.scroll)
        self.bottom = Dock(height=int(self.prefs.get("bottom_h", self.BOTTOM_H)), padding=2, parent=root)
        self.tabs = Tabs(fill=True, parent=self.bottom)
        self.console = Console(self, self.tabs.tab("Console", icon="terminal"))
        self.assets_page = self.tabs.tab("Assets", icon="folder")
        self.assets = None
        self.scripts = ScriptsTab(self, self.tabs.tab("Script", icon="code"))
        self.graph = GraphTab(self, self.tabs.tab("Graph", icon="node"))
        self.anim = AnimationTab(self, self.tabs.tab("Animation", icon="film"))
        self.api = ApiTab(self, self.tabs.tab("API", icon="help"))
        self.tabs.changed.connect(self._tab_changed)
        self.status = Bar(parent=root, color=self.ui.theme.panel)
        self.status_text = Label("", dim=True, parent=self.status)
        self.status_right = Label("", align="right", dim=True, width=420, expand=0, parent=self.status)
        self.apply_visibility()
        self.outliner.rebuild()
        self.select(e.selected, from_editor=False)
        self.ui.on_update.connect(self._on_update)
        # keyboard shortcuts that aren't on a menu
        ui.add_shortcut("Ctrl+Shift+S", self.save_as)

    def build_toolbar(self, root):
        bar = Bar(parent=root)
        self.play_btn = Button("Play", self.toggle_play, icon="play", primary=True, width=86, expand=0,
                               tooltip="Play the game here (Ctrl+P / F5)", parent=bar)
        Button(icon="save", on_click=self.save, tooltip="Save (Ctrl+S)", expand=0, parent=bar)
        Button(icon="folder-open", on_click=self.open_dialog, tooltip="Open project (Ctrl+O)", expand=0, parent=bar)
        vt.Separator(parent=bar, width=8, expand=0)
        G = self.h3d.Gizmo
        self.tool_btns = {}
        for mode, icon, tip in ((G.MOVE, "move", "Move (W)"), (G.ROTATE, "rotate", "Rotate (E)"),
                                (G.SCALE, "scale", "Scale (R)")):
            self.tool_btns[mode] = Button(icon=icon, on_click=lambda m=mode: self.set_tool(m), tooltip=tip,
                                          expand=0, parent=bar)
        vt.Separator(parent=bar, width=8, expand=0)
        for icon, tip, fn in (("cube", "Add cube", lambda: self.create("cube")),
                              ("sphere", "Add sphere", lambda: self.create("sphere")),
                              ("square", "Add plane", lambda: self.create("plane")),
                              ("light", "Add point light", lambda: self.create("point")),
                              ("user", "Add player spawn", lambda: self.create("player")),
                              ("target", "Add enemy spawn", lambda: self.create("enemy"))):
            Button(icon=icon, on_click=fn, tooltip=tip, expand=0, parent=bar)
        Button("Import", self.import_dialog, icon="download", tooltip="Import a 3D model (Ctrl+I)", expand=0,
               parent=bar)
        vt.Separator(parent=bar, width=8, expand=0)
        Button("Terrain", lambda: self.run(self.engine.toggle_terrain_editor), icon="shapes", expand=0, parent=bar)
        Button("Particles", lambda: self.run(self.engine.toggle_particle_studio), icon="sparkle", expand=0,
               parent=bar)
        Button("Buildings", self.toggle_buildings, icon="home", expand=0, parent=bar)
        self.project_label = Label("", align="right", dim=True, parent=bar)
        return bar

    def apply_visibility(self):
        p = self.prefs
        playing = bool(getattr(self.engine, "_playing", False))
        hide = playing and p.get("hide_in_play", True)
        tool_open = self.tool_panel_open()
        self.left.visible = p.get("show_outliner", True) and not hide and not tool_open
        self.right.visible = p.get("show_inspector", True) and not hide and not tool_open
        self.bottom.visible = p.get("show_bottom", True) and not hide and not tool_open
        # while playing, the game gets the whole window: a small Stop pill floats over it
        self.menubar.visible = self.toolbar.visible = self.status.visible = not hide
        self.play_pill.visible = hide
        self.ui.invalidate(layout=True)

    def tool_panel_open(self):
        """A classic panel is up (terrain / particles / buildings, or one a
        package draws): the docks step aside so it can be seen and used."""
        e = self.engine
        if getattr(getattr(e, "ui", None), "_panel_rects", None):
            return True
        for attr in ("_tp_editor", "_gu_studio", "procedural_generator"):
            t = getattr(e, attr, None)
            if t is not None and getattr(t, "panel_open", False):
                return True
        return False

    # =================================================================== menus
    def create_menu_items(self):
        c = self.create
        return [
            MenuItem("Cube", lambda: c("cube"), icon="cube"),
            MenuItem("Sphere", lambda: c("sphere"), icon="sphere"),
            MenuItem("Plane", lambda: c("plane"), icon="square"),
            MenuItem("Empty group", lambda: c("group"), icon="layers"),
            None,
            MenuItem("Point light", lambda: c("point"), icon="light"),
            MenuItem("Spot light", lambda: c("spot"), icon="light"),
            None,
            MenuItem("Player spawn", lambda: c("player"), icon="user"),
            MenuItem("Enemy spawn", lambda: c("enemy"), icon="target"),
            MenuItem("Bullet pickup", lambda: c("bullet"), icon="package"),
            MenuItem("Health pickup", lambda: c("health"), icon="heart"),
            MenuItem("Trigger point", lambda: c("trigger"), icon="flag"),
            MenuItem("Sound point", lambda: c("sound"), icon="volume"),
            None,
            MenuItem("Scripted object (visual script)", lambda: c("scripted"), icon="node"),
            MenuItem("Animated object (animator)", lambda: c("animated"), icon="film"),
            None,
            MenuItem("Import model...", self.import_dialog, icon="download"),
        ]

    def menus(self):
        e = self.engine
        G = self.h3d.Gizmo
        shading = lambda m: lambda: self.run(lambda: e.set_shading(m))  # noqa: E731
        cur_shading = lambda m: lambda: e.__dict__.get("_h3d27_view_state", {}).get("shading", "lit") == m  # noqa
        pref = self.prefs
        hint = self._hint
        return [
            ("File", [
                MenuItem("New scene", self.new_scene, icon="new"),
                MenuItem("Open project...", self.open_dialog, icon="folder-open", shortcut="Ctrl+O"),
                MenuItem("Save", self.save, icon="save", shortcut="Ctrl+S"),
                MenuItem("Save as...", self.save_as, icon="save"),
                None,
                MenuItem("Import model...", self.import_dialog, icon="download", shortcut="Ctrl+I"),
                MenuItem("Export game...", self.export, icon="upload", shortcut="Ctrl+E"),
                None,
                MenuItem("Open project folder", lambda: self.reveal(project_dir(e)), icon="folder"),
                MenuItem("Quit", lambda: pygame.event.post(pygame.event.Event(pygame.QUIT)), icon="power",
                         shortcut="Ctrl+Q"),
            ]),
            ("Edit", [
                MenuItem("Undo", lambda: self.ui.undo.undo(), icon="undo", shortcut="Ctrl+Z",
                         enabled=lambda: self.ui.undo.can_undo),
                MenuItem("Redo", lambda: self.ui.undo.redo(), icon="redo", shortcut="Ctrl+Y",
                         enabled=lambda: self.ui.undo.can_redo),
                None,
                MenuItem("Duplicate", self.duplicate, icon="copy", shortcut="Ctrl+D"),
                MenuItem("Delete", self.delete, icon="trash", shortcut="Delete"),
                MenuItem("Rename...", lambda: self.rename_dialog(e.selected), icon="edit", shortcut="F2"),
                hint(MenuItem("Focus selection", self.focus_selected, icon="target"), "F"),
                hint(MenuItem("Deselect", lambda: self.select(None), icon="close"), "Esc"),
                None,
                hint(MenuItem("Move tool", lambda: self.set_tool(G.MOVE), icon="move"), "W"),
                hint(MenuItem("Rotate tool", lambda: self.set_tool(G.ROTATE), icon="rotate"), "E"),
                hint(MenuItem("Scale tool", lambda: self.set_tool(G.SCALE), icon="scale"), "R"),
            ]),
            ("Create", self.create_menu_items()),
            ("View", [
                MenuItem("Lit", shading("lit"), checked=cur_shading("lit")),
                MenuItem("Lit + wireframe", shading("lit_wire"), checked=cur_shading("lit_wire")),
                MenuItem("Wireframe", shading("wire"), checked=cur_shading("wire")),
                MenuItem("Grid", lambda: self.run(lambda: e.set_grid_visible(
                    not e.__dict__.get("_h3d27_view_state", {}).get("grid", True))),
                    checked=lambda: e.__dict__.get("_h3d27_view_state", {}).get("grid", True)),
                None,
                MenuItem("Outliner", lambda: self.toggle_pref("show_outliner"),
                         checked=lambda: pref.get("show_outliner", True)),
                MenuItem("Inspector", lambda: self.toggle_pref("show_inspector"),
                         checked=lambda: pref.get("show_inspector", True)),
                MenuItem("Bottom panel", lambda: self.toggle_pref("show_bottom"),
                         checked=lambda: pref.get("show_bottom", True), shortcut="Ctrl+J"),
                MenuItem("Hide panels while playing", lambda: self.toggle_pref("hide_in_play"),
                         checked=lambda: pref.get("hide_in_play", True)),
                None,
                MenuItem("Interface size", submenu=[
                    MenuItem("%d%%" % (s * 100), lambda s=s: self.set_scale(s),
                             checked=lambda s=s: abs(float(pref.get("scale", 1.0)) - s) < 1e-3)
                    for s in (0.85, 1.0, 1.15, 1.3, 1.5)]),
            ]),
            ("Play", [
                MenuItem("Play / Stop", self.toggle_play, icon="play", shortcut="Ctrl+P"),
                None,
                MenuItem("Reload scripts", lambda: self.scripts.reload_all(), icon="refresh", shortcut="Ctrl+R"),
            ]),
            ("Tools", [
                MenuItem("Terrain editor", lambda: self.run(e.toggle_terrain_editor), icon="shapes"),
                MenuItem("Particle studio", lambda: self.run(e.toggle_particle_studio), icon="sparkle"),
                MenuItem("Building editor", self.toggle_buildings, icon="home"),
                MenuItem("Edit collider shape", lambda: self.run(lambda: e._phys_edit_shape(e.selected))
                         if e.selected is not None else None, icon="shield"),
                None,
                MenuItem("Python console", lambda: self.open_tab("Console", focus=True), icon="terminal"),
                MenuItem("Script editor", lambda: self.open_tab("Script"), icon="code"),
                MenuItem("New script...", lambda: self.scripts.new_script_dialog(attach_to=e.selected),
                         icon="file-plus"),
                MenuItem("Visual script (graph)", lambda: self.open_tab("Graph"), icon="node"),
                MenuItem("Animation", lambda: self.open_tab("Animation"), icon="film"),
                MenuItem("Assets", lambda: self.open_tab("Assets"), icon="folder"),
            ]),
            ("Packages", self.package_items()),
            ("Help", [
                MenuItem("API reference", lambda: self.open_tab("API"), icon="help"),
                MenuItem("Keyboard shortcuts", self.show_shortcuts, icon="keyboard"),
                MenuItem("Use the classic editor UI", self.classic_ui_info, icon="window"),
                None,
                MenuItem("About Heaven3D", self.about, icon="info"),
            ]),
        ]

    def _hint(self, item, text):
        """A shortcut shown in a menu but handled by the 3D view (it must also
        reach camera fly keys, so it is not registered with Vitrail)."""
        self._display_hints.append((item, text))
        return item

    def package_items(self):
        try:
            tools = list(self.h3d._H3D27_PKG_STATE["tools"])
        except Exception:
            tools = []
        self._pkg_count = len(tools)
        if not tools:
            return [MenuItem("No package tools - add packages in the Hub", None, enabled=False)]
        out = []
        for t in tools:
            def run(t=t):
                try:
                    t["fn"](self.engine)
                except TypeError:
                    t["fn"]()
            out.append(MenuItem(str(t.get("label", "tool")), lambda run=run: self.run(run), icon="package"))
        return out

    # =================================================================== events
    def route(self, evs):
        """Give each event to Vitrail first; return what the engine should see."""
        e, ui = self.engine, self.ui
        out = []
        playing = bool(getattr(e, "_playing", False))
        game_has_mouse = playing and bool(getattr(e, "_mouse_captured", False))
        for ev in evs:
            t = ev.type
            if t == pygame.QUIT:
                out.append(ev)
                continue
            if t == pygame.KEYDOWN and (ev.key == pygame.K_F5 or (
                    ev.key == pygame.K_p and getattr(ev, "mod", 0) & (pygame.KMOD_CTRL | pygame.KMOD_META))) and not ui.overlays:
                self.toggle_play()                 # works in play too, mouse captured or not
                continue
            if game_has_mouse:
                out.append(ev)
                continue
            try:
                if t == pygame.MOUSEBUTTONDOWN:
                    if ev.button in (1, 2, 3):
                        if ui.handle_event(ev):
                            self._owner[ev.button] = "ui"
                            continue
                        self._owner[ev.button] = "engine"
                        out.append(ev)
                    elif ui.widget_at(*ev.pos) is None:
                        out.append(ev)
                elif t == pygame.MOUSEBUTTONUP:
                    owner = self._owner.pop(ev.button, None)
                    if ev.button == 1 and ui.drag is not None and ui.widget_at(*ev.pos) is None:
                        payload = ui.drag.payload
                        ui.cancel_drag()
                        self.drop_on_viewport(payload, ev.pos)
                    ui.handle_event(ev)
                    if owner != "ui":
                        out.append(ev)
                elif t == pygame.MOUSEMOTION:
                    if "engine" in self._owner.values():
                        out.append(ev)
                    elif not ui.handle_event(ev):
                        out.append(ev)
                elif t == pygame.MOUSEWHEEL:
                    if not ui.handle_event(ev):
                        out.append(ev)
                elif t == pygame.KEYDOWN:
                    if (playing or "engine" in self._owner.values()) and ui.focus is None and not ui.overlays:
                        out.append(ev)             # game keys / camera fly keys
                    elif not ui.handle_event(ev):
                        out.append(ev)
                elif t == pygame.KEYUP:
                    ui.handle_event(ev)
                    out.append(ev)
                elif t == pygame.TEXTINPUT:
                    if not ui.handle_event(ev):
                        out.append(ev)
                else:
                    ui.handle_event(ev)
                    out.append(ev)
            except Exception as ex:                # a UI bug must never eat input
                self._report(ex)
                out.append(ev)
        return out

    def _report(self, ex):
        import traceback
        key = (type(ex).__name__, str(ex))
        seen = self.__dict__.setdefault("_seen_errors", set())
        if key not in seen:
            seen.add(key)
            self.h3d.Log.error("[Editor] %s: %s\n%s" % (type(ex).__name__, ex, traceback.format_exc()))

    # =================================================================== per frame
    def frame(self):
        e = self.engine
        now = time.perf_counter()
        try:
            self.tick(now)
            self.ui.update(now)
            self.ui.draw()
        except Exception as ex:
            self._report(ex)
        ctx = e.ctx
        ctx.disable(_GL_BLEND)
        ctx.blend_func = _GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA

    def _on_update(self, now):
        for ce in (self.scripts.code,):
            if ce.focused:
                ce.tick(self.ui)

    def tick(self, now):
        e = self.engine
        while self._pending_log:
            self.console.add(self._pending_log.popleft())
        playing = bool(getattr(e, "_playing", False))
        if playing != self._was_playing:
            self._was_playing = playing
            self.play_btn.text = "Stop" if playing else "Play"
            self.play_btn.icon = "stop" if playing else "play"
            self.play_btn.invalidate()
            if playing:
                self._sel_before_play = e.selected
                if e.selected is not None:
                    e.deselect()               # no gizmo / outline over the game
                self.ui.set_focus(None)
                self.ui.close_popups()
                self.graph.flush(force=True)
                self.anim.flush(force=True)
                self.anim.stop_preview()
            else:
                prev = getattr(self, "_sel_before_play", None)
                if prev is not None and prev.parent is not None:
                    e.select_node(prev)
            self.apply_visibility()
        if self._display_hints and self.ui.root is not None and self.menubar._registered is not None:
            for item, text in self._display_hints:
                item.shortcut = text
            self._display_hints = []
        sel = e.selected
        if sel is not self._last_sel:
            self.select(sel, from_editor=False)
        self._track_gizmo_undo()
        if now >= self._next_sync:
            self._next_sync = now + (0.5 if playing else 0.2)
            want = self.tool_panel_open()
            if want != getattr(self, "_tool_open", False):
                self._tool_open = want
                self.apply_visibility()
            if self.left.visible:
                self.outliner.sync()
            if self.right.visible:
                self.inspector.sync()
            self.graph.flush()
            if not playing:
                self.anim.flush()
            if self._pkg_count != len(getattr(self.h3d, "_H3D27_PKG_STATE", {}).get("tools", ())):
                self.menubar.menus[6] = ("Packages", self.package_items())
                self.menubar.invalidate()
            self._update_status(now)
            self._update_tools()
            self._update_viewport_rect()

    def _update_status(self, now):
        e = self.engine
        if now > self._toast_until:
            sel = e.selected
            if getattr(e, "_playing", False):
                msg = "Playing - Esc frees the mouse, Ctrl+P / F5 stops"
            elif sel is not None:
                msg = "%s  -  W move  E rotate  R scale  F focus  Ctrl+D duplicate  Del delete" % sel.name
            else:
                msg = "Right-drag: look  WASD: fly  wheel: zoom  click: select  Create menu / toolbar: add objects"
            if self.status_text.text != msg:
                self.status_text.text = msg
                self.status_text.color = None
                self.status_text.invalidate()
        if now >= self._fps_at:
            self._fps_at = now + 0.5
            n = len(self.outliner._sig or ())
            txt = "%d objects   %s   %.0f FPS   UI %.1f ms" % (
                n, "PLAYING" if getattr(e, "_playing", False) else "editing", getattr(e, "_fps", 0.0),
                self.ui.stats.get("build_ms", 0.0))
            if self.status_right.text != txt:
                self.status_right.text = txt
                self.status_right.invalidate()
            name = os.path.basename(e.project_path) if getattr(e, "project_path", None) else "unsaved scene"
            if self.project_label.text != name:
                self.project_label.text = name
                self.project_label.invalidate()

    def _update_tools(self):
        g = getattr(self.engine, "gizmo", None)
        if g is None:
            return
        for mode, b in self.tool_btns.items():
            on = g.mode == mode
            if b.primary != on:
                b.primary = on
                b.invalidate()

    def _update_viewport_rect(self):
        vg = getattr(self.engine, "view_gizmo", None)
        if vg is None or self.viewport.w <= 0:
            return
        W, H = self.engine.width, self.engine.height
        if self.play_pill.visible and self.play_pill.w > 0:
            x = round((self.viewport.w - self.play_pill.w) / 2)
            if getattr(self.play_pill, "pos", None) != (x, 8):
                self.viewport.place(self.play_pill, x, 8)
        cls = type(vg)
        cls._VIEWPORT_RIGHT = (self.viewport.x + self.viewport.w) / float(W)
        cls._VIEWPORT_TOP = 1.0 - self.viewport.y / float(H)

    def toast(self, msg, error=False):
        self.status_text.text = msg
        self.status_text.color = (1.0, 0.5, 0.5, 1.0) if error else (0.55, 0.85, 1.0, 1.0)
        self.status_text.invalidate()
        self._toast_until = time.perf_counter() + 4.0

    def log(self, text, level="INFO"):
        self.console.add(str(text), level)

    # =================================================================== selection & objects
    def select(self, node, from_editor=True):
        e = self.engine
        if from_editor:
            if node is None:
                e.deselect()
            else:
                e.select_node(node)
            node = e.selected
        self._last_sel = node
        self.inspector.show(node)
        self.outliner.show_selection(node)
        self.graph.bind(node)
        self.anim.bind(node)

    def run(self, fn):
        """Run an engine action; show its error instead of crashing the editor."""
        try:
            r = fn()
        except Exception as ex:
            self._report(ex)
            self.toast("%s: %s" % (type(ex).__name__, ex), error=True)
            return None
        self.inspector.show(self.engine.selected, force=True)
        self.outliner.sync()
        return r

    def _pdir(self):
        return project_dir(self.engine)

    def _snapshot(self, node):
        return self.h3d.ProjectSerializer._serialize_node(node, self._pdir())

    def _restore_node(self, data, parent):
        before = list(parent.children)
        self.h3d.ProjectSerializer._deserialize_node(data, parent, self.engine, self._pdir())
        new = [c for c in parent.children if c not in before]
        return new[-1] if new else None

    def create(self, kind):
        e = self.engine
        fns = {"cube": e.add_cube, "sphere": e.add_sphere, "plane": e.add_plane, "point": e.add_point_light,
               "spot": e.add_spot_light, "player": e.add_player_spawn, "enemy": e.add_enemy_spawn,
               "bullet": e.add_bullet_pickup, "health": e.add_health_pickup, "trigger": e.add_trigger_point,
               "sound": getattr(e, "add_sound_point", None)}
        if kind in ("group", "scripted", "animated"):
            n = self.h3d.SceneNode(e._unique_node_name({"group": "Group", "scripted": "Scripted",
                                                        "animated": "Animated"}[kind]))
            if kind != "group":
                n = e.add_cube(name=n.name)
            else:
                e.scene.add(n)
            if kind == "scripted":
                self.add_blueprint(n, "VisualScript", quiet=True)
            elif kind == "animated":
                self.add_blueprint(n, "Animator", quiet=True)
        else:
            fn = fns.get(kind)
            if fn is None:
                return
            if kind == "player" and e.find_player_spawn() is not None:
                self.toast("There is already a Player Spawn (only one per scene)", error=True)
                self.select(e.find_player_spawn())
                return
            n = self.run(fn)
        if n is None:
            return
        self.select(n)
        self._push_created(n, "Create " + n.name)
        if kind == "scripted":
            self.open_tab("Graph")
        elif kind == "animated":
            self.open_tab("Animation")

    def _push_created(self, n, label):
        box = {"node": n, "data": None, "parent": n.parent}

        def undo():
            nd = box["node"]
            if nd is not None and nd.parent is not None:
                box["data"] = self._snapshot(nd)
                box["parent"] = nd.parent
                self.engine._delete_node(nd)
                box["node"] = None
                self.select(None)

        def redo():
            if box["data"] is not None:
                box["node"] = self._restore_node(box["data"], box["parent"])
                if box["node"] is not None:
                    self.select(box["node"])
        self.ui.undo.push(label, undo, redo)

    def delete(self):
        e = self.engine
        n = e.selected
        if n is None or getattr(e, "_playing", False):
            return
        parent = n.parent or e.scene.root
        try:
            data = self._snapshot(n)
        except Exception:
            data = None
        was_sun, was_clouds, was_ocean = n.is_sun, n.is_clouds, n.is_ocean
        e._delete_node(n)
        self.select(None)
        if data is None:
            return
        box = {"node": None}

        def undo():
            box["node"] = self._restore_node(data, parent)
            nd = box["node"]
            if nd is not None:
                sc = e.scene
                if was_sun:
                    sc._sun_node = nd
                if was_clouds:
                    sc._clouds_node = nd
                if was_ocean:
                    sc._ocean_node = nd
                self.select(nd)

        def redo():
            if box["node"] is not None:
                e._delete_node(box["node"])
                box["node"] = None
                self.select(None)
        self.ui.undo.push("Delete " + n.name, undo, redo)

    def duplicate(self):
        e = self.engine
        n = e.selected
        if n is None or getattr(e, "_playing", False):
            return
        self.run(lambda: e._duplicate_node(n))
        if e.selected is not None and e.selected is not n:
            self._push_created(e.selected, "Duplicate " + n.name)

    def rename(self, n, name):
        name = (name or "").strip()
        if n is None or not name or name == n.name:
            return
        old = n.name
        n.name = name
        self.ui.undo.push("Rename", lambda: setattr(n, "name", old), lambda: setattr(n, "name", name))
        self.outliner.sync()

    def rename_dialog(self, n):
        if n is None:
            return
        InputDialog.ask(self.ui, "Rename", "Name", lambda v: self.rename(n, v), default=n.name)

    def toggle_visible(self, n):
        n.visible = not n.visible
        self.outliner.sync()

    def reparent(self, n, parent, index=None):
        if n is None or parent is n or parent in n.traverse():
            return
        old_parent = n.parent
        n.remove()
        parent.add_child(n)
        if index is not None and 0 <= index < len(parent.children) - 1:
            parent.children.remove(n)
            parent.children.insert(index, n)
        if old_parent is not parent:
            self.ui.undo.push("Re-parent", lambda: self.reparent(n, old_parent), lambda: self.reparent(n, parent))
        self.outliner.sync()

    def focus_selected(self):
        e = self.engine
        if e.selected is not None:
            e.camera.frame(e.selected.world_position(), 8.0)

    def set_tool(self, mode):
        if getattr(self.engine, "gizmo", None) is not None:
            self.engine.gizmo.set_mode(mode)
            self._update_tools()

    def add_blueprint(self, n, name, quiet=False):
        reg = self.h3d.BlueprintRegistry
        cls = reg.get(name) if name else None
        if n is None or cls is None:
            self.toast("Select an object and a blueprint first", error=True)
            return None
        bp = self.engine.attach_blueprint(n, cls)
        if bp is not None and name == "Animator" and not bp.properties.get("clips"):
            bp.properties["clips"] = {"Clip": {"start": 1, "end": 120, "fps": 24, "tracks": []}}
            bp.properties["clip"] = "Clip"
        if not quiet:
            self.toast("%s added to %s" % (name, n.name))
        self.inspector.show(n, force=True)
        self.graph.node = None
        self.graph.bind(n)
        self.anim.node = None
        self.anim.bind(n)
        return bp

    def remove_blueprint(self, n, bp):
        if bp in n.blueprints:
            n.blueprints.remove(bp)
        if bp in self.engine._blueprints:
            self.engine._blueprints.remove(bp)
        self.inspector.show(n, force=True)
        self.graph.node = None
        self.graph.bind(n)
        self.anim.node = None
        self.anim.bind(n)

    def _track_gizmo_undo(self):
        g = getattr(self.engine, "gizmo", None)
        if g is None:
            return
        dragging = bool(g.dragging)
        n = self.engine.selected
        if dragging and self._gizmo_drag is None and n is not None:
            self._gizmo_drag = (n, _tf(n))
        elif not dragging and self._gizmo_drag is not None:
            node, before = self._gizmo_drag
            self._gizmo_drag = None
            after = _tf(node)
            if after != before:
                self.ui.undo.push("Transform", lambda: _set_tf(node, before), lambda: _set_tf(node, after))

    # =================================================================== play / files
    def toggle_play(self):
        self.graph.flush(force=True)
        self.anim.flush(force=True)
        self.anim.stop_preview()
        self.run(self.engine.toggle_play)

    def toggle_buildings(self):
        gen = getattr(self.engine, "procedural_generator", None)
        if gen is None:
            self.toast("The building editor isn't available in this build", error=True)
            return
        self.run(gen.toggle_panel)

    def toggle_pref(self, key):
        self.prefs[key] = not self.prefs.get(key, True)
        self.save_prefs()
        self.apply_visibility()

    def set_scale(self, s):
        self.prefs["scale"] = s
        self.save_prefs()
        t = self.ui.theme
        t.scale = s
        self.ui.set_theme(t)
        self.ui.refresh()

    def save_prefs(self):
        try:
            os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
            with open(PREFS_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.prefs, fh, indent=1)
        except OSError:
            pass

    def _with_path(self, method, path, *args):
        """Run an engine action that asks for a file with a native (Tk)
        dialog, answering the dialog with ``path`` (picked in Vitrail)."""
        e = self.engine
        had = "_native_dialog" in e.__dict__
        old = e.__dict__.get("_native_dialog")
        e._native_dialog = lambda *a, **k: path
        try:
            return self.run(lambda: method(*args))
        finally:
            if had:
                e._native_dialog = old
            else:
                e.__dict__.pop("_native_dialog", None)

    def save(self):
        e = self.engine
        if getattr(e, "_playing", False):
            self.toast("Stop the game before saving", error=True)
            return
        if not e.project_path:
            self.save_as()
            return
        self.graph.flush(force=True)
        self.anim.flush(force=True)
        if self.scripts.dirty:
            self.scripts.save()
        self.run(lambda: e.save_project(e.project_path))
        self.toast("Saved " + os.path.basename(e.project_path))

    def save_as(self):
        e = self.engine

        def done(path):
            if not path:
                return
            if not path.lower().endswith(".heaven3d"):
                path += ".heaven3d"
            self.graph.flush(force=True)
            self.anim.flush(force=True)
            self.run(lambda: e.save_project(path))
            self.toast("Saved " + os.path.basename(path))
            self.refresh_assets()
        FileDialog.save_file(self.ui, done, filters=PROJECT_FILTERS, start_dir=self._pdir(),
                             default_name=os.path.basename(e.project_path or "main.heaven3d"))

    def open_dialog(self):
        FileDialog.open_file(self.ui, lambda p: self.open_project(p) if p else None, filters=PROJECT_FILTERS,
                             start_dir=self._pdir())

    def open_project(self, path):
        e = self.engine
        if getattr(e, "_playing", False):
            e.toggle_play()
        try:
            self.h3d._h3d27_import_project_scripts(os.path.dirname(os.path.abspath(path)))
        except Exception:
            pass
        self.run(lambda: e.load_project(path))
        e.project_path = path
        pygame.display.set_caption("Heaven3D v%s  -  %s" % (self.h3d.HEAVEN_VERSION,
                                                              os.path.splitext(os.path.basename(path))[0]))
        self.ui.undo = vt.UndoStack()
        self.select(None)
        self.outliner.rebuild()
        self.refresh_assets()
        self.scripts.refresh_files()
        self.toast("Opened " + os.path.basename(path))

    def new_scene(self):
        def answer(r):
            if r != "Clear scene":
                return
            e = self.engine
            if getattr(e, "_playing", False):
                e.toggle_play()
            for n in list(e.scene.root.children):
                if not (n.is_sun or n.is_clouds or n.is_ocean):
                    e._delete_node(n)
            e.project_path = None
            self.ui.undo = vt.UndoStack()
            self.select(None)
            self.toast("New scene")
        MessageBox.ask(self.ui, "Remove every object except the Sun, clouds and ocean?\nUnsaved changes are lost.",
                       "New scene", answer, buttons=("Cancel", "Clear scene"), icon="warning", cancel="Cancel")

    def import_dialog(self):
        FileDialog.open_file(self.ui, lambda p: self.import_model(p) if p else None, filters=MODEL_FILTERS,
                             start_dir=os.path.join(self._pdir(), "assets") if os.path.isdir(
                                 os.path.join(self._pdir(), "assets")) else self._pdir(),
                             title="Import model")

    def import_model(self, path, at=None):
        e = self.engine
        n = self.run(lambda: e.load_model(path))
        if n is None:
            self.toast("Couldn't import " + os.path.basename(path), error=True)
            return None
        if at is not None:
            n.position = at
        self.select(n)
        self._push_created(n, "Import " + n.name)
        self.toast("Imported " + os.path.basename(path))
        return n

    def change_spawn_mesh(self, n):
        FileDialog.open_file(self.ui, lambda p: self._with_path(self.engine._change_spawn_mesh, p, n) if p else None,
                             filters=MODEL_FILTERS, start_dir=self._pdir(), title="Custom mesh")

    def export(self):
        e = self.engine
        try:
            players = [p for p in ("windows", "linux", "macos")
                       if self.h3d.H3DPortable.find_player(self.h3d._H3D27_ENGINE_DIR, p)]
        except Exception:
            players = []
        if players and e.project_path:
            self.save()
            self.run(e.export_runtime_dialog)
            self.toast("Exporting %s - see the Console" % " + ".join(p.title() for p in players))
            self.open_tab("Console")
            return
        if not e.project_path:
            self.toast("Save the project first, then export", error=True)
            self.save_as()
            return

        def done(path):
            if path:
                old = e.project_path
                self.run(lambda: e.save_project(path))
                e.project_path = old
                self.toast("Exported %s (run it with Heaven3D_runtime.py)" % os.path.basename(path))
        FileDialog.save_file(self.ui, done, filters=PROJECT_FILTERS, start_dir=self._pdir(),
                             default_name="game.heaven3d", title="Export game")

    def drop_on_viewport(self, payload, pos):
        if not isinstance(payload, dict) or payload.get("type") != "asset":
            return
        e = self.engine
        at = None
        try:
            ro, rd = e._mouse_ray(pos)
            if abs(rd.y) > 1e-4:
                t = -ro.y / rd.y
                if 0 < t < 500:
                    at = ro + rd * t
        except Exception:
            at = None
        for a in payload.get("assets", []):
            p = getattr(a, "path", None)
            if p and p.lower().endswith(MODEL_EXT):
                self.import_model(p, at=at)

    # =================================================================== tabs
    def open_tab(self, title, focus=False):
        if not self.bottom.visible:
            self.prefs["show_bottom"] = True
            self.apply_visibility()
        if title in self.tabs.titles:
            self.tabs.select(self.tabs.titles.index(title))
        if focus and title == "Console":
            self.console.prompt.begin_text_edit(self.ui)

    def _tab_changed(self, i):
        title = self.tabs.titles[i]
        if title == "Assets":
            self.refresh_assets(create=True)
        elif title == "API":
            self.api.ensure()
        elif title == "Script" and self.scripts.path is None:
            files = self.scripts.list_scripts()
            if files:
                self.scripts.open(files[0])
            else:
                self.scripts.code.set_text("# No scripts in this project yet.\n# Press New to make a blueprint "
                                           "script: it appears in the Inspector's Add list.\n")

    def refresh_assets(self, create=False):
        root = self._pdir()
        if self.assets is None:
            if not create:
                return
            self.assets = AssetBrowser(root, fill=True, parent=self.assets_page)
            self.assets.activated.connect(self._asset_activated)
        elif self.assets.root != os.path.abspath(root):
            self.assets_page.clear()
            self.assets = AssetBrowser(root, fill=True, parent=self.assets_page)
            self.assets.activated.connect(self._asset_activated)
        else:
            self.assets.refresh()

    def _asset_activated(self, a):
        p = a.path
        low = p.lower()
        if low.endswith(MODEL_EXT):
            self.import_model(p)
        elif low.endswith(".py"):
            self.scripts.open(p)
        elif low.endswith(".heaven3d"):
            self.open_project(p)
        else:
            self.reveal(p)

    def reveal(self, path):
        """Open a file or folder with the system (Explorer / Finder / file manager)."""
        try:
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(path)                          # noqa: S606 (Windows only)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as ex:
            self.toast("Can't open %s: %s" % (path, ex), error=True)

    # =================================================================== help
    def show_shortcuts(self):
        MessageBox.ask(self.ui, "\n".join([
            "Viewport:  right-drag look   WASD/QE fly (while right-dragging)   wheel zoom",
            "           click select   W move   E rotate   R scale   F focus   Esc deselect",
            "Objects:   Ctrl+D duplicate   Delete delete   F2 rename   Ctrl+Z / Ctrl+Y undo / redo",
            "Project:   Ctrl+S save   Ctrl+O open   Ctrl+I import   Ctrl+E export",
            "Play:      Ctrl+P or F5 play / stop   Esc frees the mouse   Ctrl+R reload scripts",
            "Script:    Ctrl+S save + hot reload   Ctrl+/ comment   Tab / Shift+Tab indent   Ctrl+D dup line",
            "Graph:     right-click / Shift+A add node   drag pins to connect   Delete remove   F frame",
            "Fields:    drag to scrub   double-click to type   Ctrl+wheel step   Delete over a field: reset",
        ]), "Keyboard shortcuts", icon="keyboard")

    def classic_ui_info(self):
        MessageBox.ask(self.ui, "Start the engine with  --classic-ui  (or set HEAVEN3D_CLASSIC_UI=1)\n"
                       "to get the previous editor interface back.", "Classic UI", icon="info")

    def about(self):
        MessageBox.ask(self.ui, "Heaven3D %s\nEditor UI: Vitrail %s (GPU-drawn, one draw call)\n"
                       "Python %s  -  pygame %s" % (self.h3d.HEAVEN_VERSION, vt.__version__,
                                                    sys.version.split()[0], pygame.version.ver),
                       "About Heaven3D", icon="info")


# ===================================================================== helpers
try:
    import moderngl as _mgl
    _GL_BLEND = _mgl.BLEND
    _GL_SRC_ALPHA = _mgl.SRC_ALPHA
    _GL_ONE_MINUS_SRC_ALPHA = _mgl.ONE_MINUS_SRC_ALPHA
except Exception:                                  # pragma: no cover
    _GL_BLEND, _GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA = 1, 0x0302, 0x0303


def _tf(n):
    return (tuple(n.position), tuple(n.rotation_euler), tuple(n.scale))


def _set_tf(n, tf):
    import glm
    n.position, n.rotation_euler, n.scale = (glm.vec3(*tf[0]), glm.vec3(*tf[1]), glm.vec3(*tf[2]))
