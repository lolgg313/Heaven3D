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
import io
import os
import sys
import tokenize

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


def _string_rows(text):
    """Line numbers (1-based) that start INSIDE a multi-line string value.

    Those lines are string content, not code: indenting them changes the
    string.  (It used to: the shell scripts an export writes - a game's
    launcher and install-shortcut.sh - came out indented by 8 spaces, so
    install-shortcut.sh's here-document never ended at "EOF" and wrote a
    broken .desktop file.)  Docstrings and other bare string statements keep
    the old indentation; their whitespace means nothing."""
    skip = (tokenize.NL, tokenize.COMMENT)
    fs_start = getattr(tokenize, "FSTRING_START", None)          # Python 3.12+
    fs_end = getattr(tokenize, "FSTRING_END", None)
    toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in skip]
    rows = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        j = i
        if fs_start is not None and t.type == fs_start:
            depth = 0
            while j < len(toks):
                if toks[j].type == fs_start:
                    depth += 1
                elif toks[j].type == fs_end:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
        elif t.type != tokenize.STRING:
            i += 1
            continue
        end_row = toks[min(j, len(toks) - 1)].end[0]
        prev = toks[i - 1].type if i else tokenize.NEWLINE
        nxt = toks[j + 1].type if j + 1 < len(toks) else tokenize.ENDMARKER
        statement = (prev in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)
                     and nxt in (tokenize.NEWLINE, tokenize.ENDMARKER))
        if not statement and end_row > t.start[0]:
            rows.update(range(t.start[0] + 1, end_row + 1))
        i = j + 1
    return rows


def read_part(path, ind):
    full = os.path.join(SRC, *path.split("/"))
    with open(full, encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text.startswith(HEADER):                      # the part's own label line
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if ind:
        pad = " " * ind
        try:
            keep = _string_rows(text)
        except (SyntaxError, tokenize.TokenError) as ex:
            raise SystemExit(f"build.py: cannot tokenize src/{path}: {ex}")
        text = "\n".join((ln if ln == "" or n in keep else pad + ln)
                         for n, ln in enumerate(text.split("\n"), 1))
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
