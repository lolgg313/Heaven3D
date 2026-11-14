# @heaven3d-part physics/physics_kernels.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [PHYSICS:kernels]  Contacts and the contact solver as compiled kernels
#
#   While a pile of bodies is moving, most of a physics step went into three
#   pieces of plain Python arithmetic: the box-box contact (separating axis
#   test + corner containment), the box-plane contact, and the sequential
#   impulse solver (velocity iterations + position correction).  They are
#   now numba kernels.
#
#   Same results by construction: every kernel is a transcription of the
#   Python code next to it in physics.py, with the SAME operation order and
#   the SAME precision at every step (pyglm vectors are 32-bit floats and
#   compute in 32 bits; what the Python code held in a Python float stays a
#   64-bit float).  tests/test_physics_kernels.py runs both versions on the
#   same input, thousands of times, inside a live simulation.
#
#   Without numba, for a handful of contacts, or on any error the original
#   Python code runs.  HEAVEN3D_NO_PHYS_KERNELS=1 turns the kernels off.
# ---------------------------------------------------------------------------
_PK_ON = _HAS_NUMBA and os.environ.get("HEAVEN3D_NO_PHYS_KERNELS", "") not in ("1", "true", "yes")
_PK_STATE = {"on": _PK_ON, "errors": 0}

_PK_PY_BOX_BOX = _contact_box_box
_PK_PY_BOX_PLANE = _contact_box_plane
_PK_PY_SOLVE_VEL = PhysicsWorld._solve_contacts_velocity
_PK_PY_SOLVE_ITER = PhysicsWorld._solve_contact_velocity_iter
_PK_PY_SOLVE_POS = PhysicsWorld._solve_contact_position


@_jit(cache=True)
def _pk_dot(ax, ay, az, bx, by, bz):
    """glm.dot of two float32 vectors: float32 products, summed left to right."""
    return ax * bx + ay * by + az * bz


@_jit(cache=True)
def _pk_len(x, y, z):
    """glm.length of a float32 vector (float32 square root)."""
    return np.float32(math.sqrt(np.float64(x * x + y * y + z * z)))


