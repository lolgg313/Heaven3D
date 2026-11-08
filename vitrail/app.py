# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""App: window + ModernGL context + an idle-friendly main loop.

When nothing changes the loop blocks in ``pygame.event.wait`` and uses no
CPU; a frame is rendered only after input, a timer (tooltips) or an explicit
``request_redraw``.  Set ``continuous=True`` for games that animate every
frame -- the UI then costs one cached draw call per frame.
"""
from __future__ import annotations

import sys
import time
import warnings

import moderngl
import pygame

from .core import UI, Signal
from .layout import Canvas, Column, ScrollArea
from .theme import Theme


class App:
    def __init__(self, width=960, height=640, title="Vitrail", *, theme: Theme | None = None,
                 vsync=True, resizable=True, continuous=False, fps=0, root="scroll", samples=0, icon=True):
        pygame.init()
        gl = pygame.display.gl_set_attribute
        gl(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        gl(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        gl(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        if sys.platform == "darwin":
            gl(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)
        if samples:
            gl(pygame.GL_MULTISAMPLEBUFFERS, 1)
            gl(pygame.GL_MULTISAMPLESAMPLES, samples)
        if icon:
            try:
                from .logo import logo_surface
                pygame.display.set_icon(icon if isinstance(icon, pygame.Surface) else logo_surface(64))
            except Exception:
                pass
        flags = pygame.OPENGL | pygame.DOUBLEBUF | (pygame.RESIZABLE if resizable else 0)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")   # "vsync not available" is not an error
                pygame.display.set_mode((width, height), flags, vsync=1 if vsync else 0)
        except pygame.error:
            pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption(title)
        self.ctx = moderngl.create_context()
        self.theme = theme or Theme()
        self.ui = UI(self.ctx, (width, height), self.theme)
        self.continuous = continuous
        self.fps = fps
        self.running = False
        self.clock = pygame.time.Clock()
        self.on_event = Signal()   # (event) -- events the UI did not consume
        self.on_frame = Signal()   # (dt) -- every frame in continuous mode
        self.on_draw = Signal()    # (ctx) -- draw your scene; UI is drawn on top
        if root == "canvas":
            self.root = Canvas()
            self.ui.set_root(self.root)
        elif root == "fill":   # a Column exactly the window size; give children fill=True
            self.root = Column(spacing=0)
            self.ui.set_root(self.root)
        else:
            self.root = Column(padding=self.theme.padding, spacing=self.theme.spacing + 3)
            self.scroll = ScrollArea(self.root)
            self.ui.set_root(self.scroll)

    @property
    def clear_color(self):
        return self.theme.background

    def request_redraw(self):
        self.ui.invalidate()

    def quit(self):
        self.running = False

    def render(self):
        ctx = self.ctx
        ctx.screen.use()
        w, h = self.ui.size
        ctx.viewport = (0, 0, w, h)
        c = self.ui.theme.background
        ctx.clear(c[0], c[1], c[2], 1.0)
        self.on_draw.emit(ctx)
        self.ui.draw()
        pygame.display.flip()

    def run(self):
        self.running = True
        last = time.perf_counter()
        ui = self.ui
        while self.running:
            events = []
            if not self.continuous and not ui.dirty:
                timeout = ui.next_timeout()
                if timeout is None:
                    e = pygame.event.wait()
                else:
                    e = pygame.event.wait(max(1, int(timeout * 1000) + 1))
                if e.type != pygame.NOEVENT:
                    events.append(e)
            events.extend(pygame.event.get())
            for e in events:
                if e.type == pygame.QUIT:
                    self.running = False
                    continue
                if e.type == pygame.VIDEORESIZE:
                    ui.resize(e.w, e.h)
                if not ui.handle_event(e):
                    self.on_event.emit(e)
            now = time.perf_counter()
            dt, last = now - last, now
            ui.update(now)
            if self.continuous:
                self.on_frame.emit(dt)
            if ui.dirty or self.continuous:
                self.render()
            if self.continuous and self.fps:
                self.clock.tick(self.fps)
        pygame.quit()
