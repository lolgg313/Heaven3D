"""Light theme for the Hub: hub.set_theme() replaces palette colours before
the window is built (toggling it asks for a Hub restart)."""

PALETTE = {
    "dark": False,
    "bg": "#f4f5f8", "side": "#eceef2", "panel": "#ffffff", "panel_hi": "#f3f6fb",
    "raised": "#e7eaf0", "raised_hi": "#dce1e9", "field": "#eef0f4", "field_hi": "#e5e9f0",
    "line": "#dfe3ea", "text": "#1d2129", "muted": "#5f6878", "faint": "#99a1ae",
    "accent": "#3b6ff5", "accent_hi": "#5584ff", "accent_soft": "#e3ebff", "accent_text": "#2f5bd3",
    "ok": "#1f9d61", "ok_soft": "#dcf3e7", "warn": "#c98a12", "warn_soft": "#fbf0d9",
    "danger": "#d64545", "danger_soft": "#fbe3e3", "danger_soft_hi": "#f7d3d3",
    "code": "#f1f3f7", "code_text": "#1f2733", "sel": "#e3ebff", "sel_hi": "#d6e2ff",
    "body": "#2c3340", "icode": "#b4236b", "tip_bg": "#e4f6ec", "warn_bg": "#fdf3dc", "note_bg": "#e8efff",
    "dim_title": "#4a5260", "callout": "#2c3340", "key_bg": "#dfe3ea", "key_fg": "#1d2129",
}


def register(hub):
    hub.set_theme(PALETTE)