@_jit(cache=True)
def _pk_box_box(acx, acy, acz, a0x, a0y, a0z, a1x, a1y, a1z, a2x, a2y, a2z, ahx, ahy, ahz,
                bcx, bcy, bcz, b0x, b0y, b0z, b1x, b1y, b1z, b2x, b2y, b2z, bhx, bhy, bhz):
    """_sat_obb_obb + _contact_box_box.  -> (hit, px, py, pz, nx, ny, nz, depth)"""
    f = np.float32
    c = np.empty((2, 3), f)                 # centres
    e = np.empty((2, 3, 3), f)              # axes  e[box, axis, xyz]
    h = np.empty((2, 3), np.float64)        # half sizes (read as Python floats by the original)
    c[0, 0] = acx; c[0, 1] = acy; c[0, 2] = acz
    c[1, 0] = bcx; c[1, 1] = bcy; c[1, 2] = bcz
    e[0, 0, 0] = a0x; e[0, 0, 1] = a0y; e[0, 0, 2] = a0z
    e[0, 1, 0] = a1x; e[0, 1, 1] = a1y; e[0, 1, 2] = a1z
    e[0, 2, 0] = a2x; e[0, 2, 1] = a2y; e[0, 2, 2] = a2z
    e[1, 0, 0] = b0x; e[1, 0, 1] = b0y; e[1, 0, 2] = b0z
    e[1, 1, 0] = b1x; e[1, 1, 1] = b1y; e[1, 1, 2] = b1z
    e[1, 2, 0] = b2x; e[1, 2, 1] = b2y; e[1, 2, 2] = b2z
    h[0, 0] = ahx; h[0, 1] = ahy; h[0, 2] = ahz
    h[1, 0] = bhx; h[1, 1] = bhy; h[1, 2] = bhz

    R = np.empty((3, 3), np.float64)
    AbsR = np.empty((3, 3), np.float64)
    for i in range(3):
        for j in range(3):
            R[i, j] = _pk_dot(e[0, i, 0], e[0, i, 1], e[0, i, 2], e[1, j, 0], e[1, j, 1], e[1, j, 2])
            AbsR[i, j] = abs(R[i, j]) + 1e-6
    dx = c[1, 0] - c[0, 0]                  # B.c - A.c (float32)
    dy = c[1, 1] - c[0, 1]
    dz = c[1, 2] - c[0, 2]
    tA = np.empty(3, np.float64)
    tB = np.empty(3, np.float64)
    for i in range(3):
        tA[i] = _pk_dot(dx, dy, dz, e[0, i, 0], e[0, i, 1], e[0, i, 2])
        tB[i] = _pk_dot(dx, dy, dz, e[1, i, 0], e[1, i, 1], e[1, i, 2])

    best = np.inf
    nx = f(0.0); ny = f(0.0); nz = f(0.0)
    found = False
    # A's faces
    for i in range(3):
        ra = h[0, i]
        rb = h[1, 0] * AbsR[i, 0] + h[1, 1] * AbsR[i, 1] + h[1, 2] * AbsR[i, 2]
        overlap = ra + rb - abs(tA[i])
        if overlap <= 0.0:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        if overlap < best:
            best = overlap
            nx = e[0, i, 0]; ny = e[0, i, 1]; nz = e[0, i, 2]
            found = True
    # B's faces
    for j in range(3):
        ra = h[0, 0] * AbsR[0, j] + h[0, 1] * AbsR[1, j] + h[0, 2] * AbsR[2, j]
        rb = h[1, j]
        overlap = ra + rb - abs(tB[j])
        if overlap <= 0.0:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        if overlap < best:
            best = overlap
            nx = e[1, j, 0]; ny = e[1, j, 1]; nz = e[1, j, 2]
            found = True
    # 9 edge-edge axes
    for i in range(3):
        for j in range(3):
            xx = e[0, i, 0]; xy = e[0, i, 1]; xz = e[0, i, 2]
            yx = e[1, j, 0]; yy = e[1, j, 1]; yz = e[1, j, 2]
            cx = xy * yz - yy * xz          # glm.cross
            cy = xz * yx - yz * xx
            cz = xx * yy - yx * xy
            ln = _pk_len(cx, cy, cz)
            if np.float64(ln) < 1e-6:
                continue
            cx = cx / ln; cy = cy / ln; cz = cz / ln
            i1 = (i + 1) % 3; i2 = (i + 2) % 3
            j1 = (j + 1) % 3; j2 = (j + 2) % 3
            ra = h[0, i1] * AbsR[i2, j] + h[0, i2] * AbsR[i1, j]
            rb = h[1, j1] * AbsR[i, j2] + h[1, j2] * AbsR[i, j1]
            dist = abs(tA[i1] * R[i2, j] - tA[i2] * R[i1, j])
            overlap = ra + rb - dist
            if overlap <= 0.0:
                return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            if overlap < best:
                best = overlap
                nx = cx; ny = cy; nz = cz
                found = True
    if not found:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    if np.float64(_pk_dot(c[0, 0] - c[1, 0], c[0, 1] - c[1, 1], c[0, 2] - c[1, 2], nx, ny, nz)) < 0.0:
        zero = f(0.0)                       # pyglm negates as 0 - v (a zero stays +0)
        nx = zero - nx; ny = zero - ny; nz = zero - nz

    # corners of each box inside the other one
    px = f(0.0); py = f(0.0); pz = f(0.0)
    count = 0
    for X in range(2):
        Y = 1 - X
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    kx = f(h[X, 0] * sx); ky = f(h[X, 1] * sy); kz = f(h[X, 2] * sz)
                    vx = c[X, 0] + e[X, 0, 0] * kx + e[X, 1, 0] * ky + e[X, 2, 0] * kz
                    vy = c[X, 1] + e[X, 0, 1] * kx + e[X, 1, 1] * ky + e[X, 2, 1] * kz
                    vz = c[X, 2] + e[X, 0, 2] * kx + e[X, 1, 2] * ky + e[X, 2, 2] * kz
                    qx = vx - c[Y, 0]; qy = vy - c[Y, 1]; qz = vz - c[Y, 2]
                    inside = True
                    for i in range(3):
                        if abs(np.float64(_pk_dot(qx, qy, qz, e[Y, i, 0], e[Y, i, 1], e[Y, i, 2]))) > h[Y, i]:
                            inside = False
                            break
                    if inside:
                        px = px + vx; py = py + vy; pz = pz + vz
                        count += 1
    if count:
        k = f(count)
        px = px / k; py = py / k; pz = pz / k
    else:
        half = f(0.5)
        px = (c[0, 0] + c[1, 0]) * half
        py = (c[0, 1] + c[1, 1]) * half
        pz = (c[0, 2] + c[1, 2]) * half
    return 1.0, np.float64(px), np.float64(py), np.float64(pz), np.float64(nx), np.float64(ny), np.float64(nz), best


