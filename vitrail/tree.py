# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Tree view (outliner / file list).

* virtualised: only the visible rows are drawn, so 100 000 items scroll fine
* optional columns with click-to-sort headers
* lazy children (``TreeItem(lazy=fn)``) for file systems and big data
* multi-select (Ctrl / Shift), keyboard navigation, type-ahead search
* drag & drop re-parenting / re-ordering (``drag_drop=True``)
"""
from __future__ import annotations

import time

import pygame

from .core import CTRL, SHIFT, Signal, Widget
from .theme import shade, with_alpha


class TreeItem:
    __slots__ = ("text", "children", "parent", "icon", "icon_color", "data", "expanded", "values",
                 "sort_keys", "lazy", "_loaded", "enabled")

    def __init__(self, text, children=None, *, icon=None, icon_color=None, data=None, expanded=False,
                 values=(), sort_keys=None, lazy=None, enabled=True):
        self.text = str(text)
        self.children = []
        self.parent = None
        self.icon = icon
        self.icon_color = icon_color
        self.data = data
        self.expanded = expanded
        self.values = tuple(str(v) for v in values)
        self.sort_keys = sort_keys
        self.lazy = lazy
        self._loaded = lazy is None
        self.enabled = enabled
        for c in children or ():
            self.add(c)

    def add(self, child, index=None):
        if isinstance(child, str):
            child = TreeItem(child)
        if child.parent is not None:
            child.parent.children.remove(child)
        child.parent = self
        if index is None:
            self.children.append(child)
        else:
            self.children.insert(index, child)
        return child

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    @property
    def has_children(self):
        return bool(self.children) or not self._loaded

    def ensure_loaded(self):
        if not self._loaded:
            self._loaded = True
            for c in self.lazy(self) or ():
                self.add(c)

    def is_ancestor_of(self, other):
        p = other.parent
        while p is not None:
            if p is self:
                return True
            p = p.parent
        return False

    def depth(self):
        d, p = 0, self.parent
        while p is not None:
            d, p = d + 1, p.parent
        return d

    def __repr__(self):
        return f"<TreeItem {self.text!r}>"


class TreeView(Widget):
    focusable = True
    wants_text_input = True
    hover_redraw = True

    def __init__(self, items=(), *, columns=None, multi_select=True, drag_drop=False, row_height=None,
                 rows=12, show_root_lines=True, **kw):
        super().__init__(**kw)
        self.root = TreeItem("<root>", list(items), expanded=True)
        self.columns = list(columns) if columns else None   # [(title, width or None for flex)]
        self.multi_select = multi_select
        self.drag_drop = drag_drop
        self.row_height = row_height
        self.min_rows = rows
        self.show_root_lines = show_root_lines
        self.selection = []
        self.cursor_item = None
        self._anchor = None
        self.scroll = 0.0
        self._rows = None
        self._hover_row = None
        self._press = None
        self._drop = None
        self._typeahead = ("", 0.0)
        self._sort = (None, False)
        self._sb_drag = None
        self.sorter = None             # fn(items, column, descending) -> list
        self.drop_filter = None        # fn(payload) -> bool: accept external drag & drop payloads
        self.payload_dropped = Signal()  # (payload, item or None, "before"/"into"/"after")
        self.selection_changed = Signal()
        self.activated = Signal()      # (item) double-click / Enter
        self.expanded_changed = Signal()
        self.moved = Signal()          # (item, new_parent, index)
        self.context = Signal()        # (item or None, x, y) right click

    # -- model -----------------------------------------------------------------------
    @property
    def items(self):
        return self.root.children

    def set_items(self, items):
        self.root = TreeItem("<root>", list(items), expanded=True)
        self.selection = []
        self.cursor_item = None
        self.scroll = 0.0
        self.refresh()

    def add(self, item, parent=None):
        (parent or self.root).add(item)
        self.refresh()
        return item

    def refresh(self):
        """Call after changing the item tree."""
        self._rows = None
        if self._sort[0] is not None:
            self._apply_sort(self.root)
        self.invalidate()

    def rows(self):
        if self._rows is None:
            out = []

            def walk(item, depth):
                for c in item.children:
                    out.append((c, depth))
                    if c.expanded:
                        c.ensure_loaded()
                        walk(c, depth + 1)
            walk(self.root, 0)
            self._rows = out
        return self._rows

    def set_expanded(self, item, on=True):
        if on and not item.has_children:
            return
        if item.expanded != on:
            item.expanded = on
            if on:
                item.ensure_loaded()
                if self._sort[0] is not None:
                    self._apply_sort(item)
            self._rows = None
            self.expanded_changed.emit(item)
            self.invalidate()

    def select(self, items, emit=True):
        self.selection = [i for i in items if i is not None]
        if self.selection:
            self.cursor_item = self.selection[-1]
        self.invalidate()
        if emit:
            self.selection_changed.emit(list(self.selection))

    def reveal(self, item):
        p = item.parent
        while p is not None and p is not self.root:
            self.set_expanded(p, True)
            p = p.parent
        rows = self.rows()
        for i, (it, _) in enumerate(rows):
            if it is item:
                self._scroll_to_row(i)
                break

    # -- geometry -----------------------------------------------------------------------
    def rh(self, ui):
        return round((self.row_height or 22) * ui.theme.scale)

    def header_h(self, ui):
        return self.rh(ui) + 2 if self.columns else 0

    def preferred_size(self, ui):
        return (220 * ui.theme.scale, self.header_h(ui) + self.min_rows * self.rh(ui) + 2)

    def _view_h(self, ui):
        return self.h - self.header_h(ui) - 2

    def max_scroll(self, ui):
        return max(0.0, len(self.rows()) * self.rh(ui) - self._view_h(ui))

    def _clamp_scroll(self, ui):
        self.scroll = max(0.0, min(self.scroll, self.max_scroll(ui)))

    def _scroll_to_row(self, i):
        ui = self.ui
        if ui is None:
            return
        rh = self.rh(ui)
        top = i * rh
        vh = self._view_h(ui)
        if top < self.scroll:
            self.scroll = top
        elif top + rh > self.scroll + vh:
            self.scroll = top + rh - vh
        self._clamp_scroll(ui)
        self.invalidate()

    def _col_x(self, ui):
        """x offsets/widths of columns (first column is the tree)."""
        if not self.columns:
            return [(self.x, self.w)]
        sb = 10 * ui.theme.scale
        fixed = sum(w * ui.theme.scale for _, w in self.columns if w)
        flex = [c for c in self.columns if not c[1]]
        fw = max(40.0, (self.w - sb - fixed) / max(1, len(flex)))
        out, x = [], self.x
        for _, w in self.columns:
            cw = w * ui.theme.scale if w else fw
            out.append((x, cw))
            x += cw
        return out

    def row_at(self, ui, py):
        top = self.y + 1 + self.header_h(ui)
        if py < top or py >= self.y + self.h - 1:
            return None
        i = int((py - top + self.scroll) // self.rh(ui))
        return i if 0 <= i < len(self.rows()) else None

    def _indent(self, ui):
        return 16 * ui.theme.scale

    def _arrow_rect(self, ui, depth, ry):
        s = ui.theme.scale
        x = self.x + 6 * s + depth * self._indent(ui)
        return (x, ry, 14 * s, self.rh(ui))

    # -- painting -----------------------------------------------------------------------
    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        rh = self.rh(ui)
        focused = ui.focus is self
        dl.box(self.x, self.y, self.w, self.h, shade(t.field, -0.25), border=t.accent if focused else t.outline,
               radius=self.radii(ui))
        self._clamp_scroll(ui)
        cols = self._col_x(ui)
        hh = self.header_h(ui)
        if self.columns:
            dl.push_clip(self.x + 1, self.y + 1, self.w - 2, hh)
            dl.box(self.x + 1, self.y + 1, self.w - 2, hh - 1, t.panel_header_bg, radius=(t.radius * s, t.radius * s, 0, 0))
            dl.box(self.x + 1, self.y + hh, self.w - 2, 1, t.outline)
            for ci, ((title, _), (cx, cw)) in enumerate(zip(self.columns, cols)):
                if ci:
                    dl.box(cx, self.y + 4 * s, 1, hh - 8 * s, t.outline)
                end = ui.text(dl, title, cx + 8 * s, self.y + 1, cw - 26 * s, hh - 1, t.text_dim, bold=True)
                if self._sort[0] == ci:
                    a = round(7 * s)
                    dl.triangle(end + 6 * s, self.y + (hh - a) / 2 + 1, a, a, t.text_dim, 3 if not self._sort[1] else 1)
            dl.pop_clip()
        top = self.y + 1 + hh
        vh = self._view_h(ui)
        dl.push_clip(self.x + 1, top, self.w - 2, vh)
        rows = self.rows()
        first = int(self.scroll // rh)
        last = min(len(rows), first + int(vh // rh) + 2)
        sel = set(map(id, self.selection))
        isz = ui.icon_size()
        for i in range(first, last):
            item, depth = rows[i]
            ry = round(top + i * rh - self.scroll)
            if id(item) in sel:
                dl.box(self.x + 1, ry, self.w - 2, rh, with_alpha(t.accent, 0.42 if focused else 0.28))
            elif i == self._hover_row:
                dl.box(self.x + 1, ry, self.w - 2, rh, with_alpha(t.text, 0.05))
            elif i % 2 == 1:
                dl.box(self.x + 1, ry, self.w - 2, rh, with_alpha(t.text, 0.018))
            if item is self.cursor_item and focused and id(item) not in sel:
                dl.box(self.x + 1, ry, self.w - 2, rh, (0, 0, 0, 0), border=with_alpha(t.accent, 0.6), radius=2)
            ax, _, aw, _ = self._arrow_rect(ui, depth, ry)
            if self.show_root_lines and depth:
                for d in range(depth):
                    lx = round(self.x + 6 * s + d * self._indent(ui) + 7 * s)
                    dl.box(lx, ry, 1, rh, with_alpha(t.text, 0.07))
            if item.has_children:
                a = round(8 * s)
                dl.triangle(ax + 3 * s, ry + (rh - a) / 2, a, a, t.text_dim, 1 if item.expanded else 0)
            x = ax + aw + 2 * s
            col = t.text if item.enabled else t.text_dim
            c0x, c0w = cols[0]
            if item.icon:
                ui.icon(dl, item.icon, x, ry + (rh - isz) / 2, None, item.icon_color or t.text_dim)
                x += isz + 6 * s
            ui.text(dl, item.text, x, ry, c0x + c0w - x - 6 * s, rh, col)
            for (cx, cw), v in zip(cols[1:], item.values):
                ui.text(dl, v, cx + 8 * s, ry, cw - 14 * s, rh, t.text_dim)
        if ui.drag is not None and ui.drag.target is self:
            hi = self.row_at(ui, ui.mouse[1])
            if hi is not None and first <= hi < last:
                ry = round(top + hi * rh - self.scroll)
                dl.box(self.x + 2, ry + 1, self.w - 4, rh - 2, with_alpha(t.accent, 0.15), border=t.accent, radius=3)
        if self._drop is not None:
            target, where = self._drop
            for i in range(first, last):
                if rows[i][0] is target:
                    ry = round(top + i * rh - self.scroll)
                    lx = self.x + 6 * s + rows[i][1] * self._indent(ui) + 18 * s
                    if where == "into":
                        dl.box(self.x + 2, ry + 1, self.w - 4, rh - 2, (0, 0, 0, 0), border=t.accent, radius=3,
                               border_width=2)
                    else:
                        yy = ry if where == "before" else ry + rh
                        dl.box(lx, yy - 1, self.x + self.w - lx - 4, 2, t.accent)
                        dl.box(lx - 3, yy - 3, 6, 6, t.accent, radius=3)
        dl.pop_clip()
        ms = self.max_scroll(ui)
        if ms > 0:
            th = max(20.0, vh * vh / (vh + ms))
            ty = top + (vh - th) * self.scroll / ms
            bw = 6 * s
            dl.box(self.x + self.w - bw - 3, ty, bw, th, t.scrollbar_hover if self._sb_drag else t.scrollbar, radius=bw / 2)

    # -- mouse -------------------------------------------------------------------------
    def on_wheel(self, ui, dx, dy):
        if not dy:
            return False
        self.scroll -= dy * self.rh(ui) * 3
        self._clamp_scroll(ui)
        self._hover_row = self.row_at(ui, ui.mouse[1])
        self.invalidate()
        return True

    def on_hover(self, ui, hovered):
        if not hovered:
            self._hover_row = None
        self.invalidate()

    def _header_hit(self, ui, ev):
        if not self.columns or ev.y >= self.y + 1 + self.header_h(ui):
            return False
        for ci, (cx, cw) in enumerate(self._col_x(ui)):
            if cx <= ev.x < cx + cw:
                asc = not (self._sort[0] == ci and not self._sort[1])
                self.sort_by(ci, descending=not asc)
                return True
        return True

    def sort_by(self, column, descending=False):
        self._sort = (column, descending)
        self._apply_sort(self.root)
        self._rows = None
        self.invalidate()

    def _key(self, item, col):
        if item.sort_keys is not None and col < len(item.sort_keys):
            return item.sort_keys[col]
        if col == 0:
            return item.text.lower()
        return item.values[col - 1] if col - 1 < len(item.values) else ""

    def _apply_sort(self, item):
        col, desc = self._sort
        if col is None:
            return
        if self.sorter is not None:
            item.children = list(self.sorter(item.children, col, desc))
        else:
            item.children.sort(key=lambda it: self._key(it, col), reverse=desc)
        for c in item.children:
            if c.children:
                self._apply_sort(c)

    def on_mouse_down(self, ui, ev):
        ui.set_focus(self)
        bw = 10 * ui.theme.scale
        if self.max_scroll(ui) > 0 and ev.x >= self.x + self.w - bw:
            self._sb_drag = (ev.y, self.scroll)
            return True
        if self._header_hit(ui, ev):
            return True
        i = self.row_at(ui, ev.y)
        rows = self.rows()
        if ev.button == 3:
            item = rows[i][0] if i is not None else None
            if item is not None and item not in self.selection:
                self.select([item])
            self.context.emit(item, ev.x, ev.y)
            return True
        if ev.button != 1:
            return False
        if i is None:
            if not ev.ctrl:
                self.select([])
            return True
        item, depth = rows[i]
        ry = self.y + 1 + self.header_h(ui) + i * self.rh(ui) - self.scroll
        ax, _, aw, _ = self._arrow_rect(ui, depth, ry)
        if item.has_children and ax - 2 <= ev.x < ax + aw + 2:
            self.set_expanded(item, not item.expanded)
            return True
        if ev.clicks >= 2:
            self.select([item])
            self.activated.emit(item)
            return True
        if ev.ctrl and self.multi_select:
            sel = list(self.selection)
            if item in sel:
                sel.remove(item)
            else:
                sel.append(item)
            self.select(sel)
            self._anchor = item
        elif ev.shift and self.multi_select and self._anchor is not None:
            self._select_range(self._anchor, item)
        else:
            if item not in self.selection:
                self.select([item])
            self._anchor = item
        self.cursor_item = item
        self._press = (ev.x, ev.y, item, False)
        return True

    def _select_range(self, a, b):
        rows = [r[0] for r in self.rows()]
        try:
            ia, ib = rows.index(a), rows.index(b)
        except ValueError:
            return
        lo, hi = min(ia, ib), max(ia, ib)
        self.select(rows[lo:hi + 1])
        self.cursor_item = b

    def on_mouse_move(self, ui, ev):
        if self._sb_drag is not None:
            y0, s0 = self._sb_drag
            vh = self._view_h(ui)
            ms = self.max_scroll(ui)
            th = max(20.0, vh * vh / (vh + ms))
            self.scroll = s0 + (ev.y - y0) * ms / max(1.0, vh - th)
            self._clamp_scroll(ui)
            self.invalidate()
            return
        hr = self.row_at(ui, ev.y)
        if hr != self._hover_row:
            self._hover_row = hr
            self.invalidate()
        if self._press is None or not self.drag_drop:
            return
        x0, y0, item, dragging = self._press
        if not dragging:
            if abs(ev.x - x0) + abs(ev.y - y0) < 6:
                return
            self._press = (x0, y0, item, True)
        # auto-scroll near the edges
        top = self.y + 1 + self.header_h(ui)
        if ev.y < top + 8:
            self.scroll -= self.rh(ui) / 2
        elif ev.y > self.y + self.h - 8:
            self.scroll += self.rh(ui) / 2
        self._clamp_scroll(ui)
        self._drop = self._drop_target(ui, ev)
        self.invalidate()

    def _drop_target(self, ui, ev):
        i = self.row_at(ui, ev.y)
        if i is None:
            return None
        target, _ = self.rows()[i]
        moving = self.selection or [self._press[2]]
        if any(target is m or m.is_ancestor_of(target) for m in moving):
            return None
        ry = self.y + 1 + self.header_h(ui) + i * self.rh(ui) - self.scroll
        f = (ev.y - ry) / self.rh(ui)
        where = "before" if f < 0.28 else ("after" if f > 0.72 else "into")
        return (target, where)

    def on_mouse_up(self, ui, ev):
        self._sb_drag = None
        press, self._press = self._press, None
        drop, self._drop = self._drop, None
        if press is None:
            self.invalidate()
            return
        if not press[3]:
            if not ev.ctrl and not ev.shift and len(self.selection) > 1 and press[2] in self.selection:
                self.select([press[2]])
            return
        if drop is None:
            self.invalidate()
            return
        target, where = drop
        moving = [m for m in self.selection if not any(o.is_ancestor_of(m) for o in self.selection)] or [press[2]]
        for m in moving:
            if where == "into":
                parent, index = target, len(target.children)
                target.ensure_loaded()
                target.expanded = True
            else:
                parent = target.parent
                index = parent.children.index(target) + (1 if where == "after" else 0)
                if m.parent is parent and parent.children.index(m) < index:
                    index -= 1
            parent.add(m, index)
            if where == "after":
                target = m
            self.moved.emit(m, parent, parent.children.index(m))
        self._rows = None
        self.invalidate()

    # -- external drag & drop (ui.start_drag payloads) --------------------------------------
    def accepts_drop(self, payload):
        return self.drop_filter is not None and bool(self.drop_filter(payload))

    def on_drop(self, ui, payload, x, y):
        i = self.row_at(ui, y)
        if i is None:
            self.payload_dropped.emit(payload, None, "into")
            return
        item = self.rows()[i][0]
        ry = self.y + 1 + self.header_h(ui) + i * self.rh(ui) - self.scroll
        f = (y - ry) / self.rh(ui)
        self.payload_dropped.emit(payload, item, "before" if f < 0.25 else ("after" if f > 0.75 else "into"))

    # -- keyboard -----------------------------------------------------------------------
    def on_focus(self, ui, focused):
        self.invalidate()

    def _move_cursor(self, ui, delta, extend):
        rows = [r[0] for r in self.rows()]
        if not rows:
            return
        cur = self.cursor_item if self.cursor_item in rows else None
        i = rows.index(cur) if cur is not None else -1
        i = max(0, min(len(rows) - 1, i + delta))
        item = rows[i]
        if extend and self.multi_select:
            if self._anchor is None:
                self._anchor = cur or item
            self._select_range(self._anchor, item)
        else:
            self._anchor = item
            self.select([item])
        self.cursor_item = item
        self._scroll_to_row(i)

    def on_key(self, ui, e):
        k = e.key
        page = max(1, int(self._view_h(ui) // self.rh(ui)) - 1)
        cur = self.cursor_item
        extend = bool(e.mod & SHIFT)
        if k == pygame.K_DOWN:
            self._move_cursor(ui, 1, extend)
        elif k == pygame.K_UP:
            self._move_cursor(ui, -1, extend)
        elif k == pygame.K_PAGEDOWN:
            self._move_cursor(ui, page, extend)
        elif k == pygame.K_PAGEUP:
            self._move_cursor(ui, -page, extend)
        elif k == pygame.K_HOME:
            self._move_cursor(ui, -10 ** 9, extend)
        elif k == pygame.K_END:
            self._move_cursor(ui, 10 ** 9, extend)
        elif k == pygame.K_RIGHT and cur is not None:
            if cur.has_children and not cur.expanded:
                self.set_expanded(cur, True)
            elif cur.expanded and cur.children:
                self._move_cursor(ui, 1, False)
        elif k == pygame.K_LEFT and cur is not None:
            if cur.expanded:
                self.set_expanded(cur, False)
            elif cur.parent is not None and cur.parent is not self.root:
                self.select([cur.parent])
                self.reveal(cur.parent)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and cur is not None:
            self.activated.emit(cur)
        elif k == pygame.K_a and e.mod & CTRL and self.multi_select:
            self.select([r[0] for r in self.rows()])
        elif k == pygame.K_ESCAPE:
            ui.set_focus(None)
            return False          # let an enclosing dialog close too
        elif k == pygame.K_TAB:
            ui.focus_next(self, backward=bool(e.mod & SHIFT))
        else:
            return k not in (pygame.K_ESCAPE,) and bool(e.unicode) and e.unicode.isprintable()
        return True

    def on_text(self, ui, text):
        """Type-ahead: jump to the next item whose text starts with what was typed."""
        prev, t0 = self._typeahead
        now = time.perf_counter()
        buf = (prev if now - t0 < 0.9 else "") + text
        self._typeahead = (buf, now)
        rows = [r[0] for r in self.rows()]
        if not rows:
            return True
        start = rows.index(self.cursor_item) if self.cursor_item in rows else 0
        if len(buf) == 1:
            start += 1
        n = len(rows)
        for k in range(n):
            it = rows[(start + k) % n]
            if it.text.lower().startswith(buf.lower()):
                self.select([it])
                self._anchor = it
                self._scroll_to_row((start + k) % n)
                break
        return True
