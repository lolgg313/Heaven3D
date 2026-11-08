# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Menus: popup menus with icons, shortcuts, check marks and cascading
submenus; a menu bar; context menus; keyboard shortcuts.

    bar = MenuBar([
        ("File", [
            MenuItem("Open...", open_file, icon="folder-open", shortcut="Ctrl+O"),
            MenuItem("Recent", submenu=["a.txt", "b.txt"]),
            None,                                    # separator
            MenuItem("Quit", app.quit, shortcut="Ctrl+Q"),
        ]),
    ])

Type while a menu is open to search it -- including every submenu -- with
typo tolerance.  Arrow keys / Enter / Esc navigate.
"""
from __future__ import annotations

import pygame

from .core import CTRL, SHIFT, ALT, Popup, Widget
from .fuzzy import fuzzy_filter
from .theme import shade


class MenuItem:
    __slots__ = ("label", "action", "value", "icon", "shortcut", "checked", "submenu", "enabled", "path")

    def __init__(self, label, action=None, *, value=None, icon=None, shortcut=None, checked=None,
                 submenu=None, enabled=True):
        self.label = str(label)
        if action is not None and not callable(action) and value is None:
            value, action = action, None
        self.action = action
        self.value = label if (value is None and action is None and submenu is None) else value
        self.icon = icon
        self.shortcut = shortcut
        self.checked = checked          # None = not checkable; bool or callable -> bool
        self.submenu = submenu
        self.enabled = enabled
        self.path = self.label          # "Parent > Child" for search results

    def is_checked(self):
        c = self.checked
        return bool(c()) if callable(c) else bool(c)

    def is_enabled(self):
        e = self.enabled
        return bool(e()) if callable(e) else bool(e)


def normalize_items(items):
    out = []
    for it in items or ():
        if it is None or isinstance(it, MenuItem):
            out.append(it)
        elif isinstance(it, tuple):
            label = it[0]
            second = it[1] if len(it) > 1 else label
            enabled = it[2] if len(it) > 2 else True
            if callable(second):
                out.append(MenuItem(label, second, enabled=enabled))
            else:
                out.append(MenuItem(label, value=second, enabled=enabled))
        else:
            out.append(MenuItem(str(it), value=it))
    return out


# ----------------------------------------------------------------------------- shortcuts
def parse_shortcut(spec: str):
    """"Ctrl+Shift+S" -> (ctrl, shift, alt, keycode)."""
    parts = [p.strip() for p in spec.replace("-", "+").split("+") if p.strip()]
    ctrl = shift = alt = False
    key = None
    names = {"del": "delete", "esc": "escape", "enter": "return", "ins": "insert", "pgup": "page up",
             "pgdn": "page down", "plus": "+", "minus": "-", "space": "space"}
    for p in parts:
        lp = p.lower()
        if lp in ("ctrl", "control", "cmd", "command", "meta"):
            ctrl = True
        elif lp == "shift":
            shift = True
        elif lp in ("alt", "option"):
            alt = True
        else:
            key = pygame.key.key_code(names.get(lp, lp))
    return (ctrl, shift, alt, key)


def shortcut_matches(parsed, e):
    ctrl, shift, alt, key = parsed
    return (e.key == key and bool(e.mod & CTRL) == ctrl and bool(e.mod & SHIFT) == shift
            and bool(e.mod & ALT) == alt)


# ----------------------------------------------------------------------------- Menu
class Menu(Popup):
    """Popup list of MenuItems (or plain values / (label, value) tuples / None)."""

    def __init__(self, items, on_pick=None, *, title="", current=None, searchable=True, min_width=0,
                 parent_menu=None):
        super().__init__()
        self.items = normalize_items(items)
        self.on_pick = on_pick
        self.title = title
        self.current = current
        self.searchable = searchable
        self.min_width = min_width
        self.parent_menu = parent_menu
        self.child = None
        self._opener = None
        self.owner_bar = None
        self.side = None              # "right" for submenus
        self.query = ""
        self.highlight = None
        self._rows = []
        self._view = self.items
        for i, it in enumerate(self.items):
            if it is not None and current is not None and it.value == current:
                self.highlight = i

    # -- search over the whole tree -------------------------------------------------
    def _leaves(self, items=None, prefix=""):
        for it in (self.items if items is None else items):
            if it is None or not it.is_enabled():
                continue
            if it.submenu:
                yield from self._leaves(normalize_items(it.submenu), prefix + it.label + " › ")
            else:
                it.path = prefix + it.label
                yield it

    def _refilter(self):
        if self.query:
            self._view = fuzzy_filter(self.query, list(self._leaves()), key=lambda it: it.path)
            self.highlight = 0 if self._view else None
        else:
            self._view = self.items
            self.highlight = None

    # -- geometry --------------------------------------------------------------------------
    def _label(self, it):
        return it.path if self.query else it.label

    def preferred_size(self, ui):
        s = ui.theme.scale
        rh = ui.theme.h
        f = ui.font()
        view = self._view if self.query else self.items
        lab = max([f.width(self._label(it)) for it in view if it is not None] + [f.width(self.title), 60 * s])
        sc = max([f.width(it.shortcut) for it in self.items if it is not None and it.shortcut] + [0])
        w = 30 * s + lab + (sc + 24 * s if sc else 0) + 22 * s
        w = max(w, self.min_width, 140 * s)
        h = 8 * s + (rh if self.title else 0) + (rh + 4 * s if self.query else 0)
        for it in self._view:
            h += 7 * s if it is None else rh
        if not self._view:
            h += rh
        return (w, h)

    def place(self, ui):
        if self.side != "right" or self.anchor is None:
            return super().place(ui)
        mw, mh = self.measure(ui)
        sw, sh = ui.size
        ax, ay, aw, ah = self.anchor
        x = ax + aw - 2
        if x + mw > sw:
            x = max(0, ax - mw + 2)
        y = min(max(0, ay - 4 * ui.theme.scale), max(0, sh - mh))
        self.arrange(x, y, mw, mh)

    def arrange(self, x, y, w, h):
        super().arrange(x, y, w, h)
        ui = self.ui
        s = ui.theme.scale if ui else 1.0
        rh = ui.theme.h if ui else 22
        cy = y + 4 * s + (rh if self.title else 0) + (rh + 4 * s if self.query else 0)
        self._rows = []
        for i, it in enumerate(self._view):
            hh = 7 * s if it is None else rh
            self._rows.append((cy, hh, i))
            cy += hh

    def row_rect(self, i):
        for (ry, rh, j) in self._rows:
            if j == i:
                return (self.x, ry, self.w, rh)
        return None

    def index_at(self, px, py):
        if not self.contains(px, py):
            return None
        for (ry, rh, i) in self._rows:
            if ry <= py < ry + rh:
                it = self._view[i]
                return i if (it is not None and it.is_enabled()) else None
        return None

    # -- painting ------------------------------------------------------------------------
    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = t.radius * s
        dl.shadow(self.x, self.y, self.w, self.h, t.shadow, r, 10 * s)
        dl.box(self.x, self.y, self.w, self.h, t.menu, border=t.outline, radius=r)
        rh = t.h
        y = self.y + 4 * s
        if self.title:
            ui.text(dl, self.title, self.x + 10 * s, y, self.w - 20 * s, rh, t.text_dim, bold=True)
            y += rh
        if self.query:
            dl.box(self.x + 4 * s, y, self.w - 8 * s, rh, t.field_edit, border=t.accent, radius=r)
            ui.icon(dl, "search", self.x + 9 * s, y + (rh - 14 * s) / 2, 14, t.text_dim)
            end = ui.text(dl, self.query, self.x + 28 * s, y, self.w - 36 * s, rh, t.text)
            dl.box(round(end + 1), y + 4 * s, max(1, round(s)), rh - 8 * s, t.caret)
        isz = ui.icon_size(14)
        for (ry, hh, i) in self._rows:
            it = self._view[i]
            if it is None:
                dl.box(self.x + 8 * s, round(ry + hh / 2), self.w - 16 * s, 1, t.outline)
                continue
            enabled = it.is_enabled()
            hl = (i == self.highlight or (self.child is not None and self.child._opener is it)) and enabled
            if hl:
                dl.box(self.x + 3 * s, ry, self.w - 6 * s, hh, t.menu_hover, radius=r * 0.75)
            col = t.text_on_accent if hl else (t.text if enabled else t.text_dim)
            dim = col if hl else t.text_dim
            iy = ry + (hh - isz) / 2
            if it.checked is not None:
                if it.is_checked():
                    dl.check(self.x + 9 * s, ry + (hh - 11 * s) / 2, 11 * s, 11 * s, col, 1.7 * s)
            elif it.icon:
                ui.icon(dl, it.icon, self.x + 8 * s, iy, 14, col)
            elif self.current is not None and it.value == self.current:
                dl.check(self.x + 9 * s, ry + (hh - 11 * s) / 2, 11 * s, 11 * s, col, 1.7 * s)
            right = self.x + self.w - 10 * s
            if it.submenu and not self.query:
                a = round(7 * s)
                dl.triangle(right - a, round(ry + (hh - a) / 2), a, a, dim, 0)
                right -= a + 6 * s
            if it.shortcut and not self.query:
                sw_ = ui.text_width(it.shortcut)
                ui.text(dl, it.shortcut, right - sw_, ry, sw_ + 1, hh, dim, elide=False)
                right -= sw_ + 14 * s
            ui.text(dl, self._label(it), self.x + 30 * s, ry, right - self.x - 30 * s, hh, col)
        if not self._view:
            ui.text(dl, "No matches", self.x + 30 * s, self.y + self.h - rh - 4 * s, self.w - 38 * s, rh, t.text_dim)

    # -- interaction ---------------------------------------------------------------------
    def widget_at(self, px, py):
        return self if self.contains(px, py) else None

    def root_menu(self):
        m = self
        while m.parent_menu is not None:
            m = m.parent_menu
        return m

    def _open_child(self, ui, i):
        it = self._view[i]
        if self.child is not None:
            if self.child._opener is it:
                return
            ui.close_popup(self.child)
        rect = self.row_rect(i)
        child = Menu(it.submenu, self.on_pick, parent_menu=self, searchable=self.searchable, current=self.current)
        child._opener = it
        child.side = "right"
        self.child = child
        ui.open_popup(child, anchor=rect)

    def on_close(self, ui):
        if self.parent_menu is not None and self.parent_menu.child is self:
            self.parent_menu.child = None
            self.parent_menu.invalidate()

    def track(self, ui, px, py):
        i = self.index_at(px, py)
        if i is None or i == self.highlight:
            return
        self.highlight = i
        it = self._view[i]
        if it.submenu and not self.query:
            self._open_child(ui, i)
        elif self.child is not None:
            ui.close_popup(self.child)
        self.invalidate()

    def on_mouse_move(self, ui, ev):
        self.track(ui, ev.x, ev.y)

    def on_mouse_down(self, ui, ev):
        return True

    def on_mouse_up(self, ui, ev):
        self.release_at(ui, ev.x, ev.y)

    def release_at(self, ui, px, py):
        i = self.index_at(px, py)
        if i is None:
            return False
        self.pick(ui, i)
        return True

    def pick(self, ui, i):
        it = self._view[i]
        if it is None or not it.is_enabled():
            return
        if it.submenu and not self.query:
            self._open_child(ui, i)
            return
        ui.close_popup(self.root_menu())
        if it.action is not None:
            it.action()
        elif self.on_pick is not None:
            self.on_pick(it.value)

    def _move_highlight(self, d):
        n = len(self._view)
        if not n:
            return
        i = self.highlight if self.highlight is not None else (-1 if d > 0 else n)
        for _ in range(n):
            i = (i + d) % n
            it = self._view[i]
            if it is not None and it.is_enabled():
                self.highlight = i
                return

    def on_key(self, ui, e):
        k = e.key
        if k in (pygame.K_DOWN, pygame.K_UP):
            self._move_highlight(1 if k == pygame.K_DOWN else -1)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) or (k == pygame.K_RIGHT and self.highlight is not None
                                                           and self._view[self.highlight].submenu):
            if self.highlight is not None:
                self.pick(ui, self.highlight)
                if self.child is not None:
                    self.child._move_highlight(1)
        elif k == pygame.K_LEFT and self.parent_menu is not None:
            ui.close_popup(self)
        elif k == pygame.K_BACKSPACE and self.query:
            self.query = self.query[:-1]
            self._refilter()
            self.invalidate(layout=True)
        elif k == pygame.K_ESCAPE and self.query:
            self.query = ""
            self._refilter()
            self.invalidate(layout=True)
        else:
            return False
        self.invalidate()
        return True

    def on_text(self, ui, text):
        if not self.searchable:
            return False
        if self.child is not None:
            ui.close_popup(self.child)
        self.query += text
        self._refilter()
        self.invalidate(layout=True)
        return True


def open_context_menu(ui, items, at=None, title=""):
    """Open a menu at the pointer. Items: MenuItem / ("Label", callable) / None."""
    m = Menu(items, title=title)
    ui.ensure_text_input()
    return ui.open_popup(m, at=at or ui.mouse)


# ----------------------------------------------------------------------------- MenuBar
class MenuBar(Widget):
    """Horizontal application menu bar. Hover switches menus while one is open."""

    hover_redraw = True

    def __init__(self, menus, **kw):
        super().__init__(**kw)
        self.menus = [(title, items) for title, items in menus]
        self._open_index = None
        self._menu = None
        self._registered = None

    def _register_shortcuts(self, ui):
        if self._registered is ui:
            return
        self._registered = ui

        def walk(items):
            for it in normalize_items(items):
                if it is None:
                    continue
                if it.submenu:
                    walk(it.submenu)
                elif it.shortcut and it.action is not None:
                    ui.add_shortcut(it.shortcut, it.action, enabled=it.is_enabled)
        for _, items in self.menus:
            walk(items)

    def preferred_size(self, ui):
        self._register_shortcuts(ui)
        s = ui.theme.scale
        return (sum(ui.text_width(t) + 20 * s for t, _ in self.menus), ui.theme.h + 6 * s)

    def _title_rects(self, ui):
        s = ui.theme.scale
        x = self.x + 4 * s
        out = []
        for t, _ in self.menus:
            w = ui.text_width(t) + 20 * s
            out.append((x, self.y + 3 * s, w, self.h - 6 * s))
            x += w
        return out

    def index_at(self, ui, px, py):
        if not self.contains(px, py):
            return None
        for i, (x, y, w, h) in enumerate(self._title_rects(ui)):
            if x <= px < x + w:
                return i
        return None

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        dl.box(self.x, self.y, self.w, self.h, shade(t.panel_header_bg, 0.02), shade(t.panel_header_bg, -0.03))
        dl.box(self.x, self.y + self.h - 1, self.w, 1, t.outline)
        hover = self.index_at(ui, *ui.mouse) if self.hovered else None
        for i, ((title, _), (x, y, w, h)) in enumerate(zip(self.menus, self._title_rects(ui))):
            active = i == self._open_index
            if active or i == hover:
                dl.box(x, y, w, h, t.menu_hover if active else t.button_hover, radius=t.radius * s)
            ui.text(dl, title, x, y, w, h, t.text_on_accent if active else t.text, "center")

    def open_menu(self, ui, i):
        if self._menu is not None:
            ui.close_popup(self._menu)
        x, y, w, h = self._title_rects(ui)[i]
        m = Menu(self.menus[i][1])
        m.owner_bar = self
        orig = m.on_close

        def on_close(ui_):
            orig(ui_)
            if self._menu is m:
                self._menu = None
                self._open_index = None
                self.invalidate()

        m.on_close = on_close
        self._menu = m
        self._open_index = i
        ui.ensure_text_input()
        ui.open_popup(m, anchor=(x, self.y + self.h - 1, w, 0))
        self.invalidate()

    def on_mouse_down(self, ui, ev):
        if ev.button != 1:
            return False
        i = self.index_at(ui, ev.x, ev.y)
        if i is None:
            return False
        if i == self._open_index:
            ui.close_popups()
        else:
            ui.close_popups()
            self.open_menu(ui, i)
        return True

    def on_mouse_move(self, ui, ev):
        i = self.index_at(ui, ev.x, ev.y)
        if self._menu is not None and i is not None and i != self._open_index:
            self.open_menu(ui, i)
        elif self._menu is not None and ui.active is self:
            self._menu.track(ui, ev.x, ev.y)
        self.invalidate()

    def on_mouse_up(self, ui, ev):
        if self._menu is not None and ev.button == 1:
            self._menu.release_at(ui, ev.x, ev.y)
