# SPDX-License-Identifier: MIT
"""Prefabs in the editor.

  * Save as Prefab (Edit menu, Outliner right-click, Inspector) writes the
    selection to assets/prefabs/<Name>.prefab; the object becomes an instance
  * double-click a .prefab in Assets, or drag it into the viewport, to place one
  * the Inspector's Prefab panel shows the link and whether the instance was
    edited, with Apply (instance -> prefab and the other instances), Revert
    and Unlink
"""
import os

from vitrail import Button, FileDialog, Label, MenuItem, MessageBox, Row

from .scripting import project_dir

PREFAB_FILTERS = [("Heaven3D prefab", "*.prefab"), ("All files", "*")]
PREFAB_COLOR = (0.36, 0.66, 1.0, 1)


class PrefabTools:
    def __init__(self, editor):
        self.ed = editor
        self.engine = editor.engine
        self.h3d = editor.h3d

    # -- queries -----------------------------------------------------------------------
    def link(self, n):
        return self.h3d.prefab_link(n) if n is not None else None

    def root_of(self, n):
        return self.h3d.prefab_root(n) if n is not None else None

    def modified(self, n):
        try:
            return bool(self.engine.prefab_is_modified(n))
        except Exception:
            return False

    def can_save(self, n):
        e = self.engine
        return (n is not None and n is not e.scene.root and not (n.is_sun or n.is_clouds or n.is_ocean)
                and getattr(n, "spawn_kind", None) != "player")

    # -- actions -------------------------------------------------------------------------
    def save_dialog(self, n=None):
        ed, e = self.ed, self.engine
        n = n if n is not None else e.selected
        if not self.can_save(n):
            ed.toast("Select an object to save as a prefab", error=True)
            return
        folder = os.path.join(project_dir(e), "assets", "prefabs")
        os.makedirs(folder, exist_ok=True)
        FileDialog.save_file(ed.ui, lambda p: self.save(n, p) if p else None, filters=PREFAB_FILTERS,
                             start_dir=folder, default_name=_safe(n.name) + ".prefab", title="Save as Prefab")

    def save(self, n, path):
        ed = self.ed
        old = self.link(n)
        old = dict(old) if old else None
        p = ed.run(lambda: self.engine.save_prefab(n, path))
        if not p:
            return None
        new = dict(self.link(n) or {})
        ed.ui.undo.push("Save as Prefab", lambda: setattr(n, "prefab", old), lambda: setattr(n, "prefab", new))
        ed.toast("Saved prefab " + os.path.basename(p))
        ed.refresh_assets()
        ed.inspector.show(n, force=True)
        return p

    def place(self, path, at=None):
        ed = self.ed
        n = ed.run(lambda: self.engine.spawn_prefab(path, position=at))
        if n is None:
            ed.toast("Couldn't place " + os.path.basename(path), error=True)
            return None
        if at is None:
            try:   # 8 m in front of the camera, at ground height
                cam = self.engine.camera
                p = cam.position + cam.forward() * 8.0
                n.position = type(n.position)(p.x, 0.0, p.z)
            except Exception:
                pass
        ed.select(n)
        ed._push_created(n, "Place " + n.name)
        ed.toast("Placed " + os.path.basename(path))
        return n

    def apply(self, n):
        ed = self.ed
        root = self.root_of(n)
        if root is None:
            return
        name = os.path.basename(self.link(root)["path"])
        others = len(self.engine.prefab_instances(self.link(root)["path"])) - 1

        def go(answer="Apply"):
            if answer != "Apply":
                return
            done = ed.run(lambda: self.engine.prefab_apply(root))
            if done is None:
                return
            ed.toast("Applied to %s%s" % (name, " (+%d instance%s updated)" % (done, "s" if done != 1 else "")
                                           if done else ""))
            ed.refresh_assets()
            ed.inspector.show(self.engine.selected, force=True)
        if others > 0:
            MessageBox.ask(ed.ui, "Write '%s' into %s?\n%d other instance(s) in this scene follow it unless "
                           "they were edited themselves.\nThis can't be undone." % (root.name, name, others),
                           go, title="Apply to prefab", buttons=("Apply", "Cancel"), cancel="Cancel",
                           icon="prefab")
        else:
            go()

    def revert(self, n):
        ed = self.ed
        root = self.root_of(n)
        if root is None:
            return
        parent = root.parent
        data = ed._snapshot(root)
        box = {"old": None}

        def do():
            new = self.engine.prefab_revert(box["old"] or root)
            ed.select(new)
            return new
        new = ed.run(do)
        if new is None:
            return
        box["new"] = new

        def undo():
            nn = box.get("new")
            if nn is not None and nn.parent is not None:
                idx = nn.parent.children.index(nn)
                self.engine._delete_node(nn)
                o = ed._restore_node(data, parent)
                if o is not None and idx < len(parent.children) - 1:
                    parent.children.remove(o)
                    parent.children.insert(idx, o)
                box["old"], box["new"] = o, None
                ed.select(o)

        def redo():
            o = box.get("old")
            if o is not None and o.parent is not None:
                box["new"] = self.engine.prefab_revert(o)
                box["old"] = None
                ed.select(box["new"])
        ed.ui.undo.push("Revert " + root.name, undo, redo)
        ed.toast("Reverted " + root.name)

    def unlink(self, n):
        ed = self.ed
        root = self.root_of(n)
        if root is None:
            return
        old = dict(self.link(root))
        self.engine.prefab_unlink(root)
        ed.ui.undo.push("Unlink prefab", lambda: setattr(root, "prefab", old), lambda: setattr(root, "prefab", None))
        ed.inspector.show(ed.engine.selected, force=True)
        ed.outliner.rebuild()
        ed.toast(root.name + " is no longer linked to its prefab")

    def show_in_assets(self, n):
        root = self.root_of(n)
        if root is None:
            return
        ed = self.ed
        ed.open_tab("Assets")
        ed.refresh_assets(create=True)
        try:
            ed.assets.navigate(os.path.dirname(self.link(root)["path"]))
            ed.assets.select_path(self.link(root)["path"])
        except Exception:
            ed.reveal(os.path.dirname(self.link(root)["path"]))

    def tick(self, now):
        """Keep the Inspector's 'edited / same' line current (twice a second)."""
        st = getattr(self, "_state", None)
        if st is None or now < getattr(self, "_next", 0.0):
            return
        self._next = now + 0.5
        label, root, missing = st
        if label.parent is None or root.parent is None:
            self._state = None
            return
        if missing:
            txt = "The file is missing; the project keeps its own copy."
        else:
            txt = "Edited here - Apply or Revert" if self.modified(root) else "Same as the prefab"
        if label.text != txt:
            label.text = txt

    # -- UI pieces -----------------------------------------------------------------------
    def menu_items(self, n):
        root = self.root_of(n)
        items = [MenuItem("Save as Prefab...", lambda: self.save_dialog(n), icon="prefab",
                          enabled=self.can_save(n))]
        if root is not None:
            items.append(MenuItem("Prefab", icon="prefab", submenu=[
                MenuItem("Apply to prefab", lambda: self.apply(root), icon="upload"),
                MenuItem("Revert to prefab", lambda: self.revert(root), icon="undo"),
                MenuItem("Unlink", lambda: self.unlink(root), icon="close"),
                None,
                MenuItem("Show in Assets", lambda: self.show_in_assets(root), icon="folder")]))
        return items

    def inspector_panel(self, insp, n):
        root = self.root_of(n)
        if root is None:
            return
        link = self.link(root)
        p = insp.panel("Prefab", icon="prefab")
        name = os.path.basename(link.get("path") or "?")
        missing = not os.path.isfile(link.get("path") or "")
        Label(name + ("  (file missing)" if missing else ""), icon="prefab", parent=p)
        if root is not n:
            Label("Part of the instance '%s'" % root.name, dim=True, parent=p)
        self._state = (Label("", dim=True, parent=p), root, missing)
        self._next = 0.0
        self.tick(0.0)
        r = Row(spacing=3, parent=p)
        Button("Apply", lambda: self.apply(root), icon="upload", parent=r, enabled=not missing,
               tooltip="Write this object into the prefab; other unedited instances follow")
        Button("Revert", lambda: self.revert(root), icon="undo", parent=r, enabled=not missing,
               tooltip="Throw away this instance's edits (keeps its name and place)")
        Button("Unlink", lambda: self.unlink(root), icon="close", parent=r,
               tooltip="Keep the object, forget the prefab")


def _safe(name):
    out = "".join(c if (c.isalnum() or c in " _-") else "_" for c in (name or "Prefab")).strip()
    return out or "Prefab"
