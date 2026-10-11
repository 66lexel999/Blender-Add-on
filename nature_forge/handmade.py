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


def hand(o):
    """o.nf_hand, or None when the object never had one. Blender 4.2 writes an empty group into any object whose
    nf_hand is merely read, so scans over the whole file must not touch it on plain objects (5.x keeps add-on
    data apart from the custom properties: nothing shows up there)"""
    if hasattr(o, 'bl_system_properties_get'):
        return o.nf_hand
    return o.nf_hand if o.get('nf_hand') is not None else None


def in_flow(ob):
    if ob is None or ob.type != 'MESH':
        return False
    h = hand(ob)
    return h is not None and h.active


def _linked(o):
    """data from a library (linked or a library override): never ours to change"""
    return o.library is not None or o.override_library is not None or \
        (o.data is not None and getattr(o.data, 'library', None) is not None)


def _is_stack(m):
    """a Material Layer Mixer layered material - recognised also while the Mixer add-on is off (its node tree
    keeps the Mixer's output node), so it is never mistaken for a plain material and replaced"""
    if m is None:
        return False
    if hasattr(m, 'mlm'):
        return bool(m.mlm.is_stack)
    return m.node_tree is not None and m.node_tree.nodes.get('MLM::out') is not None


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
        return len(ob.data.polygons)
    if n == 0 and ob.mode == 'EDIT':
        return len(ob.data.polygons)                  # edit mode may not hand out the evaluated faces
    return n


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
        hint = ": its Voxel Size may be too big for the rock" if name == M_REMESH else " (it is disabled or failed)"
        raise StepError(f"Could not apply '{name}'{hint}")


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


def _alive(o):
    return o is not None and o.name in bpy.data.objects and o.users > 0


def is_backup(o):
    return o is not None and bool(o.get('nf_backup')) and not o.nf_hand.active


def backup_of(ob):
    """the hidden live blockout kept for this rock (linked by pointer both ways, so renames / duplicates / a new rock
    reusing a name never pick up someone else's copy)"""
    b = ob.nf_hand.backup
    if _alive(b) and is_backup(b) and b.nf_hand.owner == ob:
        return b
    return None


def hide_backup(o):
    o.hide_render = True
    o.hide_viewport = True                         # global: hidden in every scene / view layer
    try:
        o.hide_set(True)
    except RuntimeError:
        pass


def unhide(o):
    o.hide_render = False
    o.hide_viewport = False
    try:
        o.hide_set(False)
    except RuntimeError:
        pass


def remove_object(o):
    e = o.nf_hand.noise_object if o.type == 'MESH' else None
    me = o.data if o.type == 'MESH' else None
    bpy.data.objects.remove(o)
    if e is not None and e.name in bpy.data.objects and not _empty_used(e):
        bpy.data.objects.remove(e)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


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
    return [o for o in bpy.data.objects if o != ob and o.type == 'MESH' and o.library is None and
            any(getattr(m, 'texture', None) == tex for m in o.modifiers)]


def _material_users(m, ob):
    """other objects showing material m. Alt+D twins of ob (same mesh: one slot for both), kept blockout copies and
    linked objects don't count - those can't or shouldn't get a copy of their own"""
    return [o for o in bpy.data.objects if o != ob and o.type == 'MESH' and o.users and o.library is None and
            o.data != ob.data and not o.get('nf_backup') and any(s.material == m for s in o.material_slots)]


def _copy_material(m):
    cp = m.copy()
    if hasattr(m, 'mlm') and m.mlm.is_stack and len(m.mlm.layers) and m.mlm.layers[0].material and \
            m.mlm.layers[0].material.get('nf_hand_mat'):
        cp.mlm.layers[0].material = m.mlm.layers[0].material.copy()
    return cp


