# Material Layer Mixer - bake any material (layered or not) to plain PBR textures and export for Roblox.
# Color / Roughness / Metalness / Emission / Height are baked exactly through a temporary emission shader,
# the normal map is a real tangent-space (OpenGL, Roblox) normal bake, AO can be multiplied into the color.
import os
import re
import math
import numpy as np
import bpy
import bmesh
from bpy.props import StringProperty, BoolProperty
from bpy.types import Operator
from . import nodes
from .ops import ctx_object, make_paint_uv, uv_coverage

CH = [  # channel, file suffix, setting, colorspace, bake kind
    ('color', 'Color', 'use_color', 'sRGB', 'EMIT'),
    ('normal', 'Normal', 'use_normal', 'Non-Color', 'NORMAL'),
    ('roughness', 'Roughness', 'use_roughness', 'Non-Color', 'EMIT'),
    ('metallic', 'Metalness', 'use_metallic', 'Non-Color', 'EMIT'),
    ('emission', 'Emissive', 'use_emission', 'sRGB', 'EMIT'),
    ('height', 'Height', 'use_height', 'Non-Color', 'EMIT'),
]
DEFAULTS = {'color': (0.6, 0.6, 0.6, 1.0), 'roughness': 0.5, 'metallic': 0.0, 'emission': (0.0, 0.0, 0.0, 1.0),
            'height': 0.5}


def safe(name):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('_') or 'object'


def channel_source(mat, ch):
    """-> (socket or None, default value, emission strength (socket / float))"""
    nt = mat.node_tree
    if mat.mlm.is_stack:
        r = nt.nodes.get('MLM::ch:' + ch)
        if r is not None:
            return r.outputs[0], None, 1.0
    out, bsdf = nodes.material_bsdf(mat)
    if ch == 'height':
        if out is not None:
            dfs = nodes.upstream(out.inputs['Displacement'])
            if dfs is not None and dfs.node.bl_idname == 'ShaderNodeDisplacement':
                h = nodes.upstream(dfs.node.inputs['Height'])
                if h is not None:
                    return h, None, 1.0
        return None, DEFAULTS['height'], 1.0
    if bsdf is None:
        return None, DEFAULTS[ch], 1.0
    name = {'color': 'Base Color' if 'Base Color' in bsdf.inputs else 'Color', 'metallic': 'Metallic',
            'roughness': 'Roughness', 'emission': 'Emission Color'}[ch]
    if bsdf.bl_idname == 'ShaderNodeEmission' and ch == 'emission':
        name = 'Color'
    s = bsdf.inputs.get(name)
    if s is None:
        return None, DEFAULTS[ch], 1.0
    strength = 1.0
    if ch == 'emission':
        ss = bsdf.inputs.get('Emission Strength') or bsdf.inputs.get('Strength')
        if ss is not None:
            strength = nodes.upstream(ss) or ss.default_value
    fs = nodes.upstream(s)
    if fs is not None:
        return fs, None, strength
    return None, s.default_value, strength


def _rgba(v):
    if isinstance(v, (int, float)):
        return (float(v), float(v), float(v), 1.0)
    v = tuple(v)
    return v[:3] + (1.0,) if len(v) >= 3 else (v[0], v[0], v[0], 1.0)


