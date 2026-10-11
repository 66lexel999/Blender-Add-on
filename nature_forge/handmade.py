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
# Properties editor exactly like in a tutorial. Settings live on the object (Object.nf_hand).
import json
import math
import random
import bmesh
import numpy as np
import bpy
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


def faces_now(context, ob):
    try:
        n = len(ob.evaluated_get(context.evaluated_depsgraph_get()).data.polygons)
    except (RuntimeError, ReferenceError):
        n = 0
    if n == 0:                                      # sculpt mode with multires (4.2) reports no evaluated faces
        n = len(ob.data.polygons)
        for m in ob.modifiers:
            if m.type == 'MULTIRES' and m.show_viewport:
                n *= 4 ** m.sculpt_levels
            elif m.type == 'SUBSURF' and m.show_viewport:
                n *= 4 ** m.levels
    return n


def mark_done(ob, key):
    H = ob.nf_hand
    H.done = H.done | (1 << STEP_KEYS.index(key))


def is_done(ob, key):
    return bool(ob.nf_hand.done & (1 << STEP_KEYS.index(key)))


def object_mode(context):
    if context.mode != 'OBJECT' and context.view_layer.objects.active is not None:
        bpy.ops.object.mode_set(mode='OBJECT')


def apply_modifier(context, ob, name):
    if ob.modifiers.get(name) is None:
        return False
    with context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                               selected_editable_objects=[ob]):
        bpy.ops.object.modifier_apply(modifier=name)
    return True


def move_to_index(context, ob, name, index):
    if ob.modifiers.get(name) is None:
        return
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


def noise_empty(ob, create=False):
    e = ob.nf_hand.noise_object
    if e is None and create:
        e = bpy.data.objects.new(f"NF Clouds Offset ({ob.name})", None)
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
    return tex


def sync_clouds(ob):
    """seed / size / stretch / detail / pattern -> the clouds texture and its hidden offset empty"""
    H = ob.nf_hand
    tex = clouds_texture(ob)
    if tex is not None:
        tex.noise_scale = 1.0
        tex.noise_depth = H.cloud_detail
        tex.noise_basis = H.cloud_basis
        tex.noise_type = 'HARD_NOISE' if H.cloud_hard else 'SOFT_NOISE'
        tex.intensity = 1.0
        tex.contrast = H.cloud_contrast
    e = noise_empty(ob)
    if e is not None:
        r = random.Random(H.seed * 7919 + 13)
        e.location = (r.uniform(-60, 60), r.uniform(-60, 60), r.uniform(-60, 60))
        e.rotation_euler = (r.uniform(0, math.tau), r.uniform(0, math.tau), r.uniform(0, math.tau)) \
            if H.cloud_twist else (0.0, 0.0, 0.0)
        s = max(H.cloud_size, 1e-3)
        e.scale = (s, s, s * max(H.cloud_stretch, 0.05))
    ob.update_tag()


def upd_clouds(self, context):
    sync_clouds(self.id_data)


