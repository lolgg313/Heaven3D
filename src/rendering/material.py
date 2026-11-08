# @heaven3d-part rendering/material.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 4  —  MATERIAL  (PBR descriptor bound to GLSL uniforms)
# #############################################################################

# ---- Texture-descriptor (de)serialisation helpers ------------------------
# Texture sources on a Material can be one of:
#     None                         → no texture
#     "/abs/path.png"  (str)       → file on disk (legacy form)
#     ("path", "/abs/path.png")    → file on disk (descriptor form)
#     ("bytes", b"<image bytes>")  → embedded image data from a GLB
# JSON can't serialise raw bytes, and we don't want to embed image data in
# the project file anyway (it already lives inside the source GLB).  These
# helpers convert descriptors to/from a JSON-safe representation:
#     path descriptor  → the path string
#     bytes descriptor → the sentinel "__embedded__"
# On load, "__embedded__" comes back as None; the real descriptor is
# re-resolved from the source GLB when the node is loaded (see
# ProjectSerializer._deserialize_node).
_EMBEDDED_SENTINEL = "__embedded__"


def _tex_descriptor_to_json(desc):
    if desc is None:
        return None
    if isinstance(desc, str):
        return desc
    if isinstance(desc, tuple) and len(desc) == 2:
        kind, payload = desc
        if kind == "path":
            return payload
        if kind == "bytes":
            return _EMBEDDED_SENTINEL
    # Fallback for anything unexpected: drop it (don't crash the save).
    return None


def _tex_descriptor_from_json(value):
    if value is None or value == _EMBEDDED_SENTINEL:
        # Both None and the embedded marker restore as None.  Embedded
        # descriptors are re-resolved from the GLB during node load.
        return None
    # A stored path string — round-trip back into descriptor form so the
    # renderer's get_texture() (which accepts both forms) treats it uniformly.
    return value


@dataclass
class Material:
    name: str = "default"
    base_color: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    metallic: float = 0.0
    roughness: float = 0.5
    emissive: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    emissive_intensity: float = 1.0
    base_color_tex: Optional[str] = None
    metallic_roughness_tex: Optional[str] = None
    normal_tex: Optional[str] = None
    emissive_tex: Optional[str] = None
    # glTF occlusionTexture (ambient occlusion in the R channel) — baked AO
    # multiplies the image-based ambient/specular, the EEVEE "contact shadow"
    # look for imported assets.
    occlusion_tex: Optional[str] = None
    double_sided: bool = False
    alpha_mode: str = "OPAQUE"
    # glTF alphaCutoff — MASK discards fragments below this alpha; editable
    # in the inspector so cut-out edges can be tightened/softened per asset.
    alpha_cutoff: float = 0.5

    def to_dict(self) -> dict:
        return {"name": self.name,
                "base_color": list(self.base_color),
                "metallic": self.metallic,
                "roughness": self.roughness,
                "emissive": list(self.emissive),
                "emissive_intensity": self.emissive_intensity,
                "alpha_cutoff": self.alpha_cutoff,
                # Texture descriptors are normalised to a JSON-safe form:
                #   ("path", "/abs.png")  -> "/abs.png"
                #   ("bytes", b"...")     -> "__embedded__"  (re-resolved from
                #                                         the source GLB on load)
                #   plain str / None      -> as-is
                "base_color_tex": _tex_descriptor_to_json(self.base_color_tex),
                "metallic_roughness_tex": _tex_descriptor_to_json(self.metallic_roughness_tex),
                "normal_tex": _tex_descriptor_to_json(self.normal_tex),
                "emissive_tex": _tex_descriptor_to_json(self.emissive_tex),
                "occlusion_tex": _tex_descriptor_to_json(self.occlusion_tex),
                "double_sided": self.double_sided,
                "alpha_mode": self.alpha_mode}

    @classmethod
    def from_dict(cls, d: dict) -> "Material":
        return cls(name=d.get("name", "default"),
                   base_color=tuple(d.get("base_color", [1,1,1,1])),
                   metallic=d.get("metallic", 0.0),
                   roughness=d.get("roughness", 0.5),
                   emissive=tuple(d.get("emissive", [0,0,0])),
                   emissive_intensity=d.get("emissive_intensity", 1.0),
                   # Restore: paths come back as path strings; the
                   # "__embedded__" marker comes back as None placeholder
                   # because the real descriptor is re-resolved from the
                   # source GLB during node load (see _deserialize_node).
                   base_color_tex=_tex_descriptor_from_json(d.get("base_color_tex")),
                   metallic_roughness_tex=_tex_descriptor_from_json(d.get("metallic_roughness_tex")),
                   normal_tex=_tex_descriptor_from_json(d.get("normal_tex")),
                   emissive_tex=_tex_descriptor_from_json(d.get("emissive_tex")),
                   occlusion_tex=_tex_descriptor_from_json(d.get("occlusion_tex")),
                   double_sided=d.get("double_sided", False),
                   alpha_mode=d.get("alpha_mode", "OPAQUE"),
                   alpha_cutoff=d.get("alpha_cutoff", 0.5))

    @classmethod
    def from_glb_info(cls, info: dict) -> "Material":
        return cls(name=info.get("name", "glb_mat"),
                   base_color=tuple(info.get("base_color", [1,1,1,1])),
                   metallic=info.get("metallic", 1.0),
                   roughness=info.get("roughness", 1.0),
                   emissive=tuple(info.get("emissive", [0,0,0])),
                   emissive_intensity=info.get("emissive_intensity", 1.0),
                   base_color_tex=info.get("base_color_tex"),
                   metallic_roughness_tex=info.get("metallic_roughness_tex"),
                   normal_tex=info.get("normal_tex"),
                   emissive_tex=info.get("emissive_tex"),
                   occlusion_tex=info.get("occlusion_tex"),
                   double_sided=info.get("double_sided", False),
                   alpha_mode=info.get("alpha_mode", "OPAQUE"),
                   alpha_cutoff=info.get("alpha_cutoff", 0.5))

