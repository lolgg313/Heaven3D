"""Heaven3D as a pip package.

    pip install git+https://github.com/lolgg313/Heaven3D

    heaven3d                      the editor          (python -m heaven3d)
    heaven3d --project game.heaven3d --runtime        play a project
    heaven3d-hub                  the Hub (projects, templates, packages)

    import heaven3d
    engine = heaven3d.HeavenEngine(mode="runtime", project_path="game.heaven3d")
    engine.run()

The engine itself is still the single file Heaven3D.py (built from src/ by
build.py); this package only finds it and hands it over.  Everything the
engine defines for you (HeavenEngine, SceneNode, Blueprint ...) is available
as heaven3d.<name> (heaven3d.load() is the engine module itself); the engine is loaded the first time one of them is used, so
`import heaven3d` alone opens no window and needs no GPU.
"""
import importlib.util
import os
import sys

__all__ = ["engine_dir", "engine_file", "load", "HeavenEngine"]

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE = None


def engine_dir():
    """The folder that holds Heaven3D.py, the editor, templates and packages."""
    for d in (os.path.join(_HERE, "_engine"), os.path.dirname(_HERE)):     # installed / this repository
        if os.path.isfile(os.path.join(d, "Heaven3D.py")):
            return d
    raise ImportError("heaven3d: Heaven3D.py was not found next to the package (%s)" % _HERE)


def engine_file(name="Heaven3D.py"):
    return os.path.join(engine_dir(), name)


def load():
    """The engine module (Heaven3D.py), loaded once."""
    global _MODULE
    if _MODULE is None:
        mod = sys.modules.get("Heaven3D")
        if mod is None or not hasattr(mod, "HeavenEngine"):
            path = engine_file()
            # the engine reads the command line only when it is run as a program
            spec = importlib.util.spec_from_file_location("Heaven3D", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["Heaven3D"] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop("Heaven3D", None)
                raise
        _MODULE = mod
    return _MODULE


def __getattr__(name):
    if name == "__version__":
        return load().HEAVEN_VERSION
    if name.startswith("_"):              # submodules (heaven3d._cli) and private names: never load the
        raise AttributeError(name)        # engine for those; heaven3d.load() gives the whole module
    try:
        return getattr(load(), name)
    except AttributeError:
        raise AttributeError("module 'heaven3d' has no attribute %r" % name) from None


def __dir__():
    return sorted(set(__all__) | (set(dir(_MODULE)) if _MODULE is not None else set()))
