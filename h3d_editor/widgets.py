# SPDX-License-Identifier: MIT
"""Widgets the Heaven3D editor adds on top of Vitrail.

* PassColumn / PassRow -- layout containers that are transparent to the
  mouse where they have no child, so the 3D viewport behind the editor
  gets those clicks.
* Dock / Bar -- opaque panels (the outliner, inspector, toolbar...).
* CodeEditor -- a multi-line Python editor: syntax colours, line numbers,
  auto-indent, selection, clipboard, undo/redo, comment toggling.
* LogView -- the console's scrolling, coloured log.
"""
import keyword
import re
import time

import pygame

from vitrail import Column, Row, Signal, Widget
from vitrail.core import ALT, CTRL, SHIFT
from vitrail.font import FontCache
from vitrail.theme import rgb

IBEAM = pygame.SYSTEM_CURSOR_IBEAM


class PassColumn(Column):
    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        for c in reversed(self.children):
            r = c.widget_at(px, py)
            if r is not None:
                return r
        return None


class PassRow(Row):
    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        for c in reversed(self.children):
            r = c.widget_at(px, py)
            if r is not None:
                return r
        return None


class Dock(Column):
    """An opaque editor panel."""

    def __init__(self, *children, color=None, border=True, **kw):
        kw.setdefault("padding", 4)
        kw.setdefault("spacing", 4)
        super().__init__(*children, **kw)
        self.color = color
        self.border = border

    def draw(self, dl, ui):
        t = ui.theme
        dl.box(self.x, self.y, self.w, self.h, self.color or t.background,
               border=t.outline if self.border else None)
        self.draw_children(dl, ui)


class Bar(Row):
    """An opaque horizontal strip (toolbar, status bar)."""

    def __init__(self, *children, color=None, **kw):
        kw.setdefault("padding", 3)
        kw.setdefault("spacing", 3)
        super().__init__(*children, **kw)
        self.color = color

    def draw(self, dl, ui):
        dl.box(self.x, self.y, self.w, self.h, self.color or ui.theme.panel_header_bg)
        self.draw_children(dl, ui)


# =========================================================================== fonts
_MONO = None


def mono_font(ui, size=None):
    """A monospace face (DejaVu Sans Mono / Consolas / Menlo...) from the
    glyph atlas Vitrail already uses, so code costs no extra draw calls."""
    global _MONO
    if _MONO is None or _MONO[0] is not ui:
        path = None
        try:
            import pygame.font
            if not pygame.font.get_init():
                pygame.font.init()
            path = pygame.font.match_font(
                "dejavusansmono,consolas,menlo,sfmono,liberationmono,cascadiamono,ubuntumono,"
                "couriernew,monospace")
        except Exception:
            path = None
        _MONO = (ui, FontCache(ui.atlas, path))
    px = max(8, round((size or ui.theme.font_size) * ui.theme.scale))
    return _MONO[1].get(px)


# =========================================================================== syntax colours
SYNTAX = {
    "text": rgb("#d7dae0"), "keyword": rgb("#c678dd"), "builtin": rgb("#56b6c2"),
    "string": rgb("#98c379"), "number": rgb("#d19a66"), "comment": rgb("#7f848e"),
    "def": rgb("#61afef"), "decorator": rgb("#e5c07b"), "self": rgb("#e06c75"),
    "gutter": rgb("#5c6370"), "gutter_cur": rgb("#c8ccd4"), "bg": rgb("#1b1d22"),
    "gutter_bg": rgb("#202227"), "line": rgb("#ffffff", 0.045), "sel": rgb("#3b8eea", 0.38),
    "error": rgb("#e05252"), "error_bg": rgb("#e05252", 0.16),
}
_BUILTINS = frozenset(dir(__builtins__) if not isinstance(__builtins__, dict) else __builtins__.keys())
_KW = frozenset(keyword.kwlist) | {"self_", "match", "case"}
_TOKEN = re.compile(r"""
    (?P<comment>\#.*)
  | (?P<string>[rbfuRBFU]{0,2}(?:'''|\"\"\"|'(?:\\.|[^'\\\n])*'?|"(?:\\.|[^"\\\n])*"?))
  | (?P<number>\b(?:0[xXoObB][0-9a-fA-F_]+|\d[\d_]*\.?\d*(?:[eE][-+]?\d+)?j?|\.\d+)\b)
  | (?P<decorator>@[\w.]+)
  | (?P<name>[A-Za-z_]\w*)
  | (?P<other>\s+|.)
""", re.X)


