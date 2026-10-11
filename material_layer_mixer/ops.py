# Material Layer Mixer - operators: stack / layers / masks / painting / UVs / asset browser
import math
import os
import uuid
import numpy as np
import bpy
from bpy.props import StringProperty, EnumProperty, IntProperty, BoolProperty, FloatProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement
from . import nodes, texsets, presets
from .props import MASK_TYPES

MASK_LABEL = {t[0]: t[1] for t in MASK_TYPES}


def new_uid():
    return uuid.uuid4().hex[:8]


def ctx_object(context):
    ob = context.object or context.view_layer.objects.active
    return ob if ob is not None and ob.type == 'MESH' else None


def ctx_stack(context):
    """-> (object, material, stack) ; stack is None when the active material is not layered"""
    ob = ctx_object(context)
    mat = ob.active_material if ob else None
    st = mat.mlm if mat is not None and mat.mlm.is_stack else None
    return ob, mat, st


def active_layer(st):
    if st is None or not len(st.layers):
        return None
    return st.layers[min(max(st.active, 0), len(st.layers) - 1)]


def active_mask(L):
    if L is None or not len(L.masks):
        return None
    return L.masks[min(max(L.active_mask, 0), len(L.masks) - 1)]


def paint_mask_of(L):
    """the active mask if it can be painted, else the first paintable one"""
    M = active_mask(L)
    if M is not None and M.type in ('PAINT', 'VERTEX'):
        return M
    return next((m for m in L.masks if m.type in ('PAINT', 'VERTEX')), None) if L is not None else None


def first_paintable(st):
    """index of the top-most (visible first) layer with a paint / vertex mask (-1: none; the base is never painted)"""
    idx = [i for i in range(len(st.layers) - 1, 0, -1) if paint_mask_of(st.layers[i]) is not None]
    return next((i for i in idx if st.layers[i].enabled), idx[0] if idx else -1)


def paint_target(context):
    """-> (image, color attribute name) the brush paints into right now (None where not applicable)"""
    ob = ctx_object(context)
    if context.mode == 'PAINT_VERTEX':
        a = ob.data.color_attributes.active_color if ob is not None else None
        return None, (a.name if a is not None else None)
    ip = context.tool_settings.image_paint
    if ip.mode == 'IMAGE':
        return ip.canvas, None
    mat = ob.active_material if ob is not None else None
    imgs = list(getattr(mat, 'texture_paint_images', ())) if mat is not None else []
    i = getattr(mat, 'paint_active_slot', 0) if mat is not None else 0
    return (imgs[i] if 0 <= i < len(imgs) else None), None


def painting_mask(context, st):
    """the (layer, mask) the brush paints into, or None when it would paint something else (a source texture!)"""
    img, attr = paint_target(context)
    for L in list(st.layers)[1:]:
        for M in L.masks:
            if (M.type == 'PAINT' and img is not None and M.image == img) or \
                    (M.type == 'VERTEX' and attr and M.attr == attr):
                return L, M
    return None


_tri_cache = {}


def tri_count_cached(ob):
    me = ob.data
    key = (me.name, len(me.polygons), len(me.loops))
    n = _tri_cache.get(key)
    if n is None:
        lt = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get('loop_total', lt)
        n = _tri_cache[key] = int((lt - 2).sum()) if len(lt) else 0
        if len(_tri_cache) > 256:
            _tri_cache.clear()
    return n


def render_uv_name(me):
    for u in me.uv_layers:
        if u.active_render:
            return u.name
    return me.uv_layers[0].name if len(me.uv_layers) else ''