def ensure_own(ob, keep_world=True, materials=None, textures=None):
    """a Shift+D copy shares the hidden offset empty, the textures and the rock's material with the original:
    give it its own, so tweaking one rock never changes the other. keep_world: the copy keeps exactly the look it
    has right now. materials: {material: objects that had it before the copy was made} - only a NEW object gets
    its own material that way (a material you linked to several rocks on purpose stays shared). textures:
    {texture: objects using it}, built once when many objects are checked"""
    if _linked(ob):
        return
    dp = ob.modifiers.get(M_CLOUDS)
    e = ob.nf_hand.noise_object
    if e is not None and e.name in bpy.data.objects and e.parent != ob:
        mw = e.matrix_world.copy()
        e2 = e.copy()
        for c in ob.users_collection:
            c.objects.link(e2)
        e2.parent = ob
        e2.matrix_parent_inverse = Matrix.Identity(4)
        if keep_world:
            e2.matrix_world = mw
        e2.name = f"{EMPTY_PREFIX} ({ob.name})"
        ob.nf_hand.noise_object = e2
        if dp is not None:
            dp.texture_coords_object = e2
    for name in (M_CLOUDS, M_DETAIL):
        md = ob.modifiers.get(name)
        if md is None or md.texture is None:
            continue
        old = md.texture
        users = [o for o in textures.get(old, ()) if o != ob] if textures is not None else _texture_users(old, ob)
        if users:
            md.texture = old.copy()
            if textures is not None and ob in textures.get(old, ()):
                textures[old].remove(ob)
    if materials is not None:
        me = ob.data
        for i, m in enumerate(me.materials):
            if (_is_hand_mat(m) or _is_stack(m)) and any(o != ob and o.data != me for o in materials.get(m, ())):
                me.materials[i] = _copy_material(m)
    b = ob.nf_hand.backup
    if b is not None and (not _alive(b) or b.nf_hand.owner != ob):
        ob.nf_hand.backup = None                       # a copy of a rock does not own the rock's blockout copy


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


def _used_empties():
    """every object some rock uses as its clouds offset (one pass over the file)"""
    used = set()
    for o in bpy.data.objects:
        if o.type != 'MESH' or not o.users:
            continue
        h = hand(o)
        if h is not None and h.noise_object is not None:
            used.add(h.noise_object)
        for m in o.modifiers:
            if m.type == 'DISPLACE' and m.texture_coords_object is not None:
                used.add(m.texture_coords_object)
    return used


def _empty_used(e):
    return e in _used_empties()


def cleanup_orphans():
    """offset empties nobody uses any more, blockout copies whose rock is gone"""
    backups, empties = [], []
    for o in bpy.data.objects:
        if o.type == 'MESH' and o.get('nf_backup'):
            backups.append(o)
        elif o.type == 'EMPTY' and o.name.startswith(EMPTY_PREFIX):
            empties.append(o)
    for b in backups:
        own = b.nf_hand.owner
        if not b.nf_hand.active and (not _alive(own) or own.nf_hand.backup != b):
            remove_object(b)
    if empties:
        used = _used_empties()
        for e in empties:
            if e.name in bpy.data.objects and e not in used:
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
    for o in [o for o in bpy.data.objects if o.type == 'MESH' and o.data == me]:   # Alt+D rocks share the UVs
        dp = o.modifiers.get(M_DETAIL)
        if dp is not None and o.nf_hand.strength_follows:
            dp.strength = dp.strength * new / old        # same bump height relative to the texture
        o.nf_hand['uv_applied'] = new
        o.nf_hand['uv_size'] = new
        o.update_tag()


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
    backup: PointerProperty(type=bpy.types.Object, description="This rock's hidden live blockout copy")
    owner: PointerProperty(type=bpy.types.Object, description="The rock this blockout copy belongs to")


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
    if ob.modifiers.get(M_DETAIL) is not None or ob.get('nf_applied'):
        mark_done(ob, 'DETAIL')
    if any(_is_hand_mat(s.material) or _is_stack(s.material) for s in ob.material_slots):
        mark_done(ob, 'MATERIAL')
    if ob.get('nf_applied'):
        mark_done(ob, 'FINISH')
    if live_blockout(ob):
        H.step = STEP_KEYS.index('BLOCKOUT')
    elif multires_of(ob) is not None:
        H.step = STEP_KEYS.index('SCULPT')               # a sculpt in progress
    elif ob.get('nf_applied'):
        H.step = STEP_KEYS.index('FINISH')
    else:
        H.step = next((i for i, k in enumerate(STEP_KEYS) if not is_done(ob, k) and k != 'SCULPT'),
                      len(STEPS) - 1)