@_jit(cache=True)
def _pk_box_plane(bcx, bcy, bcz, a0x, a0y, a0z, a1x, a1y, a1z, a2x, a2y, a2z, hx, hy, hz,
                  qpx, qpy, qpz, qnx, qny, qnz, bounded, qux, quy, quz, qwx, qwy, qwz, phx, phz, out):
    """_contact_box_plane: fills out[k] = (corner xyz, depth) -> number of contacts."""
    f = np.float32
    cx = f(bcx); cy = f(bcy); cz = f(bcz)                  # (new names: the 32-bit values)
    e0x = f(a0x); e0y = f(a0y); e0z = f(a0z)
    e1x = f(a1x); e1y = f(a1y); e1z = f(a1z)
    e2x = f(a2x); e2y = f(a2y); e2z = f(a2z)
    ppx = f(qpx); ppy = f(qpy); ppz = f(qpz)
    pnx = f(qnx); pny = f(qny); pnz = f(qnz)
    ux = f(qux); uy = f(quy); uz = f(quz)
    wx = f(qwx); wy = f(qwy); wz = f(qwz)
    n = 0
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                kx = f(hx * sx); ky = f(hy * sy); kz = f(hz * sz)
                vx = cx + e0x * kx + e1x * ky + e2x * kz
                vy = cy + e0y * kx + e1y * ky + e2y * kz
                vz = cz + e0z * kx + e1z * ky + e2z * kz
                qx = vx - ppx; qy = vy - ppy; qz = vz - ppz
                dist = np.float64(_pk_dot(qx, qy, qz, pnx, pny, pnz))
                if dist < 0.0:
                    if bounded:
                        if not (abs(np.float64(_pk_dot(qx, qy, qz, ux, uy, uz))) <= phx
                                and abs(np.float64(_pk_dot(qx, qy, qz, wx, wy, wz))) <= phz):
                            continue
                    out[n, 0] = vx; out[n, 1] = vy; out[n, 2] = vz; out[n, 3] = -dist
                    n += 1
    return n


