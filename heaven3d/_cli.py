"""The commands of the pip package: heaven3d, heaven3d-runtime, heaven3d-hub."""
import os
import runpy
import sys

from . import engine_file


def _run(name):
    path = engine_file(name)
    here = os.path.dirname(path)
    if here not in sys.path:
        sys.path.insert(0, here)
    sys.argv = [path] + sys.argv[1:]
    runpy.run_path(path, run_name="__main__")


def editor():
    """heaven3d [--project FILE] [--runtime] ...   (the same options as python Heaven3D.py)"""
    _run("Heaven3D.py")


def runtime():
    """heaven3d-runtime --project FILE: play a project without the editor."""
    _run("Heaven3D_runtime.py")


def hub():
    """heaven3d-hub: projects, templates, packages and tutorials."""
    _run("Heaven3D_Hub.py")