def _take_backup(context, ob):
    """out of the hidden copies collection (next to the rock it was kept for), visible and rendering again"""
    own = ob.nf_hand.owner
    dest = list(own.users_collection) if _alive(own) else []
    dest = [c for c in dest if c.name != BACKUPS] or [context.collection or context.scene.collection]
    keep = bpy.data.collections.get(BACKUPS)
    e = ob.nf_hand.noise_object
    for o in (ob, e):
        if o is None or o.name not in bpy.data.objects:
            continue
        for c in dest:
            if o.name not in c.objects:
                c.objects.link(o)
        if keep is not None and o.name in keep.objects:
            keep.objects.unlink(o)
    unhide(ob)


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
        H = ob.nf_hand
        resume = bool(live_blockout(ob) or H.get('uv_applied') or ob.get('nf_converted') or H.set_json or
                      ob.get('nf_backup') or ob.modifiers.get(M_DETAIL) is not None)
        if not resume and any(abs(s - 1.0) > 1e-6 for s in ob.scale):
            # fresh start only: a rock already in progress keeps its scale (its sizes were set for it)
            with context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                                       selected_editable_objects=[ob]):
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            if report:
                report({'INFO'}, "Applied the object scale, so voxels and texture size are in real metres")
        if ob.get('nf_backup'):
            _take_backup(context, ob)                     # a kept blockout copy taken into the guide is a rock now
        for k in ('nf_final', 'nf_kind', 'nf_preset', 'nf_backup'):
            if k in ob:
                del ob[k]
        if H.owner is not None:
            own = H.owner
            if _alive(own) and own.nf_hand.backup == ob:
                own.nf_hand.backup = None
            H.owner = None
        if ob.hide_viewport:
            ob.hide_viewport = False
        H.active = True
        if resume:
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


def _no_effect(m):
    """our own step-1 modifiers that Blender refuses to apply because they do nothing (off, strength 0)"""
    return not m.show_viewport or (m.type == 'DISPLACE' and abs(m.strength) < 1e-9)


def _make_backup(context, ob, e):
    cp = ob.copy()
    cp.data = ob.data.copy()
    cp.name = ob.name + "_Blockout"
    cp['nf_backup'] = True
    coll = backups_collection(context)
    coll.objects.link(cp)
    cp.nf_hand.active = False
    cp.nf_hand.backup = None
    cp.nf_hand.owner = ob
    if e is not None:                                   # the copy keeps a live clouds offset of its own
        e2 = e.copy()
        coll.objects.link(e2)
        e2.parent = cp
        e2.matrix_parent_inverse = e.matrix_parent_inverse.copy()
        md2 = cp.modifiers.get(M_CLOUDS)
        if md2 is not None:
            md2.texture_coords_object = e2
        cp.nf_hand.noise_object = e2
    hide_backup(cp)
    return cp


