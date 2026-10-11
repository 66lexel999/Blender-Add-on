# Material Layer Mixer - Randomize (anti-tiling): noise-blended multi-sample de-tiling of a layer's source.
#
#   build(nt, src_group, vec_socket, uv_name, params) -> {'color','metallic','roughness','normal','height','emission'}
#
# The layer source group (input 'Vector', outputs Color/Metallic/Roughness/Normal/Height/Emission) is instanced
# 2 or 3 times:  A = vec,  B/C = Mapping(vec; seeded rotation about Z, offset, uniform scale, all scaled by Amount).
# The samples are blended with two large-scale, non-periodic 3D-Perlin masks (an oblique slice of 3D noise, so the
# noise lattice never lines up with the texture tiles) that are sharpened by a height-aware term: near a patch border
# the sample whose texel is higher wins -> crisp, texture-following transitions instead of a ghosted cross-fade.
# There are no cells, so there are no per-cell seams.
#
# Normals: the source's Normal output is a world normal built from the ORIGINAL UV tangent frame. A sample read with
# UVs rotated by +r (UV' = Rz(r) * UV) carries tangent-space detail that has to be turned by -r inside the (T, B)
# plane: x' = x cos r + y sin r, y' = -x sin r + y cos r, z' = z. For an orthonormal frame with B = h * cross(N, T)
# (h = UV handedness, +1 normally, -1 on mirrored UVs) that is exactly a rotation of the world normal about the
# shading normal N by -h*r, so it is done with one Vector Rotate (Axis Angle, axis = Geometry Normal) per sample.
# h = sign(dot(cross(N, T_tangentnode), B_nmap)) where B_nmap is a Normal Map node fed with (0.5, 1, 0.5): that node
# returns exactly the signed bitangent the source's own Normal Map node uses (mirrored UVs / negative scale included).
#
# Shared helper groups (created once, reused by every layer):
#   '.MLM Detile Coords'  in : Vector, Amount, Patch Size, Rotation, Offset, Scale Var, Seed, Var Size
#                 out: Vector B, Vector C, Angle B, Angle C, Masks, Var, Gate
#   '.MLM Detile Blend'   in : A/B/C x (Color, Metallic, Roughness, Normal, Height, Emission), Masks, Var, Angle B, Angle C, Gate,
#                      Amount, Sharpness, Height Blend, Height Contrast, Hue Var, Value Var, Use C, Tangent,
#                      Bitangent, Fix Normals
#                 out: Color, Metallic, Roughness, Normal, Height, Emission, Weights (A, B, C)
# Amount = 0 -> Gate = 0 -> every blend factor is exactly 0 -> outputs are bit-identical to sample A (= no randomizer).
import math
import bpy

DT_VER = 5
COORDS = '.MLM Detile Coords'
BLEND = '.MLM Detile Blend'
CH = ('Color', 'Metallic', 'Roughness', 'Normal', 'Height', 'Emission')
CHK = ('color', 'metallic', 'roughness', 'normal', 'height', 'emission')
CH_T = {'Color': 'C', 'Metallic': 'F', 'Roughness': 'F', 'Normal': 'V', 'Height': 'F', 'Emission': 'C'}
CH_DV = {'Color': (0.5, 0.5, 0.5, 1.0), 'Metallic': 0.0, 'Roughness': 0.5, 'Normal': None, 'Height': 0.5,
         'Emission': (0.0, 0.0, 0.0, 1.0)}
SOCK = {'F': 'NodeSocketFloat', 'C': 'NodeSocketColor', 'V': 'NodeSocketVector'}

