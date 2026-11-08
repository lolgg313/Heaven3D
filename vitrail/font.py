# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Glyph atlas + font cache.

Glyphs are rasterised lazily with pygame.freetype into a single R8 texture
(shelf packed).  Positions are snapped to whole pixels and the atlas is
sampled with NEAREST filtering, so text stays crisp.  Text widths are memoised.
"""
from __future__ import annotations

import pygame
import pygame.freetype

ELLIPSIS_FALLBACK = "..."


class GlyphAtlas:
    def __init__(self, ctx, size: int = 1024):
        self.ctx = ctx
        self.size = size
        self.texture = ctx.texture((size, size), 1, dtype="f1")
        self.texture.filter = (ctx.NEAREST, ctx.NEAREST)
        self.texture.swizzle = "RRRR"
        self.generation = 0
        self.overflowed = False
        self._reset_packer()
        self.texture.write(bytes(size * size))

    def _reset_packer(self):
        self._x = 1
        self._y = 1
        self._row_h = 0

    def reset(self):
        """Throw every glyph away (called when the atlas is full)."""
        self.generation += 1
        self.overflowed = True
        self._reset_packer()

    def alloc(self, w: int, h: int):
        s = self.size
        if w + 2 > s or h + 2 > s:
            return None
        if self._x + w + 1 > s:
            self._x = 1
            self._y += self._row_h + 1
            self._row_h = 0
        if self._y + h + 1 > s:
            return None
        pos = (self._x, self._y)
        self._x += w + 1
        self._row_h = max(self._row_h, h)
        return pos

    def upload(self, x, y, w, h, data: bytes):
        self.texture.write(data, viewport=(x, y, w, h), alignment=1)


class Font:
    """One face at one pixel size."""

    def __init__(self, atlas: GlyphAtlas, path, px: int, bold: bool = False):
        self.atlas = atlas
        self.px = px
        self.ft = pygame.freetype.Font(path, px)
        self.ft.antialiased = True
        self.ft.pad = False
        self.ft.strong = bold
        self.ascender = self.ft.get_sized_ascender(px)
        self.descender = self.ft.get_sized_descender(px)  # negative
        self.line_height = self.ft.get_sized_height(px)
        self._glyphs = {}
        self._widths = {}
        self._runs = {}
        self._gen = atlas.generation

    def _check_gen(self):
        if self._gen != self.atlas.generation:
            self._glyphs.clear()
            self._runs.clear()
            self._gen = self.atlas.generation

    def run(self, text: str):
        """Cached glyph quads relative to (pen 0, baseline 0), pixel snapped:
        a tuple of (dx, dy, w, h, u0, v0, u1, v1)."""
        r = self._runs.get(text)
        if r is not None and self._gen == self.atlas.generation:
            return r
        self._check_gen()
        glyph = self.glyph
        pen = 0.0
        out = []
        for ch in text:
            adv, bx, by, w, h, u0, v0, u1, v1 = glyph(ch)
            if w:
                out.append((round(pen) + bx, -by, w, h, u0, v0, u1, v1))
            pen += adv
        r = tuple(out)
        if len(self._runs) > 4096:
            self._runs.clear()
        self._runs[text] = r
        return r

    def glyph(self, ch):
        """-> (advance, bearing_x, bearing_y, w, h, u0, v0, u1, v1)"""
        g = self._glyphs.get(ch)
        if g is not None:
            return g
        self._check_gen()
        metrics = self.ft.get_metrics(ch)
        m = metrics[0] if metrics else None
        if m is None:
            if ch != "?":
                g = self.glyph("?")
                self._glyphs[ch] = g
                return g
            m = (0, 0, 0, 0, self.px * 0.5, 0)
        advance = float(m[4])
        w = h = bx = by = 0
        uv = (0.0, 0.0, 0.0, 0.0)
        if not ch.isspace():
            surf, rect = self.ft.render(ch, (255, 255, 255, 255))
            w, h = surf.get_size()
            bx, by = rect.x, rect.y
            if w and h:
                pos = self.atlas.alloc(w, h)
                if pos is None:
                    self.atlas.reset()
                    self._check_gen()
                    pos = self.atlas.alloc(w, h)
                if pos is not None:
                    data = pygame.image.tobytes(surf, "RGBA")[3::4]
                    self.atlas.upload(pos[0], pos[1], w, h, data)
                    s = self.atlas.size
                    uv = (pos[0] / s, pos[1] / s, (pos[0] + w) / s, (pos[1] + h) / s)
                else:
                    w = h = 0
        g = (advance, bx, by, w, h) + uv
        self._glyphs[ch] = g
        return g

    def width(self, text: str) -> float:
        w = self._widths.get(text)
        if w is None:
            glyph = self.glyph
            w = 0.0
            for ch in text:
                w += glyph(ch)[0]
            if len(self._widths) > 8192:
                self._widths.clear()
            self._widths[text] = w
        return w

    def prefix_widths(self, text: str):
        """Cumulative x offset before each character (len(text)+1 values)."""
        out = [0.0]
        x = 0.0
        for ch in text:
            x += self.glyph(ch)[0]
            out.append(x)
        return out

    def quads(self, text: str, x: float, baseline: float):
        """Yield screen quads for ``text`` with pen starting at (x, baseline)."""
        glyph = self.glyph
        pen = x
        by_base = round(baseline)
        for ch in text:
            adv, bx, by, w, h, u0, v0, u1, v1 = glyph(ch)
            if w:
                yield (round(pen) + bx, by_base - by, w, h, u0, v0, u1, v1)
            pen += adv

    def elide(self, text: str, max_w: float) -> str:
        if self.width(text) <= max_w:
            return text
        ell = "…" if self.ft.get_metrics("…")[0] else ELLIPSIS_FALLBACK
        ew = self.width(ell)
        if ew > max_w:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:  # binary search the longest prefix that fits
            mid = (lo + hi + 1) // 2
            if self.width(text[:mid]) + ew <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + ell


class FontCache:
    def __init__(self, atlas: GlyphAtlas, path=None):
        if not pygame.freetype.get_init():
            pygame.freetype.init()
        self.atlas = atlas
        self.path = path
        self._fonts = {}

    def get(self, px: int, bold: bool = False) -> Font:
        key = (px, bold)
        f = self._fonts.get(key)
        if f is None:
            f = self._fonts[key] = Font(self.atlas, self.path, px, bold)
        return f