def convert(context, ob, keep_copy, report=None):
    """apply the stack down to the clouds on a temporary copy and swap the mesh in only when all of it worked:
    a failure never leaves a half-converted rock or loses the kept blockout"""
    object_mode(context)
    if not live_blockout(ob):
        raise StepError("Nothing to convert: add the Remesh + Clouds of step 1 first")
    if multires_of(ob) is not None:
        raise StepError("A Multires sculpt is live: apply it (step 3) first")
    check_appliable(ob)
    own_data(ob)
    ensure_own(ob, keep_world=True)
    e = noise_empty(ob)
    tex = clouds_texture(ob)
    old_backup = backup_of(ob)
    cp = _make_backup(context, ob, e) if keep_copy else None
    tmp = ob.copy()
    tmp.data = ob.data.copy()
    tmp.nf_hand.active = False
    context.scene.collection.objects.link(tmp)
    names = [m.name for m in tmp.modifiers]
    last = max(names.index(n) for n in (M_REMESH, M_CLOUDS) if n in names)
    dropped = [n for n in (M_REMESH, M_CLOUDS) if n in names and _no_effect(tmp.modifiers[n])]
    for n in dropped:
        tmp.modifiers.remove(tmp.modifiers[n])
    try:
        targets = [n for n in (M_REMESH, M_CLOUDS) if tmp.modifiers.get(n) is not None]
        skipped = apply_through(context, tmp, targets) if targets else \
            apply_through(context, tmp, [n for n in names[:last + 1] if n not in dropped][-1:])
    except StepError:
        tm = tmp.data
        bpy.data.objects.remove(tmp)
        if tm.users == 0:
            bpy.data.meshes.remove(tm)
        if cp is not None:
            remove_object(cp)
        raise
    if not len(tmp.data.polygons):
        tm = tmp.data
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(tm)
        if cp is not None:
            remove_object(cp)
        raise StepError("The Remesh leaves nothing: make its Voxel Size smaller than the rock")
    old_me = ob.data
    ob.data = tmp.data
    bpy.data.objects.remove(tmp)
    if old_me.users == 0:
        bpy.data.meshes.remove(old_me)
    for n in names[:last + 1]:                          # what the copy applied (or dropped) leaves the rock stack
        if n not in skipped and ob.modifiers.get(n) is not None:
            ob.modifiers.remove(ob.modifiers[n])
    if skipped and report:
        report({'WARNING'}, f"Left in the stack (off or not working): {', '.join(skipped)}")
    if cp is not None:
        if old_backup is not None and old_backup != cp:
            remove_object(old_backup)                   # one copy per rock: the newest blockout
        ob.nf_hand.backup = cp
    if e is not None and ob.nf_hand.noise_object == e:
        ob.nf_hand.noise_object = None
        if not _empty_used(e):
            bpy.data.objects.remove(e)
    if tex is not None and tex.users == 0:
        bpy.data.textures.remove(tex)
    for p in ob.data.polygons:
        p.use_smooth = True
    ob['nf_converted'] = True
    had_uv = is_done(ob, 'UV')
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


CLOUD_KEYS = ('seed', 'cloud_size', 'cloud_stretch', 'cloud_detail', 'cloud_contrast', 'cloud_basis', 'cloud_hard',
              'cloud_twist', 'chunk_size', 'chunk_shape', 'chunk_seed')


def _copy_modifier(src, ob):
    """the same modifier, with all its settings, on another object"""
    m = ob.modifiers.new(src.name, src.type)
    m.name = src.name
    for p in src.bl_rna.properties:
        k = p.identifier
        if p.is_readonly or k in ('name', 'type', 'rna_type', 'is_active', 'persistent_uid'):
            continue
        try:
            setattr(m, k, getattr(src, k))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    try:
        for k in src.keys():                               # geometry nodes inputs live in ID properties
            m[k] = src[k]
    except (TypeError, KeyError, ValueError):
        pass
    return m


def back_to_blockout(context, ob):
    """bring the live blockout kept at Convert back INTO this rock. The object stays what it is (name, parent,
    children, constraints, animation, your own modifiers, materials and texture set); only its mesh goes back to the
    blockout and the Remesh + Clouds return on top with the same seed and sliders. Sculpt, UVs and rock detail
    are dropped (Ctrl+Z brings them back)"""
    cp = backup_of(ob)
    if cp is None:
        raise StepError("No blockout copy kept for this object")
    object_mode(context)
    H = ob.nf_hand
    names = [m.name for m in cp.modifiers]
    nf = [names.index(n) for n in (M_REMESH, M_CLOUDS) if n in names]
    top = names[:max(nf) + 1] if nf else []            # what Convert applied: the blockout and anything above it
    for m in list(ob.modifiers):
        if m.name in (M_MULTIRES, M_SUBDIV, M_DETAIL) or m.type == 'MULTIRES':
            ob.modifiers.remove(m)                     # made for the converted mesh
    # the blockout mesh, with the rock's current materials (the same slot count keeps per-object slots)
    old_me, me = ob.data, cp.data
    me.materials.clear()
    for m in old_me.materials:
        me.materials.append(m)
    ob.data = me
    name = old_me.name
    if old_me.users == 0:
        bpy.data.meshes.remove(old_me)
        me.name = name
    for n in top:
        if ob.modifiers.get(n) is None:
            _copy_modifier(cp.modifiers[n], ob)
    for i, n in enumerate(top):
        move_to_index(context, ob, n, i)
    e = cp.nf_hand.noise_object
    if e is not None and e.name in bpy.data.objects:
        keep = bpy.data.collections.get(BACKUPS)
        for c in ob.users_collection:
            if e.name not in c.objects:
                c.objects.link(e)
        if keep is not None and e.name in keep.objects and keep not in ob.users_collection:
            keep.objects.unlink(e)
        e.parent = ob                                  # same local offset: the pattern sits on the rock as before
        e.name = f"{EMPTY_PREFIX} ({ob.name})"
        H.noise_object = e
        dp = ob.modifiers.get(M_CLOUDS)
        if dp is not None:
            dp.texture_coords_object = e
    for k in CLOUD_KEYS:
        v = cp.nf_hand.get(k)
        if v is not None:
            H[k] = v
        elif k in H:
            del H[k]
    cp.nf_hand.noise_object = None
    H.backup = None
    bpy.data.objects.remove(cp)
    for k in ('nf_converted', 'nf_sculpted', 'nf_applied'):
        if k in ob:
            del ob[k]
    for k in ('uv_applied', 'detail_set'):
        if k in H:
            del H[k]
    H.done = 0
    mark_done(ob, 'BLOCKOUT')
    H.step = 0
    sync_clouds(ob)
    for o in context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob
    return ob


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


