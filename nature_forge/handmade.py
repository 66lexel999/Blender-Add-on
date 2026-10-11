# Nature Forge - Step by Step: the classic hand-made rock / cliff workflow, one button per step.
#
#   1 Blockout   Remesh (voxel) + Displace with a Clouds texture  ->  one lumpy, solid rock mass
#   2 Shape      edit / duplicate / add chunks (the remesh welds them), then Convert to Mesh
#   3 Sculpt     Multires + rock brushes, apply (optional)
#   4 Unwrap     Cube projection UVs at real-world texture size
#   5 Detail     Subdivision + Displace with the texture set's height map (UV)  ->  real rocky geometry
#   6 Material   every other map of the same set (colour, AO, roughness, metal, normal, bump)
#   7 Finish     apply, bake game LODs (Material Layer Mixer), or start the next one
#
# Everything stays normal Blender modifiers / textures / materials, so every value can also be tweaked in the
# Properties editor exactly like in a tutorial. Settings live on the object (Object.nf_hand). Steps that need an
# earlier one finished do it for you (e.g. Unwrap converts a live blockout first) and say so.
import json
import math
import os
import random
import bmesh
import numpy as np
import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, PropertyGroup
from mathutils import Matrix, Vector, Euler
from . import library, shading

STEPS = [
    ('BLOCKOUT', "Blockout", "Remesh + cloud displacement: a solid, lumpy rock mass", 'MOD_REMESH'),
    ('SHAPE', "Shape", "Move, duplicate or add chunks, then convert to mesh", 'EDITMODE_HLT'),
    ('SCULPT', "Sculpt", "Multires sculpting of big forms (optional)", 'SCULPTMODE_HLT'),
    ('UV', "Unwrap", "Cube projection UVs at real-world texture size", 'UV'),
    ('DETAIL', "Rock Detail", "Subdivision + the texture's displacement map", 'MOD_DISPLACE'),
    ('MATERIAL', "Material", "All the other maps of the texture set", 'MATERIAL'),
    ('FINISH', "Finish", "Apply, bake game LODs or start the next one", 'CHECKMARK'),
]
STEP_KEYS = [s[0] for s in STEPS]
M_REMESH, M_CLOUDS, M_MULTIRES, M_SUBDIV, M_DETAIL = (
    "NF Remesh", "NF Clouds", "NF Multires", "NF Detail Subdivision", "NF Rock Detail")
EMPTY_PREFIX = "NF Clouds Offset"
BACKUPS = "NF Editable Copies"
BASES = {'ROCK': ("Rock", (1.6, 1.3, 1.1)), 'CLIFF': ("Cliff", (2.0, 1.6, 4.4)),
         'SLAB': ("Slab", (3.0, 2.2, 0.8)), 'PILLAR': ("Pillar", (1.2, 1.1, 5.0))}
NOISE_BASES = [('BLENDER_ORIGINAL', "Soft Lumps", "Blender's original noise: soft rounded masses"),
               ('ORIGINAL_PERLIN', "Perlin", "Classic Perlin noise"),
               ('IMPROVED_PERLIN', "Improved Perlin", "Smoother Perlin noise"),
               ('VORONOI_F1', "Cells", "Voronoi: rounded cells, boulder heaps"),
               ('VORONOI_F2_F1', "Cracked Plates", "Voronoi F2-F1: flat plates with cracks between them"),
               ('VORONOI_CRACKLE', "Crackle", "Sharp ridges and cracks, very rocky"),
               ('CELL_NOISE', "Blocks", "Square blocky steps")]
# sculpt brushes: (label, Blender 4.3+ essentials asset, Blender 4.2 tool id, icon)
BRUSHES = {
    'CLAY': ("Clay Strips", "Clay Strips", "builtin_brush.Clay Strips", 'BRUSH_CLAY_STRIPS'),
    'SCRAPE': ("Scrape", "Scrape/Fill", "builtin_brush.Scrape", 'BRUSH_SCRAPE'),
    'FLATTEN': ("Flatten", "Flatten/Contrast", "builtin_brush.Flatten", 'BRUSH_FLATTEN'),
    'CRACK': ("Cracks", "Draw Sharp", "builtin_brush.Draw Sharp", 'BRUSH_SCULPT_DRAW'),
    'CREASE': ("Crease", "Crease Sharp", "builtin_brush.Crease", 'BRUSH_CREASE'),
    'PINCH': ("Pinch", "Pinch/Magnify", "builtin_brush.Pinch", 'BRUSH_PINCH'),
    'GRAB': ("Grab", "Grab", "builtin_brush.Grab", 'BRUSH_GRAB'),
    'SMOOTH': ("Smooth", "Smooth", "builtin_brush.Smooth", 'BRUSH_SMOOTH'),
}
FACE_BUDGET = 1_500_000
GROUND_KINDS = {'ROCK', 'CLIFF', 'SLAB', 'PILLAR'}


class StepError(Exception):
    """a step can't run: the message is shown to the user, nothing was changed"""


# ---------------------------------------------------------------------------------------------- helpers
def ctx_ob(context):
    ob = context.object or context.view_layer.objects.active
    return ob if ob is not None and ob.type == 'MESH' else None


def in_flow(ob):
    return ob is not None and ob.type == 'MESH' and ob.nf_hand.active


def avg_dim(ob):
    d = ob.data.vertices
    if not len(d):
        return 1.0
    co = np.empty(len(d) * 3, np.float32)
    d.foreach_get('co', co)
    co = co.reshape(-1, 3)
    return float(max((co.max(0) - co.min(0)).mean(), 1e-3))


def surface_area(ob):
    me = ob.data
    if not len(me.polygons):
        return 1.0
    a = np.empty(len(me.polygons), np.float32)
    me.polygons.foreach_get('area', a)
    return float(max(a.sum(), 1e-6))


def multires_of(ob):
    return next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)


def faces_now(context, ob):
    mr = multires_of(ob)
    if ob.mode == 'SCULPT' and mr is not None and mr.show_viewport:
        # sculpt mode reports the base mesh (5.x) or nothing (4.2): count the level being sculpted
        n = len(ob.data.polygons) * 4 ** mr.sculpt_levels
        for m in ob.modifiers:
            if m.type == 'SUBSURF' and m.show_viewport:
                n *= 4 ** m.levels
        return n
    try:
        n = len(ob.evaluated_get(context.evaluated_depsgraph_get()).data.polygons)
    except (RuntimeError, ReferenceError):
        n = 0
    return n or len(ob.data.polygons)


def mark_done(ob, key, done=True):
    H = ob.nf_hand
    bit = 1 << STEP_KEYS.index(key)
    H.done = (H.done | bit) if done else (H.done & ~bit)


def is_done(ob, key):
    return bool(ob.nf_hand.done & (1 << STEP_KEYS.index(key)))


def live_blockout(ob):
    return ob.modifiers.get(M_REMESH) is not None or ob.modifiers.get(M_CLOUDS) is not None