class _MatState:
    """temporary bake nodes in one material + restore"""
    def __init__(self, mat, image):
        self.mat = mat
        nt = mat.node_tree
        self.prev_active = [n.name for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial'
                            and n.is_active_output]
        self.prev_node = nt.nodes.active.name if nt.nodes.active else None
        self.target = nt.nodes.new('ShaderNodeTexImage')
        self.target.name = 'MLM_TMP_target'
        self.target.image = image
        self.target.location = (2000, -800)
        self.tmp = []
        if mat.mlm.is_stack and nt.nodes.get('MLM::out') is not None:
            nt.nodes['MLM::out'].is_active_output = True
        self.activate()

    def activate(self):
        nt = self.mat.node_tree
        for n in nt.nodes:
            n.select = False
        self.target.select = True
        nt.nodes.active = self.target

    def set_image(self, image):
        self.target.image = image
        self.activate()

    def push_emit(self, ch):
        src, dv, strength = channel_source(self.mat, ch)
        self.push_source(src, dv, strength if ch == 'emission' else 1.0)

    def push_source(self, src, dv=(0.0, 0.0, 0.0, 1.0), strength=1.0):
        nt = self.mat.node_tree
        em = nt.nodes.new('ShaderNodeEmission')
        em.location = (2000, -1100)
        if src is not None:
            nt.links.new(src, em.inputs['Color'])
        else:
            em.inputs['Color'].default_value = _rgba(dv)
        if isinstance(strength, bpy.types.NodeSocket):
            nt.links.new(strength, em.inputs['Strength'])
        else:
            em.inputs['Strength'].default_value = float(strength)
        o = nt.nodes.new('ShaderNodeOutputMaterial')
        o.location = (2300, -1100)
        o.target = 'ALL'
        nt.links.new(em.outputs[0], o.inputs['Surface'])
        o.is_active_output = True
        self.tmp += [em, o]
        self.activate()

    def push_plain(self):
        """plain diffuse surface: AO is baked from the geometry, not from bumpy shading normals"""
        nt = self.mat.node_tree
        d = nt.nodes.new('ShaderNodeBsdfDiffuse')
        o = nt.nodes.new('ShaderNodeOutputMaterial')
        o.target = 'ALL'
        nt.links.new(d.outputs[0], o.inputs['Surface'])
        o.is_active_output = True
        self.tmp += [d, o]
        self.activate()

    def pop_emit(self):
        nt = self.mat.node_tree
        for n in self.tmp:
            nt.nodes.remove(n)
        self.tmp = []
        self._restore_outputs()
        if self.mat.mlm.is_stack and nt.nodes.get('MLM::out') is not None:
            nt.nodes['MLM::out'].is_active_output = True
        self.activate()

    def _restore_outputs(self):
        nt = self.mat.node_tree
        for nm in self.prev_active:
            n = nt.nodes.get(nm)
            if n is not None:
                n.is_active_output = True

    def restore(self):
        nt = self.mat.node_tree
        for n in self.tmp:
            nt.nodes.remove(n)
        nt.nodes.remove(self.target)
        self._restore_outputs()
        if self.prev_node and nt.nodes.get(self.prev_node):
            nt.nodes.active = nt.nodes[self.prev_node]


def _image(name, res, colorspace, fill):
    old = bpy.data.images.get(name)              # always bake into a fresh buffer (file images load lazily)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=False)
    try:
        img.colorspace_settings.name = colorspace
    except TypeError:
        pass
    img.generated_color = fill
    return img


def _pixels(img):
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


