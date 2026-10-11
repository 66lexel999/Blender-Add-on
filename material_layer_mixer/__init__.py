# Material Layer Mixer - Substance-style material layers for Blender:
# stack whole materials / texture sets / fills, mix every channel (color, roughness, metal, normal, height,
# emission) with painted + procedural mask stacks, turn the height into real 3D geometry and bake it onto a
# low-poly copy, then bake to plain PBR textures for Roblox / game engines.
# Works as a Blender extension (4.2+, blender_manifest.toml) and as a legacy add-on (bl_info).
bl_info = {
    "name": "Material Layer Mixer",
    "author": "FC3_Extract",
    "version": (1, 4, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar (N) > Mixer",
    "description": "Layer and paint whole materials (all PBR channels), procedural masks, bake for Roblox",
    "category": "Material",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from . import props, nodes, ops, bake, ui, brushes, paint_guard, relief


@persistent
def _pack_masks(*_):
    """painted masks live inside the .blend: pack the ones painted since the last save"""
    for img in bpy.data.images:
        if img.get('mlm_mask') and img.is_dirty:
            try:
                img.pack()
            except RuntimeError:
                pass


def register():
    props.register()
    for c in ops.CLASSES + bake.CLASSES + brushes.CLASSES:
        bpy.utils.register_class(c)
    brushes.register()
    ui.register()
    relief.register()
    paint_guard.register()
    bpy.types.WindowManager.mlm_painting = StringProperty()
    if _pack_masks not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_pack_masks)


def unregister():
    if _pack_masks in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_pack_masks)
    paint_guard.unregister()
    del bpy.types.WindowManager.mlm_painting
    relief.unregister()
    ui.unregister()
    brushes.unregister()
    for c in reversed(ops.CLASSES + bake.CLASSES + brushes.CLASSES):
        bpy.utils.unregister_class(c)
    props.unregister()