def object_mode(context):
    if context.mode != 'OBJECT' and context.view_layer.objects.active is not None:
        bpy.ops.object.mode_set(mode='OBJECT')


def own_data(ob):
    """modifiers can't be applied to a mesh shared with another object (Alt+D)"""
    if ob.data.users > 1:
        ob.data = ob.data.copy()


def check_appliable(ob):
    if ob.data.shape_keys is not None:
        raise StepError("This mesh has shape keys: remove them first (Object Data > Shape Keys)")


def apply_modifier(context, ob, name):
    if ob.modifiers.get(name) is None:
        return
    with context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                               selected_editable_objects=[ob]):
        try:
            bpy.ops.object.modifier_apply(modifier=name)
        except RuntimeError as e:
            raise StepError(f"Could not apply '{name}': {str(e).splitlines()[-1].replace('Error: ', '')}")
    if ob.modifiers.get(name) is not None:              # Blender only warned ('disabled, skipping apply')
        raise StepError(f"Could not apply '{name}' (it is disabled or failed)")


def apply_through(context, ob, last_names):
    """apply the stack from the top down to (and including) the lowest of last_names, in order - exactly what
    the viewport showed (a Mirror above the remesh is applied first, later steps stay live). Modifiers that are
    switched off or broken (a Boolean without object) did not change the preview: they are left in the stack.
    -> names left alone"""
    idx = [ob.modifiers.find(n) for n in last_names if ob.modifiers.find(n) >= 0]
    if not idx:
        return []
    skipped = []
    for name in [m.name for m in list(ob.modifiers)[:max(idx) + 1]]:
        m = ob.modifiers.get(name)
        if name not in last_names and not m.show_viewport:
            skipped.append(name)
            continue
        try:
            apply_modifier(context, ob, name)
        except StepError:
            if name in last_names:
                raise
            skipped.append(name)
    return skipped


def move_to_index(context, ob, name, index):
    if ob.modifiers.get(name) is None:
        return
    index = max(0, min(index, len(ob.modifiers) - 1))
    with context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.modifier_move_to_index(modifier=name, index=index)


def texture_set(ob):
    H = ob.nf_hand
    if not H.set_json:
        return None
    try:
        ts = json.loads(H.set_json)
    except ValueError:
        return None
    return library.get(ts.get('id')) or ts


def store_set(ob, ts):
    keep = {k: ts[k] for k in ('id', 'name', 'maps', 'normal_dx', 'category', 'dir', 'info', 'preview') if k in ts}
    ob.nf_hand.set_json = json.dumps(keep)
    library.remember(ts)


def need_set(ob, maps=None):
    ts = texture_set(ob)
    if ts is None:
        raise StepError("Choose a texture set first (Library pick or From Disk, box at the top)")
    missing = [p for c, p in ts['maps'].items() if (maps is None or c in maps) and not os.path.isfile(p)]
    if missing:
        raise StepError(f"Texture files missing in '{os.path.dirname(missing[0])}': pick the set again")
    return ts


def backup_of(ob):
    return next((o for o in bpy.data.objects if o.get('nf_blockout_of') == ob.name), None)


def backups_collection(context):
    coll = bpy.data.collections.get(BACKUPS)
    if coll is None:
        coll = bpy.data.collections.new(BACKUPS)
    if coll not in context.scene.collection.children_recursive:
        context.scene.collection.children.link(coll)
    return coll


# ---------------------------------------------------------------------------------------------- clouds data
def noise_empty(ob, create=False):
    e = ob.nf_hand.noise_object
    if e is not None and e.name not in bpy.data.objects:
        e = None
    if e is None and create:
        e = bpy.data.objects.new(f"{EMPTY_PREFIX} ({ob.name})", None)
        e.empty_display_type = 'SPHERE'
        e.empty_display_size = 0.25
        for c in ob.users_collection:
            c.objects.link(e)
        e.parent = ob
        e.hide_viewport = True
        e.hide_render = True
        e.hide_select = True
        ob.nf_hand.noise_object = e
    return e


def clouds_texture(ob, create=False):
    md = ob.modifiers.get(M_CLOUDS)
    tex = md.texture if md is not None and md.texture is not None else None
    if tex is None and create:
        tex = bpy.data.textures.new(f"NF Clouds ({ob.name})", 'CLOUDS')
        if md is not None:
            md.texture = tex
    return tex


def _texture_users(tex, ob):
    """other objects whose modifiers use this texture"""
    return [o for o in bpy.data.objects if o != ob and o.type == 'MESH' and
            any(getattr(m, 'texture', None) == tex for m in o.modifiers)]


def ensure_own(ob):
    """a Shift+D copy shares the hidden offset empty and the textures with the original: give it its own, so
    tweaking one rock never changes the other"""
    dp = ob.modifiers.get(M_CLOUDS)
    e = ob.nf_hand.noise_object
    if e is not None and e.name in bpy.data.objects and e.parent != ob:
        e2 = e.copy()
        for c in ob.users_collection:
            c.objects.link(e2)
        e2.parent = ob
        e2.matrix_parent_inverse = Matrix.Identity(4)
        e2.name = f"{EMPTY_PREFIX} ({ob.name})"
        ob.nf_hand.noise_object = e2
        if dp is not None:
            dp.texture_coords_object = e2
    for name in (M_CLOUDS, M_DETAIL):
        md = ob.modifiers.get(name)
        if md is not None and md.texture is not None and _texture_users(md.texture, ob):
            md.texture = md.texture.copy()


def sync_clouds(ob):
    """seed / size / stretch / detail / pattern -> the clouds texture and its hidden offset empty (recreated if
    they were deleted)"""
    H = ob.nf_hand
    dp = ob.modifiers.get(M_CLOUDS)
    if dp is None:
        return
    ensure_own(ob)
    tex = clouds_texture(ob, create=True)
    tex.noise_scale = 1.0
    tex.noise_depth = H.cloud_detail
    tex.noise_basis = H.cloud_basis
    tex.noise_type = 'HARD_NOISE' if H.cloud_hard else 'SOFT_NOISE'
    tex.intensity = 1.0
    tex.contrast = H.cloud_contrast
    e = noise_empty(ob, create=True)
    if dp.texture_coords != 'OBJECT' or dp.texture_coords_object != e:
        dp.texture_coords = 'OBJECT'
        dp.texture_coords_object = e
    r = random.Random(H.seed * 7919 + 13)
    e.location = (r.uniform(-60, 60), r.uniform(-60, 60), r.uniform(-60, 60))
    yaw = r.uniform(0, math.tau)
    tilt = (r.uniform(0, math.tau), r.uniform(0, math.tau))
    if not H.cloud_twist:
        e.rotation_euler = (0.0, 0.0, 0.0)
    elif abs(H.cloud_stretch - 1.0) < 1e-3:
        e.rotation_euler = (tilt[0], tilt[1], yaw)       # no stretch: any orientation
    else:
        e.rotation_euler = (0.0, 0.0, yaw)              # stretched: only spin around Z so it stays vertical
    s = max(H.cloud_size, 1e-3)
    e.scale = (s, s, s * max(H.cloud_stretch, 0.05))
    ob.update_tag()


