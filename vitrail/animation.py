# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Animation: keyframed tracks, a player, a dope-sheet timeline, a graph
(curve) editor and a transport bar.

    clip = Clip("Bounce", start=1, end=96, fps=24)
    tx = clip.add_track("Position X", keys=[(1, 0.0), (48, 4.0), (96, 0.0)])
    player = AnimationPlayer(clip)
    player.bind(tx, some_number_field)       # the field follows the animation
    player.attach(ui)                         # drives playback from ui.update()

    Timeline(player)       # dope sheet: keys per track, scrub, box-select, drag keys
    CurveEditor(player)    # value curves: drag keys in time and value
    TransportBar(player)   # |<  <>  >  <>  >|  loop  frame  start/end  fps

Hover any bound field and press **I** to insert a key at the current frame,
**Alt+I** to delete it.  Keyed fields are tinted (yellow on a key, green when
animated).  Space plays / pauses.
"""
from __future__ import annotations

import bisect
import math
import time

import pygame

from .core import CTRL, SHIFT, ALT, Signal, Widget
from .layout import Row
from .menus import MenuItem, open_context_menu
from .theme import rgb, shade, with_alpha
from .widgets import Button, Dropdown, Label, NumberField, Toggle, ValueWidget

TRACK_COLORS = [rgb("#e8605a"), rgb("#6cc46a"), rgb("#5a9ae8"), rgb("#e8c547"), rgb("#c46ad8"),
                rgb("#4fd0c8"), rgb("#f08a3c"), rgb("#9aa4b4")]
INTERPOLATIONS = ("constant", "linear", "bezier")


# ============================================================================ model
class Keyframe:
    __slots__ = ("frame", "value", "interp", "selected")

    def __init__(self, frame, value, interp="bezier"):
        self.frame = float(frame)
        self.value = float(value)
        self.interp = interp
        self.selected = False

    def __repr__(self):
        return f"<Key {self.frame:g}={self.value:g} {self.interp}>"


class Track:
    """A float channel. ``target`` is a ValueWidget, a callable(value) or (obj, "attr")."""

    def __init__(self, name, keys=(), *, color=None, target=None, getter=None, default=0.0):
        self.name = name
        self.keys = []
        self.color = color
        self.target = target
        self.getter = getter
        self.default = default
        self.visible = True
        self.player = None
        for k in keys:
            self.add_key(*k)

    # -- keys -----------------------------------------------------------------------
    def add_key(self, frame, value, interp="bezier"):
        """Insert (or replace the key at the same frame). Returns the key."""
        for k in self.keys:
            if abs(k.frame - frame) < 1e-6:
                k.value = float(value)
                return k
        k = Keyframe(frame, value, interp)
        bisect.insort(self.keys, k, key=lambda kk: kk.frame) if _HAS_INSORT_KEY else self._insort(k)
        return k

    def _insort(self, k):
        self.keys.append(k)
        self.keys.sort(key=lambda kk: kk.frame)

    def remove_key(self, k):
        if k in self.keys:
            self.keys.remove(k)

    def sort(self):
        self.keys.sort(key=lambda k: k.frame)

    def key_at(self, frame, tol=0.5):
        for k in self.keys:
            if abs(k.frame - frame) <= tol:
                return k
        return None

    # -- evaluation ----------------------------------------------------------------
    def _tangent(self, i):
        ks = self.keys
        if i <= 0 or i >= len(ks) - 1:
            return 0.0                                 # ease in/out at the ends
        a, b, c = ks[i - 1], ks[i], ks[i + 1]
        if (b.value - a.value) * (c.value - b.value) <= 0:
            return 0.0                                 # extreme -> flat (no overshoot)
        return (c.value - a.value) / max(1e-9, c.frame - a.frame)

    def evaluate(self, frame):
        ks = self.keys
        if not ks:
            return self.default
        if frame <= ks[0].frame:
            return ks[0].value
        if frame >= ks[-1].frame:
            return ks[-1].value
        lo, hi = 0, len(ks) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ks[mid].frame <= frame:
                lo = mid
            else:
                hi = mid
        k0, k1 = ks[lo], ks[hi]
        h = k1.frame - k0.frame
        t = (frame - k0.frame) / h if h else 0.0
        if k0.interp == "constant":
            return k0.value
        if k0.interp == "linear":
            return k0.value + (k1.value - k0.value) * t
        m0, m1 = self._tangent(lo) * h, self._tangent(hi) * h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * k0.value + (t3 - 2 * t2 + t) * m0 +
                (-2 * t3 + 3 * t2) * k1.value + (t3 - t2) * m1)

    # -- binding ------------------------------------------------------------------------
    def current_value(self):
        tg = self.target
        if self.getter is not None:
            return self.getter()
        if isinstance(tg, ValueWidget):
            return float(tg.value)
        if isinstance(tg, tuple):
            return float(getattr(tg[0], tg[1]))
        return self.evaluate(self.player.frame) if self.player else self.default

    def apply(self, frame):
        if not self.keys or self.target is None:
            return
        v = self.evaluate(frame)
        tg = self.target
        if isinstance(tg, ValueWidget):
            tg.set_value(v)
        elif isinstance(tg, tuple):
            setattr(tg[0], tg[1], v)
        elif callable(tg):
            tg(v)

    def value_range(self):
        if not self.keys:
            return (self.default - 1, self.default + 1)
        vals = [k.value for k in self.keys]
        return (min(vals), max(vals))


try:
    bisect.insort([], 1, key=lambda x: x)
    _HAS_INSORT_KEY = True
except TypeError:
    _HAS_INSORT_KEY = False


class Clip:
    def __init__(self, name="Clip", *, start=1, end=120, fps=24):
        self.name = name
        self.start, self.end, self.fps = start, end, fps
        self.tracks = []

    def add_track(self, name, keys=(), **kw):
        kw.setdefault("color", TRACK_COLORS[len(self.tracks) % len(TRACK_COLORS)])
        t = Track(name, keys, **kw)
        self.tracks.append(t)
        return t

    def all_keys(self):
        return [(t, k) for t in self.tracks for k in t.keys]

    def selected_keys(self):
        return [(t, k) for t in self.tracks for k in t.keys if k.selected]

    def snapshot(self):
        return [[(k.frame, k.value, k.interp, k.selected) for k in t.keys] for t in self.tracks]

    def restore(self, snap):
        for t, keys in zip(self.tracks, snap):
            t.keys = []
            for f, v, i, sel in keys:
                k = Keyframe(f, v, i)
                k.selected = sel
                t.keys.append(k)
            t.sort()

    def to_dict(self):
        return {"name": self.name, "start": self.start, "end": self.end, "fps": self.fps,
                "tracks": [{"name": t.name, "keys": [[k.frame, k.value, k.interp] for k in t.keys]} for t in self.tracks]}


# ============================================================================ player
class AnimationPlayer:
    def __init__(self, clip):
        self.clip = clip
        self.frame = float(clip.start)
        self.playing = False
        self.loop = True
        self.speed = 1.0
        self.ui = None
        self.views = []                 # widgets repainted when the frame changes
        self.bound = {}                 # widget -> track
        self._last = None
        self.frame_changed = Signal()   # (frame)
        self.playing_changed = Signal()  # (bool)
        self.keys_changed = Signal()
        for t in clip.tracks:
            t.player = self

    # -- wiring -----------------------------------------------------------------------------
    def attach(self, ui, shortcuts=True):
        self.ui = ui
        ui.on_update.connect(self._tick)
        if shortcuts:
            ui.add_shortcut("I", self.key_hovered)
            ui.add_shortcut("Alt+I", lambda: self.key_hovered(delete=True))
            ui.add_shortcut("Space", self.toggle)
        return self

    def bind(self, track, widget):
        track.target = widget
        track.player = self
        self.bound[widget] = track
        widget.anim_track = track
        widget.anim_player = self
        return track

    def add_view(self, w):
        if w not in self.views:
            self.views.append(w)

    def _touch(self):
        for v in self.views:
            v.invalidate()
        for w in self.bound:
            w.invalidate()
        ui = self.ui
        if ui is not None:
            ui.invalidate()

    # -- transport -----------------------------------------------------------------------------
    def set_frame(self, f, *, apply=True):
        c = self.clip
        f = max(float(c.start), min(float(c.end), float(f)))
        if f == self.frame and not apply:
            return
        self.frame = f
        if apply:
            for t in c.tracks:
                t.apply(f)
        self.frame_changed.emit(f)
        self._touch()

    def play(self):
        if not self.playing:
            if self.frame >= self.clip.end and not self.loop:
                self.set_frame(self.clip.start)
            self.playing = True
            self._last = time.perf_counter()
            self.playing_changed.emit(True)
            self._keep_alive()

    def pause(self):
        if self.playing:
            self.playing = False
            self.set_frame(round(self.frame))
            self.playing_changed.emit(False)
            self._touch()

    def toggle(self):
        (self.pause if self.playing else self.play)()

    def jump(self, where):
        c = self.clip
        if where == "start":
            self.set_frame(c.start)
        elif where == "end":
            self.set_frame(c.end)
        elif where in ("next", "prev"):
            frames = sorted({k.frame for t in c.tracks for k in t.keys})
            if where == "next":
                nxt = [f for f in frames if f > self.frame + 1e-6]
                if nxt:
                    self.set_frame(nxt[0])
            else:
                prv = [f for f in frames if f < self.frame - 1e-6]
                if prv:
                    self.set_frame(prv[-1])

    def _keep_alive(self):
        if self.ui is not None:
            for v in self.views or [None]:
                if v is not None:
                    self.ui.animate(v, 0.25)

    def _tick(self, now):
        if not self.playing:
            return
        dt = now - (self._last or now)
        self._last = now
        c = self.clip
        f = self.frame + dt * c.fps * self.speed
        if f > c.end:
            if self.loop:
                f = c.start + (f - c.end) % max(1e-6, (c.end - c.start))
            else:
                f = c.end
                self.playing = False
                self.playing_changed.emit(False)
        self.frame = f
        for t in c.tracks:
            t.apply(f)
        self.frame_changed.emit(f)
        self._touch()
        if self.playing:
            self._keep_alive()

    # -- keying --------------------------------------------------------------------------------------
    def insert_key(self, track, frame=None, value=None):
        f = round(self.frame) if frame is None else frame
        v = track.current_value() if value is None else value
        before = self.clip.snapshot()
        track.add_key(f, v)
        self.push_undo("Insert key", before)
        self.keys_changed.emit()
        self._touch()

    def key_hovered(self, delete=False):
        ui = self.ui
        w = ui.hover if ui else None
        while w is not None and w not in self.bound:
            w = w.parent
        if w is None:
            return False
        track = self.bound[w]
        if delete:
            k = track.key_at(self.frame)
            if k is not None:
                before = self.clip.snapshot()
                track.remove_key(k)
                self.push_undo("Delete key", before)
                self.keys_changed.emit()
                self._touch()
        else:
            self.insert_key(track)
        return True

    def key_all(self):
        before = self.clip.snapshot()
        for t in self.clip.tracks:
            if t.target is not None:
                t.add_key(round(self.frame), t.current_value())
        self.push_undo("Key all", before)
        self.keys_changed.emit()
        self._touch()

    def push_undo(self, label, before):
        ui = self.ui
        if ui is None:
            return
        after = self.clip.snapshot()

        def apply(snap):
            self.clip.restore(snap)
            for t in self.clip.tracks:
                t.apply(self.frame)
            self.keys_changed.emit()
            self._touch()
        ui.undo.push(label, lambda: apply(before), lambda: apply(after))

    def anim_state(self, widget):
        """None, "animated" or "keyed" (for tinting bound fields)."""
        t = self.bound.get(widget)
        if t is None or not t.keys:
            return None
        return "keyed" if t.key_at(self.frame, 0.01) else "animated"


ANIM_TINT = {"keyed": rgb("#d9b632", 0.42), "animated": rgb("#4fa556", 0.38)}


# ============================================================================ shared view helpers
def _nice_step(px_per_unit, min_px=60):
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if step * px_per_unit >= min_px:
            return step
    return 10000


def _nice_value_step(px_per_value, min_px=28):
    raw = min_px / max(1e-9, px_per_value)
    p = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * p >= raw:
            return m * p
    return 10 * p


class _FrameView(Widget):
    """Horizontal frame axis shared by the timeline and curve editor."""

    focusable = True
    RULER = 24

    def __init__(self, player, **kw):
        super().__init__(**kw)
        self.player = player
        player.add_view(self)
        self.frame0 = None
        self.ppf = 6.0
        self._mode = None
        self._drag = None
        self._box = None
        self.left = 0.0

    # mapping
    def fx(self, frame):
        return self.x + self.left + (frame - self.frame0) * self.ppf

    def xf(self, x):
        return self.frame0 + (x - self.x - self.left) / self.ppf

    def _ensure_view(self):
        if self.frame0 is None and self.w > self.left + 20:
            self.frame_range()

    def frame_range(self):
        c = self.player.clip
        span = max(1, c.end - c.start)
        avail = max(40.0, self.w - self.left - 30)
        self.ppf = avail / span
        self.frame0 = c.start - 12 / self.ppf
        self.invalidate()

    def ruler_h(self, ui):
        return round(self.RULER * ui.theme.scale)

    def _draw_ruler(self, dl, ui):
        t = ui.theme
        s = t.scale
        rh = self.ruler_h(ui)
        x0 = self.x + self.left
        dl.box(x0, self.y, self.w - self.left, rh, shade(t.panel_header_bg, -0.02))
        dl.box(x0, self.y + rh - 1, self.w - self.left, 1, t.outline)
        step = _nice_step(self.ppf)
        f = math.floor(self.xf(x0) / step) * step
        end = self.xf(self.x + self.w)
        while f <= end:
            x = self.fx(f)
            if x >= x0:
                dl.box(round(x), self.y + rh - 7 * s, 1, 6 * s, t.text_dim)
                ui.text(dl, f"{f:g}", x + 3 * s, self.y, 60 * s, rh - 4 * s, t.text_dim, size=11, elide=False)
            sub = step / 5 if step >= 5 else 0
            if sub and sub * self.ppf > 6:
                for i in range(1, 5):
                    sx = self.fx(f + i * sub)
                    if x0 <= sx <= self.x + self.w:
                        dl.box(round(sx), self.y + rh - 4 * s, 1, 3 * s, with_alpha(t.text_dim, 0.5))
            f += step

    def _draw_body_grid(self, dl, ui, top):
        t = ui.theme
        c = self.player.clip
        x0 = self.x + self.left
        step = _nice_step(self.ppf)
        f = math.floor(self.xf(x0) / step) * step
        while f <= self.xf(self.x + self.w):
            x = self.fx(f)
            if x >= x0:
                dl.box(round(x), top, 1, self.y + self.h - top, with_alpha(t.text, 0.05))
            f += step
        # outside the clip range
        xs, xe = self.fx(c.start), self.fx(c.end)
        if xs > x0:
            dl.box(x0, top, xs - x0, self.y + self.h - top, (0, 0, 0, 0.22))
        if xe < self.x + self.w:
            dl.box(max(x0, xe), top, self.x + self.w - max(x0, xe), self.y + self.h - top, (0, 0, 0, 0.22))

    def _draw_playhead(self, dl, ui):
        t = ui.theme
        s = t.scale
        f = self.player.frame
        x = self.fx(f)
        if x < self.x + self.left - 1 or x > self.x + self.w + 1:
            return
        rh = self.ruler_h(ui)
        dl.box(round(x) - 1, self.y + rh, 2, self.h - rh, t.accent)
        label = f"{f:.0f}" if not self.player.playing else f"{f:.1f}"
        lw = ui.text_width(label, size=11) + 12 * s
        dl.box(x - lw / 2, self.y + 3 * s, lw, rh - 6 * s, t.accent, radius=4 * s)
        ui.text(dl, label, x - lw / 2, self.y + 3 * s, lw, rh - 6 * s, t.text_on_accent, "center", size=11, bold=True)

    # shared interactions
    def _scrub(self, ui, ev):
        self.player.set_frame(round(self.xf(ev.x)))

    def on_wheel(self, ui, dx, dy):
        if not dy and not dx:
            return False
        mods = ui._mods
        if mods & SHIFT or dx:
            self.frame0 -= (dy or -dx) * 40 / self.ppf
        else:
            f = self.xf(ui.mouse[0])
            self.ppf = max(0.3, min(80.0, self.ppf * (1.18 if dy > 0 else 1 / 1.18)))
            self.frame0 = f - (ui.mouse[0] - self.x - self.left) / self.ppf
        self.invalidate()
        return True

    def _begin_pan(self, ev):
        self._mode = "pan"
        self._drag = (ev.x, ev.y, self.frame0)

    def _pan(self, ev):
        x0, _, f0 = self._drag[:3]
        self.frame0 = f0 - (ev.x - x0) / self.ppf
        self.invalidate()

    def _common_keys(self, ui, e):
        p = self.player
        k = e.key
        if k == pygame.K_SPACE:
            p.toggle()
        elif k == pygame.K_LEFT:
            p.set_frame(round(p.frame) - 1)
        elif k == pygame.K_RIGHT:
            p.set_frame(round(p.frame) + 1)
        elif k == pygame.K_UP:
            p.jump("next")
        elif k == pygame.K_DOWN:
            p.jump("prev")
        elif k == pygame.K_HOME:
            self.frame_all()
        elif k in (pygame.K_DELETE, pygame.K_x) and not e.mod & CTRL:
            self.delete_selected()
        elif k == pygame.K_a and e.mod & CTRL:
            for _, key in p.clip.all_keys():
                key.selected = True
            p._touch()
        elif k == pygame.K_i:
            p.key_all()
        else:
            return False
        return True

    def frame_all(self):
        self.frame_range()

    def delete_selected(self):
        p = self.player
        sel = p.clip.selected_keys()
        if not sel:
            return
        before = p.clip.snapshot()
        for t, k in sel:
            t.remove_key(k)
        p.push_undo("Delete keys", before)
        for t in p.clip.tracks:
            t.apply(p.frame)
        p.keys_changed.emit()
        p._touch()

    def _set_interp(self, interp):
        p = self.player
        before = p.clip.snapshot()
        for _, k in p.clip.selected_keys():
            k.interp = interp
        p.push_undo("Interpolation", before)
        for t in p.clip.tracks:
            t.apply(p.frame)
        p._touch()

    def _key_menu(self, ui, ev):
        cur = {k.interp for _, k in self.player.clip.selected_keys()}
        open_context_menu(ui, [
            MenuItem("Interpolation", icon="chart-line", submenu=[
                MenuItem(n.title(), (lambda n=n: self._set_interp(n)), checked=(cur == {n})) for n in INTERPOLATIONS]),
            MenuItem("Insert Keys at Playhead", self.player.key_all, icon="diamond", shortcut="I"),
            MenuItem("Delete Keys", self.delete_selected, icon="trash", shortcut="X"),
            None,
            MenuItem("Frame All", self.frame_all, icon="fullscreen", shortcut="Home"),
        ], at=(ev.x, ev.y))

    def on_focus(self, ui, focused):
        self.invalidate()


# ============================================================================ dope sheet
class Timeline(_FrameView):
    """Dope sheet: one row per track plus a summary row."""

    def __init__(self, player, *, name_width=160, row_height=22, **kw):
        kw.setdefault("height", 170)
        super().__init__(player, **kw)
        self.name_width = name_width
        self.row_height = row_height
        self.vscroll = 0.0

    def preferred_size(self, ui):
        return (400 * ui.theme.scale, 160 * ui.theme.scale)

    def rh(self, ui):
        return round(self.row_height * ui.theme.scale)

    def _rows(self):
        return [None] + list(self.player.clip.tracks)    # None = summary

    def row_y(self, ui, i):
        return self.y + self.ruler_h(ui) + i * self.rh(ui) - self.vscroll

    def row_at(self, ui, py):
        i = int((py - self.y - self.ruler_h(ui) + self.vscroll) // self.rh(ui))
        return i if 0 <= i < len(self._rows()) else None

    def _hit_key(self, ui, px, py):
        i = self.row_at(ui, py)
        if i is None:
            return None, None
        tol = 6 * ui.theme.scale
        tr = self._rows()[i]
        tracks = self.player.clip.tracks if tr is None else [tr]
        best, bd = None, tol + 1
        for t in tracks:
            for k in t.keys:
                d = abs(self.fx(k.frame) - px)
                if d < bd:
                    best, bd = (t, k), d
        return i, best

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        self.left = self.name_width * s
        self._ensure_view()
        if self.frame0 is None:
            return
        rh = self.rh(ui)
        top = self.y + self.ruler_h(ui)
        dl.push_clip(self.x, self.y, self.w, self.h)
        dl.box(self.x, self.y, self.w, self.h, shade(t.panel, -0.06))
        rows = self._rows()
        body_h = self.y + self.h - top
        self.vscroll = max(0.0, min(self.vscroll, len(rows) * rh - body_h))
        dl.push_clip(self.x, top, self.w, body_h)
        for i, tr in enumerate(rows):
            y = self.row_y(ui, i)
            if i % 2:
                dl.box(self.x, y, self.w, rh, with_alpha(t.text, 0.025))
        self._draw_body_grid(dl, ui, top)
        # names column
        dl.box(self.x, top, self.left, body_h, t.panel)
        dl.box(self.x + self.left - 1, top, 1, body_h, t.outline)
        for i, tr in enumerate(rows):
            y = self.row_y(ui, i)
            if tr is None:
                dl.box(self.x, y, self.left - 1, rh, with_alpha(t.accent, 0.12))
                ui.text(dl, "Summary", self.x + 10 * s, y, self.left - 20 * s, rh, t.text, bold=True)
            else:
                dl.box(self.x + 10 * s, y + rh / 2 - 4 * s, 8 * s, 8 * s, tr.color, radius=2 * s)
                ui.text(dl, tr.name, self.x + 24 * s, y, self.left - 30 * s, rh, t.text)
        # keys
        x0 = self.x + self.left
        dl.push_clip(x0, top, self.w - self.left, body_h)
        ksz = round(12 * s)
        sel_col = rgb("#ffa630")
        summary = {}
        for tr in self.player.clip.tracks:
            for k in tr.keys:
                summary.setdefault(round(k.frame, 3), []).append(k)
        y = self.row_y(ui, 0)
        for f, ks in summary.items():
            x = self.fx(f)
            if x0 - ksz <= x <= self.x + self.w + ksz:
                col = sel_col if any(k.selected for k in ks) else t.text
                ui.icon(dl, "diamond-filled", x - (ksz + 2) / 2, y + (rh - ksz - 2) / 2, (ksz + 2) / s, (0.05, 0.05, 0.06, 1))
                ui.icon(dl, "diamond-filled", x - ksz / 2 + 1, y + (rh - ksz) / 2 + 1, (ksz - 2) / s, col)
        for i, tr in enumerate(rows[1:], start=1):
            y = self.row_y(ui, i)
            prev = None
            for k in tr.keys:
                x = self.fx(k.frame)
                if prev is not None and prev.value == k.value and x - self.fx(prev.frame) > ksz:
                    dl.box(self.fx(prev.frame), y + rh / 2 - 2 * s, x - self.fx(prev.frame), 4 * s,
                           with_alpha(tr.color, 0.35))
                prev = k
            for k in tr.keys:
                x = self.fx(k.frame)
                if x0 - ksz <= x <= self.x + self.w + ksz:
                    ui.icon(dl, "diamond-filled", x - ksz / 2, y + (rh - ksz) / 2, ksz / s, (0.05, 0.05, 0.06, 1))
                    inner = sel_col if k.selected else (shade(tr.color, 0.35) if k.interp != "constant" else t.text_dim)
                    ui.icon(dl, "diamond-filled", x - ksz / 2 + 1.5 * s, y + (rh - ksz) / 2 + 1.5 * s,
                            (ksz - 3 * s) / s, inner)
        if self._box is not None:
            bx0, by0, bx1, by1 = self._box
            dl.box(min(bx0, bx1), min(by0, by1), abs(bx1 - bx0), abs(by1 - by0), with_alpha(t.accent, 0.12),
                   border=t.accent, radius=2)
        dl.pop_clip()
        dl.pop_clip()
        self._draw_ruler(dl, ui)
        dl.box(self.x, self.y, self.left, self.ruler_h(ui), t.panel_header_bg)
        ui.icon(dl, "film", self.x + 8 * s, self.y + (self.ruler_h(ui) - ui.icon_size(14)) / 2, 14, t.text_dim)
        ui.text(dl, self.player.clip.name, self.x + 28 * s, self.y, self.left - 34 * s, self.ruler_h(ui),
                t.text_dim, bold=True)
        self._draw_playhead(dl, ui)
        if ui.focus is self:
            dl.box(self.x, self.y, self.w, self.h, (0, 0, 0, 0), border=with_alpha(t.accent, 0.6))
        dl.pop_clip()

    def on_mouse_down(self, ui, ev):
        ui.set_focus(self)
        p = self.player
        if ev.button == 2 or (ev.button == 1 and ev.mods & ALT):
            self._begin_pan(ev)
            return True
        if ev.y < self.y + self.ruler_h(ui):
            if ev.x >= self.x + self.left:
                self._mode = "scrub"
                self._scrub(ui, ev)
            return True
        i, hit = self._hit_key(ui, ev.x, ev.y)
        if ev.x < self.x + self.left:                   # names: select a whole track
            if i is not None:
                tr = self._rows()[i]
                for t, k in p.clip.all_keys():
                    k.selected = (tr is None or t is tr) if not ev.shift else (k.selected or tr is None or t is tr)
                p._touch()
            return True
        if ev.button == 3:
            if hit is not None and not hit[1].selected:
                self._select_hit(i, hit, False)
            self._key_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        if hit is not None:
            self._select_hit(i, hit, ev.shift)
            sel = p.clip.selected_keys()
            self._mode = "move"
            self._drag = (ev.x, ev.y, [(t, k, k.frame) for t, k in sel], p.clip.snapshot(), False)
            return True
        self._mode = "box"
        self._box = [ev.x, ev.y, ev.x, ev.y]
        self._box_shift = ev.shift
        if not ev.shift:
            for _, k in p.clip.all_keys():
                k.selected = False
        p._touch()
        return True

    def _select_hit(self, row, hit, additive):
        p = self.player
        t, k = hit
        same = [kk for tt in (p.clip.tracks if self._rows()[row] is None else [t]) for kk in tt.keys
                if abs(kk.frame - k.frame) < 1e-6]
        if additive:
            new = not k.selected
            for kk in same:
                kk.selected = new
        elif not k.selected:
            for _, kk in p.clip.all_keys():
                kk.selected = False
            for kk in same:
                kk.selected = True
        p._touch()

    def on_mouse_move(self, ui, ev):
        m = self._mode
        if m == "pan":
            self._pan(ev)
        elif m == "scrub":
            self._scrub(ui, ev)
        elif m == "move":
            x0, y0, items, snap, moved = self._drag
            df = round((ev.x - x0) / self.ppf)
            if df or moved:
                for t, k, f in items:
                    k.frame = f + df
                for t in {t for t, _, _ in items}:
                    t.sort()
                self._drag = (x0, y0, items, snap, True)
                for t in self.player.clip.tracks:
                    t.apply(self.player.frame)
                self.player._touch()
        elif m == "box":
            self._box[2], self._box[3] = ev.x, ev.y
            self.invalidate()

    def on_mouse_up(self, ui, ev):
        m, self._mode = self._mode, None
        p = self.player
        if m == "move":
            x0, y0, items, snap, moved = self._drag
            if moved:
                # merge keys that landed on the same frame
                for t in {t for t, _, _ in items}:
                    seen = {}
                    for k in list(t.keys):
                        if k.frame in seen and k.selected:
                            t.keys.remove(seen[k.frame])
                        seen[k.frame] = k
                p.push_undo("Move keys", snap)
                p.keys_changed.emit()
        elif m == "box":
            bx0, by0, bx1, by1 = self._box
            self._box = None
            fa, fb = sorted((self.xf(bx0), self.xf(bx1)))
            ia, ib = sorted((self.row_at(ui, min(by0, by1)) or 0, self.row_at(ui, max(by0, by1)) or len(self._rows()) - 1))
            rows = self._rows()
            tracks = set()
            for i in range(ia, ib + 1):
                tracks |= set(p.clip.tracks) if rows[i] is None else {rows[i]}
            for t in tracks:
                for k in t.keys:
                    if fa <= k.frame <= fb:
                        k.selected = True
            p._touch()
        self._drag = None
        self.invalidate()

    def on_wheel(self, ui, dx, dy):
        if ui._mods & CTRL:
            self.vscroll -= dy * self.rh(ui) * 2
            self.invalidate()
            return True
        return super().on_wheel(ui, dx, dy)

    def on_key(self, ui, e):
        return self._common_keys(ui, e)


# ============================================================================ graph editor
class CurveEditor(_FrameView):
    """Function curves of every visible track; drag keys in time and value."""

    LEGEND_W = 150

    def __init__(self, player, **kw):
        kw.setdefault("height", 220)
        super().__init__(player, **kw)
        self.v0 = None      # value at the bottom edge
        self.ppv = 40.0     # pixels per value unit

    def preferred_size(self, ui):
        return (400 * ui.theme.scale, 220 * ui.theme.scale)

    def vy(self, v):
        return self.y + self.h - (v - self.v0) * self.ppv

    def yv(self, y):
        return self.v0 + (self.y + self.h - y) / self.ppv

    def frame_all(self):
        self.frame_range()
        tracks = [t for t in self.player.clip.tracks if t.visible and t.keys]
        if not tracks:
            self.v0, self.ppv = -1.0, max(10.0, self.h / 3)
        else:
            lo = min(t.value_range()[0] for t in tracks)
            hi = max(t.value_range()[1] for t in tracks)
            if hi - lo < 1e-6:
                lo, hi = lo - 1, hi + 1
            ui = self.ui
            rh = self.ruler_h(ui) if ui else 24
            avail = max(20.0, self.h - rh - 40)
            self.ppv = avail / (hi - lo)
            self.v0 = lo - 20 / self.ppv
        self.invalidate()

    def _ensure_view(self):
        if (self.frame0 is None or self.v0 is None) and self.w > self.left + 20 and self.h > 40:
            self.frame_all()

    def _legend_rows(self, ui):
        s = ui.theme.scale
        out = []
        y = self.y + self.ruler_h(ui) + 6 * s
        for t in self.player.clip.tracks:
            out.append((t, (self.x + 6 * s, y, self.LEGEND_W * s, 20 * s)))
            y += 20 * s
        return out

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        self.left = 0.0
        self._ensure_view()
        if self.frame0 is None or self.v0 is None:
            return
        rh = self.ruler_h(ui)
        top = self.y + rh
        dl.push_clip(self.x, self.y, self.w, self.h)
        dl.box(self.x, self.y, self.w, self.h, shade(t.background, -0.08))
        self._draw_body_grid(dl, ui, top)
        # value grid
        vstep = _nice_value_step(self.ppv)
        v = math.floor(self.yv(self.y + self.h) / vstep) * vstep
        while v <= self.yv(top):
            y = self.vy(v)
            if y >= top:
                strong = abs(v) < vstep * 1e-6
                dl.box(self.x, round(y), self.w, 1, with_alpha(t.text, 0.14 if strong else 0.05))
                ui.text(dl, f"{v:g}", self.x + self.w - 64 * s, y - 14 * s, 60 * s, 14 * s, t.text_dim, "right",
                        size=10, elide=False)
            v += vstep
        # curves
        for tr in self.player.clip.tracks:
            if not tr.visible or not tr.keys:
                continue
            pts = []
            xx = self.x
            stepx = 3 * s
            while xx <= self.x + self.w + stepx:
                pts.append((xx, self.vy(tr.evaluate(self.xf(xx)))))
                xx += stepx
            dl.polyline(pts, with_alpha(tr.color, 0.95), 1.8 * s)
            for k in tr.keys:
                x, y = self.fx(k.frame), self.vy(k.value)
                r = 4.5 * s
                dl.box(x - r - 1, y - r - 1, 2 * r + 2, 2 * r + 2, (0.05, 0.05, 0.06, 1), radius=r + 1)
                dl.box(x - r, y - r, 2 * r, 2 * r, rgb("#ffa630") if k.selected else (0.95, 0.95, 0.95, 1), radius=r)
        if self._box is not None:
            bx0, by0, bx1, by1 = self._box
            dl.box(min(bx0, bx1), min(by0, by1), abs(bx1 - bx0), abs(by1 - by0), with_alpha(t.accent, 0.12),
                   border=t.accent, radius=2)
        # legend (click to show / hide channels)
        rows = self._legend_rows(ui)
        if rows:
            lx, ly, lw, _ = rows[0][1]
            dl.box(lx - 2 * s, ly - 3 * s, lw + 4 * s, len(rows) * 20 * s + 6 * s, with_alpha(t.panel, 0.88),
                   border=t.outline, radius=t.radius * s)
            for tr, (x, y, w, h) in rows:
                dl.box(x + 6 * s, y + h / 2 - 5 * s, 10 * s, 10 * s, tr.color if tr.visible else (0, 0, 0, 0),
                       border=tr.color, radius=3 * s, border_width=1.5 * s)
                ui.text(dl, tr.name, x + 22 * s, y, w - 26 * s, h, t.text if tr.visible else t.text_dim, size=12)
        self._draw_ruler(dl, ui)
        self._draw_playhead(dl, ui)
        if ui.focus is self:
            dl.box(self.x, self.y, self.w, self.h, (0, 0, 0, 0), border=with_alpha(t.accent, 0.6))
        dl.pop_clip()

    def _hit(self, ui, px, py):
        r = 8 * ui.theme.scale
        best, bd = None, r
        for tr in self.player.clip.tracks:
            if not tr.visible:
                continue
            for k in tr.keys:
                d = math.hypot(self.fx(k.frame) - px, self.vy(k.value) - py)
                if d < bd:
                    best, bd = (tr, k), d
        return best

    def on_mouse_down(self, ui, ev):
        ui.set_focus(self)
        p = self.player
        if ev.button == 2 or (ev.button == 1 and ev.mods & ALT):
            self._mode = "pan"
            self._drag = (ev.x, ev.y, self.frame0, self.v0)
            return True
        if ev.y < self.y + self.ruler_h(ui):
            self._mode = "scrub"
            self._scrub(ui, ev)
            return True
        for tr, (x, y, w, h) in self._legend_rows(ui):
            if x <= ev.x < x + w and y <= ev.y < y + h:
                tr.visible = not tr.visible
                self.invalidate()
                return True
        hit = self._hit(ui, ev.x, ev.y)
        if ev.button == 3:
            if hit is not None and not hit[1].selected:
                for _, k in p.clip.all_keys():
                    k.selected = False
                hit[1].selected = True
            self._key_menu(ui, ev)
            return True
        if ev.button != 1:
            return False
        if hit is not None:
            tr, k = hit
            if ev.shift:
                k.selected = not k.selected
            elif not k.selected:
                for _, kk in p.clip.all_keys():
                    kk.selected = False
                k.selected = True
            sel = p.clip.selected_keys()
            self._mode = "move"
            self._drag = (ev.x, ev.y, [(t, kk, kk.frame, kk.value) for t, kk in sel], p.clip.snapshot(), False)
            p._touch()
            return True
        self._mode = "box"
        self._box = [ev.x, ev.y, ev.x, ev.y]
        if not ev.shift:
            for _, k in p.clip.all_keys():
                k.selected = False
        p._touch()
        return True

    def on_mouse_move(self, ui, ev):
        m = self._mode
        p = self.player
        if m == "pan":
            x0, y0, f0, v0 = self._drag
            self.frame0 = f0 - (ev.x - x0) / self.ppf
            self.v0 = v0 + (ev.y - y0) / self.ppv
            self.invalidate()
        elif m == "scrub":
            self._scrub(ui, ev)
        elif m == "move":
            x0, y0, items, snap, moved = self._drag
            df = (ev.x - x0) / self.ppf
            dv = -(ev.y - y0) / self.ppv
            if ev.shift:                     # axis lock: the larger motion wins
                if abs(ev.x - x0) > abs(ev.y - y0):
                    dv = 0.0
                else:
                    df = 0.0
            for t, k, f, v in items:
                k.frame = round(f + df) if not ev.ctrl else f + df
                k.value = v + dv
            for t in {t for t, *_ in items}:
                t.sort()
            self._drag = (x0, y0, items, snap, True)
            for t in p.clip.tracks:
                t.apply(p.frame)
            p._touch()
        elif m == "box":
            self._box[2], self._box[3] = ev.x, ev.y
            self.invalidate()

    def on_mouse_up(self, ui, ev):
        m, self._mode = self._mode, None
        p = self.player
        if m == "move" and self._drag[4]:
            p.push_undo("Move keys", self._drag[3])
            p.keys_changed.emit()
        elif m == "box":
            bx0, by0, bx1, by1 = self._box
            self._box = None
            fa, fb = sorted((self.xf(bx0), self.xf(bx1)))
            va, vb = sorted((self.yv(by0), self.yv(by1)))
            for tr in p.clip.tracks:
                if tr.visible:
                    for k in tr.keys:
                        if fa <= k.frame <= fb and va <= k.value <= vb:
                            k.selected = True
            p._touch()
        self._drag = None
        self.invalidate()

    def on_wheel(self, ui, dx, dy):
        if not dy:
            return super().on_wheel(ui, dx, dy)
        mods = ui._mods
        mx, my = ui.mouse
        k = 1.18 if dy > 0 else 1 / 1.18
        if not mods & SHIFT:            # value zoom (Ctrl = value only)
            v = self.yv(my)
            self.ppv = max(1e-3, min(1e6, self.ppv * k))
            self.v0 = v - (self.y + self.h - my) / self.ppv
        if not mods & CTRL:
            f = self.xf(mx)
            self.ppf = max(0.3, min(80.0, self.ppf * k))
            self.frame0 = f - (mx - self.x - self.left) / self.ppf
        self.invalidate()
        return True

    def on_key(self, ui, e):
        return self._common_keys(ui, e)


# ============================================================================ transport
class TransportBar(Row):
    """Playback controls bound to a player."""

    def __init__(self, player, **kw):
        kw.setdefault("spacing", 4)
        kw.setdefault("padding", 4)
        super().__init__(**kw)
        self.player = player
        p = player
        c = p.clip
        B = lambda icon, fn, tip: Button(icon=icon, tooltip=tip, expand=0, parent=self, on_click=fn)  # noqa: E731
        B("skip-back", lambda: p.jump("start"), "Jump to start (Shift+Left)")
        B("keyframe-prev", lambda: p.jump("prev"), "Previous key (Down)")
        self.play_btn = B("play", p.toggle, "Play / pause (Space)")
        B("keyframe-next", lambda: p.jump("next"), "Next key (Up)")
        B("skip-forward", lambda: p.jump("end"), "Jump to end")
        self.loop = Toggle("", p.loop, icon="repeat", expand=0, width=34, parent=self, tooltip="Loop playback")
        self.loop.changed.connect(lambda v: setattr(p, "loop", v))
        self.frame_field = NumberField("Frame", int(p.frame), integer=True, width=130, expand=0, parent=self)
        self.frame_field.changed.connect(lambda v: p.set_frame(v))
        Label("", expand=1, parent=self)
        self.start_field = NumberField("Start", int(c.start), integer=True, width=110, expand=0, parent=self)
        self.end_field = NumberField("End", int(c.end), integer=True, width=110, expand=0, parent=self)
        self.start_field.changed.connect(self._range)
        self.end_field.changed.connect(self._range)
        self.fps = Dropdown([(f, f"{f} fps") for f in (12, 24, 25, 30, 48, 60)], c.fps, width=90, expand=0, parent=self)
        self.fps.changed.connect(lambda v: setattr(c, "fps", v))
        self.key_btn = Button("Key", p.key_all, icon="diamond", expand=0, parent=self,
                              tooltip="Insert keys on all bound tracks (I)")
        p.frame_changed.connect(self._sync)
        p.playing_changed.connect(self._sync_play)

    def _range(self, _v):
        c = self.player.clip
        c.start, c.end = min(self.start_field.value, self.end_field.value - 1), self.end_field.value
        self.player.set_frame(self.player.frame)

    def _sync(self, f):
        self.frame_field.set_value(int(round(f)), emit=False)

    def _sync_play(self, playing):
        self.play_btn.icon = "pause" if playing else "play"
        self.play_btn.invalidate()