def _save(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    if img.source != 'FILE':
        img.source = 'FILE'
    rel = path
    if bpy.data.filepath:
        try:
            rel = bpy.path.relpath(path)
        except ValueError:
            pass
    img.filepath = rel
    img.reload()


def output_dir(settings):
    d = settings.output_dir or '//MLM_Baked/'
    if d.startswith('//') and not bpy.data.filepath:
        return os.path.join(os.path.expanduser('~'), 'Documents', 'MLM_Baked')
    return os.path.abspath(bpy.path.abspath(d))


def baked_material(name, imgs):
    bm = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if bm.node_tree is None:
        bm.use_nodes = True
    nt = bm.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (500, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (100, 0)
    nt.links.new(bsdf.outputs[0], out.inputs['Surface'])
    y = 400

    def tex(img, label):
        nonlocal y
        n = nt.nodes.new('ShaderNodeTexImage')
        n.image = img
        n.label = label
        n.location = (-500, y)
        y -= 300
        return n
    if 'color' in imgs:
        nt.links.new(tex(imgs['color'], 'Color').outputs['Color'], bsdf.inputs['Base Color'])
    if 'metallic' in imgs:
        nt.links.new(tex(imgs['metallic'], 'Metalness').outputs['Color'], bsdf.inputs['Metallic'])
    else:
        bsdf.inputs['Metallic'].default_value = 0.0
    if 'roughness' in imgs:
        nt.links.new(tex(imgs['roughness'], 'Roughness').outputs['Color'], bsdf.inputs['Roughness'])
    if 'normal' in imgs:
        nm = nt.nodes.new('ShaderNodeNormalMap')
        nm.location = (-200, -500)
        nt.links.new(tex(imgs['normal'], 'Normal').outputs['Color'], nm.inputs['Color'])
        nt.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
    if 'emission' in imgs:
        nt.links.new(tex(imgs['emission'], 'Emissive').outputs['Color'], bsdf.inputs['Emission Color'])
        bsdf.inputs['Emission Strength'].default_value = 1.0
    if 'height' in imgs:
        tex(imgs['height'], 'Height (not connected)')
    bm['mlm_baked'] = True
    return bm


def baked_copy(context, ob, bm, uv_name):
    cp = bpy.data.objects.get(ob.get('mlm_baked', ''))
    if cp is None or cp.get('mlm_source') != ob.name:
        cp = ob.copy()
        cp.name = ob.name + '_Baked'
        coll = bpy.data.collections.get('MLM Baked')
        if coll is None:
            coll = bpy.data.collections.new('MLM Baked')
            context.scene.collection.children.link(coll)
        coll.objects.link(cp)
    old = cp.data
    me = ob.data.copy()
    me.name = ob.data.name + '_Baked'
    cp.data = me
    if old is not None and old != ob.data and old.users == 0:
        bpy.data.meshes.remove(old)
    for uv in list(me.uv_layers):
        if uv.name != uv_name:
            me.uv_layers.remove(uv)
    if len(me.uv_layers):
        me.uv_layers[0].name = 'UVMap'
        me.uv_layers.active = me.uv_layers[0]
        me.uv_layers[0].active_render = True
    for a in list(me.color_attributes):
        if a.name.startswith('MLM_'):
            me.color_attributes.remove(a)
    me.materials.clear()
    me.materials.append(bm)
    if len(me.polygons):
        me.polygons.foreach_set('material_index', np.zeros(len(me.polygons), np.int32))
    for s in cp.material_slots:
        s.link = 'DATA'
    cp.matrix_world = ob.matrix_world
    cp['mlm_source'] = ob.name
    ob['mlm_baked'] = cp.name
    return cp


def show_baked(ob, cp, baked):
    """show either the layered original or its baked copy (viewport and render)"""
    ob.hide_set(baked)
    ob.hide_render = baked
    cp.hide_set(not baked)
    cp.hide_render = not baked


def gpu_ready():
    """a GPU is set up in Preferences > System > Cycles Render Devices"""
    try:
        cp = bpy.context.preferences.addons['cycles'].preferences
        return cp.compute_device_type not in ('', 'NONE') and any(d.use and d.type != 'CPU' for d in cp.devices)
    except (KeyError, AttributeError, TypeError):
        return False


def set_device(scene, use_gpu):
    """-> previous device"""
    prev = scene.cycles.device
    if use_gpu and gpu_ready():
        scene.cycles.device = 'GPU'
    return prev


def tri_count(me):
    if not len(me.polygons):
        return 0
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get('loop_total', lt)
    return int((lt - 2).sum())


def drop_empty_emission(imgs):
    """an all-black emissive map only makes Roblox warn and re-process the color map: leave it out"""
    e = imgs.get('emission')
    if e is not None and float(_pixels(e)[..., :3].max()) < 0.004:
        imgs.pop('emission')
        bpy.data.images.remove(e)
        return True
    return False


def run_bake(context, active, selected=None, **kw):
    """object.bake with an explicit context: the bake always targets `active` (and bakes from `selected` in
    selected-to-active mode), whatever context the caller passed in"""
    sel = list(selected) if selected else [active]
    with context.temp_override(object=active, active_object=active, selected_objects=sel,
                               selected_editable_objects=sel):
        return bpy.ops.object.bake(**kw)


class Isolate:
    """render only the kept objects while baking: no AO / ray hits from the rest of the scene, and a big scene is
    not synced into Cycles for nothing"""
    def __init__(self, scene, keep):
        self.prev = [(o, o.hide_render) for o in scene.objects if o not in keep and not o.hide_render]
        for o, _ in self.prev:
            o.hide_render = True

    def restore(self):
        for o, h in self.prev:
            o.hide_render = h


def bake_object(context, ob, S, report):
    scene = context.scene
    me = ob.data
    res = int(S.resolution)
    uv_name = S.uv_name or 'MLM_BakeUV'
    mats = []
    for s in ob.material_slots:
        if s.material is not None and s.material not in mats and s.material.node_tree is not None:
            mats.append(s.material)
    if not mats:
        report({'WARNING'}, f"{ob.name}: no material to bake")
        return None
    if uv_name in {m.mlm.paint_uv for m in mats if m.mlm.is_stack}:
        uv_name = 'MLM_BakeUV'                      # the paint UV is laid out for brushes: bake into its own UV
    if me.uv_layers.get(uv_name) is None or uv_coverage(me, uv_name) < 0.3:
        made = make_paint_uv(context, ob, uv_name, 'AUTO')
        report({'INFO'}, f"{ob.name}: bake UV '{uv_name}' ({made})")
    base = safe(ob.name)
    chans = [c for c in CH if getattr(S, c[2])]
    imgs = {c[0]: _image(f'{base}_{c[1]}', res, c[3], (0.5, 0.5, 1.0, 1.0) if c[0] == 'normal' else (0, 0, 0, 1))
            for c in chans}
    prev_active_uv = me.uv_layers.active.name if me.uv_layers.active else None
    me.uv_layers.active = me.uv_layers[uv_name]          # tangent frame of the normal bake follows the active UV
    iso = Isolate(scene, {ob})                           # incl. old baked copies sitting on top of the original
    states = [_MatState(m, next(iter(imgs.values()))) for m in mats]
    try:
        for ch, suf, _, cs, kind in chans:
            for stt in states:
                stt.set_image(imgs[ch])
            if kind == 'NORMAL':
                run_bake(context, ob, type='NORMAL', normal_space='TANGENT', normal_r='POS_X', normal_g='POS_Y',
                                    normal_b='POS_Z', uv_layer=uv_name, margin=S.margin, margin_type='EXTEND',
                                    use_clear=True, target='IMAGE_TEXTURES')
            else:
                for stt in states:
                    stt.push_emit(ch)
                try:
                    run_bake(context, ob, type='EMIT', uv_layer=uv_name, margin=S.margin, margin_type='EXTEND',
                                        use_clear=True, target='IMAGE_TEXTURES')
                finally:
                    for stt in states:
                        stt.pop_emit()
        if S.ao_into_color and 'color' in imgs and S.ao_strength > 0:
            ao = _image(f'{base}_AO_tmp', res, 'Non-Color', (1, 1, 1, 1))
            for stt in states:
                stt.set_image(ao)
                stt.push_plain()
            world = scene.world
            tmp_world = None
            if world is None:
                tmp_world = scene.world = bpy.data.worlds.new('MLM_tmp_world')
                world = tmp_world
            prev_dist = world.light_settings.distance
            prev_samples = scene.cycles.samples
            world.light_settings.distance = S.ao_distance
            scene.cycles.samples = max(prev_samples, 64)
            try:
                run_bake(context, ob, type='AO', uv_layer=uv_name, margin=S.margin, margin_type='EXTEND',
                                    use_clear=True, target='IMAGE_TEXTURES')
            finally:
                for stt in states:
                    stt.pop_emit()
                world.light_settings.distance = prev_dist
                scene.cycles.samples = prev_samples
                if tmp_world is not None:
                    scene.world = None
                    bpy.data.worlds.remove(tmp_world)
            a = _pixels(ao)[..., 0]
            from .ops import _blur2d
            a = _blur2d(a, 1)
            c = _pixels(imgs['color'])
            c[..., :3] *= (1.0 - S.ao_strength * (1.0 - np.clip(a, 0.0, 1.0)))[..., None]
            imgs['color'].pixels.foreach_set(c.ravel())
            imgs['color'].update()
            bpy.data.images.remove(ao)
    finally:
        for stt in states:
            stt.restore()
        iso.restore()
        if prev_active_uv and me.uv_layers.get(prev_active_uv):
            me.uv_layers.active = me.uv_layers[prev_active_uv]
    drop_empty_emission(imgs)
    folder = os.path.join(output_dir(S), base)
    for ch, suf, *_ in chans:
        if ch in imgs:
            _save(imgs[ch], os.path.join(folder, f'{base}_{suf}.png'))
    bm = baked_material(f'{base}_Baked', imgs)
    cp = baked_copy(context, ob, bm, uv_name)
    if S.hide_original:
        show_baked(ob, cp, True)
    else:
        cp.hide_render = True
    tris = tri_count(cp.data)
    return cp, folder, tris


class MLM_OT_bake(Operator):
    bl_idname = 'mlm.bake'
    bl_label = "Bake to Textures"
    bl_description = ("Bake the selected objects' materials (layered or not) into Color / Normal / Roughness / "
                      "Metalness textures on a non-overlapping UV, and make a '_Baked' copy ready for Roblox")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects) or ctx_object(context) is not None

    def execute(self, context):
        S = context.scene.mlm_bake
        scene = context.scene
        obs = [o for o in context.selected_objects if o.type == 'MESH' and not o.get('mlm_source')]
        act = ctx_object(context)
        if act is not None and act not in obs and not act.get('mlm_source'):
            obs.insert(0, act)
        if not obs:
            self.report({'ERROR'}, "Select the layered objects to bake (not their _Baked copies)")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        vl = context.view_layer
        prev_sel = list(context.selected_objects)
        prev_act = vl.objects.active
        prev_engine = scene.render.engine
        prev_samples = scene.cycles.samples if hasattr(scene, 'cycles') else None
        scene.render.engine = 'CYCLES'
        prev_device = set_device(scene, S.use_gpu)
        scene.cycles.samples = S.samples
        wm = context.window_manager
        wm.progress_begin(0, len(obs))
        results = []
        try:
            for k, ob in enumerate(obs):
                for o in context.selected_objects:
                    o.select_set(False)
                ob.hide_set(False)
                ob.hide_render = False
                ob.select_set(True)
                vl.objects.active = ob
                r = bake_object(context, ob, S, self.report)
                if r:
                    results.append((ob, *r))
                wm.progress_update(k + 1)
        except RuntimeError as e:
            self.report({'ERROR'}, f"Bake failed: {e}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()
            scene.render.engine = prev_engine
            scene.cycles.device = prev_device
            if prev_samples is not None:
                scene.cycles.samples = prev_samples
            for o in context.selected_objects:
                o.select_set(False)
            for o in prev_sel:
                if o.name in vl.objects and o.visible_get():
                    o.select_set(True)
            for ob, cp, folder, tris in results:
                cp.select_set(True)
            if results:
                vl.objects.active = results[0][1]
            elif prev_act is not None:
                vl.objects.active = prev_act
        heavy = [f"{cp.name} ({tris:,} tris)" for ob, cp, folder, tris in results if tris > 20000]
        msg = f"Baked {len(results)} object(s) -> {output_dir(S)}"
        if heavy and S.split_parts:
            self.report({'INFO'}, msg + ". Over 20,000 triangles (Export splits them into Roblox-sized parts): "
                        + ', '.join(heavy))
        elif heavy:
            self.report({'WARNING'}, msg + ". Over Roblox's 20,000 triangle limit: " + ', '.join(heavy))
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class _RenderState:
    """switch to Cycles + object mode + only `ob` selected, restore afterwards"""
    def __init__(self, context, ob, samples):
        self.context = context
        sc = context.scene
        self.engine = sc.render.engine
        self.samples = sc.cycles.samples
        self.sel = list(context.selected_objects)
        self.act = context.view_layer.objects.active
        self.mode = context.mode
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        sc.render.engine = 'CYCLES'
        sc.cycles.samples = samples
        self.device = set_device(sc, sc.mlm_bake.use_gpu)
        for o in self.sel:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob

    def restore(self):
        sc = self.context.scene
        sc.render.engine = self.engine
        sc.cycles.samples = self.samples
        sc.cycles.device = self.device
        for o in self.context.selected_objects:
            o.select_set(False)
        for o in self.sel:
            if o.visible_get():
                o.select_set(True)
        self.context.view_layer.objects.active = self.act


class MLM_OT_mask_freeze(Operator):
    bl_idname = 'mlm.mask_freeze'
    bl_label = "Freeze Masks to Paint"
    bl_description = ("Bake the layer's whole mask stack into one paintable image: procedural masks (edges, cavity "
                      "...) then show in EEVEE / Material Preview and you can paint over them. "
                      "The old masks are kept, switched off")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from .ops import ctx_stack, active_layer
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        return L is not None and st.active > 0 and any(M.enabled for M in L.masks)

    def execute(self, context):
        from .ops import ctx_stack, active_layer, new_mask_image, add_mask
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        me = ob.data
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if me.uv_layers.get(st.paint_uv) is None:
            make_paint_uv(context, ob, st.paint_uv, 'AUTO')
        nodes.rebuild(mat)
        ln = nodes.layer_node(mat, L)
        if ln is None:
            self.report({'ERROR'}, "Layer node not found - press Refresh")
            return {'CANCELLED'}
        img = new_mask_image(mat, L, None, int(st.mask_res))
        img.name = f"Frozen {mat.name} {L.name}"[:60]
        rs = _RenderState(context, ob, max(8, context.scene.mlm_bake.samples))
        prev_uv = me.uv_layers.active.name if me.uv_layers.active else None
        me.uv_layers.active = me.uv_layers[st.paint_uv]
        mats = []
        for s in ob.material_slots:
            if s.material is not None and s.material not in mats and s.material.node_tree is not None:
                mats.append(s.material)
        states = [_MatState(m, img) for m in mats]
        try:
            for s in states:
                if s.mat == mat:
                    s.push_source(nodes.layer_node(mat, L).outputs['Mask Stack'])
                else:
                    s.push_source(None, (0.0, 0.0, 0.0, 1.0))
            run_bake(context, ob, type='EMIT', uv_layer=st.paint_uv, margin=8, margin_type='EXTEND', use_clear=True,
                                target='IMAGE_TEXTURES')
        except RuntimeError as e:
            self.report({'ERROR'}, f"Freeze failed: {e}")
            bpy.data.images.remove(img)
            return {'CANCELLED'}
        finally:
            for s in states:
                s.restore()
            if prev_uv and me.uv_layers.get(prev_uv):
                me.uv_layers.active = me.uv_layers[prev_uv]
            rs.restore()
        img.pack()
        with nodes.suspend():
            for M in L.masks:
                M.enabled = False
            M = add_mask(ob, L, 'PAINT')
            M.image = img
            M.name = 'Frozen Mask'
            L.masks.move(len(L.masks) - 1, 0)
            L.active_mask = 0
            L.masks[0].op = 'MIX'
        nodes.rebuild(mat)
        self.report({'INFO'}, f"Masks of '{L.name}' frozen into a paint mask - paint over it any time")
        return {'FINISHED'}


class MLM_OT_toggle_baked(Operator):
    bl_idname = 'mlm.toggle_baked'
    bl_label = "Layers / Baked"
    bl_description = "Switch between the layered original and its baked copy"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.get('mlm_baked') or o.get('mlm_source') for o in context.selected_objects)

    def execute(self, context):
        vl = context.view_layer
        pairs = set()
        for o in context.selected_objects:
            src = bpy.data.objects.get(o['mlm_source']) if o.get('mlm_source') else o
            cp = bpy.data.objects.get(src.get('mlm_baked', '')) if src else None
            if src is not None and cp is not None:
                pairs.add((src.name, cp.name))
        for s, c in pairs:
            src, cp = bpy.data.objects[s], bpy.data.objects[c]
            baked = not src.hide_get() if src.name in vl.objects else True
            show_baked(src, cp, baked)
            (cp if baked else src).select_set(True)
            vl.objects.active = cp if baked else src
        return {'FINISHED'}


ROBLOX_TRIS = 20000


def split_for_roblox(context, ob, limit=19000):
    """meshes over Roblox's 20,000 triangle limit -> compact parts (k-means on the faces) that keep the same
    material / UVs / textures and the original corner normals, so the cuts don't show. -> (objects, split?)"""
    me = ob.data
    n = tri_count(me)
    if n <= ROBLOX_TRIS:
        return [ob], False
    npol = len(me.polygons)
    cen = np.empty(npol * 3, np.float32)
    me.polygons.foreach_get('center', cen)
    cen = cen.reshape(-1, 3).astype(np.float64)
    lt = np.empty(npol, np.int32)
    me.polygons.foreach_get('loop_total', lt)
    tw = (lt - 2).astype(np.float64)
    k = int(math.ceil(n / limit))
    a = np.zeros(npol, np.int64)
    for _ in range(8):
        c = [cen[np.argmax(((cen - cen.mean(0)) ** 2).sum(1))]]
        for _ in range(k - 1):                       # farthest-point seeds
            d = np.min(np.stack([((cen - x) ** 2).sum(1) for x in c]), axis=0)
            c.append(cen[np.argmax(d)])
        c = np.array(c)
        for _ in range(25):
            a = np.argmin(((cen[:, None, :] - c[None, :, :]) ** 2).sum(2), 1)
            for j in range(k):
                m = a == j
                if m.any():
                    c[j] = np.average(cen[m], axis=0, weights=np.maximum(tw[m], 1e-6))
        if np.bincount(a, tw, k).max() <= ROBLOX_TRIS:
            break
        k += 1
    cn = np.empty(len(me.loops) * 3, np.float32)
    me.corner_normals.foreach_get('vector', cn)
    cn = cn.reshape(-1, 3)
    parts = []
    for j in range(k):
        sel = np.nonzero(a != j)[0]
        if len(sel) == npol:
            continue
        m2 = me.copy()
        at = m2.attributes.new('mlm_src_loop', 'INT', 'CORNER')
        at.data.foreach_set('value', np.arange(len(me.loops), dtype=np.int32))
        bm = bmesh.new()
        bm.from_mesh(m2)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in sel.tolist()], context='FACES')
        bm.to_mesh(m2)
        bm.free()
        idx = np.empty(len(m2.loops), np.int32)
        m2.attributes['mlm_src_loop'].data.foreach_get('value', idx)
        m2.attributes.remove(m2.attributes['mlm_src_loop'])
        m2.normals_split_custom_set(cn[idx].tolist())
        m2.name = f'{ob.name}_Part{len(parts) + 1}'
        o2 = bpy.data.objects.new(m2.name, m2)
        context.scene.collection.objects.link(o2)
        o2.matrix_world = ob.matrix_world
        parts.append(o2)
    return parts, True


