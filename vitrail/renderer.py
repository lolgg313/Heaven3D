# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""GPU renderer.

Everything the UI draws -- boxes, borders, gradients, shadows, glyphs, icons,
colour wheels -- is one *instance* of a single unit quad.  A frame is therefore
exactly ONE instanced draw call, whatever the number of widgets:

* every instance carries its own clip rectangle, so scroll areas and popups
  never break the batch (no scissor changes, no state switches);
* shapes are signed-distance functions evaluated in the fragment shader, so
  rounded corners and borders are anti-aliased at any size without meshes;
* text is drawn from one R8 glyph atlas that lives in the same draw call.

The CPU side is a flat ``array('f')`` that grows with ``extend`` (C speed) and
is uploaded with a single buffer write, only when the UI actually changed.
"""
from __future__ import annotations

from array import array

import moderngl

# Instance layout: 32 float32 = 128 bytes
#   rect(4) uv(4) radii(4) params(4) col0(4) col1(4) border(4) clip(4)
FLOATS_PER_INSTANCE = 32
BYTES_PER_INSTANCE = FLOATS_PER_INSTANCE * 4

# shape modes (params.y)
(M_BOX, M_GLYPH, M_SHADOW, M_HUE, M_SV, M_TRI, M_CHECK, M_CHECKER, M_LINE, M_GRID, M_RIPPLE, M_SPHERE,
 M_IMAGE) = range(13)

NO_CLIP = (-1e9, -1e9, 1e9, 1e9)
ZERO4 = (0.0, 0.0, 0.0, 0.0)
CLEAR = (0.0, 0.0, 0.0, 0.0)

VERTEX_SHADER = """
#version 330
uniform vec2 u_screen;
in vec4 i_rect;   in vec4 i_uv;   in vec4 i_radii; in vec4 i_params;
in vec4 i_col0;   in vec4 i_col1; in vec4 i_border; in vec4 i_clip;
out vec2 v_local; out vec2 v_pos; out vec2 v_uv;
flat out vec2 v_size;
flat out vec4 v_radii, v_params, v_col0, v_col1, v_border, v_clip, v_uvr;
void main() {
    vec2 corner = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
    vec2 pos = i_rect.xy + corner * i_rect.zw;
    v_local = corner * i_rect.zw;
    v_size = i_rect.zw;
    v_pos = pos;
    v_uv = mix(i_uv.xy, i_uv.zw, corner);
    v_uvr = i_uv;
    v_radii = i_radii; v_params = i_params;
    v_col0 = i_col0; v_col1 = i_col1; v_border = i_border; v_clip = i_clip;
    vec2 ndc = pos / u_screen * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330
uniform sampler2D u_atlas;
uniform sampler2D u_images;
in vec2 v_local; in vec2 v_pos; in vec2 v_uv;
flat in vec2 v_size;
flat in vec4 v_radii, v_params, v_col0, v_col1, v_border, v_clip, v_uvr;
out vec4 f_color;

// radii = (top-left, top-right, bottom-right, bottom-left), y axis points down
float sd_round_box(vec2 p, vec2 b, vec4 r) {
    vec2 side = (p.x > 0.0) ? vec2(r.y, r.z) : vec2(r.x, r.w);
    float rr = (p.y > 0.0) ? side.y : side.x;
    vec2 q = abs(p) - b + rr;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - rr;
}
float sd_segment(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return length(pa - ba * h);
}
float sd_triangle(vec2 p, vec2 p0, vec2 p1, vec2 p2) {
    vec2 e0 = p1 - p0, e1 = p2 - p1, e2 = p0 - p2;
    vec2 v0 = p - p0, v1 = p - p1, v2 = p - p2;
    vec2 pq0 = v0 - e0 * clamp(dot(v0, e0) / dot(e0, e0), 0.0, 1.0);
    vec2 pq1 = v1 - e1 * clamp(dot(v1, e1) / dot(e1, e1), 0.0, 1.0);
    vec2 pq2 = v2 - e2 * clamp(dot(v2, e2) / dot(e2, e2), 0.0, 1.0);
    float s = sign(e0.x * e2.y - e0.y * e2.x);
    vec2 d = min(min(vec2(dot(pq0, pq0), s * (v0.x * e0.y - v0.y * e0.x)),
                     vec2(dot(pq1, pq1), s * (v1.x * e1.y - v1.y * e1.x))),
                     vec2(dot(pq2, pq2), s * (v2.x * e2.y - v2.y * e2.x)));
    return -sqrt(d.x) * sign(d.y);
}
vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}
vec4 premul(vec4 c) { return vec4(c.rgb * c.a, c.a); }