def upd_clouds(self, context):
    sync_clouds(self.id_data)


def cleanup_orphans():
    """offset empties of deleted / converted rocks"""
    for e in [o for o in bpy.data.objects if o.type == 'EMPTY' and o.name.startswith(EMPTY_PREFIX)]:
        p = e.parent
        used = p is not None and p.type == 'MESH' and any(
            m.type == 'DISPLACE' and m.texture_coords_object == e for m in p.modifiers)
        if not used:
            bpy.data.objects.remove(e)


def upd_uv_size(self, context):
    """live texture size: scale the cube projected UVs (the rock detail + material follow)"""
    ob = self.id_data
    old = self.get('uv_applied', 0.0)
    new = max(self.uv_size, 1e-4)
    if not old or abs(old - new) < 1e-9:
        return
    me = ob.data
    if ob.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(me)
        layer = bm.loops.layers.uv.get('UVMap') or bm.loops.layers.uv.active
        if layer is None:
            return
        k = old / new
        for f in bm.faces:
            for lp in f.loops:
                lp[layer].uv = lp[layer].uv * k
        bmesh.update_edit_mesh(me)
    else:
        uvl = me.uv_layers.get('UVMap') or me.uv_layers.active
        if uvl is None:
            return
        uv = np.empty(len(me.loops) * 2, np.float32)
        uvl.data.foreach_get('uv', uv)
        uvl.data.foreach_set('uv', uv * (old / new))
        me.update()
    dp = ob.modifiers.get(M_DETAIL)
    if dp is not None and self.strength_follows:
        dp.strength = dp.strength * new / old            # same bump height relative to the texture
    self['uv_applied'] = new
    ob.update_tag()


class NF_HandSettings(PropertyGroup):
    active: BoolProperty(default=False)
    kind: StringProperty(default='ROCK')
    step: IntProperty(default=0, min=0, max=len(STEPS) - 1)
    done: IntProperty(default=0)
    noise_object: PointerProperty(type=bpy.types.Object)
    seed: IntProperty(name="Seed", default=0, min=0, update=upd_clouds, description="A different cloud pattern")
    cloud_size: FloatProperty(name="Cloud Size", default=0.8, min=0.01, soft_max=20.0, unit='LENGTH',
                              update=upd_clouds, description="Size of the lumps (bigger = fewer, larger masses)")
    cloud_stretch: FloatProperty(name="Vertical Stretch", default=1.0, min=0.05, soft_max=6.0, update=upd_clouds,
                                 description="> 1 stretches the lumps up and down (cliff columns), < 1 flattens "
                                             "them into layers")
    cloud_detail: IntProperty(name="Detail", default=2, min=0, max=6, update=upd_clouds,
                              description="Noise octaves: more = rougher, smaller bumps on the lumps")
    cloud_contrast: FloatProperty(name="Contrast", default=2.0, min=0.01, max=5.0, update=upd_clouds,
                                  description="Higher = deeper lumps and dents (lower = gentle waves)")
    cloud_basis: EnumProperty(name="Pattern", items=NOISE_BASES, default='BLENDER_ORIGINAL', update=upd_clouds)
    cloud_hard: BoolProperty(name="Hard", default=False, update=upd_clouds,
                             description="Hard noise: sharper, more broken lumps")
    cloud_twist: BoolProperty(name="Random Rotation", default=True, update=upd_clouds,
                              description="Spin the pattern with the seed (no grid-aligned look). With Vertical "
                                          "Stretch it only turns around the up axis, so columns stay upright")
    chunk_size: FloatProperty(name="Chunk Size", default=0.45, min=0.05, max=1.5, subtype='FACTOR',
                              description="Size of added chunks (relative to the object)")
    chunk_shape: EnumProperty(name="Chunk", items=[('BOX', "Box", "Angular block", 'MESH_CUBE', 0),
                                                   ('ROUND', "Round", "Rounded lump", 'MESH_ICOSPHERE', 1)],
                              default='BOX')
    chunk_seed: IntProperty(default=0)
    sculpt_levels: IntProperty(name="Sculpt Resolution", default=2, min=1, max=6,
                               description="Multires subdivisions to sculpt on (each level = 4x faces)")
    keep_level: IntProperty(name="Keep Level", default=1, min=0, max=6,
                            description="Detail kept in the mesh when applying the sculpt. 0 = only the base "
                                        "mesh, reshaped to the big sculpted forms (lightest). The rock-detail step "
                                        "subdivides again, so 0-1 is usually plenty")
    uv_size: FloatProperty(name="Texture Size", default=2.0, min=0.01, soft_max=50.0, unit='LENGTH',
                           update=upd_uv_size, description="Real-world size of one texture repeat (cube projection)")
    strength_follows: BoolProperty(name="Strength Follows Size", default=True,
                                   description="Scale the rock-detail strength with the Texture Size, so smaller "
                                               "textures make finer (not spikier) rock")
    set_json: StringProperty()


# ---------------------------------------------------------------------------------------------- state
def infer_state(ob):
    """done bits / current step from what is really on the object (restarting the guide on it)"""
    H = ob.nf_hand
    H.done = 0
    if live_blockout(ob):
        mark_done(ob, 'BLOCKOUT')
    elif ob.get('nf_converted'):
        mark_done(ob, 'BLOCKOUT')
        mark_done(ob, 'SHAPE')
    if ob.get('nf_sculpted'):
        mark_done(ob, 'SCULPT')
    if ob.data.uv_layers.get('UVMap') is not None and H.get('uv_applied'):
        mark_done(ob, 'UV')
        H['uv_size'] = H['uv_applied']
    if ob.modifiers.get(M_DETAIL) is not None:
        mark_done(ob, 'DETAIL')
    if any(s.material is not None and (s.material.get('nf_hand_mat') or
                                       (hasattr(s.material, 'mlm') and s.material.mlm.is_stack))
           for s in ob.material_slots):
        mark_done(ob, 'MATERIAL')
    if live_blockout(ob):
        H.step = STEP_KEYS.index('BLOCKOUT')
    else:
        H.step = next((i for i, k in enumerate(STEP_KEYS) if not is_done(ob, k) and k != 'SCULPT'),
                      len(STEPS) - 1)


def _from_nf(ob):
    from .ops import nf_mod
    return nf_mod(ob)


