# @heaven3d-part importers/zstd.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
#  Zstandard decoder (RFC 8878) - Blender 3.0+ "Compress" .blend files.
#  Python 3.13 has no zstd in the standard library and Heaven3D adds no
#  extra packages, so this is a complete decoder in numpy: frames, skippable
#  frames (Blender's seek table), raw / RLE / compressed blocks, Huffman
#  literals (1 or 4 streams, treeless), FSE sequences (predefined / RLE /
#  compressed / repeat tables) and repeat offsets.  The hot loops run under
#  numba when it is installed (it is, with install.py) and in plain Python
#  otherwise.  Dictionaries are not supported (Blender never uses them).
# ---------------------------------------------------------------------------


class H3DZstdError(ValueError):
    pass


_ZS_LL_BASE = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 40,
                        48, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536], np.int64)
_ZS_LL_BITS = np.array([0] * 16 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], np.int64)
_ZS_ML_BASE = np.array(list(range(3, 35)) + [35, 37, 39, 41, 43, 47, 51, 59, 67, 83, 99, 131, 259, 515, 1027,
                                              2051, 4099, 8195, 16387, 32771, 65539], np.int64)
_ZS_ML_BITS = np.array([0] * 32 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], np.int64)
_ZS_LL_DEF = [4, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1, 1, 1, 1, 1,
              -1, -1, -1, -1]
_ZS_ML_DEF = [1, 4, 3, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
              1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1, -1, -1]
_ZS_OF_DEF = [1, 1, 1, 1, 1, 1, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1]


@_jit(cache=True)
def _zs_bits(src, start, lo, n):
    """n bits (n <= 32) at bit index lo of the stream starting at byte
    ``start`` (bit 0 = lowest bit of the first byte).  Bits before the
    stream start (lo < 0) read as zero, as the format requires."""
    if n <= 0:
        return 0
    pad = 0
    if lo < 0:
        if lo + n <= 0:
            return 0
        pad = -lo
        n += lo
        lo = 0
    first = lo >> 3
    shift = lo & 7
    nbytes = (shift + n + 7) >> 3
    acc = 0
    for i in range(nbytes):
        acc |= np.int64(src[start + first + i]) << (8 * i)
    return ((acc >> shift) & ((np.int64(1) << n) - 1)) << pad


@_jit(cache=True)
def _zs_stream_bits(src, start, end):
    """Number of data bits in a backward stream (the last byte's highest
    set bit is an end marker); -1 if the stream is malformed."""
    if end <= start:
        return -1
    last = np.int64(src[end - 1])
    if last == 0:
        return -1
    hb = 0
    while (last >> (hb + 1)) != 0:
        hb += 1
    return (end - start - 1) * 8 + hb


@_jit(cache=True)
def _zs_huff_stream(src, start, end, tsym, tnb, maxbits, out, opos, count):
    pos = _zs_stream_bits(src, start, end)
    if pos < 0:
        return -1
    for k in range(count):
        v = _zs_bits(src, start, pos - maxbits, maxbits)
        out[opos + k] = tsym[v]
        pos -= tnb[v]
    if pos != 0:
        return -1
    return opos + count