def _mlm_nodes():
    """the Material Layer Mixer's node module (legacy add-on or extension), if it is enabled"""
    import sys
    for name, mod in list(sys.modules.items()):
        if name.endswith('material_layer_mixer.nodes') and hasattr(mod, 'rebuild'):
            return mod
    return None


def mlm_refresh(stack):
    """make the Mixer regenerate its layer groups (it copies the base material into its own group)"""
    mod = _mlm_nodes()
    if mod is not None:
        try:
            mod.rebuild(stack, force=True)
            return True
        except Exception as e:                       # never break the guide over the other add-on
            print("Nature Forge: Mixer refresh failed:", e)
    return False


def mlm_stack(ob):
    return next((m for m in ob.data.materials if _is_stack(m)), None)


def rock_material(ob):
    """the material the guide made for this rock (its Mixer stack, or the plain one)"""
    return mlm_stack(ob) or next((m for m in ob.data.materials if _is_hand_mat(m)), None)


def check_stack(ob):
    stack = mlm_stack(ob)
    if stack is not None and not hasattr(stack, 'mlm'):
        raise StepError(f"'{stack.name}' is a Material Layer Mixer material: enable the Material Layer Mixer "
                        f"add-on to change it")
    return stack


def material(context, ob, rebuild=False, report=None, auto=True):
    """the rock's own material (never the shared library asset; a material other rocks use too is copied, not
    changed). A Mixer layered material keeps its layers and only gets the new base. Only the rock material's
    slots are touched: your other materials stay. auto=False: don't convert / unwrap for it"""
    ts = need_set(ob)
    check_stack(ob)
    if auto:
        ensure_converted(context, ob, report)
        if ob.data.uv_layers.get('UVMap') is None:
            unwrap(context, ob, report)
    ensure_own(ob)
    me = ob.data
    stack = mlm_stack(ob)
    if stack is not None and len(stack.mlm.layers):
        base = stack.mlm.layers[0]
        cur = base.material if base.source_type == 'MATERIAL' else None
        if _is_hand_mat(cur, ts['id']) and not rebuild:
            return stack
        if _material_users(stack, ob):                  # other rocks show this stack: this one gets its own
            old, stack = stack, stack.copy()
            for i, m in enumerate(me.materials):
                if m == old:
                    me.materials[i] = stack
            base = stack.mlm.layers[0]
        # a NEW datablock: the Mixer only regenerates a layer when its source changes
        mat = shading.asset_material(ts, mark_asset=False, hand=True)
        mat.name = f"{ob.name}_{ts['name']}"
        if hasattr(mat, 'displacement_method'):
            mat.displacement_method = 'BUMP'
        base.source_type = 'MATERIAL'
        base.material = mat
        mlm_refresh(stack)
        if _is_hand_mat(cur) and cur.users == 0:
            bpy.data.materials.remove(cur)
        mark_done(ob, 'MATERIAL')
        return stack
    hand_slots = [i for i, m in enumerate(me.materials) if _is_hand_mat(m)]
    cur = me.materials[hand_slots[0]] if hand_slots else None
    if _is_hand_mat(cur, ts['id']) and not rebuild:
        mat = cur                                            # keep the user's node edits
    else:
        reuse = cur if cur is not None and not _material_users(cur, ob) else None
        mat = shading.asset_material(ts, mark_asset=False, mat=reuse, hand=True)
        mat.name = f"{ob.name}_{ts['name']}"
    if hasattr(mat, 'displacement_method'):
        mat.displacement_method = 'BUMP'                 # the modifier already displaced: shader adds bump only
    if hand_slots:
        for i in hand_slots:
            me.materials[i] = mat
    elif not any(m is not None for m in me.materials):
        if len(me.materials):
            for i in range(len(me.materials)):
                me.materials[i] = mat
        else:
            me.materials.append(mat)
    else:
        me.materials.append(mat)                         # your own materials keep their slots
        n = len(me.materials) - 1
        me.polygons.foreach_set('material_index', [n] * len(me.polygons))
        me.update()
        if report:
            report({'INFO'}, "Added the rock material as a new slot; your other materials are kept in theirs")
    mark_done(ob, 'MATERIAL')
    return mat