@_jit(cache=True)
def _pk_solve(iters, ia, ib, has_a, has_b, ima, imb, nrm, jn, jt0, fric, rest, vel):
    """PhysicsWorld._solve_contacts_velocity: Gauss-Seidel over every contact
    per iteration, stops early when the largest impulse change is tiny."""
    f = np.float32
    nc = ia.shape[0]
    for _it in range(iters):
        max_dj = 0.0
        for k in range(nc):
            a = ia[k]; b = ib[k]
            nx = nrm[k, 0]; ny = nrm[k, 1]; nz = nrm[k, 2]
            rx = vel[a, 0] - vel[b, 0]; ry = vel[a, 1] - vel[b, 1]; rz = vel[a, 2] - vel[b, 2]
            vn = np.float64(_pk_dot(rx, ry, rz, nx, ny, nz))
            if vn > 0.0 and jn[k] > 0.0:
                continue
            ims = ima[k] + imb[k]
            if ims <= 0.0:
                continue
            e = rest[k] if vn < -1.0 else 0.0
            j = -(1.0 + e) * vn / ims
            j_new = jn[k] + j
            if not (j_new >= 0.0):          # max(j_new, 0.0)
                j_new = 0.0
            j = j_new - jn[k]
            jn[k] = j_new
            dj = abs(j)
            jf = f(j)
            ix = nx * jf; iy = ny * jf; iz = nz * jf
            if has_a[k]:
                m = f(ima[k])
                vel[a, 0] += ix * m; vel[a, 1] += iy * m; vel[a, 2] += iz * m
            if has_b[k]:
                m = f(imb[k])
                vel[b, 0] -= ix * m; vel[b, 1] -= iy * m; vel[b, 2] -= iz * m
            # friction (one tangential direction, Coulomb clamp)
            rx = vel[a, 0] - vel[b, 0]; ry = vel[a, 1] - vel[b, 1]; rz = vel[a, 2] - vel[b, 2]
            d = f(np.float64(_pk_dot(rx, ry, rz, nx, ny, nz)))
            tx = rx - nx * d; ty = ry - ny * d; tz = rz - nz * d
            vtl = _pk_len(tx, ty, tz)
            if np.float64(vtl) > 1e-6:
                tx = tx / vtl; ty = ty / vtl; tz = tz / vtl
                jt = -np.float64(_pk_dot(rx, ry, rz, tx, ty, tz)) / ims
                max_f = fric[k] * jn[k]
                tot = jt0[k] + jt
                if max_f < tot:             # _clamp(tot, -max_f, max_f) = max(lo, min(hi, v))
                    tot = max_f
                if tot < -max_f:
                    tot = -max_f
                jt = tot - jt0[k]
                jt0[k] = tot
                if abs(jt) > dj:
                    dj = abs(jt)
                jf = f(jt)
                ix = tx * jf; iy = ty * jf; iz = tz * jf
                if has_a[k]:
                    m = f(ima[k])
                    vel[a, 0] += ix * m; vel[a, 1] += iy * m; vel[a, 2] += iz * m
                if has_b[k]:
                    m = f(imb[k])
                    vel[b, 0] -= ix * m; vel[b, 1] -= iy * m; vel[b, 2] -= iz * m
            if dj > max_dj:
                max_dj = dj
        if max_dj < 1e-5:
            break


@_jit(cache=True)
def _pk_correct(ia, ib, has_a, has_b, ima, imb, nrm, depth, pos, moved):
    """PhysicsWorld._solve_contact_position for every contact, in order."""
    f = np.float32
    for k in range(ia.shape[0]):
        ims = ima[k] + imb[k]
        if ims <= 0.0:
            continue
        over = depth[k] - 0.005
        if not (over >= 0.0):
            over = 0.0
        mag = over * 0.6 / ims
        if mag < 1e-4:
            continue
        mf = f(mag)
        cx = nrm[k, 0] * mf; cy = nrm[k, 1] * mf; cz = nrm[k, 2] * mf
        if has_a[k] and ima[k] > 0.0:
            a = ia[k]
            m = f(ima[k])
            pos[a, 0] += cx * m; pos[a, 1] += cy * m; pos[a, 2] += cz * m
            moved[a] = True
        if has_b[k] and imb[k] > 0.0:
            b = ib[k]
            m = f(imb[k])
            pos[b, 0] -= cx * m; pos[b, 1] -= cy * m; pos[b, 2] -= cz * m
            moved[b] = True


# ---- the Python side ------------------------------------------------------
def _pk_fail(what, ex):
    _PK_STATE["errors"] += 1
    if _PK_STATE["errors"] <= 3:
        Log.warn(f"[Physics] compiled {what} failed ({type(ex).__name__}: {ex}); the Python code is used")
    if _PK_STATE["errors"] > 20:             # something is really wrong here: stop trying
        _PK_STATE["on"] = False