@_jit(cache=True)
def _zs_sequences(src, start, end, nseq, lls, lln, llb, llal, ofs, ofn, ofb, ofal, mls, mln, mlb, mlal,
                  ll_base, ll_bits, ml_base, ml_bits, lit, nlit, out, opos, reps):
    pos = _zs_stream_bits(src, start, end)
    if pos < 0:
        return -1
    pos -= llal
    sll = _zs_bits(src, start, pos, llal)
    pos -= ofal
    sof = _zs_bits(src, start, pos, ofal)
    pos -= mlal
    sml = _zs_bits(src, start, pos, mlal)
    lp = 0
    for i in range(nseq):
        ofc = ofs[sof]
        mlc = mls[sml]
        llc = lls[sll]
        pos -= ofc
        ofv = (np.int64(1) << ofc) + _zs_bits(src, start, pos, ofc)
        nb = ml_bits[mlc]
        pos -= nb
        ml = ml_base[mlc] + _zs_bits(src, start, pos, nb)
        nb = ll_bits[llc]
        pos -= nb
        ll = ll_base[llc] + _zs_bits(src, start, pos, nb)
        if ofv > 3:
            off = ofv - 3
            reps[2] = reps[1]
            reps[1] = reps[0]
            reps[0] = off
        else:
            idx = ofv
            if ll == 0:
                idx += 1
            if idx == 1:
                off = reps[0]
            elif idx == 2:
                off = reps[1]
                reps[1] = reps[0]
                reps[0] = off
            elif idx == 3:
                off = reps[2]
                reps[2] = reps[1]
                reps[1] = reps[0]
                reps[0] = off
            else:
                off = reps[0] - 1
                reps[2] = reps[1]
                reps[1] = reps[0]
                reps[0] = off
        if i != nseq - 1:
            nb = lln[sll]
            pos -= nb
            sll = llb[sll] + _zs_bits(src, start, pos, nb)
            nb = mln[sml]
            pos -= nb
            sml = mlb[sml] + _zs_bits(src, start, pos, nb)
            nb = ofn[sof]
            pos -= nb
            sof = ofb[sof] + _zs_bits(src, start, pos, nb)
        if lp + ll > nlit or off <= 0 or off > opos + ll or opos + ll + ml > len(out):
            return -1
        for k in range(ll):
            out[opos + k] = lit[lp + k]
        opos += ll
        lp += ll
        s = opos - off
        for k in range(ml):                      # byte by byte: matches may overlap
            out[opos + k] = out[s + k]
        opos += ml
    if pos != 0:
        return -1
    if opos + nlit - lp > len(out):
        return -1
    for k in range(nlit - lp):
        out[opos + k] = lit[lp + k]
    return opos + nlit - lp


# ---- plain-Python versions of the two hot loops (no numba): the same
# algorithm with a 64-bit window instead of per-read byte gathering and
# slice copies instead of byte loops - several times faster than running
# the numba kernels above as ordinary Python.
def _zs_py_reader(data, nbits_total):
    st = [nbits_total, -1, 0]                  # pos, window byte, window value

    def rd(n):
        if n <= 0:
            return 0
        pos = st[0] - n
        st[0] = pos
        wb = st[1]
        if wb < 0 or pos < wb * 8:
            wb = max(0, ((pos + n) >> 3) - 6)
            st[1] = wb
            st[2] = int.from_bytes(data[wb:wb + 8], "little")
        if pos >= 0:
            return (st[2] >> (pos - wb * 8)) & ((1 << n) - 1)
        return (st[2] << -pos) & ((1 << n) - 1)
    return rd, st


def _zs_huff_stream_py(src, start, end, tsym, tnb, maxbits, out, opos, count):
    nb = int(_zs_stream_bits(src, start, end))
    if nb < 0:
        return -1
    data = src[start:end].tobytes()
    sym, bits = tsym.tolist(), tnb.tolist()
    res = bytearray(count)
    mask = (1 << maxbits) - 1
    pos, wb, wv = nb, -1, 0
    for k in range(count):
        lo = pos - maxbits
        if wb < 0 or lo < wb * 8:
            wb = max(0, (pos >> 3) - 6)
            wv = int.from_bytes(data[wb:wb + 8], "little")
        v = ((wv >> (lo - wb * 8)) if lo >= 0 else (wv << -lo)) & mask
        res[k] = sym[v]
        pos -= bits[v]
    if pos != 0:
        return -1
    out[opos:opos + count] = np.frombuffer(bytes(res), np.uint8)
    return opos + count


