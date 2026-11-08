# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Radial (pie) menu: pick by direction.

Up to eight items are laid out in the order W, E, S, N, NW, NE, SW, SE so the
first two choices are always the easiest flicks.  Hold the opening key, flick
toward an item and release to pick it -- or tap the key and click.
"""
from __future__ import annotations

import math
import time

import pygame

from .core import Popup
from .theme import shade

# screen angles (degrees, y down) for slots 0..7
_SLOT_ANGLES = [180, 0, 90, 270, 225, 315, 135, 45]


class PieMenu(Popup):
    close_on_outside = False

    def __init__(self, items, on_pick=None, *, title="", radius=110, release_key=None):
        super().__init__()
        norm = []
        for it in list(items)[:8]:
            if it is None:
                norm.append(None)
            elif isinstance(it, tuple):
                norm.append((str(it[0]), it[1] if len(it) > 1 else it[0]))
            else:
                norm.append((str(it), it))
        self.items = norm
        self.on_pick = on_pick
        self.title = title
        self.radius = radius
        self.release_key = release_key
        self.center = (0, 0)
        self.highlight = None
        self._opened_at = time.perf_counter()

    @classmethod
    def open(cls, ui, items, on_pick=None, *, at=None, **kw):
        pie = cls(items, on_pick, **kw)
        pie.center = at or ui.mouse
        pie._opened_at = time.perf_counter()
        return ui.open_popup(pie)

    # covers the whole screen so every motion/click reaches us
    def place(self, ui):
        self.arrange(0, 0, ui.size[0], ui.size[1])

    def widget_at(self, px, py):
        return self

    def _slot_pos(self, ui, slot):
        a = math.radians(_SLOT_ANGLES[slot])
        r = self.radius * ui.theme.scale
        return (self.center[0] + math.cos(a) * r, self.center[1] + math.sin(a) * r)

    def _pick_slot(self, ui, px, py):
        dx, dy = px - self.center[0], py - self.center[1]
        if math.hypot(dx, dy) < 14 * ui.theme.scale:
            return None
        ang = math.degrees(math.atan2(dy, dx)) % 360
        best, best_d = None, 999.0
        for slot, it in enumerate(self.items):
            if it is None:
                continue
            d = abs((ang - _SLOT_ANGLES[slot] + 180) % 360 - 180)
            if d < best_d:
                best, best_d = slot, d
        return best

    def _item_rect(self, ui, slot):
        s = ui.theme.scale
        label = self.items[slot][0]
        w = ui.text_width(label) + 24 * s
        h = ui.theme.h + 4 * s
        cx, cy = self._slot_pos(ui, slot)
        a = _SLOT_ANGLES[slot]
        if a in (0, 315, 45):        # right side: grow rightwards
            x = cx - 10 * s
        elif a in (180, 225, 135):   # left side: grow leftwards
            x = cx - w + 10 * s
        else:
            x = cx - w / 2
        return (round(x), round(cy - h / 2), round(w), round(h))

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        cx, cy = self.center
        r0 = 18 * s
        dl.box(cx - r0, cy - r0, 2 * r0, 2 * r0, (0, 0, 0, 0.35), border=t.outline, radius=r0, border_width=2 * s)
        mx, my = ui.mouse
        if math.hypot(mx - cx, my - cy) > 4:
            a = math.atan2(my - cy, mx - cx)
            dx, dy = math.cos(a) * r0, math.sin(a) * r0
            d = 6 * s
            dl.box(cx + dx - d / 2, cy + dy - d / 2, d, d, t.accent, radius=d / 2)
        if self.title:
            tw = ui.text_width(self.title, bold=True) + 20 * s
            ty = cy - self.radius * s - 2.2 * t.h
            dl.box(cx - tw / 2, ty, tw, t.h, t.menu, border=t.outline, radius=t.radius * s)
            ui.text(dl, self.title, cx - tw / 2, ty, tw, t.h, t.text_dim, "center", bold=True)
        for slot, it in enumerate(self.items):
            if it is None:
                continue
            x, y, w, h = self._item_rect(ui, slot)
            hl = slot == self.highlight
            base = t.menu_hover if hl else t.button
            dl.shadow(x, y, w, h, t.shadow, t.radius * s, 6 * s)
            dl.box(x, y, w, h, shade(base, t.emboss), shade(base, -t.emboss), t.outline, t.radius * s)
            ui.text(dl, it[0], x, y, w, h, t.text_on_accent if hl else t.text, "center")

    def on_mouse_move(self, ui, ev):
        hl = self._pick_slot(ui, ev.x, ev.y)
        if hl != self.highlight:
            self.highlight = hl
        ui.invalidate()   # direction indicator follows the pointer

    def on_mouse_down(self, ui, ev):
        return True

    def on_mouse_up(self, ui, ev):
        slot = self._pick_slot(ui, ev.x, ev.y)
        if slot is None:
            ui.close_popup(self)
        else:
            self.pick(ui, slot)

    def on_key_up(self, ui, e):
        if self.release_key is not None and e.key == self.release_key:
            held = time.perf_counter() - self._opened_at
            if self.highlight is not None and held > 0.25:
                self.pick(ui, self.highlight)
            return True
        return False

    def on_key(self, ui, e):
        if pygame.K_1 <= e.key <= pygame.K_8:
            slot = e.key - pygame.K_1
            if slot < len(self.items) and self.items[slot] is not None:
                self.pick(ui, slot)
                return True
        return False

    def pick(self, ui, slot):
        it = self.items[slot]
        ui.close_popup(self)
        if self.on_pick is not None and it is not None:
            v = it[1]
            self.on_pick(v)
