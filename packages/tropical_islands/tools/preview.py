"""Preview a built vegetation asset: LOD0 / LOD1 / LOD2 side by side (needs GL).

    python tools/preview.py assets/veg/island_tree.npz out.png [variant]
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image

VS = """
#version 330
in vec3 p; in vec3 n; in vec2 uv;
uniform mat4 mvp; uniform vec3 off;
out vec3 wn; out vec2 tuv;
void main(){ gl_Position = mvp * vec4(p + off, 1.0); wn = n; tuv = uv; }"""
FS = """
#version 330
in vec3 wn; in vec2 tuv;
uniform sampler2D tex; uniform int has_tex; uniform vec3 col;
out vec4 o;
void main(){
    vec4 a = has_tex == 1 ? texture(tex, tuv) : vec4(col, 1.0);
    if (a.a < 0.5) discard;
    vec3 L = normalize(vec3(0.4, 0.8, 0.45));
    vec3 N = normalize(wn); if (!gl_FrontFacing) N = -N;
    float d = max(dot(N, L), 0.0) * 0.75 + 0.35;
    o = vec4(pow(a.rgb, vec3(1.0)) * d, 1.0);
}"""


def load(path):
    z = np.load(path)
    meta = json.loads(bytes(z["meta.json"]).decode())
    parts = {}
    for k in z.files:
        if k == "meta.json":
            continue
        tag, field = k.rsplit(".", 1)
        parts.setdefault(tag, {})[field] = z[k]
    return meta, parts


def tex_for(meta, tag, d):
    part = tag.split(".")[2]
    if part == "cards":
        return os.path.join(d, meta["cards"]["albedo"])
    if part == "palm":
        return os.path.join(d, meta["albedo"])
    if part == "imp":
        return os.path.join(d, meta["imp"]["albedo"])
    if part == "rock":
        return os.path.join(d, meta["mat"]["base"])
    if part == "wood":
        key = tag.split(".", 3)[3]
        return os.path.join(d, meta["wood"][key]["base"])
    return None


def main():
    import glm
    import moderngl
    path, out = sys.argv[1], sys.argv[2]
    variant = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    meta, parts = load(path)
    d = os.path.dirname(path)
    W, H = 1500, 600
    ctx = moderngl.create_standalone_context()
    fbo = ctx.simple_framebuffer((W, H))
    fbo.use()
    ctx.enable(moderngl.DEPTH_TEST)
    fbo.clear(0.62, 0.74, 0.86, 1.0)
    prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
    texs = {}
    sel = {t: m for t, m in parts.items() if int(t.split(".")[0]) == variant}
    allp = np.vstack([m["P"] for m in sel.values()])
    lo, hi = allp.min(0), allp.max(0)
    size = max(hi - lo)
    eye = glm.vec3(0, (hi[1] + lo[1]) / 2, size * 2.4)
    tgt = glm.vec3(0, (hi[1] + lo[1]) / 2, 0)
    proj = glm.perspective(glm.radians(35.0), W / H, 0.05, size * 20)
    mvp = proj * glm.lookAt(eye, tgt, glm.vec3(0, 1, 0))
    prog["mvp"].write(mvp)
    tris = {0: 0, 1: 0, 2: 0}
    for tag, m in sel.items():
        li = int(tag.split(".")[1])
        tris[li] += len(m["I"])
        tp = tex_for(meta, tag, d)
        if tp and tp not in texs:
            im = Image.open(tp).convert("RGBA").transpose(Image.FLIP_TOP_BOTTOM)
            t = ctx.texture(im.size, 4, im.tobytes())
            t.build_mipmaps()
            t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            texs[tp] = t
        uv = m["UV"].copy()
        uv[:, 1] = 1.0 - uv[:, 1]                     # top-down -> GL
        data = np.hstack([m["P"], m["N"], uv]).astype("f4")
        vbo = ctx.buffer(data.tobytes())
        ibo = ctx.buffer(m["I"].astype("u4").tobytes())
        vao = ctx.vertex_array(prog, [(vbo, "3f 3f 2f", "p", "n", "uv")], ibo)
        prog["off"].value = ((li - 1) * size * 1.25, 0.0, 0.0)
        if tp:
            texs[tp].use(0)
            prog["tex"].value = 0
            prog["has_tex"].value = 1
        else:
            prog["has_tex"].value = 0
            prog["col"].value = (0.5, 0.5, 0.5)
        vao.render()
    img = Image.frombytes("RGB", (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
    img.save(out)
    print(os.path.basename(path), "tris per LOD", tris, "size", np.round(hi - lo, 2))


if __name__ == "__main__":
    main()