# user parameter -> (group, input name, default)
PARAMS = {
    'amount':       ('coords', 'Amount', 1.0),         # 0..1   0 = off (bit-identical to the plain layer)
    'patch_size':   ('coords', 'Patch Size', 1.75),    # in texture repeats (units of the mapped vector)
    'rotation':     ('coords', 'Rotation', 180.0),     # max random rotation, degrees
    'offset':       ('coords', 'Offset', 1.0),         # max random offset, texture repeats
    'scale_var':    ('coords', 'Scale Var', 0.1),      # +- relative scale variation
    'seed':         ('coords', 'Seed', 0.0),
    'var_size':     ('coords', 'Var Size', 8.0),       # size of the hue/value variation blobs, repeats
    'sharpness':    ('blend', 'Sharpness', 48.0),      # 1 = soft cross-fade ... 100 = hard cut
    'height_blend': ('blend', 'Height Blend', 0.06),   # width of the border band where the higher texel wins
    'height_contrast': ('blend', 'Height Contrast', 20.0),  # a height difference of 1/x counts as 'clearly higher'
    'hue_var':      ('blend', 'Hue Var', 0.0),         # per-patch hue variation
    'value_var':    ('blend', 'Value Var', 0.08),      # per-patch brightness variation
    'fix_normals':  ('blend', 'Fix Normals', 1.0),     # 1 = rotate normal detail with the UV rotation
}
BUILD_DEFAULTS = {'samples': 3, 'name': 'DT', 'src_inputs': None, 'box': False, 'normal_fix': True}

K = 3.0                  # noise contrast: Perlin fac (std ~0.087) -> mask term std ~0.26
C_BIAS = 0.113           # pair 2 threshold so sample C covers ~1/3 (z = 0.43 sigma)


# ------------------------------------------------------------------------------------------------- tiny builder
class _B:
    def __init__(self, nt):
        self.nt = nt

    def n(self, typ, x=0, y=0, name=None, label=None, **props):
        node = self.nt.nodes.new(typ)
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

    def math(self, op, a, b=0.0, c=None, x=0, y=0, clamp=False):
        node = self.n('ShaderNodeMath', x, y, operation=op, use_clamp=clamp)
        self.set(node.inputs[0], a)
        self.set(node.inputs[1], b)
        if c is not None:
            self.set(node.inputs[2], c)
        return node.outputs[0]

    def vmath(self, op, a, b=None, c=None, scale=None, x=0, y=0):
        node = self.n('ShaderNodeVectorMath', x, y, operation=op)
        self.set(node.inputs[0], a)
        if b is not None:
            self.set(node.inputs[1], b)
        if c is not None:
            self.set(node.inputs[2], c)
        if scale is not None:
            self.set(node.inputs[3], scale)
        return node.outputs[1] if op in ('DOT_PRODUCT', 'LENGTH', 'DISTANCE') else node.outputs[0]

    def mix(self, dtype, fac, a, b, x=0, y=0):
        node = self.n('ShaderNodeMix', x, y, data_type=dtype, blend_type='MIX', clamp_factor=True)
        suf = {'FLOAT': 'Float', 'VECTOR': 'Vector', 'RGBA': 'Color'}[dtype]
        ins = {s.identifier: s for s in node.inputs}
        self.set(ins['Factor_Float'], fac)
        self.set(ins['A_' + suf], a)
        self.set(ins['B_' + suf], b)
        return {s.identifier: s for s in node.outputs}['Result_' + suf]


def _group(name, ins, outs, force=False):
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.bl_idname != 'ShaderNodeTree':
        ng.name = name + '.old'
        ng = None
    if ng is not None and ng.get('dt_ver') == DT_VER and not force:
        return ng, None
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    ng.nodes.clear()
    want = [(nm, 'OUTPUT', SOCK[t]) for nm, t in outs] + [(nm, 'INPUT', SOCK[t]) for nm, t, _ in ins]
    have = [(s.name, s.in_out, s.socket_type) for s in ng.interface.items_tree if s.item_type == 'SOCKET']
    if have != want:          # same interface: keep it, so the group nodes using it (other layers) keep their links
        ng.interface.clear()
        for nm, t in outs:
            ng.interface.new_socket(nm, in_out='OUTPUT', socket_type=SOCK[t])
        for nm, t, dv in ins:
            s = ng.interface.new_socket(nm, in_out='INPUT', socket_type=SOCK[t])
            if dv is not None:
                s.default_value = dv
    ng['dt_ver'] = DT_VER
    b = _B(ng)
    gi = b.n('NodeGroupInput', -1400, 0)
    go = b.n('NodeGroupOutput', 1400, 0)
    return ng, (b, gi, go)