def upd_uv_size(self, context):
    """live texture size: scale the cube projected UVs (the rock detail + material follow)"""
    ob = self.id_data
    old = self.get('uv_applied', 0.0)
    if not old or ob.mode == 'EDIT':
        return
    me = ob.data
    uvl = me.uv_layers.get('UVMap') or me.uv_layers.active
    if uvl is None or abs(old - self.uv_size) < 1e-9:
        return
    uv = np.empty(len(me.loops) * 2, np.float32)
    uvl.data.foreach_get('uv', uv)
    uvl.data.foreach_set('uv', uv * (old / max(self.uv_size, 1e-4)))
    self['uv_applied'] = self.uv_size
    me.update()
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
                              description="Rotate the pattern with the seed (no grid-aligned look)")
    chunk_size: FloatProperty(name="Chunk Size", default=0.45, min=0.05, max=1.5, subtype='FACTOR',
                              description="Size of added chunks (relative to the object)")
    chunk_shape: EnumProperty(name="Chunk", items=[('BOX', "Box", "Angular block", 'MESH_CUBE', 0),
                                                   ('ROUND', "Round", "Rounded lump", 'MESH_ICOSPHERE', 1)],
                              default='BOX')
    chunk_seed: IntProperty(default=0)
    sculpt_levels: IntProperty(name="Sculpt Resolution", default=2, min=1, max=6,
                               description="Multires subdivisions to sculpt on (each level = 4x faces)")
    keep_level: IntProperty(name="Keep Level", default=1, min=0, max=6,
                            description="Detail level baked into the mesh when applying the sculpt (the "
                                        "rock-detail step subdivides again, so 1 is usually plenty)")
    uv_size: FloatProperty(name="Texture Size", default=2.0, min=0.01, soft_max=50.0, unit='LENGTH',
                           update=upd_uv_size, description="Real-world size of one texture repeat (cube projection)")
    set_json: StringProperty()


# ---------------------------------------------------------------------------------------------- step actions
def start(context, kind):
    object_mode(context)
    if kind == 'SELECTED':
        ob = ctx_ob(context)
        if ob is None:
            return None
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


def blockout(context, ob):
    object_mode(context)
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
        dp.texture = clouds_texture(ob, create=True)
        dp.direction = 'NORMAL'
        dp.strength = round(avg_dim(ob) * 0.18, 3)
        dp.mid_level = 0.5
    dp.texture_coords = 'OBJECT'
    dp.texture_coords_object = noise_empty(ob, create=True)
    if dp.texture is None:
        dp.texture = clouds_texture(ob, create=True)
    for m in (rm, dp):
        m.show_in_editmode = True
    sync_clouds(ob)
    mark_done(ob, 'BLOCKOUT')


def add_chunk(context, ob):
    """a box / lump fused into the mesh where the remesh will weld it to the rock"""
    H = ob.nf_hand
    H.chunk_seed += 1
    r = random.Random(H.seed * 31 + H.chunk_seed * 1009)
    edit = ob.mode == 'EDIT'
    if edit:
        bm = bmesh.from_edit_mesh(ob.data)
    else:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    if not len(bm.verts):
        return False
    a = avg_dim(ob)
    v = bm.verts[r.randrange(len(bm.verts))]
    n = v.normal if v.normal.length > 0.1 else Vector((0.0, 0.0, 1.0))
    size = H.chunk_size * a * r.uniform(0.6, 1.0)
    sx, sy, sz = (size * r.uniform(0.6, 1.3) for _ in range(3))
    center = v.co + n * size * 0.15
    rot = Euler((r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6), r.uniform(0, math.tau))).to_matrix().to_4x4()
    mat = Matrix.Translation(center) @ rot @ Matrix.Diagonal((sx, sy, sz, 1.0))
    for e in bm.verts:
        e.select = False
    for f in bm.faces:
        f.select = False
    if H.chunk_shape == 'BOX':
        res = bmesh.ops.create_cube(bm, size=1.0, matrix=mat)
    else:
        res = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.5, matrix=mat)
    for e in res['verts']:
        e.select = True
    bm.select_flush(True)
    if edit:
        bmesh.update_edit_mesh(ob.data)
    else:
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.update()
    return True


def convert(context, ob, keep_copy):
    object_mode(context)
    e = ob.nf_hand.noise_object
    tex = clouds_texture(ob)
    if keep_copy:
        cp = ob.copy()
        cp.data = ob.data.copy()
        cp.name = ob.name + "_Blockout"
        coll = bpy.data.collections.get("NF Editable Copies")
        if coll is None:
            coll = bpy.data.collections.new("NF Editable Copies")
            context.scene.collection.children.link(coll)
        coll.objects.link(cp)
        cp.nf_hand.active = False
        cp.hide_render = True
        if e is not None:                                  # the copy keeps a live clouds offset of its own
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
    for name in (M_REMESH, M_CLOUDS):
        apply_modifier(context, ob, name)
    if e is not None:
        ob.nf_hand.noise_object = None
        bpy.data.objects.remove(e)
    if tex is not None and tex.users == 0:
        bpy.data.textures.remove(tex)
    for p in ob.data.polygons:
        p.use_smooth = True
    mark_done(ob, 'SHAPE')