def _pk_contact_box_box(A, B):
    """SAT detection + vertex-face contact points (compiled)."""
    if not _PK_STATE["on"]:
        return _PK_PY_BOX_BOX(A, B)
    try:
        ac, ae, ah = A["c"], A["e"], A["h"]
        bc, be, bh = B["c"], B["e"], B["h"]
        a0, a1, a2 = ae
        b0, b1, b2 = be
        r = _pk_box_box(ac.x, ac.y, ac.z, a0.x, a0.y, a0.z, a1.x, a1.y, a1.z, a2.x, a2.y, a2.z, ah.x, ah.y, ah.z,
                        bc.x, bc.y, bc.z, b0.x, b0.y, b0.z, b1.x, b1.y, b1.z, b2.x, b2.y, b2.z, bh.x, bh.y, bh.z)
    except Exception as ex:
        _pk_fail("box-box contact", ex)
        return _PK_PY_BOX_BOX(A, B)
    if r[0] == 0.0:
        return None
    return [(glm.vec3(r[1], r[2], r[3]), glm.vec3(r[4], r[5], r[6]), r[7])]


_PK_PLANE_OUT = np.empty((8, 4), np.float64)


def _pk_contact_box_plane(box, plane, box_is_a):
    """Box corners vs plane: one contact per penetrating corner (compiled)."""
    if not _PK_STATE["on"]:
        return _PK_PY_BOX_PLANE(box, plane, box_is_a)
    try:
        c, h = box["c"], box["h"]
        e0, e1, e2 = box["e"]
        p, n = plane["p"], plane["n"]
        bounded = bool(plane["bounded"])
        out = _PK_PLANE_OUT
        if bounded:
            u, w = plane["u"], plane["v"]
            k = _pk_box_plane(c.x, c.y, c.z, e0.x, e0.y, e0.z, e1.x, e1.y, e1.z, e2.x, e2.y, e2.z, h.x, h.y, h.z,
                              p.x, p.y, p.z, n.x, n.y, n.z, True, u.x, u.y, u.z, w.x, w.y, w.z,
                              float(plane["hx"]), float(plane["hz"]), out)
        else:
            k = _pk_box_plane(c.x, c.y, c.z, e0.x, e0.y, e0.z, e1.x, e1.y, e1.z, e2.x, e2.y, e2.z, h.x, h.y, h.z,
                              p.x, p.y, p.z, n.x, n.y, n.z, False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, out)
    except Exception as ex:
        _pk_fail("box-plane contact", ex)
        return _PK_PY_BOX_PLANE(box, plane, box_is_a)
    if not k:
        return None
    nn = n if box_is_a else -n
    rows = out[:k].tolist()
    return [(glm.vec3(r[0], r[1], r[2]), nn, r[3]) for r in rows]


_PK_MIN_CONTACTS = 12       # below this the Python loop is as fast as filling the arrays


_PK_GATHER = [None]         # the last gathered list: the velocity and the position pass share it


def _pk_gather(contacts):
    """Flat arrays of a contact list.  -> (bodies, ia, ib, has_a, has_b, ima, imb, normals)"""
    hit = _PK_GATHER[0]
    if (hit is not None and hit[0] is contacts and hit[1] == len(contacts)
            and hit[2] is contacts[0] and hit[3] is contacts[-1]):
        return hit[4]
    ea = [c.a for c in contacts]
    eb = [c.b for c in contacts]
    ba = [e.body for e in ea]
    bb = [e.body for e in eb]
    index = {}
    bodies = []
    for body in ba + bb:
        if body is not None and id(body) not in index:
            index[id(body)] = len(bodies)
            bodies.append(body)
    nb = len(bodies)                        # (a static entry reads the row of zeros after the bodies)
    ia = np.array([nb if b is None else index[id(b)] for b in ba], np.int64)
    ib = np.array([nb if b is None else index[id(b)] for b in bb], np.int64)
    ha = np.array([b is not None for b in ba], np.bool_)
    hb = np.array([b is not None for b in bb], np.bool_)
    ima = np.array([0.0 if b is None else b.inv_mass for b in ba], np.float64)
    imb = np.array([0.0 if b is None else b.inv_mass for b in bb], np.float64)
    nrm = np.array([(n.x, n.y, n.z) for n in [c.normal for c in contacts]], np.float32).reshape(-1, 3)
    data = (bodies, ia, ib, ha, hb, ima, imb, nrm)
    _PK_GATHER[0] = (contacts, len(contacts), contacts[0], contacts[-1], data)
    return data


