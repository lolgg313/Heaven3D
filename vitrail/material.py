# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Material-style widgets: pill buttons with ripples, switches, radios,
sliders, chips, cards, FAB, app bar, outlined text fields, spinner, snackbar.

They take their colours from the current Theme, so they work with every
preset; ``Theme.material_dark()`` / ``Theme.material_light()`` give the
intended roomy, rounded look.  Animations (ripples, switch thumbs, spinner)
use ``ui.animate`` -- frames are only produced while something moves.
"""
from __future__ import annotations

import math
import time

import pygame

from .core import Popup, Signal, Widget
from .layout import Column
from .theme import shade, with_alpha
from .widgets import TextField, ValueWidget, _BoolWidget

SIZEWE = pygame.SYSTEM_CURSOR_SIZEWE
HAND = pygame.SYSTEM_CURSOR_HAND


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class _Ripple:
    """Mixin: press ripple clipped to the widget's rounded shape."""

    RIPPLE_TIME = 0.55
    _rip = None

    def start_ripple(self, ui, x, y):
        self._rip = (x, y, time.perf_counter())
        ui.animate(self, self.RIPPLE_TIME)

    def draw_ripple(self, dl, ui, color, rect=None, radius=None):
        if self._rip is None:
            return
        x, y, t0 = self._rip
        e = time.perf_counter() - t0
        if e > self.RIPPLE_TIME:
            self._rip = None
            return
        rx, ry, rw, rh = rect or (self.x, self.y, self.w, self.h)
        far = max(math.hypot(x - cx, y - cy) for cx in (rx, rx + rw) for cy in (ry, ry + rh))
        r = far * _ease(e / (self.RIPPLE_TIME * 0.6))
        a = color[3] * (1.0 - max(0.0, (e - self.RIPPLE_TIME * 0.45) / (self.RIPPLE_TIME * 0.55)))
        dl.ripple(rx, ry, rw, rh, radius if radius is not None else rh / 2, x, y, r, with_alpha(color, a))


# =============================================================================== buttons
class MButton(_Ripple, Widget):
    """Pill button. variant: "filled", "tonal", "outlined", "text", "elevated"."""

    hover_redraw = True
    cursor = HAND

    def __init__(self, text="", on_click=None, *, variant="filled", icon=None, **kw):
        super().__init__(**kw)
        self.text = text
        self.variant = variant
        self.icon = icon
        self.clicked = Signal()
        if on_click is not None:
            self.clicked.connect(on_click)
        self._pressed = False

    def preferred_size(self, ui):
        s = ui.theme.scale
        iw = ui.icon_size(18) + 8 * s if self.icon else 0
        return (ui.text_width(self.text, bold=True) + iw + 48 * s, max(ui.theme.h + 6 * s, 36 * s))

    def colors(self, ui):
        t = ui.theme
        v = self.variant
        if v == "filled":
            return t.accent, t.text_on_accent, None
        if v == "tonal":
            return with_alpha(t.accent, 0.22), t.text, None
        if v == "outlined":
            return (0, 0, 0, 0), t.accent, t.outline
        if v == "elevated":
            return shade(t.panel, 0.06), t.accent, None
        return (0, 0, 0, 0), t.accent, None

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        bg, fg, border = self.colors(ui)
        if not self.enabled:
            bg, fg = with_alpha(bg, bg[3] * 0.35), with_alpha(t.text_dim, 0.6)
        r = self.h / 2
        if self.variant in ("elevated", "filled") and self.enabled:
            dl.shadow(self.x, self.y + (2 if self.hovered else 1) * s, self.w, self.h, (0, 0, 0, 0.35 if self.hovered else 0.22),
                      r, (6 if self.hovered else 3) * s)
        dl.box(self.x, self.y, self.w, self.h, bg, border=border, radius=r)
        if self.hovered and self.enabled:
            dl.box(self.x, self.y, self.w, self.h, with_alpha(fg, 0.08), radius=r)
        self.draw_ripple(dl, ui, with_alpha(fg, 0.28))
        isz = ui.icon_size(18) if self.icon else 0
        gap = 8 * s if self.icon and self.text else 0
        tw = ui.text_width(self.text, bold=True)
        x = self.x + (self.w - isz - gap - tw) / 2
        if self.icon:
            ui.icon(dl, self.icon, x, self.y + (self.h - isz) / 2, 18, fg)
        ui.text(dl, self.text, x + isz + gap, self.y, tw + 2, self.h, fg, bold=True)

    def on_mouse_down(self, ui, ev):
        if ev.button != 1:
            return False
        self._pressed = True
        self.start_ripple(ui, ev.x, ev.y)
        return True

    def on_mouse_up(self, ui, ev):
        fire = self._pressed and self.contains(ev.x, ev.y)
        self._pressed = False
        if fire:
            self.clicked.emit()

    def activate(self, ui):
        self.start_ripple(ui, self.x + self.w / 2, self.y + self.h / 2)
        self.clicked.emit()


