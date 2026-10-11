# Material Layer Mixer - node tree generation.
#
# A layered material is fully generated from its layer stack (Material.mlm):
#   rebuild(mat)  - regenerates the node tree after a structure change (layers/masks added, types changed ...);
#                   only the layer groups whose structure changed are rebuilt
#   sync(mat)     - only pushes slider values into the existing nodes (fast, no shader recompile)
#
# Every layer delivers the same 6 channels: Color, Metallic, Roughness, Normal (world space shading normal),
# Height (0..1, 0.5 = neutral) and Emission. Layers are mixed bottom -> top by their mask stack.
import math
import bpy
from . import detile

GV = 4                               # version of the shared helper node groups (bump to force a rebuild)
SOCK = {'F': 'NodeSocketFloat', 'C': 'NodeSocketColor', 'V': 'NodeSocketVector'}
CHANNELS = ('color', 'metallic', 'roughness', 'normal', 'height', 'emission')
CH_SOCK = {'color': 'Color', 'metallic': 'Metallic', 'roughness': 'Roughness', 'normal': 'Normal',
           'height': 'Height', 'emission': 'Emission'}
MASK_OPS = {'MIX': 'MIX', 'MULTIPLY': 'MULTIPLY', 'ADD': 'ADD', 'SUBTRACT': 'SUBTRACT', 'MAX': 'LIGHTEN',
            'MIN': 'DARKEN', 'OVERLAY': 'OVERLAY', 'SCREEN': 'SCREEN', 'DIFFERENCE': 'DIFFERENCE'}
SKIP_COPY = {'ShaderNodeOutputMaterial', 'ShaderNodeOutputAOV', 'ShaderNodeOutputLight', 'ShaderNodeOutputWorld',
             'NodeFrame', 'ShaderNodeOutputLineStyle'}
SKIP_PROPS = {'rna_type', 'name', 'label', 'location', 'location_absolute', 'width', 'height', 'dimensions', 'type',
              'inputs', 'outputs', 'internal_links', 'parent', 'select', 'show_options', 'show_preview', 'hide',
              'mute', 'show_texture', 'bl_idname', 'bl_label', 'bl_description', 'bl_icon', 'bl_static_type',
              'bl_width_default', 'bl_width_min', 'bl_width_max', 'bl_height_default', 'bl_height_min',
              'bl_height_max', 'color', 'use_custom_color', 'is_active_output', 'warning_propagation', 'color_tag'}

_suspend = [0]


class suspend:
    """with suspend(): ...  - property updates don't rebuild/sync while operators fill in many values"""
    def __enter__(self):
        _suspend[0] += 1

    def __exit__(self, *a):
        _suspend[0] -= 1


def suspended():
    return _suspend[0] > 0


# ---------------------------------------------------------------------------------------------- small helpers
def sock(node, key, out=False):
    coll = node.outputs if out else node.inputs
    for s in coll:
        if s.identifier == key:
            return s
    for s in coll:
        if s.name == key and s.enabled:
            return s
    return coll[key]


def _same(a, b):
    try:
        if hasattr(a, '__len__'):
            return len(a) == len(b) and all(abs(x - y) < 1e-7 for x, y in zip(a, b))
        return abs(a - b) < 1e-7
    except TypeError:
        return a == b


def setv(sk, v):
    """set a socket default value only when it changed (avoids needless shader updates)"""
    if sk is None or not hasattr(sk, 'default_value'):
        return
    try:
        if not _same(sk.default_value, v):
            sk.default_value = v
    except (TypeError, ValueError, AttributeError):
        pass


def setp(obj, attr, v):
    if obj is not None and getattr(obj, attr, None) != v:
        try:
            setattr(obj, attr, v)
        except (TypeError, ValueError, AttributeError):
            pass


class NB:
    """tiny node builder"""
    def __init__(self, nt, frame=None):
        self.nt = nt
        self.frame = frame

    def n(self, typ, x=0, y=0, name=None, label=None, **props):
        node = self.nt.nodes.new(typ)
        if self.frame is not None:
            node.parent = self.frame
        node.location = (x, y)
        if name:
            node.name = name
        if label:
            node.label = label
        for k, v in props.items():
            setattr(node, k, v)
        return node

    def set(self, sk, v):
        if v is None:
            return
        if isinstance(v, bpy.types.NodeSocket):
            self.nt.links.new(v, sk)
        else:
            sk.default_value = v

    def link(self, a, b):
        return self.nt.links.new(a, b)

    def math(self, op, a, b=0.0, x=0, y=0, clamp=False, name=None, c=None):
        node = self.n('ShaderNodeMath', x, y, name=name, operation=op, use_clamp=clamp)
        self.set(node.inputs[0], a)
        self.set(node.inputs[1], b)
        if c is not None:
            self.set(node.inputs[2], c)
        return node.outputs[0]

    def vmath(self, op, a, b=None, x=0, y=0, c=None, scale=None, name=None):
        node = self.n('ShaderNodeVectorMath', x, y, name=name, operation=op)
        self.set(node.inputs[0], a)
        if b is not None:
            self.set(node.inputs[1], b)
        if c is not None:
            self.set(node.inputs[2], c)
        if scale is not None:
            self.set(node.inputs[3], scale)
        return node.outputs[1] if op in ('DOT_PRODUCT', 'LENGTH', 'DISTANCE') else node.outputs[0]

    def mix(self, dtype, fac, a, b, x=0, y=0, blend='MIX', name=None, clamp=False):
        node = self.n('ShaderNodeMix', x, y, name=name, data_type=dtype, blend_type=blend, clamp_result=clamp)
        suf = {'FLOAT': 'Float', 'VECTOR': 'Vector', 'RGBA': 'Color'}[dtype]
        self.set(sock(node, 'Factor_Float'), fac)
        self.set(sock(node, 'A_' + suf), a)
        self.set(sock(node, 'B_' + suf), b)
        return sock(node, 'Result_' + suf, out=True)

    def value(self, v, x=0, y=0, name=None):
        node = self.n('ShaderNodeValue', x, y, name=name)
        node.outputs[0].default_value = v
        return node.outputs[0]

    def rgb(self, c, x=0, y=0, name=None):
        node = self.n('ShaderNodeRGB', x, y, name=name)
        node.outputs[0].default_value = tuple(c)[:3] + (1.0,) if len(c) == 3 else tuple(c)
        return node.outputs[0]

    def group(self, ng, x=0, y=0, name=None, label=None):
        node = self.n('ShaderNodeGroup', x, y, name=name, label=label)
        node.node_tree = ng
        return node


def _group(name, ins, outs, version=GV, force=False):
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.bl_idname != 'ShaderNodeTree':
        ng.name = name + '.old'
        ng = None
    if ng is not None and ng.get('mlm_ver') == version and not force:
        return ng, None
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    ng.nodes.clear()
    want = [(nm, 'OUTPUT', SOCK[t]) for nm, t in outs] + [(nm, 'INPUT', SOCK[t]) for nm, t, _ in ins]
    have = [(s.name, s.in_out, s.socket_type) for s in ng.interface.items_tree if s.item_type == 'SOCKET']
    if have != want:          # same interface: keep it, so group nodes in layers that are not rebuilt keep their links
        ng.interface.clear()
        for nm, t in outs:
            ng.interface.new_socket(nm, in_out='OUTPUT', socket_type=SOCK[t])
        for nm, t, dv in ins:
            s = ng.interface.new_socket(nm, in_out='INPUT', socket_type=SOCK[t])
            if dv is not None:
                s.default_value = dv
    ng['mlm_ver'] = version
    b = NB(ng)
    gi = b.n('NodeGroupInput', -900, 0)
    go = b.n('NodeGroupOutput', 900, 0)
    return ng, (b, gi, go)


CH_OUTS = [('Color', 'C'), ('Metallic', 'F'), ('Roughness', 'F'), ('Normal', 'V'), ('Height', 'F'), ('Emission', 'C')]