def start(context, kind, report=None):
    object_mode(context)
    cleanup_orphans()
    if kind == 'SELECTED':
        ob = ctx_ob(context)
        if ob is None:
            raise StepError("Select a mesh first")
        if _from_nf(ob) is not None:
            raise StepError("This is a live Nature Forge generator: use its sliders, or 'Make Real Mesh' first")
        if not len(ob.data.polygons):
            raise StepError("This mesh has no faces")
        check_appliable(ob)
        own_data(ob)
        if any(abs(s - 1.0) > 1e-6 for s in ob.scale):
            with context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                                       selected_editable_objects=[ob]):
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            if report:
                report({'INFO'}, "Applied the object scale, so voxels and texture size are in real metres")
        for k in ('nf_final', 'nf_kind', 'nf_preset'):
            if k in ob:
                del ob[k]
        H = ob.nf_hand
        H.active = True
        if live_blockout(ob) or H.get('uv_applied') or ob.get('nf_converted') or H.set_json:
            H.kind = H.kind or 'SELECTED'
            infer_state(ob)                               # continue where it was, keep its settings
            sync_clouds(ob)
            return ob
    else:
        label, dims = BASES[kind]
        me = bpy.data.meshes.new(label)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co = Vector((v.co.x * dims[0], v.co.y * dims[1], v.co.z * dims[2] + dims[2] * 0.5))
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(label, me)
        (context.collection or context.scene.collection).objects.link(ob)
        ob.location = context.scene.cursor.location
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
    H = ob.nf_hand
    H.active = True
    H.kind = kind
    H.step = 0
    H.done = 0
    a = avg_dim(ob)
    H['cloud_size'] = round(a * 0.25, 3)
    H['cloud_contrast'] = 2.0
    H['cloud_stretch'] = 1.6 if kind in ('CLIFF', 'PILLAR') else 1.0
    H['uv_size'] = round(max(a * 0.9, 0.25), 2)
    H['seed'] = random.randint(0, 9999)
    return ob


def _later_index(ob):
    """where step-1 modifiers go: before the rock-detail ones (they come later in the workflow)"""
    idx = [ob.modifiers.find(n) for n in (M_SUBDIV, M_DETAIL) if ob.modifiers.find(n) >= 0]
    return min(idx) if idx else len(ob.modifiers)


def blockout(context, ob, report=None):
    object_mode(context)
    if multires_of(ob) is not None:
        raise StepError("A Multires sculpt is still live: apply it (step 3) before a new blockout")
    check_appliable(ob)
    ensure_own(ob)
    cleanup_orphans()
    rm = ob.modifiers.get(M_REMESH)
    if rm is None:
        rm = ob.modifiers.new(M_REMESH, 'REMESH')
        rm.mode = 'VOXEL'
        rm.voxel_size = round(max(math.sqrt(surface_area(ob) / 12000.0), 0.002), 4)
        rm.use_smooth_shade = False
        rm.adaptivity = 0.0
    dp = ob.modifiers.get(M_CLOUDS)
    if dp is None:
        dp = ob.modifiers.new(M_CLOUDS, 'DISPLACE')
        dp.direction = 'NORMAL'
        dp.strength = round(avg_dim(ob) * 0.18, 3)
        dp.mid_level = 0.5
    clouds_texture(ob, create=True)
    for m in (rm, dp):
        m.show_in_editmode = True
    later = _later_index(ob)
    if later < len(ob.modifiers) - 2 or ob.modifiers.find(M_CLOUDS) != ob.modifiers.find(M_REMESH) + 1:
        move_to_index(context, ob, M_REMESH, later)
        move_to_index(context, ob, M_CLOUDS, ob.modifiers.find(M_REMESH) + 1)
        if report and ob.modifiers.get(M_DETAIL) is not None:
            report({'WARNING'}, "The remesh rebuilds the UVs: unwrap again (step 4) after converting")
    sync_clouds(ob)
    mark_done(ob, 'BLOCKOUT')


def _surface_point(bm, r, ground, selected_only):
    faces = [f for f in bm.faces if f.select] if selected_only else []
    faces = faces or [f for f in bm.faces if not (ground and f.normal.z < -0.5)] or list(bm.faces)
    if not faces:
        return None, None
    areas = np.array([max(f.calc_area(), 1e-9) for f in faces], np.float64)
    f = faces[int(np.searchsorted(np.cumsum(areas) / areas.sum(), r.random()))] if len(faces) > 1 else faces[0]
    vs = [v.co for v in f.verts]
    i = r.randrange(1, len(vs) - 1) if len(vs) > 3 else 1      # a random triangle of the face fan
    a, b = r.random(), r.random()
    if a + b > 1.0:
        a, b = 1.0 - a, 1.0 - b
    p = vs[0] + (vs[i] - vs[0]) * a + (vs[i + 1] - vs[0]) * b
    return p, f.normal.copy()


def add_chunk(context, ob):
    """a box / lump sunk into the surface where the live remesh welds it to the rock"""
    if ob.modifiers.get(M_REMESH) is None:
        raise StepError("Chunks need the live Remesh of step 1 to weld them in")
    H = ob.nf_hand
    H.chunk_seed += 1
    r = random.Random(H.seed * 31 + H.chunk_seed * 1009)
    edit = ob.mode == 'EDIT'
    if edit:
        bm = bmesh.from_edit_mesh(ob.data)
    else:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    sel = edit and any(f.select for f in bm.faces)
    p, n = _surface_point(bm, r, H.kind in GROUND_KINDS, sel)
    if p is None:
        if not edit:
            bm.free()
        raise StepError("No faces to put a chunk on")
    a = avg_dim(ob)
    size = H.chunk_size * a * r.uniform(0.6, 1.0)
    sx, sy, sz = (size * r.uniform(0.6, 1.3) for _ in range(3))
    center = p - n * size * 0.2
    rot = Euler((r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6), r.uniform(0, math.tau))).to_matrix().to_4x4()
    mat = Matrix.Translation(center) @ rot @ Matrix.Diagonal((sx, sy, sz, 1.0))
    for v in bm.verts:
        v.select = False
    for f in bm.faces:
        f.select = False
    if H.chunk_shape == 'BOX':
        res = bmesh.ops.create_cube(bm, size=1.0, matrix=mat)
    else:
        res = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.5, matrix=mat)
    for v in res['verts']:
        v.select = True
    bm.select_flush(True)
    if edit:
        bmesh.update_edit_mesh(ob.data)
    else:
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.update()