def highlight(line, state):
    """-> (tokens [(text, kind)], state after the line).  ``state`` is None
    or the triple quote that's still open at the start of the line."""
    out = []
    i = 0
    n = len(line)
    if state:
        j = line.find(state)
        if j < 0:
            return [(line, "string")], state
        out.append((line[:j + 3], "string"))
        i = j + 3
        state = None
    prev_kw = None
    while i < n:
        m = _TOKEN.match(line, i)
        if m is None:
            out.append((line[i:], "text"))
            break
        kind = m.lastgroup
        text = m.group()
        if kind == "string":
            q = text.lstrip("rbfuRBFU")
            if q in ("'''", '"""'):
                end = line.find(q, m.end())
                if end < 0:
                    out.append((line[i:], "string"))
                    return out, q
                text = line[i:end + 3]
        elif kind == "name":
            if text in _KW:
                kind = "keyword"
            elif prev_kw in ("def", "class"):
                kind = "def"
            elif text in ("self", "cls"):
                kind = "self"
            elif text in _BUILTINS:
                kind = "builtin"
            else:
                kind = "text"
            prev_kw = text if text in ("def", "class") else (prev_kw if text.isspace() else None)
        elif kind == "other":
            kind = "text"
            if not text.isspace():
                prev_kw = None
        out.append((text, kind))
        i += len(text)
    # merge neighbours of the same kind: fewer, longer (cached) glyph runs
    merged = []
    for t, k in out:
        if merged and merged[-1][1] == k:
            merged[-1] = (merged[-1][0] + t, k)
        else:
            merged.append((t, k))
    return merged, state


