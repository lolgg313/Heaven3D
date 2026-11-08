# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Built-in vector icon set (original artwork, Apache-2.0 like the rest).

Icons are tiny vector programs on a 24x24 grid.  The first time an icon is
needed at a pixel size it is rasterised (4x supersampled, then filtered down)
into the shared glyph atlas, so drawing an icon afterwards is ONE quad in the
same draw call as everything else, tinted with any colour.

    ui.icon(dl, "folder", x, y, 16, color)
    Button("Save", icon="save")
    register_icon("my-logo", [S((4, 4), (20, 20)), C(12, 12, 6)])

Ops (all coordinates in 24-unit space):
    S(*points, close=False)   stroke polyline (round caps and joins)
    C(cx, cy, r)              stroke circle
    FC(cx, cy, r)             filled circle
    P(*points)                filled polygon
    R(x, y, w, h, radius)     stroke rounded rectangle
    FR(x, y, w, h, radius)    filled rounded rectangle
    A(cx, cy, r, a0, a1)      stroke arc, degrees, 0 = +x, 90 = down
    E(cx, cy, rx, ry)         stroke ellipse
    X(op)                     erase with op's shape (cut-outs), stroked ops erase wider
"""
from __future__ import annotations

import math

import pygame

STROKE = 2.0          # default stroke width in grid units
SUPERSAMPLE = 4


# ----------------------------------------------------------------------------- op builders
def S(*pts, close=False):
    return ("s", tuple(pts), close)


def C(cx, cy, r):
    return ("a", cx, cy, r, 0.0, 360.0)


def A(cx, cy, r, a0, a1):
    return ("a", cx, cy, r, a0, a1)


def E(cx, cy, rx, ry):
    pts = [(cx + rx * math.cos(t), cy + ry * math.sin(t)) for t in (i * math.tau / 48 for i in range(48))]
    return ("s", tuple(pts), True)


def FC(cx, cy, r):
    return ("fc", cx, cy, r)


def P(*pts):
    return ("p", tuple(pts))


def R(x, y, w, h, r=0.0):
    return ("s", tuple(_rrect_pts(x, y, w, h, r)), True)


def FR(x, y, w, h, r=0.0):
    return ("p", tuple(_rrect_pts(x, y, w, h, r)))


def X(op):
    return ("x",) + (op,)


def _rrect_pts(x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(7):
            a = math.radians(a0 + i * 15)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _arc_pts(cx, cy, r, a0, a1):
    n = max(6, int(abs(a1 - a0) / 7.5))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def _bezier(p0, p1, p2, p3, n=16):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def _star(cx, cy, ro, ri, n=5, rot=-90):
    pts = []
    for i in range(n * 2):
        r = ro if i % 2 == 0 else ri
        a = math.radians(rot + i * 180 / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _gear(cx, cy, ro, ri, teeth=8):
    pts = []
    step = 360 / teeth
    for i in range(teeth):
        a = i * step
        for da, r in ((-step * 0.22, ro), (step * 0.22, ro), (step * 0.32, ri), (step * 0.68, ri)):
            t = math.radians(a + da)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def _capsule(cx, cy, length, rad, angle):
    a = math.radians(angle)
    dx, dy = math.cos(a) * length / 2, math.sin(a) * length / 2
    pts = []
    for i in range(13):
        t = a - math.pi / 2 + i * math.pi / 12
        pts.append((cx + dx + rad * math.cos(t), cy + dy + rad * math.sin(t)))
    for i in range(13):
        t = a + math.pi / 2 + i * math.pi / 12
        pts.append((cx - dx + rad * math.cos(t), cy - dy + rad * math.sin(t)))
    return pts


def rotate(ops, deg):
    """Rotate an icon (list of ops) around the grid centre."""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)

    def rp(p):
        x, y = p[0] - 12, p[1] - 12
        return (12 + x * ca - y * sa, 12 + x * sa + y * ca)

    out = []
    for op in ops:
        k = op[0]
        if k == "s":
            out.append(("s", tuple(rp(p) for p in op[1]), op[2]))
        elif k == "p":
            out.append(("p", tuple(rp(p) for p in op[1])))
        elif k == "fc":
            cx, cy = rp((op[1], op[2]))
            out.append(("fc", cx, cy, op[3]))
        elif k == "a":
            cx, cy = rp((op[1], op[2]))
            out.append(("a", cx, cy, op[3], op[4] + deg, op[5] + deg))
        elif k == "x":
            out.append(("x", rotate([op[1]], deg)[0]))
    return out


def mirror(ops):
    """Mirror an icon horizontally."""
    out = []
    for op in ops:
        k = op[0]
        if k == "s":
            out.append(("s", tuple((24 - x, y) for x, y in op[1]), op[2]))
        elif k == "p":
            out.append(("p", tuple((24 - x, y) for x, y in op[1])))
        elif k == "fc":
            out.append(("fc", 24 - op[1], op[2], op[3]))
        elif k == "a":
            out.append(("a", 24 - op[1], op[2], op[3], 180 - op[4], 180 - op[5]))
        elif k == "x":
            out.append(("x", mirror([op[1]])[0]))
    return out


# ----------------------------------------------------------------------------- the icon set
_PAGE = S((14, 2.5), (5.5, 2.5), (5.5, 21.5), (18.5, 21.5), (18.5, 7), (14, 2.5), close=True)
_FOLD = S((14, 2.5), (14, 7), (18.5, 7))
_FOLDER = S((3, 5), (9.5, 5), (11.5, 7.5), (21, 7.5), (21, 19.5), (3, 19.5), close=True)
_SEARCH = [C(10.5, 10.5, 6.5), S((15.3, 15.3), (20.5, 20.5))]
_TRAY = S((4, 16), (4, 20.5), (20, 20.5), (20, 16))
_SPEAKER = S((3.5, 9), (7.5, 9), (12.5, 4.5), (12.5, 19.5), (7.5, 15), (3.5, 15), close=True)
_ARROW_R = [S((4.5, 12), (19.5, 12)), S((13, 5.5), (19.5, 12), (13, 18.5))]
_CHEV_R = [S((9, 5), (16, 12), (9, 19))]
_CIRCLE = C(12, 12, 9.5)


def _sun_rays():
    ops = []
    for i in range(8):
        a = math.radians(i * 45)
        ops.append(S((12 + 6.8 * math.cos(a), 12 + 6.8 * math.sin(a)), (12 + 9.5 * math.cos(a), 12 + 9.5 * math.sin(a))))
    return ops


def _heart():
    pts = []
    for i in range(48):
        t = i * math.tau / 48
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((12 + x * 0.56, 11.2 - y * 0.56))
    return pts


def _eye():
    up = [(2.5 + 19 * u, 12 - 7 * math.sin(math.pi * u)) for u in (i / 16 for i in range(17))]
    lo = [(2.5 + 19 * u, 12 + 7 * math.sin(math.pi * u)) for u in (i / 16 for i in range(16, 0, -1))]
    return S(*(up + lo), close=True)


ICONS = {
    # files & folders
    "file": [_PAGE, _FOLD],
    "file-text": [_PAGE, _FOLD, S((8.5, 12), (15.5, 12)), S((8.5, 15.5), (15.5, 15.5)), S((8.5, 8.5), (10.5, 8.5))],
    "file-code": [_PAGE, _FOLD, S((10.2, 11), (8, 13.5), (10.2, 16)), S((13.8, 11), (16, 13.5), (13.8, 16))],
    "file-image": [_PAGE, _FOLD, C(10, 11.2, 1.6), S((18.5, 17.5), (14.5, 13.5), (7, 21))],
    "file-audio": [_PAGE, _FOLD, S((10.5, 17.5), (10.5, 10.5), (15, 9.5), (15, 16.5)), FC(9, 17.5, 1.7), FC(13.5, 16.5, 1.7)],
    "file-video": [_PAGE, _FOLD, P((9.5, 10.5), (15.5, 14), (9.5, 17.5))],
    "file-archive": [_PAGE, _FOLD, S((11, 4.5), (11, 5.2)), S((11, 7.5), (11, 8.2)), S((11, 10.5), (11, 11.2)), R(9, 13.5, 4, 5, 1)],
    "file-plus": [_PAGE, _FOLD, S((12, 11), (12, 17.5)), S((8.75, 14.25), (15.25, 14.25))],
    "folder": [_FOLDER],
    "folder-open": [S((3, 19.5), (3, 5), (9.5, 5), (11.5, 7.5), (18.5, 7.5), (18.5, 10.5)),
                    S((3, 19.5), (6, 10.5), (22, 10.5), (19, 19.5), close=True)],
    "folder-plus": [_FOLDER, S((12, 10.5), (12, 16.5)), S((9, 13.5), (15, 13.5))],
    "folder-up": [_FOLDER, S((12, 17), (12, 10.5)), S((9, 13.3), (12, 10.3), (15, 13.3))],
    "save": [S((3, 3), (17, 3), (21, 7), (21, 21), (3, 21), close=True), S((7, 3), (7, 8), (15, 8), (15, 3)), R(7, 13, 10, 8)],
    "download": [S((12, 3), (12, 15)), S((7, 10), (12, 15), (17, 10)), _TRAY],
    "upload": [S((12, 15), (12, 3)), S((7, 8), (12, 3), (17, 8)), _TRAY],
    "package": [R(3, 7, 18, 14, 1.5), S((3, 7), (5, 3), (19, 3), (21, 7)), S((12, 3), (12, 7)), S((10, 11), (14, 11))],
    "drive": [R(2.5, 13, 19, 7.5, 2), S((2.5, 13), (5.5, 4), (18.5, 4), (21.5, 13)), FC(17, 16.8, 1.2)],
    "database": [E(12, 5.5, 8, 3), S((4, 5.5), (4, 18.5)), S((20, 5.5), (20, 18.5)), A(12, 18.5, 8, 0, 180),
                 ("s", tuple((12 + 8 * math.cos(t), 12 + 3 * math.sin(t)) for t in (i * math.pi / 16 for i in range(17))), False)],
    # editing
    "close": [S((6, 6), (18, 18)), S((18, 6), (6, 18))],
    "check": [S((4.5, 12.5), (9.5, 17.5), (19.5, 6.5))],
    "plus": [S((12, 5), (12, 19)), S((5, 12), (19, 12))],
    "minus": [S((5, 12), (19, 12))],
    "edit": [S((16, 3.5), (20.5, 8), (8, 20.5), (3.5, 20.5), (3.5, 16), close=True), S((13.5, 6), (18, 10.5))],
    "trash": [S((3.5, 6), (20.5, 6)), S((9, 6), (9, 3.5), (15, 3.5), (15, 6)), S((5.5, 6), (6.5, 21), (17.5, 21), (18.5, 6)),
              S((10, 10), (10, 17)), S((14, 10), (14, 17))],
    "copy": [R(8, 8, 13, 13, 2), S((15.5, 8), (15.5, 3), (3, 3), (3, 15.5), (8, 15.5))],
    "paste": [R(5, 4, 14, 18, 2), S((9, 2.5), (15, 2.5), (15, 6), (9, 6), close=True)],
    "cut": [C(6.5, 17.5, 3), C(17.5, 17.5, 3), S((8.6, 15.4), (18, 3.5)), S((15.4, 15.4), (6, 3.5))],
    "undo": [S((8, 5), (4, 9), (8, 13)), S((4, 9), (14, 9)), A(14, 14, 5, -90, 90), S((14, 19), (9, 19))],
    "redo": [S((16, 5), (20, 9), (16, 13)), S((20, 9), (10, 9)), A(10, 14, 5, 270, 90), S((10, 19), (15, 19))],
    "refresh": [A(12, 12, 8, -40, 250), S((19.5, 2.8), (18.2, 6.9), (14.2, 6.2))],
    "search": _SEARCH,
    "zoom-in": _SEARCH + [S((10.5, 7.5), (10.5, 13.5)), S((7.5, 10.5), (13.5, 10.5))],
    "zoom-out": _SEARCH + [S((7.5, 10.5), (13.5, 10.5))],
    "filter": [S((3, 4.5), (21, 4.5), (14, 12.5), (14, 19.5), (10, 21), (10, 12.5), close=True)],
    "sort": [S((7, 4), (7, 20)), S((3.5, 16.5), (7, 20), (10.5, 16.5)), S((17, 20), (17, 4)), S((13.5, 7.5), (17, 4), (20.5, 7.5))],
    "link": [("s", tuple(_capsule(8.6, 15.4, 5, 3.6, -45)), True), ("s", tuple(_capsule(15.4, 8.6, 5, 3.6, -45)), True),
             S((9.8, 14.2), (14.2, 9.8))],
    "text": [S((5, 6.5), (5, 4), (19, 4), (19, 6.5)), S((12, 4), (12, 20)), S((9, 20), (15, 20))],
    # navigation
    "home": [S((3, 11), (12, 3.5), (21, 11)), S((5.5, 9), (5.5, 20.5), (18.5, 20.5), (18.5, 9)),
             S((10, 20.5), (10, 14.5), (14, 14.5), (14, 20.5))],
    "arrow-right": _ARROW_R,
    "arrow-down": rotate(_ARROW_R, 90),
    "arrow-left": rotate(_ARROW_R, 180),
    "arrow-up": rotate(_ARROW_R, 270),
    "chevron-right": _CHEV_R,
    "chevron-down": rotate(_CHEV_R, 90),
    "chevron-left": rotate(_CHEV_R, 180),
    "chevron-up": rotate(_CHEV_R, 270),
    "caret-down": [P((6.5, 9), (17.5, 9), (12, 15.5))],
    "caret-right": [P((9, 6.5), (15.5, 12), (9, 17.5))],
    "menu": [S((4, 6), (20, 6)), S((4, 12), (20, 12)), S((4, 18), (20, 18))],
    "dots": [FC(5, 12, 1.8), FC(12, 12, 1.8), FC(19, 12, 1.8)],
    "dots-vertical": [FC(12, 5, 1.8), FC(12, 12, 1.8), FC(12, 19, 1.8)],
    "grip": [FC(9, 6, 1.5), FC(15, 6, 1.5), FC(9, 12, 1.5), FC(15, 12, 1.5), FC(9, 18, 1.5), FC(15, 18, 1.5)],
    "external": [S((13, 3.5), (20.5, 3.5), (20.5, 11)), S((20.5, 3.5), (11, 13)),
                 S((17.5, 14), (17.5, 20.5), (3.5, 20.5), (3.5, 6.5), (10, 6.5))],
    "fullscreen": [S((3.5, 9), (3.5, 3.5), (9, 3.5)), S((15, 3.5), (20.5, 3.5), (20.5, 9)),
                   S((20.5, 15), (20.5, 20.5), (15, 20.5)), S((9, 20.5), (3.5, 20.5), (3.5, 15))],
    "move": [S((12, 2.5), (12, 21.5)), S((2.5, 12), (21.5, 12)), S((9.5, 5), (12, 2.5), (14.5, 5)),
             S((9.5, 19), (12, 21.5), (14.5, 19)), S((5, 9.5), (2.5, 12), (5, 14.5)), S((19, 9.5), (21.5, 12), (19, 14.5))],
    "scale": [S((4, 20), (20, 4)), S((13, 4), (20, 4), (20, 11)), S((4, 13), (4, 20), (11, 20))],
    "target": [C(12, 12, 8), C(12, 12, 3), S((12, 1.5), (12, 5)), S((12, 19), (12, 22.5)), S((1.5, 12), (5, 12)), S((19, 12), (22.5, 12))],
    # status
    "info": [_CIRCLE, S((12, 11), (12, 16.5)), FC(12, 7.8, 1.25)],
    "warning": [S((12, 3), (22, 20.5), (2, 20.5), close=True), S((12, 9.5), (12, 14)), FC(12, 17.2, 1.25)],
    "error": [_CIRCLE, S((8.5, 8.5), (15.5, 15.5)), S((15.5, 8.5), (8.5, 15.5))],
    "success": [_CIRCLE, S((7.5, 12.5), (10.8, 15.5), (16.5, 8.8))],
    "help": [_CIRCLE, A(12, 9.6, 3, 195, 400), S((14.3, 11.5), (12, 13.2), (12, 14.3)), FC(12, 17.3, 1.25)],
    "bell": [S((5, 17), (5, 10.5)), A(12, 10.5, 7, 180, 360), S((19, 10.5), (19, 17)), S((3.5, 17), (20.5, 17)), A(12, 18.5, 2.5, 0, 180)],
    "lock": [R(4.5, 10.5, 15, 11, 2), S((8, 10.5), (8, 7)), A(12, 7, 4, 180, 360), S((16, 7), (16, 10.5)), FC(12, 15.8, 1.4)],
    "unlock": [R(4.5, 10.5, 15, 11, 2), S((8, 10.5), (8, 7)), A(12, 7, 4, 180, 330), FC(12, 15.8, 1.4)],
    "shield": [S((12, 2.5), (20, 5.5), (20, 11), (18.5, 16), (12, 21.5), (5.5, 16), (4, 11), (4, 5.5), close=True)],
    "key": [C(7.5, 15.5, 4), S((10.3, 12.7), (20, 3)), S((17, 6), (19.5, 8.5)), S((14.5, 8.5), (16.5, 10.5))],
    "eye": [_eye(), C(12, 12, 3)],
    "eye-off": [_eye(), C(12, 12, 3), X(S((3, 3), (21, 21))), S((3.5, 3.5), (20.5, 20.5))],
    "star": [S(*_star(12, 12.6, 10, 4.2), close=True)],
    "star-filled": [P(*_star(12, 12.6, 10, 4.2))],
    "heart": [S(*_heart(), close=True)],
    "heart-filled": [P(*_heart())],
    "bookmark": [S((6, 3), (18, 3), (18, 21), (12, 16.5), (6, 21), close=True)],
    "flag": [S((5, 21.5), (5, 3)), S((5, 4), (19, 4), (16, 8.5), (19, 13), (5, 13))],
    "tag": [S((3, 3), (11.5, 3), (21, 12.5), (12.5, 21), (3, 11.5), close=True), FC(7.5, 7.5, 1.4)],
    "pin": [A(12, 9.5, 6.5, 150, 390), S((6.37, 12.75), (12, 21.5), (17.63, 12.75)), C(12, 9.5, 2.3)],
    "sparkle": [P((12, 2), (14, 10), (22, 12), (14, 14), (12, 22), (10, 14), (2, 12), (10, 10))],
    "bolt": [S((13, 2.5), (4.5, 13.5), (12, 13.5), (11, 21.5), (19.5, 10.5), (12, 10.5), close=True)],
    "drop": [A(12, 14.5, 6.5, -35, 215), S((6.68, 10.77), (12, 2.5), (17.32, 10.77))],
    # media
    "play": [P((7, 4), (19.5, 12), (7, 20))],
    "pause": [FR(6, 4, 4, 16, 1), FR(14, 4, 4, 16, 1)],
    "stop": [FR(5, 5, 14, 14, 2)],
    "record": [FC(12, 12, 7)],
    "skip-forward": [P((5, 5), (14, 12), (5, 19)), FR(16, 5, 3, 14, 1)],
    "skip-back": mirror([P((5, 5), (14, 12), (5, 19)), FR(16, 5, 3, 14, 1)]),
    "volume": [_SPEAKER, A(13, 12, 4.5, -45, 45), A(13, 12, 8.5, -45, 45)],
    "mute": [_SPEAKER, S((16, 9), (21, 15)), S((21, 9), (16, 15))],
    "mic": [R(9, 2.5, 6, 11.5, 3), A(12, 11, 6.5, 0, 180), S((12, 17.5), (12, 21))],
    "image": [R(3, 4, 18, 16, 2), C(8.5, 9.5, 1.8), S((21, 16), (15.5, 10.5), (5, 20))],
    "music": [S((9, 18), (9, 5), (20, 3), (20, 16)), C(6.5, 18, 2.5), C(17.5, 16, 2.5)],
    "video": [R(2.5, 6, 13, 12, 2), S((15.5, 10.5), (21.5, 7), (21.5, 17), (15.5, 13.5), close=True)],
    "camera": [S((2.5, 8), (2.5, 19.5), (21.5, 19.5), (21.5, 8), (17, 8), (15, 5), (9, 5), (7, 8), close=True), C(12, 13, 3.6)],
    # devices & system
    "settings": [S(*_gear(12, 12, 10, 7.4), close=True), C(12, 12, 3)],
    "sliders": [S((4, 6), (20, 6)), S((4, 12), (20, 12)), S((4, 18), (20, 18)), FC(15, 6, 2.5), FC(8, 12, 2.5), FC(13, 18, 2.5)],
    "terminal": [R(2.5, 4, 19, 16, 2), S((6.5, 9), (10, 12), (6.5, 15)), S((12, 15.5), (17, 15.5))],
    "code": [S((8, 7), (3, 12), (8, 17)), S((16, 7), (21, 12), (16, 17)), S((14, 4.5), (10, 19.5))],
    "monitor": [R(2.5, 3.5, 19, 13, 2), S((8, 21), (16, 21)), S((12, 16.5), (12, 21))],
    "keyboard": [R(2, 6, 20, 12, 2), FC(6, 10, 1), FC(10, 10, 1), FC(14, 10, 1), FC(18, 10, 1), S((7, 14.5), (17, 14.5))],
    "mouse": [R(6, 2.5, 12, 19, 6), S((12, 6), (12, 9))],
    "gamepad": [R(2.5, 7, 19, 11, 5.5), S((6.5, 12.5), (10, 12.5)), S((8.25, 10.75), (8.25, 14.25)), FC(15.5, 11.5, 1.2), FC(18, 13.5, 1.2)],
    "battery": [R(2.5, 7, 17, 10, 2), S((21.5, 10.5), (21.5, 13.5)), FR(5, 9.5, 8, 5, 1)],
    "power": [A(12, 13, 8, -55, 235), S((12, 3), (12, 12))],
    "wifi": [A(12, 19, 13, 230, 310), A(12, 19, 8.5, 228, 312), A(12, 19, 4, 225, 315), FC(12, 19, 1.4)],
    "globe": [_CIRCLE, E(12, 12, 4, 9.5), S((2.5, 12), (21.5, 12))],
    "cloud": [S((6.5, 19), (18, 19)), A(6.5, 14.5, 4.5, 90, 250), A(12, 10, 5.6, 195, 335), A(17.5, 14, 5, 260, 450)],
    "user": [C(12, 8, 4), A(12, 21, 7.5, 180, 360)],
    "users": [C(9, 8, 3.5), A(9, 20.5, 6.5, 180, 360), A(16.5, 8, 3, -80, 120), A(16.5, 19.5, 5.5, 250, 360)],
    "mail": [R(2.5, 5, 19, 14, 2), S((2.5, 6.5), (12, 13), (21.5, 6.5))],
    "send": [S((21.5, 2.5), (10.5, 13.5)), S((21.5, 2.5), (14.5, 21.5), (10.5, 13.5), (2.5, 9.5), close=True)],
    "share": [C(18, 5, 2.5), C(6, 12, 2.5), C(18, 19, 2.5), S((8.2, 10.8), (15.8, 6.2)), S((8.2, 13.2), (15.8, 17.8))],
    "calendar": [R(3, 4.5, 18, 16.5, 2), S((3, 9.5), (21, 9.5)), S((8, 2.5), (8, 6.5)), S((16, 2.5), (16, 6.5))],
    "clock": [_CIRCLE, S((12, 6.5), (12, 12), (15.5, 14))],
    "history": [A(12, 12, 9, 200, 520), S((2.3, 5.5), (3.6, 9.2), (7.2, 8.2)), S((12, 7.5), (12, 12), (15, 13.8))],
    "bug": [E(12, 14, 5, 6.5), A(12, 7.8, 3, 180, 360), S((12, 8), (12, 20.5)), S((7, 10.5), (4, 8.5)), S((17, 10.5), (20, 8.5)),
            S((7, 14.5), (3.5, 14.5)), S((17, 14.5), (20.5, 14.5)), S((7.5, 18.5), (4.5, 21)), S((16.5, 18.5), (19.5, 21))],
    # 3D, design, data
    "cube": [S((12, 2.5), (20.5, 7), (20.5, 17), (12, 21.5), (3.5, 17), (3.5, 7), close=True), S((3.5, 7), (12, 11.5), (20.5, 7)),
             S((12, 11.5), (12, 21.5))],
    "sphere": [C(12, 12, 9), E(12, 12, 9, 3.2), E(12, 12, 3.5, 9)],
    "cylinder": [E(12, 5.5, 7.5, 3), S((4.5, 5.5), (4.5, 18.5)), S((19.5, 5.5), (19.5, 18.5)), A(12, 18.5, 7.5, 0, 180)],
    "layers": [S((12, 3), (21.5, 8), (12, 13), (2.5, 8), close=True), S((2.5, 12.5), (12, 17.5), (21.5, 12.5)),
               S((2.5, 17), (12, 22), (21.5, 17))],
    "node": [R(2.5, 4, 8, 6, 1.5), R(13.5, 14, 8, 6, 1.5), ("s", tuple(_bezier((10.5, 7), (16, 7), (8, 17), (13.5, 17))), False)],
    "palette": [_CIRCLE, FC(8, 9, 1.5), FC(12.2, 6.8, 1.5), FC(16.4, 9, 1.5), FC(7.6, 13.6, 1.5), FC(15, 15.5, 2.3)],
    "brush": [S((20.5, 3.5), (11, 13)), S((11, 13), (8.5, 13), (7, 14.5), (6.5, 18), (3.5, 20.5), (8.5, 20.5), (10.5, 18.5), (11, 15), close=True)],
    "light": [A(12, 9, 6.5, 130, 410), S((7.82, 13.98), (9, 16.5), (15, 16.5), (16.18, 13.98)), S((9.5, 19), (14.5, 19)), S((10.5, 21.5), (13.5, 21.5))],
    "sun": [C(12, 12, 4)] + _sun_rays(),
    "moon": [FC(12, 12, 8.5), X(FC(16.5, 8, 7))],
    "shapes": [C(8, 8, 5), R(12.5, 12.5, 9, 9, 1), S((4, 21), (8, 14), (12, 21), close=True)],
    "grid": [R(3, 3, 7.5, 7.5, 1.5), R(13.5, 3, 7.5, 7.5, 1.5), R(3, 13.5, 7.5, 7.5, 1.5), R(13.5, 13.5, 7.5, 7.5, 1.5)],
    "list": [S((9, 6), (20, 6)), S((9, 12), (20, 12)), S((9, 18), (20, 18)), FC(4.8, 6, 1.4), FC(4.8, 12, 1.4), FC(4.8, 18, 1.4)],
    "table": [R(3, 4, 18, 16, 2), S((3, 9.5), (21, 9.5)), S((3, 14.8), (21, 14.8)), S((9.5, 9.5), (9.5, 20))],
    "chart": [S((3.5, 3.5), (3.5, 20.5), (20.5, 20.5)), S((8, 16.5), (8, 12)), S((12.5, 16.5), (12.5, 7)), S((17, 16.5), (17, 10))],
    "chart-line": [S((3.5, 3.5), (3.5, 20.5), (20.5, 20.5)), S((7, 15), (11, 10), (14, 13), (19.5, 6.5))],
    "window": [R(3, 3.5, 18, 17, 2), S((3, 8), (21, 8))],
    "sidebar": [R(3, 3, 18, 18, 2), S((9, 3), (9, 21))],
    "magic": [S((4, 20), (15, 9)), P((17, 2.5), (18, 6), (21.5, 7), (18, 8), (17, 11.5), (16, 8), (12.5, 7), (16, 6))],
    "diamond": [S((12, 3), (21, 12), (12, 21), (3, 12), close=True)],
    "diamond-filled": [P((12, 2.5), (21.5, 12), (12, 21.5), (2.5, 12))],
    "repeat": [S((4, 11), (4, 8.5), (6.5, 6.5), (19.5, 6.5)), S((16.5, 3.5), (19.5, 6.5), (16.5, 9.5)),
               S((20, 13), (20, 15.5), (17.5, 17.5), (4.5, 17.5)), S((7.5, 14.5), (4.5, 17.5), (7.5, 20.5))],
    "keyframe-prev": [S((10, 6), (4, 12), (10, 18)), P((16, 7), (21, 12), (16, 17), (11, 12))],
    "keyframe-next": [S((14, 6), (20, 12), (14, 18)), P((8, 7), (13, 12), (8, 17), (3, 12))],
    "film": [R(3, 3, 18, 18, 2), S((7, 3), (7, 21)), S((17, 3), (17, 21)), S((3, 8), (7, 8)), S((3, 12), (7, 12)),
             S((3, 16), (7, 16)), S((17, 8), (21, 8)), S((17, 12), (21, 12)), S((17, 16), (21, 16))],
    "prefab": [S((12, 2.5), (20.5, 7), (20.5, 17), (12, 21.5), (3.5, 17), (3.5, 7), close=True),
               S((3.5, 7), (12, 11.5), (20.5, 7)), S((12, 11.5), (12, 21.5)), FC(12, 7, 1.6)],
    "material": [C(12, 12, 9), A(12, 12, 5.5, 200, 250)],
    "scene": [S((2.5, 18), (8, 9), (12, 14), (15, 10.5), (21.5, 18), close=True), C(17, 6, 2)],
    "font": [S((5, 20), (12, 4), (19, 20)), S((8, 14), (16, 14))],
    "circle": [_CIRCLE],
    "square": [R(3.5, 3.5, 17, 17, 2)],
    "triangle": [S((12, 3.5), (21, 19.5), (3, 19.5), close=True)],
}

_ALIASES = {"open": "folder-open", "new": "file-plus", "delete": "trash", "settings-2": "sliders",
            "desktop": "monitor", "x": "close", "add": "plus", "blueprint": "node", "rotate": "refresh"}
for _a, _t in _ALIASES.items():
    ICONS[_a] = ICONS[_t]


def icon_names():
    return sorted(ICONS)


def register_icon(name, ops):
    """Add or replace an icon. ``ops`` is a list built with S/C/A/E/FC/P/R/FR/X."""
    ICONS[name] = list(ops)


# ----------------------------------------------------------------------------- rasteriser
def draw_ops(surf, ops, k, color=(255, 255, 255, 255), stroke=STROKE, min_hw=0.0):
    """Draw icon ops onto a pygame Surface, ``k`` = pixels per grid unit."""
    off = (0, 0, 0, 0)
    hw0 = max(stroke * k / 2.0, min_hw)

    def stroke_pts(pts, closed, col, hw):
        P_ = [(x * k, y * k) for x, y in pts]
        if closed and P_:
            P_.append(P_[0])
        for a, b in zip(P_, P_[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(dx, dy) or 1e-6
            nx, ny = -dy / ln * hw, dx / ln * hw
            pygame.draw.polygon(surf, col, [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                                            (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)])
        for p in P_:
            pygame.draw.circle(surf, col, p, hw)

    def run(op, col, grow=0.0):
        kind = op[0]
        if kind == "s":
            stroke_pts(op[1], op[2], col, hw0 + grow)
        elif kind == "a":
            _, cx, cy, r, a0, a1 = op
            stroke_pts(_arc_pts(cx, cy, r, a0, a1), abs(a1 - a0) >= 360, col, hw0 + grow)
        elif kind == "fc":
            pygame.draw.circle(surf, col, (op[1] * k, op[2] * k), op[3] * k + grow)
        elif kind == "p":
            pygame.draw.polygon(surf, col, [(x * k, y * k) for x, y in op[1]])
        elif kind == "x":
            run(op[1], off, grow=hw0 * 1.6)

    for op in ops:
        run(op, color)


def rasterize(ops, px, stroke=STROKE):
    """Render ``ops`` into a px*px alpha mask (bytes)."""
    big = px * SUPERSAMPLE
    surf = pygame.Surface((big, big), pygame.SRCALPHA)
    draw_ops(surf, ops, big / 24.0, (255, 255, 255, 255), stroke, SUPERSAMPLE * 0.55)
    small = pygame.transform.smoothscale(surf, (px, px))
    return pygame.image.tobytes(small, "RGBA")[3::4]


class IconCache:
    """Rasterises icons on demand into the shared GlyphAtlas."""

    def __init__(self, atlas):
        self.atlas = atlas
        self._cache = {}
        self._gen = atlas.generation

    def get(self, name, px):
        """-> glyph-run tuple usable with DrawList.text_run, or None if unknown."""
        if self._gen != self.atlas.generation:
            self._cache.clear()
            self._gen = self.atlas.generation
        key = (name, px)
        r = self._cache.get(key)
        if r is not None:
            return r
        ops = ICONS.get(name)
        if ops is None:
            return None
        px = max(6, int(px))
        stroke = STROKE if px >= 14 else STROKE * 1.12   # a touch bolder when tiny
        data = rasterize(ops, px, stroke)
        pos = self.atlas.alloc(px, px)
        if pos is None:
            self.atlas.reset()
            self._cache.clear()
            self._gen = self.atlas.generation
            pos = self.atlas.alloc(px, px)
            if pos is None:
                return None
        self.atlas.upload(pos[0], pos[1], px, px, data)
        s = self.atlas.size
        run = ((0, 0, px, px, pos[0] / s, pos[1] / s, (pos[0] + px) / s, (pos[1] + px) / s),)
        self._cache[key] = run
        return run