# ------------------------------------------------------------------------------------------------- '.MLM Detile Coords'
def g_coords(force=False):
    P = PARAMS
    ng, ctx = _group(COORDS, [('Vector', 'V', None)] + [
        (P[k][1], 'F', P[k][2]) for k in ('amount', 'patch_size', 'rotation', 'offset', 'scale_var', 'seed', 'var_size')],
        [('Vector B', 'V'), ('Vector C', 'V'), ('Angle B', 'F'), ('Angle C', 'F'), ('Masks', 'V'), ('Var', 'F'), ('Gate', 'F')],
        force)
    if ctx is None:
        return ng
    b, gi, go = ctx
    I = gi.outputs
    b.link(b.math('GREATER_THAN', I['Amount'], 0.0, x=-1100, y=500), go.inputs['Gate'])
    rot = b.math('RADIANS', b.math('MULTIPLY', I['Amount'], I['Rotation'], x=-1100, y=300), x=-950, y=300)
    off = b.math('MULTIPLY', I['Amount'], I['Offset'], x=-1100, y=150)
    scl = b.math('MULTIPLY', I['Amount'], I['Scale Var'], x=-1100, y=0)
    for k, (sn, sign, w1, w2, y) in enumerate((('B', 1.0, 0.1234, 7.3456, 300), ('C', -1.0, 3.5678, 11.789, -300))):
        wn1 = b.n('ShaderNodeTexWhiteNoise', -800, y + 120, noise_dimensions='1D')
        b.link(b.math('ADD', I['Seed'], w1, x=-950, y=y + 120), wn1.inputs['W'])
        wn2 = b.n('ShaderNodeTexWhiteNoise', -800, y - 120, noise_dimensions='1D')
        b.link(b.math('ADD', I['Seed'], w2, x=-950, y=y - 120), wn2.inputs['W'])
        sep = b.n('ShaderNodeSeparateColor', -600, y - 120)
        b.link(wn2.outputs['Color'], sep.inputs[0])
        # angle = sign * Amount*Rotation * (0.35 .. 1)
        ang = b.math('MULTIPLY_ADD', sep.outputs[0], 0.65, 0.35, x=-400, y=y)
        ang = b.math('MULTIPLY', ang, rot, x=-250, y=y)
        if sign < 0:
            ang = b.math('MULTIPLY', ang, -1.0, x=-100, y=y)
        # scale = 1 + Amount*ScaleVar * (-1 .. 1)
        s = b.math('MULTIPLY_ADD', sep.outputs[1], 2.0, -1.0, x=-400, y=y - 150)
        s = b.math('MULTIPLY_ADD', s, scl, 1.0, x=-250, y=y - 150)
        loc = b.vmath('SCALE', wn1.outputs['Color'], scale=off, x=-400, y=y + 150)
        er = b.n('ShaderNodeCombineXYZ', -100, y - 100)
        b.link(ang, er.inputs['Z'])
        mp = b.n('ShaderNodeMapping', 150, y, vector_type='POINT')
        b.link(I['Vector'], mp.inputs['Vector'])
        b.link(loc, mp.inputs['Location'])
        b.link(er.outputs[0], mp.inputs['Rotation'])
        b.link(s, mp.inputs['Scale'])
        b.link(mp.outputs[0], go.inputs['Vector ' + sn])
        b.link(ang, go.inputs['Angle ' + sn])
    # masks: oblique slice through 3D Perlin fBm, 1 feature ~ Patch Size repeats, domain shifted by the seed
    wn = b.n('ShaderNodeTexWhiteNoise', -800, -800, noise_dimensions='1D')
    b.link(b.math('ADD', I['Seed'], 5.4321, x=-950, y=-800), wn.inputs['W'])
    loc = b.vmath('MULTIPLY_ADD', wn.outputs['Color'], (173.0, 157.0, 131.0), (0.31, 0.47, 0.53), x=-600, y=-800)
    inv = b.math('DIVIDE', 1.0, b.math('MAXIMUM', I['Patch Size'], 0.01, x=-950, y=-1000), x=-800, y=-1000)
    mp = b.n('ShaderNodeMapping', -400, -800, vector_type='POINT')
    b.link(I['Vector'], mp.inputs['Vector'])
    b.link(loc, mp.inputs['Location'])
    mp.inputs['Rotation'].default_value = (0.6109, 0.4363, 0.2793)
    b.link(inv, mp.inputs['Scale'])
    nz = b.n('ShaderNodeTexNoise', -150, -800, noise_dimensions='3D')
    if hasattr(nz, 'noise_type'):
        nz.noise_type = 'FBM'
    if hasattr(nz, 'normalize'):
        nz.normalize = True
    b.link(mp.outputs[0], nz.inputs['Vector'])
    nz.inputs['Scale'].default_value = 1.0
    nz.inputs['Detail'].default_value = 2.0
    nz.inputs['Roughness'].default_value = 0.5
    nz.inputs['Distortion'].default_value = 0.0
    b.link(nz.outputs['Color'], go.inputs['Masks'])
    # macro hue/value variation: same oblique domain, Var Size / Patch Size times larger features
    nv = b.n('ShaderNodeTexNoise', -150, -1100, noise_dimensions='3D')
    if hasattr(nv, 'normalize'):
        nv.normalize = True
    b.link(mp.outputs[0], nv.inputs['Vector'])
    b.link(b.math('DIVIDE', I['Patch Size'], b.math('MAXIMUM', I['Var Size'], 0.01, x=-600, y=-1200), x=-400,
                  y=-1200), nv.inputs['Scale'])
    nv.inputs['Detail'].default_value = 1.0
    nv.inputs['Roughness'].default_value = 0.5
    b.link(nv.outputs['Fac'], go.inputs['Var'])
    return ng


