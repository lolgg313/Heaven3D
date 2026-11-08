# @heaven3d-part physics/numba_boost.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# [CFAMILYPATCH:numba_boost_begin]
# ---------------------------------------------------------------------------
# Cfamily numba boost — append-only, guarded, fallback-safe.
#
# Zero behavioral change by construction:
#   * every kernel below is an exact transcription of the original engine
#     code (same operations, same order, same float32/float64 dtypes);
#   * kernels reuse the engine's own _jit decorator, so when numba is not
#     installed they run as plain Python with bit-identical results;
#   * every target is isolated in its own try/except, and every wrapper
#     falls back to the captured original on ANY exception (including a
#     numba compile error at call time);
#   * no fastmath, no new imports, no dtype changes.
# ---------------------------------------------------------------------------
if not globals().get("_CFAM_NUMBA_BOOST_INSTALLED"):
    _CFAM_NUMBA_BOOST_INSTALLED = True

    # ------------------------------------------------------------------
    # T1: _TriMeshBVH.raycast — full BVH traversal with _ray_aabb_t and
    # _ray_triangle_t inlined.  Stack order replicated exactly (stack=[0],
    # pop from end, push left then right; strict t < best_t update).
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_bvh_raycast = _TriMeshBVH.raycast

        @_jit(cache=True)
        def _cfam_bvh_raycast_kernel(ro, rd, max_t, node_lo, node_hi,
                                     left, right, start, count, packed,
                                     tris, stack):
            best_t = max_t
            best_tri = -1
            sp = 0
            stack[sp] = 0
            sp += 1
            while sp > 0:
                sp -= 1
                ni = stack[sp]
                # --- inlined _ray_aabb_t(ro, rd, node_lo[ni], node_hi[ni], best_t)
                t0 = 0.0
                t1 = best_t
                box_ok = True
                for i in range(3):
                    o = ro[i]
                    d = rd[i]
                    l = float(node_lo[ni, i])
                    h = float(node_hi[ni, i])
                    if abs(d) < 1e-12:
                        if o < l or o > h:
                            box_ok = False
                    else:
                        inv = 1.0 / d
                        a = (l - o) * inv
                        b = (h - o) * inv
                        if a > b:
                            tmp = a
                            a = b
                            b = tmp
                        if a > t0:
                            t0 = a
                        if b < t1:
                            t1 = b
                        if t0 > t1:
                            box_ok = False
                if not box_ok:
                    continue
                if start[ni] >= 0:
                    s = start[ni]
                    for k in range(s, s + count[ni]):
                        ti = packed[k]
                        # --- inlined _ray_triangle_t(ro, rd, tris[ti], best_t)
                        v0x = tris[ti, 0, 0]
                        v0y = tris[ti, 0, 1]
                        v0z = tris[ti, 0, 2]
                        e1x = float(tris[ti, 1, 0] - v0x)
                        e1y = float(tris[ti, 1, 1] - v0y)
                        e1z = float(tris[ti, 1, 2] - v0z)
                        e2x = float(tris[ti, 2, 0] - v0x)
                        e2y = float(tris[ti, 2, 1] - v0y)
                        e2z = float(tris[ti, 2, 2] - v0z)
                        px = rd[1] * e2z - rd[2] * e2y
                        py = rd[2] * e2x - rd[0] * e2z
                        pz = rd[0] * e2y - rd[1] * e2x
                        det = e1x * px + e1y * py + e1z * pz
                        tri_ok = False
                        tval = 0.0
                        if not (det > -1e-12 and det < 1e-12):
                            inv = 1.0 / det
                            tx = ro[0] - float(v0x)
                            ty = ro[1] - float(v0y)
                            tz = ro[2] - float(v0z)
                            u = (tx * px + ty * py + tz * pz) * inv
                            if not (u < -1e-6 or u > 1.000001):
                                qx = ty * e1z - tz * e1y
                                qy = tz * e1x - tx * e1z
                                qz = tx * e1y - ty * e1x
                                v = (rd[0] * qx + rd[1] * qy + rd[2] * qz) * inv
                                if not (v < -1e-6 or u + v > 1.000001):
                                    tval = (e2x * qx + e2y * qy
                                            + e2z * qz) * inv
                                    if not (tval < 1e-6 or tval > best_t):
                                        tri_ok = True
                        if tri_ok and tval < best_t:
                            best_t = tval
                            best_tri = ti
                else:
                    stack[sp] = left[ni]
                    sp += 1
                    stack[sp] = right[ni]
                    sp += 1
            return best_t, best_tri

        def _cfam_bvh_raycast(self, ro, rd, max_t):
            if self.node_count == 0:
                return None
            try:
                ro_a = np.array([float(ro[0]), float(ro[1]), float(ro[2])],
                                dtype=np.float64)
                rd_a = np.array([float(rd[0]), float(rd[1]), float(rd[2])],
                                dtype=np.float64)
                stack = np.empty(2 * self.node_count + 8, dtype=np.int64)
                best_t, best_tri = _cfam_bvh_raycast_kernel(
                    ro_a, rd_a, float(max_t), self.node_lo, self.node_hi,
                    self.left, self.right, self.start, self.count,
                    self.packed, self.tris, stack)
                if best_tri < 0:
                    return None
                return float(best_t), int(best_tri)
            except Exception:
                return _CFAM_ORIG_bvh_raycast(self, ro, rd, max_t)

        _TriMeshBVH.raycast = _cfam_bvh_raycast
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T2: _TriMeshBVH.query_aabb — same traversal; per-leaf AABB overlap
    # as 3 scalar compares (np.any equivalent).  Kernel fills a
    # preallocated int32 buffer and returns the count; the wrapper extends
    # `out` in the same order (out=None -> new list).
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_bvh_query_aabb = _TriMeshBVH.query_aabb

        @_jit(cache=True)
        def _cfam_bvh_query_aabb_kernel(lo, hi, node_lo, node_hi, left,
                                        right, start, count, packed,
                                        tri_lo, tri_hi, buf, stack):
            n_out = 0
            sp = 0
            stack[sp] = 0
            sp += 1
            while sp > 0:
                sp -= 1
                ni = stack[sp]
                if (lo[0] > node_hi[ni, 0] or lo[1] > node_hi[ni, 1]
                        or lo[2] > node_hi[ni, 2]
                        or hi[0] < node_lo[ni, 0] or hi[1] < node_lo[ni, 1]
                        or hi[2] < node_lo[ni, 2]):
                    continue
                if start[ni] >= 0:          # leaf
                    s = start[ni]
                    for k in range(s, s + count[ni]):
                        t = packed[k]
                        if not (lo[0] > tri_hi[t, 0] or lo[1] > tri_hi[t, 1]
                                or lo[2] > tri_hi[t, 2]
                                or hi[0] < tri_lo[t, 0]
                                or hi[1] < tri_lo[t, 1]
                                or hi[2] < tri_lo[t, 2]):
                            buf[n_out] = t
                            n_out += 1
                else:
                    stack[sp] = left[ni]
                    sp += 1
                    stack[sp] = right[ni]
                    sp += 1
            return n_out

        def _cfam_bvh_query_aabb(self, lo, hi, out=None):
            if out is None:
                out = []
            if self.node_count == 0:
                return out
            try:
                lo_a = np.asarray([float(lo[0]), float(lo[1]), float(lo[2])])
                hi_a = np.asarray([float(hi[0]), float(hi[1]), float(hi[2])])
                buf = np.empty(len(self.packed), dtype=np.int32)
                stack = np.empty(2 * self.node_count + 8, dtype=np.int64)
                n = _cfam_bvh_query_aabb_kernel(
                    lo_a, hi_a, self.node_lo, self.node_hi, self.left,
                    self.right, self.start, self.count, self.packed,
                    self.tri_lo, self.tri_hi, buf, stack)
                for i in range(n):
                    out.append(int(buf[i]))
                return out
            except Exception:
                return _CFAM_ORIG_bvh_query_aabb(self, lo, hi, out)

        _TriMeshBVH.query_aabb = _cfam_bvh_query_aabb
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T3: _triplea_ray_mesh_triangles_t — full-function rebind.  Only the
    # <=40000-triangle Python loop is replaced by a kernel (sequential
    # min with the same per-triangle acceptance); the >40000 numpy branch
    # is kept byte-identical.
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_triplea_ray_mesh = _triplea_ray_mesh_triangles_t

        @_jit(cache=True)
        def _cfam_ray_tris_best(tris, ro, rd, best):
            hit = 0
            for ti in range(tris.shape[0]):
                # --- inlined _ray_triangle_t(ro_t, rd_t, tris[ti], best)
                v0x = tris[ti, 0, 0]
                v0y = tris[ti, 0, 1]
                v0z = tris[ti, 0, 2]
                e1x = float(tris[ti, 1, 0] - v0x)
                e1y = float(tris[ti, 1, 1] - v0y)
                e1z = float(tris[ti, 1, 2] - v0z)
                e2x = float(tris[ti, 2, 0] - v0x)
                e2y = float(tris[ti, 2, 1] - v0y)
                e2z = float(tris[ti, 2, 2] - v0z)
                px = rd[1] * e2z - rd[2] * e2y
                py = rd[2] * e2x - rd[0] * e2z
                pz = rd[0] * e2y - rd[1] * e2x
                det = e1x * px + e1y * py + e1z * pz
                if not (det > -1e-12 and det < 1e-12):
                    inv = 1.0 / det
                    tx = ro[0] - float(v0x)
                    ty = ro[1] - float(v0y)
                    tz = ro[2] - float(v0z)
                    u = (tx * px + ty * py + tz * pz) * inv
                    if not (u < -1e-6 or u > 1.000001):
                        qx = ty * e1z - tz * e1y
                        qy = tz * e1x - tx * e1z
                        qz = tx * e1y - ty * e1x
                        v = (rd[0] * qx + rd[1] * qy + rd[2] * qz) * inv
                        if not (v < -1e-6 or u + v > 1.000001):
                            t = (e2x * qx + e2y * qy + e2z * qz) * inv
                            if not (t < 1e-6 or t > best):
                                best = t
                                hit = 1
            return best, hit

        def _cfam_triplea_ray_mesh_triangles_t(ro, rd, mesh, model, max_t):
            """Triangle-accurate ray-vs-mesh distance for scene picking.

            Returns:
                float t — nearest triangle hit distance (<= max_t)
                False   — CPU triangles exist but the ray missed ALL of them
                          (an OBB hit on empty space — the candidate must lose)
                None    — no CPU triangle data; caller falls back to the OBB t
            """
            try:
                positions, indices = _mesh_cpu_triangles(mesh)
            except Exception:
                return None
            if positions is None or indices is None:
                return None
            try:
                m = np.array([[float(model[c][r]) for c in range(4)]
                              for r in range(4)], dtype=np.float64)
                pos = np.asarray(positions, dtype=np.float64)
                world = pos @ m[:3, :3].T + m[:3, 3]
                tris = world[np.asarray(indices, dtype=np.int64).reshape(-1, 3)]
            except Exception:
                return None
            if len(tris) == 0:
                return None
            ro3 = np.array([float(ro[0]), float(ro[1]), float(ro[2])],
                           dtype=np.float64)
            rd3 = np.array([float(rd[0]), float(rd[1]), float(rd[2])],
                           dtype=np.float64)
            best = float(max_t)
            hit = False
            if len(tris) <= 40000:
                try:
                    best, hit_i = _cfam_ray_tris_best(tris, ro3, rd3, best)
                    if hit_i:
                        hit = True
                except Exception:
                    return _CFAM_ORIG_triplea_ray_mesh(ro, rd, mesh, model,
                                                       max_t)
            else:
                t = _triplea_ray_tris_t_vectorized(ro3, rd3, tris, best)
                if t is not None:
                    best = t
                    hit = True
            return best if hit else False

        _triplea_ray_mesh_triangles_t = _cfam_triplea_ray_mesh_triangles_t
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T4: _synth_lowpass — sequential one-pole IIR.  acc float64, x
    # float32 widened via np.float64 (the original float()), out float32.
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_synth_lowpass = _synth_lowpass

        @_jit(cache=True)
        def _cfam_lowpass_kernel(x, a, out):
            acc = 0.0
            for i in range(len(x)):
                acc += a * (np.float64(x[i]) - acc)
                out[i] = acc

        def _cfam_synth_lowpass(x, alpha=0.15):
            """One-pole low-pass filter (cheap smoothing)."""
            if len(x) == 0:
                return x
            try:
                out = np.empty_like(x)
                a = max(0.001, min(1.0, alpha))
                _cfam_lowpass_kernel(x, a, out)
                return out
            except Exception:
                return _CFAM_ORIG_synth_lowpass(x, alpha)

        _synth_lowpass = _cfam_synth_lowpass
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T5: HeightfieldCollider.height_at_local — bilinear over float32
    # heights; _clamp inlined as max(lo, min(hi, v)); int() truncation
    # safe (clamped >= 0); f32 -> f64 widening via np.float64 (float()).
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_height_at_local = HeightfieldCollider.height_at_local

        @_jit(cache=True)
        def _cfam_height_kernel(h, lx, lz, size, cell, resolution):
            n = resolution
            gx = max(0.0, min(n - 1e-4, (lx + size * 0.5) / cell))
            gz = max(0.0, min(n - 1e-4, (lz + size * 0.5) / cell))
            x0 = int(gx)
            z0 = int(gz)
            x1 = min(x0 + 1, n)
            z1 = min(z0 + 1, n)
            fx = gx - x0
            fz = gz - z0
            h00 = np.float64(h[z0, x0])
            h10 = np.float64(h[z0, x1])
            h01 = np.float64(h[z1, x0])
            h11 = np.float64(h[z1, x1])
            return (h00 * (1 - fx) + h10 * fx) * (1 - fz) + \
                   (h01 * (1 - fx) + h11 * fx) * fz

        def _cfam_height_at_local(self, lx, lz):
            """Bilinear height at local XZ (origin at -size/2 corner)."""
            try:
                return float(_cfam_height_kernel(self.heights, lx, lz,
                                                 self.size, self._cell,
                                                 self.resolution))
            except Exception:
                return _CFAM_ORIG_height_at_local(self, lx, lz)

        HeightfieldCollider.height_at_local = _cfam_height_at_local
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T5b: PhysicsWorld._ray_heightfield — the 201-sample march, the
    # 16-step bisection and the gradient normal in ONE kernel (it was
    # ~4 ms of Python per ray: enemy line-of-sight, bullets, step probes).
    # Exact transcription: glm's float32 ray points (ro + rd * t computed
    # in float32), float64 height math, same comparisons and order.  Used
    # only when the collider uses the stock samplers above; anything else
    # (or no numba) runs the original method.
    # ------------------------------------------------------------------
    try:
        if not _HAS_NUMBA:
            raise RuntimeError("numba not installed")
        _CFAM_ORIG_ray_heightfield = PhysicsWorld._ray_heightfield
        _CFAM_BASE_inside_local = HeightfieldCollider.inside_local

        @_jit(cache=True)
        def _cfam_hf_sample(px, pz, h, size, cell, res, wpx, wpy, wpz,
                            wsx, wsy, wsz):
            # _hf_height_world: (ok, height)
            if abs(wsx) < 1e-8 or abs(wsz) < 1e-8:
                return False, 0.0
            lx = (px - wpx) / wsx
            lz = (pz - wpz) / wsz
            half = size * 0.5
            if not (-half <= lx <= half and -half <= lz <= half):
                return False, 0.0
            return True, wpy + _cfam_height_kernel(h, lx, lz, size, cell, res) * wsy

        @_jit(cache=True)
        def _cfam_hf_h_clamped(x, z, h, size, cell, res, wpx, wpz,
                               wsx, wsy, wsz):
            # h_at() inside _hf_normal_world
            half = size * 0.5
            lx = (x - wpx) / max(wsx, 1e-8)
            lz = (z - wpz) / max(wsz, 1e-8)
            lx = max(-half, min(half, lx))
            lz = max(-half, min(half, lz))
            return _cfam_height_kernel(h, lx, lz, size, cell, res) * wsy

        @_jit(cache=True)
        def _cfam_ray_hf_kernel(rox, roy, roz, rdx, rdy, rdz, max_t, h,
                                size, cell, res, wpx, wpy, wpz,
                                wsx, wsy, wsz):
            ox = np.float32(rox); oy = np.float32(roy); oz = np.float32(roz)
            dx = np.float32(rdx); dy = np.float32(rdy); dz = np.float32(rdz)
            steps = 200
            dt_step = max_t / steps
            prev_t = 0.0
            prev_ok = False
            prev_above = 0.0
            for i in range(steps + 1):
                t = i * dt_step
                tf = np.float32(t)
                px = np.float64(ox + dx * tf)
                py = np.float64(oy + dy * tf)
                pz = np.float64(oz + dz * tf)
                ok, hh = _cfam_hf_sample(px, pz, h, size, cell, res,
                                         wpx, wpy, wpz, wsx, wsy, wsz)
                above = py - hh
                if ok and above <= 0.0 and prev_ok and prev_above > 0.0:
                    lo = prev_t
                    hi = t
                    for _k in range(16):
                        mid = (lo + hi) * 0.5
                        mf = np.float32(mid)
                        mx = np.float64(ox + dx * mf)
                        my = np.float64(oy + dy * mf)
                        mz = np.float64(oz + dz * mf)
                        mok, hm = _cfam_hf_sample(mx, mz, h, size, cell, res,
                                                  wpx, wpy, wpz, wsx, wsy, wsz)
                        if mok and my - hm <= 0.0:
                            hi = mid
                        else:
                            lo = mid
                    hit_t = hi
                    hf32 = np.float32(hit_t)
                    hx_ = np.float64(ox + dx * hf32)
                    hz_ = np.float64(oz + dz * hf32)
                    eps = cell * 0.75
                    gx = (_cfam_hf_h_clamped(hx_ + eps, hz_, h, size, cell, res, wpx, wpz, wsx, wsy, wsz)
                          - _cfam_hf_h_clamped(hx_ - eps, hz_, h, size, cell, res, wpx, wpz, wsx, wsy, wsz)) / (2 * eps)
                    gz = (_cfam_hf_h_clamped(hx_, hz_ + eps, h, size, cell, res, wpx, wpz, wsx, wsy, wsz)
                          - _cfam_hf_h_clamped(hx_, hz_ - eps, h, size, cell, res, wpx, wpz, wsx, wsy, wsz)) / (2 * eps)
                    return True, hit_t, gx, gz
                prev_t = t
                prev_ok = ok
                prev_above = above
            return False, 0.0, 0.0, 0.0

        def _cfam_ray_heightfield(self, ro, rd, max_t, s):
            try:
                hf = s["hf"]
                cls = type(hf)
                if (cls.height_at_local is not _cfam_height_at_local
                        or cls.inside_local is not _CFAM_BASE_inside_local
                        or not isinstance(hf.heights, np.ndarray)
                        or hf.heights.ndim != 2):
                    return _CFAM_ORIG_ray_heightfield(self, ro, rd, max_t, s)
                wp, ws = _hf_xform(s["node"])
                ok, t, gx, gz = _cfam_ray_hf_kernel(
                    float(ro.x), float(ro.y), float(ro.z),
                    float(rd.x), float(rd.y), float(rd.z), float(max_t),
                    hf.heights, float(hf.size), float(hf._cell), int(hf.resolution),
                    float(wp.x), float(wp.y), float(wp.z),
                    float(ws.x), float(ws.y), float(ws.z))
                if not ok:
                    return None, None
                n = _safe_normalize(glm.vec3(-gx, 1.0, -gz))
                if glm.dot(n, rd) > 0.0:
                    n = -n
                return float(t), n
            except Exception:
                return _CFAM_ORIG_ray_heightfield(self, ro, rd, max_t, s)

        PhysicsWorld._ray_heightfield = _cfam_ray_heightfield
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T6: Terrain._build_mesh — kernel fills preallocated positions /
    # normals / uvs (float32) and indices (uint32).  float32 fidelity:
    # xL - xR etc. are float32 subtracts; ny = 2.0 * step is float64.
    # The normals sum promotes differently depending on the installed
    # numpy (numpy >= 2 keeps np.float32-scalar ops in float32 — NEP 50;
    # numpy < 2 promotes to float64), so the kernel replicates whichever
    # semantics the RUNTIME numpy gives the original code.  The wrapper
    # keeps the original trailing lines verbatim.
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_terrain_build_mesh = Terrain._build_mesh
        _CFAM_NEP50 = 1 if isinstance(np.float32(1.0) + 1.0, np.float32) else 0

        @_jit(cache=True)
        def _cfam_terrain_mesh_kernel(heights, step, origin, positions,
                                      normals, uvs, indices, nep50):
            h = heights.shape[0]
            w = heights.shape[1]
            for zi in range(h):
                for xi in range(w):
                    idx = zi * w + xi
                    positions[idx, 0] = origin + xi * step
                    positions[idx, 1] = np.float64(heights[zi, xi])
                    positions[idx, 2] = origin + zi * step
            # normals via finite differences
            for zi in range(h):
                for xi in range(w):
                    xL = heights[zi, max(0, xi - 1)]
                    xR = heights[zi, min(w - 1, xi + 1)]
                    zU = heights[max(0, zi - 1), xi]
                    zD = heights[min(h - 1, zi + 1), xi]
                    nx = (xL - xR)
                    nz = (zU - zD)
                    ny = 2.0 * step
                    if nep50:
                        # numpy >= 2 (NEP 50): np.float32 scalar ops with
                        # Python floats stay float32.
                        s = nx * nx + np.float32(ny * ny) + nz * nz
                        length = math.sqrt(np.float64(s)) + 1e-8
                        n0 = nx / np.float32(length)
                        n1 = ny / length
                        n2 = nz / np.float32(length)
                    else:
                        # numpy < 2: scalar ops with Python floats
                        # promote to float64.
                        s = np.float64(nx * nx) + ny * ny + np.float64(nz * nz)
                        length = math.sqrt(s) + 1e-8
                        n0 = np.float64(nx) / length
                        n1 = ny / length
                        n2 = np.float64(nz) / length
                    idx = zi * w + xi
                    normals[idx, 0] = n0
                    normals[idx, 1] = n1
                    normals[idx, 2] = n2
            for zi in range(h):
                for xi in range(w):
                    idx = zi * w + xi
                    uvs[idx, 0] = (xi * step) * 0.1
                    uvs[idx, 1] = (zi * step) * 0.1
            # indices
            ii = 0
            for zi in range(h - 1):
                for xi in range(w - 1):
                    a = zi * w + xi
                    b = a + 1
                    c = a + w
                    d = c + 1
                    indices[ii] = a
                    indices[ii + 1] = c
                    indices[ii + 2] = b
                    indices[ii + 3] = b
                    indices[ii + 4] = c
                    indices[ii + 5] = d
                    ii += 6

        def _cfam_terrain_build_mesh(self):
            if self.heights is None:
                return
            try:
                h, w = self.heights.shape
                step = self.size / max(self.resolution, 1)
                origin = -self.size * 0.5
                positions = np.zeros((h * w, 3), dtype=np.float32)
                normals = np.zeros_like(positions)
                uvs = np.zeros((h * w, 2), dtype=np.float32)
                indices = np.empty(max(0, (h - 1) * (w - 1) * 6),
                                   dtype=np.uint32)
                _cfam_terrain_mesh_kernel(self.heights, step, origin,
                                          positions, normals, uvs, indices,
                                          _CFAM_NEP50)
            except Exception:
                return _CFAM_ORIG_terrain_build_mesh(self)
            self.mesh = Mesh.from_arrays(self.ctx, positions, normals, uvs,
                                         indices, "terrain")
            Log.info(f"Terrain built: {self.resolution}x{self.resolution}")

        Terrain._build_mesh = _cfam_terrain_build_mesh
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T7: Renderer.update_env_texture — full-function rebind.  glm, the
    # palettes, and the loop-invariant sun-spot parameters stay in the
    # Python wrapper (same libm); the per-row sky loop and the 7x7 sun
    # bake move into a kernel over the preallocated float32 image.
    # float32 promotion fidelity: float32 palette element * Python float
    # produces float32 (scalar cast to float32 first), replicated with
    # explicit np.float32() casts.
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_update_env_texture = Renderer.update_env_texture
        _CFAM_SUN_COL = np.array([1.0, 0.92, 0.78], dtype=np.float32)

        @_jit(cache=True)
        def _cfam_env_tex_kernel(img, zenith, horizon, warm, ground_t,
                                 ground_b, sun_col, sun_px, sun_py,
                                 intensity, do_sun):
            H = img.shape[0]
            W = img.shape[1]
            for y in range(H):
                v = (y + 0.5) / H
                phi = (v - 0.5) * math.pi
                dy = math.sin(phi)
                if dy >= 0.0:
                    t = dy ** 0.65
                    f1 = np.float32(1.0 - t)
                    f2 = np.float32(t)
                    b0 = horizon[0] * f1 + zenith[0] * f2
                    b1 = horizon[1] * f1 + zenith[1] * f2
                    b2 = horizon[2] * f1 + zenith[2] * f2
                    band = max(0.0, 1.0 - abs(dy - 0.06) / 0.22) ** 2
                    g1 = np.float32(1.0 - 0.45 * band)
                    g2 = np.float32(0.45 * band)
                    c0 = b0 * g1 + warm[0] * g2
                    c1 = b1 * g1 + warm[1] * g2
                    c2 = b2 * g1 + warm[2] * g2
                else:
                    t = min(1.0, -dy * 2.2) ** 0.8
                    f1 = np.float32(1.0 - t)
                    f2 = np.float32(t)
                    c0 = ground_t[0] * f1 + ground_b[0] * f2
                    c1 = ground_t[1] * f1 + ground_b[1] * f2
                    c2 = ground_t[2] * f1 + ground_b[2] * f2
                for x in range(W):
                    img[y, x, 0] = c0
                    img[y, x, 1] = c1
                    img[y, x, 2] = c2
                    img[y, x, 3] = 1.0
            # Bake sun spot into env map so reflections show the real sun
            if do_sun:
                fi = np.float32(intensity)
                for ddy in range(-3, 4):
                    for ddx in range(-3, 4):
                        py = sun_py + ddy
                        px = (sun_px + ddx) % W
                        if py >= 0 and py < H:
                            dist = math.sqrt(ddx * ddx + ddy * ddy)
                            falloff = max(0.0, 1.0 - dist / 3.0)
                            ff = np.float32(falloff)
                            img[py, px, 0] += (sun_col[0] * fi) * ff
                            img[py, px, 1] += (sun_col[1] * fi) * ff
                            img[py, px, 2] += (sun_col[2] * fi) * ff

        def _cfam_update_env_texture(self, sun_direction):
            """Regenerate the procedural environment map each frame so the
            sky gradient and IBL match the sun's current direction."""
            W, H = 128, 64
            img = np.zeros((H, W, 4), dtype=np.float32)
            sun_dir = glm.normalize(sun_direction)
            sun_elev = -sun_dir.y   # elevation above horizon

            # ---- colour palettes that interpolate with sun elevation ----
            if sun_elev > -0.15:
                t = _clamp((sun_elev + 0.15) / 1.15, 0.0, 1.0)
                zenith   = np.array([0.15 + t*0.13, 0.25 + t*0.21, 0.50 + t*0.35], dtype=np.float32) * (0.35 + t*1.0)
                horizon  = np.array([0.70, 0.65, 0.58], dtype=np.float32) * (0.45 + t*0.75)
                warm     = np.array([1.00, 0.65, 0.35], dtype=np.float32) * (0.25 + t*0.85)
                ground_t = np.array([0.35, 0.32, 0.28], dtype=np.float32) * (0.45 + t*0.45)
                ground_b = np.array([0.15, 0.14, 0.12], dtype=np.float32) * (0.40 + t*0.40)
            else:
                t = _clamp((sun_elev + 1.0) / 0.85, 0.0, 1.0)
                zenith   = np.array([0.03, 0.03, 0.08], dtype=np.float32) * (0.4 + t*0.6)
                horizon  = np.array([0.05, 0.05, 0.10], dtype=np.float32) * (0.35 + t*0.65)
                warm     = np.array([0.10, 0.06, 0.04], dtype=np.float32) * 0.25
                ground_t = np.array([0.03, 0.03, 0.04], dtype=np.float32)
                ground_b = np.array([0.01, 0.01, 0.02], dtype=np.float32)

            if sun_elev > -0.25:
                sun_u = math.atan2(sun_dir.z, sun_dir.x) / (2.0 * math.pi) + 0.5
                sun_v = math.asin(_clamp(sun_dir.y, -1.0, 1.0)) / math.pi + 0.5
                sun_px = int(sun_u * W) % W
                sun_py = int(sun_v * H)
                intensity = max(0.0, sun_elev + 0.25) * 12.0 + 2.0
                do_sun = 1
            else:
                sun_px = 0
                sun_py = 0
                intensity = 0.0
                do_sun = 0

            try:
                _cfam_env_tex_kernel(img, zenith, horizon, warm, ground_t,
                                     ground_b, _CFAM_SUN_COL, sun_px,
                                     sun_py, intensity, do_sun)
            except Exception:
                return _CFAM_ORIG_update_env_texture(self, sun_direction)

            self._env_tex.write(img.astype(np.float16).tobytes())
            self._env_tex.build_mipmaps()

        Renderer.update_env_texture = _cfam_update_env_texture
    except Exception:
        pass

    # ------------------------------------------------------------------
    # T8: _ocean_wave_height_at — 3-octave Gerstner sum.  The wrapper
    # hoists the _OCEAN_STATE dict reads (preserving the wl < 1e-5
    # wind-normalize branch exactly) and converts the constant tuples to
    # float64 arrays at call time; the kernel keeps the same libm calls.
    # ------------------------------------------------------------------
    try:
        _CFAM_ORIG_ocean_wave_height_at = _ocean_wave_height_at

        @_jit(cache=True)
        def _cfam_ocean_kernel(x, z, t, wx, wz, spd, wave_height,
                               wls, frs, ans):
            h = 0.0
            for i in range(wls.shape[0]):
                wav = wls[i]
                fr = frs[i]
                an = ans[i]
                ca = math.cos(an)
                sa = math.sin(an)
                dx = wx * ca - wz * sa
                dz = wx * sa + wz * ca
                k = 2.0 * math.pi / wav
                om = math.sqrt(9.81 * k) * spd
                h += wave_height * fr * math.sin(k * (dx * x + dz * z)
                                                 + om * t)
            return h

        def _cfam_ocean_wave_height_at(x, z, t):
            """CPU mirror of OCEAN_FRAG.wave_height_at (vertical component)."""
            st = _OCEAN_STATE
            wx, wz = st["wind_dir"]
            wl = math.hypot(wx, wz)
            if wl < 1e-5:
                wx, wz = 1.0, 0.0
            else:
                wx, wz = wx / wl, wz / wl
            spd = st["wave_speed"] * (0.35 + 0.65 * st["wind_strength"])
            try:
                wls = np.asarray(_OCEAN_WAVELENGTHS, dtype=np.float64)
                frs = np.asarray(_OCEAN_AMP_FRAC, dtype=np.float64)
                ans = np.asarray(_OCEAN_DIR_OFFSET, dtype=np.float64)
                return _cfam_ocean_kernel(x, z, t, wx, wz, spd,
                                          st["wave_height"], wls, frs, ans)
            except Exception:
                return _CFAM_ORIG_ocean_wave_height_at(x, z, t)

        _ocean_wave_height_at = _cfam_ocean_wave_height_at
    except Exception:
        pass