class FAB(MButton):
    """Floating action button (icon, optional label)."""

    def __init__(self, icon="plus", on_click=None, *, text="", **kw):
        super().__init__(text, on_click, variant="filled", icon=icon, **kw)

    def preferred_size(self, ui):
        s = ui.theme.scale
        if not self.text:
            return (56 * s, 56 * s)
        return (ui.text_width(self.text, bold=True) + 76 * s, 56 * s)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = 16 * s
        dl.shadow(self.x, self.y + 3 * s, self.w, self.h, (0, 0, 0, 0.4), r, 10 * s)
        dl.box(self.x, self.y, self.w, self.h, shade(t.accent, 0.08) if self.hovered else t.accent, radius=r)
        self.draw_ripple(dl, ui, with_alpha(t.text_on_accent, 0.3), radius=r)
        isz = ui.icon_size(24)
        if self.text:
            ui.icon(dl, self.icon, self.x + 18 * s, self.y + (self.h - isz) / 2, 24, t.text_on_accent)
            ui.text(dl, self.text, self.x + 18 * s + isz + 10 * s, self.y, self.w, self.h, t.text_on_accent, bold=True)
        else:
            ui.icon_centered(dl, self.icon, self.x, self.y, self.w, self.h, 24, t.text_on_accent)


class IconButton(MButton):
    """Round icon-only button (toolbar / app bar)."""

    def __init__(self, icon, on_click=None, *, tooltip=None, **kw):
        super().__init__("", on_click, variant="text", icon=icon, tooltip=tooltip, **kw)

    def preferred_size(self, ui):
        s = ui.theme.scale
        return (40 * s, 40 * s)

    def draw(self, dl, ui):
        t = ui.theme
        r = min(self.w, self.h) / 2
        if self.hovered:
            dl.box(self.x, self.y, self.w, self.h, with_alpha(t.text, 0.08), radius=r)
        self.draw_ripple(dl, ui, with_alpha(t.text, 0.22), radius=r)
        ui.icon_centered(dl, self.icon, self.x, self.y, self.w, self.h, 20, t.text if self.enabled else t.text_dim)


