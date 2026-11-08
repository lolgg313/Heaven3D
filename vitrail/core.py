# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Core: Signal, Widget, Container, UndoStack, Clipboard and the UI object.

The UI object owns the GPU renderer, the widget tree, the popup stack and the
input state (hover, active capture, keyboard focus).  It only rebuilds its
draw list when something was invalidated, so an idle UI costs one cached
draw call per frame -- or nothing at all when driven by ``App`` (which sleeps
until the next event).
"""
from __future__ import annotations

import time
from contextlib import contextmanager

import pygame

from .font import FontCache, GlyphAtlas
from .icons import IconCache

from .renderer import DrawList, Renderer
from .theme import Theme

_build_stack: list = []

CTRL = pygame.KMOD_CTRL | pygame.KMOD_META
SHIFT = pygame.KMOD_SHIFT
ALT = pygame.KMOD_ALT


_MOUSE_EVENTS = (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL)
_INPUT_EVENTS = _MOUSE_EVENTS + (pygame.KEYDOWN, pygame.KEYUP, pygame.TEXTINPUT)


# --------------------------------------------------------------------------- utils
class Signal:
    """Minimal Qt-style signal: ``sig.connect(fn)``, ``sig.emit(*args)``."""

    __slots__ = ("_subs",)

    def __init__(self):
        self._subs = []

    def connect(self, fn):
        if fn not in self._subs:
            self._subs.append(fn)
        return fn

    def disconnect(self, fn):
        if fn in self._subs:
            self._subs.remove(fn)

    def emit(self, *args):
        for fn in tuple(self._subs):
            fn(*args)

    def __bool__(self):
        return bool(self._subs)


class MouseEvent:
    __slots__ = ("x", "y", "button", "mods", "clicks", "dx", "dy")

    def __init__(self, x, y, button=0, mods=0, clicks=1, dx=0, dy=0):
        self.x, self.y, self.button, self.mods, self.clicks, self.dx, self.dy = x, y, button, mods, clicks, dx, dy

    @property
    def ctrl(self):
        return bool(self.mods & CTRL)

    @property
    def shift(self):
        return bool(self.mods & SHIFT)


class UndoStack:
    """Undo/redo of value changes. A whole drag is pushed as ONE step."""

    def __init__(self, limit: int = 256):
        self.limit = limit
        self._undo = []
        self._redo = []
        self.changed = Signal()

    def push(self, label, undo_fn, redo_fn):
        self._undo.append((label, undo_fn, redo_fn))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()
        self.changed.emit()

    def push_value(self, widget, old, new):
        self.push(getattr(widget, "label", "") or type(widget).__name__,
                  lambda: widget.set_value(old, emit=True),
                  lambda: widget.set_value(new, emit=True))

    def undo(self):
        if not self._undo:
            return False
        item = self._undo.pop()
        item[1]()
        self._redo.append(item)
        self.changed.emit()
        return True

    def redo(self):
        if not self._redo:
            return False
        item = self._redo.pop()
        item[2]()
        self._undo.append(item)
        self.changed.emit()
        return True

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)


class Clipboard:
    """System clipboard through pygame.scrap when available, else in-process."""

    def __init__(self):
        self._local = ""

    def put(self, text: str):
        self._local = text
        try:
            pygame.scrap.put_text(text)
        except Exception:
            pass

    def get(self) -> str:
        try:
            t = pygame.scrap.get_text()
            if t:
                return t
        except Exception:
            pass
        return self._local


# --------------------------------------------------------------------------- widgets
# attribute writes that do NOT change how a widget looks by themselves
# (geometry is part of the paint-cache key instead)
_NO_REPAINT = frozenset(("_pv", "_pc_key", "_pc_data", "_pc_count", "x", "y", "w", "h", "_pref",
                         "parent", "_ui", "_m", "_selecting", "_drag_px", "_drag_val"))


class Widget:
    focusable = False      # reachable with Tab while text-editing
    cursor = None          # pygame.SYSTEM_CURSOR_* shown on hover
    hover_redraw = False   # repaint when hover state changes
    auto_add = True        # join the enclosing ``with container:`` block

    _pv = 0          # paint version: bumped by any visual attribute write
    _pc_key = None   # paint cache: key, instance floats, instance count
    _pc_data = None
    _pc_count = 0

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name not in _NO_REPAINT:
            object.__setattr__(self, "_pv", self._pv + 1)

    def __init__(self, *, width=None, height=None, expand=1.0, tooltip=None,
                 enabled=True, visible=True, parent=None, name=None, fill=False):
        self.parent = None
        self.fill = fill          # take leftover height inside a Column
        self._ui = None
        self.x = self.y = 0.0
        self.w = self.h = 0.0
        self.fixed_w = width
        self.fixed_h = height
        self.expand = expand
        self.tooltip = tooltip
        self.name = name
        self._enabled = enabled
        self._visible = visible
        self.corners = (True, True, True, True)  # tl, tr, br, bl rounded?
        self.hovered = False
        self._pref = (0, 0)
        if parent is not None:
            parent.add(self)
        elif _build_stack and self.auto_add:
            _build_stack[-1].add(self)

    # -- tree helpers ------------------------------------------------------------
    @property
    def ui(self):
        w = self
        while w is not None:
            if w._ui is not None:
                return w._ui
            w = w.parent
        return None

    @property
    def enabled(self):
        w = self
        while w is not None:
            if not w._enabled:
                return False
            w = w.parent
        return True

    @enabled.setter
    def enabled(self, v):
        self._enabled = bool(v)
        self.invalidate()

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, v):
        self._visible = bool(v)
        self.invalidate(layout=True)

    def invalidate(self, layout=False):
        object.__setattr__(self, "_pv", self._pv + 1)
        ui = self.ui
        if ui is not None:
            ui.invalidate(layout)

    def is_descendant_of(self, other):
        w = self
        while w is not None:
            if w is other:
                return True
            w = w.parent
        return False

    # -- layout --------------------------------------------------------------------
    def measure(self, ui):
        w, h = self.preferred_size(ui)
        if self.fixed_w is not None:
            w = self.fixed_w * ui.theme.scale
        if self.fixed_h is not None:
            h = self.fixed_h * ui.theme.scale
        self._pref = (w, h)
        return self._pref

    def preferred_size(self, ui):
        return (60 * ui.theme.scale, ui.theme.h)

    def arrange(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def widget_at(self, px, py):
        return self if self._visible and self.contains(px, py) else None

    def radii(self, ui, r=None):
        r = ui.theme.radius * ui.theme.scale if r is None else r
        c = self.corners
        return (r if c[0] else 0.0, r if c[1] else 0.0, r if c[2] else 0.0, r if c[3] else 0.0)

    # -- painting ------------------------------------------------------------------
    def draw(self, dl, ui):
        """Emit this widget's primitives. Override in subclasses."""

    def paint(self, dl, ui):
        """draw() through a per-widget cache: an unchanged widget re-emits its
        previous instances with one memcpy instead of re-running draw()."""
        key = (self._pv, self.x, self.y, self.w, self.h, dl.clip, ui._epoch, ui.atlas.generation,
               ui.images.generation, self.enabled)
        if key == self._pc_key:
            dl.data.extend(self._pc_data)
            dl.count += self._pc_count
            return
        start, n0 = len(dl.data), dl.count
        self.draw(dl, ui)
        object.__setattr__(self, "_pc_data", dl.data[start:])
        object.__setattr__(self, "_pc_count", dl.count - n0)
        object.__setattr__(self, "_pc_key", key)

    # -- input (return True when handled; on_mouse_down True == capture mouse) -----
    def on_mouse_down(self, ui, ev):
        return False

    def on_mouse_up(self, ui, ev):
        pass

    def on_mouse_move(self, ui, ev):
        pass

    def on_wheel(self, ui, dx, dy):
        return False

    def on_key(self, ui, e):
        return False

    def on_text(self, ui, text):
        return False

    def on_focus(self, ui, focused):
        pass

    def on_hover(self, ui, hovered):
        if self.hover_redraw:
            self.invalidate()

    def __repr__(self):
        return f"<{type(self).__name__} {self.name or ''} @({self.x:.0f},{self.y:.0f} {self.w:.0f}x{self.h:.0f})>"