def _pk_solve_contacts_velocity(self, contacts):
    """Gauss-Seidel over ALL contacts per iteration (compiled; the same
    steps as the Python solver, see _pk_solve)."""
    if (not _PK_STATE["on"] or len(contacts) < _PK_MIN_CONTACTS
            or type(self)._solve_contact_velocity_iter is not _PK_PY_SOLVE_ITER):
        return _PK_PY_SOLVE_VEL(self, contacts)        # (a subclass with its own solver keeps it)
    try:
        bodies, ia, ib, ha, hb, ima, imb, nrm = _pk_gather(contacts)
        nb = len(bodies)
        vel = np.zeros((nb + 1, 3), np.float32)
        if nb:
            vel[:nb] = [(v.x, v.y, v.z) for v in [b.velocity for b in bodies]]
        jn = np.array([c.jn for c in contacts], np.float64)
        jt0 = np.array([c.jt0 for c in contacts], np.float64)
        fric = np.array([c.friction for c in contacts], np.float64)
        rest = np.array([c.restitution for c in contacts], np.float64)
        if not (np.isfinite(vel).all() and np.isfinite(jn).all() and np.isfinite(jt0).all()):
            return _PK_PY_SOLVE_VEL(self, contacts)    # (the Python code defines what a NaN does)
        _pk_solve(int(self.SOLVER_ITERATIONS), ia, ib, ha, hb, ima, imb, nrm, jn, jt0, fric, rest, vel)
    except Exception as ex:
        _pk_fail("contact solver", ex)
        return _PK_PY_SOLVE_VEL(self, contacts)
    for b, row in zip(bodies, vel.tolist()):
        v = b.velocity
        v.x, v.y, v.z = row
    for c, a, t in zip(contacts, jn.tolist(), jt0.tolist()):
        c.jn = a
        c.jt0 = t


def _pk_solve_contacts_position(self, contacts):
    """Positional correction of every contact (single pass, early-out)."""
    if (not _PK_STATE["on"] or len(contacts) < _PK_MIN_CONTACTS
            or type(self)._solve_contact_position is not _PK_PY_SOLVE_POS):
        for c in contacts:
            self._solve_contact_position(c)
        return
    try:
        bodies, ia, ib, ha, hb, ima, imb, nrm = _pk_gather(contacts)
        nb = len(bodies)
        pos = np.zeros((nb + 1, 3), np.float32)
        if nb:
            pos[:nb] = [(p.x, p.y, p.z) for p in [b.position for b in bodies]]
        depth = np.array([c.depth for c in contacts], np.float64)
        moved = np.zeros(nb + 1, np.bool_)
        if not (np.isfinite(pos).all() and np.isfinite(depth).all()):
            raise ValueError("not a number")
        _pk_correct(ia, ib, ha, hb, ima, imb, nrm, depth, pos, moved)
        _PK_GATHER[0] = None                # (nothing is kept alive between substeps)
    except Exception as ex:
        _PK_GATHER[0] = None
        if not isinstance(ex, ValueError):
            _pk_fail("position correction", ex)
        for c in contacts:
            self._solve_contact_position(c)
        return
    rows = pos.tolist()
    for k in np.nonzero(moved[:nb])[0].tolist():
        p = bodies[k].position
        p.x, p.y, p.z = rows[k]


if _PK_ON:
    _contact_box_box = _pk_contact_box_box
    _contact_box_plane = _pk_contact_box_plane
    PhysicsWorld._solve_contacts_velocity = _pk_solve_contacts_velocity
PhysicsWorld._solve_contacts_position = _pk_solve_contacts_position