# =============================================================================== toggles
class Switch(_BoolWidget):
    cursor = HAND

    def __init__(self, text="", value=False, **kw):
        super().__init__(text, value, **kw)
        self._anim = (0.0, 1.0 if value else 0.0)

    def set_value(self, v, *, undo=False, emit=True):
        old = self._value
        changed = super().set_value(v, undo=undo, emit=emit)
        if changed:
            self._anim = (time.perf_counter(), 1.0 if old else 0.0)
            ui = self.ui
            if ui is not None:
                ui.animate(self, 0.2)
        return changed

    def preferred_size(self, ui):
        s = ui.theme.scale
        return (ui.text_width(self.text) + 64 * s, max(ui.theme.h, 32 * s))

    def _pos(self):
        t0, start = self._anim
        target = 1.0 if self._value else 0.0
        k = _ease((time.perf_counter() - t0) / 0.18)
        return start + (target - start) * k

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        tw, th = 44 * s, 24 * s
        x = self.x + self.w - tw - 2 * s
        y = round(self.y + (self.h - th) / 2)
        p = self._pos()
        track_on = t.accent
        track_off = shade(t.field, 0.05)
        track = tuple(a + (b - a) * p for a, b in zip(track_off, track_on))
        dl.box(x, y, tw, th, track, border=None if p > 0.5 else t.outline, radius=th / 2,
               border_width=1.5 * s)
        d = (14 + 6 * p) * s
        cx = x + th / 2 + (tw - th) * p
        knob = tuple(a + (b - a) * p for a, b in zip(t.text_dim, t.text_on_accent))
        if self.hovered:
            dl.box(cx - 17 * s, y + th / 2 - 17 * s, 34 * s, 34 * s, with_alpha(t.text, 0.07), radius=17 * s)
        dl.box(cx - d / 2, y + (th - d) / 2, d, d, knob, radius=d / 2)
        if p > 0.6:
            ui.icon_centered(dl, "check", cx - d / 2, y + (th - d) / 2, d, d, 12, with_alpha(t.accent, (p - 0.6) / 0.4))
        if self.text:
            ui.text(dl, self.text, self.x, self.y, self.w - tw - 12 * s, self.h,
                    t.text if self.enabled else t.text_dim)


class Chip(_Ripple, _BoolWidget):
    """Filter chip: toggles; shows a check when selected."""

    cursor = HAND

    def __init__(self, text, value=False, *, icon=None, **kw):
        super().__init__(text, value, **kw)
        self.icon = icon

    def preferred_size(self, ui):
        s = ui.theme.scale
        return (ui.text_width(self.text) + 28 * s + 22 * s, 32 * s)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = 8 * s
        on = self._value
        bg = with_alpha(t.accent, 0.24) if on else (0, 0, 0, 0)
        dl.box(self.x, self.y, self.w, self.h, bg, border=None if on else t.outline, radius=r, border_width=1.2 * s)
        if self.hovered:
            dl.box(self.x, self.y, self.w, self.h, with_alpha(t.text, 0.06), radius=r)
        self.draw_ripple(dl, ui, with_alpha(t.text, 0.2), radius=r)
        icon = "check" if on else self.icon
        isz = ui.icon_size(16)
        x = self.x + 12 * s
        if icon:
            ui.icon(dl, icon, x, self.y + (self.h - isz) / 2, 16, t.accent if on else t.text_dim)
            x += isz + 6 * s
        else:
            x += (isz + 6 * s) / 2
        ui.text(dl, self.text, x, self.y, self.x + self.w - x - 8 * s, self.h, t.text)

    def on_mouse_down(self, ui, ev):
        if ev.button == 1:
            self.start_ripple(ui, ev.x, ev.y)
        return super().on_mouse_down(ui, ev)