class Container(Widget):
    """Widget with children. Use as a context manager to add children:

        with Column() as col:
            Button("A"); Button("B")
    """

    def __init__(self, *children, padding=0, spacing=None, align=False, **kw):
        super().__init__(**kw)
        self.children = []
        self.padding = padding
        self.spacing = spacing
        self.align = align
        for c in children:
            self.add(c)

    def add(self, *widgets):
        for w in widgets:
            if w.parent is not None:
                if w.parent is self:
                    continue
                w.parent.remove(w)
            w.parent = self
            self.children.append(w)
        self.invalidate(layout=True)
        return widgets[0] if len(widgets) == 1 else widgets

    def remove(self, w):
        if w in self.children:
            self.children.remove(w)
            w.parent = None
            ui = self.ui
            if ui is not None:
                ui._forget(w)
            self.invalidate(layout=True)

    def clear(self):
        for w in list(self.children):
            self.remove(w)

    def __enter__(self):
        _build_stack.append(self)
        return self

    def __exit__(self, *exc):
        _build_stack.pop()
        return False

    def visible_children(self):
        return [c for c in self.children if c._visible]

    def iter_tree(self):
        """Depth-first over visible, expanded descendants."""
        for c in self.visible_children():
            yield c
            if isinstance(c, Container) and c.children_shown():
                yield from c.iter_tree()

    def children_shown(self):
        return True

    def pad(self, ui):
        return self.padding * ui.theme.scale

    def gap(self, ui):
        if self.align:
            return -1.0  # joined widgets share their 1px border
        return (ui.theme.spacing if self.spacing is None else self.spacing) * ui.theme.scale

    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        if self.children_shown():
            for c in reversed(self.children):
                r = c.widget_at(px, py)
                if r is not None:
                    return r
        return self

    def paint(self, dl, ui):
        self.draw(dl, ui)

    def draw(self, dl, ui):
        self.draw_children(dl, ui)

    def draw_children(self, dl, ui):
        for c in self.children:
            if c._visible:
                c.paint(dl, ui)