# ---------------------------------------------------------------------------------------------- shared groups
def g_adjust():
    ng, ctx = _group('.MLM Adjust', [
        ('Color', 'C', (0.5, 0.5, 0.5, 1)), ('Metallic', 'F', 0.0), ('Roughness', 'F', 0.5), ('Normal', 'V', None),
        ('Height', 'F', 0.5), ('Emission', 'C', (0, 0, 0, 1)),
        ('Hue', 'F', 0.5), ('Saturation', 'F', 1.0), ('Value', 'F', 1.0), ('Brightness', 'F', 0.0),
        ('Contrast', 'F', 0.0), ('Tint', 'C', (1, 1, 1, 1)), ('Tint Amount', 'F', 0.0),
        ('Roughness Offset', 'F', 0.0), ('Metallic Offset', 'F', 0.0), ('Normal Strength', 'F', 1.0),
        ('Height Scale', 'F', 1.0), ('Height Offset', 'F', 0.0), ('Emission Strength', 'F', 1.0)], CH_OUTS)
    if ctx is None:
        return ng
    b, gi, go = ctx
    I = gi.outputs
    hsv = b.n('ShaderNodeHueSaturation', -500, 300)
    for k in ('Hue', 'Saturation', 'Value', 'Color'):
        b.link(I[k], hsv.inputs[k])
    hsv.inputs['Fac'].default_value = 1.0
    bc = b.n('ShaderNodeBrightContrast', -300, 300)
    b.link(hsv.outputs[0], bc.inputs['Color'])
    b.link(I['Brightness'], bc.inputs['Bright'])
    b.link(I['Contrast'], bc.inputs['Contrast'])
    b.link(b.mix('RGBA', I['Tint Amount'], bc.outputs[0], I['Tint'], -100, 300, blend='MULTIPLY'), go.inputs['Color'])
    b.link(b.math('ADD', I['Roughness'], I['Roughness Offset'], -300, 100, clamp=True), go.inputs['Roughness'])
    b.link(b.math('ADD', I['Metallic'], I['Metallic Offset'], -300, 0, clamp=True), go.inputs['Metallic'])
    geo = b.n('ShaderNodeNewGeometry', -700, -250)
    d = b.vmath('SUBTRACT', I['Normal'], geo.outputs['Normal'], -500, -200)
    r = b.vmath('MULTIPLY_ADD', d, I['Normal Strength'], -300, -200, c=geo.outputs['Normal'])
    b.link(b.vmath('NORMALIZE', r, x=-100, y=-200), go.inputs['Normal'])
    h = b.math('SUBTRACT', I['Height'], 0.5, -500, -450)
    h = b.math('MULTIPLY_ADD', h, I['Height Scale'], -300, -450, c=0.5)
    b.link(b.math('ADD', h, I['Height Offset'], -100, -450), go.inputs['Height'])
    b.link(b.vmath('SCALE', I['Emission'], x=-300, y=-650, scale=I['Emission Strength']), go.inputs['Emission'])
    return ng


def g_heightblend():
    # factor = clamp(((m + w*(m - 1 + h)) - 0.5) * sharp + 0.5)   (w = 0, sharp = 1  ->  factor = mask)
    ng, ctx = _group('.MLM Height Blend', [('Mask', 'F', 1.0), ('Height', 'F', 0.5), ('Influence', 'F', 0.0),
                                           ('Sharpness', 'F', 1.0)], [('Factor', 'F')])
    if ctx is None:
        return ng
    b, gi, go = ctx
    I = gi.outputs
    a = b.math('ADD', I['Mask'], I['Height'], -600, 0)
    a = b.math('SUBTRACT', a, 1.0, -450, 0)
    a = b.math('MULTIPLY', a, I['Influence'], -300, 0)
    t = b.math('ADD', I['Mask'], a, -150, 0)
    t = b.math('SUBTRACT', t, 0.5, 0, 0)
    b.link(b.math('MULTIPLY_ADD', t, I['Sharpness'], 150, 0, clamp=True, c=0.5), go.inputs['Factor'])
    return ng


def g_blend():
    ins = [('Factor', 'F', 1.0), ('Use Metallic', 'F', 1.0), ('Use Roughness', 'F', 1.0), ('Use Normal', 'F', 1.0),
           ('Use Height', 'F', 1.0), ('Use Emission', 'F', 1.0), ('Normal Detail', 'F', 0.0), ('Height Add', 'F', 0.0)]
    for side in ('A', 'B'):
        ins += [(f'{side} Metallic', 'F', 0.0), (f'{side} Roughness', 'F', 0.5), (f'{side} Normal', 'V', None),
                (f'{side} Height', 'F', 0.5), (f'{side} Emission', 'C', (0, 0, 0, 1))]
    ng, ctx = _group('.MLM Blend', ins, [('Metallic', 'F'), ('Roughness', 'F'), ('Normal', 'V'), ('Height', 'F'),
                                         ('Emission', 'C')])
    if ctx is None:
        return ng
    b, gi, go = ctx
    I = gi.outputs
    f = I['Factor']
    fm = b.math('MULTIPLY', f, I['Use Metallic'], -500, 400)
    b.link(b.mix('FLOAT', fm, I['A Metallic'], I['B Metallic'], -300, 400), go.inputs['Metallic'])
    fr = b.math('MULTIPLY', f, I['Use Roughness'], -500, 250)
    b.link(b.mix('FLOAT', fr, I['A Roughness'], I['B Roughness'], -300, 250), go.inputs['Roughness'])
    fe = b.math('MULTIPLY', f, I['Use Emission'], -500, -550)
    b.link(b.mix('RGBA', fe, I['A Emission'], I['B Emission'], -300, -550), go.inputs['Emission'])
    # normals: replace = slerp-ish mix, detail = add the layer's perturbation on top of the normal below
    fn = b.math('MULTIPLY', f, I['Use Normal'], -600, 50)
    geo = b.n('ShaderNodeNewGeometry', -800, -100)
    rep = b.mix('VECTOR', fn, I['A Normal'], I['B Normal'], -400, 100)
    d = b.vmath('SUBTRACT', I['B Normal'], geo.outputs['Normal'], -500, -100)
    det = b.vmath('MULTIPLY_ADD', d, fn, -300, -100, c=I['A Normal'])
    sel = b.mix('VECTOR', I['Normal Detail'], rep, det, -100, 0)
    b.link(b.vmath('NORMALIZE', sel, x=100, y=0), go.inputs['Normal'])
    # height: replace or add (layer height - 0.5 is added on top)
    fh = b.math('MULTIPLY', f, I['Use Height'], -600, -300)
    hrep = b.mix('FLOAT', fh, I['A Height'], I['B Height'], -400, -250)
    hd = b.math('SUBTRACT', I['B Height'], 0.5, -500, -400)
    hadd = b.math('MULTIPLY_ADD', hd, fh, -300, -400, c=I['A Height'])
    b.link(b.mix('FLOAT', I['Height Add'], hrep, hadd, -100, -300), go.inputs['Height'])
    return ng


def g_fill():
    ng, ctx = _group('.MLM Fill', [('Color', 'C', (0.5, 0.5, 0.5, 1)), ('Metallic', 'F', 0.0), ('Roughness', 'F', 0.6),
                                   ('Height', 'F', 0.5), ('Emission', 'C', (0, 0, 0, 1))], CH_OUTS)
    if ctx is None:
        return ng
    b, gi, go = ctx
    for k in ('Color', 'Metallic', 'Roughness', 'Height', 'Emission'):
        b.link(gi.outputs[k], go.inputs[k])
    geo = b.n('ShaderNodeNewGeometry', -300, -300)
    b.link(geo.outputs['Normal'], go.inputs['Normal'])
    return ng


def g_band():
    ng, ctx = _group('.MLM Band', [('Value', 'F', 0.0), ('From', 'F', 0.0), ('To', 'F', 1.0), ('Smooth', 'F', 0.1)],
                     [('Mask', 'F')])
    if ctx is None:
        return ng
    b, gi, go = ctx
    I = gi.outputs
    lo = b.math('SUBTRACT', I['From'], I['Smooth'], -500, 150)
    hi = b.math('ADD', I['To'], I['Smooth'], -500, -150)
    r1 = b.n('ShaderNodeMapRange', -250, 150, interpolation_type='SMOOTHSTEP', clamp=True)
    b.link(I['Value'], r1.inputs['Value'])
    b.link(lo, r1.inputs['From Min'])
    b.link(I['From'], r1.inputs['From Max'])
    r2 = b.n('ShaderNodeMapRange', -250, -150, interpolation_type='SMOOTHSTEP', clamp=True)
    b.link(I['Value'], r2.inputs['Value'])
    b.link(I['To'], r2.inputs['From Min'])
    b.link(hi, r2.inputs['From Max'])
    r2.inputs['To Min'].default_value = 1.0
    r2.inputs['To Max'].default_value = 0.0
    b.link(b.math('MULTIPLY', r1.outputs[0], r2.outputs[0], 0, 0), go.inputs['Mask'])
    return ng


