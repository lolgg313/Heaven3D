# SPDX-License-Identifier: MIT
"""Coding inside the editor.

* ScriptsTab -- edit the project's scripts/*.py, create new blueprints from
  templates, save with Ctrl+S: the script is re-imported at once and every
  object using one of its blueprints switches to the new code (hot reload,
  even during Play).  Syntax errors are marked on their line.
* Console -- the engine log plus a Python prompt with `engine`, `scene`,
  `selected`, `h3d` (the whole engine module) and `editor` ready to use.
* ApiTab -- a searchable reference of everything scripts can call, built
  from the engine itself (so it is never out of date), with "insert".
"""
import contextlib
import importlib
import inspect
import io
import os
import re
import sys
import traceback

import pygame

from vitrail import Button, Dropdown, InputDialog, Label, MessageBox, Row, TextField, TreeItem, TreeView

from .widgets import CodeEditor, LogView

BLUEPRINT_TEMPLATE = '''"""{name}: describe what this script does.

Attach it to an object in the Inspector (Add -> Blueprint -> {name}).
Every property below shows up in the Inspector and is saved with the scene.
"""
from Heaven3D import Blueprint, blueprint, Log, glm


@blueprint
class {name}(Blueprint):
    """{doc}"""

    def __init__(self):
        super().__init__()
        self.properties = {{
            "speed": 2.0,          # numbers, booleans and text are editable in the Inspector
            "enabled": True,
        }}

    def on_begin_play(self):
        # Runs once when Play starts.  self.node is the object, self.engine the engine.
        self.start = glm.vec3(self.node.position)

    def on_tick(self, dt):
        # Runs every frame while playing.  dt = seconds since the last frame.
        if not self.properties["enabled"]:
            return
        self.node.rotation_euler.y += 45.0 * self.properties["speed"] * dt

    def on_collision_enter(self, other, contact):
        pass

    def on_trigger_enter(self, other):
        pass

    def on_message(self, event, data):
        # engine.send_message("door_open") reaches every blueprint here.
        pass
'''

TEMPLATES = {
    "Blueprint (empty)": BLUEPRINT_TEMPLATE,
    "Spinner": BLUEPRINT_TEMPLATE,
    "Mover (ping-pong)": '''"""{name}: moves an object back and forth."""
import math
from Heaven3D import Blueprint, blueprint, glm


@blueprint
class {name}(Blueprint):
    """{doc}"""

    def __init__(self):
        super().__init__()
        self.properties = {{"distance": 3.0, "speed": 1.0, "axis_x": 1.0, "axis_y": 0.0, "axis_z": 0.0}}

    def on_begin_play(self):
        self.start = glm.vec3(self.node.position)
        self.t = 0.0

    def on_tick(self, dt):
        p = self.properties
        self.t += dt * p["speed"]
        axis = glm.vec3(p["axis_x"], p["axis_y"], p["axis_z"])
        if glm.length(axis) > 0:
            axis = glm.normalize(axis)
        self.node.position = self.start + axis * math.sin(self.t) * p["distance"]
''',
    "Collectible (pickup)": '''"""{name}: disappears when the player touches it and sends a message."""
from Heaven3D import Blueprint, blueprint, Log


@blueprint
class {name}(Blueprint):
    """{doc}"""

    def __init__(self):
        super().__init__()
        self.properties = {{"message": "collected", "points": 1, "spin": 90.0}}

    def on_begin_play(self):
        # make sure we have a trigger collider
        if self.node.collider is None:
            self.engine.add_sphere_collider(self.node, radius=0.6, is_trigger=True)

    def on_tick(self, dt):
        self.node.rotation_euler.y += self.properties["spin"] * dt

    def on_trigger_enter(self, other):
        Log.info("%s collected" % self.node.name)
        self.engine.send_message(self.properties["message"], self.properties["points"])
        self.engine.destroy_node(self.node)
''',
    "Door (opens on message)": '''"""{name}: slides open when it receives a message."""
from Heaven3D import Blueprint, blueprint, glm


@blueprint
class {name}(Blueprint):
    """{doc}"""

    def __init__(self):
        super().__init__()
        self.properties = {{"open_on": "door_open", "lift": 3.0, "speed": 2.0}}

    def on_begin_play(self):
        self.closed = glm.vec3(self.node.position)
        self.target = glm.vec3(self.closed)

    def on_message(self, event, data):
        if event == self.properties["open_on"]:
            self.target = self.closed + glm.vec3(0, self.properties["lift"], 0)

    def on_tick(self, dt):
        p = self.node.position
        self.node.position = p + (self.target - p) * min(1.0, dt * self.properties["speed"])
''',
}