# [CFAMILYPATCH:numba_boost_end]


# ---------------------------------------------------------------------------
# Numba warm-up.  Kernels compile on their FIRST call (~1 s the very first
# time a game runs, ~0.3 s to load them from the on-disk cache afterwards),
# which used to freeze gameplay the first time an enemy looked for the
# player, a bullet hit terrain or a mesh collider was touched.  A background
# thread started with the engine compiles / loads them while the project
# loads.  It uses throw-away stand-in objects only (no scene nodes, no
# engine state), and any failure is ignored: kernels then compile on first
# use exactly as before.  numba serialises compilation, so a gameplay call
# arriving mid-compile just waits for it.
# ---------------------------------------------------------------------------
_H3D_WARMUP = {"thread": None, "done": False, "seconds": None}


class _H3DWarmNode:
    """Minimal node stand-in (identity transform) for warm-up shapes."""
    def world_matrix(self):
        return glm.mat4(1.0)


def _h3d_numba_warmup():
    if not _HAS_NUMBA:
        return
    t0 = time.perf_counter()
    try:
        for dt in (np.float32, np.float64):
            hf = HeightfieldCollider.__new__(HeightfieldCollider)
            hf.size, hf.resolution, hf._cell = 8.0, 8, 1.0
            hf.heights = (np.arange(81, dtype=np.float64).reshape(9, 9) * 0.01).astype(dt)
            s = {"kind": "heightfield", "node": _H3DWarmNode(), "hf": hf}
            type(hf).height_at_local(hf, 0.5, 0.5)
            PhysicsWorld._ray_heightfield(None, glm.vec3(0.1, 5.0, 0.2),
                                          glm.vec3(0.0, -1.0, 0.0), 20.0, s)
    except Exception:
        pass
    try:
        tris = np.array([[[0, 0, 0], [1, 0, 0], [0, 0, 1]],
                         [[1, 0, 0], [1, 0, 1], [0, 0, 1]]], dtype=np.float32)
        bvh = _TriMeshBVH(tris)
        bvh.raycast(glm.vec3(0.3, 1.0, 0.3), glm.vec3(0.0, -1.0, 0.0), 5.0)
        bvh.query_aabb(glm.vec3(-1.0), glm.vec3(2.0))
    except Exception:
        pass
    try:
        _ocean_wave_height_at(1.0, 2.0, 0.5)
    except Exception:
        pass
    _H3D_WARMUP["seconds"] = time.perf_counter() - t0


def _h3d_start_numba_warmup():
    """Start the warm-up once per process (background, daemon thread)."""
    if not _HAS_NUMBA or _H3D_WARMUP["thread"] is not None:
        return
    if os.environ.get("HEAVEN3D_NO_WARMUP"):
        return
    import threading

    def _run():
        try:
            _h3d_numba_warmup()
        except Exception:
            pass
        _H3D_WARMUP["done"] = True

    th = threading.Thread(target=_run, name="heaven3d-numba-warmup", daemon=True)
    _H3D_WARMUP["thread"] = th
    th.start()