# ------------------------------------------------------------------------------------------------- '.MLM Detile Blend'
def g_blend(force=False):
    P = PARAMS
    ins = []
    for side in 'ABC':
        ins += [(f'{side} {c}', CH_T[c], CH_DV[c]) for c in CH]
    ins += [('Masks', 'V', None), ('Var', 'F', 0.5), ('Angle B', 'F', 0.0), ('Angle C', 'F', 0.0), ('Gate', 'F', 1.0),
            ('Amount', 'F', 1.0)]
    ins += [(P[k][1], 'F', P[k][2]) for k in ('sharpness', 'height_blend', 'height_contrast', 'hue_var', 'value_var')]
    ins += [('Use C', 'F', 1.0), ('Tangent', 'V', None), ('Bitangent', 'V', None),
            ('Fix Normals', 'F', P['fix_normals'][2])]
    outs = [(c, CH_T[c]) for c in CH] + [('Weights', 'V')]
    ng, ctx = _group(BLEND, ins, outs, force)
    if ctx is None:
        return ng
    b, gi, go = ctx
    I = gi.outputs
    # ---- normal fix: rotate B/C normals about the shading normal by -h*angle
    geo = b.n('ShaderNodeNewGeometry', -1200, -1400)
    N = geo.outputs['Normal']
    h = b.vmath('DOT_PRODUCT', b.vmath('CROSS_PRODUCT', N, I['Tangent'], x=-1050, y=-1500), I['Bitangent'],
                x=-900, y=-1500)
    h = b.math('SIGN', h, x=-750, y=-1500)
    h = b.math('MULTIPLY', h, I['Fix Normals'], x=-600, y=-1500)
    h = b.math('MULTIPLY', h, -1.0, x=-450, y=-1500)
    nrm = {}
    for side, y in (('B', -1300), ('C', -1600)):
        vr = b.n('ShaderNodeVectorRotate', -200, y, rotation_type='AXIS_ANGLE')
        b.link(I[f'{side} Normal'], vr.inputs['Vector'])
        b.link(N, vr.inputs['Axis'])
        b.link(b.math('MULTIPLY', I[f'Angle {side}'], h, x=-350, y=y), vr.inputs['Angle'])
        nrm[side] = vr.outputs[0]
    # ---- masks
    sep = b.n('ShaderNodeSeparateXYZ', -1200, 600)
    b.link(I['Masks'], sep.inputs[0])
    S = I['Sharpness']
    hw = I['Height Blend']
    hg = b.math('MULTIPLY', I['Height Contrast'], 0.5, x=-1200, y=300)

    # d = noise term (std ~0.26, biased for pair 2) + HeightBlend * clamp(Contrast * (hb - ha), -1, 1)
    # the height term is clamped so the band where texels compete is HeightBlend wide in noise units no matter how
    # tall a feature is (pebbles of the other sample can't leak far into a patch); t = clamp(0.5 + Sharpness * d)
    def factor(nz, bias, ha, hb, gate, x, y):
        m = b.math('MULTIPLY_ADD', nz, K, -0.5 * K - bias, x=x, y=y)
        c = b.math('MULTIPLY_ADD', b.math('SUBTRACT', hb, ha, x=x, y=y - 150), hg, 0.5, clamp=True,
                   x=x + 150, y=y - 150)
        c = b.math('MULTIPLY_ADD', c, 2.0, -1.0, x=x + 300, y=y - 150)
        d = b.math('MULTIPLY_ADD', c, hw, m, x=x + 450, y=y)
        t = b.math('MULTIPLY_ADD', d, S, 0.5, clamp=True, x=x + 600, y=y)
        return b.math('MULTIPLY', t, gate, x=x + 750, y=y)

    def chan(side, c):
        if c == 'Normal' and side in nrm:
            return nrm[side]
        return I[f'{side} {c}']

    dt = {'Color': 'RGBA', 'Metallic': 'FLOAT', 'Roughness': 'FLOAT', 'Normal': 'VECTOR', 'Height': 'FLOAT',
          'Emission': 'RGBA'}
    t1 = factor(sep.outputs[0], 0.0, I['A Height'], I['B Height'], I['Gate'], -1000, 600)
    ab = {c: b.mix(dt[c], t1, chan('A', c), chan('B', c), x=-200, y=400 - 200 * i) for i, c in enumerate(CH)}
    g2 = b.math('MULTIPLY', I['Gate'], I['Use C'], x=-1000, y=0)
    t2 = factor(sep.outputs[1], C_BIAS, ab['Height'], I['C Height'], g2, -1000, 200)
    res = {c: b.mix(dt[c], t2, ab[c], chan('C', c), x=200, y=400 - 200 * i) for i, c in enumerate(CH)}
    # ---- macro hue / value variation (smooth, separate large noise)
    v = b.math('MULTIPLY_ADD', I['Var'], 2.0 * K, -K, x=-1000, y=1000)
    v = b.math('MULTIPLY', v, I['Amount'], x=-850, y=1000)
    hsv = b.n('ShaderNodeHueSaturation', 600, 600)
    b.link(b.math('MULTIPLY_ADD', v, I['Hue Var'], 0.5, x=-700, y=1100), hsv.inputs['Hue'])
    b.link(b.math('MULTIPLY_ADD', v, I['Value Var'], 1.0, x=-700, y=950), hsv.inputs['Value'])
    hsv.inputs['Saturation'].default_value = 1.0
    b.link(I['Gate'], hsv.inputs['Fac'])
    b.link(res['Color'], hsv.inputs['Color'])
    res['Color'] = hsv.outputs['Color']
    for c in CH:
        b.link(res[c], go.inputs[c])
    # ---- weights (debug / masks for the user): A, B, C
    i1 = b.math('SUBTRACT', 1.0, t1, x=800, y=-800)
    i2 = b.math('SUBTRACT', 1.0, t2, x=800, y=-950)
    w = b.n('ShaderNodeCombineXYZ', 1100, -800)
    b.link(b.math('MULTIPLY', i1, i2, x=950, y=-800), w.inputs[0])
    b.link(b.math('MULTIPLY', t1, i2, x=950, y=-950), w.inputs[1])
    b.link(t2, w.inputs[2])
    b.link(w.outputs[0], go.inputs['Weights'])
    return ng