def project_dir(engine):
    p = getattr(engine, "project_path", None)
    return os.path.dirname(os.path.abspath(p)) if p else os.getcwd()


def scripts_dir(engine):
    return os.path.join(project_dir(engine), "scripts")


def check_syntax(source, filename="<script>"):
    """-> None, or (line, message)."""
    try:
        compile(source, filename, "exec")
        return None
    except SyntaxError as ex:
        return (ex.lineno or 1, "%s: %s" % (type(ex).__name__, ex.msg))


def hot_reload(h3d, engine, path):
    """(Re)import a project script and move live blueprints to the new classes.
    -> (ok, message, error line or None)"""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    err = check_syntax(src, path)
    if err:
        return False, "line %d: %s" % err, err[0]
    sdir = os.path.dirname(os.path.abspath(path))
    pdir = os.path.dirname(sdir)
    for d in (pdir, sdir):
        if d not in sys.path:
            sys.path.insert(0, d)
    if "Heaven3D" not in sys.modules:
        sys.modules["Heaven3D"] = h3d
    name = os.path.splitext(os.path.basename(path))[0]
    reg = h3d.BlueprintRegistry._registry
    before = dict(reg)
    try:
        mod = sys.modules.get(name)
        if mod is not None and os.path.abspath(getattr(mod, "__file__", "") or "") == os.path.abspath(path):
            mod = importlib.reload(mod)
        else:
            sys.modules.pop(name, None)
            mod = importlib.import_module(name)
    except Exception as ex:
        tb = traceback.extract_tb(ex.__traceback__)
        line = None
        for fr in tb:
            if os.path.abspath(fr.filename) == os.path.abspath(path):
                line = fr.lineno
        return False, "%s: %s" % (type(ex).__name__, ex), line
    swapped = 0
    changed = {k: v for k, v in reg.items() if before.get(k) is not v}
    if changed:
        seen = set()
        for n in engine.scene.root.traverse():
            for bp in n.blueprints:
                new = changed.get(type(bp).__name__)
                if new is not None and type(bp) is not new and id(bp) not in seen:
                    seen.add(id(bp))
                    try:
                        bp.__class__ = new
                        # new properties a script added get their defaults
                        fresh = new().properties
                        for k, v in fresh.items():
                            bp.properties.setdefault(k, v)
                        swapped += 1
                    except Exception:
                        pass
    names = ", ".join(sorted(changed)) or "no blueprints"
    msg = "reloaded %s (%s)" % (os.path.basename(path), names)
    if swapped:
        msg += " - %d live object%s updated" % (swapped, "" if swapped == 1 else "s")
    return True, msg, None


