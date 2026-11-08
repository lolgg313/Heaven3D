# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Layout containers: Column, Row, Canvas, ScrollArea, Spacer, Separator.

Layout is two passes: ``measure`` (bottom-up preferred sizes) and ``arrange``
(top-down final rectangles).  Rows share their width between children by
``expand`` weight; a child with an explicit ``width`` (or ``expand=0``) keeps
its preferred width.  ``align=True`` joins children into one rounded group.
All rectangles are snapped to whole pixels so 1px borders stay crisp.
"""
from __future__ import annotations

from .core import Container, Widget


class Column(Container):
    def preferred_size(self, ui):
        vis = self.visible_children()
        pad, gap = self.pad(ui), self.gap(ui)
        self._m = (pad, gap)
        n = len(vis)
        w = h = 0.0
        for i, c in enumerate(vis):
            if self.align:
                c.corners = (i == 0, i == 0, i == n - 1, i == n - 1)
            cw, ch = c.measure(ui)
            w = max(w, cw)
            h += ch
        h += gap * max(0, n - 1)
        return (w + 2 * pad, h + 2 * pad)

    def content_box(self):
        return self.x, self.y, self.w, self.h

    def arrange(self, x, y, w, h):
        super().arrange(x, y, w, h)
        pad, gap = getattr(self, "_m", (0.0, 0.0))
        bx, by, bw, bh = self.content_box()
        vis = self.visible_children()
        fillers = [c for c in vis if c.fill]
        extra = 0.0
        if fillers:
            used = sum(c._pref[1] for c in vis) + gap * max(0, len(vis) - 1) + 2 * pad
            extra = max(0.0, bh - used) / len(fillers)
        cy = by + pad
        for c in vis:
            ch = c._pref[1] + (extra if c.fill else 0.0)
            c.arrange(round(bx + pad), round(cy), round(bw - 2 * pad), round(ch))
            cy += ch + gap


class Row(Container):
    def preferred_size(self, ui):
        vis = self.visible_children()
        pad, gap = self.pad(ui), self.gap(ui)
        self._m = (pad, gap)
        n = len(vis)
        w = h = 0.0
        for i, c in enumerate(vis):
            if self.align:
                c.corners = (i == 0, i == n - 1, i == n - 1, i == 0)
            cw, ch = c.measure(ui)
            w += cw
            h = max(h, ch)
        w += gap * max(0, n - 1)
        return (w + 2 * pad, h + 2 * pad)

    def arrange(self, x, y, w, h):
        super().arrange(x, y, w, h)
        pad, gap = getattr(self, "_m", (0.0, 0.0))
        vis = self.visible_children()
        if not vis:
            return
        avail = w - 2 * pad - gap * (len(vis) - 1)
        widths = []
        weight = 0.0
        fixed = 0.0
        for c in vis:
            if c.fixed_w is not None or c.expand <= 0:
                widths.append(c._pref[0])
                fixed += c._pref[0]
            else:
                widths.append(None)
                weight += c.expand
        rest = max(0.0, avail - fixed)
        cx = x + pad
        for c, cw in zip(vis, widths):
            if cw is None:
                cw = rest * c.expand / weight if weight else 0.0
            x0 = round(cx)
            x1 = round(cx + cw)
            c.arrange(x0, round(y + pad), x1 - x0, round(h - 2 * pad))
            cx += cw + gap


class Canvas(Container):
    """Free positioning: each child is placed at ``child.pos`` (x, y).

    Empty canvas area is transparent to input, which makes Canvas the right
    root when the UI floats over a game: clicks that miss every widget fall
    through (``ui.handle_event`` returns False).
    """

    def place(self, widget, x, y):
        widget.pos = (x, y)
        if widget.parent is not self:
            self.add(widget)
        self.invalidate(layout=True)
        return widget

    def preferred_size(self, ui):
        for c in self.visible_children():
            c.measure(ui)
        return (0.0, 0.0)

    def arrange(self, x, y, w, h):
        super().arrange(x, y, w, h)
        for c in self.visible_children():
            px, py = getattr(c, "pos", (0, 0))
            cw, ch = c._pref
            c.arrange(round(x + px), round(y + py), round(cw), round(ch))

    def widget_at(self, px, py):
        if not self._visible:
            return None
        for c in reversed(self.children):
            r = c.widget_at(px, py)
            if r is not None:
                return r
        return None


class ScrollArea(Container):
    """Vertical scrolling viewport around a single content widget."""

    BAR = 6

    def __init__(self, content=None, **kw):
        super().__init__(**kw)
        self.scroll = 0.0
        self._drag = None
        self._content_h = 0.0
        if content is not None:
            self.add(content)

    @property
    def content(self):
        return self.children[0] if self.children else None

    def preferred_size(self, ui):
        self._bar = self.BAR * ui.theme.scale
        c = self.content
        if c is None:
            return (0.0, 0.0)
        cw, ch = c.measure(ui)
        if self.fill:   # a filler scroll area asks for little; the Column gives it the rest
            ch = min(ch, 80 * ui.theme.scale)
        return (cw + self._bar + 2, ch)

    @property
    def max_scroll(self):
        return max(0.0, self._content_h - self.h)

    def arrange(self, x, y, w, h):
        super().arrange(x, y, w, h)
        self._reflow()

    def _reflow(self):
        c = self.content
        if c is None:
            return
        self._content_h = c._pref[1]
        self.scroll = max(0.0, min(self.scroll, self.max_scroll))
        bar = getattr(self, "_bar", self.BAR) + 2 if self.max_scroll > 0 else 0
        c.arrange(self.x, round(self.y - self.scroll), self.w - bar, self._content_h)

    def scroll_to(self, value):
        self.scroll = value
        self._reflow()
        self.invalidate()

    def _thumb(self):
        if self.max_scroll <= 0:
            return None
        frac = self.h / self._content_h
        th = max(20.0, self.h * frac)
        ty = self.y + (self.h - th) * (self.scroll / self.max_scroll)
        bar = getattr(self, "_bar", self.BAR)
        return (self.x + self.w - bar - 1, ty, bar, th)

    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        t = self._thumb()
        if t is not None and px >= t[0] - 2:
            return self
        c = self.content
        if c is not None:
            r = c.widget_at(px, py)
            if r is not None:
                return r
        return self

    def draw(self, dl, ui):
        dl.push_clip(self.x, self.y, self.w, self.h)
        self.draw_children(dl, ui)
        dl.pop_clip()
        t = self._thumb()
        if t is not None:
            col = ui.theme.scrollbar_hover if (self._drag is not None or self.hovered) else ui.theme.scrollbar
            dl.box(*t, col, radius=t[2] * 0.5)

    hover_redraw = True

    def on_wheel(self, ui, dx, dy):
        if self.max_scroll <= 0 or not dy:
            return False
        self.scroll_to(self.scroll - dy * 48 * ui.theme.scale)
        ui._set_hover(ui.widget_at(*ui.mouse))
        return True

    def on_mouse_down(self, ui, ev):
        t = self._thumb()
        if t is None or ev.x < t[0] - 2:
            return False
        if not (t[1] <= ev.y < t[1] + t[3]):  # click in track: jump
            self.scroll_to(self.max_scroll * (ev.y - self.y - t[3] / 2) / max(1.0, self.h - t[3]))
            t = self._thumb()
        self._drag = (ev.y, self.scroll, t[3])
        return True

    def on_mouse_move(self, ui, ev):
        if self._drag is not None:
            y0, s0, th = self._drag
            track = max(1.0, self.h - th)
            self.scroll_to(s0 + (ev.y - y0) * self.max_scroll / track)

    def on_mouse_up(self, ui, ev):
        self._drag = None
        self.invalidate()


class Spacer(Widget):
    def __init__(self, size=8, **kw):
        super().__init__(**kw)
        self.size = size

    def preferred_size(self, ui):
        s = self.size * ui.theme.scale
        return (s, s)


class Separator(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)

    def preferred_size(self, ui):
        return (8 * ui.theme.scale, 9 * ui.theme.scale)

    def draw(self, dl, ui):
        y = round(self.y + self.h / 2)
        dl.box(self.x + 2, y, self.w - 4, 1, ui.theme.outline)
