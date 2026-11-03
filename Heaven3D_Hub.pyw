"""Double-click launcher for the Heaven3D Hub on Windows (runs without a console window)."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.argv = [os.path.join(HERE, "Heaven3D_Hub.py")] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