def copy_pg(src, dst, skip=('rna_type', 'uid')):
    for p in src.bl_rna.properties:
        k = p.identifier
        if k in skip or p.type == 'COLLECTION':
            continue
        if p.is_readonly:
            continue
        try:
            setattr(dst, k, getattr(src, k))
        except (AttributeError, TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------------------------- UVs
def is_flat(ob):
    """height-field like (terrain): wide, low and almost no steep faces (a top-down UV would smear those)"""
    d = ob.dimensions
    if not (max(d.x, d.y) > 0 and d.z < 0.4 * max(d.x, d.y)):
        return False
    me = ob.data
    n = len(me.polygons)
    if not n:
        return False
    nrm = np.empty(n * 3, np.float32)
    me.polygons.foreach_get('normal', nrm)
    ar = np.empty(n, np.float32)
    me.polygons.foreach_get('area', ar)
    steep = ar[np.abs(nrm.reshape(-1, 3)[:, 2]) < 0.4].sum() / max(float(ar.sum()), 1e-9)
    return steep < 0.02


def _uv_vectors(uv):
    return uv.uv if hasattr(uv, 'uv') else uv.data


def _uv_array(uvl):
    a = np.empty(len(uvl.id_data.loops) * 2, np.float32)
    _uv_vectors(uvl).foreach_get('vector' if hasattr(uvl, 'uv') else 'uv', a)
    return a.reshape(-1, 2)


def uv_coverage(me, name):
    """share of the 0-1 UV square covered by faces: how much of a baked / painted texture is really used"""
    uvl = me.uv_layers.get(name)
    if uvl is None or not len(me.polygons):
        return 0.0
    uv = _uv_array(uvl)
    n = len(me.polygons)
    ls = np.empty(n, np.int32)
    lt = np.empty(n, np.int32)
    me.polygons.foreach_get('loop_start', ls)
    me.polygons.foreach_get('loop_total', lt)
    pol = np.repeat(np.arange(n), lt)
    idx = np.arange(len(uv))
    nxt = np.where(idx == (ls + lt - 1)[pol], ls[pol], idx + 1)
    cr = uv[idx, 0] * uv[nxt, 1] - uv[nxt, 0] * uv[idx, 1]
    return float(np.abs(0.5 * np.bincount(pol, cr, minlength=n)).sum())


_cov_cache = {}


def uv_coverage_cached(me, name):
    key = (me.name, name, len(me.loops), me.uv_layers.get(name) is not None)
    v = _cov_cache.get(key)
    if v is None:
        v = _cov_cache[key] = uv_coverage(me, name)
        if len(_cov_cache) > 128:
            _cov_cache.clear()
    return v


def uv_island_count(me, name):
    uvl = me.uv_layers.get(name)
    if uvl is None:
        return 0
    uv = np.round(_uv_array(uvl), 5)
    vi = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', vi)
    n = len(me.polygons)
    lt = np.empty(n, np.int32)
    me.polygons.foreach_get('loop_total', lt)
    pol = np.repeat(np.arange(n), lt)
    # corners sharing vertex + uv are connected: union faces through those keys
    key = np.stack([vi.astype(np.float64), uv[:, 0], uv[:, 1]], 1)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    parent = np.arange(n)

    def find(a):
        r = a
        while parent[r] != r:
            r = parent[r]
        while parent[a] != r:
            parent[a], a = r, parent[a]
        return r
    first = {}
    for f, k in zip(pol.tolist(), inv.ravel().tolist()):
        g = first.setdefault(k, f)
        if g != f:
            a, b = find(f), find(g)
            if a != b:
                parent[a] = b
    return len({find(f) for f in range(n)})


DENSE_FACES = 3000


def _smoothed_coords(me):
    """vertex positions with the small bumps smoothed away (Laplacian): the unwrap follows the overall shape,
    not every displaced stone / bark ridge"""
    nv = len(me.vertices)
    co = np.empty(nv * 3, np.float64)
    me.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    e = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get('vertices', e)
    e = e.reshape(-1, 2)
    deg = np.bincount(e.ravel(), minlength=nv).astype(np.float64)
    deg[deg == 0] = 1.0
    iters = int(min(300, max(40, 3e7 / max(nv, 1))))
    for _ in range(iters):
        s = np.empty_like(co)
        for k in range(3):
            s[:, k] = np.bincount(e[:, 0], co[e[:, 1], k], nv) + np.bincount(e[:, 1], co[e[:, 0], k], nv)
        co += 0.5 * (s / deg[:, None] - co)
    return co


def _unwrap_copy(context, ob, coords=None, src_uv=None, margin=0.004):
    """unwrap a temporary copy of the mesh (optionally smoothed, or starting from an existing UV map that is only
    re-packed) -> (uv array per loop, island count, coverage). The user's object is never put in edit mode."""
    pm = ob.data.copy()
    for u in list(pm.uv_layers):
        if u.name != src_uv:
            pm.uv_layers.remove(u)
    t = pm.uv_layers.get(src_uv) if src_uv else None
    if t is None:
        t = pm.uv_layers.new(name='MLM_tmp_unwrap')
    tname = t.name                                      # layer references die when edit mode rebuilds the mesh
    if coords is not None:
        pm.vertices.foreach_set('co', coords.astype(np.float32).ravel())
        pm.update()
    po = bpy.data.objects.new('MLM_tmp_unwrap', pm)
    context.scene.collection.objects.link(po)
    vl = context.view_layer
    prev_act = vl.objects.active
    prev_sel = [o for o in context.selected_objects]
    try:
        for o in prev_sel:
            o.select_set(False)
        po.select_set(True)
        vl.objects.active = po
        pm.uv_layers.active = t
        # explicit context: only the temporary copy is ever edited, whatever the caller's context says
        ovr = dict(object=po, active_object=po, selected_objects=[po], selected_editable_objects=[po])
        with context.temp_override(**ovr):
            bpy.ops.object.mode_set(mode='EDIT')
        with context.temp_override(**ovr, edit_object=po):
            bpy.ops.mesh.reveal(select=False)
            bpy.ops.mesh.select_all(action='SELECT')
            if src_uv is None:
                bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=margin, area_weight=0.0,
                                         correct_aspect=True, scale_to_bounds=False)
            bpy.ops.uv.select_all(action='SELECT')
            try:
                bpy.ops.uv.pack_islands(rotate=True, margin=margin, shape_method='CONCAVE')
            except TypeError:
                bpy.ops.uv.pack_islands(rotate=True, margin=margin)
            bpy.ops.object.mode_set(mode='OBJECT')
        res = (_uv_array(pm.uv_layers[tname]).copy(), uv_island_count(pm, tname), uv_coverage(pm, tname))
    finally:
        if po.mode != 'OBJECT':
            with context.temp_override(object=po, active_object=po, edit_object=po):
                bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(po)
        bpy.data.meshes.remove(pm)
        for o in prev_sel:
            if o.name in vl.objects:
                o.select_set(True)
        vl.objects.active = prev_act
    return res


def best_unwrap(context, ob):
    """dense / displaced meshes: unwrap of the smoothed shape vs. re-packed render UV, the better one wins
    (more of the texture used, fewer seams)"""
    me = ob.data
    cands = []
    if len(me.polygons) > DENSE_FACES:
        cands.append(('smart (smoothed shape)',) + _unwrap_copy(context, ob, coords=_smoothed_coords(me)))
    else:
        cands.append(('smart',) + _unwrap_copy(context, ob))
    ren = render_uv_name(me)
    if ren and not ren.startswith('MLM_') and uv_island_count(me, ren) <= 200:
        cands.append(('your UV map, re-packed',) + _unwrap_copy(context, ob, src_uv=ren))
    best = max(cands, key=lambda c: c[3] * (1.0 - min(c[2], 400) / 1000.0))
    return best