def sculpt_start(context, ob):
    object_mode(context)
    mr = ob.modifiers.get(M_MULTIRES) or next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)
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
        ob.nf_hand['keep_level'] = min(ob.nf_hand.keep_level, mr.total_levels)
    mr.levels = mr.sculpt_levels = mr.render_levels = mr.total_levels
    context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='SCULPT')
    return mr


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
    mr = ob.modifiers.get(M_MULTIRES) or next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)
    if mr is not None:
        mr.levels = min(ob.nf_hand.keep_level, mr.total_levels)
        apply_modifier(context, ob, mr.name)
    mark_done(ob, 'SCULPT')


def unwrap(context, ob):
    object_mode(context)
    me = ob.data
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
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.cube_project(cube_size=ob.nf_hand.uv_size, correct_aspect=True, clip_to_bounds=False,
                                scale_to_bounds=False)
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
    object_mode(context)
    ts = texture_set(ob)
    if ts is None:
        return False
    if ob.data.uv_layers.get('UVMap') is None:
        unwrap(context, ob)
        if report:
            report({'INFO'}, "Unwrapped first (cube projection)")
    img, src = detail_image(ts)
    if img is None:
        return False
    sub = ob.modifiers.get(M_SUBDIV)
    if sub is None:
        sub = ob.modifiers.new(M_SUBDIV, 'SUBSURF')
        sub.subdivision_type = 'CATMULL_CLARK'
        n = max(len(ob.data.polygons), 1)
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
    return True


def material(context, ob):
    ts = texture_set(ob)
    if ts is None:
        return None
    mat = shading.asset_material(ts, mark_asset=False)
    if hasattr(mat, 'displacement_method'):
        mat.displacement_method = 'BUMP'                 # the modifier already displaced: shader adds bump only
    elif hasattr(mat, 'cycles'):
        try:
            mat.cycles.displacement_method = 'BUMP'
        except (AttributeError, TypeError):
            pass
    me = ob.data
    if len(me.materials):
        for i in range(len(me.materials)):
            me.materials[i] = mat
    else:
        me.materials.append(mat)
    mark_done(ob, 'MATERIAL')
    return mat


def apply_all(context, ob):
    object_mode(context)
    for m in list(ob.modifiers):
        if m.show_viewport:
            apply_modifier(context, ob, m.name)
    mark_done(ob, 'FINISH')


# ---------------------------------------------------------------------------------------------- operators
class _Op:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return in_flow(ctx_ob(context))


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

    def execute(self, context):
        if self.kind == 'SELECTED' and ctx_ob(context) is None:
            self.report({'ERROR'}, "Select a mesh first")
            return {'CANCELLED'}
        ob = start(context, self.kind)
        if ob is None:
            return {'CANCELLED'}
        self.report({'INFO'}, "Step 1: press 'Add Remesh + Clouds'")
        return {'FINISHED'}


class NF_OT_hand_goto(_Op, Operator):
    bl_idname = 'nf.hand_goto'
    bl_label = "Go to Step"
    bl_description = "Show this step"

    step: IntProperty()

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
        blockout(context, ob)
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
    bl_description = ("Add a random block / lump to the mesh (like duplicating parts in Edit Mode); the remesh welds "
                      "it to the rock. In Edit Mode it stays selected so you can G / S / R it right away")

    def execute(self, context):
        ob = ctx_ob(context)
        if not add_chunk(context, ob):
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
                       description="Keep a hidden copy with the live remesh / clouds in 'NF Editable Copies'")

    def invoke(self, context, event):
        from .props import prefs
        p = prefs(context)
        self.keep = p.keep_editable if p else True
        return self.execute(context)

    def execute(self, context):
        ob = ctx_ob(context)
        convert(context, ob, self.keep)
        ob.nf_hand.step = STEP_KEYS.index('SCULPT')
        self.report({'INFO'}, f"{ob.name}: {len(ob.data.polygons):,} faces")
        return {'FINISHED'}