void main() {
    if (v_pos.x < v_clip.x || v_pos.y < v_clip.y || v_pos.x >= v_clip.z || v_pos.y >= v_clip.w)
        discard;
    int mode = int(v_params.y + 0.5);
    vec2 half_size = v_size * 0.5;
    vec2 p = v_local - half_size;

    if (mode == 1) {                                   // glyph
        float a = texture(u_atlas, v_uv).r;
        f_color = premul(v_col0) * a;
        return;
    }
    if (mode == 2) {                                   // soft shadow
        float soft = max(v_params.z, 0.5);
        float d = sd_round_box(p, half_size - soft, v_radii);
        float a = 1.0 - smoothstep(-soft, soft, d);
        f_color = premul(v_col0) * a * a;
        return;
    }
    if (mode == 5) {                                   // triangle icon
        vec2 q = p;
        int dir = int(v_params.w + 0.5);
        if (dir == 1) q = vec2(p.y, -p.x);
        else if (dir == 2) q = vec2(-p.x, p.y);
        else if (dir == 3) q = vec2(-p.y, p.x);
        float s = min(half_size.x, half_size.y);
        float d = sd_triangle(q, vec2(-0.45, -0.8) * s, vec2(0.75, 0.0) * s, vec2(-0.45, 0.8) * s);
        f_color = premul(v_col0) * clamp(0.5 - d, 0.0, 1.0);
        return;
    }
    if (mode == 6) {                                   // check mark
        float s = min(half_size.x, half_size.y);
        vec2 a = vec2(-0.62, 0.02) * s, b = vec2(-0.18, 0.48) * s, c = vec2(0.62, -0.5) * s;
        float d = min(sd_segment(p, a, b), sd_segment(p, b, c)) - v_params.z * 0.5;
        f_color = premul(v_col0) * clamp(0.5 - d, 0.0, 1.0);
        return;
    }
    if (mode == 9) {                                   // infinite grid (uv.xy = origin, params.w = cell)
        float cell = max(v_params.w, 2.0);
        vec2 g = (v_pos - v_uvr.xy) / cell;
        vec2 f = abs(fract(g - 0.5) - 0.5) * cell;
        float minor = clamp(1.0 - min(f.x, f.y), 0.0, 1.0);
        vec2 f5 = abs(fract(g / 5.0 - 0.5) - 0.5) * cell * 5.0;
        float major = clamp(1.25 - min(f5.x, f5.y), 0.0, 1.0);
        f_color = premul(v_col1) * major + premul(v_col0) * minor * (1.0 - major);
        return;
    }
    if (mode == 8) {                                   // line segment (uv holds endpoints)
        float d = sd_segment(v_pos, v_uvr.xy, v_uvr.zw) - v_params.z * 0.5;
        f_color = premul(v_col0) * clamp(0.5 - d, 0.0, 1.0);
        return;
    }

    if (mode == 11) {                                  // material ball (radii = rough, metal, emission, -)
        float r = min(half_size.x, half_size.y);
        vec2 q = p / r;
        float len_q = length(q);
        float cover = clamp((1.0 - len_q) * r + 0.5, 0.0, 1.0);
        if (cover <= 0.0) discard;
        vec3 n = normalize(vec3(q.x, -q.y, sqrt(max(0.0, 1.0 - min(1.0, dot(q, q))))));
        vec3 base = pow(v_col0.rgb, vec3(2.2));
        float rough = clamp(v_radii.x, 0.045, 1.0);
        float metal = clamp(v_radii.y, 0.0, 1.0);
        vec3 V = vec3(0.0, 0.0, 1.0);
        vec3 F0 = mix(vec3(0.04), base, metal);
        float NdV = max(n.z, 1e-3);
        float a = rough * rough;
        float a2 = a * a;
        float k = (rough + 1.0) * (rough + 1.0) / 8.0;
        vec3 col = vec3(0.0);
        vec3 lights[2] = vec3[2](normalize(vec3(-0.55, 0.62, 0.58)), normalize(vec3(0.8, -0.1, 0.35)));
        vec3 lcol[2] = vec3[2](vec3(3.0, 2.9, 2.75), vec3(0.55, 0.65, 0.9));
        for (int i = 0; i < 2; i++) {
            vec3 L = lights[i];
            vec3 H = normalize(L + V);
            float NdL = max(dot(n, L), 0.0);
            float NdH = max(dot(n, H), 0.0);
            float dd = NdH * NdH * (a2 - 1.0) + 1.0;
            float D = a2 / (3.14159 * dd * dd);
            vec3 F = F0 + (1.0 - F0) * pow(1.0 - max(dot(H, V), 0.0), 5.0);
            float G = (NdL / (NdL * (1.0 - k) + k)) * (NdV / (NdV * (1.0 - k) + k));
            vec3 spec = D * F * G / max(4.0 * NdL * NdV, 1e-3);
            vec3 kd = (1.0 - F) * (1.0 - metal);
            col += (kd * base / 3.14159 + spec) * NdL * lcol[i];
        }
        vec3 R = reflect(-V, n);
        float sky_t = clamp(R.y * 0.5 + 0.5, 0.0, 1.0);
        vec3 env = mix(vec3(0.10, 0.09, 0.085), vec3(0.62, 0.70, 0.85), smoothstep(0.25, 0.75, sky_t));
        env = mix(env, vec3(0.32), rough * 0.75);                                  // blurrier when rough
        vec3 Fe = F0 + (max(vec3(1.0 - rough), F0) - F0) * pow(1.0 - NdV, 5.0);
        vec3 amb = mix(vec3(0.07, 0.065, 0.06), vec3(0.28, 0.31, 0.38), n.y * 0.5 + 0.5);
        col += amb * base * (1.0 - metal) + env * Fe;
        col += pow(v_col1.rgb, vec3(2.2)) * v_radii.z;                              // emission (sRGB in)
        col = col / (col + vec3(0.6)) * 1.6;                                        // filmic-ish
        col = pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
        f_color = vec4(col, 1.0) * cover * v_col0.a;
        return;
    }
    if (mode == 12) {                                  // RGBA image from the image atlas
        float cover = clamp(0.5 - sd_round_box(p, half_size, v_radii), 0.0, 1.0);
        vec4 c = texture(u_images, v_uv) * v_col0;
        f_color = vec4(c.rgb * c.a, c.a) * cover;
        return;
    }
    if (mode == 10) {                                  // circle (uv.xy centre, params.w radius) clipped to a rounded box
        float box = clamp(0.5 - sd_round_box(p, half_size, v_radii), 0.0, 1.0);
        float circ = clamp(0.5 - (length(v_pos - v_uvr.xy) - v_params.w), 0.0, 1.0);
        f_color = premul(v_col0) * box * circ;
        return;
    }
    // boxes: 0 = gradient box, 3 = hue strip, 4 = sat/value square, 7 = checker
    float d = sd_round_box(p, half_size, v_radii);
    float cover = clamp(0.5 - d, 0.0, 1.0);
    if (cover <= 0.0) discard;
    vec2 t = v_local / max(v_size, vec2(1.0));
    vec4 fill;
    if (mode == 3) fill = vec4(hsv2rgb(vec3(t.x, 1.0, 1.0)), 1.0);
    else if (mode == 4) fill = vec4(hsv2rgb(vec3(v_params.w, t.x, 1.0 - t.y)), 1.0);
    else if (mode == 7) {
        vec2 cell = floor(v_local / max(v_params.w, 1.0));
        fill = mix(v_col0, v_col1, mod(cell.x + cell.y, 2.0));
    }
    else fill = mix(v_col0, v_col1, t.y);
    vec4 c = premul(fill);
    float bw = v_params.x;
    if (bw > 0.0) {
        float inner = clamp(0.5 - (d + bw), 0.0, 1.0);
        c = mix(premul(v_border), c, inner);
    }
    f_color = c * cover;
}
"""


def _clip_and(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


class DrawList:
    """CPU-side list of instances for one frame.

    All coordinates are pixels, origin top-left.  Colours are RGBA float
    tuples in 0..1.  ``radius`` may be a number or a 4-tuple
    (top-left, top-right, bottom-right, bottom-left).
    """

    __slots__ = ("data", "_clip", "_clip_stack", "count", "version")

    def __init__(self):
        self.data = array("f")
        self._clip = NO_CLIP
        self._clip_stack = []
        self.count = 0
        self.version = 0

    def clear(self):
        del self.data[:]
        self._clip = NO_CLIP
        self._clip_stack.clear()
        self.count = 0
        self.version += 1

    # -- clipping --------------------------------------------------------------
    def push_clip(self, x, y, w, h):
        self._clip_stack.append(self._clip)
        self._clip = _clip_and(self._clip, (x, y, x + w, y + h))

    def pop_clip(self):
        self._clip = self._clip_stack.pop()

    @property
    def clip(self):
        return self._clip

    def _visible(self, x, y, w, h):
        c = self._clip
        return not (x >= c[2] or y >= c[3] or x + w <= c[0] or y + h <= c[1] or c[0] >= c[2] or c[1] >= c[3])

    # -- primitives ------------------------------------------------------------
    def _emit(self, x, y, w, h, uv, radii, params, c0, c1, cb):
        if w <= 0 or h <= 0 or not self._visible(x, y, w, h):
            return
        self.data.extend((x, y, w, h) + uv + radii + params + c0 + c1 + cb + self._clip)
        self.count += 1

    @staticmethod
    def _radii(r):
        if isinstance(r, (int, float)):
            return (r, r, r, r)
        return r

    def box(self, x, y, w, h, top, bottom=None, border=None, radius=0.0, border_width=1.0):
        """Rounded box with optional vertical gradient and border."""
        self._emit(x, y, w, h, ZERO4, self._radii(radius),
                   (border_width if border else 0.0, M_BOX, 0.0, 0.0),
                   top, bottom or top, border or CLEAR)

    def shadow(self, x, y, w, h, color, radius=4.0, softness=8.0):
        s = softness
        r = self._radii(radius)
        self._emit(x - s, y - s, w + 2 * s, h + 2 * s, ZERO4, tuple(v + s for v in r),
                   (0.0, M_SHADOW, s, 0.0), color, color, CLEAR)

    def triangle(self, x, y, w, h, color, direction=0):
        """direction: 0 right, 1 down, 2 left, 3 up"""
        self._emit(x, y, w, h, ZERO4, ZERO4, (0.0, M_TRI, 0.0, float(direction)), color, color, CLEAR)

    def check(self, x, y, w, h, color, thickness=2.0):
        self._emit(x, y, w, h, ZERO4, ZERO4, (0.0, M_CHECK, thickness, 0.0), color, color, CLEAR)

    def hue_strip(self, x, y, w, h, radius=0.0, border=None):
        self._emit(x, y, w, h, ZERO4, self._radii(radius), (1.0 if border else 0.0, M_HUE, 0.0, 0.0),
                   CLEAR, CLEAR, border or CLEAR)

    def sv_square(self, x, y, w, h, hue, radius=0.0, border=None):
        self._emit(x, y, w, h, ZERO4, self._radii(radius), (1.0 if border else 0.0, M_SV, 0.0, hue),
                   CLEAR, CLEAR, border or CLEAR)

    def checker(self, x, y, w, h, a, b, cell=6.0, radius=0.0):
        self._emit(x, y, w, h, ZERO4, self._radii(radius), (0.0, M_CHECKER, 0.0, cell), a, b, CLEAR)

    def line(self, x0, y0, x1, y1, color, thickness=1.0):
        pad = thickness + 1.0
        x, y = min(x0, x1) - pad, min(y0, y1) - pad
        w, h = abs(x1 - x0) + 2 * pad, abs(y1 - y0) + 2 * pad
        self._emit(x, y, w, h, (x0, y0, x1, y1), ZERO4, (0.0, M_LINE, thickness, 0.0), color, color, CLEAR)

    def text_run(self, run, ox, oy, color):
        """Emit a cached Font.run() at integer origin (ox, baseline oy)."""
        c = self._clip
        if not run or c[0] >= c[2] or c[1] >= c[3]:
            return
        tail = (0.0, 0.0, 0.0, 0.0, 0.0, float(M_GLYPH), 0.0, 0.0) + color + color + CLEAR + c
        ext = self.data.extend
        x0, y0, x1, y1 = c
        n = 0
        for (dx, dy, w, h, u0, v0, u1, v1) in run:
            x = ox + dx
            y = oy + dy
            if x >= x1 or y >= y1 or x + w <= x0 or y + h <= y0:
                continue
            ext((x, y, w, h, u0, v0, u1, v1) + tail)
            n += 1
        self.count += n

    def grid(self, x, y, w, h, cell, origin, minor, major):
        """Grid lines every ``cell`` px (every 5th is ``major``), passing through ``origin``."""
        self._emit(x, y, w, h, (origin[0], origin[1], 0.0, 0.0), ZERO4, (0.0, M_GRID, 0.0, cell), minor, major, CLEAR)

    def ripple(self, x, y, w, h, radius, cx, cy, r, color):
        """Circle of radius r at (cx, cy) clipped to the rounded box (x, y, w, h)."""
        self._emit(x, y, w, h, (cx, cy, 0.0, 0.0), self._radii(radius), (0.0, M_RIPPLE, 0.0, r), color, color, CLEAR)

    def arc(self, cx, cy, r, a0, a1, color, thickness=2.0, segments=None):
        """Arc (radians, 0 = +x, clockwise on screen) as anti-aliased segments."""
        import math
        n = segments or max(6, int(abs(a1 - a0) * r / 6))
        pts = [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
        self.polyline(pts, color, thickness)

    def material_ball(self, x, y, size, base, roughness=0.5, metallic=0.0, emission=(0, 0, 0, 1), strength=0.0):
        """A lit sphere previewing a PBR material (one quad, shaded per pixel)."""
        self._emit(x, y, size, size, ZERO4, (roughness, metallic, strength, 0.0), (0.0, M_SPHERE, 0.0, 0.0),
                   tuple(base[:3]) + (base[3] if len(base) > 3 else 1.0,), tuple(emission[:3]) + (1.0,), CLEAR)

    def image(self, x, y, w, h, uv, tint=(1.0, 1.0, 1.0, 1.0), radius=0.0):
        """Draw a region (uv = u0, v0, u1, v1) of the RGBA image atlas."""
        self._emit(x, y, w, h, uv, self._radii(radius), (0.0, M_IMAGE, 0.0, 0.0), tint, tint, CLEAR)

    def polyline(self, pts, color, thickness=1.0):
        for a, b in zip(pts, pts[1:]):
            self.line(a[0], a[1], b[0], b[1], color, thickness)

    def bezier(self, p0, p1, p2, p3, color, thickness=2.0, segments=None):
        """Cubic bezier as anti-aliased segments (segment count adapts to length)."""
        if segments is None:
            est = abs(p3[0] - p0[0]) + abs(p3[1] - p0[1]) + abs(p1[0] - p0[0]) + abs(p2[0] - p3[0])
            segments = max(8, min(48, int(est / 14)))
        pts = []
        for i in range(segments + 1):
            t = i / segments
            u = 1 - t
            a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
            pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
        self.polyline(pts, color, thickness)
        return pts

    def glyph_run(self, quads, color):
        """quads: iterable of (x, y, w, h, u0, v0, u1, v1) -- emitted in one go."""
        c = self._clip
        if c[0] >= c[2] or c[1] >= c[3]:
            return
        ext = self.data.extend
        params = (0.0, float(M_GLYPH), 0.0, 0.0)
        n = 0
        for (x, y, w, h, u0, v0, u1, v1) in quads:
            if x >= c[2] or y >= c[3] or x + w <= c[0] or y + h <= c[1]:
                continue
            ext((x, y, w, h, u0, v0, u1, v1))
            ext(ZERO4)
            ext(params)
            ext(color)
            ext(color)
            ext(CLEAR)
            ext(c)
            n += 1
        self.count += n


class Renderer:
    """Uploads a DrawList and renders it with one instanced draw call."""

    def __init__(self, ctx: moderngl.Context, atlas_texture: moderngl.Texture, image_texture=None):
        self.ctx = ctx
        self.atlas = atlas_texture
        self.prog = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
        if "u_atlas" in self.prog:
            self.prog["u_atlas"].value = 0
        if "u_images" in self.prog:
            self.prog["u_images"].value = 1
        self.images = image_texture
        self._capacity = 0
        self._buffer = None
        self._vao = None
        self._uploaded = (-1, None)
        self._reserve(1024)

    def _reserve(self, instances):
        if instances <= self._capacity:
            return
        cap = max(instances, self._capacity * 2, 1024)
        if self._vao is not None:
            self._vao.release()
            self._buffer.release()
        self._buffer = self.ctx.buffer(reserve=cap * BYTES_PER_INSTANCE, dynamic=True)
        self._vao = self.ctx.vertex_array(
            self.prog,
            [(self._buffer, "4f 4f 4f 4f 4f 4f 4f 4f/i",
              "i_rect", "i_uv", "i_radii", "i_params", "i_col0", "i_col1", "i_border", "i_clip")],
        )
        self._capacity = cap
        self._uploaded = (-1, None)

    def render(self, dl: DrawList, width: int, height: int):
        if dl.count == 0:
            return
        key = (dl.version, id(dl))
        if self._uploaded != key:
            self._reserve(dl.count)
            self._buffer.write(dl.data)
            self._uploaded = key
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
        self.prog["u_screen"].value = (float(width), float(height))
        self.atlas.use(0)
        if self.images is not None:
            self.images.use(1)
        self._vao.render(moderngl.TRIANGLE_STRIP, vertices=4, instances=dl.count)

    def release(self):
        if self._vao is not None:
            self._vao.release()
            self._buffer.release()
        self.prog.release()