def _zs_sequences_py(src, start, end, nseq, lls, lln, llb, llal, ofs, ofn, ofb, ofal, mls, mln, mlb, mlal,
                     ll_base, ll_bits, ml_base, ml_bits, lit, nlit, out, opos, reps):
    nb = int(_zs_stream_bits(src, start, end))
    if nb < 0:
        return -1
    rd, st = _zs_py_reader(src[start:end].tobytes(), nb)
    lls, lln, llb = lls.tolist(), lln.tolist(), llb.tolist()
    ofs, ofn, ofb = ofs.tolist(), ofn.tolist(), ofb.tolist()
    mls, mln, mlb = mls.tolist(), mln.tolist(), mlb.tolist()
    llB, llX, mlB, mlX = ll_base.tolist(), ll_bits.tolist(), ml_base.tolist(), ml_bits.tolist()
    r0, r1, r2 = int(reps[0]), int(reps[1]), int(reps[2])
    sll = rd(llal)
    sof = rd(ofal)
    sml = rd(mlal)
    lp = 0
    cap = len(out)
    for i in range(nseq):
        ofc = ofs[sof]
        mlc = mls[sml]
        llc = lls[sll]
        ofv = (1 << ofc) + rd(ofc)
        ml = mlB[mlc] + rd(mlX[mlc])
        ll = llB[llc] + rd(llX[llc])
        if ofv > 3:
            off = ofv - 3
            r0, r1, r2 = off, r0, r1
        else:
            idx = ofv + (1 if ll == 0 else 0)
            if idx == 1:
                off = r0
            elif idx == 2:
                off = r1
                r0, r1 = r1, r0
            elif idx == 3:
                off = r2
                r0, r1, r2 = r2, r0, r1
            else:
                off = r0 - 1
                r0, r1, r2 = off, r0, r1
        if i != nseq - 1:
            sll = llb[sll] + rd(lln[sll])
            sml = mlb[sml] + rd(mln[sml])
            sof = ofb[sof] + rd(ofn[sof])
        if lp + ll > nlit or off <= 0 or off > opos + ll or opos + ll + ml > cap:
            return -1
        if ll:
            out[opos:opos + ll] = lit[lp:lp + ll]
            opos += ll
            lp += ll
        s = opos - off
        if off >= ml:
            out[opos:opos + ml] = out[s:s + ml]
        else:                                    # overlapping: the pattern doubles each copy
            done = min(off, ml)
            out[opos:opos + done] = out[s:s + done]
            while done < ml:
                n = min(done, ml - done)
                out[opos + done:opos + done + n] = out[s:s + n]
                done += n
        opos += ml
    if st[0] != 0:
        return -1
    reps[0], reps[1], reps[2] = r0, r1, r2
    rest = nlit - lp
    if opos + rest > cap:
        return -1
    out[opos:opos + rest] = lit[lp:nlit]
    return opos + rest


def _zs_fse_table(norm, al):
    size = 1 << al
    sym = np.zeros(size, np.int64)
    high = size - 1
    nxt = []
    for s, c in enumerate(norm):
        if c == -1:
            sym[high] = s
            high -= 1
            nxt.append(1)
        else:
            nxt.append(max(c, 0))
    step = (size >> 1) + (size >> 3) + 3
    mask = size - 1
    p = 0
    for s, c in enumerate(norm):
        for _ in range(max(c, 0)):
            sym[p] = s
            p = (p + step) & mask
            while p > high:
                p = (p + step) & mask
    if p != 0:
        raise H3DZstdError("bad FSE table")
    nb = np.zeros(size, np.int64)
    base = np.zeros(size, np.int64)
    for u in range(size):
        s = int(sym[u])
        x = nxt[s]
        nxt[s] += 1
        b = al - (x.bit_length() - 1)
        nb[u] = b
        base[u] = (x << b) - size
    return sym, nb, base, al