def shared_groups():
    return {'adjust': g_adjust(), 'hb': g_heightblend(), 'blend': g_blend(), 'fill': g_fill(), 'band': g_band()}


# ---------------------------------------------------------------------------------------------- reading materials
def find_output(nt):
    outs = [n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial']
    for n in outs:
        if n.is_active_output and n.target in ('ALL', 'CYCLES', 'EEVEE'):
            return n
    return outs[0] if outs else None


def upstream(sk):
    """socket feeding `sk` (reroutes skipped) or None"""
    seen = 0
    while sk is not None and sk.is_linked and seen < 64:
        seen += 1
        lk = sk.links[0]
        if lk.is_muted:
            return None
        fs = lk.from_socket
        if fs.node.bl_idname == 'NodeReroute':
            sk = fs.node.inputs[0]
            continue
        return fs
    return None


def find_bsdf(node, depth=0):
    if node is None or depth > 10:
        return None
    if node.bl_idname == 'ShaderNodeBsdfPrincipled':
        return node
    if node.bl_idname in ('ShaderNodeMixShader', 'ShaderNodeAddShader'):
        for s in node.inputs:
            if s.type == 'SHADER':
                fs = upstream(s)
                r = find_bsdf(fs.node if fs else None, depth + 1)
                if r is not None:
                    return r
        return None
    if node.outputs and node.outputs[0].type == 'SHADER' and ('Color' in node.inputs or 'Base Color' in node.inputs):
        return node
    return None


def material_bsdf(mat):
    if mat is None or mat.node_tree is None:
        return None, None
    out = find_output(mat.node_tree)
    if out is None:
        return None, None
    fs = upstream(out.inputs['Surface'])
    return out, find_bsdf(fs.node if fs else None)


def _copy_ramp(a, b):
    for k in ('color_mode', 'interpolation', 'hue_interpolation'):
        setp(b, k, getattr(a, k))
    while len(b.elements) > len(a.elements):
        b.elements.remove(b.elements[-1])
    while len(b.elements) < len(a.elements):
        b.elements.new(1.0)
    for ea, eb in zip(sorted(a.elements, key=lambda e: e.position), b.elements):
        eb.position = ea.position
        eb.color = ea.color


def _copy_curves(a, b):
    for k in ('use_clip', 'clip_min_x', 'clip_min_y', 'clip_max_x', 'clip_max_y', 'extend', 'tone'):
        if hasattr(a, k):
            setp(b, k, getattr(a, k))
    try:
        b.black_level = a.black_level
        b.white_level = a.white_level
    except AttributeError:
        pass
    for ca, cb in zip(a.curves, b.curves):
        while len(cb.points) > max(2, len(ca.points)):
            cb.points.remove(cb.points[-1])
        while len(cb.points) < len(ca.points):
            cb.points.new(0.5, 0.5)
        for pa, pb in zip(ca.points, cb.points):
            pb.location = pa.location
            pb.handle_type = pa.handle_type
    b.update()


def copy_node(src, b):
    n = b.nt.nodes.new(src.bl_idname)
    for p in src.bl_rna.properties:
        pid = p.identifier
        if pid in SKIP_PROPS or p.is_readonly or pid.startswith('bl_'):
            continue
        try:
            setattr(n, pid, getattr(src, pid))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    if getattr(src, 'color_ramp', None) is not None:
        _copy_ramp(src.color_ramp, n.color_ramp)
    if isinstance(getattr(src, 'mapping', None), bpy.types.CurveMapping):
        _copy_curves(src.mapping, n.mapping)
    for coll_s, coll_d in ((src.inputs, n.inputs), (src.outputs, n.outputs)):
        for s in coll_s:
            if not hasattr(s, 'default_value'):
                continue
            d = next((x for x in coll_d if x.identifier == s.identifier), None)
            if d is not None and hasattr(d, 'default_value'):
                try:
                    d.default_value = s.default_value
                except (TypeError, ValueError, AttributeError):
                    pass
    n.location = src.location
    n.label = src.label
    n.hide = src.hide
    n.mute = src.mute
    return n


def _out_sock(n, ident):
    return next((s for s in n.outputs if s.identifier == ident), None)


def _in_sock(n, ident):
    return next((s for s in n.inputs if s.identifier == ident), None)


def _fresh_group(name, ins, outs):
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    ng.nodes.clear()
    ng.interface.clear()
    for nm, t in outs:
        ng.interface.new_socket(nm, in_out='OUTPUT', socket_type=SOCK[t])
    for nm, t, dv in ins:
        s = ng.interface.new_socket(nm, in_out='INPUT', socket_type=SOCK[t])
        if dv is not None:
            s.default_value = dv
    return ng


def _rotate_normal_maps(b, gi, nmaps, uv_name, x=400, y=-1100):
    """The source is read with UVs rotated by the group input 'Angle' (layer Rotation + Randomize patch angle), but a
    tangent-space Normal Map node decodes in the unrotated UV frame: turn its output back by -h*Angle around the
    shading normal (h = UV handedness from a Tangent node and a Normal Map probe, so mirrored UVs work).
    Bump / procedural normals already follow the rotated pattern and are left alone."""
    nmaps = [n for n in nmaps if n.space == 'TANGENT' and n.outputs['Normal'].is_linked]
    if not nmaps:
        return
    N = b.n('ShaderNodeNewGeometry', x, y).outputs['Normal']
    tg = b.n('ShaderNodeTangent', x, y - 200, direction_type='UV_MAP')
    pr = b.n('ShaderNodeNormalMap', x, y - 400, space='TANGENT')
    if uv_name:
        tg.uv_map = uv_name
        pr.uv_map = uv_name
    pr.inputs['Color'].default_value = (0.5, 1.0, 0.5, 1.0)        # -> the signed bitangent
    h = b.vmath('DOT_PRODUCT', b.vmath('CROSS_PRODUCT', N, tg.outputs['Tangent'], x + 200, y - 200),
                pr.outputs['Normal'], x + 400, y - 200)
    ang = b.math('MULTIPLY', b.math('SIGN', h, 0.0, x + 600, y - 200), gi.outputs['Angle'], x + 800, y - 200)
    ang = b.math('MULTIPLY', ang, -1.0, x + 1000, y - 200)
    for n in nmaps:
        out = n.outputs['Normal']
        targets = [lk.to_socket for lk in out.links]
        vr = b.n('ShaderNodeVectorRotate', n.location.x + 180, n.location.y - 120, rotation_type='AXIS_ANGLE')
        b.link(out, vr.inputs['Vector'])
        b.link(N, vr.inputs['Axis'])
        b.link(ang, vr.inputs['Angle'])
        for t in targets:
            b.link(vr.outputs[0], t)


def material_to_group(src_mat, name, uv_name='', box=False, box_blend=0.25, inject=True):
    """Copy a (Principled based) material into a node group with one output per channel.
    inject: feed the group's Vector input into all image textures / UV nodes (tiling, triplanar)."""
    ng = _fresh_group(name, [('Vector', 'V', None), ('Angle', 'F', 0.0)], CH_OUTS)
    b = NB(ng)
    gi = b.n('NodeGroupInput', -1400, 0)
    go = b.n('NodeGroupOutput', 900, 0)
    out, bsdf = material_bsdf(src_mat)
    nt = src_mat.node_tree if src_mat else None
    nm = {}
    if nt is not None:
        for s in nt.nodes:
            if s.bl_idname in SKIP_COPY:
                continue
            try:
                nm[s.name] = copy_node(s, b)
            except RuntimeError:
                pass
        for lk in nt.links:
            a, c = nm.get(lk.from_node.name), nm.get(lk.to_node.name)
            if a is None or c is None:
                continue
            fs, ts = _out_sock(a, lk.from_socket.identifier), _in_sock(c, lk.to_socket.identifier)
            if fs is not None and ts is not None:
                nl = ng.links.new(fs, ts)
                nl.is_muted = lk.is_muted
    vec = gi.outputs['Vector']
    if inject:
        for s_name, n in nm.items():
            bid = n.bl_idname
            if bid == 'ShaderNodeTexImage':
                vin = n.inputs['Vector']
                if not vin.is_linked:
                    ng.links.new(vec, vin)
                if box:
                    n.projection = 'BOX'
                    n.projection_blend = box_blend
            elif bid == 'ShaderNodeUVMap' or bid == 'ShaderNodeTexCoord':
                o = n.outputs['UV']
                for lk in list(o.links):
                    ng.links.new(vec, lk.to_socket)
            elif bid == 'ShaderNodeNormalMap' and not n.uv_map and uv_name and not box:
                n.uv_map = uv_name

    def src_of(socket_in_src):
        fs = upstream(socket_in_src) if socket_in_src is not None else None
        if fs is None:
            return None
        cn = nm.get(fs.node.name)
        return _out_sock(cn, fs.identifier) if cn is not None else None

    def chan(name, default, typ):
        s = bsdf.inputs.get(name) if bsdf is not None else None
        o = src_of(s)
        if o is not None:
            return o
        dv = s.default_value if s is not None and hasattr(s, 'default_value') else default
        if typ == 'C':
            return b.rgb(tuple(dv), 600, 300)
        return b.value(float(dv), 600, 300)

    is_emission_shader = bsdf is not None and bsdf.bl_idname == 'ShaderNodeEmission'
    if bsdf is not None and 'Base Color' in bsdf.inputs:
        col = chan('Base Color', (0.6, 0.6, 0.6, 1), 'C')
    else:
        col = chan('Color', (0.6, 0.6, 0.6, 1), 'C')
    b.link(col, go.inputs['Color'])
    b.link(chan('Metallic', 0.0, 'F') if bsdf is not None and 'Metallic' in bsdf.inputs else b.value(0.0, 600, 200),
           go.inputs['Metallic'])
    b.link(chan('Roughness', 0.6, 'F') if bsdf is not None and 'Roughness' in bsdf.inputs else b.value(0.6, 600, 100),
           go.inputs['Roughness'])
    nrm = src_of(bsdf.inputs.get('Normal')) if bsdf is not None else None
    if nrm is None:
        nrm = b.n('ShaderNodeNewGeometry', 600, -100).outputs['Normal']
    b.link(nrm, go.inputs['Normal'])
    # emission = color * strength
    if is_emission_shader:
        ec = col
        es = chan('Strength', 1.0, 'F')
    elif bsdf is not None and 'Emission Color' in bsdf.inputs:
        ec = chan('Emission Color', (0, 0, 0, 1), 'C')
        es = chan('Emission Strength', 0.0, 'F')
    else:
        ec, es = b.rgb((0, 0, 0, 1), 600, -300), 0.0
    b.link(b.vmath('SCALE', ec, x=750, y=-300, scale=es), go.inputs['Emission'])
    # height from the material output's Displacement node (height - midlevel + 0.5)
    h = None
    if out is not None:
        dfs = upstream(out.inputs['Displacement'])
        if dfs is not None and dfs.node.bl_idname == 'ShaderNodeDisplacement':
            dn = dfs.node
            hs = src_of(dn.inputs['Height'])
            mid = dn.inputs['Midlevel'].default_value
            if hs is not None:
                h = b.math('ADD', hs, 0.5 - mid, 600, -450)
    if h is None:
        h = b.value(0.5, 600, -450)
    b.link(h, go.inputs['Height'])
    if inject and not box:                       # last: also re-routes a Normal Map wired straight to the output
        _rotate_normal_maps(b, gi, [n for n in nm.values() if n.bl_idname == 'ShaderNodeNormalMap'
                                    and n.uv_map in ('', uv_name)], uv_name)
    return ng


def texset_group(name, L, uv_name=''):
    ng = _fresh_group(name, [('Vector', 'V', None), ('AO Strength', 'F', 1.0), ('Angle', 'F', 0.0)], CH_OUTS)
    b = NB(ng)
    gi = b.n('NodeGroupInput', -900, 0)
    go = b.n('NodeGroupOutput', 700, 0)
    box = L.projection == 'BOX'
    y = [400]

    def img(image, label):
        n = b.n('ShaderNodeTexImage', -500, y[0], label=label, image=image)
        y[0] -= 280
        if box:
            n.projection = 'BOX'
            n.projection_blend = L.box_blend
        b.link(gi.outputs['Vector'], n.inputs['Vector'])
        return n

    col = img(L.ts_color, 'Color').outputs['Color'] if L.ts_color else b.rgb((0.5, 0.5, 0.5, 1), -300, 400)
    if L.ts_ao:
        ao = img(L.ts_ao, 'AO')
        col = b.mix('RGBA', gi.outputs['AO Strength'], col, ao.outputs['Color'], 100, 400, blend='MULTIPLY')
    b.link(col, go.inputs['Color'])
    if L.ts_rough:
        r = img(L.ts_rough, 'Roughness').outputs['Color']
        if L.ts_gloss:
            r = b.math('SUBTRACT', 1.0, r, 100, 100)
    else:
        r = b.value(0.7, 100, 100)
    b.link(r, go.inputs['Roughness'])
    b.link(img(L.ts_metal, 'Metallic').outputs['Color'] if L.ts_metal else b.value(0.0, 100, 0), go.inputs['Metallic'])
    if L.ts_normal:
        t = img(L.ts_normal, 'Normal')
        nmap = b.n('ShaderNodeNormalMap', 100, -150)
        if uv_name and not box:
            nmap.uv_map = uv_name
        nc = t.outputs['Color']
        if L.ts_normal_dx:
            if hasattr(nmap, 'convention'):
                nmap.convention = 'DIRECTX'
            else:                                    # older Blender: flip green
                sep = b.n('ShaderNodeSeparateColor', -150, -150)
                b.link(nc, sep.inputs[0])
                comb = b.n('ShaderNodeCombineColor', 0, -150)
                b.link(sep.outputs[0], comb.inputs[0])
                b.link(b.math('SUBTRACT', 1.0, sep.outputs[1], -50, -250), comb.inputs[1])
                b.link(sep.outputs[2], comb.inputs[2])
                nc = comb.outputs[0]
        b.link(nc, nmap.inputs['Color'])
        b.link(nmap.outputs['Normal'], go.inputs['Normal'])
        if not box:
            _rotate_normal_maps(b, gi, [nmap], uv_name)
    else:
        b.link(b.n('ShaderNodeNewGeometry', 100, -150).outputs['Normal'], go.inputs['Normal'])
    b.link(img(L.ts_height, 'Height').outputs['Color'] if L.ts_height else b.value(0.5, 100, -400),
           go.inputs['Height'])
    b.link(img(L.ts_emit, 'Emission').outputs['Color'] if L.ts_emit else b.rgb((0, 0, 0, 1), 100, -550),
           go.inputs['Emission'])
    return ng


# ---------------------------------------------------------------------------------------------- stack -> tree
# The material tree is a short chain of layer group nodes: every layer lives in its own node group
# ('.MLM <mid> <uid> L') that takes the channels from below and outputs the mixed channels. Each layer group stores a
# signature of its node structure, so rebuild() only regenerates the layers that really changed - node API calls
# cost time proportional to the tree size, so one big tree gets quadratically slow with many layers.
LV = 4                               # layer group layout version (bump to rebuild every layer group)
TS_SLOTS = ('ts_color', 'ts_normal', 'ts_rough', 'ts_metal', 'ts_ao', 'ts_height', 'ts_emit')
LAYER_INS = [('Color', 'C', (0.5, 0.5, 0.5, 1.0)), ('Metallic', 'F', 0.0), ('Roughness', 'F', 0.5),
             ('Normal', 'V', None), ('Height', 'F', 0.5), ('Emission', 'C', (0.0, 0.0, 0.0, 1.0))]
LAYER_OUTS = CH_OUTS + [('Mask', 'F'), ('Mask Stack', 'F'), ('Layer Color', 'C'), ('Layer Height', 'F')]


def _clash(mat):
    st = mat.mlm
    return st.mid == '' or any(m is not mat and m.mlm.is_stack and m.mlm.mid == st.mid for m in bpy.data.materials)


def ensure_mid(mat):
    """unique id per stack material (duplicated materials get a new one so their node groups don't clash)
    -> (mid, renewed)"""
    import uuid
    st = mat.mlm
    if not _clash(mat):
        return st.mid, False
    copied = st.mid != ''                        # another stack has our id: we are a copy (or the original of one)
    with suspend():
        st.mid = uuid.uuid4().hex[:6]
        for L in st.layers:                      # copies of a material share layer uids too -> renew
            L.uid = uuid.uuid4().hex[:8]
            for M in L.masks:
                M.uid = uuid.uuid4().hex[:8]
                if copied and M.type == 'PAINT' and M.image is not None:
                    M.image = copy_mask_image(M.image)      # painting one copy must not paint the other
    return st.mid, True


def copy_mask_image(img):
    """independent copy of a painted mask, pixels included"""
    import numpy as np
    new = img.copy()
    try:
        px = np.empty(img.size[0] * img.size[1] * 4, np.float32)
        img.pixels.foreach_get(px)
        new.pixels.foreach_set(px)
        new.update()
    except (RuntimeError, ValueError):
        pass
    new['mlm_mask'] = True
    if img.packed_file is not None:
        try:
            new.pack()
        except RuntimeError:
            pass
    return new


def layer_uv(mat, L):
    return L.uv_map or mat.mlm.uv_map


def enabled_masks(L):
    return [M for M in L.masks if M.enabled]


def layer_group_name(mid, L):
    return f'.MLM {mid} {L.uid} L'


def layer_node(mat, L):
    nt = mat.node_tree
    return nt.nodes.get(f'MLM:{L.uid}:layer') if nt is not None else None


def _idname(idb):
    """name + session id: a different datablock with the same name (re-appended material, reloaded image) rebuilds"""
    return f'{idb.name}#{getattr(idb, "session_uid", 0)}' if idb is not None else ''


def layer_sig(mat, L, i):
    """everything a layer group's node structure depends on (plain values are synced, not rebuilt)"""
    p = [str(LV), f'G{GV}D{detile.DT_VER}', str(i == 0), L.source_type, L.projection, layer_uv(mat, L),
         f'rand{L.rand_amount > 0}']
    if L.source_type == 'MATERIAL':
        p.append(_idname(L.material))
    elif L.source_type == 'TEXTURE':
        p += [_idname(getattr(L, k)) for k in TS_SLOTS] + [str(L.ts_normal_dx), str(L.ts_gloss)]
    if i:
        p += [f'{M.uid}:{M.type}:{M.projection}:{M.noise}:{M.axis}:{M.space}:{M.image is not None}:{M.breakup > 0}'
              for M in enabled_masks(L)]
    return '|'.join(p)


def _build_source(b, mat, L, G, x, y, mid):
    u = L.uid
    ng, label = None, ''
    if L.source_type == 'MATERIAL' and L.material is not None and L.material.node_tree is not None:
        ng = material_to_group(L.material, f'.MLM {mid} {u}', layer_uv(mat, L), L.projection == 'BOX', L.box_blend)
        label = L.material.name
    elif L.source_type == 'TEXTURE':
        ng = texset_group(f'.MLM {mid} {u}', L, layer_uv(mat, L))
        label = 'Texture Set'
    adj = b.group(G['adjust'], x + 250, y, name=f'MLM:{u}:adj', label='Adjust')
    if ng is None:
        src = b.group(G['fill'], x, y, name=f'MLM:{u}:fill', label='Fill')
        out = {CH_SOCK[c]: src.outputs[CH_SOCK[c]] for c in CHANNELS}
    else:
        if L.projection == 'BOX':
            vec = b.n('ShaderNodeTexCoord', x - 500, y).outputs['Object']
        else:
            vec = b.n('ShaderNodeUVMap', x - 500, y, name=f'MLM:{u}:uv', uv_map=layer_uv(mat, L)).outputs[0]
        mp = b.n('ShaderNodeMapping', x - 300, y, name=f'MLM:{u}:map')
        b.link(vec, mp.inputs['Vector'])
        rot = b.value(0.0, x - 300, y - 400, name=f'MLM:{u}:rot')       # UV rotation the normal maps must follow
        if L.rand_amount > 0:                    # Randomize: the source is sampled 3x and blended along natural borders
            pre = f'MLM:{u}:dt'
            r = detile.build(b.nt, ng, mp.outputs[0], layer_uv(mat, L),
                             dict(name=pre, box=L.projection == 'BOX', samples=3, normal_fix=False), x=x, y=y)
            co = b.nt.nodes[f'{pre}:coords']
            for k, extra in enumerate((None, 'Angle B', 'Angle C')):
                sn = b.nt.nodes.get(f'{pre}:src{k}')
                if sn is not None and 'Angle' in sn.inputs:
                    b.link(rot if extra is None else b.math('ADD', rot, co.outputs[extra], x - 100, y - 500 - 100 * k),
                           sn.inputs['Angle'])
            out = {CH_SOCK[c]: r[c] for c in CHANNELS}
        else:
            src = b.group(ng, x, y, name=f'MLM:{u}:src', label=label)
            b.link(mp.outputs[0], src.inputs['Vector'])
            b.link(rot, src.inputs['Angle'])
            out = {CH_SOCK[c]: src.outputs[CH_SOCK[c]] for c in CHANNELS}
    for k, s in out.items():
        b.link(s, adj.inputs[k])
    return {c: adj.outputs[CH_SOCK[c]] for c in CHANNELS}


def _coords(b, M, x, y, space='OBJECT'):
    if M.projection == 'UV':
        return b.n('ShaderNodeUVMap', x, y, name=f'MLM:{M.uid}:uv').outputs[0]
    tc = b.n('ShaderNodeTexCoord', x, y)
    return tc.outputs['Object']


def _mask_value(b, mat, L, M, G, hbelow, x, y):
    u = M.uid
    t = M.type
    st = mat.mlm
    if t in ('PAINT', 'IMAGE') and M.image is None:
        return b.value(0.0, x, y)                # nothing painted / picked yet (an empty image node is magenta in Cycles)
    if t == 'PAINT':
        uvn = b.n('ShaderNodeUVMap', x - 250, y, name=f'MLM:{u}:uv', uv_map=st.paint_uv)
        im = b.n('ShaderNodeTexImage', x, y, name=f'MLM:{u}:img', label='Paint Mask', image=M.image,
                 extension='EXTEND', interpolation='Linear')
        vec = uvn.outputs[0]
        if M.breakup > 0:
            n = _breakup_noise(b, M, x - 250, y - 250)
            d = b.vmath('SUBTRACT', n.outputs['Color'], (0.5, 0.5, 0.5), x - 120, y - 200)
            vec = b.vmath('MULTIPLY_ADD', d, (0.004, 0.004, 0.0), x - 60, y - 120, c=vec, name=f'MLM:{u}:brkw')
            b.link(vec, im.inputs['Vector'])
            return b.mix('RGBA', 0.0, im.outputs['Color'], n.outputs['Fac'], x + 150, y - 60, blend='OVERLAY',
                         name=f'MLM:{u}:brk', clamp=True)
        b.link(vec, im.inputs['Vector'])
        return im.outputs['Color']
    if t == 'VERTEX':
        vc = b.n('ShaderNodeVertexColor', x, y, name=f'MLM:{u}:vc', label='Vertex Mask', layer_name=M.attr)
        if M.breakup > 0:
            n = _breakup_noise(b, M, x - 250, y - 250)
            return b.mix('RGBA', 0.0, vc.outputs['Color'], n.outputs['Fac'], x + 150, y - 60, blend='OVERLAY',
                         name=f'MLM:{u}:brk', clamp=True)
        return vc.outputs['Color']
    if t == 'IMAGE':
        vec = _coords(b, M, x - 450, y)
        mp = b.n('ShaderNodeMapping', x - 250, y, name=f'MLM:{u}:map')
        b.link(vec, mp.inputs['Vector'])
        im = b.n('ShaderNodeTexImage', x, y, name=f'MLM:{u}:img', label='Mask Texture', image=M.image)
        if M.projection == 'BOX':
            im.projection = 'BOX'
            im.projection_blend = 0.3
        b.link(mp.outputs[0], im.inputs['Vector'])
        return im.outputs['Color']
    if t == 'NOISE':
        vec = _coords(b, M, x - 700, y)
        vec = b.vmath('SCALE', vec, x=x - 520, y=y, scale=1.0, name=f'MLM:{u}:scale')
        wn = b.n('ShaderNodeTexNoise', x - 520, y - 220, name=f'MLM:{u}:warpn', noise_dimensions='3D')
        wn.inputs['Scale'].default_value = 1.5
        b.link(vec, wn.inputs['Vector'])
        d = b.vmath('SUBTRACT', wn.outputs['Color'], (0.5, 0.5, 0.5), x - 350, y - 200)
        vec = b.vmath('MULTIPLY_ADD', d, (0.2, 0.2, 0.2), x - 200, y, c=vec, name=f'MLM:{u}:warp')
        k = M.noise
        if k == 'CLOUDS':
            tx = b.n('ShaderNodeTexNoise', x, y, name=f'MLM:{u}:tex', noise_dimensions='4D', normalize=True)
            tx.inputs['Scale'].default_value = 1.0
            o = tx.outputs['Fac']
        elif k in ('CELLS', 'SPOTS', 'CRACKS'):
            tx = b.n('ShaderNodeTexVoronoi', x, y, name=f'MLM:{u}:tex', voronoi_dimensions='4D',
                     feature='DISTANCE_TO_EDGE' if k == 'CRACKS' else 'F1')
            tx.inputs['Scale'].default_value = 1.0
            o = tx.outputs['Color'] if k == 'CELLS' else tx.outputs['Distance']
        elif k == 'STRIPES':
            tx = b.n('ShaderNodeTexWave', x, y, name=f'MLM:{u}:tex', wave_type='BANDS', bands_direction=M.axis)
            tx.inputs['Scale'].default_value = 1.0
            o = tx.outputs['Fac']
        else:  # GRAIN
            tx = b.n('ShaderNodeTexWhiteNoise', x, y, name=f'MLM:{u}:tex', noise_dimensions='4D')
            o = tx.outputs['Value']
        b.link(vec, tx.inputs['Vector'])
        return o
    if t == 'SLOPE':
        geo = b.n('ShaderNodeNewGeometry', x - 700, y)
        sep = b.n('ShaderNodeSeparateXYZ', x - 520, y)
        b.link(geo.outputs['Normal'], sep.inputs[0])
        z = b.math('ADD', sep.outputs['Z'], 0.0, x - 360, y, clamp=True)
        a = b.math('ARCCOSINE', z, 0.0, x - 220, y)
        a = b.math('DEGREES', a, 0.0, x - 80, y)
        bd = b.group(G['band'], x + 60, y, name=f'MLM:{u}:band', label='Slope')
        b.link(a, bd.inputs['Value'])
        return bd.outputs[0]
    if t == 'POSITION':
        if M.space == 'WORLD':
            v = b.n('ShaderNodeNewGeometry', x - 500, y).outputs['Position']
        else:
            v = b.n('ShaderNodeTexCoord', x - 500, y).outputs['Object']
        sep = b.n('ShaderNodeSeparateXYZ', x - 300, y)
        b.link(v, sep.inputs[0])
        bd = b.group(G['band'], x, y, name=f'MLM:{u}:band', label='Position')
        b.link(sep.outputs[M.axis], bd.inputs['Value'])
        return bd.outputs[0]
    if t == 'DIRECTION':
        geo = b.n('ShaderNodeNewGeometry', x - 500, y)
        dot = b.vmath('DOT_PRODUCT', geo.outputs['Normal'], (0.0, 0.0, 1.0), x - 300, y, name=f'MLM:{u}:dot')
        mr = b.n('ShaderNodeMapRange', x - 100, y, name=f'MLM:{u}:dir', interpolation_type='SMOOTHSTEP', clamp=True)
        b.link(dot, mr.inputs['Value'])
        return mr.outputs[0]
    if t in ('EDGES', 'CAVITY'):
        ao = b.n('ShaderNodeAmbientOcclusion', x - 200, y, name=f'MLM:{u}:ao', inside=(t == 'EDGES'),
                 only_local=True)
        return b.math('SUBTRACT', 1.0, ao.outputs['AO'], x, y)
    if t == 'HEIGHT':
        return hbelow
    return b.value(0.0, x, y)


def _breakup_noise(b, M, x, y):
    """object-space grunge noise that roughens painted mask edges (no UV seams)"""
    tc = b.n('ShaderNodeTexCoord', x - 200, y)
    n = b.n('ShaderNodeTexNoise', x, y, name=f'MLM:{M.uid}:brkn', noise_dimensions='3D')
    n.inputs['Detail'].default_value = 8.0
    n.inputs['Roughness'].default_value = 0.65
    b.link(tc.outputs['Object'], n.inputs['Vector'])
    return n


def _build_mask(b, mat, L, G, hbelow, x, y):
    """combine the enabled masks top -> bottom (disabled masks drop out, like a hidden mask in Photoshop)"""
    prev = None
    for k, M in enumerate(enabled_masks(L)):
        xx = x - 3000 + k * 900
        yy = y - 520
        v = _mask_value(b, mat, L, M, G, hbelow, xx, yy)
        lv = b.n('ShaderNodeMapRange', xx + 250, yy, name=f'MLM:{M.uid}:lv', label=M.name, clamp=True)
        b.link(v, lv.inputs['Value'])
        comb = b.n('ShaderNodeMix', xx + 450, yy, name=f'MLM:{M.uid}:comb', data_type='RGBA',
                   blend_type=MASK_OPS.get(M.op, 'MIX') if prev is not None else 'MIX', clamp_result=True)
        if prev is not None:
            b.link(prev, sock(comb, 'A_Color'))
        else:
            sock(comb, 'A_Color').default_value = (0, 0, 0, 1)
        b.link(lv.outputs['Result'], sock(comb, 'B_Color'))
        prev = sock(comb, 'Result_Color', out=True)
    return prev


def _layer_group(name):
    """get / create a layer node group with the standard interface (kept when it matches, so links to the group
    node in the material survive) and empty it"""
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.bl_idname != 'ShaderNodeTree':
        ng.name = name + '.old'
        ng = None
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    want = [(n, 'OUTPUT', SOCK[t]) for n, t in LAYER_OUTS] + [(n, 'INPUT', SOCK[t]) for n, t, _ in LAYER_INS]
    have = [(s.name, s.in_out, s.socket_type) for s in ng.interface.items_tree if s.item_type == 'SOCKET']
    if have != want:
        ng.interface.clear()
        for n, t in LAYER_OUTS:
            ng.interface.new_socket(n, in_out='OUTPUT', socket_type=SOCK[t])
        for n, t, dv in LAYER_INS:
            s = ng.interface.new_socket(n, in_out='INPUT', socket_type=SOCK[t])
            if dv is not None:
                s.default_value = dv
    ng.nodes.clear()
    return ng


def build_layer(mat, L, i, G, mid):
    """one layer as a node group: source -> adjust -> mask stack -> blend over the channels from below"""
    ng = _layer_group(layer_group_name(mid, L))
    b = NB(ng)
    gi = b.n('NodeGroupInput', -5000, 800)
    go = b.n('NodeGroupOutput', 300, 0)
    O = go.inputs
    ch = _build_source(b, mat, L, G, -1500, 0, mid)
    b.link(ch['color'], O['Layer Color'])
    b.link(ch['height'], O['Layer Height'])          # the layer's own (adjusted) height: 3D Surface compositing
    if i == 0:
        for c in CHANNELS:
            b.link(ch[c], O[CH_SOCK[c]])
        one = b.value(1.0, 0, -600)
        b.link(one, O['Mask'])
        b.link(one, O['Mask Stack'])
        return ng
    below = {c: gi.outputs[CH_SOCK[c]] for c in CHANNELS}
    m = _build_mask(b, mat, L, G, below['height'], -1500, 0)
    if m is None:                                # no (enabled) mask: the layer covers everything
        m = b.value(1.0, -1250, -520)
    tap = b.n('NodeReroute', -1050, -520, name=f'MLM:{L.uid}:masktap')
    b.link(m, tap.inputs[0])
    hb = b.group(G['hb'], -900, 0, name=f'MLM:{L.uid}:hb', label='Height Blend')
    b.link(tap.outputs[0], hb.inputs['Mask'])
    b.link(ch['height'], hb.inputs['Height'])
    F = b.math('MULTIPLY', hb.outputs[0], 1.0, -700, 0, name=f'MLM:{L.uid}:opacity')
    Fc = b.math('MULTIPLY', F, 1.0, -550, 150, name=f'MLM:{L.uid}:usecol')
    b.link(b.mix('RGBA', Fc, below['color'], ch['color'], -350, 200, blend=L.blend_mode,
                 name=f'MLM:{L.uid}:colmix', clamp=True), O['Color'])
    bl = b.group(G['blend'], -350, -100, name=f'MLM:{L.uid}:blend', label='Blend')
    b.link(F, bl.inputs['Factor'])
    for c in ('metallic', 'roughness', 'normal', 'height', 'emission'):
        b.link(below[c], bl.inputs['A ' + CH_SOCK[c]])
        b.link(ch[c], bl.inputs['B ' + CH_SOCK[c]])
        b.link(bl.outputs[CH_SOCK[c]], O[CH_SOCK[c]])
    b.link(F, O['Mask'])
    b.link(tap.outputs[0], O['Mask Stack'])
    return ng


def cleanup_groups(mid, used):
    pre = f'.MLM {mid} '
    for ng in list(bpy.data.node_groups):
        if ng.name.startswith(pre) and ng.name not in used:
            bpy.data.node_groups.remove(ng)


def _build_main(mat, layers, groups):
    st = mat.mlm
    nt = mat.node_tree
    nt.nodes.clear()
    b = NB(nt)
    x = len(layers) * 260
    out = b.n('ShaderNodeOutputMaterial', x + 1000, 0, name='MLM::out', target='ALL')
    bsdf = b.n('ShaderNodeBsdfPrincipled', x + 600, 0, name='MLM::bsdf')
    b.link(bsdf.outputs[0], out.inputs['Surface'])
    lnodes = []
    for i, (L, ng) in enumerate(zip(layers, groups)):
        node = b.group(ng, i * 260, 0, name=f'MLM:{L.uid}:layer', label=f'{i}: {L.name}')
        node.width = 200
        if lnodes:
            for c in CHANNELS:
                b.link(lnodes[-1].outputs[CH_SOCK[c]], node.inputs[CH_SOCK[c]])
        lnodes.append(node)
    if not lnodes:
        out.is_active_output = True
        return
    taps = {}                                    # channel taps (used by baking and previews)
    for k, c in enumerate(CHANNELS):
        r = b.n('NodeReroute', x + 100, 300 - k * 60, name='MLM::ch:' + c, label=c)
        b.link(lnodes[-1].outputs[CH_SOCK[c]], r.inputs[0])
        taps[c] = r.outputs[0]
    bump = b.n('ShaderNodeBump', x + 300, -300, name='MLM::bump')
    b.link(taps['height'], bump.inputs['Height'])
    b.link(taps['normal'], bump.inputs['Normal'])
    b.link(taps['color'], bsdf.inputs['Base Color'])
    b.link(taps['metallic'], bsdf.inputs['Metallic'])
    b.link(taps['roughness'], bsdf.inputs['Roughness'])
    b.link(bump.outputs[0], bsdf.inputs['Normal'])
    b.link(taps['emission'], bsdf.inputs['Emission Color'])
    bsdf.inputs['Emission Strength'].default_value = 1.0
    if st.disp_method != 'BUMP':
        disp = b.n('ShaderNodeDisplacement', x + 600, -500, name='MLM::disp')
        b.link(taps['height'], disp.inputs['Height'])
        disp.inputs['Midlevel'].default_value = 0.5
        b.link(disp.outputs[0], out.inputs['Displacement'])
    try:
        mat.displacement_method = st.disp_method
    except (AttributeError, TypeError):
        pass
    if st.preview != 'OFF':                      # preview output (one channel / the active layer's mask)
        act = lnodes[min(max(st.active, 0), len(lnodes) - 1)]
        if st.preview == 'MASK':
            pv = act.outputs['Mask']
        elif st.preview == 'LAYER':
            pv = act.outputs['Layer Color']
        elif st.preview == 'NORMAL':
            pv = b.vmath('MULTIPLY_ADD', bump.outputs[0], (0.5, 0.5, 0.5), x + 400, 700, c=(0.5, 0.5, 0.5))
        else:
            pv = taps[st.preview.lower()]
        em = b.n('ShaderNodeEmission', x + 600, 700, name='MLM::preview_emit')
        b.link(pv, em.inputs['Color'])
        po = b.n('ShaderNodeOutputMaterial', x + 1000, 700, name='MLM::preview_out', target='ALL')
        b.link(em.outputs[0], po.inputs['Surface'])
        po.is_active_output = True
    else:
        out.is_active_output = True


def rebuild(mat, force=False):
    """(re)generate the node tree of a layered material from its stack. Only the layer groups whose structure
    changed are regenerated; force=True regenerates all (re-reads the source materials after they were edited)"""
    if mat is None or not mat.mlm.is_stack:
        return
    st = mat.mlm
    if mat.node_tree is None:
        mat.use_nodes = True
    mid, renewed = ensure_mid(mat)
    force = force or renewed
    G = shared_groups()
    layers = list(st.layers)
    used, groups = set(), []
    for i, L in enumerate(layers):
        name = layer_group_name(mid, L)
        sig = layer_sig(mat, L, i)
        ng = bpy.data.node_groups.get(name)
        if force or ng is None or ng.get('mlm_sig') != sig:
            ng = build_layer(mat, L, i, G, mid)
            ng['mlm_sig'] = sig
        groups.append(ng)
        used.add(name)
        used.add(f'.MLM {mid} {L.uid}')
    # the material tree only links the layer groups: rebuilt layer groups keep their interface (links survive), so
    # it is regenerated only when the layer list / preview / displacement changed (saves a shader recompile)
    nt = mat.node_tree
    msig = '|'.join([str(LV), st.preview, st.disp_method, str(st.active) if st.preview in ('MASK', 'LAYER') else '']
                    + [g.name for g in groups])
    intact = is_mlm_node_tree_ok(mat) and all(
        getattr(nt.nodes.get(f'MLM:{L.uid}:layer'), 'node_tree', None) is g for L, g in zip(layers, groups))
    if force or not intact or nt.get('mlm_sig') != msig:
        _build_main(mat, layers, groups)
        nt['mlm_sig'] = msig
    cleanup_groups(mid, used)
    _sync(mat)


# ---------------------------------------------------------------------------------------------- values -> nodes
def _sync_mask(nt, st, L, M, first):
    u = M.uid
    g = nt.nodes.get
    lv = g(f'MLM:{u}:lv')
    if lv is not None:
        lo, hi = M.low, max(M.high, M.low + 1e-4)
        setv(lv.inputs['From Min'], lo)
        setv(lv.inputs['From Max'], hi)
        setv(lv.inputs['To Min'], 1.0 if M.invert else 0.0)
        setv(lv.inputs['To Max'], 0.0 if M.invert else 1.0)
    comb = g(f'MLM:{u}:comb')
    if comb is not None:
        setv(sock(comb, 'Factor_Float'), M.opacity)
        setp(comb, 'blend_type', 'MIX' if first else MASK_OPS.get(M.op, 'MIX'))
    im = g(f'MLM:{u}:img')
    if im is not None:
        setp(im, 'image', M.image)
    uvn = g(f'MLM:{u}:uv')
    if uvn is not None:
        setp(uvn, 'uv_map', st.paint_uv if M.type == 'PAINT' else '')
    brk = g(f'MLM:{u}:brk')
    if brk is not None:
        setv(sock(brk, 'Factor_Float'), M.breakup)
        setv(g(f'MLM:{u}:brkn').inputs['Scale'], M.breakup_scale)
        w = g(f'MLM:{u}:brkw')
        if w is not None:
            k = 0.006 * M.breakup
            setv(w.inputs[1], (k, k, 0.0))
    t = M.type
    if t == 'VERTEX':
        setp(g(f'MLM:{u}:vc'), 'layer_name', M.attr)
    elif t == 'IMAGE':
        mp = g(f'MLM:{u}:map')
        if mp is not None:
            s = M.scale
            setv(mp.inputs['Scale'], (s, s, s))
    elif t == 'NOISE':
        sc = g(f'MLM:{u}:scale')
        if sc is not None:
            setv(sc.inputs[3], M.scale)
        wp = g(f'MLM:{u}:warp')
        if wp is not None:
            setv(wp.inputs[1], (M.warp,) * 3)
        tx = g(f'MLM:{u}:tex')
        if tx is not None:
            for k, v in (('W', M.seed), ('Detail', M.detail), ('Roughness', M.roughness),
                         ('Distortion', M.detail * 2.0 if M.noise == 'STRIPES' else None)):
                if v is not None and k in tx.inputs:
                    setv(tx.inputs[k], v)
            if M.noise == 'STRIPES':
                setp(tx, 'bands_direction', M.axis)
    elif t == 'SLOPE':
        bd = g(f'MLM:{u}:band')
        if bd is not None:
            setv(bd.inputs['From'], M.angle_from)
            setv(bd.inputs['To'], max(M.angle_to, M.angle_from))
            setv(bd.inputs['Smooth'], max(M.angle_smooth, 0.01))
    elif t == 'POSITION':
        bd = g(f'MLM:{u}:band')
        if bd is not None:
            setv(bd.inputs['From'], M.pos_from)
            setv(bd.inputs['To'], max(M.pos_to, M.pos_from))
            setv(bd.inputs['Smooth'], max(M.pos_smooth, 0.001))
    elif t == 'DIRECTION':
        dot = g(f'MLM:{u}:dot')
        if dot is not None:
            v = M.direction
            ln = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
            setv(dot.inputs[1], (v[0] / ln, v[1] / ln, v[2] / ln))
        mr = g(f'MLM:{u}:dir')
        if mr is not None:
            c = 1.0 - 2.0 * M.amount
            s = max(M.dir_smooth, 0.001)
            setv(mr.inputs['From Min'], c - s)
            setv(mr.inputs['From Max'], c + s)
    elif t in ('EDGES', 'CAVITY'):
        ao = g(f'MLM:{u}:ao')
        if ao is not None:
            setv(ao.inputs['Distance'], M.distance)
            setp(ao, 'samples', M.samples)


def _sync_layer(nt, mat, L, i):
    """nt = the layer's node group"""
    st = mat.mlm
    u = L.uid
    g = nt.nodes.get
    adj = g(f'MLM:{u}:adj')
    if adj is not None:
        I = adj.inputs
        setv(I['Hue'], 0.5 + L.hue)
        setv(I['Saturation'], L.saturation)
        setv(I['Value'], L.value)
        setv(I['Brightness'], L.brightness)
        setv(I['Contrast'], L.contrast)
        setv(I['Tint'], tuple(L.tint))
        setv(I['Tint Amount'], L.tint_amount)
        setv(I['Roughness Offset'], L.rough_offset)
        setv(I['Metallic Offset'], L.metal_offset)
        setv(I['Normal Strength'], L.normal_strength)
        setv(I['Height Scale'], L.height_scale)
        setv(I['Height Offset'], L.height_offset)
        setv(I['Emission Strength'], L.emission_strength)
    fill = g(f'MLM:{u}:fill')
    if fill is not None:
        setv(fill.inputs['Color'], tuple(L.fill_color))
        setv(fill.inputs['Metallic'], L.fill_metal)
        setv(fill.inputs['Roughness'], L.fill_rough)
        setv(fill.inputs['Height'], L.fill_height)
        setv(fill.inputs['Emission'], tuple(L.fill_emission))
    srcs = [n for n in (g(f'MLM:{u}:src'), g(f'MLM:{u}:dt:src0'), g(f'MLM:{u}:dt:src1'), g(f'MLM:{u}:dt:src2'))
            if n is not None]
    for src in srcs:
        if 'AO Strength' in src.inputs:
            setv(src.inputs['AO Strength'], L.ts_ao_strength)
    if srcs and L.projection == 'BOX' and srcs[0].node_tree is not None:
        for n in srcs[0].node_tree.nodes:
            if n.bl_idname == 'ShaderNodeTexImage':
                setp(n, 'projection_blend', L.box_blend)
    if g(f'MLM:{u}:dt:blend') is not None:
        box = L.projection == 'BOX'             # box: no UV tangent frame -> no rotation / normal fix
        detile.set_params(nt, dict(amount=L.rand_amount, patch_size=L.rand_patch,
                                   rotation=0.0 if box else math.degrees(L.rand_rotation), offset=L.rand_offset,
                                   scale_var=L.rand_scale, seed=float(L.rand_seed), sharpness=L.rand_sharpness,
                                   hue_var=L.rand_hue, value_var=L.rand_value, var_size=L.rand_var_size,
                                   fix_normals=0.0), name=f'MLM:{u}:dt')
    uvn = g(f'MLM:{u}:uv')
    if uvn is not None:
        setp(uvn, 'uv_map', layer_uv(mat, L))
    mp = g(f'MLM:{u}:map')
    if mp is not None:
        if L.projection == 'BOX':
            s = 1.0 / max(L.tile_size, 1e-4)
            setv(mp.inputs['Scale'], (s, s, s))
        else:
            setv(mp.inputs['Scale'], (L.uv_scale[0], L.uv_scale[1], 1.0))
        setv(mp.inputs['Location'], (L.offset[0], L.offset[1], 0.0))
        setv(mp.inputs['Rotation'], (0.0, 0.0, L.rotation))
    rv = g(f'MLM:{u}:rot')
    if rv is not None:
        setv(rv.outputs[0], 0.0 if L.projection == 'BOX' else L.rotation)
    if i == 0:
        return
    hb = g(f'MLM:{u}:hb')
    if hb is not None:
        setv(hb.inputs['Influence'], L.height_influence)
        setv(hb.inputs['Sharpness'], L.sharpness)
    op = g(f'MLM:{u}:opacity')
    if op is not None:
        setv(op.inputs[1], L.opacity if L.enabled else 0.0)
    uc = g(f'MLM:{u}:usecol')
    if uc is not None:
        setv(uc.inputs[1], 1.0 if L.use_color else 0.0)
    cm = g(f'MLM:{u}:colmix')
    if cm is not None:
        setp(cm, 'blend_type', L.blend_mode)
    bl = g(f'MLM:{u}:blend')
    if bl is not None:
        I = bl.inputs
        setv(I['Use Metallic'], float(L.use_metallic))
        setv(I['Use Roughness'], float(L.use_roughness))
        setv(I['Use Normal'], float(L.use_normal))
        setv(I['Use Height'], float(L.use_height))
        setv(I['Use Emission'], float(L.use_emission))
        setv(I['Normal Detail'], 1.0 if L.normal_mode == 'DETAIL' else 0.0)
        setv(I['Height Add'], 1.0 if L.height_mode == 'ADD' else 0.0)
    for k, M in enumerate(enabled_masks(L)):
        _sync_mask(nt, st, L, M, k == 0)


def _sync(mat):
    st = mat.mlm
    nt = mat.node_tree
    for i, L in enumerate(st.layers):
        n = nt.nodes.get(f'MLM:{L.uid}:layer')
        if n is None or n.node_tree is None:
            continue
        setp(n, 'label', f'{i}: {L.name}')
        _sync_layer(n.node_tree, mat, L, i)
    bump = nt.nodes.get('MLM::bump')
    if bump is not None:
        setv(bump.inputs['Strength'], 0.0 if st.disp_method == 'DISPLACEMENT' else st.bump_strength)
        setv(bump.inputs['Distance'], st.bump_distance)
    disp = nt.nodes.get('MLM::disp')
    if disp is not None:
        setv(disp.inputs['Scale'], st.disp_scale)


def sync(mat):
    """push the slider values into the existing nodes (no shader structure change)"""
    if mat is None or not mat.mlm.is_stack or mat.node_tree is None:
        return
    nt = mat.node_tree
    ok = all(getattr(nt.nodes.get(f'MLM:{L.uid}:layer'), 'node_tree', None) is not None for L in mat.mlm.layers)
    if not ok or _clash(mat):                    # file from an older version / duplicated material / edited by hand
        rebuild(mat)
        return
    _sync(mat)


def is_mlm_node_tree_ok(mat):
    nt = mat.node_tree
    return nt is not None and nt.nodes.get('MLM::out') is not None