class Popup(Container):
    """Base for overlay widgets (menus, pickers). Lives in ``ui.overlays``."""

    close_on_outside = True
    auto_add = False
    modal = False          # dims everything below and blocks outside clicks
    passive = False        # True: does not block the UI underneath (snackbars, toasts)

    def __init__(self, *children, **kw):
        super().__init__(*children, **kw)
        self.anchor = None      # (x, y, w, h) we are attached to
        self.prefer_below = True

    def place(self, ui):
        """Position self next to its anchor, kept on screen."""
        mw, mh = self.measure(ui)
        sw, sh = ui.size
        if self.anchor is not None:
            ax, ay, aw, ah = self.anchor
            mw = max(mw, aw)
            x = ax
            y = ay + ah if self.prefer_below else ay - mh
            if y + mh > sh and ay - mh >= 0:
                y = ay - mh
            if y < 0:
                y = min(ay + ah, max(0, sh - mh))
        else:
            x, y = self.x, self.y
        x = max(0, min(x, sw - mw))
        y = max(0, min(y, sh - mh))
        self.arrange(x, y, mw, mh)

    def on_close(self, ui):
        pass


class DragInfo:
    """An in-flight drag & drop. ``payload`` is anything; targets implement
    ``accepts_drop(payload) -> bool`` and ``on_drop(ui, payload, x, y)``."""

    __slots__ = ("payload", "label", "icon", "source", "target", "color")

    def __init__(self, payload, label="", icon=None, source=None, color=None):
        self.payload, self.label, self.icon, self.source, self.color = payload, label, icon, source, color
        self.target = None