class RadioGroup(ValueWidget):
    """Vertical (or ``horizontal=True``) list of radio buttons."""

    hover_redraw = True
    cursor = HAND

    def __init__(self, options, value=None, *, horizontal=False, **kw):
        self.options = [(o[0], str(o[1])) if isinstance(o, tuple) else (o, str(o)) for o in options]
        if value is None and self.options:
            value = self.options[0][0]
        self.horizontal = horizontal
        self._hot = None
        super().__init__(value, **kw)

    def _item_w(self, ui, lab):
        return ui.text_width(lab) + 40 * ui.theme.scale

    def preferred_size(self, ui):
        s = ui.theme.scale
        rh = max(ui.theme.h, 32 * s)
        if self.horizontal:
            return (sum(self._item_w(ui, l) for _, l in self.options), rh)
        return (max([self._item_w(ui, l) for _, l in self.options] + [0]), rh * len(self.options))

    def _rects(self, ui):
        s = ui.theme.scale
        rh = max(ui.theme.h, 32 * s)
        out, x, y = [], self.x, self.y
        for _, lab in self.options:
            w = self._item_w(ui, lab)
            out.append((x, y, w if self.horizontal else self.w, rh))
            if self.horizontal:
                x += w
            else:
                y += rh
        return out

    def _index_at(self, ui, px, py):
        for i, (x, y, w, h) in enumerate(self._rects(ui)):
            if x <= px < x + w and y <= py < y + h:
                return i
        return None

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        for i, ((val, lab), (x, y, w, h)) in enumerate(zip(self.options, self._rects(ui))):
            on = val == self._value
            d = 18 * s
            cx, cy = x + 4 * s, y + (h - d) / 2
            if i == self._hot and self.hovered:
                dl.box(cx - 8 * s, cy - 8 * s, d + 16 * s, d + 16 * s, with_alpha(t.text, 0.07), radius=d / 2 + 8 * s)
            dl.box(cx, cy, d, d, (0, 0, 0, 0), border=t.accent if on else t.text_dim, radius=d / 2, border_width=2 * s)
            if on:
                dl.box(cx + 4.5 * s, cy + 4.5 * s, d - 9 * s, d - 9 * s, t.accent, radius=(d - 9 * s) / 2)
            ui.text(dl, lab, cx + d + 10 * s, y, w - d - 18 * s, h, t.text if self.enabled else t.text_dim)

    def on_mouse_move(self, ui, ev):
        i = self._index_at(ui, ev.x, ev.y)
        if i != self._hot:
            self._hot = i
            self.invalidate()

    def on_mouse_down(self, ui, ev):
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        i = self._index_at(ui, ev.x, ev.y)
        if ev.button == 1 and i is not None:
            self.set_value(self.options[i][0], undo=True)
            return True
        return False

    def step(self, n, undo=False):
        vals = [v for v, _ in self.options]
        if self._value in vals:
            return self.set_value(vals[(vals.index(self._value) + n) % len(vals)], undo=undo)
        return False


