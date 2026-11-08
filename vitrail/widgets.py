# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Standard widget set.

Interaction conventions (shared by every value widget):

* drag a number field to scrub it; Shift = fine, Ctrl = snap; a whole drag is
  one undo step
* click a number field (without dragging) to type; arithmetic is evaluated
* Ctrl+wheel steps the hovered value, Ctrl+C / Ctrl+V copy/paste it,
  Backspace resets it to its default, right-click opens a value menu
* Tab / Shift+Tab move between fields while typing
* dropdown menus open on press and pick on release; type to search them
  (typo tolerant)
* press a checkbox and drag across others to set them all
"""
from __future__ import annotations

import colorsys

import pygame

from .core import CTRL, SHIFT, Container, Popup, Signal, Widget
from .expr import ExprError, evaluate
from .layout import Column, Row
from .menus import Menu, open_context_menu
from .theme import rgb, shade, to_hex, with_alpha

IBEAM = pygame.SYSTEM_CURSOR_IBEAM
SIZEWE = pygame.SYSTEM_CURSOR_SIZEWE
HAND = pygame.SYSTEM_CURSOR_HAND


def _box(dl, ui, w, base, *, pressed=False, border=True):
    t = ui.theme
    e = t.emboss
    top, bottom = (shade(base, -e), shade(base, e)) if pressed else (shade(base, e), shade(base, -e))
    if not w.enabled:
        top, bottom = with_alpha(top, top[3] * 0.5), with_alpha(bottom, bottom[3] * 0.5)
    dl.box(w.x, w.y, w.w, w.h, top, bottom, t.outline if border else None, w.radii(ui))


def draw_icon_label(dl, ui, rect, icon, text, col, align="center", pad=4):
    """Icon + text centred (or left-aligned) inside ``rect`` (x, y, w, h)."""
    s = ui.theme.scale
    rx, ry, rw, rh = rect
    px = ui.icon_size() if icon else 0
    gap = 6 * s if (icon and text) else 0
    avail = rw - 2 * pad * s
    tw = min(ui.text_width(text), max(0.0, avail - px - gap)) if text else 0
    total = px + gap + tw
    x = rx + (rw - total) / 2 if align == "center" else rx + pad * s
    if icon:
        ui.icon(dl, icon, x, ry + (rh - px) / 2, None, col)
    if text:
        ui.text(dl, text, x + px + gap, ry, tw + 1, rh, col)


def _text_color(ui, w, on_accent=False):
    t = ui.theme
    if not w.enabled:
        return t.text_dim
    return t.text_on_accent if on_accent else t.text


# =========================================================================== basics
class Label(Widget):
    def __init__(self, text="", *, align="left", dim=False, bold=False, size=None, color=None, icon=None, **kw):
        super().__init__(**kw)
        self.icon = icon
        self._text = str(text)
        self.align = align
        self.dim = dim
        self.bold = bold
        self.size = size
        self.color = color

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, v):
        v = str(v)
        if v != self._text:
            self._text = v
            self.invalidate(layout=True)

    def preferred_size(self, ui):
        f = ui.font(self.bold, self.size)
        iw = ui.icon_size() + 6 * ui.theme.scale if self.icon else 0
        return (f.width(self._text) + iw + 4 * ui.theme.scale, max(ui.theme.h, f.line_height + 4))

    def draw(self, dl, ui):
        t = ui.theme
        col = self.color or (t.text_dim if (self.dim or not self.enabled) else t.text)
        s = t.scale
        x = self.x + 2 * s
        if self.icon:
            px = ui.icon(dl, self.icon, x, self.y + (self.h - ui.icon_size()) / 2, None, col)
            x += px + 6 * s
        ui.text(dl, self._text, x, self.y, self.x + self.w - 2 * s - x, self.h, col, self.align, self.bold, self.size)


class Button(Widget):
    hover_redraw = True

    def __init__(self, text="", on_click=None, *, primary=False, icon=None, flat=False, **kw):
        super().__init__(**kw)
        self.text = text
        self.icon = icon
        self.flat = flat
        self.primary = primary
        self.clicked = Signal()
        if on_click is not None:
            self.clicked.connect(on_click)
        self._pressed = False
        self._inside = False

    def preferred_size(self, ui):
        s = ui.theme.scale
        if self.icon and not self.text:
            return (ui.theme.h, ui.theme.h)
        iw = ui.icon_size() + 6 * s if self.icon else 0
        return (ui.text_width(self.text) + iw + 24 * s, ui.theme.h)

    def draw(self, dl, ui):
        t = ui.theme
        down = self._pressed and self._inside
        base = t.button_pressed if down else (t.accent if self.primary else (t.button_hover if self.hovered else t.button))
        if self.primary and self.hovered and not down:
            base = shade(t.accent, 0.12)
        if not self.flat or down or self.hovered:
            _box(dl, ui, self, base, pressed=down, border=not self.flat)
        col = _text_color(ui, self, down or self.primary)
        draw_icon_label(dl, ui, (self.x, self.y, self.w, self.h), self.icon, self.text, col)

    def on_mouse_down(self, ui, ev):
        if ev.button != 1:
            return False
        self._pressed = self._inside = True
        return True

    def on_mouse_move(self, ui, ev):
        if self._pressed:
            inside = self.contains(ev.x, ev.y)
            if inside != self._inside:
                self._inside = inside
                self.invalidate()

    def on_mouse_up(self, ui, ev):
        fire = self._pressed and self.contains(ev.x, ev.y)
        self._pressed = False
        if fire:
            self.clicked.emit()

    def activate(self, ui):
        self.clicked.emit()


# =========================================================================== value base
class ValueWidget(Widget):
    """A widget that edits one value. Emits ``changed(value)``."""

    def __init__(self, value=None, *, default=None, on_change=None, **kw):
        super().__init__(**kw)
        self._value = self.coerce(value) if value is not None else value
        self.default = self._value if default is None else self.coerce(default)
        self.changed = Signal()
        if on_change is not None:
            self.changed.connect(on_change)
        self._edit_start = None
        self.undoable = True

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self.set_value(v, emit=False)

    def coerce(self, v):
        return v

    def set_value(self, v, *, undo=False, emit=True):
        v = self.coerce(v)
        if v == self._value:
            return False
        old = self._value
        self._value = v
        self.invalidate()
        if undo and self.undoable and self._edit_start is None:
            ui = self.ui
            if ui is not None:
                ui.undo.push_value(self, old, v)
        if emit:
            self.changed.emit(v)
        return True

    # grouped edits (drags, typing, pickers) become a single undo step
    def begin_edit(self):
        self._edit_start = self._value

    def end_edit(self):
        start, self._edit_start = self._edit_start, None
        if start is not None and start != self._value and self.undoable:
            ui = self.ui
            if ui is not None:
                ui.undo.push_value(self, start, self._value)

    def cancel_edit(self):
        start, self._edit_start = self._edit_start, None
        if start is not None:
            self.set_value(start)

    def reset(self):
        if self.default is not None:
            self.set_value(self.default, undo=True)

    def step(self, n, undo=False):
        return False

    def copy_text(self):
        return str(self._value)

    def paste_text(self, s):
        pass

    def activate(self, ui):
        pass

    def context_menu(self, ui, ev):
        items = [("Copy Value", lambda: ui.clipboard.put(self.copy_text())),
                 ("Paste Value", lambda: self.paste_text(ui.clipboard.get())),
                 None,
                 ("Reset to Default", self.reset)]
        open_context_menu(ui, items, at=(ev.x, ev.y))


# =========================================================================== booleans
class _BoolWidget(ValueWidget):
    hover_redraw = True

    def __init__(self, text="", value=False, **kw):
        super().__init__(bool(value), **kw)
        self.text = text

    def coerce(self, v):
        return bool(v)

    def on_mouse_down(self, ui, ev):
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        new = not self._value
        self.set_value(new, undo=True)
        ui.begin_drag_toggle(_BoolWidget, new)
        return True

    def drag_toggle_to(self, ui, value):
        self.set_value(value, undo=True)

    def step(self, n, undo=False):
        return self.set_value(n > 0, undo=undo)

    def paste_text(self, s):
        self.set_value(s.strip().lower() in ("1", "true", "yes", "on"), undo=True)

    def activate(self, ui):
        self.set_value(not self._value, undo=True)


class Checkbox(_BoolWidget):
    def preferred_size(self, ui):
        s = ui.theme.scale
        return (ui.text_width(self.text) + 26 * s, ui.theme.h)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        bs = round(14 * s)
        bx = self.x + 2 * s
        by = round(self.y + (self.h - bs) / 2)
        if self._value:
            base = shade(t.accent, 0.1) if self.hovered else t.accent
        else:
            base = shade(t.check_bg, 0.08) if self.hovered else t.check_bg
        if not self.enabled:
            base = with_alpha(base, 0.5)
        dl.box(bx, by, bs, bs, shade(base, t.emboss), shade(base, -t.emboss), t.outline, 3 * s)
        if self._value:
            dl.check(bx + 2 * s, by + 2 * s, bs - 4 * s, bs - 4 * s, t.text_on_accent, 1.8 * s)
        if self.text:
            ui.text(dl, self.text, bx + bs + 6 * s, self.y, self.w - bs - 8 * s, self.h, _text_color(ui, self))


class Toggle(_BoolWidget):
    """Button that stays pressed while on (optionally with an icon)."""

    def __init__(self, text="", value=False, *, icon=None, **kw):
        super().__init__(text, value, **kw)
        self.icon = icon

    def preferred_size(self, ui):
        s = ui.theme.scale
        if self.icon and not self.text:
            return (ui.theme.h + 8 * s, ui.theme.h)
        return (ui.text_width(self.text) + 24 * s + (ui.icon_size() + 6 * s if self.icon else 0), ui.theme.h)

    def draw(self, dl, ui):
        t = ui.theme
        if self._value:
            base = shade(t.accent, 0.1) if self.hovered else t.accent
        else:
            base = t.button_hover if self.hovered else t.button
        _box(dl, ui, self, base, pressed=self._value)
        draw_icon_label(dl, ui, (self.x, self.y, self.w, self.h), self.icon, self.text,
                        _text_color(ui, self, self._value))


# =========================================================================== line editing
class LineEdit:
    """Text buffer + caret + selection used by text and number fields."""

    def __init__(self, text=""):
        self.text = text
        self.cursor = len(text)
        self.anchor = 0
        self.scroll = 0.0

    @property
    def selection(self):
        return (min(self.anchor, self.cursor), max(self.anchor, self.cursor))

    @property
    def has_selection(self):
        return self.anchor != self.cursor

    def select_all(self):
        self.anchor, self.cursor = 0, len(self.text)

    def selected_text(self):
        a, b = self.selection
        return self.text[a:b]

    def _delete_selection(self):
        a, b = self.selection
        self.text = self.text[:a] + self.text[b:]
        self.cursor = self.anchor = a

    def insert(self, s):
        s = s.replace("\r", "").replace("\n", " ")
        if self.has_selection:
            self._delete_selection()
        c = self.cursor
        self.text = self.text[:c] + s + self.text[c:]
        self.cursor = self.anchor = c + len(s)

    def _word_left(self, i):
        t = self.text
        while i > 0 and not t[i - 1].isalnum():
            i -= 1
        while i > 0 and t[i - 1].isalnum():
            i -= 1
        return i

    def _word_right(self, i):
        t, n = self.text, len(self.text)
        while i < n and not t[i].isalnum():
            i += 1
        while i < n and t[i].isalnum():
            i += 1
        return i

    def select_word_at(self, i):
        t = self.text
        a = b = max(0, min(i, len(t)))
        while a > 0 and t[a - 1].isalnum():
            a -= 1
        while b < len(t) and t[b].isalnum():
            b += 1
        self.anchor, self.cursor = a, b

    def _move(self, pos, select):
        self.cursor = max(0, min(len(self.text), pos))
        if not select:
            self.anchor = self.cursor

    def key(self, e, ui):
        """Returns 'enter' / 'escape' / 'tab' / 'backtab' / True (handled)."""
        k, mods = e.key, e.mod
        ctrl, shift = bool(mods & CTRL), bool(mods & SHIFT)
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return "enter"
        if k == pygame.K_ESCAPE:
            return "escape"
        if k == pygame.K_TAB:
            return "backtab" if shift else "tab"
        if k == pygame.K_LEFT:
            if self.has_selection and not shift:
                self._move(self.selection[0], False)
            else:
                self._move(self._word_left(self.cursor) if ctrl else self.cursor - 1, shift)
        elif k == pygame.K_RIGHT:
            if self.has_selection and not shift:
                self._move(self.selection[1], False)
            else:
                self._move(self._word_right(self.cursor) if ctrl else self.cursor + 1, shift)
        elif k in (pygame.K_HOME, pygame.K_UP):
            self._move(0, shift)
        elif k in (pygame.K_END, pygame.K_DOWN):
            self._move(len(self.text), shift)
        elif k == pygame.K_BACKSPACE:
            if self.has_selection:
                self._delete_selection()
            elif self.cursor > 0:
                a = self._word_left(self.cursor) if ctrl else self.cursor - 1
                self.text = self.text[:a] + self.text[self.cursor:]
                self.cursor = self.anchor = a
        elif k == pygame.K_DELETE:
            if self.has_selection:
                self._delete_selection()
            elif self.cursor < len(self.text):
                b = self._word_right(self.cursor) if ctrl else self.cursor + 1
                self.text = self.text[:self.cursor] + self.text[b:]
        elif ctrl and k == pygame.K_a:
            self.select_all()
        elif ctrl and k in (pygame.K_c, pygame.K_x):
            if self.has_selection:
                ui.clipboard.put(self.selected_text())
                if k == pygame.K_x:
                    self._delete_selection()
        elif ctrl and k == pygame.K_v:
            self.insert(ui.clipboard.get())
        return True

    def index_at(self, font, x_rel):
        pw = font.prefix_widths(self.text)
        x = x_rel + self.scroll
        best, best_d = 0, abs(x)
        for i, px in enumerate(pw):
            d = abs(px - x)
            if d < best_d:
                best, best_d = i, d
        return best

    def draw(self, dl, ui, x, y, w, h, color):
        t = ui.theme
        f = ui.font()
        pw = f.prefix_widths(self.text)
        cx = pw[self.cursor]
        total = pw[-1]
        inner = w - 2
        if total <= inner:
            self.scroll = 0.0
        else:
            if cx - self.scroll > inner:
                self.scroll = cx - inner
            if cx - self.scroll < 0:
                self.scroll = cx
            self.scroll = max(0.0, min(self.scroll, total - inner))
        dl.push_clip(x - 1, y, w + 2, h)
        if self.has_selection:
            a, b = self.selection
            dl.box(x + pw[a] - self.scroll, y + 3, pw[b] - pw[a], h - 6, t.selection, radius=2)
        ui.text(dl, self.text, x - self.scroll, y, None, h, color, elide=False)
        dl.box(round(x + cx - self.scroll), y + 4, max(1, round(t.scale)), h - 8, t.caret)
        dl.pop_clip()


class _TextEditing:
    """Mixin: in-place text editing for ValueWidgets (needs edit_text/commit_text)."""

    focusable = True
    wants_text_input = True
    editor = None
    submit_on_enter = True     # Enter also presses the default button of an enclosing dialog

    def _text_rect(self, ui):
        s = ui.theme.scale
        return (self.x + 6 * s, self.y, self.w - 12 * s, self.h)

    @property
    def editing(self):
        return self.editor is not None

    def begin_text_edit(self, ui, select_all=True):
        if self.editor is None:
            self.begin_edit()
            self.editor = LineEdit(self.edit_text())
            if select_all:
                self.editor.select_all()
        ui.set_focus(self)
        self.invalidate()

    def _finish(self, ui, commit):
        if self.editor is None:
            return
        text = self.editor.text
        self.editor = None
        if commit:
            self.commit_text(text)
            self.end_edit()
        else:
            self.cancel_edit()
        self.invalidate(layout=True)

    def on_focus(self, ui, focused):
        if not focused:
            self._finish(ui, True)

    def on_key(self, ui, e):
        if self.editor is None:
            return False
        r = self.editor.key(e, ui)
        if r == "enter":
            self._finish(ui, True)
            ui.set_focus(None)
            blocking = ui.blocking_overlays
            if (self.submit_on_enter and blocking and self.is_descendant_of(blocking[-1])
                    and getattr(blocking[-1], "default_button", None) is not None):
                return False
        elif r == "escape":
            self._finish(ui, False)
            ui.set_focus(None)
        elif r in ("tab", "backtab"):
            self._finish(ui, True)
            ui.focus_next(self, backward=(r == "backtab"))
        else:
            self.on_live_edit(ui)
        self.invalidate()
        return True

    def on_text(self, ui, text):
        if self.editor is None:
            return False
        self.editor.insert(text)
        self.on_live_edit(ui)
        self.invalidate()
        return True

    def on_live_edit(self, ui):
        pass

    def _edit_press(self, ui, ev):
        x, _, _, _ = self._text_rect(ui)
        f = ui.font()
        ed = self.editor
        i = ed.index_at(f, ev.x - x)
        if ev.clicks >= 3:
            ed.select_all()
        elif ev.clicks == 2:
            ed.select_word_at(i)
        else:
            ed.cursor = i
            if not ev.shift:
                ed.anchor = i
        self._selecting = ev.clicks == 1
        self.invalidate()
        return True

    def _edit_drag(self, ui, ev):
        if getattr(self, "_selecting", False) and self.editor is not None:
            x, _, _, _ = self._text_rect(ui)
            self.editor.cursor = self.editor.index_at(ui.font(), ev.x - x)
            self.invalidate()

    def _draw_editor(self, dl, ui):
        t = ui.theme
        dl.box(self.x, self.y, self.w, self.h, t.field_edit, border=t.accent, radius=self.radii(ui))
        x, y, w, h = self._text_rect(ui)
        self.editor.draw(dl, ui, x, y, w, h, t.text)


# =========================================================================== numbers
class NumberField(_TextEditing, ValueWidget):
    """Scrubbable numeric field. ``slider=True`` draws a fill bar for the range."""

    hover_redraw = True

    def __init__(self, label="", value=0.0, *, min=None, max=None, soft_min=None, soft_max=None,
                 step=None, precision=None, integer=None, slider=False, unit="", **kw):
        if integer is None:
            integer = isinstance(value, int) and not isinstance(value, bool)
        self.integer = integer
        self.min, self.max = min, max
        self.label = label
        self.unit = unit
        self.slider = slider
        self.step_size = step if step is not None else (1 if integer else 0.1)
        self.precision = precision if precision is not None else (0 if integer else 3)
        smin = soft_min if soft_min is not None else min
        smax = soft_max if soft_max is not None else max
        if slider and (smin is None or smax is None):
            smin = 0.0 if smin is None else smin
            smax = 1.0 if smax is None else smax
        self.soft_min, self.soft_max = smin, smax
        self._press = None
        self._dragging = False
        self._drag_val = 0.0
        self._arrow_zone = 0
        super().__init__(value, **kw)
        self.cursor = SIZEWE

    # -- value -------------------------------------------------------------------
    def coerce(self, v):
        v = float(v)
        if self.min is not None:
            v = max(self.min, v)
        if self.max is not None:
            v = min(self.max, v)
        if self.integer:
            return int(round(v))
        return v

    def format_value(self):
        v = self._value
        if self.integer:
            s = str(v)
        else:
            s = f"{v:.{self.precision}f}"
            if s.startswith("-") and float(s) == 0:
                s = s[1:]
        return s + self.unit

    def edit_text(self):
        v = self._value
        if self.integer:
            return str(v)
        s = f"{v:.{max(self.precision, 6)}f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    def commit_text(self, text):
        try:
            self.set_value(evaluate(text))
        except (ExprError, ValueError, OverflowError):
            pass

    def step(self, n, undo=False):
        return self.set_value(self._value + n * self.step_size, undo=undo)

    def copy_text(self):
        return self.edit_text()

    def paste_text(self, s):
        try:
            self.set_value(evaluate(s), undo=True)
        except (ExprError, ValueError, OverflowError):
            pass

    def activate(self, ui):
        self.begin_text_edit(ui)

    # -- layout / paint --------------------------------------------------------------
    def preferred_size(self, ui):
        s = ui.theme.scale
        lw = ui.text_width(self.label + ":") + 8 * s if self.label else 0
        return (lw + ui.text_width(self.format_value()) + 34 * s, ui.theme.h)

    def _arrow_w(self, ui):
        return 0 if self.slider else 14 * ui.theme.scale

    def draw(self, dl, ui):
        if self.editing:
            self._draw_editor(dl, ui)
            return
        t = ui.theme
        s = t.scale
        hot = self.hovered or self._press is not None
        base = t.field_hover if hot else t.field
        _box(dl, ui, self, base)
        player = getattr(self, "anim_player", None)
        if player is not None:
            from .animation import ANIM_TINT
            st = player.anim_state(self)
            if st:
                dl.box(self.x, self.y, self.w, self.h, ANIM_TINT[st], radius=self.radii(ui))
        if self.slider and self.soft_max != self.soft_min:
            frac = (float(self._value) - self.soft_min) / (self.soft_max - self.soft_min)
            frac = max(0.0, min(1.0, frac))
            fw = round(self.w * frac)
            if fw > 0:
                dl.push_clip(self.x, self.y, fw, self.h)
                fill = shade(t.slider_fill, 0.08) if hot else t.slider_fill
                if not self.enabled:
                    fill = with_alpha(fill, 0.5)
                dl.box(self.x, self.y, self.w, self.h, shade(fill, t.emboss), shade(fill, -t.emboss),
                       t.outline, self.radii(ui))
                dl.pop_clip()
        aw = self._arrow_w(ui)
        col = _text_color(ui, self)
        if aw and hot and self.enabled:
            a = round(8 * s)
            ay = round(self.y + (self.h - a) / 2)
            lo = shade(t.text_dim, 0.4) if self._arrow_zone < 0 else t.text_dim
            hi = shade(t.text_dim, 0.4) if self._arrow_zone > 0 else t.text_dim
            dl.triangle(self.x + 4 * s, ay, a, a, lo, 2)
            dl.triangle(self.x + self.w - 4 * s - a, ay, a, a, hi, 0)
        pad = aw + 6 * s
        inner = self.w - 2 * pad
        value = self.format_value()
        if self.label:
            vw = ui.text_width(value)
            ui.text(dl, self.label + ":", self.x + pad, self.y, inner - vw - 6 * s, self.h, col)
            ui.text(dl, value, self.x + pad, self.y, inner, self.h, col, "right")
        else:
            ui.text(dl, value, self.x + pad, self.y, inner, self.h, col, "center")

    # -- mouse -------------------------------------------------------------------------
    def _zone(self, ui, x):
        aw = self._arrow_w(ui)
        if not aw:
            return 0
        if x < self.x + aw:
            return -1
        if x >= self.x + self.w - aw:
            return 1
        return 0

    def on_mouse_down(self, ui, ev):
        if self.editing:
            return self._edit_press(ui, ev)
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        self._press = (ev.x, ev.y)
        self._dragging = False
        self._drag_val = float(self._value)
        self._drag_px = 0.0
        self.begin_edit()
        return True

    def on_mouse_move(self, ui, ev):
        if self.editing:
            self._edit_drag(ui, ev)
            return
        if self._press is None:
            z = self._zone(ui, ev.x)
            if z != self._arrow_zone:
                self._arrow_zone = z
                self.invalidate()
            return
        self._drag_px += ev.dx
        if not self._dragging:
            if abs(self._drag_px) < 3 * ui.theme.scale:
                return
            self._dragging = True
            self._arrow_zone = 0
            try:  # hidden + grabbed cursor => SDL relative mode => unlimited drag distance
                pygame.mouse.set_visible(False)
                pygame.event.set_grab(True)
            except Exception:
                pass
        if self.slider and self.soft_max != self.soft_min:
            per_px = (self.soft_max - self.soft_min) / max(1.0, self.w)
        else:
            per_px = self.step_size / (5.0 if self.integer else 2.0)
        if ev.shift:
            per_px *= 0.1
        self._drag_val += ev.dx * per_px
        if self.min is not None:
            self._drag_val = max(self.min, self._drag_val)
        if self.max is not None:
            self._drag_val = min(self.max, self._drag_val)
        v = self._drag_val
        if ev.ctrl:
            snap = self.step_size * (5 if self.integer else 10)
            if self.slider and self.soft_max != self.soft_min:
                snap = (self.soft_max - self.soft_min) / 20.0
            v = round(v / snap) * snap
        self.set_value(v)

    def on_mouse_up(self, ui, ev):
        if self.editing:
            self._selecting = False
            return
        if self._press is None or ev.button != 1:
            return
        press, self._press = self._press, None
        if self._dragging:
            self._dragging = False
            try:
                pygame.event.set_grab(False)
                pygame.mouse.set_visible(True)
                pygame.mouse.set_pos(press)
            except Exception:
                pass
            self.end_edit()
            self.invalidate()
            return
        self._edit_start = None
        z = self._zone(ui, press[0])
        if z:
            self.step(z, undo=True)
        else:
            self.begin_text_edit(ui)

    def on_hover(self, ui, hovered):
        if not hovered:
            self._arrow_zone = 0
        self.invalidate()


class VectorField(Column):
    """N joined number fields (e.g. Location X/Y/Z) with an optional heading."""

    def __init__(self, label="", values=(0.0, 0.0, 0.0), *, axes="XYZW", on_change=None, **field_kw):
        kw = {k: field_kw.pop(k) for k in ("width", "height", "expand", "tooltip", "enabled", "visible", "name")
              if k in field_kw}
        super().__init__(spacing=0, **kw)
        self.label = label
        self.changed = Signal()
        if on_change is not None:
            self.changed.connect(on_change)
        if label:
            self.add(Label(label))
        group = Column(align=True)
        self.add(group)
        self.fields = []
        for i, v in enumerate(values):
            f = NumberField(axes[i] if i < len(axes) else str(i), v, **field_kw)
            f.changed.connect(lambda _v: self.changed.emit(self.value))
            group.add(f)
            self.fields.append(f)

    @property
    def value(self):
        return tuple(f.value for f in self.fields)

    @value.setter
    def value(self, vals):
        for f, v in zip(self.fields, vals):
            f.value = v


# =========================================================================== text
class TextField(_TextEditing, ValueWidget):
    hover_redraw = True

    def __init__(self, value="", *, label="", placeholder="", **kw):
        self.label = label
        self.placeholder = placeholder
        super().__init__(str(value), **kw)
        self.edited = Signal()   # every keystroke (text so far)
        self.cursor = IBEAM

    def coerce(self, v):
        return "" if v is None else str(v)

    def edit_text(self):
        return self._value

    def commit_text(self, text):
        self.set_value(text)

    def on_live_edit(self, ui):
        if self.editor is not None:
            self.edited.emit(self.editor.text)

    def paste_text(self, s):
        self.set_value(s, undo=True)

    def activate(self, ui):
        self.begin_text_edit(ui)

    def _label_w(self, ui):
        return ui.label_width(self, self.label)

    def _text_rect(self, ui):
        s = ui.theme.scale
        lw = self._label_w(ui)
        return (self.x + lw + 6 * s, self.y, self.w - lw - 12 * s, self.h)

    def preferred_size(self, ui):
        return (ui.label_width(None, self.label) + 120 * ui.theme.scale, ui.theme.h)

    def draw(self, dl, ui):
        t = ui.theme
        lw = self._label_w(ui)
        if lw:
            ui.text(dl, self.label, self.x + 2, self.y, lw - 4, self.h, _text_color(ui, self))
        fx, fw = self.x + lw, self.w - lw
        x, y, w, h = self._text_rect(ui)
        if self.editing:
            dl.box(fx, self.y, fw, self.h, t.field_edit, border=t.accent, radius=self.radii(ui))
            self.editor.draw(dl, ui, x, y, w, h, t.text)
            return
        base = t.field_hover if self.hovered else t.field
        if not self.enabled:
            base = with_alpha(base, 0.5)
        dl.box(fx, self.y, fw, self.h, shade(base, -t.emboss), shade(base, t.emboss), t.outline, self.radii(ui))
        if self._value:
            ui.text(dl, self._value, x, y, w, h, _text_color(ui, self))
        elif self.placeholder:
            ui.text(dl, self.placeholder, x, y, w, h, with_alpha(t.text_dim, 0.7))

    def on_mouse_down(self, ui, ev):
        if ev.button == 3 and not self.editing:
            self.context_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        if not self.editing:
            self.begin_text_edit(ui, select_all=False)
        return self._edit_press(ui, ev)

    def on_mouse_move(self, ui, ev):
        self._edit_drag(ui, ev)

    def on_mouse_up(self, ui, ev):
        self._selecting = False


# =========================================================================== dropdowns
class Dropdown(ValueWidget):
    """Enum picker. ``options``: list of values, or (value, label) pairs."""

    hover_redraw = True

    def __init__(self, options, value=None, *, label="", searchable=True, **kw):
        self.options = [(o[0], str(o[1])) if isinstance(o, tuple) else (o, str(o)) for o in options]
        self.label = label
        self.searchable = searchable
        self._menu = None
        if value is None and self.options:
            value = self.options[0][0]
        super().__init__(value, **kw)

    def label_of(self, v):
        for val, lab in self.options:
            if val == v:
                return lab
        return str(v)

    def _label_w(self, ui):
        return ui.label_width(self, self.label)

    def preferred_size(self, ui):
        s = ui.theme.scale
        w = max([ui.text_width(lab) for _, lab in self.options] + [0])
        return (ui.label_width(None, self.label) + w + 34 * s, ui.theme.h)

    def _button_rect(self, ui):
        lw = self._label_w(ui)
        return (self.x + lw, self.y, self.w - lw, self.h)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        lw = self._label_w(ui)
        if lw:
            ui.text(dl, self.label, self.x + 2, self.y, lw - 4, self.h, _text_color(ui, self))
        bx, by, bw, bh = self._button_rect(ui)
        open_ = self._menu is not None
        base = t.button_hover if (self.hovered or open_) else t.button
        e = t.emboss
        top, bot = shade(base, e), shade(base, -e)
        if not self.enabled:
            top, bot = with_alpha(top, 0.5), with_alpha(bot, 0.5)
        dl.box(bx, by, bw, bh, top, bot, t.outline, self.radii(ui))
        a = round(7 * s)
        dl.triangle(bx + bw - a - 7 * s, round(by + (bh - a) / 2), a, a, t.text_dim, 1)
        ui.text(dl, self.label_of(self._value), bx + 8 * s, by, bw - a - 20 * s, bh, _text_color(ui, self))

    def open_menu(self, ui):
        bx, by, bw, bh = self._button_rect(ui)

        def pick(v):
            self.set_value(v, undo=True)

        m = Menu([(lab, val) for val, lab in self.options], pick, current=self._value,
                 searchable=self.searchable, min_width=bw)
        orig_close = m.on_close

        def on_close(ui_):
            orig_close(ui_)
            self._menu = None
            self.invalidate()

        m.on_close = on_close
        self._menu = m
        ui.ensure_text_input()
        ui.open_popup(m, anchor=(bx, by, bw, bh))

    def on_mouse_down(self, ui, ev):
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        self.open_menu(ui)
        return True

    def on_mouse_move(self, ui, ev):
        if self._menu is not None:
            self._menu.track(ui, ev.x, ev.y)

    def on_mouse_up(self, ui, ev):
        if self._menu is not None and ev.button == 1:
            self._menu.release_at(ui, ev.x, ev.y)   # press-drag-release picks

    def step(self, n, undo=False):
        vals = [v for v, _ in self.options]
        if self._value not in vals:
            return False
        i = (vals.index(self._value) + n) % len(vals)
        return self.set_value(vals[i], undo=undo)

    def copy_text(self):
        return self.label_of(self._value)

    def paste_text(self, s):
        for val, lab in self.options:
            if lab == s or str(val) == s:
                self.set_value(val, undo=True)

    def activate(self, ui):
        self.open_menu(ui)


class EnumRow(ValueWidget):
    """All options side by side as joined buttons (an "expanded" enum)."""

    hover_redraw = True

    def __init__(self, options, value=None, **kw):
        self.options = [(o[0], str(o[1])) if isinstance(o, tuple) else (o, str(o)) for o in options]
        if value is None and self.options:
            value = self.options[0][0]
        super().__init__(value, **kw)
        self._hover_i = None

    def preferred_size(self, ui):
        s = ui.theme.scale
        return (sum(ui.text_width(l) + 18 * s for _, l in self.options), ui.theme.h)

    def _seg(self, i):
        n = len(self.options)
        x0 = self.x + round(self.w * i / n)
        x1 = self.x + round(self.w * (i + 1) / n)
        return x0, x1

    def _index_at(self, px):
        n = len(self.options)
        if not n or self.w <= 0:
            return None
        return max(0, min(n - 1, int((px - self.x) * n / self.w)))

    def draw(self, dl, ui):
        t = ui.theme
        r = t.radius * t.scale
        n = len(self.options)
        c = self.corners
        for i, (val, lab) in enumerate(self.options):
            x0, x1 = self._seg(i)
            on = val == self._value
            base = t.accent if on else (t.button_hover if (self.hovered and i == self._hover_i) else t.button)
            radii = (r if (i == 0 and c[0]) else 0, r if (i == n - 1 and c[1]) else 0,
                     r if (i == n - 1 and c[2]) else 0, r if (i == 0 and c[3]) else 0)
            e = t.emboss
            top, bot = (shade(base, -e), shade(base, e)) if on else (shade(base, e), shade(base, -e))
            dl.box(x0, self.y, x1 - x0 + (1 if i < n - 1 else 0), self.h, top, bot, t.outline, radii)
            ui.text(dl, lab, x0 + 4, self.y, x1 - x0 - 8, self.h, _text_color(ui, self, on), "center")

    def on_mouse_move(self, ui, ev):
        i = self._index_at(ev.x)
        if i != self._hover_i:
            self._hover_i = i
            self.invalidate()
        if ui.active is self and i is not None:
            self.set_value(self.options[i][0])

    def on_mouse_down(self, ui, ev):
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        self.begin_edit()
        i = self._index_at(ev.x)
        if i is not None:
            self.set_value(self.options[i][0])
        return True

    def on_mouse_up(self, ui, ev):
        self.end_edit()

    def step(self, n, undo=False):
        vals = [v for v, _ in self.options]
        if self._value not in vals:
            return False
        return self.set_value(vals[(vals.index(self._value) + n) % len(vals)], undo=undo)


# =========================================================================== progress
class ProgressBar(Widget):
    def __init__(self, value=0.0, *, text=None, **kw):
        super().__init__(**kw)
        self._value = float(value)
        self.text = text  # None => percentage; "" => no text; str with {value:.0%} allowed

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        v = max(0.0, min(1.0, float(v)))
        if v != self._value:
            self._value = v
            self.invalidate()

    def preferred_size(self, ui):
        return (120 * ui.theme.scale, ui.theme.h)

    def draw(self, dl, ui):
        t = ui.theme
        dl.box(self.x, self.y, self.w, self.h, shade(t.field, -t.emboss), shade(t.field, t.emboss),
               t.outline, self.radii(ui))
        fw = round(self.w * self._value)
        if fw > 0:
            dl.push_clip(self.x, self.y, fw, self.h)
            dl.box(self.x, self.y, self.w, self.h, shade(t.accent, 0.1), shade(t.accent, -0.1), t.outline,
                   self.radii(ui))
            dl.pop_clip()
        text = f"{self._value:.0%}" if self.text is None else self.text.format(value=self._value)
        if text:
            ui.text(dl, text, self.x, self.y, self.w, self.h, t.text, "center")


# =========================================================================== panels
class Panel(Column):
    """Collapsible section. Click the header to fold; drag across headers to
    fold several; Ctrl+click folds every other panel. ``draggable=True`` lets
    the panel be moved when its parent is a Canvas."""

    hover_redraw = True

    def __init__(self, title="", *children, collapsed=False, padding=6, draggable=False, icon=None, **kw):
        super().__init__(*children, padding=padding, **kw)
        self.title = title
        self.icon = icon
        self._collapsed = collapsed
        self.draggable = draggable
        self._drag = None
        self.toggled = Signal()

    @property
    def collapsed(self):
        return self._collapsed

    @collapsed.setter
    def collapsed(self, v):
        v = bool(v)
        if v != self._collapsed:
            self._collapsed = v
            self.invalidate(layout=True)
            self.toggled.emit(v)

    def children_shown(self):
        return not self._collapsed

    def header_h(self, ui):
        return ui.theme.panel_header * ui.theme.scale

    def preferred_size(self, ui):
        self._hh = hh = self.header_h(ui)
        tw = ui.text_width(self.title, bold=True) + (64 if self.icon else 40) * ui.theme.scale
        if self._collapsed:
            return (tw, hh)
        w, h = super().preferred_size(ui)
        return (max(w, tw), h + hh)

    def content_box(self):
        hh = getattr(self, "_hh", 0)
        return self.x, self.y + hh, self.w, self.h - hh

    def arrange(self, x, y, w, h):
        if self._collapsed:
            Widget.arrange(self, x, y, w, h)
        else:
            super().arrange(x, y, w, h)

    def in_header(self, py):
        return self.y <= py < self.y + getattr(self, "_hh", 0)

    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        if self.in_header(py):
            return self
        return super().widget_at(px, py)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = t.radius * s
        hh = self._hh
        if self.draggable:
            dl.shadow(self.x, self.y, self.w, self.h, t.shadow, r, 8 * s)
        dl.box(self.x, self.y, self.w, self.h, t.panel, border=t.outline, radius=r)
        hot = self.hovered and self.in_header(ui.mouse[1])
        hb = shade(t.panel_header_bg, 0.05) if hot else t.panel_header_bg
        rad = (r, r, r, r) if self._collapsed else (r, r, 0, 0)
        dl.box(self.x, self.y, self.w, hh, shade(hb, t.emboss), shade(hb, -t.emboss), t.outline, rad)
        a = round(8 * s)
        dl.triangle(self.x + 8 * s, round(self.y + (hh - a) / 2), a, a, t.text_dim, 0 if self._collapsed else 1)
        tx = self.x + 22 * s
        if self.icon:
            tx += ui.icon(dl, self.icon, tx, self.y + (hh - ui.icon_size()) / 2, None, t.text_dim) + 6 * s
        ui.text(dl, self.title, tx, self.y, self.x + self.w - 8 * s - tx, hh, t.text, bold=True)
        if not self._collapsed:
            self.draw_children(dl, ui)

    def on_mouse_down(self, ui, ev):
        if ev.button != 1 or not self.in_header(ev.y):
            return False
        if self.draggable and isinstance(self.parent, Container) and hasattr(self.parent, "place"):
            self._drag = (ev.x, ev.y, getattr(self, "pos", (0, 0)), False)
            return True
        if ev.ctrl and self.parent is not None:
            for sib in self.parent.children:
                if isinstance(sib, Panel):
                    sib.collapsed = sib is not self
            return True
        new = not self._collapsed
        self.collapsed = new
        ui.begin_drag_toggle(Panel, new)
        return True

    def drag_toggle_to(self, ui, value):
        if self.in_header(ui.mouse[1]):
            self.collapsed = value

    def on_mouse_move(self, ui, ev):
        if self._drag is None:
            hot = self.in_header(ev.y)
            if hot != getattr(self, "_hot", False):
                self._hot = hot
                self.invalidate()
            return
        x0, y0, (px, py), moved = self._drag
        dx, dy = ev.x - x0, ev.y - y0
        if not moved and abs(dx) + abs(dy) < 4:
            return
        self._drag = (x0, y0, (px, py), True)
        sw, sh = ui.size
        nx = max(0, min(sw - 40, px + dx))
        ny = max(0, min(sh - self.header_h(ui), py + dy))
        self.pos = (nx, ny)
        self.invalidate(layout=True)

    def on_mouse_up(self, ui, ev):
        if self._drag is not None:
            moved = self._drag[3]
            self._drag = None
            if not moved:
                self.collapsed = not self._collapsed
            elif self.parent is not None and self in self.parent.children:  # bring to front
                self.parent.children.remove(self)
                self.parent.children.append(self)
                self.invalidate()


# =========================================================================== tabs
class Tabs(Container):
    """Tabbed pages. ``style="editor"`` (boxed tabs) or ``"underline"``.

        tabs = Tabs()
        with tabs.tab("Scene", icon="cube"):
            Button("Hello")
    """

    hover_redraw = True

    def __init__(self, *, style="editor", **kw):
        super().__init__(**kw)
        self.style = style
        self.titles = []
        self.tab_icons = []
        self.index = 0
        self.changed = Signal()

    def add_tab(self, title, widget=None, icon=None):
        page = widget if widget is not None else Column(padding=8, spacing=6, parent=self)
        self.add(page)
        self.titles.append(title)
        self.tab_icons.append(icon)
        return page

    def tab(self, title, icon=None):
        """Create a page (a Column) and return it -- handy as a ``with`` block."""
        return self.add_tab(title, None, icon)

    @property
    def current(self):
        return self.children[self.index] if 0 <= self.index < len(self.children) else None

    def select(self, i):
        i = max(0, min(len(self.children) - 1, i))
        if i != self.index:
            self.index = i
            self.invalidate(layout=True)
            ui = self.ui
            if ui is not None:
                ui.close_popups()
            self.changed.emit(i)

    def step(self, n, undo=False):
        self.select((self.index + n) % max(1, len(self.children)))
        return True

    def visible_children(self):
        c = self.current
        return [c] if c is not None and c._visible else []

    def header_h(self, ui):
        return ui.theme.h + 10 * ui.theme.scale

    def _tab_rects(self, ui):
        s = ui.theme.scale
        x = self.x + 6 * s
        out = []
        for title, icon in zip(self.titles, self.tab_icons):
            w = ui.text_width(title) + 28 * s + (ui.icon_size() + 6 * s if icon else 0)
            out.append((x, self.y + 4 * s, w, self.header_h(ui) - 4 * s))
            x += w + 2 * s
        return out

    def preferred_size(self, ui):
        hh = self.header_h(ui)
        tw = sum(r[2] for r in self._tab_rects(ui)) + 12 * ui.theme.scale
        c = self.current
        cw, ch = c.measure(ui) if c is not None else (0, 0)
        return (max(tw, cw), hh + ch)

    def arrange(self, x, y, w, h):
        Widget.arrange(self, x, y, w, h)
        c = self.current
        if c is not None:
            hh = self.header_h(self.ui)
            c.arrange(x, round(y + hh), w, round(h - hh))

    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        c = self.current
        if c is not None and py >= self.y + self.header_h(self.ui):
            r = c.widget_at(px, py)
            if r is not None:
                return r
        return self

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        hh = self.header_h(ui)
        dl.box(self.x, self.y, self.w, hh, t.panel_header_bg)
        dl.box(self.x, self.y + hh - 1, self.w, 1, t.outline)
        mx, my = ui.mouse
        for i, (r, title, icon) in enumerate(zip(self._tab_rects(ui), self.titles, self.tab_icons)):
            x, y, w, h = r
            on = i == self.index
            hot = self.hovered and x <= mx < x + w and y <= my < y + h
            if self.style == "underline":
                col = t.accent if on else (t.text if hot else t.text_dim)
                if on:
                    dl.box(x + 4 * s, y + h - 3 * s, w - 8 * s, 3 * s, t.accent, radius=(1.5 * s, 1.5 * s, 0, 0))
            else:
                if on:
                    dl.box(x, y, w, h + 1, t.panel, border=t.outline, radius=(t.radius * s, t.radius * s, 0, 0))
                    dl.box(x + 1, y + h - 1, w - 2, 2, t.panel)
                elif hot:
                    dl.box(x, y + 1, w, h - 2, shade(t.panel_header_bg, 0.06), radius=t.radius * s)
                col = t.text if (on or hot) else t.text_dim
            draw_icon_label(dl, ui, r, icon, title, col)
        if self.style == "editor":
            dl.box(self.x, self.y + hh, self.w, self.h - hh, t.panel)
        c = self.current
        if c is not None and c._visible:
            c.paint(dl, ui)

    def on_mouse_down(self, ui, ev):
        if ev.button != 1 or ev.y >= self.y + self.header_h(ui):
            return False
        for i, (x, y, w, h) in enumerate(self._tab_rects(ui)):
            if x <= ev.x < x + w:
                self.select(i)
                return True
        return False

    def on_mouse_move(self, ui, ev):
        if ev.y < self.y + self.header_h(ui):
            self.invalidate()


# =========================================================================== colour
def _hsv_of(rgba):
    return colorsys.rgb_to_hsv(*rgba[:3])


class _SVPlane(Widget):
    def __init__(self, picker, **kw):
        super().__init__(**kw)
        self.picker = picker
        self.cursor = pygame.SYSTEM_CURSOR_CROSSHAIR

    def preferred_size(self, ui):
        return (180 * ui.theme.scale, 150 * ui.theme.scale)

    def draw(self, dl, ui):
        t = ui.theme
        h, s_, v = self.picker.hsv
        dl.sv_square(self.x, self.y, self.w, self.h, h, radius=t.radius * t.scale, border=t.outline)
        cx, cy = self.x + s_ * self.w, self.y + (1 - v) * self.h
        r = 6 * t.scale
        dl.push_clip(self.x - r, self.y - r, self.w + 2 * r, self.h + 2 * r)
        dl.box(cx - r, cy - r, 2 * r, 2 * r, (0, 0, 0, 0), border=(1, 1, 1, 1), radius=r, border_width=2 * t.scale)
        dl.box(cx - r - 1, cy - r - 1, 2 * r + 2, 2 * r + 2, (0, 0, 0, 0), border=(0, 0, 0, 0.6), radius=r + 1)
        dl.pop_clip()

    def _apply(self, ev):
        s_ = max(0.0, min(1.0, (ev.x - self.x) / max(1, self.w)))
        v = max(0.0, min(1.0, 1 - (ev.y - self.y) / max(1, self.h)))
        self.picker.set_hsv(self.picker.hsv[0], s_, v)

    def on_mouse_down(self, ui, ev):
        if ev.button == 1:
            self._apply(ev)
            return True
        return False

    def on_mouse_move(self, ui, ev):
        if ui.active is self:
            self._apply(ev)


class _HueBar(Widget):
    def __init__(self, picker, **kw):
        super().__init__(**kw)
        self.picker = picker

    def preferred_size(self, ui):
        return (180 * ui.theme.scale, 16 * ui.theme.scale)

    def draw(self, dl, ui):
        t = ui.theme
        dl.hue_strip(self.x, self.y, self.w, self.h, radius=t.radius * t.scale, border=t.outline)
        x = round(self.x + self.picker.hsv[0] * self.w)
        dl.box(x - 3, self.y - 2, 6, self.h + 4, (1, 1, 1, 1), border=(0, 0, 0, 0.7), radius=2)

    def on_mouse_down(self, ui, ev):
        if ev.button == 1:
            self.on_mouse_move(ui, ev)
            return True
        return False

    def on_mouse_move(self, ui, ev):
        if ui.active is self:
            h = max(0.0, min(0.9999, (ev.x - self.x) / max(1, self.w)))
            p = self.picker
            p.set_hsv(h, p.hsv[1], p.hsv[2])


class ColorPicker(Popup, Column):
    """Popup editor for a ColorSwatch: SV square, hue strip, RGB(A) sliders, hex."""

    def __init__(self, swatch):
        super().__init__(padding=8)
        self.swatch = swatch
        self.hsv = _hsv_of(swatch.value)
        self._syncing = False
        self.add(_SVPlane(self))
        self.add(_HueBar(self))
        group = Column(align=True)
        self.add(group)
        self.channels = []
        for i, name in enumerate("RGBA"[:4 if swatch.alpha else 3]):
            f = NumberField(name, float(swatch.value[i]), min=0.0, max=1.0, slider=True, precision=3)
            f.changed.connect(self._from_channels)
            f.undoable = False
            group.add(f)
            self.channels.append(f)
        self.hex = TextField(to_hex(swatch.value), label="Hex")
        self.hex.undoable = False
        self.hex.changed.connect(self._from_hex)
        self.add(self.hex)

    def set_hsv(self, h, s, v):
        self.hsv = (h, s, v)
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        a = self.swatch.value[3]
        self._push((r, g, b, a), from_hsv=True)

    def _from_channels(self, _v):
        if self._syncing:
            return
        vals = [f.value for f in self.channels]
        rgba = (vals[0], vals[1], vals[2], vals[3] if len(vals) > 3 else self.swatch.value[3])
        self._push(rgba)

    def _from_hex(self, s):
        if self._syncing:
            return
        try:
            c = rgb(s.strip())
        except (ValueError, IndexError):
            return
        if len(s.strip().lstrip("#")) != 8:
            c = with_alpha(c, self.swatch.value[3])
        self._push(c)

    def _push(self, rgba, from_hsv=False):
        if not from_hsv:
            h, s, v = _hsv_of(rgba)
            if s == 0 or v == 0:  # keep hue (and sat) stable on greys/black
                h = self.hsv[0]
                if v == 0:
                    s = self.hsv[1]
            self.hsv = (h, s, v)
        self._syncing = True
        try:
            for f, c in zip(self.channels, rgba):
                f.set_value(c, emit=False)
            self.hex.set_value(to_hex(rgba), emit=False)
        finally:
            self._syncing = False
        self.swatch.set_value(rgba)
        for c in self.iter_tree():
            c.invalidate()
        self.invalidate()

    def draw(self, dl, ui):
        t = ui.theme
        r = t.radius * t.scale
        dl.shadow(self.x, self.y, self.w, self.h, t.shadow, r, 10 * t.scale)
        dl.box(self.x, self.y, self.w, self.h, t.menu, border=t.outline, radius=r)
        self.draw_children(dl, ui)

    def on_close(self, ui):
        self.swatch._picker = None
        self.swatch.end_edit()
        self.swatch.invalidate()


class ColorSwatch(ValueWidget):
    """RGBA colour value; click to open a picker."""

    hover_redraw = True

    def __init__(self, value=(1.0, 1.0, 1.0, 1.0), *, label="", alpha=True, **kw):
        self.label = label
        self.alpha = alpha
        self._picker = None
        if isinstance(value, str):
            value = rgb(value)
        super().__init__(value, **kw)

    def coerce(self, v):
        if isinstance(v, str):
            v = rgb(v)
        v = tuple(max(0.0, min(1.0, float(c))) for c in v)
        if len(v) == 3:
            v = v + (1.0,)
        return v

    def _label_w(self, ui):
        return ui.label_width(self, self.label)

    def preferred_size(self, ui):
        return (ui.label_width(None, self.label) + 60 * ui.theme.scale, ui.theme.h)

    def draw(self, dl, ui):
        t = ui.theme
        lw = self._label_w(ui)
        if lw:
            ui.text(dl, self.label, self.x + 2, self.y, lw - 4, self.h, _text_color(ui, self))
        x, y, w, h = self.x + lw, self.y, self.w - lw, self.h
        rad = self.radii(ui)
        c = self._value
        if c[3] < 0.999:
            dl.checker(x, y, w, h, (0.8, 0.8, 0.8, 1), (0.55, 0.55, 0.55, 1), 6 * t.scale, rad)
            half = round(w / 2)
            dl.box(x, y, half, h, with_alpha(c, 1.0), radius=(rad[0], 0, 0, rad[3]))
            dl.box(x + half, y, w - half, h, c, radius=(0, rad[1], rad[2], 0))
        else:
            dl.box(x, y, w, h, c, radius=rad)
        border = t.accent if (self.hovered or self._picker) else t.outline
        dl.box(x, y, w, h, (0, 0, 0, 0), border=border, radius=rad)

    def open_picker(self, ui):
        if self._picker is not None:
            return
        lw = self._label_w(ui)
        self.begin_edit()
        self._picker = ColorPicker(self)
        ui.open_popup(self._picker, anchor=(self.x + lw, self.y, self.w - lw, self.h))

    def on_mouse_down(self, ui, ev):
        if ev.button == 3:
            self.context_menu(ui, ev)
            return True
        if ev.button == 1:
            self.open_picker(ui)
            return True
        return False

    def copy_text(self):
        return to_hex(self._value)

    def paste_text(self, s):
        try:
            self.set_value(rgb(s.strip()), undo=True)
        except (ValueError, IndexError):
            pass

    def activate(self, ui):
        self.open_picker(ui)


__all__ = [
    "Label", "Button", "ValueWidget", "Checkbox", "Toggle", "LineEdit", "NumberField", "VectorField",
    "TextField", "Dropdown", "EnumRow", "ProgressBar", "Panel",
    "ColorPicker", "ColorSwatch", "Row", "Column", "Tabs", "draw_icon_label",
]
