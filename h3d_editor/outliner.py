# SPDX-License-Identifier: MIT
"""The Outliner: the scene tree.  Click to select, double-click to focus,
drag to re-parent, right-click for actions, type in the box to filter."""
from vitrail import Column, MenuItem, Row, TextField, TreeItem, TreeView, open_context_menu

from .inspector import ICON_OF, node_kind

KIND_COLORS = {"sun": (1.0, 0.82, 0.35, 1), "point": (1.0, 0.9, 0.55, 1), "spot": (1.0, 0.9, 0.55, 1),
               "player": (0.45, 0.85, 0.55, 1), "enemy": (0.95, 0.45, 0.4, 1), "clouds": (0.8, 0.85, 0.95, 1),
               "ocean": (0.4, 0.7, 0.95, 1), "terrain": (0.55, 0.8, 0.45, 1)}


def editor_only(n):
    return (n.name.startswith("__") or getattr(n, "_heaven3d_imported_part", False)
            or getattr(n, "_h3d_editor_only", False))


class Outliner:
    def __init__(self, editor):
        self.ed = editor
        self.engine = editor.engine
        self.box = Column(spacing=4, fill=True)
        top = Row(spacing=3, parent=self.box)
        self.search = TextField("", placeholder="Search objects", parent=top)
        self.search.edited.connect(lambda _t: self.rebuild())
        self.search.changed.connect(lambda _t: self.rebuild())
        self.tree = TreeView(multi_select=False, drag_drop=True, fill=True, parent=self.box)
        self.tree.selection_changed.connect(self._picked)
        self.tree.activated.connect(lambda it: (self.ed.select(it.data), self.ed.focus_selected()))
        self.tree.moved.connect(self._moved)
        self.tree.context.connect(self._context)
        self._sig = None
        self._items = {}
        self._quiet = False

    # -- model -----------------------------------------------------------------------
    def signature(self):
        out = []
        root = self.engine.scene.root

        def walk(n, depth):
            for c in n.children:
                if editor_only(c):
                    continue
                out.append((id(c), c.name, depth, c.visible))
                if depth < 6:
                    walk(c, depth + 1)
        walk(root, 0)
        return tuple(out)

    def rebuild(self, sig=None):
        expanded = {k for k, it in self._items.items() if it.expanded}
        f = (self.search.value or "").strip().lower()
        self._items = {}

        def make(n, depth):
            kids = [c for c in n.children if not editor_only(c)]
            kind = node_kind(n)
            it = TreeItem(n.name if n.visible else n.name + "  (hidden)", icon=ICON_OF.get(kind, "cube"),
                          icon_color=KIND_COLORS.get(kind), data=n, expanded=id(n) in expanded)
            self._items[id(n)] = it
            if depth < 6:
                for c in kids:
                    ch = make(c, depth + 1)
                    if ch is not None:
                        it.add(ch)
            if f and f not in n.name.lower() and not it.children:
                return None
            if f:
                it.expanded = True
            return it
        items = [x for x in (make(c, 0) for c in self.engine.scene.root.children if not editor_only(c))
                 if x is not None]
        self._quiet = True
        self.tree.set_items(items)
        self._quiet = False
        self._sig = sig if sig is not None else self.signature()
        self.show_selection(self.engine.selected)

    def sync(self):
        sig = self.signature()
        if sig != self._sig:
            self.rebuild(sig)

    def show_selection(self, node):
        it = self._items.get(id(node)) if node is not None else None
        self._quiet = True
        self.tree.select([it] if it is not None else [], emit=False)
        if it is not None:
            p = it.parent
            while p is not None and p is not self.tree.root:
                p.expanded = True
                p = p.parent
            self.tree.refresh()
            self.tree.reveal(it)
        self._quiet = False

    # -- actions ------------------------------------------------------------------------------
    def _picked(self, sel):
        if self._quiet:
            return
        if sel:
            self.ed.select(sel[0].data)
        else:
            self.ed.select(None)

    def _moved(self, item, new_parent, index):
        n = item.data
        parent = new_parent.data if (new_parent is not None and new_parent is not self.tree.root) else \
            self.engine.scene.root
        if parent is None:
            parent = self.engine.scene.root
        self.ed.reparent(n, parent, index)

    def _context(self, item, x, y):
        ed = self.ed
        if item is not None:
            ed.select(item.data)
            n = item.data
            items = [MenuItem("Rename", lambda: ed.rename_dialog(n), icon="edit", shortcut="F2"),
                     MenuItem("Duplicate", ed.duplicate, icon="copy", shortcut="Ctrl+D"),
                     MenuItem("Focus", ed.focus_selected, icon="target", shortcut="F"),
                     MenuItem("Hide" if n.visible else "Show", lambda: ed.toggle_visible(n), icon="eye"),
                     MenuItem("Unparent", lambda: ed.reparent(n, self.engine.scene.root, None),
                              enabled=n.parent is not self.engine.scene.root),
                     None,
                     MenuItem("Add Visual Script", lambda: ed.add_blueprint(n, "VisualScript"), icon="node"),
                     MenuItem("Add Animator", lambda: ed.add_blueprint(n, "Animator"), icon="film"),
                     MenuItem("New script for this object...", lambda: ed.scripts.new_script_dialog(attach_to=n),
                              icon="file-plus"),
                     None,
                     MenuItem("Delete", ed.delete, icon="trash", shortcut="Delete")]
        else:
            items = ed.create_menu_items()
        open_context_menu(ed.ui, items, at=(x, y))