# =============================================================================== slider
class Slider(ValueWidget):
    """Track + thumb slider. ``step`` makes it discrete (with tick marks)."""

    hover_redraw = True
    cursor = HAND

    def __init__(self, value=0.5, *, min=0.0, max=1.0, step=None, label="", show_value=True, fmt="{:.2f}", **kw):
        self.min, self.max, self.step_size = min, max, step
        self.label = label
        self.show_value = show_value
        self.fmt = fmt
        self._drag = False
        super().__init__(value, **kw)

    def coerce(self, v):
        v = max(self.min, min(self.max, float(v)))
        if self.step_size:
            v = self.min + round((v - self.min) / self.step_size) * self.step_size
        return v

    def preferred_size(self, ui):
        s = ui.theme.scale
        return (200 * s, max(ui.theme.h, 36 * s) + (16 * s if self.label else 0))

    def _track(self, ui):
        s = ui.theme.scale
        top = self.y + (16 * s if self.label else 0)
        h = self.h - (16 * s if self.label else 0)
        vw = 52 * s if self.show_value else 0
        return (self.x + 10 * s, top + h / 2, self.w - 20 * s - vw)

    def _frac(self):
        return 0.0 if self.max == self.min else (self._value - self.min) / (self.max - self.min)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        if self.label:
            ui.text(dl, self.label, self.x + 2 * s, self.y, self.w, 16 * s, t.text_dim, size=12)
        x, cy, w = self._track(ui)
        f = self._frac()
        th = 4 * s
        dl.box(x, cy - th / 2, w, th, with_alpha(t.accent, 0.25), radius=th / 2)
        dl.box(x, cy - th / 2, w * f, th, t.accent, radius=th / 2)
        if self.step_size:
            n = int(round((self.max - self.min) / self.step_size))
            if 0 < n <= 40:
                for i in range(n + 1):
                    tx = x + w * i / n
                    dl.box(tx - 1.5 * s, cy - 1.5 * s, 3 * s, 3 * s,
                           with_alpha(t.text_on_accent if i / n <= f else t.accent, 0.7), radius=1.5 * s)
        kx = x + w * f
        hot = self.hovered or self._drag
        if hot:
            hr = 18 * s
            dl.box(kx - hr, cy - hr, 2 * hr, 2 * hr, with_alpha(t.accent, 0.14 if self._drag else 0.08), radius=hr)
        kr = 9 * s
        dl.shadow(kx - kr, cy - kr + s, 2 * kr, 2 * kr, (0, 0, 0, 0.3), kr, 3 * s)
        dl.box(kx - kr, cy - kr, 2 * kr, 2 * kr, t.accent, radius=kr)
        if self.show_value:
            ui.text(dl, self.fmt.format(self._value), x + w + 12 * s, cy - t.h / 2, 48 * s, t.h, t.text, "right")
        if self._drag:   # value bubble
            txt = self.fmt.format(self._value)
            bw = ui.text_width(txt) + 16 * s
            by = cy - kr - 30 * s
            dl.box(kx - bw / 2, by, bw, 24 * s, t.accent, radius=12 * s)
            ui.text(dl, txt, kx - bw / 2, by, bw, 24 * s, t.text_on_accent, "center")

    def _set_from(self, ui, px):
        x, _, w = self._track(ui)
        self.set_value(self.min + (self.max - self.min) * max(0.0, min(1.0, (px - x) / max(1.0, w))))

    def on_mouse_down(self, ui, ev):
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        self.begin_edit()
        self._drag = True
        self._set_from(ui, ev.x)
        return True

    def on_mouse_move(self, ui, ev):
        if self._drag:
            self._set_from(ui, ev.x)

    def on_mouse_up(self, ui, ev):
        if self._drag:
            self._drag = False
            self.end_edit()
            self.invalidate()

    def step(self, n, undo=False):
        return self.set_value(self._value + n * (self.step_size or (self.max - self.min) / 20), undo=undo)

    def copy_text(self):
        return repr(self._value)

    def paste_text(self, s):
        try:
            self.set_value(float(s), undo=True)
        except ValueError:
            pass


# =============================================================================== containers
class Card(Column):
    """Elevated rounded surface with an optional title / subtitle."""

    def __init__(self, title="", *children, subtitle="", elevation=2, padding=14, spacing=8, **kw):
        super().__init__(*children, padding=padding, spacing=spacing, **kw)
        self.title = title
        self.subtitle = subtitle
        self.elevation = elevation

    def _head(self, ui):
        s = ui.theme.scale
        h = 0.0
        if self.title:
            h += 24 * s
        if self.subtitle:
            h += 18 * s
        return h + (6 * s if h else 0)

    def preferred_size(self, ui):
        self._hh = self._head(ui)
        w, h = super().preferred_size(ui)
        tw = max(ui.text_width(self.title, size=17, bold=True), ui.text_width(self.subtitle)) + 2 * self.padding * ui.theme.scale
        return (max(w, tw), h + self._hh)

    def content_box(self):
        hh = getattr(self, "_hh", 0)
        return self.x, self.y + hh, self.w, self.h - hh

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = 14 * s
        if self.elevation:
            e = self.elevation
            dl.shadow(self.x, self.y + e * s, self.w, self.h, (0, 0, 0, 0.18 + 0.05 * e), r, (4 + 3 * e) * s)
        dl.box(self.x, self.y, self.w, self.h, shade(t.panel, 0.025 * self.elevation), border=with_alpha(t.outline, 0.6), radius=r)
        y = self.y + self.padding * s
        if self.title:
            ui.text(dl, self.title, self.x + self.padding * s, y, self.w - 2 * self.padding * s, 24 * s, t.text,
                    bold=True, size=17)
            y += 24 * s
        if self.subtitle:
            ui.text(dl, self.subtitle, self.x + self.padding * s, y, self.w - 2 * self.padding * s, 18 * s, t.text_dim)
        self.draw_children(dl, ui)