def swap_detail_image(ob, ts):
    """new set on a rock whose rock detail exists: just swap the displacement image (nothing gets converted)"""
    dp = ob.modifiers.get(M_DETAIL)
    if dp is None:
        return
    ensure_own(ob)
    img, src = detail_image(ts)
    if img is None:
        return
    if dp.texture is None or dp.texture.type != 'IMAGE':
        dp.texture = bpy.data.textures.new(f"NF Rock Detail ({ts['name']})", 'IMAGE')
    dp.texture.image = img
    if ob.nf_hand.get('detail_set') != ts.get('id'):
        dp.mid_level = float(ts.get('info', {}).get('mid', 0.5))
    ob.nf_hand['detail_set'] = ts.get('id', '')


def use_set(context, ob, ts, report):
    """give the guide a texture set: checked first, then the rock detail image and (if done) the material follow.
    Never converts a live blockout, never touches node edits when the set did not change"""
    miss = shading.missing_files(ts)
    if miss:
        report({'ERROR'}, f"Texture files missing in '{os.path.dirname(miss[0])}': rescan the library")
        return False
    if is_done(ob, 'MATERIAL'):
        try:
            check_stack(ob)
        except StepError as e:
            report({'ERROR'}, str(e))
            return False
    old = texture_set(ob)
    changed = old is None or old.get('id') != ts.get('id')
    store_set(ob, ts)
    try:
        if changed:
            swap_detail_image(ob, ts)
        if is_done(ob, 'MATERIAL'):
            material(context, ob, rebuild=changed, auto=False)
    except StepError as e:
        report({'ERROR'}, str(e))
        return False
    report({'INFO'}, f"{ts['name']}: {library.describe(ts)}")
    return True


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
    if not failed:
        ob['nf_applied'] = True
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
    bl_description = ("Bring back the live Remesh + Clouds kept by 'Convert to Mesh' (same seed and sliders). Only "
                      "the mesh goes back: the object, its parent, children, your own modifiers, material and "
                      "texture set stay")

    @classmethod
    def poll(cls, context):
        ob = ctx_ob(context)
        return in_flow(ob) and backup_of(ob) is not None

    def invoke(self, context, event):
        msg = "Go back to the live blockout? The sculpt, UVs and rock detail are dropped (Ctrl+Z undoes it)"
        return context.window_manager.invoke_confirm(self, event, message=msg) \
            if bpy.app.version >= (4, 1, 0) else context.window_manager.invoke_confirm(self, event)

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
        return {'FINISHED'} if use_set(context, ob, ts, self.report) else {'CANCELLED'}


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
        if not use_set(context, ob, found[0], self.report):
            return {'CANCELLED'}
        context.window_manager.nf_lib.pick_id = found[0]['id']
        return {'FINISHED'}


