# @heaven3d-part rendering/instancing.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 16 —  ENTRY POINT
# #############################################################################

# #############################################################################
# SECTION 15b —  GPU INSTANCING SUPPORT  (instanced geometry program +
#                ShaderLib hook; appended by the Triple-A render patch)
# #############################################################################

# [TRIPLEA:geometry_inst_shaderlib_hook]
GEOMETRY_INST_VERT = """#version 330 core
// # [TRIPLEA:geometry_inst_shaderlib_hook]
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec3 in_normal;
layout (location = 2) in vec4 in_tangent;
layout (location = 3) in vec2 in_uv;
// Per-instance model matrix (16 floats = 4 column vectors, streamed through
// a "/i"-divisor instance VBO; occupies attribute locations 4..7).
layout (location = 4) in mat4 i_model;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec3 v_world_pos;
out vec3 v_normal;
out vec4 v_tangent;
out vec2 v_uv;
void main(){
    vec4 wp = i_model * vec4(in_position, 1.0);
    v_world_pos = wp.xyz;
    // Correct normal transform for non-uniform scale (inverse-transpose),
    // identical to the per-mesh GEOMETRY_VERT path.
    v_normal = transpose(inverse(mat3(i_model))) * in_normal;
    // Tangents transform fine with the plain model matrix (they lie in the
    // surface plane); handedness is preserved in w.
    v_tangent = vec4(mat3(i_model) * in_tangent.xyz, in_tangent.w);
    v_uv = in_uv;
    gl_Position = u_proj * u_view * wp;
}
"""

try:
    _shaderlib_get_unpatched = ShaderLib.get.__func__

    def _shaderlib_get_with_instancing(cls, ctx, name):
        """ShaderLib.get wrapper that also knows 'geometry_inst'.

        The instanced program pairs GEOMETRY_INST_VERT with the original
        module-level GEOMETRY_FRAG and shares ShaderLib's per-name cache.
        Every other shader name delegates to the original classmethod."""
        if name == "geometry_inst":
            if name in ShaderLib._cache:
                return ShaderLib._cache[name]
            try:
                prog = ctx.program(vertex_shader=GEOMETRY_INST_VERT,
                                   fragment_shader=GEOMETRY_FRAG)
                ShaderLib._cache[name] = prog
                Log.debug("Shader compiled: geometry_inst")
                return prog
            except Exception as ex:
                Log.error(f"Shader 'geometry_inst' compile failed: {ex}")
                return None
        return _shaderlib_get_unpatched(cls, ctx, name)

    ShaderLib.get = classmethod(_shaderlib_get_with_instancing)
except Exception:
    # Instancing is a pure optimization; never block engine startup on it.
    pass

try:
    _renderer_release_unpatched = Renderer.release

    def _renderer_release_with_instancing(self):
        """Release the GL objects the render patch allocates lazily
        (instanced VAOs/VBOs, cluster grid texture), then run the
        original Renderer.release."""
        try:
            _cache = getattr(self, "_inst_vao_cache", None)
            if _cache:
                for _vao, _ivbo, _cap in _cache.values():
                    try:
                        _vao.release()
                    except Exception:
                        pass
                    try:
                        _ivbo.release()
                    except Exception:
                        pass
                _cache.clear()
            _tex = getattr(self, "_cl_grid_tex", None)
            if _tex is not None:
                try:
                    _tex.release()
                except Exception:
                    pass
                self._cl_grid_tex = None
                self._cl_grid_key = None
        except Exception:
            pass
        return _renderer_release_unpatched(self)

    Renderer.release = _renderer_release_with_instancing
except Exception:
    pass

