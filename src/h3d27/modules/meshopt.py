# @heaven3d-part h3d27/modules/meshopt.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ------------------------------------------------ h3d_meshopt
# ---------------------------------------------------------------------------
#  meshoptimizer decoders (EXT_meshopt_compression / KHR_meshopt_compression)
#  Pure-Python port of zeux/meshoptimizer's scalar reference decoders
#  (vertexcodec.cpp v0+v1, indexcodec.cpp, vertexfilter.cpp).
# ---------------------------------------------------------------------------
import numpy as np


class MeshoptError(ValueError):
    pass


_BITS_V0 = (0, 2, 4, 8)
_BITS_V1 = (0, 1, 2, 4, 8)
_REV8 = bytes(int("{:08b}".format(i)[::-1], 2) for i in range(256))


def _block_size(vertex_size):
    r = (8192 // vertex_size) & ~15
    return min(r, 256)


def _decode_bytes_group(data, pos, out, opos, bits):
    if bits == 0:
        for k in range(16):
            out[opos + k] = 0
        return pos
    if bits == 8:
        out[opos:opos + 16] = data[pos:pos + 16]
        return pos + 16
    if bits == 1:
        var = pos + 2
        k = opos
        for b in (_REV8[data[pos]], _REV8[data[pos + 1]]):
            for s in range(7, -1, -1):
                enc = (b >> s) & 1
                if enc == 1:
                    out[k] = data[var]; var += 1
                else:
                    out[k] = enc
                k += 1
        return var
    if bits == 2:
        var = pos + 4
        k = opos
        for i in range(4):
            b = data[pos + i]
            for s in (6, 4, 2, 0):
                enc = (b >> s) & 3
                if enc == 3:
                    out[k] = data[var]; var += 1
                else:
                    out[k] = enc
                k += 1
        return var
    if bits == 4:
        var = pos + 8
        k = opos
        for i in range(8):
            b = data[pos + i]
            for s in (4, 0):
                enc = (b >> s) & 15
                if enc == 15:
                    out[k] = data[var]; var += 1
                else:
                    out[k] = enc
                k += 1
        return var
    raise MeshoptError("bad bit length")


def _decode_bytes(data, pos, out, opos, size, bits_table):
    header_size = (size // 16 + 3) // 4
    header = pos
    pos += header_size
    for i in range(0, size, 16):
        ho = i // 16
        bk = (data[header + ho // 4] >> ((ho % 4) * 2)) & 3
        pos = _decode_bytes_group(data, pos, out, opos + i, bits_table[bk])
        if pos > len(data):
            raise MeshoptError("meshopt vertex data truncated")
    return pos


def _unzigzag(v, bits):
    return (v >> 1) ^ (-(v & 1) & ((1 << bits) - 1))


def decode_vertex_buffer(data, count, vertex_size):
    data = bytes(data)
    if not data or (data[0] & 0xF0) != 0xA0:
        raise MeshoptError("not a meshopt vertex stream")
    version = data[0] & 0x0F
    if version > 1:
        raise MeshoptError(f"meshopt vertex codec v{version} is newer than supported")
    tail_size = vertex_size + (0 if version == 0 else vertex_size // 4)
    tail_min = 32 if version == 0 else 24
    tail_pad = max(tail_size, tail_min)
    if len(data) - 1 < tail_pad:
        raise MeshoptError("meshopt vertex stream too short")
    tail = len(data) - tail_size
    last = bytearray(data[tail:tail + vertex_size])
    channels = data[tail + vertex_size:tail + tail_size] if version else None
    out = np.zeros(count * vertex_size, np.uint8)
    pos = 1
    bsize = _block_size(vertex_size)
    voff = 0
    bits_v1 = _BITS_V1
    while voff < count:
        n = min(bsize, count - voff)
        aligned = (n + 15) & ~15
        ctrl_size = 0 if version == 0 else vertex_size // 4
        control = data[pos:pos + ctrl_size]
        pos += ctrl_size
        block = np.zeros((n, vertex_size), np.uint8)
        for k in range(0, vertex_size, 4):
            cb = 0 if version == 0 else control[k // 4]
            planes = []
            for j in range(4):
                if k + j >= vertex_size:
                    planes.append(np.zeros(n, np.uint8))
                    continue
                ctrl = (cb >> (j * 2)) & 3
                if ctrl == 3:
                    planes.append(np.frombuffer(data, np.uint8, n, pos).copy()); pos += n
                elif ctrl == 2:
                    planes.append(np.zeros(n, np.uint8))
                else:
                    buf = bytearray(aligned)
                    table = _BITS_V0 if version == 0 else bits_v1[ctrl:ctrl + 4]
                    pos = _decode_bytes(data, pos, buf, 0, aligned, table)
                    planes.append(np.frombuffer(bytes(buf[:n]), np.uint8))
            ch = 0 if version == 0 else channels[k // 4]
            width = min(4, vertex_size - k)
            lastk = last[k:k + 4]
            mode = ch & 3
            if mode == 0:                        # byte deltas
                for j in range(width):
                    v = planes[j].astype(np.int64)
                    d = (v >> 1) ^ (-(v & 1) & 0xFF)
                    block[:, k + j] = ((np.cumsum(d) + lastk[j]) & 0xFF).astype(np.uint8)
            elif mode == 1:                      # 16-bit deltas
                for h in range(0, 4, 2):
                    if k + h >= vertex_size:
                        break
                    v = planes[h].astype(np.int64) | (planes[h + 1].astype(np.int64) << 8)
                    d = (v >> 1) ^ (-(v & 1) & 0xFFFF)
                    p0 = lastk[h] | (lastk[h + 1] << 8)
                    vals = (np.cumsum(d) + p0) & 0xFFFF
                    block[:, k + h] = (vals & 0xFF).astype(np.uint8)
                    block[:, k + h + 1] = (vals >> 8).astype(np.uint8)
            elif mode == 2:                      # 32-bit xor + rotate
                rot = (32 - (ch >> 4)) & 31
                v = (planes[0].astype(np.uint64) | (planes[1].astype(np.uint64) << 8) |
                     (planes[2].astype(np.uint64) << 16) | (planes[3].astype(np.uint64) << 24))
                if rot:
                    v = ((v << np.uint64(rot)) | (v >> np.uint64((32 - rot) & 31))) & np.uint64(0xFFFFFFFF)
                p = int(lastk[0]) | (int(lastk[1]) << 8) | (int(lastk[2]) << 16) | (int(lastk[3]) << 24)
                acc = np.bitwise_xor.accumulate(np.concatenate([[np.uint64(p)], v]))[1:]
                for j in range(4):
                    block[:, k + j] = ((acc >> np.uint64(8 * j)) & np.uint64(0xFF)).astype(np.uint8)
            else:
                raise MeshoptError("invalid meshopt channel mode")
        out[voff * vertex_size:(voff + n) * vertex_size] = block.ravel()
        last = bytearray(block[n - 1].tobytes())
        voff += n
    if len(data) - pos != tail_pad:
        raise MeshoptError("meshopt vertex stream has trailing garbage")
    return out.tobytes()


def _vbyte(data, pos):
    lead = data[pos]; pos += 1
    if lead < 128:
        return lead, pos
    result = lead & 127
    shift = 7
    for _ in range(4):
        g = data[pos]; pos += 1
        result |= (g & 127) << shift
        shift += 7
        if g < 128:
            break
    return result & 0xFFFFFFFF, pos


def _decode_index(data, pos, last):
    v, pos = _vbyte(data, pos)
    d = (v >> 1) ^ (-(v & 1) & 0xFFFFFFFF)
    return (last + d) & 0xFFFFFFFF, pos


def decode_index_buffer(data, count):
    data = bytes(data)
    if (data[0] & 0xF0) != 0xE0:
        raise MeshoptError("not a meshopt triangle stream")
    version = data[0] & 0x0F
    if version > 1:
        raise MeshoptError(f"meshopt index codec v{version} unsupported")
    ef = [[0xFFFFFFFF, 0xFFFFFFFF] for _ in range(16)]
    vf = [0xFFFFFFFF] * 16
    efo = vfo = 0
    nxt = last = 0
    fecmax = 13 if version >= 1 else 15
    code = 1
    code_end = code + count // 3
    pos = code_end
    aux = data[len(data) - 16:]
    out = []
    while code < code_end:
        ct = data[code]; code += 1
        if ct < 0xF0:
            fe = ct >> 4
            a, b = ef[(efo - 1 - fe) & 15]
            fec = ct & 15
            if fec < fecmax:
                cf = vf[(vfo - 1 - fec) & 15]
                c = nxt if fec == 0 else cf
                f0 = 1 if fec == 0 else 0
                nxt += f0
                vf[vfo] = c; vfo = (vfo + f0) & 15
            else:
                if fec != 15:
                    c = (last + (fec * 2 - 27)) & 0xFFFFFFFF
                else:
                    c, pos = _decode_index(data, pos, last)
                last = c
                vf[vfo] = c; vfo = (vfo + 1) & 15
            ef[efo] = [c, b]; efo = (efo + 1) & 15
            ef[efo] = [a, c]; efo = (efo + 1) & 15
            out += (a, b, c)
        elif ct < 0xFE:
            ca = aux[ct & 15]
            feb, fec = ca >> 4, ca & 15
            a = nxt; nxt += 1
            bf = vf[(vfo - feb) & 15]
            b = nxt if feb == 0 else bf
            fb0 = 1 if feb == 0 else 0
            nxt += fb0
            cf = vf[(vfo - fec) & 15]
            c = nxt if fec == 0 else cf
            fc0 = 1 if fec == 0 else 0
            nxt += fc0
            out += (a, b, c)
            vf[vfo] = a; vfo = (vfo + 1) & 15
            vf[vfo] = b; vfo = (vfo + fb0) & 15
            vf[vfo] = c; vfo = (vfo + fc0) & 15
            ef[efo] = [b, a]; efo = (efo + 1) & 15
            ef[efo] = [c, b]; efo = (efo + 1) & 15
            ef[efo] = [a, c]; efo = (efo + 1) & 15
        else:
            ca = data[pos]; pos += 1
            fea = 0 if ct == 0xFE else 15
            feb, fec = ca >> 4, ca & 15
            if ca == 0:
                nxt = 0
            if fea == 0:
                a = nxt; nxt += 1
            else:
                a = 0
            if feb == 0:
                b = nxt; nxt += 1
            else:
                b = vf[(vfo - feb) & 15]
            if fec == 0:
                c = nxt; nxt += 1
            else:
                c = vf[(vfo - fec) & 15]
            if fea == 15:
                a, pos = _decode_index(data, pos, last); last = a
            if feb == 15:
                b, pos = _decode_index(data, pos, last); last = b
            if fec == 15:
                c, pos = _decode_index(data, pos, last); last = c
            out += (a, b, c)
            vf[vfo] = a; vfo = (vfo + 1) & 15
            vf[vfo] = b; vfo = (vfo + (1 if feb in (0, 15) else 0)) & 15
            vf[vfo] = c; vfo = (vfo + (1 if fec in (0, 15) else 0)) & 15
            ef[efo] = [b, a]; efo = (efo + 1) & 15
            ef[efo] = [c, b]; efo = (efo + 1) & 15
            ef[efo] = [a, c]; efo = (efo + 1) & 15
    return np.array(out, np.uint32)


def decode_index_sequence(data, count):
    data = bytes(data)
    if (data[0] & 0xF0) != 0xD0:
        raise MeshoptError("not a meshopt index-sequence stream")
    pos = 1
    last = [0, 0]
    out = np.empty(count, np.uint32)
    for i in range(count):
        v, pos = _vbyte(data, pos)
        cur = v & 1
        v >>= 1
        d = (v >> 1) ^ (-(v & 1) & 0xFFFFFFFF)
        idx = (last[cur] + d) & 0xFFFFFFFF
        last[cur] = idx
        out[i] = idx
    return out


# ---------------------------------------------------------------- filters
def _round_signed(x):
    return np.where(x >= 0, np.floor(x + 0.5), np.ceil(x - 0.5))


def filter_oct(buf, count, stride):
    t = np.int8 if stride == 4 else np.int16
    d = np.frombuffer(buf, t).reshape(count, 4).astype(np.float32)
    mx = np.float32(127.0 if stride == 4 else 32767.0)
    x, y = d[:, 0].copy(), d[:, 1].copy()
    z = d[:, 2] - np.abs(x) - np.abs(y)
    tt = np.where(z >= 0, 0.0, z)
    x = x + np.where(x >= 0, tt, -tt)
    y = y + np.where(y >= 0, tt, -tt)
    ln = np.sqrt(x * x + y * y + z * z)
    s = mx / np.maximum(ln, 1e-30)
    out = np.frombuffer(buf, t).reshape(count, 4).copy()
    out[:, 0] = _round_signed(x * s).astype(t)
    out[:, 1] = _round_signed(y * s).astype(t)
    out[:, 2] = _round_signed(z * s).astype(t)
    return out.tobytes()


def filter_quat(buf, count):
    d = np.frombuffer(buf, np.int16).reshape(count, 4)
    sf = (d[:, 3].astype(np.int32) | 3).astype(np.float32)
    x, y, z = (d[:, i].astype(np.float32) for i in range(3))
    ww = sf * sf * 2.0 - x * x - y * y - z * z
    w = np.sqrt(np.maximum(ww, 0.0))
    ss = np.float32(32767.0 / np.sqrt(2.0)) / sf
    xf = _round_signed(x * ss); yf = _round_signed(y * ss); zf = _round_signed(z * ss)
    wf = np.floor(w * ss + 0.5)
    qc = d[:, 3].astype(np.int32) & 3
    out = np.zeros((count, 4), np.int16)
    r = np.arange(count)
    out[r, (qc + 1) & 3] = xf; out[r, (qc + 2) & 3] = yf
    out[r, (qc + 3) & 3] = zf; out[r, qc & 3] = wf
    return out.tobytes()


def filter_exp(buf, count, stride):
    v = np.frombuffer(buf, np.uint32).astype(np.int64)
    m = ((v << 8) & 0xFFFFFFFF)
    m = np.where(m >= 2 ** 31, m - 2 ** 32, m) >> 8
    e = np.where(v >= 2 ** 31, v - 2 ** 32, v) >> 24
    f = (m.astype(np.float64) * np.power(2.0, e.astype(np.float64))).astype(np.float32)
    return f.tobytes()


def filter_color(buf, count, stride):
    if stride == 4:
        u = np.frombuffer(buf, np.uint8).reshape(count, 4).astype(np.int32)
        st = np.frombuffer(buf, np.int8).reshape(count, 4).astype(np.int32)
        mx, t = 255.0, np.uint8
    else:
        u = np.frombuffer(buf, np.uint16).reshape(count, 4).astype(np.int32)
        st = np.frombuffer(buf, np.int16).reshape(count, 4).astype(np.int32)
        mx, t = 65535.0, np.uint16
    a_s = u[:, 3].copy()
    a_s |= a_s >> 1; a_s |= a_s >> 2; a_s |= a_s >> 4; a_s |= a_s >> 8
    y, co, cg = u[:, 0], st[:, 1], st[:, 2]
    r = y + co - cg; g = y + cg; b = y - co - cg
    a = u[:, 3]
    a = ((a << 1) & a_s) | (a & 1)
    ss = np.float32(mx) / np.maximum(a_s, 1).astype(np.float32)
    out = np.stack([np.floor(r * ss + 0.5), np.floor(g * ss + 0.5), np.floor(b * ss + 0.5),
                    np.floor(a * ss + 0.5)], 1)
    return out.astype(np.int64).astype(t).tobytes()


def decode_meshopt(data, count, stride, mode="ATTRIBUTES", flt="NONE"):
    if mode == "ATTRIBUTES":
        raw = decode_vertex_buffer(data, count, stride)
        if flt == "OCTAHEDRAL":
            raw = filter_oct(raw, count, stride)
        elif flt == "QUATERNION":
            raw = filter_quat(raw, count)
        elif flt == "EXPONENTIAL":
            raw = filter_exp(raw, count, stride)
        elif flt == "COLOR":
            raw = filter_color(raw, count, stride)
        return raw
    if mode == "TRIANGLES":
        idx = decode_index_buffer(data, count)
    elif mode == "INDICES":
        idx = decode_index_sequence(data, count)
    else:
        raise MeshoptError(f"unknown meshopt mode {mode}")
    return idx.astype(np.uint16 if stride == 2 else np.uint32).tobytes()