def _export_file(context, objs, path, fmt):
    vl = context.view_layer
    for o in context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    vl.objects.active = objs[0]
    with context.temp_override(object=objs[0], active_object=objs[0], selected_objects=list(objs),
                               selected_editable_objects=list(objs)):
        if fmt == 'GLB':
            bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True, export_apply=True,
                                      export_yup=True, export_cameras=False, export_lights=False,
                                      export_extras=False, export_animations=False)
        else:
            bpy.ops.export_scene.fbx(filepath=path, use_selection=True, apply_unit_scale=True, axis_forward='-Z',
                                     axis_up='Y', path_mode='COPY', embed_textures=True, mesh_smooth_type='FACE',
                                     object_types={'MESH'}, use_mesh_modifiers=True, bake_anim=False)


def export_objects(context, objs, path, fmt='GLB', split=True, separate=False):
    """export baked objects (split into Roblox-sized parts when needed) -> (written paths, {object: parts})"""
    ext = '.glb' if fmt == 'GLB' else '.fbx'
    path = os.path.splitext(bpy.path.abspath(path))[0] + ext
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    vl = context.view_layer
    prev_sel = list(context.selected_objects)
    prev_act = vl.objects.active
    hidden = [o for o in objs if o.hide_get()]
    for o in hidden:
        o.hide_set(False)
    temp, parts_of = [], {}
    for o in objs:
        ps, made = split_for_roblox(context, o) if split else ([o], False)
        parts_of[o] = ps
        if made:
            temp += ps
    if separate:
        groups = [(o.name, parts_of[o]) for o in objs]
    else:
        groups = [(objs[0].name, [p for o in objs for p in parts_of[o]])]
    written = []
    try:
        for gname, g in groups:
            p = path if not separate else os.path.join(os.path.dirname(path), safe(gname) + ext)
            _export_file(context, g, p, fmt)
            written.append(p)
    finally:
        for o in temp:
            m = o.data
            bpy.data.objects.remove(o)
            if m.users == 0:
                bpy.data.meshes.remove(m)
        for o in hidden:
            o.hide_set(True)
        for o in context.selected_objects:
            o.select_set(False)
        for o in prev_sel:
            if o.name in vl.objects and o.visible_get():
                o.select_set(True)
        vl.objects.active = prev_act
    return written, parts_of