class AppBar(Widget):
    """Top app bar: navigation icon, title, action icons."""

    hover_redraw = True

    def __init__(self, title="", *, nav_icon="menu", on_nav=None, actions=(), **kw):
        super().__init__(**kw)
        self.title = title
        self.nav_icon = nav_icon
        self.on_nav = on_nav
        self.actions = list(actions)   # [(icon, callable, tooltip?)]
        self._hot = None

    def preferred_size(self, ui):
        return (300 * ui.theme.scale, 56 * ui.theme.scale)

    def _buttons(self, ui):
        s = ui.theme.scale
        b = 40 * s
        y = self.y + (self.h - b) / 2
        out = []
        if self.nav_icon:
            out.append((self.x + 8 * s, y, b, self.nav_icon, self.on_nav))
        x = self.x + self.w - 8 * s - b
        for a in reversed(self.actions):
            out.append((x, y, b, a[0], a[1]))
            x -= b + 2 * s
        return out

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        dl.box(self.x, self.y, self.w, self.h, t.panel_header_bg)
        mx, my = ui.mouse
        for i, (x, y, b, icon, _) in enumerate(self._buttons(ui)):
            if self.hovered and x <= mx < x + b and y <= my < y + b:
                dl.box(x, y, b, b, with_alpha(t.text, 0.08), radius=b / 2)
            ui.icon_centered(dl, icon, x, y, b, b, 20, t.text)
        tx = self.x + (60 if self.nav_icon else 16) * s
        ui.text(dl, self.title, tx, self.y, self.w - tx - 160 * s, self.h, t.text, size=19)

    def on_mouse_move(self, ui, ev):
        self.invalidate()

    def on_mouse_down(self, ui, ev):
        for x, y, b, icon, fn in self._buttons(ui):
            if x <= ev.x < x + b and y <= ev.y < y + b:
                if fn is not None:
                    fn()
                return True
        return False


class OutlinedField(TextField):
    """Text field with an outline and a floating label."""

    def __init__(self, value="", *, label="", supporting="", **kw):
        super().__init__(value, placeholder="", **kw)
        self.float_label = label
        self.supporting = supporting
        self.label = ""

    def preferred_size(self, ui):
        s = ui.theme.scale
        return (220 * s, 52 * s + (16 * s if self.supporting else 0))

    def _box_rect(self, ui):
        s = ui.theme.scale
        return (self.x, self.y + 6 * s, self.w, 44 * s)

    def _text_rect(self, ui):
        bx, by, bw, bh = self._box_rect(ui)
        s = ui.theme.scale
        return (bx + 14 * s, by, bw - 28 * s, bh)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        bx, by, bw, bh = self._box_rect(ui)
        focused = self.editing
        floated = focused or bool(self._value)
        border = t.accent if focused else (t.text_dim if self.hovered else t.outline)
        dl.box(bx, by, bw, bh, (0, 0, 0, 0), border=border, radius=6 * s, border_width=(2 if focused else 1.2) * s)
        x, y, w, h = self._text_rect(ui)
        if floated:
            lw = ui.text_width(self.float_label, size=11)
            if lw:
                dl.box(bx + 10 * s, by - 2 * s, lw + 8 * s, 4 * s, ui.theme.panel)
                ui.text(dl, self.float_label, bx + 14 * s, by - 8 * s, lw + 2, 14 * s, t.accent if focused else t.text_dim,
                        size=11)
        else:
            ui.text(dl, self.float_label, x, y, w, h, t.text_dim)
        if self.editing:
            self.editor.draw(dl, ui, x, y, w, h, t.text)
        elif self._value:
            ui.text(dl, self._value, x, y, w, h, t.text)
        if self.supporting:
            ui.text(dl, self.supporting, bx + 14 * s, by + bh + 1 * s, bw, 15 * s, t.text_dim, size=11)


