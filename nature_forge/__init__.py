# Nature Forge - Geometry Nodes generators for game environments: rocks, cliffs, mountains, trees (and "Rockify"
# for your own meshes). Every shape stays live and adjustable; texture sets from your library are connected
# automatically (colour, normal GL/DX, roughness/gloss, metal, AO, height, opacity, ORM) and the height map becomes
# real 3D detail that the colour / normal maps sit exactly on. Auto-paint layers (moss, snow, dirt, edge wear ...)
# and vertex painting, then bake game LODs with the Material Layer Mixer.
# Works as a Blender extension (4.2+, blender_manifest.toml) and as a legacy add-on (bl_info).
bl_info = {
    "name": "Nature Forge",
    "author": "FC3_Extract",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar (N) > Forge",
    "description": "Geometry Nodes rocks, cliffs, mountains and trees with automatic texture set setup",
    "category": "Add Mesh",
}

import bpy
from bpy.app.handlers import persistent
from . import props, ops, ui, library, presets


def _menu_add(self, context):
    self.layout.separator()
    for kind, label, desc, icon in presets.KINDS:
        op = self.layout.operator('nf.add', text=f"Nature Forge {label}", icon=icon)
        op.kind = kind


@persistent
def _on_load(*_):
    library.load_cache()


def _startup():
    library.load_cache()
    p = props.prefs()
    if p is not None and p.auto_scan and len(p.folders):
        try:
            library.set_sets(library.scan([f.path for f in p.folders if f.path]))
        except Exception as e:                            # never break startup over a missing drive
            print("Nature Forge: library scan failed:", e)
    return None


def register():
    props.register()
    for c in ops.CLASSES + ui.CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_mesh_add.append(_menu_add)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_startup, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.types.VIEW3D_MT_mesh_add.remove(_menu_add)
    for c in reversed(ops.CLASSES + ui.CLASSES):
        bpy.utils.unregister_class(c)
    library.unregister()
    props.unregister()
