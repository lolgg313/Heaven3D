#!/usr/bin/env python3
"""
Heaven3D's own zstd decoder (Blender 'Compress' .blend files) against the
reference zstandard library (no window or GPU needed):

    python tests/importers/run_zstd_tests.py

assets/zstd/*.zst were written by the reference compressor at levels -5..22,
with / without content size and checksum, as several frames with skippable
frames in between, RLE / raw / incompressible data.  Each must decode to the
exact bytes (sha256 in expected.json) with both decoder paths: the numba
kernels (plain Python without numba) and the faster plain-Python path.
Damaged or cut-off files must raise a clean error.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
D = os.path.join(HERE, "assets", "zstd")


def main():
    spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(H)
    exp = json.load(open(os.path.join(D, "expected.json")))
    res = []
    paths = {"kernels": (H._zs_huff_stream, H._zs_sequences),
             "python": (H._zs_huff_stream_py, H._zs_sequences_py)}
    for pname, (hf, sq) in paths.items():
        H._ZS_HUFF, H._ZS_SEQ = hf, sq
        t0 = time.time()
        for f in sorted(exp):
            try:
                out = H.h3d_zstd_decompress(open(os.path.join(D, f), "rb").read())
                ok = len(out) == exp[f]["size"] and hashlib.sha256(out).hexdigest() == exp[f]["sha256"]
                res.append(("%s: %s" % (pname, f), ok, "%d bytes" % len(out)))
            except Exception as ex:
                res.append(("%s: %s" % (pname, f), False, repr(ex)))
        res.append(("%s: all files" % pname, True, "%.2fs (numba %s)" % (time.time() - t0, H._HAS_NUMBA)))
    H._ZS_HUFF = H._zs_huff_stream if H._HAS_NUMBA else H._zs_huff_stream_py
    H._ZS_SEQ = H._zs_sequences if H._HAS_NUMBA else H._zs_sequences_py
    good = open(os.path.join(D, "text_3.zst"), "rb").read()
    bad_cases = {"cut in half": good[:len(good) // 2], "cut header": good[:5],
                 "not zstd": b"BLENDER-v300" + good, "damaged middle": good[:4000] + bytes(64) + good[4064:]}
    for name, data in bad_cases.items():
        try:
            out = H.h3d_zstd_decompress(data)
            ok = hashlib.sha256(out).hexdigest() != exp["text_3.zst"]["sha256"] and name == "damaged middle"
            res.append(("damaged: " + name, ok, "no error raised (%d bytes)" % len(out)))
        except H.H3DZstdError as ex:
            res.append(("damaged: " + name, True, "clean error: %s" % ex))
        except Exception as ex:
            res.append(("damaged: " + name, False, "crashed: %r" % ex))
    bad = 0
    for n, ok, d in res:
        bad += not ok
        print("%s  %-36s %s" % ("PASS" if ok else "FAIL", n, d))
    print("\n%d/%d passed" % (len(res) - bad, len(res)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
