# Material Layer Mixer - 3D Surface: the material's height becomes real geometry on the object
# (non-destructive Subdivision + Geometry Nodes displacement, every setting live), then the detailed surface is baked
# onto a low-poly copy with the triangle budget you choose (normal / color / roughness / metal / AO textures).
import os
import time
import numpy as np
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from . import bake, nodes
from .ops import ctx_object, make_paint_uv, _blur2d, tri_count_cached

MOD_SUB, MOD_DISP = "MLM 3D Detail", "MLM 3D Displace"
DEFAULT_DEPTH = 0.03


# ---------------------------------------------------------------------------------------------- helpers
def materials_of(ob):
    out = []
    for s in ob.material_slots:
        if s.material is not None and s.material.node_tree is not None and s.material not in out:
            out.append(s.material)
    return out


def suggest_depth(ob):
    """relief stored on library materials (Paid Mats 3D) or on the source materials of layered stacks"""
    vals = []
    for m in materials_of(ob):
        if m.get('mlm_relief_depth'):
            vals.append(float(m['mlm_relief_depth']))
        if m.mlm.is_stack:
            for L in m.mlm.layers:
                if L.source_type == 'MATERIAL' and L.material is not None and L.material.get('mlm_relief_depth'):
                    vals.append(float(L.material['mlm_relief_depth']) * max(L.height_scale, 0.0))
    return max(vals) if vals else DEFAULT_DEPTH


def est_tris(ob, levels):
    me = ob.data
    if not len(me.polygons):
        return 0
    return int(len(me.loops) * 2 * 4 ** max(levels - 1, 0))


def suggest_levels(ob, target=1_200_000):
    for lv in range(7, 0, -1):
        if est_tris(ob, lv) <= target:
            return lv
    return 1


def relief_mods(ob):
    return ob.modifiers.get(MOD_SUB), ob.modifiers.get(MOD_DISP)


GN_NAME, GN_VERSION = 'MLM_Relief_Displace', 2


def relief_node_group():
    """Geometry Nodes displacement: the captured height (or, while painting, the shapes without / with the painted
    layer mixed by the live mask) sampled per corner at the capture UV (exact across UV seams), softness = blur
    over the mesh, invert, mid level, depth. All live inputs - nothing derived is stored, so undo / save / reload
    can never show an out-of-date shape."""
    ng = bpy.data.node_groups.get(GN_NAME)
    if ng is not None and ng.get('mlm_ver') == GN_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(GN_NAME, 'GeometryNodeTree')
    ng.nodes.clear()
    ng.interface.clear()
    itf = ng.interface
    itf.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    itf.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    itf.new_socket('Height', in_out='INPUT', socket_type='NodeSocketImage')
    sk = itf.new_socket('UV Map', in_out='INPUT', socket_type='NodeSocketString')
    sk.default_value = 'MLM_UV'
    sk = itf.new_socket('Depth', in_out='INPUT', socket_type='NodeSocketFloat')
    sk.default_value = DEFAULT_DEPTH
    sk = itf.new_socket('Mid Level', in_out='INPUT', socket_type='NodeSocketFloat')
    sk.default_value = 0.5
    itf.new_socket('Invert', in_out='INPUT', socket_type='NodeSocketBool')
    sk = itf.new_socket('Softness', in_out='INPUT', socket_type='NodeSocketInt')
    sk.default_value, sk.min_value, sk.max_value = 0, 0, 1000
    itf.new_socket('Live', in_out='INPUT', socket_type='NodeSocketBool')
    itf.new_socket('Below', in_out='INPUT', socket_type='NodeSocketImage')
    itf.new_socket('Cover', in_out='INPUT', socket_type='NodeSocketImage')
    itf.new_socket('Mask', in_out='INPUT', socket_type='NodeSocketImage')
    sk = itf.new_socket('Mask UV', in_out='INPUT', socket_type='NodeSocketString')
    sk.default_value = 'MLM_UV'
    N, L = ng.nodes.new, ng.links.new
    gi = N('NodeGroupInput'); gi.location = (-1200, 0)
    go = N('NodeGroupOutput'); go.location = (1100, 0)
    na = N('GeometryNodeInputNamedAttribute'); na.data_type = 'FLOAT_VECTOR'; na.location = (-1000, -200)
    L(gi.outputs['UV Map'], na.inputs['Name'])
    nm = N('GeometryNodeInputNamedAttribute'); nm.data_type = 'FLOAT_VECTOR'; nm.location = (-1000, -650)
    L(gi.outputs['Mask UV'], nm.inputs['Name'])

    def sample(img_sock, uv_sock, y):
        tx = N('GeometryNodeImageTexture'); tx.interpolation = 'Linear'; tx.extension = 'EXTEND'
        tx.location = (-800, y)
        L(img_sock, tx.inputs['Image']); L(uv_sock, tx.inputs['Vector'])
        sc = N('FunctionNodeSeparateColor'); sc.location = (-600, y)
        L(tx.outputs['Color'], sc.inputs['Color'])
        return sc.outputs['Red']
    raw = sample(gi.outputs['Height'], na.outputs['Attribute'], -100)
    below = sample(gi.outputs['Below'], na.outputs['Attribute'], -350)
    cover = sample(gi.outputs['Cover'], na.outputs['Attribute'], -500)
    mask = sample(gi.outputs['Mask'], nm.outputs['Attribute'], -650)
    mx = N('ShaderNodeMix'); mx.data_type = 'FLOAT'; mx.clamp_factor = True; mx.location = (-400, -450)
    L(mask, mx.inputs['Factor']); L(below, mx.inputs['A']); L(cover, mx.inputs['B'])
    lv = N('GeometryNodeSwitch'); lv.input_type = 'FLOAT'; lv.location = (-250, -200)
    L(gi.outputs['Live'], lv.inputs['Switch'])
    L(raw, lv.inputs['False']); L(mx.outputs['Result'], lv.inputs['True'])
    ev = N('GeometryNodeFieldOnDomain'); ev.domain = 'CORNER'; ev.data_type = 'FLOAT'; ev.location = (-100, -200)
    L(lv.outputs[0], ev.inputs['Value'])
    inv = N('ShaderNodeMath'); inv.operation = 'SUBTRACT'; inv.inputs[0].default_value = 1.0; inv.location = (100, -350)
    L(ev.outputs['Value'], inv.inputs[1])
    sw = N('GeometryNodeSwitch'); sw.input_type = 'FLOAT'; sw.location = (250, -200)
    L(gi.outputs['Invert'], sw.inputs['Switch'])
    L(ev.outputs['Value'], sw.inputs['False'])
    L(inv.outputs[0], sw.inputs['True'])
    bl = N('GeometryNodeBlurAttribute'); bl.data_type = 'FLOAT'; bl.location = (420, -200)
    L(sw.outputs[0], bl.inputs['Value'])
    L(gi.outputs['Softness'], bl.inputs['Iterations'])
    sub = N('ShaderNodeMath'); sub.operation = 'SUBTRACT'; sub.location = (580, -200)
    L(bl.outputs['Value'], sub.inputs[0]); L(gi.outputs['Mid Level'], sub.inputs[1])
    mul = N('ShaderNodeMath'); mul.operation = 'MULTIPLY'; mul.location = (580, -380)
    L(sub.outputs[0], mul.inputs[0]); L(gi.outputs['Depth'], mul.inputs[1])
    nr = N('GeometryNodeInputNormal'); nr.location = (580, -540)
    vm = N('ShaderNodeVectorMath'); vm.operation = 'SCALE'; vm.location = (740, -380)
    L(nr.outputs['Normal'], vm.inputs[0]); L(mul.outputs[0], vm.inputs['Scale'])
    sp = N('GeometryNodeSetPosition'); sp.location = (900, 0)
    L(gi.outputs['Geometry'], sp.inputs['Geometry']); L(vm.outputs[0], sp.inputs['Offset'])
    L(sp.outputs['Geometry'], go.inputs['Geometry'])
    ng['mlm_ver'] = GN_VERSION
    return ng


