# @heaven3d-part h3d27/factory_end.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
    portable = _h3d27_types.SimpleNamespace(
        is_media_ref=is_media_ref, safe_name=safe_name, Resolver=Resolver, Collector=Collector,
        make_portable=make_portable, resolve_project_data=resolve_project_data,
        SaveData=SaveData, user_data_root=user_data_root, export_game=export_game,
        find_player=find_player, current_platform=current_platform, zip_folder=zip_folder,
        atomic_write_json=atomic_write_json, IMPORT_DIR=IMPORT_DIR, set_exe_icon=set_exe_icon,
        icon_png=icon_png, ICON_EXTS=ICON_EXTS, make_icns=make_icns, info_plist=info_plist,
        PLATFORMS=PLATFORMS, PLATFORM_TITLES=PLATFORM_TITLES)
    return _h3d27_types.SimpleNamespace(
        load_blend=load_blend, load_gltf=load_gltf,
        BlendFormatError=BlendFormatError, GltfError=GltfError,
        MeshoptError=MeshoptError, decode_meshopt=decode_meshopt), portable

H3DImporters, H3DPortable = _h3d27_build_importers()

# =================================================================
#  Engine integration (runs at import time, before any shader compiles)
# =================================================================
HEAVEN_VERSION = "3.1.4"

class _H3D27Log:
    @staticmethod
    def warn(msg):
        Log.warn(msg)

    @staticmethod
    def info(msg):
        Log.info(msg)

_H3D27_ONCE = set()

def _h3d27_log_once(key, msg):
    if key not in _H3D27_ONCE:
        _H3D27_ONCE.add(key)
        try:
            Log.warn(msg)
        except Exception:
            pass