# ------------------------------------------------------------------------------------------------- public API
def _apply(node, name, v):
    s = node.inputs.get(name)
    if s is None:
        return
    if isinstance(v, bpy.types.NodeSocket):
        node.id_data.links.new(v, s)
    elif not s.is_linked and abs(s.default_value - float(v)) > 1e-7:     # unchanged values: no shader update
        s.default_value = float(v)


def set_params(nt, params, name='DT'):
    """push parameter values into the nodes made by build() (cheap - no rebuild)"""
    nodes = {'coords': nt.nodes.get(f'{name}:coords'), 'blend': nt.nodes.get(f'{name}:blend')}
    for k, v in params.items():
        if k in PARAMS and v is not None:
            g, sname, _ = PARAMS[k]
            if nodes[g] is not None:
                _apply(nodes[g], sname, v)
    if nodes['blend'] is not None and 'amount' in params:
        _apply(nodes['blend'], 'Amount', params['amount'])


def build(nt, src_group, vec_socket, uv_name, params=None, x=0, y=0):
    """Add the randomizer to node tree nt. Returns {'color','metallic','roughness','normal','height','emission'}.
    params: see PARAMS / BUILD_DEFAULTS. Values may be numbers or sockets (sockets get linked).
      samples    : 2 or 3 source evaluations per pixel (structure - needs a rebuild to change)
      name       : node name prefix (nodes are '<name>:coords', '<name>:blend', '<name>:src0..2', ...)
      src_inputs : optional {input name: value} applied to every source instance (e.g. {'AO Strength': 1.0})
      box        : the vector is a 3D (triplanar / BOX projection) coordinate. The source's normals are then not in a
                   UV tangent frame, so the normal fix is off and the default rotation is 0 (offset + scale only);
                   a rotation about object Z only turns the texture on +-Z facing faces."""
    p = dict(BUILD_DEFAULTS)
    p.update({k: v[2] for k, v in PARAMS.items()})
    if params and params.get('box'):
        p.update(rotation=0.0, fix_normals=0.0)
    if params:
        p.update(params)
    ns = int(max(2, min(3, p['samples'])))
    pre = p['name']
    b = _B(nt)
    cg = b.n('ShaderNodeGroup', x - 600, y + 600, name=f'{pre}:coords', label='Detile Coords')
    cg.node_tree = g_coords()
    bg = b.n('ShaderNodeGroup', x + 400, y, name=f'{pre}:blend', label='Detile Blend')
    bg.node_tree = g_blend()
    b.link(vec_socket, cg.inputs['Vector'])
    vecs = [vec_socket, cg.outputs['Vector B'], cg.outputs['Vector C']][:ns]
    for i, (side, v) in enumerate(zip('ABC', vecs)):
        sn = b.n('ShaderNodeGroup', x - 200, y + 300 - 450 * i, name=f'{pre}:src{i}', label=f'Sample {side}')
        sn.node_tree = src_group
        b.link(v, sn.inputs['Vector'])
        for k, val in (p['src_inputs'] or {}).items():
            if k in sn.inputs:
                b.set(sn.inputs[k], val)
        for c in CH:
            b.link(sn.outputs[c], bg.inputs[f'{side} {c}'])
    for o in ('Masks', 'Var', 'Angle B', 'Angle C', 'Gate'):
        b.link(cg.outputs[o], bg.inputs[o])
    bg.inputs['Use C'].default_value = 1.0 if ns == 3 else 0.0
    if not p['box'] and p['normal_fix']:
        # UV frame for the normal fix: T from the Tangent node, the SIGNED bitangent from a Normal Map node fed with
        # (0.5, 1, 0.5) - exactly the B the source's own Normal Map node uses (handles mirrored UVs)
        tg = b.n('ShaderNodeTangent', x + 100, y - 900, name=f'{pre}:tan', direction_type='UV_MAP')
        nm = b.n('ShaderNodeNormalMap', x + 100, y - 1100, name=f'{pre}:bitan', space='TANGENT')
        if uv_name:
            tg.uv_map = uv_name
            nm.uv_map = uv_name
        nm.inputs['Color'].default_value = (0.5, 1.0, 0.5, 1.0)
        b.link(tg.outputs['Tangent'], bg.inputs['Tangent'])
        b.link(nm.outputs['Normal'], bg.inputs['Bitangent'])
    set_params(nt, {k: p[k] for k in PARAMS}, pre)
    return {k: bg.outputs[c] for k, c in zip(CHK, CH)}


def node_count(nt, name='DT'):
    """(nodes added to nt, nodes inside the shared groups)"""
    own = sum(1 for n in nt.nodes if n.name.startswith(name + ':'))
    inner = sum(len(bpy.data.node_groups[g].nodes) for g in (COORDS, BLEND) if g in bpy.data.node_groups)
    return own, inner
