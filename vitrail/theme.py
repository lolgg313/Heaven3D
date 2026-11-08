# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Themes: plain dataclasses of sizes and RGBA float colours."""
from __future__ import annotations

from dataclasses import dataclass, field, replace


def rgb(hex_str: str, alpha: float = 1.0):
    """'#3a7bd5' or '3a7bd5' or '#3a7bd5cc' -> (r, g, b, a) floats."""
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    if len(h) == 8:
        alpha = int(h[6:8], 16) / 255
    return (r, g, b, alpha)


def shade(c, amount: float):
    """Lighten (amount > 0) or darken (amount < 0) a colour by a fraction."""
    r, g, b, a = c
    if amount >= 0:
        return (r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount, a)
    k = 1 + amount
    return (r * k, g * k, b * k, a)


def with_alpha(c, a: float):
    return (c[0], c[1], c[2], a)


def to_hex(c) -> str:
    r, g, b = (max(0, min(255, round(v * 255))) for v in c[:3])
    s = f"#{r:02x}{g:02x}{b:02x}"
    if len(c) > 3 and c[3] < 0.999:
        s += f"{max(0, min(255, round(c[3] * 255))):02x}"
    return s


_FONT_CANDIDATES = ["segoeui", "inter", "notosans", "dejavusans", "liberationsans",
                    "helveticaneue", "helvetica", "arial", "freesans"]


def find_system_font():
    """Best-effort sans-serif lookup; None means pygame's built-in font."""
    try:
        import pygame.font
        if not pygame.font.get_init():
            pygame.font.init()
        return pygame.font.match_font(_FONT_CANDIDATES)
    except Exception:
        return None