def _zs_read_ncount(src, pos, end, max_sym, max_al):
    """FSE table description -> (normalized counts, accuracy log, new pos)."""
    bitpos = 0

    def bits(n):
        return int(_zs_bits(src, pos, bitpos, n))      # src is zero-padded at the end
    al = (int(src[pos]) & 15) + 5
    if al > max_al:
        raise H3DZstdError("FSE accuracy too large")
    bitpos = 4
    remaining = (1 << al) + 1
    threshold = 1 << al
    nbits = al + 1
    norm = []
    prev0 = False
    while remaining > 1 and len(norm) <= max_sym:
        if prev0:
            n0 = len(norm)
            while bits(16) == 0xFFFF:
                n0 += 24
                bitpos += 16
            while bits(2) == 3:
                n0 += 3
                bitpos += 2
            n0 += bits(2)
            bitpos += 2
            norm.extend([0] * (n0 - len(norm)))
            if len(norm) > max_sym:
                break
        mx = (2 * threshold - 1) - remaining
        v = bits(nbits)
        if (v & (threshold - 1)) < mx:
            c = v & (threshold - 1)
            bitpos += nbits - 1
        else:
            c = v & (2 * threshold - 1)
            if c >= threshold:
                c -= mx
            bitpos += nbits
        c -= 1
        remaining -= abs(c)
        norm.append(c)
        prev0 = c == 0
        while remaining < threshold:
            nbits -= 1
            threshold >>= 1
    if remaining != 1 or len(norm) > max_sym + 1:
        raise H3DZstdError("bad FSE table description")
    return norm, al, pos + ((bitpos + 7) >> 3)


def _zs_huff_weights_fse(src, pos, csize):
    norm, al, p = _zs_read_ncount(src, pos, pos + csize, 255, 6)
    sym, nb, base, _al = _zs_fse_table(norm, al)
    start, end = p, pos + csize
    bp = int(_zs_stream_bits(src, start, end))
    if bp < 0:
        raise H3DZstdError("bad Huffman weights")
    out = []

    def rd(n):
        nonlocal bp
        bp -= n
        return int(_zs_bits(src, start, bp, n))
    s1 = rd(al)
    s2 = rd(al)
    while len(out) < 255:
        out.append(int(sym[s1]))
        s1 = int(base[s1]) + rd(int(nb[s1]))
        if bp < 0:
            out.append(int(sym[s2]))
            break
        out.append(int(sym[s2]))
        s2 = int(base[s2]) + rd(int(nb[s2]))
        if bp < 0:
            out.append(int(sym[s1]))
            break
    return out