# =========================================================================== scripts tab
class ScriptsTab:
    def __init__(self, editor, page):
        self.ed = editor
        self.engine = editor.engine
        self.path = None
        self.page = page
        bar = Row(spacing=3, parent=page)
        self.files = Dropdown(["(no scripts yet)"], label="", parent=bar, width=260, expand=0,
                              on_change=lambda v: self.open(v) if v and os.path.isfile(v) else None)
        Button("New", self.new_script_dialog, icon="file-plus", tooltip="New blueprint script", expand=0, parent=bar)
        Button("Save", self.save, icon="save", tooltip="Save and hot-reload (Ctrl+S)", expand=0, parent=bar)
        Button("Reload all", self.reload_all, icon="refresh", tooltip="Re-import every project script",
               expand=0, parent=bar)
        Button("Attach", self.attach_selected, icon="link", tooltip="Attach this script's blueprint to the selection",
               expand=0, parent=bar)
        Button("Open folder", lambda: self.ed.reveal(scripts_dir(self.engine)), icon="folder-open", expand=0,
               parent=bar)
        self.status = Label("", dim=True, parent=bar)
        self.code = CodeEditor("", fill=True, parent=page)
        self.code.saved.connect(self.save)
        self.code.changed.connect(self._dirty)
        self.dirty = False
        self.refresh_files()

    def _dirty(self):
        if not self.dirty:
            self.dirty = True
            self._status()

    def _status(self, msg=None, error=False):
        name = os.path.basename(self.path) if self.path else "no file"
        self.status.text = (msg or ("%s%s" % (name, "  (modified)" if self.dirty else "")))
        self.status.color = (1.0, 0.45, 0.45, 1.0) if error else None
        self.status.invalidate()

    def list_scripts(self):
        d = scripts_dir(self.engine)
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".py") and not f.startswith("_")]

    def refresh_files(self):
        files = self.list_scripts()
        if self.path and self.path not in files:
            files.append(self.path)
        opts = [(f, os.path.basename(f)) for f in files] or [("", "(no scripts yet - press New)")]
        self.files.options = opts
        self.files.set_value(self.path or opts[0][0], emit=False)
        self.files.invalidate(layout=True)

    def open(self, path, find=None):
        if not path or not os.path.isfile(path):
            return
        if self.dirty and self.path and self.path != path:
            self.save(reload=False)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as ex:
            self.ed.log("Can't open %s: %s" % (path, ex), "ERROR")
            return
        self.path = os.path.abspath(path)
        self.code.set_text(text)
        self.dirty = False
        self.refresh_files()
        self._status()
        if find:
            for i, ln in enumerate(self.code.lines):
                if ln.lstrip().startswith(find):
                    self.code.goto_line(i + 1)
                    break
        self.ed.open_tab("Script")

    def save(self, reload=True):
        if self.path is None:
            self.new_script_dialog(text=self.code.text)
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.code.text.rstrip("\n") + "\n")
        except OSError as ex:
            self._status("save failed: %s" % ex, True)
            return
        self.dirty = False
        if not reload:
            self._status()
            return
        ok, msg, line = hot_reload(self.ed.h3d, self.engine, self.path)
        self.code.error_line = line
        self.code.invalidate()
        self._status(("saved, " + msg) if ok else msg, not ok)
        self.ed.log(("[Script] saved, " + msg) if ok else "[Script] %s: %s" % (os.path.basename(self.path), msg),
                    "INFO" if ok else "ERROR")
        self.ed.inspector.show(self.ed.inspector.node, force=True)

    def reload_all(self):
        n_ok = 0
        for p in self.list_scripts():
            ok, msg, _l = hot_reload(self.ed.h3d, self.engine, p)
            n_ok += ok
            self.ed.log("[Script] " + msg, "INFO" if ok else "ERROR")
        self.ed.toast("%d script%s reloaded" % (n_ok, "" if n_ok == 1 else "s"))
        self.ed.inspector.show(self.ed.inspector.node, force=True)

    def classes_in_file(self):
        if not self.path:
            return []
        return re.findall(r"^class\s+(\w+)\s*\(", self.code.text, re.M)

    def attach_selected(self):
        n = self.engine.selected
        if n is None:
            self.ed.toast("Select an object first")
            return
        if self.dirty:
            self.save()
        for name in self.classes_in_file():
            if self.ed.h3d.BlueprintRegistry.get(name) is not None:
                self.ed.add_blueprint(n, name)
                return
        self.ed.toast("No @blueprint class in this file yet (save it first)")

    def new_script_dialog(self, attach_to=None, text=None):
        ui = self.ed.ui

        def made(name):
            if not name:
                return
            words = re.findall(r"[A-Za-z0-9_]+", name)
            cls = "".join(w[:1].upper() + w[1:] for w in words) or "MyScript"
            if cls[0].isdigit():
                cls = "S" + cls
            d = scripts_dir(self.engine)
            os.makedirs(d, exist_ok=True)
            fn = os.path.join(d, re.sub(r"(?<!^)(?=[A-Z])", "_", cls).lower() + ".py")
            if os.path.exists(fn):
                MessageBox.ask(ui, "%s already exists - opening it." % os.path.basename(fn), title="New script")
            else:
                tpl = TEMPLATES.get(tmpl.value, BLUEPRINT_TEMPLATE)
                body = text if text is not None else tpl.format(name=cls, doc="What %s does." % cls)
                with open(fn, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(body)
            self.path = None
            self.open(fn, find="class " + cls)
            ok, msg, line = hot_reload(self.ed.h3d, self.engine, fn)
            self.code.error_line = line
            self.ed.log("[Script] created %s, %s" % (os.path.basename(fn), msg), "INFO" if ok else "ERROR")
            if ok and attach_to is not None and self.ed.h3d.BlueprintRegistry.get(cls) is not None:
                self.ed.add_blueprint(attach_to, cls)
        dlg = InputDialog("New script", "Blueprint name", "MyScript", on_result=made)
        tmpl = Dropdown(list(TEMPLATES), "Blueprint (empty)", label="Template", parent=dlg)
        dlg.children.remove(tmpl)
        dlg.children.insert(2, tmpl)
        dlg.open(ui)


# =========================================================================== console
class PromptField(TextField):
    """The console's input line: Enter runs, Up/Down walk the history."""

    def __init__(self, console, **kw):
        super().__init__("", placeholder=">>> Python: engine, scene, selected, h3d, editor  (Up/Down: history)", **kw)
        self.console = console
        self.submit_on_enter = False

    def on_key(self, ui, e):
        if self.editor is not None and e.key in (pygame.K_UP, pygame.K_DOWN):
            t = self.console.history_step(-1 if e.key == pygame.K_UP else 1)
            if t is not None:
                self.editor.text = t
                self.editor.cursor = self.editor.anchor = len(t)
                self.invalidate()
            return True
        if self.editor is not None and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            text = self.editor.text
            self.editor.text = ""
            self.editor.cursor = self.editor.anchor = 0
            self.invalidate()
            self.console.run(text)
            return True
        return super().on_key(ui, e)


class Console:
    def __init__(self, editor, page):
        self.ed = editor
        bar = Row(spacing=3, parent=page)
        self.filter = TextField("", placeholder="Filter log", parent=bar, width=220, expand=0)
        self.filter.edited.connect(self._filter)
        Button("Clear", lambda: self.log.clear(), icon="trash", expand=0, parent=bar)
        Button("Copy", self._copy, icon="copy", expand=0, parent=bar,
               tooltip="Copy the selected lines (all of them when none are selected)")
        self.errors = Label("", dim=True, parent=bar)
        self.log = LogView(fill=True, parent=page)
        self.prompt = PromptField(self, parent=page)
        self.history = []
        self._hi = 0
        self.ns = None
        self.n_err = 0

    def _filter(self, t):
        self.log.filter = t or ""
        self.log.sel = None
        self.log.invalidate()

    def _copy(self):
        t = self.log.copy(self.ed.ui)
        n = t.count("\n") + 1 if t else 0
        self.ed.toast("Copied %d line%s to the clipboard" % (n, "" if n == 1 else "s"))

    def add(self, line, level=None):
        if level is None:
            level = "INFO"
            for lv in ("ERROR", "WARN", "DEBUG"):
                if "[%s]" % lv in line[:40]:
                    level = lv
                    break
            line = line.replace("[Heaven3D]", "", 1)
        if level == "ERROR":
            self.n_err += 1
            self.errors.text = "%d error%s" % (self.n_err, "" if self.n_err == 1 else "s")
            self.errors.color = (1.0, 0.45, 0.45, 1.0)
            self.errors.invalidate()
        self.log.add(line, level)

    def namespace(self):
        if self.ns is None:
            h3d, e = self.ed.h3d, self.ed.engine
            self.ns = {"h3d": h3d, "engine": e, "scene": e.scene, "editor": self.ed, "glm": h3d.glm,
                       "Log": h3d.Log, "math": __import__("math")}
            for k in ("Blueprint", "blueprint", "SceneNode", "vs_node", "anim_apply", "anim_read"):
                if hasattr(h3d, k):
                    self.ns[k] = getattr(h3d, k)
        self.ns["selected"] = self.ed.engine.selected
        return self.ns

    def history_step(self, d):
        if not self.history:
            return None
        self._hi = max(0, min(len(self.history), self._hi + d))
        return self.history[self._hi] if self._hi < len(self.history) else ""

    def run(self, src):
        src = (src or "").strip()
        if not src:
            return
        self.history.append(src)
        self._hi = len(self.history)
        self.log.add(">>> " + src, "IN")
        ns = self.namespace()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                try:
                    code = compile(src, "<console>", "eval")
                    res = eval(code, ns)
                    if res is not None:
                        print(repr(res))
                        ns["_"] = res
                except SyntaxError:
                    exec(compile(src, "<console>", "exec"), ns)
        except Exception as ex:
            self.log.add("%s: %s" % (type(ex).__name__, ex), "ERROR")
        text = out.getvalue().rstrip("\n")
        if text:
            self.log.add(text, "OUT")


# =========================================================================== API reference
def _sig(fn):
    try:
        return str(inspect.signature(fn))
    except (TypeError, ValueError):
        return "(...)"


def _doc(obj):
    d = inspect.getdoc(obj) or ""
    return d.strip()


def api_entries(h3d):
    """[(section, name, signature, doc, snippet)]"""
    out = []
    E = h3d.HeavenEngine
    for name, fn in sorted(vars(E).items()):
        if name.startswith("_") or not callable(fn):
            continue
        out.append(("Engine (self.engine)", name, _sig(fn).replace("(self, ", "(").replace("(self)", "()"),
                    _doc(fn), "self.engine.%s%s" % (name, _sig(fn).replace("(self, ", "(").replace("(self)", "()"))))
    B = h3d.Blueprint
    for name in ("on_begin_play", "on_tick", "on_end_play", "on_message", "on_collision_enter",
                 "on_collision_stay", "on_collision_exit", "on_trigger_enter", "on_trigger_exit", "log"):
        fn = getattr(B, name, None)
        if fn is not None:
            s = _sig(fn)
            out.append(("Blueprint hooks", name, s, _doc(fn) or "Override this in your blueprint.",
                        "    def %s%s:\n        pass" % (name, s)))
    N = h3d.SceneNode
    try:
        sample = N("sample")
        for k, v in sorted(vars(sample).items()):
            if not k.startswith("_"):
                out.append(("Scene object (self.node)", k, type(v).__name__, "", "self.node.%s" % k))
    except Exception:
        pass
    for name, fn in sorted(vars(N).items()):
        if not name.startswith("_") and callable(fn):
            s = _sig(fn).replace("(self, ", "(").replace("(self)", "()")
            out.append(("Scene object (self.node)", name, s, _doc(fn), "self.node.%s%s" % (name, s)))
    for path, spec in sorted(getattr(h3d, "VS_NODES", {}).items()):
        ins = ", ".join("%s:%s" % (s[0], s[1]) for s in spec["inputs"] if s[1] != "exec")
        outs = ", ".join("%s:%s" % (s[0], s[1]) for s in spec["outputs"] if s[1] != "exec")
        out.append(("Visual script nodes", path, "(%s) -> %s" % (ins, outs or "-"), spec.get("doc", ""), ""))
    for name in h3d.BlueprintRegistry.all_names():
        cls = h3d.BlueprintRegistry.get(name)
        out.append(("Blueprints you can attach", name, "", _doc(cls),
                    'self.engine.attach_blueprint(self.node, h3d.BlueprintRegistry.get("%s"))' % name))
    out.append(("Animation", "anim_apply(node, path, value)", "", _doc(getattr(h3d, "anim_apply", None)) or "",
                'anim_apply(self.node, "position.y", 2.0)'))
    out.append(("Animation", "Animator.play(name)", "", "Play a clip made in the Animation tab.",
                'self.engine.first_blueprint("Animator").play("Open")'))
    return out


class ApiTab:
    def __init__(self, editor, page):
        self.ed = editor
        top = Row(spacing=3, parent=page)
        self.search = TextField("", placeholder="Search the API (e.g. raycast, collider, message, spawn)", parent=top)
        self.search.edited.connect(lambda _t: self.fill())
        Button("Insert into script", self.insert, icon="code", expand=0, parent=top)
        body = Row(spacing=4, fill=True, parent=page)
        self.tree = TreeView(multi_select=False, width=340, expand=0, parent=body)
        self.tree.selection_changed.connect(self._picked)
        self.tree.activated.connect(lambda _it: self.insert())
        self.view = CodeEditor("# Pick an entry on the left to see what it does.\n"
                               "# Double-click (or Insert into script) puts an example in your script.\n",
                               read_only=True, parent=body)
        self.entries = None
        self.current = None

    def ensure(self):
        if self.entries is None:
            self.entries = api_entries(self.ed.h3d)
            self.fill()

    def fill(self):
        if self.entries is None:
            return
        q = (self.search.value or "").lower().strip()
        secs = {}
        for e in self.entries:
            sec, name, sig, doc, snip = e
            if q and q not in name.lower() and q not in doc.lower() and q not in sec.lower():
                continue
            secs.setdefault(sec, []).append(TreeItem(name, icon="code" if sig.startswith("(") else "tag", data=e))
        items = [TreeItem("%s (%d)" % (s, len(v)), v, icon="folder", expanded=bool(q) or len(secs) == 1)
                 for s, v in secs.items()]
        self.tree.set_items(items)

    def _picked(self, sel):
        if not sel or sel[0].data is None:
            return
        sec, name, sig, doc, snip = sel[0].data
        self.current = sel[0].data
        text = "# %s\n%s%s\n\n%s\n" % (sec, name, sig if sig.startswith("(") else (": " + sig if sig else ""),
                                       doc or "(no description)")
        if snip:
            text += "\n# example\n%s\n" % snip
        self.view.set_text(text)

    def insert(self):
        if not self.current or not self.current[4]:
            return
        sc = self.ed.scripts
        if sc.path is None:
            self.ed.toast("Open or create a script first (Script tab)")
            return
        sc.code.insert(self.current[4])
        self.ed.open_tab("Script")