# =========================================================================== code editor
class CodeEditor(Widget):
    """Multi-line Python editor.

    Signals: ``changed()`` on every edit, ``saved()`` on Ctrl+S.
    """

    focusable = True
    wants_text_input = True
    hover_redraw = False
    TAB = "    "

    def __init__(self, text="", *, font_size=13, read_only=False, **kw):
        kw.setdefault("height", 200)
        super().__init__(**kw)
        self.cursor = IBEAM
        self.font_size = font_size
        self.read_only = read_only
        self.lines = [""]
        self.row = self.col = 0
        self.anchor = None             # (row, col) where the selection started
        self.top = 0                   # first visible line
        self.scroll_x = 0.0
        self.focused = False
        self.error_line = None         # 1-based line to mark red (syntax / runtime error)
        self.changed = Signal()
        self.saved = Signal()
        self._undo = []
        self._redo = []
        self._last_edit = (None, 0.0)
        self._states = None            # per-line highlighter start states
        self._drag = False
        self._blink = time.perf_counter()
        self._goal_x = None
        self.set_text(text)

    # -- text model ------------------------------------------------------------------
    @property
    def text(self):
        return "\n".join(self.lines)

    def set_text(self, text, *, keep_view=False):
        self.lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", self.TAB).split("\n")
        if not keep_view:
            self.row = self.col = 0
            self.top = 0
            self.scroll_x = 0.0
            self._undo.clear()
            self._redo.clear()
        else:
            self.row = min(self.row, len(self.lines) - 1)
            self.col = min(self.col, len(self.lines[self.row]))
        self.anchor = None
        self._states = None
        self.invalidate()

    def _snapshot(self):
        return (list(self.lines), self.row, self.col)

    def _push_undo(self, kind):
        now = time.perf_counter()
        lk, lt = self._last_edit
        if kind == "type" and lk == "type" and now - lt < 1.0:
            self._last_edit = (kind, now)
            return
        self._last_edit = (kind, now)
        self._undo.append(self._snapshot())
        if len(self._undo) > 400:
            del self._undo[0]
        self._redo.clear()

    def _restore(self, snap):
        self.lines, self.row, self.col = list(snap[0]), snap[1], snap[2]
        self.anchor = None
        self._states = None
        self._edited()

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())
            self._last_edit = (None, 0.0)

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self._last_edit = (None, 0.0)

    def _edited(self):
        self._states = None
        self.error_line = None
        self._blink = time.perf_counter()
        self._ensure_visible()
        self.invalidate()
        self.changed.emit()

    # -- selection ----------------------------------------------------------------------
    def selection(self):
        """((r0, c0), (r1, c1)) ordered, or None."""
        if self.anchor is None or self.anchor == (self.row, self.col):
            return None
        a, b = self.anchor, (self.row, self.col)
        return (a, b) if a <= b else (b, a)

    def selected_text(self):
        s = self.selection()
        if s is None:
            return ""
        (r0, c0), (r1, c1) = s
        if r0 == r1:
            return self.lines[r0][c0:c1]
        return "\n".join([self.lines[r0][c0:]] + self.lines[r0 + 1:r1] + [self.lines[r1][:c1]])

    def _delete_selection(self):
        s = self.selection()
        if s is None:
            return False
        (r0, c0), (r1, c1) = s
        self.lines[r0:r1 + 1] = [self.lines[r0][:c0] + self.lines[r1][c1:]]
        self.row, self.col = r0, c0
        self.anchor = None
        return True

    def insert(self, text):
        if self.read_only:
            return
        self._push_undo("type" if (len(text) == 1 and text not in "\n") else "paste")
        self._delete_selection()
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", self.TAB)
        line = self.lines[self.row]
        head, tail = line[:self.col], line[self.col:]
        parts = text.split("\n")
        if len(parts) == 1:
            self.lines[self.row] = head + text + tail
            self.col += len(text)
        else:
            new = [head + parts[0]] + parts[1:-1] + [parts[-1] + tail]
            self.lines[self.row:self.row + 1] = new
            self.row += len(parts) - 1
            self.col = len(parts[-1])
        self._goal_x = None
        self._edited()

    def select_all(self):
        self.anchor = (0, 0)
        self.row = len(self.lines) - 1
        self.col = len(self.lines[-1])
        self.invalidate()

    def goto_line(self, line):
        self.row = max(0, min(len(self.lines) - 1, int(line) - 1))
        self.col = len(self.lines[self.row]) - len(self.lines[self.row].lstrip())
        self.anchor = None
        self._ensure_visible(center=True)
        self.invalidate()

    # -- geometry -------------------------------------------------------------------------
    def _font(self, ui):
        return mono_font(ui, self.font_size)

    def _metrics(self, ui):
        f = self._font(ui)
        lh = round(f.line_height * 1.18)
        gw = f.width("9") * max(3, len(str(len(self.lines)))) + 18 * ui.theme.scale
        return f, lh, gw

    def visible_lines(self, ui=None):
        ui = ui or self.ui
        if ui is None:
            return 20
        return max(1, int(self.h // self._metrics(ui)[1]))

    def _ensure_visible(self, center=False):
        ui = self.ui
        if ui is None or self.h <= 0:
            return
        n = self.visible_lines(ui)
        if center:
            self.top = max(0, self.row - n // 2)
        elif self.row < self.top:
            self.top = self.row
        elif self.row >= self.top + n:
            self.top = self.row - n + 1
        f, lh, gw = self._metrics(ui)
        cx = f.width(self.lines[self.row][:self.col])
        vw = self.w - gw - 12
        if cx - self.scroll_x > vw:
            self.scroll_x = cx - vw + 40
        elif cx < self.scroll_x:
            self.scroll_x = max(0.0, cx - 40)

    def _pos_at(self, ui, px, py):
        f, lh, gw = self._metrics(ui)
        r = int((py - self.y - 2) // lh) + self.top
        r = max(0, min(len(self.lines) - 1, r))
        x = px - (self.x + gw + 6) + self.scroll_x
        line = self.lines[r]
        pw = f.prefix_widths(line)
        c = 0
        while c < len(line) and (pw[c] + pw[c + 1]) * 0.5 < x:
            c += 1
        return r, c

    def preferred_size(self, ui):
        return (240 * ui.theme.scale, 120 * ui.theme.scale)

    # -- drawing ------------------------------------------------------------------------------
    def _line_states(self):
        if self._states is None or len(self._states) != len(self.lines):
            st, out = None, []
            for ln in self.lines:
                out.append(st)
                if st or "'''" in ln or '"""' in ln:
                    _t, st = highlight(ln, st)
            self._states = out
        return self._states

    def draw(self, dl, ui):
        f, lh, gw = self._metrics(ui)
        x, y, w, h = self.x, self.y, self.w, self.h
        dl.box(x, y, w, h, SYNTAX["bg"], border=ui.theme.accent if self.focused else ui.theme.outline)
        dl.box(x + 1, y + 1, gw, h - 2, SYNTAX["gutter_bg"])
        states = self._line_states()
        n = self.visible_lines(ui) + 1
        asc = f.ascender
        base_off = (lh - (f.ascender - f.descender)) * 0.5 + asc
        sel = self.selection()
        tx0 = x + gw + 6 - self.scroll_x
        dl.push_clip(x + 1, y + 1, w - 2, h - 2)
        for i in range(self.top, min(len(self.lines), self.top + n)):
            ly = y + 2 + (i - self.top) * lh
            line = self.lines[i]
            if self.error_line == i + 1:
                dl.box(x + 1, ly, w - 2, lh, SYNTAX["error_bg"])
            elif i == self.row and self.focused:
                dl.box(x + gw + 1, ly, w - gw - 2, lh, SYNTAX["line"])
            num = str(i + 1)
            col = SYNTAX["error"] if self.error_line == i + 1 else (
                SYNTAX["gutter_cur"] if i == self.row else SYNTAX["gutter"])
            dl.text_run(f.run(num), round(x + gw - 8 - f.width(num)), round(ly + base_off), col)
            dl.push_clip(x + gw + 2, y + 1, w - gw - 4, h - 2)
            if sel is not None:
                (r0, c0), (r1, c1) = sel
                if r0 <= i <= r1:
                    a = c0 if i == r0 else 0
                    b = c1 if i == r1 else len(line)
                    sx0 = tx0 + f.width(line[:a])
                    sx1 = tx0 + f.width(line[:b]) + (0 if i == r1 else f.width(" "))
                    dl.box(sx0, ly, max(2.0, sx1 - sx0), lh, SYNTAX["sel"])
            if line:
                toks, _st = highlight(line, states[i])
                px = tx0
                for text, kind in toks:
                    tw = f.width(text)
                    if px + tw >= x + gw and px <= x + w:
                        if not text.isspace():
                            dl.text_run(f.run(text), round(px), round(ly + base_off), SYNTAX.get(kind, SYNTAX["text"]))
                    px += tw
            if self.focused and i == self.row:
                on = int((time.perf_counter() - self._blink) * 1.9) % 2 == 0
                if on:
                    cx = tx0 + f.width(line[:self.col])
                    dl.box(round(cx), ly + 2, max(1, round(1.5 * ui.theme.scale)), lh - 4, ui.theme.caret)
            dl.pop_clip()
        # scrollbar
        total = len(self.lines)
        vis = self.visible_lines(ui)
        if total > vis:
            th = max(18, h * vis / total)
            ty = y + (h - th) * (self.top / max(1, total - vis))
            dl.box(x + w - 7, ty, 5, th, ui.theme.scrollbar, radius=2.5)
        dl.pop_clip()

    # -- caret blink: keep repainting while focused ---------------------------------------------
    def tick(self, ui):
        if self.focused:
            on = int((time.perf_counter() - self._blink) * 1.9) % 2 == 0
            if on != getattr(self, "_blink_on", None):
                self._blink_on = on
                self.invalidate()

    # -- input ------------------------------------------------------------------------------------
    def on_focus(self, ui, focused):
        self.focused = focused
        self._blink = time.perf_counter()
        self.invalidate()

    def on_mouse_down(self, ui, ev):
        if ev.button not in (1,):
            return ev.button == 3
        ui.set_focus(self)
        r, c = self._pos_at(ui, ev.x, ev.y)
        if ev.clicks >= 3:
            self.anchor = (r, 0)
            self.row, self.col = r, len(self.lines[r])
        elif ev.clicks == 2:
            line = self.lines[r]
            a = b = c
            while a > 0 and (line[a - 1].isalnum() or line[a - 1] == "_"):
                a -= 1
            while b < len(line) and (line[b].isalnum() or line[b] == "_"):
                b += 1
            self.anchor = (r, a)
            self.row, self.col = r, b
        else:
            if ev.shift:
                if self.anchor is None:
                    self.anchor = (self.row, self.col)
            else:
                self.anchor = (r, c)
            self.row, self.col = r, c
        self._drag = True
        self._goal_x = None
        self._blink = time.perf_counter()
        self.invalidate()
        return True

    def on_mouse_move(self, ui, ev):
        if self._drag and ui.active is self:
            r, c = self._pos_at(ui, ev.x, ev.y)
            if (r, c) != (self.row, self.col):
                self.row, self.col = r, c
                self._ensure_visible()
                self.invalidate()
        return True

    def on_mouse_up(self, ui, ev):
        self._drag = False
        return True

    def on_wheel(self, ui, dx, dy):
        n = self.visible_lines(ui)
        self.top = max(0, min(max(0, len(self.lines) - n // 2), self.top - int(dy) * 3))
        if dx:
            self.scroll_x = max(0.0, self.scroll_x - dx * 30)
        self.invalidate()
        return True

    def on_text(self, ui, text):
        if self.read_only or not self.focused:
            return False
        self.insert(text)
        return True

    def _move(self, r, c, extend):
        if extend:
            if self.anchor is None:
                self.anchor = (self.row, self.col)
        else:
            self.anchor = None
        self.row = max(0, min(len(self.lines) - 1, r))
        self.col = max(0, min(len(self.lines[self.row]), c))
        self._blink = time.perf_counter()
        self._ensure_visible()
        self.invalidate()

    def _word_left(self):
        line, c = self.lines[self.row], self.col
        if c == 0:
            return (self.row - 1, len(self.lines[self.row - 1])) if self.row > 0 else (0, 0)
        while c > 0 and line[c - 1] == " ":
            c -= 1
        if c > 0 and (line[c - 1].isalnum() or line[c - 1] == "_"):
            while c > 0 and (line[c - 1].isalnum() or line[c - 1] == "_"):
                c -= 1
        elif c > 0:
            c -= 1
        return self.row, c

    def _word_right(self):
        line, c = self.lines[self.row], self.col
        if c >= len(line):
            return (self.row + 1, 0) if self.row < len(self.lines) - 1 else (self.row, c)
        if line[c].isalnum() or line[c] == "_":
            while c < len(line) and (line[c].isalnum() or line[c] == "_"):
                c += 1
        else:
            c += 1
        while c < len(line) and line[c] == " ":
            c += 1
        return self.row, c

    def _sel_rows(self):
        s = self.selection()
        if s is None:
            return self.row, self.row
        (r0, _c0), (r1, c1) = s
        if c1 == 0 and r1 > r0:
            r1 -= 1
        return r0, r1

    def _indent(self, back):
        r0, r1 = self._sel_rows()
        self._push_undo("indent")
        for r in range(r0, r1 + 1):
            ln = self.lines[r]
            if back:
                k = len(ln) - len(ln.lstrip(" "))
                cut = min(k, 4) if k % 4 == 0 else k % 4
                self.lines[r] = ln[cut:]
                if r == self.row:
                    self.col = max(0, self.col - cut)
                if self.anchor and self.anchor[0] == r:
                    self.anchor = (r, max(0, self.anchor[1] - cut))
            else:
                self.lines[r] = self.TAB + ln
                if r == self.row:
                    self.col += 4
                if self.anchor and self.anchor[0] == r:
                    self.anchor = (r, self.anchor[1] + 4)
        self._edited()

    def toggle_comment(self):
        r0, r1 = self._sel_rows()
        rows = [r for r in range(r0, r1 + 1) if self.lines[r].strip()]
        if not rows:
            return
        self._push_undo("comment")
        uncomment = all(self.lines[r].lstrip().startswith("#") for r in rows)
        ind = min(len(self.lines[r]) - len(self.lines[r].lstrip()) for r in rows)
        for r in rows:
            ln = self.lines[r]
            if uncomment:
                k = ln.index("#")
                cut = 2 if ln[k + 1:k + 2] == " " else 1
                self.lines[r] = ln[:k] + ln[k + cut:]
            else:
                self.lines[r] = ln[:ind] + "# " + ln[ind:]
        self.col = min(self.col, len(self.lines[self.row]))
        self._edited()

    def on_key(self, ui, e):
        if not self.focused:
            return False
        k, mod = e.key, e.mod
        ctrl, shift = bool(mod & CTRL), bool(mod & SHIFT)
        ro = self.read_only
        if ctrl:
            if k == pygame.K_s:
                self.saved.emit()
            elif k == pygame.K_z:
                if not ro:
                    (self.redo if shift else self.undo)()
            elif k == pygame.K_y:
                if not ro:
                    self.redo()
            elif k == pygame.K_a:
                self.select_all()
            elif k in (pygame.K_c, pygame.K_x):
                t = self.selected_text() or (self.lines[self.row] + "\n")
                ui.clipboard.put(t)
                if k == pygame.K_x and not ro:
                    self._push_undo("cut")
                    if not self._delete_selection():
                        if len(self.lines) > 1:
                            del self.lines[self.row]
                            self.row = min(self.row, len(self.lines) - 1)
                        else:
                            self.lines[0] = ""
                        self.col = 0
                    self._edited()
            elif k == pygame.K_v:
                if not ro:
                    self.insert(ui.clipboard.get())
            elif k == pygame.K_d:
                if not ro:
                    self._push_undo("dup")
                    self.lines.insert(self.row + 1, self.lines[self.row])
                    self.row += 1
                    self._edited()
            elif k in (pygame.K_SLASH, pygame.K_KP_DIVIDE):
                if not ro:
                    self.toggle_comment()
            elif k == pygame.K_LEFT:
                self._move(*self._word_left(), shift)
            elif k == pygame.K_RIGHT:
                self._move(*self._word_right(), shift)
            elif k == pygame.K_HOME:
                self._move(0, 0, shift)
            elif k == pygame.K_END:
                self._move(len(self.lines) - 1, len(self.lines[-1]), shift)
            else:
                return False       # Ctrl+P, F5... reach the editor's shortcuts
            return True
        if k == pygame.K_ESCAPE:
            if self.anchor is not None:
                self.anchor = None
                self.invalidate()
            else:
                ui.set_focus(None)
            return True
        if k in (pygame.K_F1, pygame.K_F2, pygame.K_F3, pygame.K_F4, pygame.K_F5, pygame.K_F6,
                 pygame.K_F7, pygame.K_F8, pygame.K_F9, pygame.K_F10, pygame.K_F11, pygame.K_F12):
            return False
        if k == pygame.K_LEFT:
            s = self.selection()
            if s and not shift:
                self._move(*s[0], False)
            elif self.col > 0:
                self._move(self.row, self.col - 1, shift)
            elif self.row > 0:
                self._move(self.row - 1, len(self.lines[self.row - 1]), shift)
            self._goal_x = None
        elif k == pygame.K_RIGHT:
            s = self.selection()
            if s and not shift:
                self._move(*s[1], False)
            elif self.col < len(self.lines[self.row]):
                self._move(self.row, self.col + 1, shift)
            elif self.row < len(self.lines) - 1:
                self._move(self.row + 1, 0, shift)
            self._goal_x = None
        elif k in (pygame.K_UP, pygame.K_DOWN, pygame.K_PAGEUP, pygame.K_PAGEDOWN):
            f = self._font(ui)
            if self._goal_x is None:
                self._goal_x = f.width(self.lines[self.row][:self.col])
            step = {pygame.K_UP: -1, pygame.K_DOWN: 1}.get(k, (self.visible_lines(ui) - 1)
                                                         * (-1 if k == pygame.K_PAGEUP else 1))
            r = max(0, min(len(self.lines) - 1, self.row + step))
            line = self.lines[r]
            pw = f.prefix_widths(line)
            c = 0
            while c < len(line) and (pw[c] + pw[c + 1]) * 0.5 < self._goal_x:
                c += 1
            gx = self._goal_x
            self._move(r, c, shift)
            self._goal_x = gx
        elif k == pygame.K_HOME:
            line = self.lines[self.row]
            ind = len(line) - len(line.lstrip())
            self._move(self.row, 0 if self.col == ind else ind, shift)
        elif k == pygame.K_END:
            self._move(self.row, len(self.lines[self.row]), shift)
        elif ro:
            return True
        elif k == pygame.K_BACKSPACE:
            self._push_undo("del")
            if not self._delete_selection():
                line = self.lines[self.row]
                if self.col > 0:
                    head = line[:self.col]
                    cut = 1
                    if head.strip() == "" and len(head) >= 4:
                        cut = (len(head) % 4) or 4
                    self.lines[self.row] = line[:self.col - cut] + line[self.col:]
                    self.col -= cut
                elif self.row > 0:
                    prev = self.lines[self.row - 1]
                    self.lines[self.row - 1:self.row + 1] = [prev + line]
                    self.row -= 1
                    self.col = len(prev)
            self._edited()
        elif k == pygame.K_DELETE:
            self._push_undo("del")
            if not self._delete_selection():
                line = self.lines[self.row]
                if self.col < len(line):
                    self.lines[self.row] = line[:self.col] + line[self.col + 1:]
                elif self.row < len(self.lines) - 1:
                    self.lines[self.row:self.row + 2] = [line + self.lines[self.row + 1]]
            self._edited()
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            line = self.lines[self.row]
            ind = line[:len(line) - len(line.lstrip())][:self.col]
            if line[:self.col].rstrip().endswith(":"):
                ind += self.TAB
            self._push_undo("newline")
            self._delete_selection()
            line = self.lines[self.row]
            self.lines[self.row:self.row + 1] = [line[:self.col], ind + line[self.col:].lstrip(" ")]
            self.row += 1
            self.col = len(ind)
            self._edited()
        elif k == pygame.K_TAB:
            if shift or self.selection() is not None:
                self._indent(back=shift)
            else:
                self.insert(" " * (4 - self.col % 4))
        else:
            return not (mod & ALT)     # swallow plain keys: W/E/R/Delete must not reach the 3D view
        return True


# =========================================================================== log view
class LogView(Widget):
    """Scrolling coloured lines (newest at the bottom, follows new output).

    Drag the scrollbar or use the wheel to scroll.  Click / drag (Shift+click
    to extend) selects lines; Ctrl+C copies them (everything when nothing is
    selected), Ctrl+A selects all; right-click for Copy / Copy all / Clear.
    Copies go to the system clipboard, so they paste into any app.
    """

    focusable = True
    COLORS = {"ERROR": rgb("#ff6b6b"), "WARN": rgb("#f0c060"), "DEBUG": rgb("#8a8f98"),
              "INFO": rgb("#d7dae0"), "IN": rgb("#7ab8ff"), "OUT": rgb("#98c379")}
    SB = 10                         # scrollbar hit width (px)

    def __init__(self, *, max_lines=4000, **kw):
        kw.setdefault("height", 120)
        super().__init__(**kw)
        self.lines = []            # (text, color)
        self.max_lines = max_lines
        self.offset = 0            # lines scrolled up from the bottom
        self.filter = ""
        self.sel = None            # (anchor, end) indices into the shown lines
        self._mode = None          # "scrollbar" / "select" while the mouse is down
        self._grab = 0.0
        self.cursor = IBEAM

    # -- content ---------------------------------------------------------------
    def add(self, text, level="INFO"):
        col = self.COLORS.get(level, self.COLORS["INFO"])
        n0 = len(self.lines)
        for ln in str(text).split("\n"):
            self.lines.append((ln, col))
        added = len(self.lines) - n0
        drop = len(self.lines) - self.max_lines
        if drop > 0:
            del self.lines[:drop]
            if self.sel is not None and not self.filter:
                a, b = self.sel[0] - drop, self.sel[1] - drop
                self.sel = (a, b) if max(a, b) >= 0 else None
                if self.sel is not None:
                    self.sel = (max(0, self.sel[0]), max(0, self.sel[1]))
        if self.offset:
            self.offset += added   # keep the view still while reading older lines
        self.invalidate()

    def clear(self):
        self.lines.clear()
        self.offset = 0
        self.sel = None
        self.invalidate()

    def _shown(self):
        if not self.filter:
            return self.lines
        f = self.filter.lower()
        return [l for l in self.lines if f in l[0].lower()]

    def text(self):
        return "\n".join(t for t, _c in self._shown())

    def selected_text(self):
        rows = self._shown()
        if self.sel is None or not rows:
            return ""
        a, b = sorted(self.sel)
        a, b = max(0, a), min(len(rows) - 1, b)
        return "\n".join(t for t, _c in rows[a:b + 1])

    def copy(self, ui, everything=False):
        t = self.text() if (everything or self.sel is None) else self.selected_text()
        ui.clipboard.put(t)
        return t

    # -- geometry ----------------------------------------------------------------
    def _metrics(self, ui):
        f = mono_font(ui, 12)
        lh = round(f.line_height * 1.12)
        n = max(1, int((self.h - 6) // lh))
        return f, lh, n

    def _window(self, ui):
        """(rows, first, last+1, y of the first drawn row, line height, visible rows)"""
        f, lh, n = self._metrics(ui)
        rows = self._shown()
        self.offset = max(0, min(self.offset, max(0, len(rows) - n)))
        end = max(0, len(rows) - self.offset)
        start = max(0, end - n)
        y0 = self.y + 3 + (n - (end - start)) * lh
        return rows, start, end, y0, lh, n

    def _row_at(self, ui, py):
        rows, start, end, y0, lh, n = self._window(ui)
        if not rows:
            return None
        i = start + int((py - y0) // lh)
        return max(0, min(len(rows) - 1, i))

    def _thumb(self, ui):
        rows, start, end, y0, lh, n = self._window(ui)
        if len(rows) <= n:
            return None
        th = max(16, self.h * n / len(rows))
        frac = 1.0 - self.offset / max(1, len(rows) - n)
        return self.y + (self.h - th) * frac, th, len(rows) - n

    def _scroll_to_thumb(self, ui, py):
        t = self._thumb(ui)
        if t is None:
            return
        _ty, th, maxoff = t
        frac = (py - self._grab - self.y) / max(1.0, self.h - th)
        self.offset = int(round((1.0 - max(0.0, min(1.0, frac))) * maxoff))
        self.invalidate()

    def preferred_size(self, ui):
        return (200 * ui.theme.scale, 80 * ui.theme.scale)

    # -- drawing -------------------------------------------------------------------
    def draw(self, dl, ui):
        f, lh, n = self._metrics(ui)
        rows, start, end, y0, lh, n = self._window(ui)
        dl.box(self.x, self.y, self.w, self.h, SYNTAX["bg"],
               border=ui.theme.accent if ui.focus is self else ui.theme.outline)
        base = (lh - (f.ascender - f.descender)) * 0.5 + f.ascender
        sel = sorted(self.sel) if self.sel is not None else None
        dl.push_clip(self.x + 2, self.y + 2, self.w - 4, self.h - 4)
        y = y0
        for i in range(start, end):
            text, col = rows[i]
            if sel is not None and sel[0] <= i <= sel[1]:
                dl.box(self.x + 2, y, self.w - self.SB - 4, lh, SYNTAX["sel"])
            if text:
                dl.text_run(f.run(f.elide(text, self.w - 22)), round(self.x + 6), round(y + base), col)
            y += lh
        dl.pop_clip()
        t = self._thumb(ui)
        if t is not None:
            ty, th, _m = t
            hot = self._mode == "scrollbar" or self.hovered
            dl.box(self.x + self.w - 8, ty, 6, th,
                   ui.theme.scrollbar_hover if hot else ui.theme.scrollbar, radius=3)

    # -- input --------------------------------------------------------------------------
    def on_wheel(self, ui, dx, dy):
        self.offset = max(0, self.offset + int(dy) * 3)
        self.invalidate()
        return True

    def on_mouse_down(self, ui, ev):
        t = self._thumb(ui)
        if ev.button == 1 and t is not None and ev.x >= self.x + self.w - self.SB:
            ty, th, _m = t
            # grab the thumb where it was clicked; a click on the track jumps there
            self._grab = (ev.y - ty) if ty <= ev.y <= ty + th else th * 0.5
            self._mode = "scrollbar"
            self._scroll_to_thumb(ui, ev.y)
            return True
        if ev.button == 1:
            ui.set_focus(self)
            i = self._row_at(ui, ev.y)
            if i is None:
                self.sel = None
            elif ev.shift and self.sel is not None:
                self.sel = (self.sel[0], i)
            else:
                self.sel = (i, i)
            self._mode = "select"
            self.invalidate()
            return True
        if ev.button == 3:
            from vitrail import MenuItem, open_context_menu
            ui.set_focus(self)
            i = self._row_at(ui, ev.y)
            if i is not None and (self.sel is None or not (min(self.sel) <= i <= max(self.sel))):
                self.sel = (i, i)
                self.invalidate()
            open_context_menu(ui, [
                MenuItem("Copy", lambda: self.copy(ui), icon="copy", shortcut="Ctrl+C"),
                MenuItem("Copy all", lambda: self.copy(ui, everything=True), icon="copy"),
                MenuItem("Select all", lambda: self.select_all(), shortcut="Ctrl+A"),
                None,
                MenuItem("Clear", self.clear, icon="trash"),
            ], at=(ev.x, ev.y))
            return True
        return False

    def on_mouse_move(self, ui, ev):
        if self._mode == "scrollbar":
            self._scroll_to_thumb(ui, ev.y)
        elif self._mode == "select" and self.sel is not None:
            # dragging above / below the view scrolls it
            if ev.y < self.y + 4:
                self.offset += 1
            elif ev.y > self.y + self.h - 4:
                self.offset = max(0, self.offset - 1)
            i = self._row_at(ui, ev.y)
            if i is not None and i != self.sel[1]:
                self.sel = (self.sel[0], i)
            self.invalidate()
        return True

    def on_mouse_up(self, ui, ev):
        self._mode = None
        self.invalidate()
        return True

    def select_all(self):
        rows = self._shown()
        self.sel = (0, len(rows) - 1) if rows else None
        self.invalidate()

    def on_key(self, ui, e):
        ctrl = bool(e.mod & CTRL)
        if ctrl and e.key in (pygame.K_c, pygame.K_INSERT):
            self.copy(ui)
            return True
        if ctrl and e.key == pygame.K_a:
            self.select_all()
            return True
        if e.key == pygame.K_ESCAPE and self.sel is not None:
            self.sel = None
            self.invalidate()
            return True
        if e.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
            n = self._metrics(ui)[2]
            self.offset = max(0, self.offset + (n - 1) * (1 if e.key == pygame.K_PAGEUP else -1))
            self.invalidate()
            return True
        if e.key == pygame.K_END:                      # back to the newest line
            self.offset = 0
            self.invalidate()
            return True
        return False