class Spinner(Widget):
    """Indeterminate circular progress (animates only while visible)."""

    def __init__(self, size=32, **kw):
        super().__init__(**kw)
        self.size = size

    def preferred_size(self, ui):
        d = self.size * ui.theme.scale
        return (d, d)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        d = min(self.w, self.h)
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        r = d / 2 - 3 * s
        now = time.perf_counter()
        head = now * 5.0
        span = 0.6 + 2.2 * (0.5 + 0.5 * math.sin(now * 2.6))
        dl.arc(cx, cy, r, 0, math.tau, with_alpha(t.accent, 0.18), 3.5 * s, 40)
        dl.arc(cx, cy, r, head, head + span, t.accent, 3.5 * s)
        ui.animate(self, 0.05)


class Snackbar(Popup):
    """Transient message at the bottom of the window with an optional action."""

    close_on_outside = False
    passive = True

    def __init__(self, text, action=None, duration=4.0):
        super().__init__()
        self.text = text
        self.action = action   # ("Undo", callable)
        self.duration = duration
        self._hot = False

    @classmethod
    def show(cls, ui, text, action=None, duration=4.0):
        for p in list(ui.overlays):
            if isinstance(p, Snackbar):
                ui.close_popup(p)
        sb = cls(text, action, duration)
        ui.open_popup(sb)
        ui.call_later(duration, lambda: ui.close_popup(sb))
        return sb

    def preferred_size(self, ui):
        s = ui.theme.scale
        aw = ui.text_width(self.action[0], bold=True) + 32 * s if self.action else 0
        return (min(ui.size[0] - 32 * s, max(300 * s, ui.text_width(self.text) + aw + 40 * s)), 48 * s)

    def place(self, ui):
        w, h = self.measure(ui)
        self.arrange(round((ui.size[0] - w) / 2), round(ui.size[1] - h - 24 * ui.theme.scale), round(w), round(h))

    def widget_at(self, px, py):
        return self if self.contains(px, py) else None

    def _action_rect(self, ui):
        s = ui.theme.scale
        aw = ui.text_width(self.action[0], bold=True) + 24 * s
        return (self.x + self.w - aw - 8 * s, self.y + 8 * s, aw, self.h - 16 * s)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        inv_bg = t.tooltip
        inv_fg = t.tooltip_text
        dl.shadow(self.x, self.y + 2 * s, self.w, self.h, (0, 0, 0, 0.45), 6 * s, 10 * s)
        dl.box(self.x, self.y, self.w, self.h, inv_bg, radius=6 * s)
        ui.text(dl, self.text, self.x + 16 * s, self.y, self.w - 140 * s, self.h, inv_fg)
        if self.action:
            ax, ay, aw, ah = self._action_rect(ui)
            if self._hot:
                dl.box(ax, ay, aw, ah, with_alpha(t.accent, 0.15), radius=ah / 2)
            ui.text(dl, self.action[0], ax, ay, aw, ah, shade(t.accent, 0.25) if t.text[0] > 0.5 else t.accent,
                    "center", bold=True)

    def on_mouse_move(self, ui, ev):
        if self.action:
            ax, ay, aw, ah = self._action_rect(ui)
            hot = ax <= ev.x < ax + aw and ay <= ev.y < ay + ah
            if hot != self._hot:
                self._hot = hot
                self.invalidate()

    def on_mouse_down(self, ui, ev):
        if self.action:
            ax, ay, aw, ah = self._action_rect(ui)
            if ax <= ev.x < ax + aw and ay <= ev.y < ay + ah:
                ui.close_popup(self)
                self.action[1]()
        return True

    def on_key(self, ui, e):
        return False
