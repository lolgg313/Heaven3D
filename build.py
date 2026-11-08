#!/usr/bin/env python3
"""Build the single-file Heaven3D engine from its modular source.

    python build.py            # writes Heaven3D.py and Heaven3D_runtime.py
    python build.py --check    # exit 1 if they are out of date (CI / pre-commit)
    python build.py --where 25120   # which src/ file holds line 25120 of Heaven3D.py

You work in src/ (see src/README.md); players and exported games keep
getting ONE file, Heaven3D.py, exactly as before.  The engine is
written as one linear program (each block patches what came before it),
so the parts are plain text concatenated in the order src/BUILD_ORDER
gives.  A part marked "indent=N" is stored without the N spaces of
indentation it has inside the engine (the importer modules, for example,
live inside one factory function) and gets them back here.

Heaven3D_runtime.py is the same engine, started in play mode by default
(python Heaven3D_runtime.py --project game.heaven3d).  It used to be a
separate, hand-kept copy and fell behind the engine; now it can't.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
ORDER = os.path.join(SRC, "BUILD_ORDER")
HEADER = "# @heaven3d-part "
RUNTIME_SWITCH = ('_H3D_DEFAULT_MODE = "editor"', '_H3D_DEFAULT_MODE = "runtime"')


def parts():
    with open(ORDER, encoding="utf-8") as fh:
        for raw in fh:
            ln = raw.strip()
            if not ln or ln.startswith("#"):
                continue
            path, _sp, opt = ln.partition(" ")
            ind = 0
            if opt.startswith("indent="):
                ind = int(opt.split("=", 1)[1])
            yield path, ind


def read_part(path, ind):
    full = os.path.join(SRC, *path.split("/"))
    with open(full, encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text.startswith(HEADER):                      # the part's own label line
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if ind:
        pad = " " * ind
        text = "\n".join(("" if ln == "" else pad + ln) for ln in text.split("\n"))
    return text


def build():
    engine = "\n".join(read_part(p, i) for p, i in parts())
    if engine.count(RUNTIME_SWITCH[0]) != 1:
        raise SystemExit("build.py: %r must appear exactly once in the engine source" % RUNTIME_SWITCH[0])
    runtime = engine.replace(RUNTIME_SWITCH[0], RUNTIME_SWITCH[1], 1)
    return {"Heaven3D.py": engine, "Heaven3D_runtime.py": runtime}


def where(line):
    """(part, line in that part's file) for a line of the built Heaven3D.py."""
    at = 1
    for path, ind in parts():
        n = read_part(path, ind).count("\n") + 1
        if line < at + n:
            return path, line - at + 2            # +1: 1-based, +1: the part's label line
        at += n
    return None, None


def main(argv):
    if "--where" in argv:
        line = int(argv[argv.index("--where") + 1])
        path, ln = where(line)
        print("Heaven3D.py:%d  ->  src/%s:%d" % (line, path, ln) if path else "beyond the end of Heaven3D.py")
        return 0
    out = build()
    stale = []
    for name, text in out.items():
        path = os.path.join(HERE, name)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                same = fh.read() == text
        except OSError:
            same = False
        if not same:
            stale.append(name)
            if "--check" not in argv:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
    if "--check" in argv:
        if stale:
            print("out of date: %s  (run: python build.py)" % ", ".join(stale))
            return 1
        print("up to date")
        return 0
    n = sum(1 for _ in parts())
    print("built %s from %d parts in src/%s" % (" + ".join(out), n,
          ("  (updated: %s)" % ", ".join(stale)) if stale else "  (no changes)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
