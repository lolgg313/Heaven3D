# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""The Vitrail logo: an arched stained-glass window.

Seven glass facets (four panes, a three-petal rose) held by dark lead
cames, with a bright jewel where the light comes through.  The same geometry
renders as an SVG, a pygame Surface (window icon / PNG) and inside the UI.
"""
from __future__ import annotations

import math

import pygame

from .icons import SUPERSAMPLE, draw_ops

LEAD = (16, 18, 26)
JEWEL = (255, 250, 235)

# (name, colour, polygon points in a 24x24 grid)
_CX, _CY, _R = 12.0, 10.0, 8.0


def _sector(a0, a1):
    pts = [(_CX, _CY)]
    for i in range(13):
        a = math.radians(a0 + (a1 - a0) * i / 12)
        pts.append((_CX + _R * math.cos(a), _CY + _R * math.sin(a)))
    return pts


FACETS = [
    ("sapphire", (47, 107, 255), [(4, 15.5), (12, 15.5), (12, 22), (4, 22)]),
    ("lagoon", (25, 195, 176), [(12, 15.5), (20, 15.5), (20, 22), (12, 22)]),
    ("amethyst", (138, 92, 255), [(4, 10), (12, 10), (12, 15.5), (4, 15.5)]),
    ("amber", (255, 181, 71), [(12, 10), (20, 10), (20, 15.5), (12, 15.5)]),
    ("rose", (255, 92, 138), _sector(180, 240)),
    ("sun", (255, 224, 138), _sector(240, 300)),
    ("sky", (56, 214, 255), _sector(300, 360)),
]


def _lead_ops():
    arch = [(4, 22), (4, 10)] + [(_CX + _R * math.cos(math.radians(a)), _CY + _R * math.sin(math.radians(a)))
                                 for a in range(180, 361, 6)] + [(20, 22)]
    ops = [("s", tuple(arch), True),
           ("s", ((12, 10), (12, 22)), False),
           ("s", ((4, 15.5), (20, 15.5)), False),
           ("s", ((4, 10), (20, 10)), False)]
    for a in (240, 300):
        r = math.radians(a)
        ops.append(("s", ((_CX, _CY), (_CX + _R * math.cos(r), _CY + _R * math.sin(r))), False))
    return ops


LEAD_OPS = _lead_ops()
JEWEL_OPS = [("fc", _CX, _CY, 2.3)]
GLASS_OPS = {name: [("p", tuple(pts))] for name, _, pts in FACETS}


def logo_surface(px: int, *, glow=True, lead_width=1.35) -> pygame.Surface:
    """RGBA pygame Surface of the logo (for set_icon, PNG export, sprites)."""
    big = px * SUPERSAMPLE
    surf = pygame.Surface((big, big), pygame.SRCALPHA)
    k = big / 24.0
    if glow:   # soft halo
        halo = pygame.Surface((big, big), pygame.SRCALPHA)
        for i in range(10, 0, -1):
            a = int(9 * (11 - i))
            pygame.draw.circle(halo, (150, 140, 255, min(255, a // 3)), (12 * k, 13 * k), (8 + i * 0.35) * k)
        surf.blit(halo, (0, 0))
    for name, color, pts in FACETS:
        draw_ops(surf, GLASS_OPS[name], k, color + (255,))
        # subtle highlight on the upper-left of each pane
        hl = pygame.Surface((big, big), pygame.SRCALPHA)
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        shrink = [(cx + (x - cx) * 0.55 - 0.6, cy + (y - cy) * 0.55 - 0.6) for x, y in pts]
        pygame.draw.polygon(hl, (255, 255, 255, 46), [(x * k, y * k) for x, y in shrink])
        surf.blit(hl, (0, 0))
    draw_ops(surf, LEAD_OPS, k, LEAD + (255,), stroke=lead_width)
    draw_ops(surf, JEWEL_OPS, k, JEWEL + (255,))
    draw_ops(surf, [("a", _CX, _CY, 2.3, 0, 360)], k, LEAD + (255,), stroke=0.8)
    return pygame.transform.smoothscale(surf, (px, px))


def wordmark_surface(height: int, text_color=(235, 238, 245), font_path=None) -> pygame.Surface:
    """Logo + "vitrail" wordmark side by side."""
    import pygame.freetype as ft
    if not ft.get_init():
        ft.init()
    icon = logo_surface(height)
    f = ft.Font(font_path, int(height * 0.62))
    f.strong = True
    txt, rect = f.render("vitrail", text_color + (255,))
    w = height + int(height * 0.08) + txt.get_width()
    out = pygame.Surface((w, height), pygame.SRCALPHA)
    out.blit(icon, (0, 0))
    out.blit(txt, (height + int(height * 0.08), (height - txt.get_height()) // 2 + int(height * 0.04)))
    return out


def logo_svg(size=256) -> str:
    """Standalone SVG of the logo."""
    def pts(p):
        return " ".join(f"{x:.3f},{y:.3f}" for x, y in p)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">',
             '<defs><radialGradient id="g" cx="50%" cy="54%" r="50%">'
             '<stop offset="0" stop-color="#968cff" stop-opacity="0.55"/>'
             '<stop offset="1" stop-color="#968cff" stop-opacity="0"/></radialGradient>'
             '<linearGradient id="hl" x1="0" y1="0" x2="1" y2="1">'
             '<stop offset="0" stop-color="#fff" stop-opacity="0.28"/>'
             '<stop offset="0.6" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>',
             '<circle cx="12" cy="13" r="11.5" fill="url(#g)"/>']
    for name, (r, g, b), p in FACETS:
        parts.append(f'<polygon points="{pts(p)}" fill="rgb({r},{g},{b})"/>')
        parts.append(f'<polygon points="{pts(p)}" fill="url(#hl)"/>')
    lead = f"rgb{LEAD}"
    for op in LEAD_OPS:
        tag = "polygon" if op[2] else "polyline"
        parts.append(f'<{tag} points="{pts(op[1])}" fill="none" stroke="{lead}" stroke-width="{1.35:.2f}" '
                     'stroke-linejoin="round" stroke-linecap="round"/>')
    parts.append(f'<circle cx="{_CX}" cy="{_CY}" r="2.3" fill="rgb{JEWEL}" stroke="{lead}" stroke-width="0.8"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def draw_logo(ui, dl, x, y, size):
    """Draw the logo inside the UI (8 tinted atlas quads)."""
    from .icons import ICONS
    px = ui.icon_size(size / ui.theme.scale)
    if "vitrail-lead" not in ICONS:
        for name, _, p in FACETS:
            ICONS["vitrail-" + name] = GLASS_OPS[name]
        ICONS["vitrail-lead"] = LEAD_OPS
        ICONS["vitrail-jewel"] = JEWEL_OPS
    dl.shadow(x + px * 0.18, y + px * 0.18, px * 0.64, px * 0.7, (0.55, 0.5, 1.0, 0.35), px * 0.3, px * 0.18)
    for name, (r, g, b), _ in FACETS:
        run = ui.icons.get("vitrail-" + name, px)
        if run:
            dl.text_run(run, round(x), round(y), (r / 255, g / 255, b / 255, 1.0))
    run = ui.icons.get("vitrail-lead", px)
    if run:
        dl.text_run(run, round(x), round(y), (LEAD[0] / 255, LEAD[1] / 255, LEAD[2] / 255, 1.0))
    run = ui.icons.get("vitrail-jewel", px)
    if run:
        dl.text_run(run, round(x), round(y), (1, 0.98, 0.92, 1))
    return px