# --------------------------------------------------------------------------- UI
class UI:
    """The UI context. Feed it pygame events and call ``draw()`` every frame."""

    TOOLTIP_DELAY = 0.55
    DOUBLE_CLICK = 0.35

    def __init__(self, ctx, size, theme: Theme | None = None):
        self.ctx = ctx
        self.size = (int(size[0]), int(size[1]))
        self.theme = theme or Theme()
        self.atlas = GlyphAtlas(ctx)
        self.fonts = FontCache(self.atlas, self.theme.font_path)
        self.icons = IconCache(self.atlas)
        from .images import ImageCache   # (images.py imports Widget from here)
        self.images = ImageCache(ctx)
        self.renderer = Renderer(ctx, self.atlas.texture, self.images.texture)
        self.dl = DrawList()
        self.root = None
        self.overlays = []
        self.hover = None
        self.active = None
        self.focus = None
        self.mouse = (0, 0)
        self.dirty = True
        self._layout_dirty = True
        self.undo = UndoStack()
        self.clipboard = Clipboard()
        self._timers = []
        self._tooltip = None
        self._tooltip_due = None
        self._tooltip_target = None
        self._drag_toggle = None
        self._last_click = (0.0, None, 0, (0, 0))
        self._cursor = None
        self._text_input = False
        self._mods = 0
        self._epoch = 0
        self._shortcuts = []
        self._anims = {}
        self.drag = None                 # active drag & drop: DragInfo
        self.on_update = Signal()        # (now) every ui.update() -- players, tickers
        self._frame_no = 0
        self.stats = {"instances": 0, "build_ms": 0.0, "builds": 0}

    # -- setup -------------------------------------------------------------------------
    def set_root(self, widget):
        if self.root is not None:
            self.root._ui = None
        self.root = widget
        widget._ui = self
        self.invalidate(layout=True)
        return widget

    def set_theme(self, theme: Theme):
        font_changed = theme.font_path != self.theme.font_path
        self.theme = theme
        self._epoch += 1
        if font_changed:
            self.fonts = FontCache(self.atlas, theme.font_path)
            self.atlas.reset()
        self.invalidate(layout=True)

    def resize(self, w, h):
        self.size = (int(w), int(h))
        self.invalidate(layout=True)

    def invalidate(self, layout=False):
        self.dirty = True
        if layout:
            self._layout_dirty = True

    def refresh(self):
        """Repaint everything (e.g. after mutating the theme in place)."""
        self._epoch += 1
        self.invalidate(layout=True)

    @property
    def blocking_overlays(self):
        return [p for p in self.overlays if not p.passive]

    @property
    def wants_mouse(self):
        """True when the pointer is over (or captured by) the UI."""
        return self.active is not None or self.hover is not None or bool(self.blocking_overlays)

    @property
    def wants_keyboard(self):
        return self.focus is not None or bool(self.blocking_overlays)

    # -- popups ------------------------------------------------------------------------
    def open_popup(self, popup: Popup, anchor=None, at=None):
        if anchor is not None:
            popup.anchor = anchor
        if at is not None:
            popup.anchor = None
            popup.x, popup.y = at
        popup._ui = self
        if popup not in self.overlays:
            self.overlays.append(popup)
        self._hide_tooltip()
        self.invalidate(layout=True)
        return popup

    def close_popup(self, popup):
        if popup in self.overlays:
            idx = self.overlays.index(popup)
            for p in self.overlays[idx:][::-1]:
                self.overlays.remove(p)
                if self.focus is not None and self.focus.is_descendant_of(p):
                    self.set_focus(None)
                p.on_close(self)
                self._forget(p)
            self.invalidate(layout=True)

    def close_popups(self, include_modal=False):
        """Close transient popups (menus, pickers) -- and modal ones if asked."""
        start = 0
        if not include_modal:
            for i, p in enumerate(self.overlays):
                if p.modal:
                    start = i + 1
        if start < len(self.overlays):
            self.close_popup(self.overlays[start])

    def _forget(self, w):
        """Drop references to a widget (or subtree) that left the UI."""
        for attr in ("hover", "active"):
            cur = getattr(self, attr)
            if cur is not None and cur.is_descendant_of(w):
                setattr(self, attr, None)
        if self.focus is not None and self.focus.is_descendant_of(w):
            self.set_focus(None)

    # -- zoomed regions (node editors) -----------------------------------------------
    @contextmanager
    def scaled(self, factor):
        """Temporarily multiply theme.scale (used to lay out / draw / handle input
        for widgets that live inside a zoomable canvas)."""
        t = self.theme
        old = t.scale
        t.scale = old * factor
        try:
            yield
        finally:
            t.scale = old

    @staticmethod
    def zoom_of(w):
        z = 1.0
        p = w.parent if w is not None else None
        while p is not None:
            cz = getattr(p, "content_zoom", None)
            if cz is not None:
                z *= cz
            p = p.parent
        return z

    def _call(self, w, name, *args):
        z = self.zoom_of(w)
        fn = getattr(w, name)
        if z == 1.0:
            return fn(*args)
        with self.scaled(z):
            return fn(*args)

    # -- drag & drop -------------------------------------------------------------------
    def start_drag(self, payload, label="", icon=None, source=None, color=None):
        self.drag = DragInfo(payload, label, icon, source, color)
        self._update_drop_target(self.mouse)
        self.invalidate()
        return self.drag

    def _update_drop_target(self, pos):
        d = self.drag
        w = self.widget_at(*pos)
        while w is not None and not (hasattr(w, "accepts_drop") and w.accepts_drop(d.payload)):
            w = w.parent
        if d.target is not None:
            d.target.invalidate()
        d.target = w
        if w is not None:
            w.invalidate()          # targets may draw a hover highlight that follows the pointer
        self.invalidate()

    def cancel_drag(self):
        if self.drag is not None:
            if self.drag.target is not None:
                self.drag.target.invalidate()
            self.drag = None
            self.invalidate()

    def _draw_drag(self, dl):
        d = self.drag
        t = self.theme
        s = t.scale
        if d.target is not None:
            tg = d.target
            dl.box(tg.x - 1, tg.y - 1, tg.w + 2, tg.h + 2, (0, 0, 0, 0), border=t.accent, radius=t.radius * s,
                   border_width=2 * s)
        mx, my = self.mouse
        label = d.label or ""
        w = self.text_width(label) + (30 if d.icon else 14) * s
        h = t.h
        x, y = mx + 14 * s, my + 10 * s
        dl.shadow(x, y, w, h, t.shadow, t.radius * s, 6 * s)
        dl.box(x, y, w, h, t.menu, border=t.accent if d.target else t.outline, radius=t.radius * s)
        tx = x + 7 * s
        if d.icon:
            tx += self.icon(dl, d.icon, tx, y + (h - self.icon_size(14)) / 2, 14, d.color or t.accent) + 6 * s
        self.text(dl, label, tx, y, w, h, t.text)

    def request_repaint(self, widget, delay=0.01):
        """Repaint ``widget`` shortly (safe to call while drawing)."""
        self.call_later(delay, widget.invalidate)

    def image(self, dl, path, x, y, w, h, *, max_px=256, fit="fit", tint=(1, 1, 1, 1), radius=0.0):
        """Draw an image file fitted in a box. Returns False while it is still loading."""
        from .images import fit_rect
        ref = self.images.get(path, max_px)
        if ref is None:
            return False
        dl.image(*fit_rect(ref.w, ref.h, x, y, w, h, fit), ref.uv, tint, radius)
        return True

    # -- shortcuts ---------------------------------------------------------------------
    def add_shortcut(self, spec, fn, enabled=None):
        """Global keyboard shortcut, e.g. ``ui.add_shortcut("Ctrl+S", save)``."""
        from .menus import parse_shortcut
        self._shortcuts.append((parse_shortcut(spec), fn, enabled))

    # -- animation -----------------------------------------------------------------------
    def animate(self, widget, duration):
        """Repaint ``widget`` every frame for ``duration`` seconds (ripples, slides)."""
        end = time.perf_counter() + duration
        self._anims[widget] = max(end, self._anims.get(widget, 0.0))
        self.invalidate()

    @property
    def animating(self):
        return bool(self._anims)

    # -- timers ------------------------------------------------------------------------
    def call_later(self, delay, fn):
        self._timers.append((time.perf_counter() + delay, fn))

    def next_timeout(self):
        """Seconds until the UI needs to wake up again (None = only on input)."""
        if self._anims:
            return 1.0 / 120.0
        dues = [t for t, _ in self._timers]
        if self._tooltip_due is not None:
            dues.append(self._tooltip_due)
        if not dues:
            return None
        return max(0.0, min(dues) - time.perf_counter())

    def update(self, now=None):
        now = time.perf_counter() if now is None else now
        if self.on_update:
            self.on_update.emit(now)
        if self._anims:
            for w, end in list(self._anims.items()):
                w.invalidate()
                if now >= end:
                    del self._anims[w]
        if self._timers:
            due = [t for t in self._timers if t[0] <= now]
            if due:
                self._timers = [t for t in self._timers if t[0] > now]
                for _, fn in due:
                    fn()
        if self._tooltip_due is not None and now >= self._tooltip_due:
            self._tooltip_due = None
            w = self._tooltip_target
            if w is not None and w is self.hover and self.active is None and w.tooltip:
                self._tooltip = (w.tooltip() if callable(w.tooltip) else w.tooltip, self.mouse, w)
                self.invalidate()

    def _hide_tooltip(self):
        self._tooltip_due = None
        if self._tooltip is not None:
            self._tooltip = None
            self.invalidate()

    # -- fonts / text --------------------------------------------------------------------
    def font(self, bold=False, size=None):
        px = self.theme.px_font if size is None else max(6, round(size * self.theme.scale))
        return self.fonts.get(px, bold)

    def text_width(self, s, bold=False, size=None):
        return self.font(bold, size).width(s)

    def label_width(self, widget, label):
        """Width of a side label so labels line up in a column."""
        if not label:
            return 0.0
        natural = self.text_width(label) + 10 * self.theme.scale
        return max(natural, round(widget.w * self.theme.label_split)) if (widget is not None and widget.w) else natural

    def text(self, dl, s, x, y, w, h, color, align="left", bold=False, size=None, elide=True):
        """Draw ``s`` vertically centred in (y, h). Returns the x after the text."""
        f = self.font(bold, size)
        if elide and w is not None:
            s = f.elide(s, w)
        tw = f.width(s)
        if align == "center":
            tx = x + (w - tw) * 0.5
        elif align == "right":
            tx = x + w - tw
        else:
            tx = x
        baseline = y + (h - (f.ascender - f.descender)) * 0.5 + f.ascender
        if s:
            dl.text_run(f.run(s), round(tx), round(baseline), color)
        return tx + tw

    def icon_size(self, size=None):
        return max(6, round((size or self.theme.icon_size) * self.theme.scale))

    def icon(self, dl, name, x, y, size=None, color=None):
        """Draw a built-in icon with its top-left at (x, y). Returns its pixel size."""
        px = self.icon_size(size)
        run = self.icons.get(name, px)
        if run is not None:
            dl.text_run(run, round(x), round(y), color or self.theme.text)
        return px

    def icon_centered(self, dl, name, x, y, w, h, size=None, color=None):
        px = self.icon_size(size)
        return self.icon(dl, name, x + (w - px) / 2, y + (h - px) / 2, size, color)

    # -- layout / painting ----------------------------------------------------------------
    def layout(self):
        if self.root is not None:
            self.root.measure(self)
            self.root.arrange(0, 0, self.size[0], self.size[1])
        for p in self.overlays:
            p.place(self)
        self._layout_dirty = False

    def build(self):
        t0 = time.perf_counter()
        self._frame_no += 1
        self.images.begin_frame(self._frame_no)
        for _ in range(2):  # second pass only if the glyph atlas had to be reset
            self.atlas.overflowed = False
            dl = self.dl
            dl.clear()
            if self.root is not None:
                self.root.paint(dl, self)
            for p in self.overlays:
                if p.modal:
                    dl.box(0, 0, self.size[0], self.size[1], self.theme.modal_dim)
                p.paint(dl, self)
            if self._tooltip is not None:
                self._draw_tooltip(dl)
            if self.drag is not None:
                self._draw_drag(dl)
            if not self.atlas.overflowed:
                break
        self.dirty = False
        self.stats["instances"] = self.dl.count
        self.stats["build_ms"] = (time.perf_counter() - t0) * 1000.0
        self.stats["builds"] += 1

    def draw(self):
        """Render the UI into the current framebuffer (does not clear)."""
        if self._layout_dirty:
            self.layout()
        if self.dirty:
            self.build()
        self.ctx.viewport = (0, 0, self.size[0], self.size[1])
        self.renderer.render(self.dl, self.size[0], self.size[1])

    def _draw_tooltip(self, dl):
        text, (mx, my), _w = self._tooltip
        t = self.theme
        s = t.scale
        f = self.font()
        lines = str(text).split("\n")
        tw = max(f.width(line) for line in lines)
        lh = f.line_height
        w = tw + 16 * s
        h = lh * len(lines) + 10 * s
        x = min(mx + 12 * s, self.size[0] - w - 2)
        y = my + 20 * s
        if y + h > self.size[1]:
            y = my - h - 8 * s
        dl.shadow(x, y, w, h, t.shadow, 4 * s, 6 * s)
        dl.box(x, y, w, h, t.tooltip, border=t.outline, radius=4 * s)
        for i, line in enumerate(lines):
            col = t.tooltip_text if i == 0 else (t.tooltip_text[0], t.tooltip_text[1], t.tooltip_text[2], 0.7)
            self.text(dl, line, x + 8 * s, y + 5 * s + i * lh, None, lh, col, elide=False)

    # -- hit testing -----------------------------------------------------------------------
    def widget_at(self, x, y):
        if self.overlays:
            for p in reversed(self.overlays):
                r = p.widget_at(x, y)
                if r is not None:
                    return r
                if p.modal:
                    return None
            blocking = self.blocking_overlays
            if blocking:
                bar = getattr(blocking[0], "owner_bar", None)   # menu bar stays live
                if bar is not None and bar.contains(x, y):
                    return bar
                return None
        if self.root is not None:
            return self.root.widget_at(x, y)
        return None

    def iter_widgets(self):
        if self.root is None:
            return
        yield self.root
        if isinstance(self.root, Container):
            yield from self.root.iter_tree()

    # -- focus -----------------------------------------------------------------------------
    def set_focus(self, w):
        if self.focus is w:
            return
        old = self.focus
        self.focus = w
        if old is not None:
            self._call(old, 'on_focus', self, False)
        if w is not None:
            self._call(w, 'on_focus', self, True)
        if w is not None and getattr(w, "wants_text_input", False):
            self.ensure_text_input()
        self.invalidate()

    def ensure_text_input(self):
        if not self._text_input:
            self._text_input = True
            try:
                pygame.key.start_text_input()
            except Exception:
                pass

    def focus_next(self, current, backward=False):
        """Tab navigation between focusable fields (keeps editing mode)."""
        blocking = self.blocking_overlays
        pool = list(blocking[-1].iter_tree()) if blocking else list(self.iter_widgets())
        fields = [w for w in pool if w.focusable and w.enabled]
        if not fields:
            return
        try:
            i = fields.index(current)
        except ValueError:
            i = -1
        nxt = fields[(i + (-1 if backward else 1)) % len(fields)]
        self.set_focus(None)
        if hasattr(nxt, "begin_text_edit"):
            nxt.begin_text_edit(self)
        else:
            self.set_focus(nxt)

    def begin_drag_toggle(self, cls, value):
        """Dragging across widgets of ``cls`` applies ``value`` to each one."""
        self._drag_toggle = (cls, value)

    def set_cursor(self, cursor):
        if cursor == self._cursor:
            return
        self._cursor = cursor
        if pygame.display.get_surface() is None:   # headless / no window yet
            return
        c = cursor if cursor is not None else pygame.SYSTEM_CURSOR_ARROW
        try:
            pygame.mouse.set_cursor(c)
        except Exception:
            try:
                pygame.mouse.set_system_cursor(c)
            except Exception:
                pass

    # -- events ----------------------------------------------------------------------------
    def handle_event(self, e) -> bool:
        """Process one pygame event. Returns True if the UI consumed it."""
        t = e.type
        if self._layout_dirty and t in _INPUT_EVENTS:
            self.layout()          # hit-testing and shortcuts need an up-to-date layout
        if t in _MOUSE_EVENTS:
            # synthetic events (tests, replays) may carry their own modifier state
            self._mods = getattr(e, "mod", None)
            if self._mods is None:
                self._mods = pygame.key.get_mods()
            if t == pygame.MOUSEMOTION:
                return self._motion(e.pos, e.rel)
            if t == pygame.MOUSEBUTTONDOWN:
                return e.button in (1, 2, 3) and self._press(e.pos, e.button)
            if t == pygame.MOUSEBUTTONUP:
                return e.button in (1, 2, 3) and self._release(e.pos, e.button)
            return self._wheel(e.x, e.y)
        if t == pygame.KEYDOWN:
            return self._key(e)
        if t == pygame.KEYUP:
            return self._keyup(e)
        if t == pygame.TEXTINPUT:
            return self._textinput(e.text)
        if t == pygame.WINDOWLEAVE:
            if self.active is None:
                self._set_hover(None)
            return False
        if t == pygame.WINDOWSIZECHANGED:
            self.resize(e.x, e.y)
            return False
        if t in (pygame.WINDOWEXPOSED, pygame.VIDEOEXPOSE, pygame.WINDOWRESTORED, pygame.WINDOWSHOWN):
            self.invalidate()
            return False
        return False

    def _set_hover(self, w):
        if w is self.hover:
            return
        old = self.hover
        self.hover = w
        if old is not None:
            old.hovered = False
            self._call(old, 'on_hover', self, False)
        if w is not None:
            w.hovered = True
            self._call(w, 'on_hover', self, True)
        self.set_cursor(w.cursor if (w is not None and w.enabled) else None)
        self._hide_tooltip()
        if w is not None and w.tooltip:
            self._tooltip_due = time.perf_counter() + self.TOOLTIP_DELAY
            self._tooltip_target = w

    def _motion(self, pos, rel):
        self.mouse = pos
        mods = self._mods
        ev = MouseEvent(pos[0], pos[1], 0, mods, 1, rel[0], rel[1])
        if self.drag is not None:
            self._update_drop_target(pos)
        if self._drag_toggle is not None:
            cls, value = self._drag_toggle
            w = self.widget_at(*pos)
            while w is not None and not isinstance(w, cls):
                w = w.parent
            if w is not None and w.enabled:
                self._call(w, 'drag_toggle_to', self, value)
        if self.active is not None:
            lp = self._last_click[3]
            if abs(pos[0] - lp[0]) > 4 or abs(pos[1] - lp[1]) > 4:   # a drag is never half a double-click
                self._last_click = (0.0, None, 0, (0, 0))
            self._call(self.active, 'on_mouse_move', self, ev)
            return True
        self._set_hover(self.widget_at(*pos))
        if self.hover is not None:
            self._call(self.hover, 'on_mouse_move', self, ev)
            return True
        return False

    def _press(self, pos, button):
        self.mouse = pos
        self._hide_tooltip()
        mods = self._mods
        w = self.widget_at(*pos)
        if self.blocking_overlays and w is None:
            # close the non-modal popups stacked above the top-most modal one
            idx = 0
            for i, p in enumerate(self.overlays):
                if p.modal:
                    idx = i + 1
            closable = [p for p in self.overlays[idx:] if p.close_on_outside and not p.passive]
            if closable:
                self.close_popup(closable[0])
            self._set_hover(self.widget_at(*pos))
            return True
        now = time.perf_counter()
        lt, lw, lc, lp = self._last_click
        near = abs(pos[0] - lp[0]) <= 4 and abs(pos[1] - lp[1]) <= 4
        clicks = lc + 1 if (lw is w and near and now - lt < self.DOUBLE_CLICK) else 1
        self._last_click = (now, w, clicks, pos)
        if self.focus is not None and (w is None or not w.is_descendant_of(self.focus)):
            self.set_focus(None)
        if w is None:
            return False
        if not w.enabled:
            return True
        ev = MouseEvent(pos[0], pos[1], button, mods, clicks)
        t = w
        while t is not None:
            if t.enabled and self._call(t, 'on_mouse_down', self, ev):
                self.active = t
                break
            t = t.parent
        self.invalidate()
        return True

    def _release(self, pos, button):
        self.mouse = pos
        self._drag_toggle = None
        if self.drag is not None and button == 1:
            d, self.drag = self.drag, None
            w = self.widget_at(*pos)
            while w is not None and not (hasattr(w, "accepts_drop") and w.accepts_drop(d.payload)):
                w = w.parent
            if w is not None:
                self._call(w, "on_drop", self, d.payload, pos[0], pos[1])
                w.invalidate()
            if d.target is not None:
                d.target.invalidate()
            self.invalidate()
        a = self.active
        if a is not None and button in (1, 2, 3):
            self.active = None
            self._call(a, 'on_mouse_up', self, MouseEvent(pos[0], pos[1], button, self._mods))
            self._set_hover(self.widget_at(*pos))
            self.invalidate()
            return True
        return self.hover is not None

    def _wheel(self, dx, dy):
        mods = self._mods
        w = self.widget_at(*self.mouse)
        if w is None:
            return False
        if mods & CTRL and w.enabled and hasattr(w, "step"):
            if dy:
                w.step(1 if dy > 0 else -1, undo=True)
            return True
        t = w
        while t is not None:
            if self._call(t, 'on_wheel', self, dx, dy):
                return True
            t = t.parent
        return True

    def _key(self, e):
        self._hide_tooltip()
        if self.drag is not None and e.key == pygame.K_ESCAPE:
            self.cancel_drag()
            return True
        blocking = self.blocking_overlays
        if blocking:
            top = blocking[-1]
            if self.focus is not None and self.focus.is_descendant_of(top) and self._call(self.focus, 'on_key', self, e):
                return True
            if top.on_key(self, e):
                return True
            if e.key == pygame.K_ESCAPE:
                self.close_popup(top)
                return True
        elif self.focus is not None and self._call(self.focus, 'on_key', self, e):
            return True
        from .menus import shortcut_matches
        for parsed, fn, enabled in self._shortcuts:
            if shortcut_matches(parsed, e) and (enabled is None or enabled()):
                fn()
                return True
        ctrl = e.mod & CTRL
        if ctrl and e.key == pygame.K_z:
            (self.undo.redo if e.mod & SHIFT else self.undo.undo)()
            return True
        if ctrl and e.key == pygame.K_y:
            self.undo.redo()
            return True
        h = self.hover
        if h is not None and h.enabled and hasattr(h, "copy_text"):
            if ctrl and e.key == pygame.K_c:
                self.clipboard.put(h.copy_text())
                return True
            if ctrl and e.key == pygame.K_v:
                h.paste_text(self.clipboard.get())
                return True
            if not ctrl and e.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
                h.reset()
                return True
        if h is not None and h.enabled and hasattr(h, "activate") and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            h.activate(self)
            return True
        return bool(blocking)

    def _keyup(self, e):
        for p in reversed(self.overlays):
            if hasattr(p, "on_key_up") and p.on_key_up(self, e):
                return True
        return False

    def _textinput(self, text):
        if self.focus is not None and self._call(self.focus, 'on_text', self, text):
            return True
        blocking = self.blocking_overlays
        if blocking and blocking[-1].on_text(self, text):
            return True
        return False