def convert(context, ob, keep_copy, report=None):
    object_mode(context)
    if not live_blockout(ob):
        raise StepError("Nothing to convert: add the Remesh + Clouds of step 1 first")
    if multires_of(ob) is not None:
        raise StepError("A Multires sculpt is live: apply it (step 3) first")
    check_appliable(ob)
    own_data(ob)
    ensure_own(ob)
    e = noise_empty(ob)
    tex = clouds_texture(ob)
    if keep_copy:
        old = backup_of(ob)
        if old is not None:                                  # one copy per rock: the newest blockout
            oe = old.nf_hand.noise_object
            bpy.data.objects.remove(old)
            if oe is not None and oe.name in bpy.data.objects:
                bpy.data.objects.remove(oe)
        cp = ob.copy()
        cp.data = ob.data.copy()
        cp.name = ob.name + "_Blockout"
        cp['nf_blockout_of'] = ob.name
        coll = backups_collection(context)
        coll.objects.link(cp)
        cp.nf_hand.active = False
        cp.hide_render = True
        if e is not None:                                   # the copy keeps a live clouds offset of its own
            e2 = e.copy()
            coll.objects.link(e2)
            e2.parent = cp
            e2.matrix_parent_inverse = e.matrix_parent_inverse.copy()
            md2 = cp.modifiers.get(M_CLOUDS)
            if md2 is not None:
                md2.texture_coords_object = e2
            cp.nf_hand.noise_object = e2
        try:
            cp.hide_set(True)
        except RuntimeError:
            pass
    had_uv = is_done(ob, 'UV')
    skipped = apply_through(context, ob, (M_REMESH, M_CLOUDS))
    if skipped and report:
        report({'WARNING'}, f"Left in the stack (off or not working): {', '.join(skipped)}")
    if e is not None and ob.nf_hand.noise_object == e:
        ob.nf_hand.noise_object = None
        if not any(o != ob and any(getattr(m, 'texture_coords_object', None) == e for m in o.modifiers)
                   for o in bpy.data.objects if o.type == 'MESH'):
            bpy.data.objects.remove(e)
    if tex is not None and tex.users == 0:
        bpy.data.textures.remove(tex)
    for p in ob.data.polygons:
        p.use_smooth = True
    ob['nf_converted'] = True
    mark_done(ob, 'BLOCKOUT')
    mark_done(ob, 'SHAPE')
    if ob.data.uv_layers.get('UVMap') is None:
        mark_done(ob, 'UV', False)
        if 'uv_applied' in ob.nf_hand:
            del ob.nf_hand['uv_applied']
        if had_uv and report:
            report({'WARNING'}, "The remesh replaced the UVs: unwrap again (step 4)")


def ensure_converted(context, ob, report=None):
    if live_blockout(ob):
        from .props import prefs
        p = prefs(context)
        convert(context, ob, p.keep_editable if p else True, report)
        if report:
            report({'INFO'}, "Converted the blockout to a mesh first")


def back_to_blockout(context, ob):
    """swap the converted rock for its hidden live blockout copy (the converted one is kept hidden)"""
    cp = backup_of(ob)
    if cp is None:
        raise StepError("No blockout copy kept for this object")
    object_mode(context)
    name, mw, colls = ob.name, ob.matrix_world.copy(), list(ob.users_collection)
    coll = backups_collection(context)
    ob.name = name + "_Converted"
    for c in colls:
        c.objects.unlink(ob)
    if ob.name not in coll.objects:
        coll.objects.link(ob)
    ob.hide_render = True
    ob.nf_hand.active = False
    for c in list(cp.users_collection):
        c.objects.unlink(cp)
    for c in colls:
        c.objects.link(cp)
    del cp['nf_blockout_of']
    cp.name = name
    cp.matrix_world = mw
    cp.hide_render = False
    e = cp.nf_hand.noise_object
    if e is not None and e.name in bpy.data.objects:
        for c in list(e.users_collection):
            c.objects.unlink(e)
        for c in colls:
            c.objects.link(e)
    try:
        ob.hide_set(True)
    except RuntimeError:
        pass
    cp.hide_set(False)
    for o in context.selected_objects:
        o.select_set(False)
    cp.select_set(True)
    context.view_layer.objects.active = cp
    H = cp.nf_hand
    H.active = True
    H.set_json = H.set_json or ob.nf_hand.set_json
    H.done = 0
    mark_done(cp, 'BLOCKOUT')
    H.step = 0
    return cp


def sculpt_start(context, ob, report=None):
    ensure_converted(context, ob, report)
    object_mode(context)
    check_appliable(ob)
    own_data(ob)
    mr = multires_of(ob)
    if mr is None:
        mr = ob.modifiers.new(M_MULTIRES, 'MULTIRES')
        move_to_index(context, ob, mr.name, 0)
        n = max(len(ob.data.polygons), 1)
        lv = 0
        while lv < ob.nf_hand.sculpt_levels and n * 4 ** (lv + 1) <= FACE_BUDGET:
            lv += 1
        with context.temp_override(object=ob, active_object=ob):
            for _ in range(lv):
                bpy.ops.object.multires_subdivide(modifier=mr.name, mode='CATMULL_CLARK')
        mr.levels = mr.sculpt_levels = mr.render_levels = mr.total_levels
        ob.nf_hand['keep_level'] = min(ob.nf_hand.keep_level, mr.total_levels)
    context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='SCULPT')
    return mr


def sculpt_subdivide(context, ob):
    mr = multires_of(ob)
    if mr is None:
        raise StepError("Start sculpting first")
    if len(ob.data.polygons) * 4 ** (mr.total_levels + 1) > FACE_BUDGET * 2:
        raise StepError("That would be more than 3 million faces")
    with context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.multires_subdivide(modifier=mr.name, mode='CATMULL_CLARK')
    mr.levels = mr.sculpt_levels = mr.render_levels = mr.total_levels


def _view3d(context):
    if context.area is not None and context.area.type == 'VIEW_3D':
        return context.window, context.area, next((r for r in context.area.regions if r.type == 'WINDOW'), None)
    wm = context.window_manager
    for win in ([context.window] if context.window else []) + [w for w in wm.windows if w != context.window]:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                return win, area, next((r for r in area.regions if r.type == 'WINDOW'), None)
    return None, None, None


def set_brush(context, key):
    label, asset, tool, icon = BRUSHES[key]
    if bpy.app.version >= (4, 3, 0):
        bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS', asset_library_identifier="",
                                     relative_asset_identifier=f"brushes/essentials_brushes-mesh_sculpt.blend/Brush/"
                                                               f"{asset}")
        return
    win, area, region = _view3d(context)                # 4.2: brushes are tools of the 3D View toolbar
    if area is None:
        raise RuntimeError("no 3D View open")
    with context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active):
        bpy.ops.wm.tool_set_by_id(name=tool, space_type='VIEW_3D')


def sculpt_apply(context, ob):
    object_mode(context)
    mr = multires_of(ob)
    if mr is None:
        raise StepError("No Multires to apply (press 'Start Sculpting' first, or skip this step)")
    check_appliable(ob)
    own_data(ob)
    keep = min(ob.nf_hand.keep_level, mr.total_levels)
    if keep == 0 and mr.total_levels > 0:
        # Apply Base: the low-poly base mesh takes the sculpted shape, then the modifier goes
        with context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
            bpy.ops.object.multires_base_apply(modifier=mr.name)
        ob.modifiers.remove(mr)
    else:
        mr.levels = keep
        apply_modifier(context, ob, mr.name)
    ob['nf_sculpted'] = True
    mark_done(ob, 'SCULPT')


def ensure_sculpt_applied(context, ob, report=None):
    if multires_of(ob) is not None:
        sculpt_apply(context, ob)
        if report:
            report({'INFO'}, "Applied the sculpt first")