class NF_OT_hand_mlm_refresh(_Op, Operator):
    bl_idname = 'nf.hand_mlm_refresh'
    bl_label = "Update Mixer Layers"
    bl_description = "The Material Layer Mixer copies the base material: push the slider changes above into it"

    def execute(self, context):
        st = mlm_stack(ctx_ob(context))
        if st is None or not mlm_refresh(st):
            self.report({'WARNING'}, "No Material Layer Mixer stack to update")
            return {'CANCELLED'}
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
           NF_OT_hand_material, NF_OT_hand_set_library, NF_OT_hand_set_file, NF_OT_hand_mlm_refresh,
           NF_OT_hand_apply_all, NF_OT_hand_exit)


def _cleanup_timer():
    try:
        cleanup_orphans()
    except (RuntimeError, ReferenceError):
        pass
    return None


_COUNT = {'objects': -1}
_KNOWN = {'uids': None}                                   # session ids of the objects seen so far


def _snapshot():
    _KNOWN['uids'] = {o.session_uid for o in bpy.data.objects}
    _COUNT['objects'] = len(bpy.data.objects)


@persistent
def _on_load(*_):
    _snapshot()
    _cleanup_timer()


@persistent
def _on_undo(*_):
    _snapshot()                                        # objects an undo brings back are not new copies


def _init_timer():
    try:
        if _KNOWN['uids'] is None:
            _snapshot()
    except (AttributeError, RuntimeError):
        return 0.5                                     # data not ready yet (add-on enabled at startup)
    return None


def _own_timer():
    """after a Shift+D / paste / append: a NEW Step by Step rock gets its own hidden clouds offset, textures and
    material right away, so a copy looks exactly like the original and then moves / changes on its own. Rocks that
    were already there are left alone (a material you linked to several rocks stays shared)"""
    try:
        known = _KNOWN['uids']
        if known is None:
            _snapshot()
            return None
        new = [o for o in bpy.data.objects if o.session_uid not in known]
        fresh = {o.session_uid for o in new}
        known.update(fresh)
        rocks = []
        for o in new:
            if o.type != 'MESH' or not o.users or o.get('nf_backup') or _linked(o):
                continue
            h = hand(o)
            if h is not None and (h.active or h.noise_object is not None):
                rocks.append(o)
        if not rocks:
            return None
        rocks.sort(key=lambda o: o.name, reverse=True)   # 'Rock.001' before 'Rock': the copy takes the copies
        before, textures = {}, {}                      # material -> objects that had it before; texture -> users
        for o in bpy.data.objects:
            if o.type != 'MESH' or not o.users or o.library is not None:
                continue
            for m in o.modifiers:
                t = getattr(m, 'texture', None)
                if t is not None:
                    textures.setdefault(t, []).append(o)
            if o.session_uid not in fresh and not o.get('nf_backup'):
                for sl in o.material_slots:
                    if sl.material is not None:
                        before.setdefault(sl.material, []).append(o)
        for o in rocks:
            ensure_own(o, keep_world=False, materials=before, textures=textures)
    except (RuntimeError, ReferenceError, AttributeError):
        pass
    return None


@persistent
def _on_depsgraph(scene, depsgraph):
    """objects deleted: remove orphaned offsets / copies; objects added (Shift+D): give copies their own data.
    Both from a timer, never inside the depsgraph update"""
    if _KNOWN['uids'] is None:
        _snapshot()
        return
    n = len(bpy.data.objects)
    if n < _COUNT['objects'] and not bpy.app.timers.is_registered(_cleanup_timer):
        bpy.app.timers.register(_cleanup_timer, first_interval=0.05)
    elif n > _COUNT['objects'] >= 0 and not bpy.app.timers.is_registered(_own_timer):
        bpy.app.timers.register(_own_timer, first_interval=0.05)
    _COUNT['objects'] = n


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.nf_hand = PointerProperty(type=NF_HandSettings)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    for hs in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_undo not in hs:
            hs.append(_on_undo)
    _KNOWN['uids'] = None
    try:
        _snapshot()
    except AttributeError:                             # enabled at startup: bpy.data is not there yet
        bpy.app.timers.register(_init_timer, first_interval=0.1)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    for fn in (_cleanup_timer, _own_timer, _init_timer):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for hs in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_undo in hs:
            hs.remove(_on_undo)
    del bpy.types.Object.nf_hand
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