def make_paint_uv(context, ob, name, method='AUTO'):
    """create (or redo) a non-overlapping UV map for painting masks and baking. Keeps the render UV as it was."""
    me = ob.data
    prev_render = render_uv_name(me)
    prev_active = me.uv_layers.active.name if me.uv_layers.active else None
    uv = me.uv_layers.get(name)
    if uv is None:
        uv = me.uv_layers.new(name=name, do_init=False)
        if uv is None:
            raise RuntimeError("Could not add a UV map (a mesh can have at most 8)")
    if method == 'AUTO':
        method = 'TOP' if is_flat(ob) else 'SMART'
    nloops = len(me.loops)
    if method in ('SMART', 'REPACK'):
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if method == 'REPACK' and prev_render and prev_render != name:
            label, arr, isl, cov = ('your UV map, re-packed',) + _unwrap_copy(context, ob, src_uv=prev_render)
        else:
            label, arr, isl, cov = best_unwrap(context, ob)
        uv = me.uv_layers[name]
        _uv_vectors(uv).foreach_set('vector' if hasattr(uv, 'uv') else 'uv', arr.ravel())
        method = f"{label}: {isl} islands, {cov:.0%} of the texture"
        for u in me.uv_layers:
            if prev_render:
                u.active_render = (u.name == prev_render)
        if prev_active and me.uv_layers.get(prev_active):
            me.uv_layers.active = me.uv_layers[prev_active]
        me.update()
        return method
    if method == 'TOP':
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        vi = np.empty(nloops, np.int32)
        me.loops.foreach_get('vertex_index', vi)
        mn = co[:, :2].min(0)
        span = float((co[:, :2].max(0) - mn).max()) or 1.0
        uvs = (co[vi, :2] - mn) / span
        uvs = uvs * 0.99 + 0.005
        _uv_vectors(uv).foreach_set('vector' if hasattr(uv, 'uv') else 'uv', uvs.astype(np.float32).ravel())
    elif method == 'COPY':
        src = me.uv_layers.get(prev_render)
        if src is not None and src != uv:
            a = np.empty(nloops * 2, np.float32)
            key = 'vector' if hasattr(src, 'uv') else 'uv'
            _uv_vectors(src).foreach_get(key, a)
            _uv_vectors(uv).foreach_set(key, a)
    else:
        vl = context.view_layer
        prev_act = vl.objects.active
        prev_sel = [o for o in context.selected_objects]
        prev_mode = ob.mode
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in prev_sel:
            o.select_set(False)
        ob.select_set(True)
        vl.objects.active = ob
        me.uv_layers.active = uv
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.reveal(select=False)
        bpy.ops.mesh.select_all(action='SELECT')
        if method == 'LIGHTMAP':
            bpy.ops.uv.lightmap_pack(PREF_CONTEXT='ALL_FACES', PREF_PACK_IN_ONE=True, PREF_NEW_UVLAYER=False,
                                     PREF_BOX_DIV=12, PREF_MARGIN_DIV=0.3)
        else:
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.01, area_weight=0.0,
                                     correct_aspect=True, scale_to_bounds=False)
        bpy.ops.object.mode_set(mode='OBJECT')
        ob.select_set(False)
        for o in prev_sel:
            o.select_set(True)
        vl.objects.active = prev_act
        if prev_mode != 'OBJECT' and prev_act == ob:
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except RuntimeError:
                pass
    for u in me.uv_layers:
        if prev_render:
            u.active_render = (u.name == prev_render)
    if prev_active and me.uv_layers.get(prev_active):
        me.uv_layers.active = me.uv_layers[prev_active]
    me.update()
    return method


def ensure_paint_uv(context, ob, name):
    if ob.data.uv_layers.get(name) is None:
        return make_paint_uv(context, ob, name, 'AUTO')
    return None


# ---------------------------------------------------------------------------------------------- stack helpers
def create_stack(ob, base_mat=None, name=None):
    mat = bpy.data.materials.new(name or ((base_mat.name if base_mat else ob.name) + '_Layered'))
    with nodes.suspend():
        st = mat.mlm
        st.is_stack = True
        L = st.layers.add()
        L.uid = new_uid()
        if base_mat is not None:
            L.source_type = 'MATERIAL'
            L.material = base_mat
            L.name = base_mat.name
        else:
            L.source_type = 'FILL'
            L.name = 'Base'
            L.fill_color = (0.55, 0.55, 0.55, 1.0)
        st.uv_map = render_uv_name(ob.data)
        st.active = 0
    nodes.rebuild(mat)
    return mat


def add_layer(mat, name, source='FILL'):
    st = mat.mlm
    with nodes.suspend():
        L = st.layers.add()
        L.uid = new_uid()
        L.name = name
        L.source_type = source
        if source == 'FILL':
            L.use_normal = False
            L.use_height = False
            L.use_emission = False
        idx = len(st.layers) - 1
        target = min(st.active + 1, idx) if len(st.layers) > 1 else 0
        if target != idx:
            st.layers.move(idx, target)
        st.active = target
    return st.layers[target]


def add_mask(ob, L, mtype, **params):
    with nodes.suspend():
        M = L.masks.add()
        M.uid = new_uid()
        M.type = mtype
        M.name = MASK_LABEL.get(mtype, 'Mask')
        first = len(L.masks) == 1
        M.op = 'MIX' if first else {'PAINT': 'ADD', 'VERTEX': 'ADD', 'NOISE': 'OVERLAY'}.get(mtype, 'MULTIPLY')
        if mtype == 'EDGES':
            M.distance, M.low, M.high = 0.04, 0.05, 0.3
        elif mtype == 'CAVITY':
            M.distance, M.low, M.high = 0.2, 0.05, 0.5
        elif mtype == 'HEIGHT':
            M.low, M.high, M.invert = 0.42, 0.58, True
        elif mtype == 'NOISE' and not first:
            M.low, M.high = 0.3, 0.7
        elif mtype == 'NOISE':
            M.low, M.high = 0.35, 0.65
        elif mtype == 'POSITION' and ob is not None:
            M.pos_to = round(ob.matrix_world.translation.z + ob.dimensions.z * 0.25, 2)
        for k, v in params.items():
            setattr(M, k, v)
        if mtype == 'VERTEX' and ob is not None and not M.attr:
            M.attr = 'MLM_' + M.uid
            ensure_color_attr(ob.data, M.attr)
        L.active_mask = len(L.masks) - 1
    return M