def _inputs(mod):
    ng = mod.node_group
    return {it.name: it.identifier for it in ng.interface.items_tree
            if getattr(it, 'item_type', 'SOCKET') == 'SOCKET' and it.in_out == 'INPUT'} if ng else {}


def sync_mods(R):
    ob = R.id_data
    sub, disp = relief_mods(ob)
    live = bool(R.live and R.live_below and R.live_cover and R.live_mask)
    if sub is not None:
        sub.subdivision_type = R.shape
        sub.render_levels = R.levels
        sub.levels = max(1, R.levels - 1) if live else R.levels   # lighter while painting live
    if disp is not None and disp.type == 'NODES' and disp.node_group is not None:
        ids = _inputs(disp)
        vals = {'Height': R.raw, 'UV Map': R.uv or 'MLM_UV', 'Depth': float(R.depth), 'Mid Level': float(R.midlevel),
                'Invert': bool(R.invert), 'Softness': int(round(R.softness * 100)), 'Live': live,
                'Below': R.live_below if live else None, 'Cover': R.live_cover if live else None,
                'Mask': R.live_mask if live else None, 'Mask UV': R.live_uv or R.uv or 'MLM_UV'}
        for k, v in vals.items():
            if k in ids:
                try:
                    if disp[ids[k]] != v:
                        disp[ids[k]] = v
                except (KeyError, TypeError):
                    disp[ids[k]] = v
        ob.update_tag()


def upd_mods(self, context):
    sync_mods(self)


def upd_image(self, context):
    sync_mods(self)


# ---------------------------------------------------------------------------------------------- settings
TEX_ITEMS = [('256', "256", ""), ('512', "512", ""), ('1024', "1024", ""), ('2048', "2048", ""),
             ('4096', "4096", "Roblox maximum")]
LODS = (('ultra', "Ultra"), ('high', "High"), ('mid', "Mid"), ('low', "Low"))