class NF_OT_hand_sculpt(_Op, Operator):
    bl_idname = 'nf.hand_sculpt'
    bl_label = "Start Sculpting"
    bl_description = "Add a Multires modifier (subdivided for sculpting) and switch to Sculpt Mode"

    def execute(self, context):
        ob = ctx_ob(context)
        mr = sculpt_start(context, ob)
        try:
            set_brush(context, 'SCRAPE')
        except (RuntimeError, AttributeError):
            pass
        self.report({'INFO'}, f"Sculpting on {faces_now(context, ob):,} faces ({mr.total_levels} levels)")
        return {'FINISHED'}


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
        sculpt_apply(context, ob)
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
        unwrap(context, ob)
        return {'FINISHED'}


class NF_OT_hand_detail(_Op, Operator):
    bl_idname = 'nf.hand_detail'
    bl_label = "Add Rock Detail"
    bl_description = ("Subdivision + Displace with the texture set's displacement map (UV): the rocky detail of the "
                      "texture becomes real geometry")

    def execute(self, context):
        ob = ctx_ob(context)
        if texture_set(ob) is None:
            self.report({'ERROR'}, "Choose a texture set first (Library or From Disk)")
            return {'CANCELLED'}
        if not detail(context, ob, self.report):
            self.report({'ERROR'}, "The set has no height / colour map")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{ob.name}: {faces_now(context, ob):,} faces in the viewport")
        return {'FINISHED'}


class NF_OT_hand_material(_Op, Operator):
    bl_idname = 'nf.hand_material'
    bl_label = "Add All Textures"
    bl_description = ("Material from the same texture set: colour (x AO), roughness, metal, normal map and a bump "
                      "from the height map, all on the cube projected UVs")

    def execute(self, context):
        ob = ctx_ob(context)
        if texture_set(ob) is None:
            self.report({'ERROR'}, "Choose a texture set first (Library or From Disk)")
            return {'CANCELLED'}
        mat = material(context, ob)
        self.report({'INFO'}, f"{mat.name}: {library.describe(texture_set(ob))}")
        return {'FINISHED'}


def _refresh_set(context, ob, report):
    if is_done(ob, 'DETAIL') and ob.modifiers.get(M_DETAIL) is not None:
        detail(context, ob)
    if is_done(ob, 'MATERIAL'):
        material(context, ob)
    ts = texture_set(ob)
    report({'INFO'}, f"{ts['name']}: {library.describe(ts)}")


class NF_OT_hand_set_library(_Op, Operator):
    bl_idname = 'nf.hand_set_library'
    bl_label = "Use Library Pick"
    bl_description = "Use the texture set picked in the Texture Library for the rock detail and the material"

    def execute(self, context):
        ob = ctx_ob(context)
        st = context.window_manager.nf_lib
        sid = st.pick_id or (st.pick if st.pick != 'NONE' else '')
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
        apply_all(context, ob)
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
           NF_OT_hand_chunk, NF_OT_hand_edit, NF_OT_hand_convert, NF_OT_hand_sculpt, NF_OT_hand_brush,
           NF_OT_hand_sculpt_apply, NF_OT_hand_unwrap, NF_OT_hand_detail, NF_OT_hand_material,
           NF_OT_hand_set_library, NF_OT_hand_set_file, NF_OT_hand_apply_all, NF_OT_hand_exit)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.nf_hand = PointerProperty(type=NF_HandSettings)


def unregister():
    del bpy.types.Object.nf_hand
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