def unwrap(context, ob, report=None):
    ensure_converted(context, ob, report)
    ensure_sculpt_applied(context, ob, report)
    object_mode(context)
    me = ob.data
    if not len(me.polygons):
        raise StepError("This mesh has no faces to unwrap")
    uvl = me.uv_layers.get('UVMap')
    if uvl is None:
        uvl = me.uv_layers.new(name='UVMap')
    me.uv_layers.active = uvl
    uvl.active_render = True
    prev = context.view_layer.objects.active
    context.view_layer.objects.active = ob
    with context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                               selected_editable_objects=[ob]):
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.cube_project(cube_size=ob.nf_hand.uv_size, correct_aspect=True, clip_to_bounds=False,
                                    scale_to_bounds=False)
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.objects.active = prev
    ob.nf_hand['uv_applied'] = ob.nf_hand.uv_size
    mark_done(ob, 'UV')


def detail_image(ts):
    maps = ts['maps']
    if 'height' in maps:
        return shading.load_image(maps['height'], True), 'height'
    if 'color' in maps:
        return shading.load_image(maps['color'], False), 'color'
    return None, None


def detail(context, ob, report=None):
    ts = need_set(ob, ('height', 'color'))
    ensure_converted(context, ob, report)
    ensure_sculpt_applied(context, ob, report)
    object_mode(context)
    if ob.data.uv_layers.get('UVMap') is None or not is_done(ob, 'UV'):
        unwrap(context, ob)
        if report:
            report({'INFO'}, "Unwrapped first (cube projection)")
    img, src = detail_image(ts)
    if img is None:
        raise StepError("This texture set has no height or colour map")
    ensure_own(ob)
    sub = ob.modifiers.get(M_SUBDIV)
    if sub is None:
        n = max(faces_now(context, ob), 1)
        sub = ob.modifiers.new(M_SUBDIV, 'SUBSURF')
        sub.subdivision_type = 'CATMULL_CLARK'
        sub.levels = 1 if n * 4 <= FACE_BUDGET else 0
        sub.render_levels = 2 if n * 16 <= FACE_BUDGET * 4 else 1
        sub.uv_smooth = 'PRESERVE_BOUNDARIES'
    dp = ob.modifiers.get(M_DETAIL)
    first = dp is None
    if dp is None:
        dp = ob.modifiers.new(M_DETAIL, 'DISPLACE')
        dp.direction = 'NORMAL'
        dp.strength = round(avg_dim(ob) * 0.11, 3)
    tex = dp.texture
    if tex is None or tex.type != 'IMAGE':
        tex = bpy.data.textures.new(f"NF Rock Detail ({ts['name']})", 'IMAGE')
        dp.texture = tex
    tex.image = img
    tex.extension = 'REPEAT'
    tex.use_interpolation = True
    dp.texture_coords = 'UV'
    dp.uv_layer = 'UVMap'
    if first or ob.nf_hand.get('detail_set') != ts.get('id'):
        dp.mid_level = float(ts.get('info', {}).get('mid', 0.5))   # a new set: its own mean height
    ob.nf_hand['detail_set'] = ts.get('id', '')
    # keep the order: ... -> Subdivision -> Displace at the end
    move_to_index(context, ob, M_SUBDIV, len(ob.modifiers) - 1)
    move_to_index(context, ob, M_DETAIL, len(ob.modifiers) - 1)
    if report and src == 'color':
        report({'INFO'}, "This set has no height map: displacing with its colour brightness")
    mark_done(ob, 'DETAIL')


def _is_hand_mat(m, sid=None):
    return m is not None and m.get('nf_hand_mat') and (sid is None or m.get('nf_asset') == sid)


def material(context, ob, rebuild=False, report=None):
    """the rock's own material (never the shared library asset): a Mixer layered material keeps its layers and
    only gets the new base"""
    ts = need_set(ob)
    ensure_converted(context, ob, report)
    if ob.data.uv_layers.get('UVMap') is None:
        unwrap(context, ob, report)
    me = ob.data
    slots = list(me.materials)
    stack = next((m for m in slots if m is not None and hasattr(m, 'mlm') and m.mlm.is_stack), None)
    if stack is not None and len(stack.mlm.layers):
        base = stack.mlm.layers[0]
        cur = base.material if base.source_type == 'MATERIAL' else None
        if _is_hand_mat(cur, ts['id']) and not rebuild:
            return stack
        mat = shading.asset_material(ts, mark_asset=False, mat=cur if _is_hand_mat(cur) else None, hand=True)
        base.source_type = 'MATERIAL'
        base.material = mat                                  # the Mixer rebuilds its stack on this
        mark_done(ob, 'MATERIAL')
        return stack
    cur = next((m for m in slots if _is_hand_mat(m)), None)
    if _is_hand_mat(cur, ts['id']) and not rebuild:
        mat = cur                                            # keep the user's node edits
    else:
        mat = shading.asset_material(ts, mark_asset=False, mat=cur, hand=True)
        mat.name = f"{ob.name}_{ts['name']}"
    if hasattr(mat, 'displacement_method'):
        mat.displacement_method = 'BUMP'                 # the modifier already displaced: shader adds bump only
    if len(me.materials):
        for i in range(len(me.materials)):
            me.materials[i] = mat
    else:
        me.materials.append(mat)
    mark_done(ob, 'MATERIAL')
    return mat


def apply_all(context, ob):
    object_mode(context)
    check_appliable(ob)
    own_data(ob)
    failed = []
    for m in [m for m in ob.modifiers if m.show_viewport]:
        try:
            apply_modifier(context, ob, m.name)
        except StepError:
            failed.append(m.name)
    cleanup_orphans()
    mark_done(ob, 'FINISH')
    return failed


# ---------------------------------------------------------------------------------------------- operators
class _Op:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return in_flow(ctx_ob(context))

    def run(self, fn, *args, **kw):
        try:
            return True, fn(*args, **kw)
        except StepError as e:
            self.report({'ERROR'}, str(e))
            return False, None


class NF_OT_hand_start(Operator):
    bl_idname = 'nf.hand_start'
    bl_label = "Start Step by Step"
    bl_description = ("Start the hand-made workflow (remesh + clouds, sculpt, cube UVs, texture displacement): a new "
                      "block at the 3D cursor, or your selected mesh")
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(items=[(k, v[0], "") for k, v in BASES.items()] +
                       [('SELECTED', "Selected Mesh", "Use the selected mesh as the starting block")])

    @classmethod
    def poll(cls, context):
        return True

    def invoke(self, context, event):
        ob = ctx_ob(context)
        if self.kind == 'SELECTED' and ob is not None and (ob.get('nf_final') or ob.data.attributes.get('nf_rest')):
            return context.window_manager.invoke_confirm(
                self, event, message="Its Nature Forge paint layers and mapping are lost when converting. Continue?"
            ) if bpy.app.version >= (4, 1, 0) else context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        try:
            ob = start(context, self.kind, self.report)
        except StepError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if ob.nf_hand.step == 0 and not is_done(ob, 'BLOCKOUT'):
            self.report({'INFO'}, "Step 1: press 'Add Remesh + Clouds'")
        return {'FINISHED'}