class MLM_Relief(PropertyGroup):
    enabled: BoolProperty(default=False)
    raw: PointerProperty(type=bpy.types.Image)
    image: PointerProperty(type=bpy.types.Image)
    texture: PointerProperty(type=bpy.types.Texture)
    uv: StringProperty()
    height_source: EnumProperty(name="Height From", items=[
        ('AUTO', "Auto", "The material's height map; its color brightness when it has none"),
        ('HEIGHT', "Height Map", "The material's height channel (layered: all layers mixed)"),
        ('COLOR', "Color Brightness", "Bright = raised, dark = sunken (materials without a height map)")],
        default='AUTO')
    height_res: EnumProperty(name="Height Detail", items=[
        ('1024', "1024", "Fast"), ('2048', "2048", "Recommended"), ('4096', "4096", "Very fine detail, slower")],
        default='2048', description="Resolution the material's height is captured at")
    depth: FloatProperty(name="Depth", default=DEFAULT_DEPTH, soft_min=-0.3, soft_max=0.3, unit='LENGTH',
                         precision=3, update=upd_mods,
                         description="How far the surface is pushed out, from the lowest to the highest point")
    midlevel: FloatProperty(name="Mid Level", default=0.5, min=0.0, max=1.0, subtype='FACTOR', update=upd_mods,
                            description="Height that stays where the original surface was (lower = everything "
                                        "grows outward, higher = carved inward)")
    softness: FloatProperty(name="Softness", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_image,
                            description="Smooths small, sharp details (rounder stones, softer grass)")
    invert: BoolProperty(name="Invert", default=False, update=upd_image,
                         description="Swap raised and sunken parts")
    levels: IntProperty(name="Detail", default=5, min=1, max=8, update=upd_mods,
                        description="Subdivision level: more = finer geometry (and many more triangles)")
    cover_size: FloatProperty(name="Cover Smoothing", default=0.02, min=0.0, soft_max=0.1, subtype='FACTOR',
                              description="'Cover' layers follow the shape below smoothed by this much (part of the "
                                          "texture): bigger = only large shapes (rocks) are followed")
    live: BoolProperty(default=False)
    live_below: PointerProperty(type=bpy.types.Image)
    live_cover: PointerProperty(type=bpy.types.Image)
    live_mask: PointerProperty(type=bpy.types.Image)
    live_uv: StringProperty()
    auto_update: BoolProperty(name="Update After Painting", default=True,
                              description="Re-shape the 3D surface automatically when you press 'Done Painting'")
    shape: EnumProperty(name="Shape", items=[
        ('SIMPLE', "Keep Shape", "Subdivide without rounding: hard edges and the silhouette stay as modelled"),
        ('CATMULL_CLARK', "Smooth Shape", "Rounds the base shape while subdividing (organic objects)")],
        default='SIMPLE', update=upd_mods)
    # game LODs: each level = its own triangle budget + texture size, baked from the detailed object
    lod_ultra: BoolProperty(name="Ultra", default=True, description="Bake the Ultra level")
    tris_ultra: IntProperty(name="Ultra Triangles", default=20000, min=50, soft_max=20000, max=2_000_000,
                            description="Triangle budget (Roblox: max 20,000 per mesh - more is split on export)")
    tex_ultra: EnumProperty(name="Ultra Texture Size", items=TEX_ITEMS, default='4096')
    lod_high: BoolProperty(name="High", default=True, description="Bake the High level")
    tris_high: IntProperty(name="High Triangles", default=10000, min=50, soft_max=20000, max=2_000_000)
    tex_high: EnumProperty(name="High Texture Size", items=TEX_ITEMS, default='2048')
    lod_mid: BoolProperty(name="Mid", default=True, description="Bake the Mid level")
    tris_mid: IntProperty(name="Mid Triangles", default=4000, min=50, soft_max=20000, max=2_000_000)
    tex_mid: EnumProperty(name="Mid Texture Size", items=TEX_ITEMS, default='1024')
    lod_low: BoolProperty(name="Low", default=True, description="Bake the Low level")
    tris_low: IntProperty(name="Low Triangles", default=1200, min=50, soft_max=20000, max=2_000_000)
    tex_low: EnumProperty(name="Low Texture Size", items=TEX_ITEMS, default='512')


# ---------------------------------------------------------------------------------------------- height capture
def geo_layers(mat):
    """a stack's layers that shape the 3D surface, bottom -> top (the base layer is the starting shape)"""
    return [L for i, L in enumerate(mat.mlm.layers) if i > 0 and L.enabled and L.use_height and L.relief_geo != 'OFF']


