# SPDX-License-Identifier: MIT
"""Heaven3D's editor interface, built with Vitrail (../vitrail).

The engine loads this package in editor mode only (src/editor/vitrail_hook.py):

    attach(h3d, engine) -> Editor      h3d is the engine module itself
    detach(engine)

Games -- run with Heaven3D_runtime.py or exported -- never import it.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:            # the vendored vitrail/ package sits next to this one
    sys.path.insert(0, _ROOT)

from .editor import Editor  # noqa: E402

__all__ = ["Editor", "attach", "detach"]


def attach(h3d, engine):
    return Editor(h3d, engine).install()


def detach(engine):
    ed = getattr(engine, "_vt", None)
    if ed is not None:
        ed.uninstall()