class MLM_OT_export_roblox(Operator):
    bl_idname = 'mlm.export_roblox'
    bl_label = "Export for Roblox"
    bl_description = ("Export the baked copies of the selected objects (.glb or .fbx with textures inside). "
                      "In Roblox Studio: File > Import 3D")
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.glb;*.fbx', options={'HIDDEN'})
    separate: BoolProperty(name="One File per Object", default=False)

    def _targets(self, context):
        out = []
        for o in context.selected_objects:
            cp = o if o.get('mlm_source') else bpy.data.objects.get(o.get('mlm_baked', ''))
            if cp is not None and cp not in out:
                out.append(cp)
        return out

    @classmethod
    def poll(cls, context):
        return any(o.get('mlm_baked') or o.get('mlm_source') for o in context.selected_objects)

    def invoke(self, context, event):
        t = self._targets(context)
        ext = '.glb' if context.scene.mlm_bake.export_format == 'GLB' else '.fbx'
        name = (t[0].name if len(t) == 1 else 'Roblox_Export') + ext
        d = output_dir(context.scene.mlm_bake)
        self.filepath = os.path.join(d, name)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        S = context.scene.mlm_bake
        t = self._targets(context)
        if not t:
            self.report({'ERROR'}, "Bake first (select layered objects and press Bake)")
            return {'CANCELLED'}
        written, parts_of = export_objects(context, t, self.filepath, S.export_format, S.split_parts, self.separate)
        heavy = [o.name for o in t if len(parts_of[o]) == 1 and tri_count(o.data) > ROBLOX_TRIS]
        split = [f"{o.name} -> {len(parts_of[o])} parts" for o in t if len(parts_of[o]) > 1]
        msg = f"Exported {len(t)} object(s) -> {written[0] if len(written) == 1 else os.path.dirname(written[0])}"
        if split:
            msg += " | split for Roblox's 20k limit: " + ', '.join(split)
        if heavy:
            self.report({'WARNING'}, msg + " | over 20k triangles (Roblox will refuse): " + ', '.join(heavy))
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


CLASSES = (MLM_OT_bake, MLM_OT_mask_freeze, MLM_OT_toggle_baked, MLM_OT_export_roblox)