def _store(R, attr, name, v, pack):
    img = getattr(R, attr)
    h, w = v.shape
    if img is None or tuple(img.size) != (w, h):
        if img is not None and img.users <= 1:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, w, h, alpha=False, float_buffer=True)
        img.colorspace_settings.name = 'Non-Color'
        img['mlm_relief'] = True
        setattr(R, attr, img)
    px = np.repeat(v[..., None], 4, axis=2).astype(np.float32)
    px[..., 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    img.update()
    if pack:
        img.file_format = 'OPEN_EXR'
        try:
            img.pack()
        except RuntimeError:
            pass
    return img


def compose(H0, layers, blur_px):
    """shape = base height, then each 3D layer by its mode: COVER follows the (smoothed) shape below and swaps in
    the layer's own relief, REPLACE takes the layer's height, ADD stacks the relief on top"""
    H = H0.astype(np.float32).copy()
    for mode, t, m in layers:
        if mode == 'REPLACE':
            H += (t - H) * m
        elif mode == 'ADD':
            H += (t - 0.5) * m
        else:
            base = _blur2d(H, blur_px) if blur_px >= 1 else H
            H += (base + (t - 0.5) - H) * m
    return H


def _norm(R, arrs, reset):
    """height range fixed at the first capture: painting / adding layers later never rescales the whole surface
    (values may leave 0..1 - the displacement is linear)"""
    src = R.get('source_req', '')
    if reset or 'norm' not in R or R.get('norm_src') != src:
        allv = np.concatenate([a.ravel()[::7] for a in arrs])
        lo, hi = float(np.percentile(allv, 0.5)), float(np.percentile(allv, 99.5))
        if hi - lo < 0.02:                                 # (almost) flat material: keep the plain 0..1 height scale
            lo, hi = 0.0, 1.0
        R['norm'] = [lo, hi]
        R['norm_src'] = src
    lo, hi = R['norm']
    flat = all(float(a.std()) < 1e-4 for a in arrs)
    return [((a - lo) / (hi - lo)).astype(np.float32) for a in arrs], flat


def bake_height(context, ob, R, report, live=None, reset=False):
    """capture the material shape on a non-overlapping UV -> R.raw. Layered materials: the base height, then every
    layer with '3D Geometry' composited by its mode. live = (material, layer) about to be painted: instead store the
    shapes without / with that layer (R.live_below / R.live_cover), mixed by the live mask in Geometry Nodes."""
    S = context.scene.mlm_bake
    me = ob.data
    mats = materials_of(ob)
    if not mats:
        report({'ERROR'}, f"{ob.name} has no material")
        return False
    uv = R.uv or S.uv_name or 'MLM_UV'
    for m in mats:
        if m.mlm.is_stack and me.uv_layers.get(m.mlm.paint_uv) is not None:
            uv = m.mlm.paint_uv
    if me.uv_layers.get(uv) is None:
        made = make_paint_uv(context, ob, uv, 'AUTO')
        report({'INFO'}, f"{ob.name}: created UV '{uv}' ({made}) for the height capture")
    R.uv = uv
    R['source_req'] = R.height_source
    src = R.height_source
    if src == 'AUTO':
        src = 'HEIGHT' if any(bake.channel_source(m, 'height')[0] is not None for m in mats) else 'COLOR'
    res = int(R.height_res)
    stacks = [m for m in mats if m.mlm.is_stack]
    for m in stacks:                                       # layer groups from older versions lack 'Layer Height'
        for L in m.mlm.layers:
            nd = nodes.layer_node(m, L)
            if nd is not None and nd.outputs.get('Layer Height') is None:
                nodes.rebuild(m, force=True)
                break
    geo = [(m, L) for m in stacks for L in geo_layers(m)] if src == 'HEIGHT' else []
    upper = [L for m in stacks for i, L in enumerate(m.mlm.layers) if i > 0 and L.use_height]
    passes = [('base', None, None)]
    for m, L in geo:
        passes.append(('t', m, L))
        if live is None or L.uid != live[1].uid:
            passes.append(('m', m, L))
    tmp = bpy.data.images.new('MLM_tmp_height', res, res, alpha=False, float_buffer=True)
    tmp.colorspace_settings.name = 'Non-Color'
    sub, disp = relief_mods(ob)
    shown = [(m, m.show_viewport, m.show_render) for m in (sub, disp) if m is not None]
    for m, *_ in shown:                                    # capture on the undisplaced surface
        m.show_viewport = m.show_render = False
    prev_uv = me.uv_layers.active.name if me.uv_layers.active else None
    hidden = ob.hide_get(), ob.hide_render
    ob.hide_set(False)
    ob.hide_render = False
    for L in upper:                                        # base pass: only the base layers' height
        L.use_height = False
    iso = bake.Isolate(context.scene, {ob})
    rs = bake._RenderState(context, ob, 4)
    states = {m: bake._MatState(m, tmp) for m in mats}
    got = {}
    try:
        me.uv_layers.active = me.uv_layers[uv]
        for kind, pm, pL in passes:
            for m, st_ in states.items():
                if kind == 'base':
                    st_.push_emit('height' if src == 'HEIGHT' else 'color')
                elif m is pm:
                    st_.push_source(nodes.layer_node(m, pL).outputs['Layer Height' if kind == 't' else 'Mask'])
                else:
                    st_.push_source(None, (0.5, 0.5, 0.5, 1.0) if kind == 't' else (0.0, 0.0, 0.0, 1.0))
            try:
                bake.run_bake(context, ob, type='EMIT', uv_layer=uv, margin=16, margin_type='EXTEND',
                              use_clear=True, target='IMAGE_TEXTURES')
            finally:
                for st_ in states.values():
                    st_.pop_emit()
            a = bake._pixels(tmp)
            if kind == 'base' and src != 'HEIGHT':
                got[(kind, None)] = a[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            else:
                got[(kind, pL.uid if pL is not None else None)] = a[..., 0].copy()
    finally:
        for st_ in states.values():
            st_.restore()
        rs.restore()
        iso.restore()
        for L in upper:
            L.use_height = True
        for m, v, r in shown:
            m.show_viewport, m.show_render = v, r
        if prev_uv and me.uv_layers.get(prev_uv):
            me.uv_layers.active = me.uv_layers[prev_uv]
        ob.hide_set(hidden[0])
        ob.hide_render = hidden[1]
        bpy.data.images.remove(tmp)
    H0 = got[('base', None)]
    blur_px = int(round(R.cover_size * res))
    base = bake.safe(ob.name)
    if live is not None:
        lm, lL = live
        stack_l = [(L.relief_geo, got[('t', L.uid)], got[('m', L.uid)] if L.uid != lL.uid else None) for m, L in geo]
        below = compose(H0, [x for x in stack_l if x[2] is not None], blur_px)
        cover = compose(H0, [(md, t, mk if mk is not None else np.ones_like(t)) for md, t, mk in stack_l], blur_px)
        (below, cover), flat = _norm(R, (below, cover), False)
        _store(R, 'live_below', f'MLM_LiveBelow_{base}', below, False)
        _store(R, 'live_cover', f'MLM_LiveCover_{base}', cover, False)
        R['source'] = src
        return True
    H = compose(H0, [(L.relief_geo, got[('t', L.uid)], got[('m', L.uid)]) for m, L in geo], blur_px)
    (v,), flat = _norm(R, (H,), reset)
    _store(R, 'raw', f'MLM_HeightRaw_{base}', v, True)      # the capture lives inside the .blend, written once
    R['mean'] = float(v.mean())
    R['source'] = src
    if flat:
        report({'WARNING'}, f"{ob.name}: the material has no height variation - try 'Height From: Color Brightness'")
    return True


def stop_live(R):
    R.live = False
    for attr in ('live_below', 'live_cover'):
        img = getattr(R, attr)
        if img is not None:
            setattr(R, attr, None)
            if img.users == 0:
                bpy.data.images.remove(img)
    R.live_mask = None
    sync_mods(R)


_LIVE = {'ob': '', 't': 0.0, 'dirty': False}


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    """every paint stroke updates the mask image in the depsgraph: remember it (the timer re-shapes after a pause)"""
    if not _LIVE['ob']:
        return
    ob = bpy.data.objects.get(_LIVE['ob'])
    mask = ob.mlm_relief.live_mask if ob is not None else None
    if mask is None:
        return
    for u in depsgraph.updates:
        if isinstance(u.id, bpy.types.Image) and u.id.original == mask:
            _LIVE['dirty'] = True
            _LIVE['t'] = time.time()
            return


def _redraw():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()


def _live_tick():
    ob = bpy.data.objects.get(_LIVE['ob'])
    if ob is None or not ob.mlm_relief.live:
        _LIVE['ob'] = ''
        return None
    if ob.mode != 'TEXTURE_PAINT':                         # left painting without 'Done': exact shape now
        _LIVE['ob'] = ''
        try:
            win = bpy.context.window_manager.windows[0]
            area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None)
            with bpy.context.temp_override(window=win, area=area, object=ob, active_object=ob,
                                           selected_objects=[ob]):
                bpy.ops.mlm.relief_make()
        except Exception as e:
            print('MLM: 3D surface update after painting failed:', e)
        return None
    if _LIVE['dirty'] and time.time() - _LIVE['t'] > 0.2:   # a short pause after a stroke: re-shape
        _LIVE['dirty'] = False
        ob.update_tag()
        _redraw()
    return 0.15


def live_refresh(ob):
    """re-evaluate the live 3D surface now (after a stroke)"""
    ob.update_tag()


def start_live(context, ob, mat, L, M, report):
    """painting a '3D Geometry' layer: capture the shapes without / with it, then the Geometry Nodes mix them by
    the mask being painted - the 3D surface follows the brush"""
    R = ob.mlm_relief
    if not R.enabled or M.image is None or L.relief_geo == 'OFF':
        return False
    t = time.time()
    if not bake_height(context, ob, R, report, live=(mat, L)):
        return False
    R.live_mask = M.image
    R.live_uv = mat.mlm.paint_uv
    R.live = True
    ensure_mods(ob, R)
    _LIVE.update(ob=ob.name, dirty=False)
    if not bpy.app.timers.is_registered(_live_tick):
        bpy.app.timers.register(_live_tick, first_interval=0.3)
    report({'INFO'}, f"Live 3D painting on '{L.name}' ({L.relief_geo.title()}) - ready in {time.time() - t:.1f}s")
    return True


def _drop_legacy(R):
    """1.2.0 kept a processed copy of the height for a Displace modifier - it could go stale on undo"""
    t = R.texture
    if t is not None:
        R.texture = None
        if t.users == 0:
            bpy.data.textures.remove(t)
    img = R.image
    if img is not None:
        R.image = None
        if img.users == 0:
            bpy.data.images.remove(img)


def ensure_mods(ob, R):
    sub, disp = relief_mods(ob)
    if sub is None:
        sub = ob.modifiers.new(MOD_SUB, 'SUBSURF')
        sub.uv_smooth = 'NONE'                             # UVs stay linear: geometry matches the captured height
        sub.boundary_smooth = 'PRESERVE_CORNERS'
        sub.show_only_control_edges = True
    if disp is not None and disp.type != 'NODES':
        ob.modifiers.remove(disp)
        disp = None
    if disp is None:
        disp = ob.modifiers.new(MOD_DISP, 'NODES')
    disp.node_group = relief_node_group()
    _drop_legacy(R)
    # keep the pair last in the stack, Subdivision before Displace
    for name in (MOD_SUB, MOD_DISP):
        idx = ob.modifiers.find(name)
        if idx != len(ob.modifiers) - 1:
            with bpy.context.temp_override(object=ob):
                bpy.ops.object.modifier_move_to_index(modifier=name, index=len(ob.modifiers) - 1)
    sync_mods(R)


def migrate_all(*_):
    """files saved with 1.2.0: switch their 3D surfaces to the Geometry Nodes displacement (uses the stored
    capture, so a stale processed height can never come back)"""
    n = 0
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or ob.library is not None:
            continue
        R = ob.mlm_relief
        if R.live and not (R.live_below and R.live_cover and R.live_mask):
            R.live = False
        if not R.enabled or R.raw is None:
            continue
        sub, disp = relief_mods(ob)
        if disp is None or disp.type != 'NODES' or disp.node_group is None or \
                disp.node_group.get('mlm_ver') != GN_VERSION:
            ensure_mods(ob, R)
            n += 1
        else:
            sync_mods(R)
    return n


class MLM_OT_relief_make(Operator):
    bl_idname = 'mlm.relief_make'
    bl_label = "Make 3D Surface"
    bl_description = ("Turn the material's height into real geometry on this object (non-destructive: "
                      "Subdivision + Displace modifiers). Run again after painting / changing the material")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = ctx_object(context)
        return ob is not None and not ob.get('mlm_source') and bool(materials_of(ob))

    def execute(self, context):
        ob = ctx_object(context)
        R = ob.mlm_relief
        first = not R.enabled
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if R.live:
            stop_live(R)
        try:
            if not bake_height(context, ob, R, self.report, reset=first):
                return {'CANCELLED'}
        except RuntimeError as e:
            self.report({'ERROR'}, f"Height capture failed: {e}")
            return {'CANCELLED'}
        if first:
            R.depth = suggest_depth(ob)
            R.midlevel = round(float(R.get('mean', 0.5)), 3)
            R.levels = suggest_levels(ob)
            flat = [p.use_smooth for p in ob.data.polygons]
            if not all(flat):
                ob.data['mlm_relief_flat'] = [int(not s) for s in flat]
                for p in ob.data.polygons:
                    p.use_smooth = True
        ensure_mods(ob, R)
        R.enabled = True
        self.report({'INFO'}, f"{ob.name}: 3D surface ready (~{est_tris(ob, R.levels):,} triangles, "
                              f"height from {R.get('source', '').lower()})")
        return {'FINISHED'}


class MLM_OT_relief_remove(Operator):
    bl_idname = 'mlm.relief_remove'
    bl_label = "Remove 3D Surface"
    bl_description = "Remove the 3D surface modifiers and the captured height (the mesh itself was never changed)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = ctx_object(context)
        return ob is not None and ob.mlm_relief.enabled

    def execute(self, context):
        ob = ctx_object(context)
        R = ob.mlm_relief
        for m in relief_mods(ob):
            if m is not None:
                ob.modifiers.remove(m)
        _drop_legacy(R)
        for attr in ('raw',):
            img = getattr(R, attr)
            if img is not None:
                setattr(R, attr, None)
                if img.users == 0:
                    bpy.data.images.remove(img)
        flat = ob.data.get('mlm_relief_flat')
        if flat is not None and len(flat) == len(ob.data.polygons):
            for p, f in zip(ob.data.polygons, flat):
                p.use_smooth = not f
            del ob.data['mlm_relief_flat']
        R.enabled = False
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- LOD bake
def _unique_mats(me):
    out = []
    for m in me.materials:
        if m is not None and m.node_tree is not None and m not in out:
            out.append(m)
    return out


def source_of(ob):
    """the detailed original of a baked LOD / copy (or the object itself)"""
    if ob is not None and ob.get('mlm_source'):
        return bpy.data.objects.get(ob['mlm_source'])
    return ob


def lod_objects(src):
    return [o for o in (bpy.data.objects.get(n) for n in src.get('mlm_lods', [])) if o is not None]


def _high_copy(context, ob):
    """the object exactly as it looks now (3D surface / modifiers included), as a temporary mesh object"""
    dg = context.evaluated_depsgraph_get()
    hi_me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    for i, s in enumerate(ob.material_slots):
        if i < len(hi_me.materials) and s.material is not None:
            hi_me.materials[i] = s.material
    hi = bpy.data.objects.new(ob.name + '_MLM_high', hi_me)
    context.scene.collection.objects.link(hi)
    hi.matrix_world = ob.matrix_world
    return hi


def _make_low(context, hi, tris, name, coll):
    """decimated copy of the high mesh (or the same mesh when it is already within budget), fresh UVs"""
    hi_me = hi.data
    lo_me = hi_me.copy()
    lo = bpy.data.objects.new(name, lo_me)
    coll.objects.link(lo)
    lo.matrix_world = hi.matrix_world
    n = bake.tri_count(hi_me)
    if n > tris:
        dm = lo.modifiers.new('dec', 'DECIMATE')
        dm.ratio = max(tris / n, 1e-4)
        dm.use_collapse_triangulate = True
        dg = context.evaluated_depsgraph_get()
        nm = bpy.data.meshes.new_from_object(lo.evaluated_get(dg), depsgraph=dg)
        lo.modifiers.clear()
        lo.data = nm
        bpy.data.meshes.remove(lo_me)
        lo_me = nm
        for p in lo_me.polygons:                       # the normal map carries the high's shading
            p.use_smooth = True
    lo_me.name = name
    for u in list(lo_me.uv_layers):
        lo_me.uv_layers.remove(u)
    for a in list(lo_me.color_attributes):
        lo_me.color_attributes.remove(a)
    make_paint_uv(context, lo, 'UVMap', 'SMART')
    lo_me.uv_layers.active = lo_me.uv_layers['UVMap']
    lo_me.uv_layers['UVMap'].active_render = True
    return lo


def _cage(context, hi, lo):
    """how far the low mesh strays from the high surface -> ray reach of the bake (world units)"""
    from mathutils.bvhtree import BVHTree
    dg = context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(hi, dg)
    me = lo.data
    n = len(me.polygons)
    cen = np.empty(n * 3, np.float32)
    me.polygons.foreach_get('center', cen)
    cen = cen.reshape(-1, 3)
    if n > 20000:
        cen = cen[np.linspace(0, n - 1, 20000).astype(np.int64)]
    d = []
    for c in cen.tolist():
        hit = tree.find_nearest(c)
        if hit[0] is not None:
            d.append(hit[3])
    size = max(lo.dimensions) or 1.0
    reach = float(np.percentile(d, 99.5)) if d else 0.01 * size
    scale = max(abs(v) for v in lo.matrix_world.to_scale()) or 1.0
    return (reach * 1.5 + 0.002 * size) * scale


def bake_high_to_low(context, hi, lo, S, res, ext, prefix):
    """selected-to-active bake of the high object's materials onto lo's 'UVMap' -> {channel: image}"""
    scene = context.scene
    chans = [c for c in bake.CH if getattr(S, c[2]) and c[0] != 'height']
    imgs = {c[0]: bake._image(f'{prefix}_{c[1]}', res, c[3],
                              (0.5, 0.5, 1.0, 1.0) if c[0] == 'normal' else (0, 0, 0, 1)) for c in chans}
    tm = bpy.data.materials.new('MLM_TMP_lowbake')
    tm.use_nodes = True
    tnode = tm.node_tree.nodes.new('ShaderNodeTexImage')
    lo.data.materials.clear()
    lo.data.materials.append(tm)
    hi_mats = _unique_mats(hi.data)
    iso = bake.Isolate(scene, {hi, lo})                 # the original coincides with the high copy: hidden too
    rs = bake._RenderState(context, lo, S.samples)
    states = [bake._MatState(m, next(iter(imgs.values()))) for m in hi_mats]
    size = max(lo.dimensions) or 1.0
    common = dict(use_selected_to_active=True, cage_extrusion=ext, max_ray_distance=ext * 2.0 + 0.001 * size,
                  uv_layer='UVMap', margin=S.margin, margin_type='EXTEND', use_clear=True, target='IMAGE_TEXTURES')

    def target(img):
        tnode.image = img
        for n in tm.node_tree.nodes:
            n.select = False
        tnode.select = True
        tm.node_tree.nodes.active = tnode
    try:
        for ch, suf, _, cs, kind in chans:
            target(imgs[ch])
            if kind == 'NORMAL':
                bake.run_bake(context, lo, [hi, lo], type='NORMAL', normal_space='TANGENT', normal_r='POS_X',
                              normal_g='POS_Y', normal_b='POS_Z', **common)
            else:
                for s in states:
                    s.push_emit(ch)
                try:
                    bake.run_bake(context, lo, [hi, lo], type='EMIT', **common)
                finally:
                    for s in states:
                        s.pop_emit()
        if S.ao_into_color and 'color' in imgs and S.ao_strength > 0:
            ao = bake._image(f'{prefix}_AO_tmp', res, 'Non-Color', (1, 1, 1, 1))
            target(ao)
            for s in states:
                s.push_plain()
            world = scene.world
            tmp_world = None
            if world is None:
                tmp_world = scene.world = bpy.data.worlds.new('MLM_tmp_world')
                world = tmp_world
            prev = world.light_settings.distance, scene.cycles.samples
            world.light_settings.distance = S.ao_distance
            scene.cycles.samples = max(prev[1], 64)
            try:
                bake.run_bake(context, lo, [hi, lo], type='AO', **common)
            finally:
                for s in states:
                    s.pop_emit()
                world.light_settings.distance, scene.cycles.samples = prev
                if tmp_world is not None:
                    scene.world = None
                    bpy.data.worlds.remove(tmp_world)
            a = _blur2d(bake._pixels(ao)[..., 0], 1)
            c = bake._pixels(imgs['color'])
            c[..., :3] *= (1.0 - S.ao_strength * (1.0 - np.clip(a, 0.0, 1.0)))[..., None]
            imgs['color'].pixels.foreach_set(c.ravel())
            imgs['color'].update()
            bpy.data.images.remove(ao)
    finally:
        for s in states:
            s.restore()
        rs.restore()
        iso.restore()
        lo.data.materials.clear()
        bpy.data.materials.remove(tm)
    bake.drop_empty_emission(imgs)
    return imgs


def remove_lods(src):
    names = list(src.get('mlm_lods', []))
    b = src.get('mlm_baked', '')
    if b and b not in names:
        names.append(b)
    for n in names:
        o = bpy.data.objects.get(n)
        if o is not None and o.get('mlm_source') == src.name:
            m = o.data
            bpy.data.objects.remove(o)
            if m is not None and m.users == 0:
                bpy.data.meshes.remove(m)
    for k in ('mlm_lods', 'mlm_baked'):
        if k in src:
            del src[k]


def show_lod(src, name):
    """show one LOD (or the original when name == '') in viewport and render"""
    for o in lod_objects(src):
        vis = o.name == name
        o.hide_set(not vis)
        o.hide_render = not vis
    src.hide_set(bool(name))
    src.hide_render = bool(name)


class MLM_OT_bake_lods(Operator):
    bl_idname = 'mlm.bake_lods'
    bl_label = "Bake LODs"
    bl_description = ("Make the ticked levels (Ultra / High / Mid / Low): each one a simplified copy with your "
                      "triangle budget and texture size, with the detailed surface baked into normal / color / "
                      "roughness / metal maps. Ready for Roblox graphics-quality levels")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = source_of(ctx_object(context))
        return ob is not None and bool(materials_of(ob))

    def execute(self, context):
        ob = source_of(ctx_object(context))
        R = ob.mlm_relief
        S = context.scene.mlm_bake
        scene = context.scene
        levels = [(k, label, getattr(R, 'tris_' + k), int(getattr(R, 'tex_' + k))) for k, label in LODS
                  if getattr(R, 'lod_' + k)]
        if not levels:
            self.report({'ERROR'}, "Tick at least one level (Ultra / High / Mid / Low)")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        remove_lods(ob)
        ob.hide_set(False)
        ob.hide_render = False
        base = bake.safe(ob.name)
        parent = bpy.data.collections.get('MLM Baked')
        if parent is None:
            parent = bpy.data.collections.new('MLM Baked')
            scene.collection.children.link(parent)
        cname = f'{ob.name} LODs'
        coll = bpy.data.collections.get(cname)
        if coll is None:
            coll = bpy.data.collections.new(cname)
            parent.children.link(coll)
        hi = _high_copy(context, ob)
        hi_tris = bake.tri_count(hi.data)
        made, notes = [], []
        wm = context.window_manager
        wm.progress_begin(0, len(levels))
        try:
            for i, (k, label, tris, res) in enumerate(levels):
                lo = _make_low(context, hi, tris, f'{ob.name}_{label}', coll)
                ext = _cage(context, hi, lo)
                imgs = bake_high_to_low(context, hi, lo, S, res, ext, f'{base}_{label}')
                folder = os.path.join(bake.output_dir(S), base + '_LODs', label)
                for ch, img in imgs.items():
                    suf = next(c[1] for c in bake.CH if c[0] == ch)
                    bake._save(img, os.path.join(folder, f'{base}_{label}_{suf}.png'))
                lo.data.materials.append(bake.baked_material(f'{base}_{label}_Baked', imgs))
                lo['mlm_source'] = ob.name
                lo['mlm_lod'] = label
                made.append(lo)
                notes.append(f"{label} {bake.tri_count(lo.data):,} tris / {res}px")
                wm.progress_update(i + 1)
        except RuntimeError as e:
            self.report({'ERROR'}, f"LOD bake failed: {e}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()
            hm = hi.data
            bpy.data.objects.remove(hi)
            if hm.users == 0:
                bpy.data.meshes.remove(hm)
            if made:
                ob['mlm_lods'] = [o.name for o in made]
                ob['mlm_baked'] = made[0].name
        if S.hide_original:
            show_lod(ob, made[0].name)
        for o in context.selected_objects:
            o.select_set(False)
        made[0].select_set(True)
        context.view_layer.objects.active = made[0]
        self.report({'INFO'}, f"{ob.name} ({hi_tris:,} tris): " + " | ".join(notes) +
                    f"  -> {os.path.join(bake.output_dir(S), base + '_LODs')}")
        return {'FINISHED'}


class MLM_OT_lod_show(Operator):
    bl_idname = 'mlm.lod_show'
    bl_label = "Show LOD"
    bl_description = "Show this level (the others and the original are hidden)"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        src = source_of(ctx_object(context))
        if src is None:
            return {'CANCELLED'}
        show_lod(src, self.name)
        target = bpy.data.objects.get(self.name) if self.name else src
        for o in context.selected_objects:
            o.select_set(False)
        if target is not None and target.name in context.view_layer.objects:
            target.select_set(True)
            context.view_layer.objects.active = target
        return {'FINISHED'}


class MLM_OT_export_lods(Operator):
    bl_idname = 'mlm.export_lods'
    bl_label = "Export LODs"
    bl_description = ("One file per level (<name>_Ultra.glb, _High, _Mid, _Low) into the bake folder. Levels over "
                      "20,000 triangles are split into parts for Roblox")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        src = source_of(ctx_object(context))
        return src is not None and bool(lod_objects(src))

    def execute(self, context):
        src = source_of(ctx_object(context))
        S = context.scene.mlm_bake
        ext = '.glb' if S.export_format == 'GLB' else '.fbx'
        folder = os.path.join(bake.output_dir(S), bake.safe(src.name) + '_LODs')
        written, split = [], []
        for o in lod_objects(src):
            p = os.path.join(folder, f"{bake.safe(src.name)}_{o.get('mlm_lod', o.name)}{ext}")
            w, parts_of = bake.export_objects(context, [o], p, S.export_format, S.split_parts)
            written += w
            if len(parts_of[o]) > 1:
                split.append(f"{o.get('mlm_lod', o.name)} -> {len(parts_of[o])} parts")
        msg = f"Exported {len(written)} LOD file(s) -> {folder}"
        if split:
            msg += " | split for Roblox's 20k limit: " + ', '.join(split)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- UI
def draw_lod_switch(layout, src):
    lods = lod_objects(src)
    if not lods:
        return
    row = layout.row(align=True)
    row.label(text="Show:")
    op = row.operator('mlm.lod_show', text="Original", depress=not src.hide_get())
    op.name = ''
    for o in lods:
        op = row.operator('mlm.lod_show', text=o.get('mlm_lod', o.name), depress=not o.hide_get())
        op.name = o.name
    row = layout.row()
    row.scale_y = 1.3
    row.operator('mlm.export_lods', icon='EXPORT')


class MLM_PT_relief(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Mixer'
    bl_idname = 'MLM_PT_relief'
    bl_label = "3D Surface + LOD Bake"
    bl_parent_id = 'MLM_PT_main'
    bl_order = 5

    @classmethod
    def poll(cls, context):
        ob = ctx_object(context)
        return ob is not None and not ob.get('mlm_source')

    def draw_header(self, context):
        self.layout.label(icon='MOD_DISPLACE')

    def draw(self, context):
        l = self.layout
        ob = ctx_object(context)
        R = ob.mlm_relief
        S = context.scene.mlm_bake
        if not materials_of(ob):
            l.label(text="Give the object a material first", icon='INFO')
            return
        sub, disp = relief_mods(ob)
        if not R.enabled or disp is None:
            box = l.box()
            box.label(text="Flat material? Its height can become", icon='INFO')
            box.label(text="real 3D geometry (skip this when the")
            box.label(text="mesh already has the detail).")
            col = l.column(align=True)
            col.use_property_split = True
            col.prop(R, 'height_source')
            col.prop(R, 'height_res')
            row = l.row()
            row.scale_y = 1.3
            row.operator('mlm.relief_make', icon='MOD_DISPLACE')
            src_tris = tri_count_cached(ob)
        else:
            col = l.column(align=True)
            col.use_property_split = True
            col.prop(R, 'depth')
            col.prop(R, 'midlevel', slider=True)
            col.prop(R, 'softness', slider=True)
            col.prop(R, 'invert')
            col.separator()
            col.prop(R, 'levels')
            col.prop(R, 'shape')
            src_tris = est_tris(ob, R.levels)
            l.label(text=f"3D surface: ~{src_tris:,} triangles", icon='ERROR' if src_tris > 4_000_000 else 'MESH_DATA')
            col = l.column(align=True)
            col.use_property_split = True
            col.prop(R, 'height_source')
            col.prop(R, 'height_res')
            row = l.row(align=True)
            row.operator('mlm.relief_make', text="Update from Material", icon='FILE_REFRESH')
            row.operator('mlm.relief_remove', text="", icon='X')
            if any(m.mlm.is_stack for m in materials_of(ob)):
                col = l.column(align=True)
                col.use_property_split = True
                col.prop(R, 'cover_size', slider=True)
                l.prop(R, 'auto_update')
                if R.live:
                    l.label(text="Live 3D painting: shape follows the brush", icon='BRUSH_DATA')
                else:
                    l.label(text="Layer '3D Geometry' mode shapes it", icon='INFO')
        box = l.box()
        box.label(text=f"LODs for Roblox (source: {src_tris:,} tris)", icon='MOD_DECIM')
        grid = box.grid_flow(row_major=True, columns=3, even_columns=False, align=True)
        grid.label(text="Level")
        grid.label(text="Triangles")
        grid.label(text="Texture")
        for k, label in LODS:
            on = getattr(R, 'lod_' + k)
            grid.prop(R, 'lod_' + k, text=label)
            sub = grid.row(align=True)
            sub.active = on
            sub.prop(R, 'tris_' + k, text="")
            sub = grid.row(align=True)
            sub.active = on
            sub.prop(R, 'tex_' + k, text="")
        over = [label for k, label in LODS if getattr(R, 'lod_' + k) and getattr(R, 'tris_' + k) > 20000]
        if over:
            box.label(text=f"{', '.join(over)}: over 20k, split into parts on export", icon='INFO')
        big = [label for k, label in LODS if getattr(R, 'lod_' + k) and getattr(R, 'tris_' + k) >= src_tris]
        if big:
            box.label(text=f"{', '.join(big)}: source has only {src_tris:,}", icon='INFO')
        grid = box.grid_flow(columns=4, align=True)
        for k in ('use_color', 'use_normal', 'use_roughness', 'use_metallic'):
            grid.prop(S, k, toggle=True)
        row = box.row(align=True)
        row.prop(S, 'ao_into_color')
        row.prop(S, 'use_emission', toggle=True)
        row = box.row()
        row.scale_y = 1.5
        row.operator('mlm.bake_lods', icon='RENDER_STILL')
        draw_lod_switch(box, ob)


CLASSES = (MLM_Relief, MLM_OT_relief_make, MLM_OT_relief_remove, MLM_OT_bake_lods, MLM_OT_lod_show,
           MLM_OT_export_lods, MLM_PT_relief)


@bpy.app.handlers.persistent
def _on_load(*_):
    try:
        migrate_all()
    except Exception as e:                                   # never block opening a file
        print('MLM: 3D surface migration failed:', e)


def _first_run():
    _on_load()
    return None


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.mlm_relief = PointerProperty(type=MLM_Relief)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.timers.register(_first_run, first_interval=0.2)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if bpy.app.timers.is_registered(_live_tick):
        bpy.app.timers.unregister(_live_tick)
    del bpy.types.Object.mlm_relief
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
