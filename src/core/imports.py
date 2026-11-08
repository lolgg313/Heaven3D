# @heaven3d-part core/imports.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 0  —  DEFENSIVE IMPORTS
# #############################################################################

import sys
import os
import json
import math
import struct
import time
import traceback
import argparse
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Dict, List, Tuple, Union

# ---- numpy ----
try:
    import numpy as np
except ImportError:
    print("[Heaven3D] FATAL: numpy not installed.\n"
          "            Run:  pip install numpy")
    sys.exit(1)

# ---- pyglm (math) ----
try:
    import glm
except ImportError:
    print("[Heaven3D] FATAL: pyglm not installed.\n"
          "            Run:  pip install pyglm")
    sys.exit(1)

# ---- pygame (window + input) ----
# A mouse button press must not start SDL's own "auto capture": on Windows,
# releasing the button ends that capture and can take the game's pointer
# lock with it (aim down sights once = no more mouse-look).
os.environ.setdefault("SDL_MOUSE_AUTO_CAPTURE", "0")
try:
    import pygame
    from pygame import OPENGL, DOUBLEBUF
except ImportError:
    print("[Heaven3D] FATAL: pygame not installed.\n"
          "            Run:  pip install pygame")
    sys.exit(1)

# ---- moderngl (rendering) ----
try:
    import moderngl
except ImportError:
    print("[Heaven3D] FATAL: moderngl not installed.\n"
          "            Run:  pip install moderngl")
    sys.exit(1)

# ---- numba (optional JIT acceleration for terrain) ----
_HAS_NUMBA = False
try:
    from numba import njit as _numba_njit
    _HAS_NUMBA = True
except ImportError:
    pass


def _jit(*args, **kwargs):
    """Decorator: use numba.njit if available, else passthrough.
    Supports @_jit and @_jit(cache=True)."""
    if _HAS_NUMBA:
        return _numba_njit(*args, **kwargs)
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]
    def _decorator(func):
        return func
    return _decorator


# --------------------------------------------------------------------------- #
#  Logging  (also feeds in-editor console)
# --------------------------------------------------------------------------- #
class Log:
    _console_cb: Optional[Callable[[str], None]] = None

    @classmethod
    def set_console(cls, cb): cls._console_cb = cb

    @classmethod
    def _emit(cls, level, msg):
        line = f"[Heaven3D][{level}] {msg}"
        print(line)
        if cls._console_cb:
            try: cls._console_cb(line)
            except Exception: pass

    @classmethod
    def info(cls, m):  cls._emit("INFO",  str(m))
    @classmethod
    def warn(cls, m):  cls._emit("WARN",  str(m))
    @classmethod
    def error(cls, m): cls._emit("ERROR", str(m))
    @classmethod
    def debug(cls, m): cls._emit("DEBUG", str(m))


# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
HEAVEN_VERSION = "2.7.3"
PROJECT_EXT = ".heaven3d"


def _hex(code: str) -> tuple:
    """'#RRGGBB' → (r, g, b, 1.0) floats, so the palette can be written in
    the same hex notation the design spec uses."""
    s = code.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0,
            int(s[4:6], 16) / 255.0, 1.0)


def _lighten(color: tuple, factor: float = 0.13) -> tuple:
    """Lighten an RGBA colour by `factor` (0.13 ≈ +13%) — the standard
    hover-state lift used for every interactive element."""
    return (min(1.0, color[0] * (1.0 + factor)),
            min(1.0, color[1] * (1.0 + factor)),
            min(1.0, color[2] * (1.0 + factor)),
            color[3])


# ---------------------------------------------------------------------------
#  EDITOR THEME — modern dark palette (VS Code-calibrated)
#  Surfaces stay SQUARE; buttons / fields / list items get UI_RADIUS corners.
# ---------------------------------------------------------------------------
THEME = {
    # --- surfaces ---
    "bg":           _hex("#1E1E1E"),   # base background
    "panel":        _hex("#252526"),   # hierarchy / inspector panels
    "panel_dark":   _hex("#1E1E1E"),   # navbar + console
    "border":       _hex("#1B1B1C"),   # 2px panel separators
    # --- interactive elements ---
    "button":       _hex("#333333"),   # default elements
    "button_hover": _hex("#3A3A3A"),   # ≈ #333 lightened 13%
    "selected":     _hex("#094771"),   # selected hierarchy row
    # --- muted action colours (no harsh primaries) ---
    "accent":       _hex("#6B9EBF"),   # desaturated steel blue — headers
    "accent_dim":   _hex("#0E639C"),   # soft blue — Library / Duplicate
    "play_green":   _hex("#4B8A4B"),   # muted green — Play
    "green_text":   _hex("#7BAF7B"),   # desaturated green — feature headers
    "danger":       _hex("#C74E4E"),   # muted brick red — Delete / Stop
    "danger_hover": _hex("#DA5959"),   # brick red lightened ~12%
    # --- text ---
    "text":         _hex("#CCCCCC"),   # standard text
    "text_bright":  _hex("#FFFFFF"),   # active / hovered text
    "text_dim":     _hex("#8C8C8C"),   # hints, console, secondary labels
}

# Corner radii in PIXELS — applied to buttons, input fields and list items.
# The main layout panels deliberately stay square (radius 0).
UI_RADIUS = 6.0          # buttons / inputs / hierarchy rows (4–6px spec)  # [FINALPATCH3:ui_scale_120]
UI_SCROLL_RADIUS = 3.6   # scrollbar track + thumb  # [FINALPATCH3:ui_scale_120]

GIZMO_COLORS = {
    "x": (1.0, 0.35, 0.28),
    "y": (0.40, 0.85, 0.35),
    "z": (0.30, 0.55, 1.0),
    "c": (0.95, 0.95, 0.95),
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

