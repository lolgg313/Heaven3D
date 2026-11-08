# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Colour images: an RGBA atlas next to the glyph atlas (same draw call).

    ui.images.get("Textures/brick.png", 128)        # -> ImageRef or None while loading
    ui.image(dl, "Textures/brick.png", x, y, w, h)   # draws it (fit / fill) once loaded
    Image("logo.png", width=128)                     # widget

Files are decoded lazily with a per-frame budget so a folder of 500
textures never stalls the UI; thumbnails appear over the next frames.
"""
from __future__ import annotations

import os
import time

import pygame

from .core import Widget


class ImageRef:
    __slots__ = ("w", "h", "uv")

    def __init__(self, w, h, uv):
        self.w, self.h, self.uv = w, h, uv


class ImageCache:
    BUDGET = 0.012        # seconds of decoding allowed per frame

    def __init__(self, ctx, size=2048):
        self.ctx = ctx
        self.size = size
        self.texture = ctx.texture((size, size), 4, dtype="f1")
        self.texture.filter = (ctx.LINEAR, ctx.LINEAR)
        self.texture.write(bytes(size * size * 4))
        self.generation = 0
        self._entries = {}
        self._failed = set()
        self._pending = False
        self._spent = 0.0
        self._frame = -1
        self.on_ready = None      # callback when deferred work remains
        self._reset_packer()

    def _reset_packer(self):
        self._x = self._y = 1
        self._row = 0

    def reset(self):
        self.generation += 1
        self._entries.clear()
        self._reset_packer()

    def _alloc(self, w, h):
        if self._x + w + 2 > self.size:
            self._x = 1
            self._y += self._row + 2
            self._row = 0
        if self._y + h + 2 > self.size:
            return None
        pos = (self._x, self._y)
        self._x += w + 2
        self._row = max(self._row, h)
        return pos

    def begin_frame(self, frame_no):
        if frame_no != self._frame:
            self._frame = frame_no
            self._spent = 0.0

    def add_surface(self, key, surf, max_px=None):
        """Upload a pygame Surface under ``key`` (scaled to fit ``max_px``)."""
        w, h = surf.get_size()
        if surf.get_bitsize() != 32 or not surf.get_flags() & pygame.SRCALPHA:
            tmp = pygame.Surface((w, h), pygame.SRCALPHA, 32)
            tmp.blit(surf, (0, 0))
            surf = tmp
        if max_px and max(w, h) > max_px:
            k = max_px / max(w, h)
            w, h = max(1, round(w * k)), max(1, round(h * k))
            surf = pygame.transform.smoothscale(surf, (w, h))
        w, h = min(w, self.size - 4), min(h, self.size - 4)
        pos = self._alloc(w, h)
        if pos is None:
            self.reset()
            pos = self._alloc(w, h)
        data = pygame.image.tobytes(surf.subsurface((0, 0, w, h)), "RGBA")
        self.texture.write(data, viewport=(pos[0], pos[1], w, h))
        s = self.size
        ref = ImageRef(w, h, (pos[0] / s, pos[1] / s, (pos[0] + w) / s, (pos[1] + h) / s))
        self._entries[key] = ref
        return ref

    def get(self, path, max_px=256):
        """Return an ImageRef, or None if not (yet) available."""
        key = (path, max_px)
        ref = self._entries.get(key)
        if ref is not None or key in self._failed:
            return ref
        if self._spent > self.BUDGET:
            self._pending = True
            if self.on_ready:
                self.on_ready()
            return None
        t0 = time.perf_counter()
        try:
            surf = pygame.image.load(path)
            ref = self.add_surface(key, surf, max_px)
        except (pygame.error, OSError, ValueError, FileNotFoundError):
            self._failed.add(key)
            ref = None
        self._spent += time.perf_counter() - t0
        return ref

    def forget(self, path):
        for k in [k for k in self._entries if k[0] == path]:
            del self._entries[k]
        self._failed = {k for k in self._failed if k[0] != path}


def fit_rect(iw, ih, x, y, w, h, mode="fit"):
    """Fit (letterbox) or fill (crop not supported -> fit) an iw x ih image in a box."""
    if iw <= 0 or ih <= 0:
        return (x, y, w, h)
    k = min(w / iw, h / ih) if mode == "fit" else max(w / iw, h / ih)
    dw, dh = iw * k, ih * k
    return (x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)


class Image(Widget):
    """Shows an image file (or a pygame Surface)."""

    def __init__(self, source=None, *, max_px=512, radius=0, tint=(1, 1, 1, 1), **kw):
        super().__init__(**kw)
        self.source = source
        self.max_px = max_px
        self.radius = radius
        self.tint = tint
        self._key = None

    def _ref(self, ui):
        src = self.source
        if src is None:
            return None
        if isinstance(src, pygame.Surface):
            key = ("surface", id(src), self.max_px)
            ref = ui.images._entries.get(key)
            if ref is None:
                ref = ui.images.add_surface(key, src, self.max_px)
            return ref
        return ui.images.get(os.fspath(src), self.max_px)

    def preferred_size(self, ui):
        ref = self._ref(ui)
        if ref is None:
            return (64 * ui.theme.scale, 64 * ui.theme.scale)
        return (ref.w * ui.theme.scale, ref.h * ui.theme.scale)

    def draw(self, dl, ui):
        ref = self._ref(ui)
        if ref is None:
            dl.box(self.x, self.y, self.w, self.h, ui.theme.field, radius=self.radius)
            ui.request_repaint(self)
            return
        x, y, w, h = fit_rect(ref.w, ref.h, self.x, self.y, self.w, self.h)
        dl.image(x, y, w, h, ref.uv, self.tint, self.radius)