def ensure_color_attr(me, name, value=0.0):
    a = me.color_attributes.get(name)
    if a is None:
        a = me.color_attributes.new(name=name, type='BYTE_COLOR', domain='CORNER')
        col = np.tile(np.array([value, value, value, 1.0], np.float32), len(a.data))
        a.data.foreach_set('color', col)
    return a


def apply_preset(ob, mat, key):
    p = presets.SMART[key]
    L = add_layer(mat, p['label'], 'FILL')
    with nodes.suspend():
        L.fill_color = p['fill']
        L.fill_rough = p.get('rough', 0.6)
        L.fill_metal = p.get('metal', 0.0)
        L.fill_height = p.get('height', 0.5)
        L.blend_mode = p.get('blend', 'MIX')
        L.opacity = p.get('opacity', 1.0)
        L.height_mode = p.get('height_mode', 'REPLACE')
        L.normal_mode = p.get('normal_mode', 'REPLACE')
        ch = set(p.get('channels', ('color', 'roughness')))
        L.use_color = 'color' in ch
        L.use_metallic = 'metallic' in ch
        L.use_roughness = 'roughness' in ch
        L.use_normal = 'normal' in ch
        L.use_height = 'height' in ch
        L.use_emission = 'emission' in ch
        for m in p['masks']:
            m = dict(m)
            add_mask(ob, L, m.pop('type'), **m)
        L.active_mask = 0
    return L


def finish(context, mat, force=False):
    nodes.rebuild(mat, force)
    from . import paint_guard                    # layer added / removed / moved while painting: follow the selection
    paint_guard.retarget(context)
    for a in context.screen.areas if context.screen else ():
        a.tag_redraw()


# ---------------------------------------------------------------------------------------------- stack operators
class MLM_OT_stack_create(Operator):
    bl_idname = 'mlm.stack_create'
    bl_label = "Make Layered Material"
    bl_description = ("Turn the active material into a layered material (the original stays untouched as the "
                      "base layer). Every selected mesh gets its own layered material")
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(items=[('CONVERT', "Convert", "Use the current material as base layer"),
                              ('NEW', "New", "Start from a plain grey base")], default='CONVERT')

    @classmethod
    def poll(cls, context):
        return ctx_object(context) is not None

    def execute(self, context):
        act = ctx_object(context)
        obs = [act] + [o for o in context.selected_objects if o.type == 'MESH' and o != act]
        done = 0
        for ob in obs:
            orig = ob.active_material if self.mode == 'CONVERT' else None
            if orig is not None and orig.mlm.is_stack:
                continue
            mat = create_stack(ob, orig, None if orig else ob.name + '_Layered')
            if len(ob.material_slots):
                ob.material_slots[ob.active_material_index].material = mat
            else:
                ob.data.materials.append(mat)
            done += 1
        if not done:
            self.report({'INFO'}, "Nothing to convert (already layered)")
            return {'CANCELLED'}
        sp = context.space_data
        if sp is not None and sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
            sp.shading.type = 'MATERIAL'
        self.report({'INFO'}, f"Layered material ready on {done} object(s) - add layers in Sidebar > Mixer")
        return {'FINISHED'}


class MLM_OT_layer_add(Operator):
    bl_idname = 'mlm.layer_add'
    bl_label = "Add Layer"
    bl_description = "Add a layer above the active one"
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(items=[('FILL', "Fill", ""), ('MATERIAL', "Material", ""), ('TEXTURE', "Texture Set", "")],
                         default='FILL')
    mask: EnumProperty(name="Start with", items=[
        ('PAINT', "Paint Mask", "Hidden until you paint it in"),
        ('NONE', "No Mask", "Covers everything")], default='PAINT')

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        L = add_layer(mat, {'FILL': 'Fill', 'MATERIAL': 'Material', 'TEXTURE': 'Texture Set'}[self.source],
                      self.source)
        if self.mask == 'PAINT':
            add_mask(ob, L, 'PAINT')
        finish(context, mat)
        return {'FINISHED'}


_mat_items = []


def _material_items(self, context):
    _mat_items.clear()
    for i, m in enumerate(sorted((m for m in bpy.data.materials if not m.mlm.is_stack and not m.is_grease_pencil),
                                 key=lambda m: m.name.lower())):
        _mat_items.append((m.name, m.name, "", m.preview.icon_id if m.preview else 0, i))
    if not _mat_items:
        _mat_items.append(('', "(no materials in this file)", "", 0, 0))
    return _mat_items