@dataclass
class Theme:
    # metrics (pixels, before ``scale``)
    scale: float = 1.0
    font_path: str | None = field(default_factory=find_system_font)
    font_size: int = 13
    widget_height: int = 22
    radius: float = 4.0
    spacing: int = 3
    padding: int = 8
    panel_header: int = 26
    emboss: float = 0.06          # strength of the top->bottom gradient on widgets
    label_split: float = 0.32     # share of the width given to side labels
    icon_size: int = 16

    # surfaces
    background: tuple = rgb("#1d1f23")
    panel: tuple = rgb("#2a2c31")
    panel_header_bg: tuple = rgb("#33363c")
    outline: tuple = rgb("#16171a")
    shadow: tuple = (0.0, 0.0, 0.0, 0.55)

    # widgets
    button: tuple = rgb("#4a4e56")
    button_hover: tuple = rgb("#565b64")
    button_pressed: tuple = rgb("#3b8eea")
    field: tuple = rgb("#3a3d44")
    field_hover: tuple = rgb("#444851")
    field_edit: tuple = rgb("#1b1c20")
    slider_fill: tuple = rgb("#4d79b8")
    accent: tuple = rgb("#3b8eea")
    selection: tuple = rgb("#3b8eea", 0.45)
    check_bg: tuple = rgb("#25272b")

    # text
    text: tuple = rgb("#e6e8eb")
    text_dim: tuple = rgb("#9aa0a8")
    text_on_accent: tuple = rgb("#ffffff")
    caret: tuple = rgb("#7ab8ff")

    # popups
    menu: tuple = rgb("#1f2125", 0.98)
    menu_hover: tuple = rgb("#3b8eea")
    tooltip: tuple = rgb("#121315", 0.96)
    tooltip_text: tuple = rgb("#e6e8eb")
    modal_dim: tuple = (0.0, 0.0, 0.0, 0.45)
    scrollbar: tuple = rgb("#ffffff", 0.18)
    scrollbar_hover: tuple = rgb("#ffffff", 0.32)

    style: str = "editor"         # "editor" or "material" (shape language of buttons etc.)

    def scaled(self, v: float) -> float:
        return v * self.scale

    @property
    def px_font(self) -> int:
        return max(6, round(self.font_size * self.scale))

    @property
    def h(self) -> int:
        return round(self.widget_height * self.scale)

    def copy(self, **changes) -> "Theme":
        return replace(self, **changes)

    @classmethod
    def dark(cls, **kw) -> "Theme":
        return cls(**kw)

    @classmethod
    def material_dark(cls, **kw) -> "Theme":
        """Rounded, flat, roomy -- Material-style dark palette ("Iris")."""
        base = dict(
            style="material", font_size=14, widget_height=32, radius=10.0, emboss=0.0, spacing=6, padding=12,
            panel_header=34, background=rgb("#141218"), panel=rgb("#1d1b22"), panel_header_bg=rgb("#25222c"),
            outline=rgb("#3a3642"), shadow=(0, 0, 0, 0.5), button=rgb("#2f2b38"), button_hover=rgb("#3a3545"),
            button_pressed=rgb("#9f8cff"), field=rgb("#2a2731"), field_hover=rgb("#332f3b"),
            field_edit=rgb("#18161c"), slider_fill=rgb("#7b68d8"), accent=rgb("#a594ff"),
            selection=rgb("#a594ff", 0.35), check_bg=rgb("#1d1b22"), text=rgb("#ebe6f2"), text_dim=rgb("#a39fad"),
            text_on_accent=rgb("#1e1440"), caret=rgb("#c4b8ff"), menu=rgb("#25222c", 0.99), menu_hover=rgb("#a594ff"),
            tooltip=rgb("#ebe6f2", 0.96), tooltip_text=rgb("#1c1a22"), scrollbar=rgb("#ffffff", 0.16), scrollbar_hover=rgb("#ffffff", 0.3),
            label_split=0.3,
        )
        base.update(kw)
        return cls(**base)

    @classmethod
    def material_light(cls, **kw) -> "Theme":
        base = dict(
            style="material", font_size=14, widget_height=32, radius=10.0, emboss=0.0, spacing=6, padding=12,
            panel_header=34, background=rgb("#f4f1f8"), panel=rgb("#fdfbff"), panel_header_bg=rgb("#efeaf6"),
            outline=rgb("#cbc4d6"), shadow=(0.15, 0.1, 0.25, 0.18), button=rgb("#ece6f5"), button_hover=rgb("#e2daef"),
            button_pressed=rgb("#5a45d4"), field=rgb("#f1edf7"), field_hover=rgb("#e9e4f1"), field_edit=rgb("#ffffff"),
            slider_fill=rgb("#9d8df0"), accent=rgb("#5a45d4"), selection=rgb("#5a45d4", 0.22), check_bg=rgb("#ffffff"),
            text=rgb("#1c1a22"), text_dim=rgb("#625d6d"), text_on_accent=rgb("#ffffff"), caret=rgb("#5a45d4"),
            menu=rgb("#fdfbff", 0.99), menu_hover=rgb("#5a45d4"), tooltip=rgb("#2d2a34", 0.96), tooltip_text=rgb("#f3eff8"),
            scrollbar=rgb("#000000", 0.18), scrollbar_hover=rgb("#000000", 0.32), label_split=0.3,
        )
        base.update(kw)
        return cls(**base)

    @classmethod
    def light(cls, **kw) -> "Theme":
        base = dict(
            background=rgb("#d9dbe0"), panel=rgb("#eceef1"), panel_header_bg=rgb("#e1e4e8"),
            outline=rgb("#a9adb5"), shadow=(0, 0, 0, 0.25),
            button=rgb("#f7f8fa"), button_hover=rgb("#ffffff"), button_pressed=rgb("#2f7de1"),
            field=rgb("#dfe2e7"), field_hover=rgb("#e8eaee"), field_edit=rgb("#ffffff"),
            slider_fill=rgb("#8fb4e8"), accent=rgb("#2f7de1"), selection=rgb("#2f7de1", 0.3),
            check_bg=rgb("#ffffff"), text=rgb("#1d1f23"), text_dim=rgb("#5d636c"),
            caret=rgb("#1f5fbf"), menu=rgb("#fbfbfc", 0.98), menu_hover=rgb("#2f7de1"),
            tooltip=rgb("#2a2c31", 0.96), tooltip_text=rgb("#eceef1"), scrollbar=rgb("#000000", 0.2),
            scrollbar_hover=rgb("#000000", 0.35), emboss=0.03,
        )
        base.update(kw)
        return cls(**base)