class NF_OT_hand_goto(Operator):
    bl_idname = 'nf.hand_goto'
    bl_label = "Go to Step"
    bl_description = "Show this step"
    bl_options = {'INTERNAL'}                       # page changes are not undo steps

    step: IntProperty()

    @classmethod
    def poll(cls, context):
        return in_flow(ctx_ob(context))

    def execute(self, context):
        ob = ctx_ob(context)
        ob.nf_hand.step = max(0, min(self.step, len(STEPS) - 1))
        return {'FINISHED'}


class NF_OT_hand_blockout(_Op, Operator):
    bl_idname = 'nf.hand_blockout'
    bl_label = "Add Remesh + Clouds"
    bl_description = ("Step 1: a voxel Remesh fuses the block into one solid, a Displace with a Clouds texture pushes "
                      "it into a lumpy rock mass (both stay live: tweak voxel size, strength, cloud size, seed)")

    def execute(self, context):
        ob = ctx_ob(context)
        ok, _ = self.run(blockout, context, ob, self.report)
        if not ok:
            return {'CANCELLED'}
        self.report({'INFO'}, f"{ob.name}: {faces_now(context, ob):,} faces - tweak, then Next")
        return {'FINISHED'}


class NF_OT_hand_seed(_Op, Operator):
    bl_idname = 'nf.hand_seed'
    bl_label = "New Clouds"
    bl_description = "Roll a different cloud pattern"

    def execute(self, context):
        ob = ctx_ob(context)
        ob.nf_hand.seed = random.randint(0, 99999)
        return {'FINISHED'}


class NF_OT_hand_chunk(_Op, Operator):
    bl_idname = 'nf.hand_chunk'
    bl_label = "Add Chunk"
    bl_description = ("Add a random block / lump on the surface (on the selected faces in Edit Mode), like "
                      "duplicating parts by hand; the remesh welds it to the rock. In Edit Mode it stays selected so "
                      "you can G / S / R it right away")

    def execute(self, context):
        ob = ctx_ob(context)
        ok, _ = self.run(add_chunk, context, ob)
        if not ok:
            return {'CANCELLED'}
        ob.update_tag()
        return {'FINISHED'}


class NF_OT_hand_edit(_Op, Operator):
    bl_idname = 'nf.hand_edit'
    bl_label = "Edit Mode"
    bl_description = ("Toggle Edit Mode: select faces, Shift+D to duplicate a chunk, G / S / R to move, scale, "
                      "rotate it. The remesh and clouds stay visible while you edit")

    def execute(self, context):
        ob = ctx_ob(context)
        if ob.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        else:
            object_mode(context)
            context.view_layer.objects.active = ob
            bpy.ops.object.mode_set(mode='EDIT')
            context.tool_settings.mesh_select_mode = (False, False, True)
        return {'FINISHED'}


class NF_OT_hand_convert(_Op, Operator):
    bl_idname = 'nf.hand_convert'
    bl_label = "Convert to Mesh"
    bl_description = "Apply the Remesh + Clouds: the lumpy shape becomes the real mesh you sculpt and texture"

    keep: BoolProperty(name="Keep Blockout Copy", default=True,
                       description="Keep a hidden copy with the live remesh / clouds in 'NF Editable Copies' "
                                   "('Back to Blockout' brings it back)")

    def invoke(self, context, event):
        from .props import prefs
        p = prefs(context)
        self.keep = p.keep_editable if p else True
        return self.execute(context)

    def execute(self, context):
        ob = ctx_ob(context)
        ok, _ = self.run(convert, context, ob, self.keep, self.report)
        if not ok:
            return {'CANCELLED'}
        ob.nf_hand.step = STEP_KEYS.index('SCULPT')
        self.report({'INFO'}, f"{ob.name}: {len(ob.data.polygons):,} faces")
        return {'FINISHED'}


class NF_OT_hand_back_blockout(_Op, Operator):
    bl_idname = 'nf.hand_back_blockout'
    bl_label = "Back to Blockout"
    bl_description = ("Bring back the live Remesh + Clouds copy kept by 'Convert to Mesh' (same seed and sliders). "
                      "The converted rock is kept hidden in 'NF Editable Copies'")

    @classmethod
    def poll(cls, context):
        ob = ctx_ob(context)
        return in_flow(ob) and backup_of(ob) is not None

    def execute(self, context):
        ok, _ = self.run(back_to_blockout, context, ctx_ob(context))
        return {'FINISHED'} if ok else {'CANCELLED'}


class NF_OT_hand_sculpt(_Op, Operator):
    bl_idname = 'nf.hand_sculpt'
    bl_label = "Start Sculpting"
    bl_description = "Add a Multires modifier (subdivided for sculpting) and switch to Sculpt Mode"

    def execute(self, context):
        ob = ctx_ob(context)
        new = multires_of(ob) is None
        ok, mr = self.run(sculpt_start, context, ob, self.report)
        if not ok:
            return {'CANCELLED'}
        if new:
            try:
                set_brush(context, 'SCRAPE')
            except (RuntimeError, AttributeError):
                pass
        self.report({'INFO'}, f"Sculpting on {faces_now(context, ob):,} faces ({mr.total_levels} levels)")
        return {'FINISHED'}


class NF_OT_hand_subdivide(_Op, Operator):
    bl_idname = 'nf.hand_subdivide'
    bl_label = "Subdivide"
    bl_description = "One more Multires level for finer sculpting (4x faces)"

    def execute(self, context):
        ob = ctx_ob(context)
        mode = ob.mode
        ok, _ = self.run(sculpt_subdivide, context, ob)
        if mode == 'SCULPT' and ob.mode != 'SCULPT':
            bpy.ops.object.mode_set(mode='SCULPT')
        return {'FINISHED'} if ok else {'CANCELLED'}