class MLM_OT_layer_add_material(Operator):
    bl_idname = 'mlm.layer_add_material'
    bl_label = "Add Material Layer"
    bl_description = ("Add any material of this file as a layer (tip: drag materials from the Asset Browser onto "
                      "an object first, or use right-click > Add as Mixer Layer in the Asset Browser)")
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = 'material'

    material: EnumProperty(name="Material", items=_material_items)
    mask: EnumProperty(name="Start with", items=[('PAINT', "Paint Mask", ""), ('NONE', "No Mask", "")],
                       default='PAINT')
    replace: BoolProperty(name="Replace Active Layer Source", default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        src = bpy.data.materials.get(self.material)
        if src is None:
            return {'CANCELLED'}
        if self.replace and active_layer(st) is not None:
            L = active_layer(st)
            with nodes.suspend():
                L.source_type = 'MATERIAL'
                L.material = src
                L.name = src.name
        else:
            L = add_layer(mat, src.name, 'MATERIAL')
            with nodes.suspend():
                L.material = src
            if self.mask == 'PAINT':
                add_mask(ob, L, 'PAINT')
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_layer_add_texture_set(Operator):
    bl_idname = 'mlm.layer_add_texture_set'
    bl_label = "Add Texture Set"
    bl_description = ("Pick one map of each texture set (e.g. sand_basecolor.png, grass.png): the normal / roughness /"
                      " metal / AO / height maps next to it are found automatically. Several picks = several layers")
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    files: CollectionProperty(type=OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    replace: BoolProperty(name="Replace Active Layer Source", default=False, options={'SKIP_SAVE'})
    projection: EnumProperty(name="Projection", items=[('UV', "UV", ""), ('BOX', "Triplanar", "")], default='UV')
    mask: EnumProperty(name="Start with", items=[('PAINT', "Paint Mask", "Hidden until you paint it in"),
                                                 ('NONE', "No Mask", "Covers everything")], default='PAINT')

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        folder = bpy.path.abspath(self.directory)
        picked, allsets = texsets.sets_for_files(folder, [f.name for f in self.files])
        if not picked:
            if allsets:
                picked = sorted(allsets.items())[:12]
                if len(allsets) > 12:
                    self.report({'WARNING'}, f"{len(allsets)} texture sets in that folder - added the first 12")
            else:
                self.report({'ERROR'}, "No texture set found (need a color or normal map)")
                return {'CANCELLED'}
        for n, (key, s) in enumerate(picked):
            if self.replace and n == 0 and active_layer(st) is not None:
                L = active_layer(st)
                with nodes.suspend():
                    L.source_type = 'TEXTURE'
                    texsets.apply_set(L, s)
                    L.name = s.get('name') or key
                continue
            L = add_layer(mat, s.get('name') or key, 'TEXTURE')
            with nodes.suspend():
                L.projection = self.projection
                texsets.apply_set(L, s)
            if self.mask == 'PAINT' and len(st.layers) > 1 and st.active > 0:
                add_mask(ob, L, 'PAINT')
        finish(context, mat)
        found = ', '.join(f"{s.get('name') or k} ({'/'.join(c for c in s if c not in ('normal_dx', 'name'))})"
                          for k, s in picked)
        self.report({'INFO'}, "Texture sets: " + found)
        return {'FINISHED'}


class MLM_OT_layer_add_smart(Operator):
    bl_idname = 'mlm.layer_add_smart'
    bl_label = "Add Smart Layer"
    bl_description = "Add a ready made detail layer (procedural masks you can tweak; swap its fill for any material)"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(items=presets.SMART_ITEMS)

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        apply_preset(ob, mat, self.preset)
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_layer_remove(Operator):
    bl_idname = 'mlm.layer_remove'
    bl_label = "Remove Layer"
    bl_description = "Delete the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and len(st.layers) > 1

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        with nodes.suspend():
            st.layers.remove(st.active)
            st.active = max(0, min(st.active, len(st.layers) - 1))
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_layer_move(Operator):
    bl_idname = 'mlm.layer_move'
    bl_label = "Move Layer"
    bl_description = "Move the active layer up (on top) or down"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and len(st.layers) > 1

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        i = st.active
        j = i + 1 if self.direction == 'UP' else i - 1
        if not 0 <= j < len(st.layers):
            return {'CANCELLED'}
        with nodes.suspend():
            st.layers.move(i, j)
            st.active = j
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_layer_duplicate(Operator):
    bl_idname = 'mlm.layer_duplicate'
    bl_label = "Duplicate Layer"
    bl_description = "Copy the active layer (painted masks are copied too)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and len(st.layers) > 0

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        src = active_layer(st)
        with nodes.suspend():
            L = st.layers.add()
            copy_pg(src, L)
            L.uid = new_uid()
            L.name = src.name + ' copy'
            for M0 in src.masks:
                M = L.masks.add()
                copy_pg(M0, M)
                M.uid = new_uid()
                if M.type == 'PAINT' and M0.image is not None:
                    M.image = nodes.copy_mask_image(M0.image)
                if M.type == 'VERTEX' and M0.attr and ob is not None:
                    a0 = ob.data.color_attributes.get(M0.attr)
                    M.attr = 'MLM_' + M.uid
                    a = ensure_color_attr(ob.data, M.attr)
                    if a0 is not None and a0.domain == a.domain:
                        buf = np.empty(len(a0.data) * 4, np.float32)
                        a0.data.foreach_get('color', buf)
                        a.data.foreach_set('color', buf)
            idx = len(st.layers) - 1
            st.layers.move(idx, st.active + 1)
            st.active = st.active + 1
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_refresh(Operator):
    bl_idname = 'mlm.refresh'
    bl_label = "Refresh"
    bl_description = "Rebuild the layered material (re-reads source materials after you edited them)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None

    def execute(self, context):
        finish(context, ctx_stack(context)[1], force=True)
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- masks
class MLM_OT_mask_add(Operator):
    bl_idname = 'mlm.mask_add'
    bl_label = "Add Mask"
    bl_description = "Add a mask to the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    type: EnumProperty(items=MASK_TYPES)

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and st.active > 0

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        add_mask(ob, active_layer(st), self.type)
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_mask_remove(Operator):
    bl_idname = 'mlm.mask_remove'
    bl_label = "Remove Mask"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        L = active_layer(st)
        return L is not None and len(L.masks) > 0

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        with nodes.suspend():
            L.masks.remove(L.active_mask)
            L.active_mask = max(0, min(L.active_mask, len(L.masks) - 1))
        finish(context, mat)
        return {'FINISHED'}


class MLM_OT_mask_move(Operator):
    bl_idname = 'mlm.mask_move'
    bl_label = "Move Mask"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    @classmethod
    def poll(cls, context):
        L = active_layer(ctx_stack(context)[2])
        return L is not None and len(L.masks) > 1

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        i = L.active_mask
        j = i - 1 if self.direction == 'UP' else i + 1
        if not 0 <= j < len(L.masks):
            return {'CANCELLED'}
        with nodes.suspend():
            L.masks.move(i, j)
            L.active_mask = j
        finish(context, mat)
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- painting
def paint_settings(context):
    ts = context.tool_settings
    mode = context.mode
    p = ts.vertex_paint if mode == 'PAINT_VERTEX' else ts.image_paint
    return p, getattr(p, 'unified_paint_settings', None) or getattr(ts, 'unified_paint_settings', None)


def set_brush_color(context, rgb):
    ts = context.tool_settings
    for p in (ts.image_paint, ts.vertex_paint):
        if p is None:
            continue
        ups = getattr(p, 'unified_paint_settings', None) or getattr(ts, 'unified_paint_settings', None)
        if ups is not None:
            try:
                ups.use_unified_color = True
                ups.color = rgb
            except (AttributeError, TypeError):
                pass
        br = p.brush
        if br is not None:
            try:
                br.color = rgb
            except (AttributeError, TypeError, RuntimeError):
                pass


def get_brush_color(context):
    p, ups = paint_settings(context)
    if ups is not None and ups.use_unified_color:
        return tuple(ups.color)
    if p is not None and p.brush is not None:
        return tuple(p.brush.color)
    return (1.0, 1.0, 1.0)


def new_mask_image(mat, L, M, res):
    name = f"Mask {mat.name} {L.name}"[:60]
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=False)
    img.generated_color = (0.0, 0.0, 0.0, 1.0)
    try:
        img.colorspace_settings.name = 'Non-Color'
    except TypeError:
        pass
    img['mlm_mask'] = True
    return img


class MLM_OT_paint(Operator):
    bl_idname = 'mlm.paint'
    bl_label = "Paint Mask"
    bl_description = ("Paint this layer's mask: white reveals the layer, black hides it "
                      "(creates the mask image / paint UV when needed)")
    bl_options = {'REGISTER', 'UNDO'}

    layer: IntProperty(default=-1, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and first_paintable(st) > 0

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        if 0 < self.layer < len(st.layers):
            st.active = self.layer
        L = active_layer(st)
        M = paint_mask_of(L) if st.active > 0 else None
        if M is None:
            self.report({'WARNING'}, "Select a layer with a paint mask (the base layer is never painted)")
            return {'CANCELLED'}
        L.active_mask = list(L.masks).index(M)
        if not L.enabled:
            L.enabled = True
            self.report({'INFO'}, f"Layer '{L.name}' was hidden - made it visible to paint it")
        me = ob.data
        if M.type == 'PAINT':
            made = None
            if me.uv_layers.get(st.paint_uv) is None:
                if context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                try:
                    made = make_paint_uv(context, ob, st.paint_uv, 'AUTO')
                except RuntimeError as e:
                    self.report({'ERROR'}, str(e))
                    return {'CANCELLED'}
            if M.image is None:
                M.image = new_mask_image(mat, L, M, int(st.mask_res))
            if ob.mlm_relief.enabled and L.relief_geo != 'OFF':
                from . import relief                     # the 3D surface follows the brush while painting
                try:
                    relief.start_live(context, ob, mat, L, M, self.report)
                except Exception as e:                   # painting must start even if the live 3D can't
                    self.report({'WARNING'}, f"Live 3D painting unavailable ({e}) - the shape updates on Done")
            if context.mode != 'PAINT_TEXTURE':
                bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            ip = context.tool_settings.image_paint
            ip.mode = 'IMAGE'
            ip.canvas = M.image
            try:
                ip.seam_bleed = max(ip.seam_bleed, 4)    # room for the edge breakup warp at UV island borders
            except (AttributeError, TypeError):
                pass
            me.uv_layers.active = me.uv_layers[st.paint_uv]
            if made:
                self.report({'INFO'}, f"Created paint UV map '{st.paint_uv}' ({made.lower()} projection)")
        else:
            if not M.attr:
                M.attr = 'MLM_' + M.uid
            a = ensure_color_attr(me, M.attr)
            if context.mode != 'PAINT_VERTEX':
                bpy.ops.object.mode_set(mode='VERTEX_PAINT')
            try:
                me.color_attributes.active_color = a
            except (AttributeError, TypeError):
                me.color_attributes.active_color_name = a.name
        set_brush_color(context, (1.0, 1.0, 1.0))
        context.window_manager.mlm_painting = f"{L.name}  >  {M.name}"
        sp = context.space_data
        if sp is not None and sp.type == 'VIEW_3D' and sp.shading.type == 'SOLID':
            sp.shading.type = 'MATERIAL'
        return {'FINISHED'}


class MLM_OT_image_revert(Operator):
    bl_idname = 'mlm.image_revert'
    bl_label = "Undo Strokes on Texture"
    bl_description = "Reload this image from disk: throws away everything painted onto it by accident"
    bl_options = {'REGISTER'}

    image: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def execute(self, context):
        img = bpy.data.images.get(self.image)
        if img is None or img.get('mlm_mask'):
            return {'CANCELLED'}
        img.reload()
        self.report({'INFO'}, f"'{img.name}' reloaded - accidental strokes discarded")
        return {'FINISHED'}


class MLM_OT_paint_color(Operator):
    bl_idname = 'mlm.paint_color'
    bl_label = "Brush Color"
    bl_description = "White reveals the layer, black hides it"
    bl_options = {'REGISTER'}

    color: EnumProperty(items=[('WHITE', "Reveal", "Paint white (show the layer)"),
                               ('BLACK', "Hide", "Paint black (hide the layer)"),
                               ('SWAP', "Swap", "Swap white / black")])

    def execute(self, context):
        if self.color == 'SWAP':
            c = get_brush_color(context)
            v = 0.0 if sum(c[:3]) / 3.0 > 0.5 else 1.0
        else:
            v = 1.0 if self.color == 'WHITE' else 0.0
        set_brush_color(context, (v, v, v))
        return {'FINISHED'}


def _blur2d(a, r):
    if r < 1:
        return a
    for axis in (0, 1):
        for _ in range(3):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (r + 1, r)
            p = np.pad(a, pad, mode='edge')
            c = np.cumsum(p, axis=axis, dtype=np.float64)
            if axis == 0:
                a = ((c[2 * r + 1:, :] - c[:-2 * r - 1, :]) / (2 * r + 1)).astype(np.float32)
            else:
                a = ((c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)).astype(np.float32)
    return a


def _vertex_smooth(me, corner_vals, iters):
    nv = len(me.vertices)
    vi = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', vi)
    ev = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get('vertices', ev)
    ev = ev.reshape(-1, 2)
    cnt = np.bincount(vi, minlength=nv).astype(np.float32)
    v = np.bincount(vi, weights=corner_vals, minlength=nv) / np.maximum(cnt, 1)
    deg = np.bincount(ev.ravel(), minlength=nv).astype(np.float32)
    for _ in range(iters):
        s = np.zeros(nv)
        np.add.at(s, ev[:, 0], v[ev[:, 1]])
        np.add.at(s, ev[:, 1], v[ev[:, 0]])
        v = np.where(deg > 0, 0.5 * v + 0.5 * s / np.maximum(deg, 1), v)
    return v[vi].astype(np.float32)


class MLM_OT_mask_edit(Operator):
    bl_idname = 'mlm.mask_edit'
    bl_label = "Edit Mask"
    bl_description = "Fill, clear, invert or soften the active painted mask"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=[('FILL', "Fill White", "Show the layer everywhere"),
                                ('CLEAR', "Clear Black", "Hide the layer everywhere"),
                                ('INVERT', "Invert", "Swap shown and hidden areas"),
                                ('BLUR', "Soften", "Blur the painted mask")])
    radius: IntProperty(name="Radius", default=6, min=1, max=128, subtype='PIXEL')

    @classmethod
    def poll(cls, context):
        M = active_mask(active_layer(ctx_stack(context)[2]))
        return M is not None and M.type in ('PAINT', 'VERTEX')

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        M = active_mask(L)
        if M.type == 'PAINT':
            img = M.image
            if img is None:
                img = M.image = new_mask_image(mat, L, M, int(st.mask_res))
            w, h = img.size
            px = np.empty(w * h * 4, np.float32)
            img.pixels.foreach_get(px)
            px = px.reshape(h, w, 4)
            g = px[..., 0]
            if self.action == 'FILL':
                g = np.ones_like(g)
            elif self.action == 'CLEAR':
                g = np.zeros_like(g)
            elif self.action == 'INVERT':
                g = 1.0 - g
            else:
                g = _blur2d(g, self.radius)
            px[..., 0] = px[..., 1] = px[..., 2] = g
            px[..., 3] = 1.0
            img.pixels.foreach_set(px.ravel())
            img.update()
        else:
            me = ob.data
            a = ensure_color_attr(me, M.attr or ('MLM_' + M.uid))
            M.attr = a.name
            buf = np.empty(len(a.data) * 4, np.float32)
            a.data.foreach_get('color', buf)
            buf = buf.reshape(-1, 4)
            g = buf[:, 0]
            if self.action == 'FILL':
                g = np.ones_like(g)
            elif self.action == 'CLEAR':
                g = np.zeros_like(g)
            elif self.action == 'INVERT':
                g = 1.0 - g
            elif a.domain == 'CORNER':
                g = _vertex_smooth(me, g, max(1, self.radius // 2))
            buf[:, 0] = buf[:, 1] = buf[:, 2] = g
            buf[:, 3] = 1.0
            a.data.foreach_set('color', buf.ravel())
            me.update()
        return {'FINISHED'}


class MLM_OT_paint_exit(Operator):
    bl_idname = 'mlm.paint_exit'
    bl_label = "Done Painting"
    bl_description = "Back to Object Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        context.window_manager.mlm_painting = ''
        ob = ctx_object(context)
        if ob is not None and ob.mlm_relief.enabled and ob.mlm_relief.auto_update:
            bpy.ops.mlm.relief_make()                      # painted geometry layers re-shape the 3D surface
        return {'FINISHED'}


def transfer_masks(context, ob, mats, old_uv, new_uv):
    """re-sample every painted mask of `mats` from old_uv into new_uv (Cycles emit bake), in place -> count"""
    from . import bake
    imgs = []
    for m in mats:
        for L in m.mlm.layers:
            for M in L.masks:
                if M.type == 'PAINT' and M.image is not None and M.image not in imgs:
                    imgs.append(M.image)
    if not imgs:
        return 0
    me = ob.data
    tm = bpy.data.materials.new('MLM_TMP_masktransfer')
    tm.use_nodes = True
    nt = tm.node_tree
    nt.nodes.clear()
    uvn = nt.nodes.new('ShaderNodeUVMap')
    uvn.uv_map = old_uv
    src = nt.nodes.new('ShaderNodeTexImage')
    src.interpolation = 'Linear'
    em = nt.nodes.new('ShaderNodeEmission')
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    tgt = nt.nodes.new('ShaderNodeTexImage')
    nt.links.new(uvn.outputs[0], src.inputs['Vector'])
    nt.links.new(src.outputs['Color'], em.inputs['Color'])
    nt.links.new(em.outputs[0], out.inputs['Surface'])
    slots = [(s, s.material) for s in ob.material_slots]
    for s, _ in slots:
        s.material = tm
    if not slots:
        me.materials.append(tm)
    prev_uv = me.uv_layers.active.name if me.uv_layers.active else None
    iso = bake.Isolate(context.scene, {ob})
    rs = bake._RenderState(context, ob, 1)
    n = 0
    try:
        me.uv_layers.active = me.uv_layers[new_uv]
        for img in imgs:
            w, h = img.size
            tmp = bpy.data.images.new('MLM_tmp_mask', w, h, alpha=False, float_buffer=img.is_float)
            try:
                tmp.colorspace_settings.name = img.colorspace_settings.name
            except TypeError:
                pass
            src.image = img
            tgt.image = tmp
            for nd in nt.nodes:
                nd.select = False
            tgt.select = True
            nt.nodes.active = tgt
            bake.run_bake(context, ob, type='EMIT', uv_layer=new_uv, margin=8, margin_type='EXTEND', use_clear=True,
                                target='IMAGE_TEXTURES')
            px = np.empty(w * h * 4, np.float32)
            tmp.pixels.foreach_get(px)
            px = px.reshape(-1, 4)
            px[:, 3] = 1.0
            img.pixels.foreach_set(px.ravel())
            img.update()
            try:
                img.pack()
            except RuntimeError:
                pass
            bpy.data.images.remove(tmp)
            n += 1
    finally:
        rs.restore()
        iso.restore()
        if slots:
            for s, m in slots:
                s.material = m
        else:
            me.materials.clear()
        bpy.data.materials.remove(tm)
        if prev_uv and me.uv_layers.get(prev_uv):
            me.uv_layers.active = me.uv_layers[prev_uv]
    return n


class MLM_OT_paint_uv_rebuild(Operator):
    bl_idname = 'mlm.paint_uv_rebuild'
    bl_label = "Rebuild Paint UV (keep painting)"
    bl_description = ("Unwrap the paint UV again so the masks use the whole texture: sharper painting and bakes. "
                      "Everything already painted is carried over to the new UVs")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None and context.mode == 'OBJECT'

    def execute(self, context):
        ob, mat, st = ctx_stack(context)
        me = ob.data
        old = st.paint_uv
        if me.uv_layers.get(old) is None:
            made = make_paint_uv(context, ob, old, 'AUTO')
            self.report({'INFO'}, f"Paint UV '{old}' created ({made})")
            return {'FINISHED'}
        mats = [s.material for s in ob.material_slots if s.material is not None and s.material.mlm.is_stack
                and s.material.mlm.paint_uv == old]
        before = uv_coverage(me, old)
        tmpname = 'MLM_tmp_paint'
        try:
            made = make_paint_uv(context, ob, tmpname, 'AUTO')
            n = transfer_masks(context, ob, mats, old, tmpname)
        except RuntimeError as e:
            if me.uv_layers.get(tmpname) is not None:
                me.uv_layers.remove(me.uv_layers[tmpname])
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        render = render_uv_name(me)
        me.uv_layers.remove(me.uv_layers[old])
        me.uv_layers[tmpname].name = old
        for u in me.uv_layers:
            u.active_render = (u.name == render) if render != old else (u.name == old)
        for m in mats:
            nodes.rebuild(m)
        _cov_cache.clear()
        self.report({'INFO'}, f"Paint UV now uses {uv_coverage(me, old):.0%} of the mask texture (was {before:.0%}); "
                              f"{n} painted mask(s) carried over")
        return {'FINISHED'}


class MLM_OT_make_uv(Operator):
    bl_idname = 'mlm.make_uv'
    bl_label = "Create Paint / Bake UV"
    bl_description = ("(Re)create the non-overlapping UV map used for painted masks and baking. "
                      "Existing painted masks follow the new UVs only if you repaint them")
    bl_options = {'REGISTER', 'UNDO'}

    method: EnumProperty(items=[
        ('AUTO', "Automatic", "Top-down for terrain-like (flat) objects, Smart UV for everything else"),
        ('SMART', "Smart UV Project", "Good for props, buildings, characters. Dense / displaced meshes are unwrapped "
                                       "from their smoothed shape: few big islands that use most of the texture"),
        ('REPACK', "Re-pack Your UV Map", "Your own unwrap, packed into the 0-1 square without overlaps"),
        ('TOP', "Top-Down (terrain)", "Planar projection from above - perfect for height-field terrain"),
        ('LIGHTMAP', "Lightmap Pack", "Every face gets its own space"),
        ('COPY', "Copy Render UV", "Use your own unwrap (it must not overlap)")], default='AUTO')

    @classmethod
    def poll(cls, context):
        return ctx_object(context) is not None

    def execute(self, context):
        ob = ctx_object(context)
        mat = ob.active_material
        name = mat.mlm.paint_uv if mat is not None and mat.mlm.is_stack else context.scene.mlm_bake.uv_name
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            m = make_paint_uv(context, ob, name, self.method)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"UV map '{name}' created ({m.lower()})")
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- asset browser
class MLM_OT_add_asset_layers(Operator):
    bl_idname = 'mlm.add_asset_layers'
    bl_label = "Add as Mixer Layer"
    bl_description = ("Add the selected material assets as layers to the active object's layered material "
                      "(makes it layered first if needed)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        assets = getattr(context, 'selected_assets', None)
        return bool(assets) and any(a.id_type == 'MATERIAL' for a in assets) and ctx_object(context) is not None

    def execute(self, context):
        ob = ctx_object(context)
        mats = []
        for a in context.selected_assets:
            if a.id_type != 'MATERIAL':
                continue
            if a.local_id is not None:
                mats.append(a.local_id)
                continue
            path = a.full_library_path
            if not path or not os.path.exists(path):
                continue
            with bpy.data.libraries.load(path, link=False) as (src, dst):
                dst.materials = [a.name] if a.name in src.materials else []
            mats += [m for m in dst.materials if m is not None]
        if not mats:
            self.report({'WARNING'}, "No material assets selected")
            return {'CANCELLED'}
        mat = ob.active_material
        if mat is None or not mat.mlm.is_stack:
            mat = create_stack(ob, mat)
            if len(ob.material_slots):
                ob.material_slots[ob.active_material_index].material = mat
            else:
                ob.data.materials.append(mat)
        for m in mats:
            L = add_layer(mat, m.name, 'MATERIAL')
            with nodes.suspend():
                L.material = m
            add_mask(ob, L, 'PAINT')
        finish(context, mat)
        self.report({'INFO'}, f"Added {len(mats)} layer(s) to {mat.name} on {ob.name}")
        return {'FINISHED'}


CLASSES = (MLM_OT_stack_create, MLM_OT_layer_add, MLM_OT_layer_add_material, MLM_OT_layer_add_texture_set,
           MLM_OT_layer_add_smart, MLM_OT_layer_remove, MLM_OT_layer_move, MLM_OT_layer_duplicate, MLM_OT_refresh,
           MLM_OT_mask_add, MLM_OT_mask_remove, MLM_OT_mask_move, MLM_OT_paint, MLM_OT_image_revert, MLM_OT_paint_color,
           MLM_OT_mask_edit, MLM_OT_paint_exit, MLM_OT_make_uv, MLM_OT_paint_uv_rebuild, MLM_OT_add_asset_layers)