def _zs_huff_table(src, pos):
    """Huffman tree description at pos -> (symbols, nbits, maxbits, size)."""
    hb = int(src[pos])
    if hb < 128:
        weights = _zs_huff_weights_fse(src, pos + 1, hb)
        size = 1 + hb
    else:
        n = hb - 127
        size = 1 + (n + 1) // 2
        weights = []
        for i in range(n):
            b = int(src[pos + 1 + i // 2])
            weights.append(b >> 4 if i % 2 == 0 else b & 15)
    total = sum((1 << (w - 1)) for w in weights if w > 0)
    if total == 0:
        raise H3DZstdError("bad Huffman tree")
    maxbits = total.bit_length()
    left = (1 << maxbits) - total
    if left & (left - 1):
        raise H3DZstdError("bad Huffman tree")
    weights.append(left.bit_length())
    if maxbits > 11:
        raise H3DZstdError("Huffman codes too long")
    tsym = np.zeros(1 << maxbits, np.uint8)
    tnb = np.zeros(1 << maxbits, np.int64)
    p = 0
    for w in range(1, maxbits + 1):
        for s, ws in enumerate(weights):
            if ws == w:
                n = 1 << (w - 1)
                tsym[p:p + n] = s
                tnb[p:p + n] = maxbits + 1 - w
                p += n
    return (tsym, tnb, maxbits), size


_ZS_PREDEF = {}


def _zs_predef(kind):
    if not _ZS_PREDEF:
        _ZS_PREDEF["ll"] = _zs_fse_table(_ZS_LL_DEF, 6)
        _ZS_PREDEF["of"] = _zs_fse_table(_ZS_OF_DEF, 5)
        _ZS_PREDEF["ml"] = _zs_fse_table(_ZS_ML_DEF, 6)
    return _ZS_PREDEF[kind]


_ZS_HUFF = _zs_huff_stream if _HAS_NUMBA else _zs_huff_stream_py
_ZS_SEQ = _zs_sequences if _HAS_NUMBA else _zs_sequences_py


def _zs_block(src, pos, end, out, opos, st):
    # ---- literals ----
    b0 = int(src[pos])
    ltype = b0 & 3
    sf = (b0 >> 2) & 3
    if ltype in (0, 1):
        if sf in (0, 2):
            regen, hsz = b0 >> 3, 1
        elif sf == 1:
            regen, hsz = (b0 >> 4) + (int(src[pos + 1]) << 4), 2
        else:
            regen, hsz = (b0 >> 4) + (int(src[pos + 1]) << 4) + (int(src[pos + 2]) << 12), 3
        pos += hsz
        if pos + (regen if ltype == 0 else 1) > end:
            raise H3DZstdError("literals run past the block")
        if ltype == 0:
            lit = src[pos:pos + regen]
            pos += regen
        else:
            lit = np.full(regen, src[pos], np.uint8)
            pos += 1
    else:
        hsz, bits = {0: (3, 10), 1: (3, 10), 2: (4, 14), 3: (5, 18)}[sf]
        v = int.from_bytes(bytes(src[pos:pos + hsz]), "little")
        mask = (1 << bits) - 1
        regen = (v >> 4) & mask
        csize = (v >> (4 + bits)) & mask
        streams = 1 if sf == 0 else 4
        pos += hsz
        lend = pos + csize
        if lend > end or regen > (1 << 17):
            raise H3DZstdError("literals run past the block")
        if ltype == 2:
            st["huf"], tsz = _zs_huff_table(src, pos)
            pos += tsz
        elif st.get("huf") is None:
            raise H3DZstdError("treeless literals without a previous tree")
        tsym, tnb, maxbits = st["huf"]
        lit = np.empty(regen, np.uint8)
        if streams == 1:
            if _ZS_HUFF(src, pos, lend, tsym, tnb, maxbits, lit, 0, regen) < 0:
                raise H3DZstdError("bad literals stream")
        else:
            s1 = int(src[pos]) | (int(src[pos + 1]) << 8)
            s2 = int(src[pos + 2]) | (int(src[pos + 3]) << 8)
            s3 = int(src[pos + 4]) | (int(src[pos + 5]) << 8)
            q = (regen + 3) // 4
            p0 = pos + 6
            bounds = [p0, p0 + s1, p0 + s1 + s2, p0 + s1 + s2 + s3, lend]
            if bounds[3] > lend:
                raise H3DZstdError("bad literal stream sizes")
            o = 0
            for k in range(4):
                n = q if k < 3 else regen - 3 * q
                if n < 0 or _ZS_HUFF(src, bounds[k], bounds[k + 1], tsym, tnb, maxbits, lit, o, n) < 0:
                    raise H3DZstdError("bad literals stream")
                o += n
        pos = lend
    # ---- sequences ----
    if pos >= end:
        nseq = 0
    else:
        b = int(src[pos])
        if b < 128:
            nseq, pos = b, pos + 1
        elif b < 255:
            nseq, pos = ((b - 128) << 8) + int(src[pos + 1]), pos + 2
        else:
            nseq, pos = int(src[pos + 1]) + (int(src[pos + 2]) << 8) + 0x7F00, pos + 3
    if nseq == 0:
        n = len(lit)
        if opos + n > len(out):
            raise H3DZstdError("block larger than the frame")
        out[opos:opos + n] = lit
        return opos + n
    modes = int(src[pos])
    pos += 1
    tabs = {}
    for kind, shift, mx, mal in (("ll", 6, 35, 9), ("of", 4, 31, 8), ("ml", 2, 52, 9)):
        mode = (modes >> shift) & 3
        if mode == 0:
            t = _zs_predef(kind)
        elif mode == 1:
            if int(src[pos]) > mx:
                raise H3DZstdError("bad RLE symbol")
            t = (np.array([int(src[pos])], np.int64), np.zeros(1, np.int64), np.zeros(1, np.int64), 0)
            pos += 1
        elif mode == 2:
            norm, al, pos = _zs_read_ncount(src, pos, end, mx, mal)
            t = _zs_fse_table(norm, al)
        else:
            t = st.get(kind)
            if t is None:
                raise H3DZstdError("repeat table without a previous one")
        st[kind] = tabs[kind] = t
    if pos > end:
        raise H3DZstdError("sequences run past the block")
    ll, of, ml = tabs["ll"], tabs["of"], tabs["ml"]
    r = _ZS_SEQ(src, pos, end, nseq, ll[0], ll[1], ll[2], ll[3], of[0], of[1], of[2], of[3],
                      ml[0], ml[1], ml[2], ml[3], _ZS_LL_BASE, _ZS_LL_BITS, _ZS_ML_BASE, _ZS_ML_BITS,
                      np.ascontiguousarray(lit), len(lit), out, opos, st["reps"])
    if r < 0:
        raise H3DZstdError("bad sequences")
    return r


def h3d_zstd_decompress(data):
    """bytes (one or more zstd frames, skippable frames allowed) -> bytes."""
    n = len(data)
    src = np.frombuffer(bytes(data) + bytes(16), np.uint8)
    pos = 0
    pieces = []
    while pos + 4 <= n:
        magic = int.from_bytes(bytes(src[pos:pos + 4]), "little")
        if (magic & 0xFFFFFFF0) == 0x184D2A50:            # skippable frame
            size = int.from_bytes(bytes(src[pos + 4:pos + 8]), "little")
            pos += 8 + size
            continue
        if magic != 0xFD2FB528:
            raise H3DZstdError("not a zstd frame")
        pos += 4
        fhd = int(src[pos])
        pos += 1
        fcs_flag, single, checksum, did = fhd >> 6, (fhd >> 5) & 1, (fhd >> 2) & 1, fhd & 3
        if fhd & 8:
            raise H3DZstdError("reserved frame header bit set")
        wsize = 0
        if not single:
            wd = int(src[pos])
            pos += 1
            e, m = wd >> 3, wd & 7
            wb = 1 << (10 + e)
            wsize = wb + (wb // 8) * m
        dsz = (0, 1, 2, 4)[did]
        if dsz and int.from_bytes(bytes(src[pos:pos + dsz]), "little"):
            raise H3DZstdError("zstd dictionaries are not supported")
        pos += dsz
        fsz = (1 if single else 0, 2, 4, 8)[fcs_flag]
        fcs = None
        if fsz:
            fcs = int.from_bytes(bytes(src[pos:pos + fsz]), "little") + (256 if fsz == 2 else 0)
            pos += fsz
        cap = fcs if fcs is not None else max(wsize, 1 << 17)
        out = np.empty(cap + 8, np.uint8)
        opos = 0
        st = {"reps": np.array([1, 4, 8], np.int64), "huf": None}
        while True:
            if pos + 3 > n:
                raise H3DZstdError("truncated zstd frame")
            bh = int(src[pos]) | (int(src[pos + 1]) << 8) | (int(src[pos + 2]) << 16)
            pos += 3
            last, btype, bsize = bh & 1, (bh >> 1) & 3, bh >> 3
            if pos + (1 if btype == 1 else bsize) > n or bsize > (1 << 17):     # blocks are <= 128 KiB
                raise H3DZstdError("truncated zstd frame")
            need = bsize if btype != 2 else (1 << 17)
            if opos + need > len(out) - 8:
                grow = np.empty(max(len(out) * 2, opos + need + 8), np.uint8)
                grow[:opos] = out[:opos]
                out = grow
            if btype == 0:
                out[opos:opos + bsize] = src[pos:pos + bsize]
                opos += bsize
                pos += bsize
            elif btype == 1:
                out[opos:opos + bsize] = src[pos]
                opos += bsize
                pos += 1
            elif btype == 2:
                opos = _zs_block(src, pos, pos + bsize, out, opos, st)
                pos += bsize
            else:
                raise H3DZstdError("reserved block type")
            if last:
                break
        if checksum:
            pos += 4
        if fcs is not None and opos != fcs:
            raise H3DZstdError("frame size mismatch")
        pieces.append(out[:opos].tobytes())
    return b"".join(pieces)