class NF_OT_hand_brush(Operator):
    bl_idname = 'nf.hand_brush'
    bl_label = "Brush"
    bl_description = "Switch to this sculpt brush"
    bl_options = {'REGISTER'}

    brush: EnumProperty(items=[(k, v[0], "", v[3], i) for i, (k, v) in enumerate(BRUSHES.items())])

    @classmethod
    def poll(cls, context):
        return context.mode == 'SCULPT'

    def execute(self, context):
        try:
            set_brush(context, self.brush)
        except (RuntimeError, AttributeError) as e:
            self.report({'WARNING'}, f"Could not switch brush: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class NF_OT_hand_sculpt_apply(_Op, Operator):
    bl_idname = 'nf.hand_sculpt_apply'
    bl_label = "Apply Sculpt"
    bl_description = "Bake the sculpt into the mesh at 'Keep Level' and remove the Multires modifier"

    def execute(self, context):
        ob = ctx_ob(context)
        ok, _ = self.run(sculpt_apply, context, ob)
        if not ok:
            return {'CANCELLED'}
        ob.nf_hand.step = STEP_KEYS.index('UV')
        self.report({'INFO'}, f"{ob.name}: {len(ob.data.polygons):,} faces")
        return {'FINISHED'}


class NF_OT_hand_unwrap(_Op, Operator):
    bl_idname = 'nf.hand_unwrap'
    bl_label = "Unwrap (Cube Projection)"
    bl_description = ("Cube projection UVs: the texture keeps its real-world size on every side. 'Texture Size' "
                      "stays live afterwards")

    def execute(self, context):
        ob = ctx_ob(context)
        ok, _ = self.run(unwrap, context, ob, self.report)
        return {'FINISHED'} if ok else {'CANCELLED'}


class NF_OT_hand_detail(_Op, Operator):
    bl_idname = 'nf.hand_detail'
    bl_label = "Add Rock Detail"
    bl_description = ("Subdivision + Displace with the texture set's displacement map (UV): the rocky detail of the "
                      "texture becomes real geometry")

    def execute(self, context):
        ob = ctx_ob(context)
        ok, _ = self.run(detail, context, ob, self.report)
        if not ok:
            return {'CANCELLED'}
        self.report({'INFO'}, f"{ob.name}: {faces_now(context, ob):,} faces in the viewport")
        return {'FINISHED'}


class NF_OT_hand_material(_Op, Operator):
    bl_idname = 'nf.hand_material'
    bl_label = "Add All Textures"
    bl_description = ("This rock's own material from the texture set: colour (x AO), roughness, metal, normal map and "
                      "a bump from the height map, all on the cube projected UVs")

    rebuild: BoolProperty(name="Rebuild", default=False, options={'SKIP_SAVE'},
                          description="Rebuild the material from scratch (your node edits are replaced)")

    def execute(self, context):
        ob = ctx_ob(context)
        ok, mat = self.run(material, context, ob, self.rebuild, self.report)
        if not ok:
            return {'CANCELLED'}
        self.report({'INFO'}, f"{mat.name}: {library.describe(texture_set(ob))}")
        return {'FINISHED'}


def _refresh_set(context, ob, report):
    try:
        if is_done(ob, 'DETAIL') and ob.modifiers.get(M_DETAIL) is not None:
            detail(context, ob)
        if is_done(ob, 'MATERIAL'):
            material(context, ob, rebuild=True)
    except StepError as e:
        report({'ERROR'}, str(e))
        return
    ts = texture_set(ob)
    report({'INFO'}, f"{ts['name']}: {library.describe(ts)}")


class NF_OT_hand_set_library(_Op, Operator):
    bl_idname = 'nf.hand_set_library'
    bl_label = "Use Library Pick"
    bl_description = "Use the texture set picked in the Texture Library for the rock detail and the material"

    set_id: StringProperty(options={'SKIP_SAVE'}, description="Empty = the set picked in the Texture Library")

    def execute(self, context):
        ob = ctx_ob(context)
        st = context.window_manager.nf_lib
        sid = self.set_id or st.pick_id or (st.pick if st.pick != 'NONE' else '')
        ts = library.get(sid)
        if ts is None:
            self.report({'ERROR'}, "Pick a texture set in the Texture Library panel first")
            return {'CANCELLED'}
        store_set(ob, ts)
        _refresh_set(context, ob, self.report)
        return {'FINISHED'}


class NF_OT_hand_set_file(_Op, Operator):
    bl_idname = 'nf.hand_set_file'
    bl_label = "Texture Set from Disk"
    bl_description = ("Pick any ONE map of the texture set (e.g. cliff_side_disp_4k.png): the colour, normal, "
                      "roughness, AO ... next to it are found automatically")

    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ob = ctx_ob(context)
        found = library.set_from_files(self.directory, [f.name for f in self.files])
        if not found:
            self.report({'ERROR'}, "No texture set there (need a height, colour or normal map)")
            return {'CANCELLED'}
        store_set(ob, found[0])
        context.window_manager.nf_lib.pick_id = found[0]['id']
        _refresh_set(context, ob, self.report)
        return {'FINISHED'}


class NF_OT_hand_apply_all(_Op, Operator):
    bl_idname = 'nf.hand_apply_all'
    bl_label = "Apply Everything"
    bl_description = ("Apply all modifiers: one real high-poly mesh (for sculpting more, or your own export). "
                      "For games prefer 'Bake Game LODs'")

    def invoke(self, context, event):
        n = faces_now(context, ctx_ob(context))
        return context.window_manager.invoke_confirm(self, event, message=f"Apply into a {n:,} face mesh?") \
            if bpy.app.version >= (4, 1, 0) else context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        ob = ctx_ob(context)
        ok, failed = self.run(apply_all, context, ob)
        if not ok:
            return {'CANCELLED'}
        if failed:
            self.report({'WARNING'}, f"Could not apply: {', '.join(failed)} (left in the stack)")
        else:
            self.report({'INFO'}, f"{ob.name}: {len(ob.data.polygons):,} faces")
        return {'FINISHED'}


class NF_OT_hand_exit(_Op, Operator):
    bl_idname = 'nf.hand_exit'
    bl_label = "Stop Guiding"
    bl_description = "Leave the step-by-step guide for this object (modifiers and material stay as they are)"

    def execute(self, context):
        ob = ctx_ob(context)
        ob.nf_hand.active = False
        return {'FINISHED'}


CLASSES = (NF_HandSettings, NF_OT_hand_start, NF_OT_hand_goto, NF_OT_hand_blockout, NF_OT_hand_seed,
           NF_OT_hand_chunk, NF_OT_hand_edit, NF_OT_hand_convert, NF_OT_hand_back_blockout, NF_OT_hand_sculpt,
           NF_OT_hand_subdivide, NF_OT_hand_brush, NF_OT_hand_sculpt_apply, NF_OT_hand_unwrap, NF_OT_hand_detail,
           NF_OT_hand_material, NF_OT_hand_set_library, NF_OT_hand_set_file, NF_OT_hand_apply_all, NF_OT_hand_exit)


def _cleanup_timer():
    try:
        cleanup_orphans()
    except (RuntimeError, ReferenceError):
        pass
    return None


_COUNT = {'objects': -1}


@persistent
def _on_load(*_):
    _COUNT['objects'] = len(bpy.data.objects)
    _cleanup_timer()


@persistent
def _on_depsgraph(scene, depsgraph):
    """a rock was deleted: remove its hidden clouds offset (from a timer, never inside the depsgraph update)"""
    n = len(bpy.data.objects)
    if n < _COUNT['objects'] and not bpy.app.timers.is_registered(_cleanup_timer):
        bpy.app.timers.register(_cleanup_timer, first_interval=0.05)
    _COUNT['objects'] = n


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.nf_hand = PointerProperty(type=NF_HandSettings)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if bpy.app.timers.is_registered(_cleanup_timer):
        bpy.app.timers.unregister(_cleanup_timer)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    del bpy.types.Object.nf_hand
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
