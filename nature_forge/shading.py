# Nature Forge - materials. Every map of a texture set is hooked up automatically:
#
#   NF Tex <set> (TRI)  triplanar sampling at the surface position the Geometry Nodes stored BEFORE the texture
#                       displacement (nf_uvw / nf_w): colour, roughness, normal, AO sit exactly on the real bumps the
#                       height map made. No UVs needed, no seams, no stretching on cliffs.
#   NF Tex <set> (UV)   the same for UV mapped geometry (leaf cards)
#   NF Mask             auto-paint mask: top / steep, cavities, edges, height, noise breakup, crack fill + painted
#   NF Mix Layers       layer over layer, every channel
#
# The material is generated from a small layer list stored on it (mat['nf_layers']); slider values live in the
# node inputs and survive rebuilds (adding / removing layers, swapping texture sets).
import json
import os
import uuid
import bpy
from . import nodekit as nk
from . import library
from . import presets

SV = 4                                   # shared shader group version
TS_V = 5                                 # texture set group version
CH_OUT = [('Color', 'COLOR'), ('Roughness', 'FLOAT'), ('Metallic', 'FLOAT'), ('Normal', 'VEC'), ('Height', 'FLOAT'),
          ('Alpha', 'FLOAT')]
ADJUST = [('Hue', 'FLOAT', 0.5, 0.0, 1.0, 'FACTOR'), ('Saturation', 'FLOAT', 1.0, 0.0, 2.0, None),
          ('Value', 'FLOAT', 1.0, 0.0, 3.0, None), ('Tint', 'COLOR', (1.0, 1.0, 1.0, 1.0), None, None, None),
          ('Tint Amount', 'FLOAT', 0.0, 0.0, 1.0, 'FACTOR'), ('Roughness +/-', 'FLOAT', 0.0, -1.0, 1.0, None),
          ('Metallic +/-', 'FLOAT', 0.0, -1.0, 1.0, None)]
MASK_IN = [('Top', 0.0, -1.0, 1.0, "Up-facing (+) or steep / overhanging (-) surfaces"),
           ('Cavity', 0.0, 0.0, 1.0, "Cracks, crevices and dents"),
           ('Edges', 0.0, 0.0, 1.0, "Sharp outer edges and ridges"),
           ('Height', 0.0, -1.0, 1.0, "High (+) or low (-) parts of the object"),
           ('Combine', 0.0, 0.0, 1.0, "0 = any of the features adds the layer (OR), 1 = all of them are needed "
                                      "(AND, e.g. snow only where it is high AND flat)"),
           ('Coverage', 0.5, 0.0, 1.0, "How much of the surface the layer covers"),
           ('Sharpness', 0.5, 0.0, 1.0, "Soft (0) or hard (1) transition"),
           ('Noise', 0.3, 0.0, 2.0, "Breaks up the mask edge with noise"),
           ('Noise Scale', 2.0, 0.0, 100.0, "Noise patches per metre"),
           ('Crack Fill', 0.0, 0.0, 2.0, "Fills the low parts of the layer below first (needs a height map)"),
           ('Detail', 0.5, 0.0, 1.0, "0 = follow the big shape, 1 = every small bump (Top mask)"),
           ('Opacity', 1.0, 0.0, 1.0, "")]


# ---------------------------------------------------------------------------------------------- shared groups
def _new(name, version):
    return nk.new_group(name, 'ShaderNodeTree', version=version)


def tri_weights_group():
    ng, t = _new('NF Tri Weights', SV)
    if t is None:
        return ng
    n = t.inp('Normal', 'VEC')
    blend = t.inp('Blend', 'FLOAT', 0.35, 0.0, 1.0)
    k = t.mrange(blend, 0.0, 1.0, 14.0, 1.0)
    x, y, z = t.sep(t.vmath('ABSOLUTE', n))
    wx, wy, wz = (t.math('POWER', c, k) for c in (x, y, z))
    s = t.math('MAXIMUM', t.add(t.add(wx, wy), wz), 1e-6)
    t.out('Weights', 'VEC', t.xyz(t.div(wx, s), t.div(wy, s), t.div(wz, s)))
    t.layout()
    return ng


def mask_group():
    ng, t = _new('NF Mask', SV)
    if t is None:
        return ng
    up_r = t.inp('Up Shape', 'FLOAT', 0.0)
    up_d = t.inp('Up Detail', 'FLOAT', 0.0)
    curv = t.inp('Curvature', 'FLOAT', 0.5)
    hgt = t.inp('Object Height', 'FLOAT', 0.5)
    rest = t.inp('Position', 'VEC')
    base_h = t.inp('Base Height', 'FLOAT', 0.5)
    paint = t.inp('Paint', 'FLOAT', 0.5)
    w = {}
    for name, dv, lo, hi, desc in MASK_IN:
        w[name] = t.inp(name, 'FLOAT', dv, lo, hi, desc=desc, subtype='FACTOR' if lo == 0.0 and hi == 1.0 else None)
    up = t.clamp01(t.mix(w['Detail'], up_r, up_d))

    def signed(weight, v):
        return t.mul(t.math('ABSOLUTE', weight), t.mix(t.math('GREATER_THAN', weight, 0.0), t.sub(1.0, v), v))
    top = signed(w['Top'], up)
    cav = t.clamp01(t.mul(t.sub(curv, 0.5), 2.0))
    edg = t.clamp01(t.mul(t.sub(0.5, curv), 2.0))
    hh = signed(w['Height'], t.clamp01(hgt))
    s = t.add(t.add(top, t.mul(w['Cavity'], cav)), t.add(t.mul(w['Edges'], edg), hh))
    wsum = t.add(t.add(t.math('ABSOLUTE', w['Top']), w['Cavity']), t.add(w['Edges'], t.math('ABSOLUTE', w['Height'])))
    s = t.div(s, t.math('MAXIMUM', wsum, 1e-3))
    # AND: weighted geometric product of the features (a feature with weight 0 counts as 1)

    def fpow(v, weight):
        return t.math('POWER', t.math('MAXIMUM', v, 1e-4), t.math('ABSOLUTE', weight))
    up_s = t.mix(t.math('GREATER_THAN', w['Top'], 0.0), t.sub(1.0, up), up)
    h_s = t.mix(t.math('GREATER_THAN', w['Height'], 0.0), t.sub(1.0, t.clamp01(hgt)), t.clamp01(hgt))
    prod = t.mul(t.mul(fpow(up_s, w['Top']), fpow(cav, w['Cavity'])), t.mul(fpow(edg, w['Edges']), fpow(h_s, w['Height'])))
    prod = t.math('POWER', prod, t.div(1.0, t.math('MAXIMUM', t.div(wsum, 1.0), 1.0)))
    s = t.mix(w['Combine'], s, t.mul(prod, t.compare_gt(wsum, 1e-3)))
    nz = t.noise(rest, w['Noise Scale'], 4.0, 0.55)[0]
    s = t.madd(t.sub(nz, 0.5), w['Noise'], s)
    th = t.sub(1.0, w['Coverage'])
    soft = t.madd(t.sub(1.0, w['Sharpness']), 0.45, 0.005)
    m = t.smooth(s, t.sub(th, soft), t.add(th, soft))
    m = t.madd(t.sub(m, base_h), t.mul(w['Crack Fill'], 2.0), m)
    m = t.clamp01(t.madd(t.sub(paint, 0.5), 2.0, t.clamp01(m)))
    t.out('Mask', 'FLOAT', t.mul(m, w['Opacity']))
    t.layout()
    return ng


def mix_group():
    ng, t = _new('NF Mix Layers', SV)
    if t is None:
        return ng
    f = t.inp('Factor', 'FLOAT', 0.0, 0.0, 1.0)
    A = {n: t.inp(n + ' A', k) for n, k in CH_OUT}
    B = {n: t.inp(n + ' B', k) for n, k in CH_OUT}
    t.out('Color', 'COLOR', t.mix(f, A['Color'], B['Color'], 'RGBA'))
    for n in ('Roughness', 'Metallic', 'Height', 'Alpha'):
        t.out(n, 'FLOAT', t.mix(f, A[n], B[n]))
    t.out('Normal', 'VEC', t.norm(t.mix(f, A['Normal'], B['Normal'], 'VECTOR')))
    t.layout()
    return ng


def finish_group():
    """cavity darkening / edge lightening baked into the colour (game engines have no AO from the shape)"""
    ng, t = _new('NF Surface Shade', SV)
    if t is None:
        return ng
    col = t.inp('Color', 'COLOR', (0.5, 0.5, 0.5, 1.0))
    curv = t.inp('Curvature', 'FLOAT', 0.5)
    dark = t.inp('Cavity Darken', 'FLOAT', 0.35, 0.0, 1.0, subtype='FACTOR')
    light = t.inp('Edge Lighten', 'FLOAT', 0.15, 0.0, 1.0, subtype='FACTOR')
    cav = t.clamp01(t.mul(t.sub(curv, 0.5), 2.0))
    edg = t.clamp01(t.mul(t.sub(0.5, curv), 2.0))
    k = t.mul(t.sub(1.0, t.mul(dark, cav)), t.madd(light, edg, 1.0))
    t.out('Color', 'COLOR', t.vscale(col, k))
    t.layout()
    return ng


# ---------------------------------------------------------------------------------------------- texture sets
def load_image(path, data):
    img = bpy.data.images.load(path, check_existing=True)
    want = 'Non-Color' if data else 'sRGB'
    try:
        if img.colorspace_settings.name != want:
            img.colorspace_settings.name = want
    except TypeError:
        pass
    return img


def ts_group(ts, kind='TRI'):
    """node group for one texture set; ts None = plain fill (colour / roughness from the inputs)"""
    sid = ts['id'] if ts else 'fill'
    label = ts['name'] if ts else 'Fill'
    name = f"NF Tex {label} ({kind})"
    ng = next((g for g in bpy.data.node_groups if g.get('nf_ts') == sid and g.get('nf_kind') == kind), None)
    if ng is not None and ng.get('nf_ver') == TS_V:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    ng.nodes.clear()
    ng.interface.clear()
    ng['nf_ts'], ng['nf_kind'], ng['nf_ver'] = sid, kind, TS_V
    t = nk.Tree(ng)
    maps = dict(ts['maps']) if ts else {}
    if kind == 'TRI':
        rest = t.inp('Position', 'VEC', hide_value=True)
        rn = t.inp('Rest Normal', 'VEC', hide_value=True)
        gco = t.inp('GN Coords', 'VEC', hide_value=True)
        gw = t.inp('GN Weights', 'VEC', hide_value=True)
        follow = t.inp('Follow Displacement', 'FLOAT', 0.0, 0.0, 1.0,
                       desc="1 = exactly the mapping the Geometry Nodes displaced with (base layer)")
        tile = t.inp('Tile Size', 'FLOAT', 1.0, 0.001, None, subtype='DISTANCE')
        blend = t.inp('Blend', 'FLOAT', 0.35, 0.0, 1.0, subtype='FACTOR')
    else:
        uv = t.inp('UV', 'VEC', hide_value=True)
        tile = t.inp('UV Scale', 'FLOAT', 1.0, 0.001, None, desc="Repeats over the UV map")
    base_n = t.inp('Base Normal', 'VEC', hide_value=True)
    nstr = t.inp('Normal Strength', 'FLOAT', 1.0, 0.0, 5.0)
    flip = t.inp('DirectX Normal', 'FLOAT', 1.0 if ts and ts.get('normal_dx') else 0.0, 0.0, 1.0,
                 desc="1 = flip the green channel (DirectX normal maps)")
    aos = t.inp('AO Strength', 'FLOAT', 1.0, 0.0, 1.0, subtype='FACTOR')
    adj = {}
    for nm, kd, dv, lo, hi, st in ADJUST:
        adj[nm] = t.inp(nm, kd, dv, lo, hi, subtype=st)
    fill_c = t.inp('Fill Color', 'COLOR', (0.4, 0.38, 0.35, 1.0), desc="Used where the set has no colour map")
    fill_r = t.inp('Fill Roughness', 'FLOAT', 0.8, 0.0, 1.0, subtype='FACTOR')
    fill_m = t.inp('Fill Metallic', 'FLOAT', 0.0, 0.0, 1.0, subtype='FACTOR')

    if kind == 'TRI':
        coords = t.mix(follow, t.vscale(rest, t.div(1.0, t.math('MAXIMUM', tile, 1e-4))), gco, 'VECTOR')
        wts = t.mix(follow, t.o(t.group(tri_weights_group(), {'Normal': rn, 'Blend': blend})), gw, 'VECTOR')
        x, y, z = t.sep(coords)
        wx, wy, wz = t.sep(wts)
        uvs = [(t.xyz(y, z, 0.0), wx), (t.xyz(x, z, 0.0), wy), (t.xyz(x, y, 0.0), wz)]
    else:
        uvs = [(t.vscale(uv, tile), None)]

    def samples(chan):
        img = load_image(maps[chan], chan in library.DATA)
        out = []
        for vec, w in uvs:
            nd = t.node('ShaderNodeTexImage', {'Vector': vec})
            nd.image = img
            nd.interpolation = 'Linear'
            nd.extension = 'REPEAT'
            out.append((nd, w))
        return out

    def blended(chan, sock='Color'):
        sm = samples(chan)
        if len(sm) == 1:
            return t.o(sm[0][0], sock)
        acc = None
        for nd, w in sm:
            v = t.vscale(t.o(nd, sock), w) if sock == 'Color' else t.mul(t.o(nd, sock), w)
            acc = v if acc is None else (t.vadd(acc, v) if sock == 'Color' else t.add(acc, v))
        return acc

    def lum(c):
        return t.dot(c, (0.2126, 0.7152, 0.0722))

    # colour + AO + adjustments
    alpha = None
    if 'color' in maps:
        col = blended('color')
        if 'alpha' not in maps and maps['color'].lower().endswith(('.png', '.tga', '.tif', '.tiff', '.exr', '.webp')):
            alpha = blended('color', 'Alpha')
    else:
        col = fill_c
    orm = t.sep(blended('orm')) if use_orm(maps) else None
    if 'ao' in maps or orm is not None:
        ao = lum(blended('ao')) if 'ao' in maps else orm[0]
        col = t.vscale(col, t.mix(aos, 1.0, ao))
    hsv = t.node('ShaderNodeHueSaturation', {'Hue': adj['Hue'], 'Saturation': adj['Saturation'],
                                             'Value': adj['Value'], 'Fac': 1.0, 'Color': col})
    col = t.mix(adj['Tint Amount'], t.o(hsv), t.mix(1.0, t.o(hsv), adj['Tint'], 'RGBA', 'MULTIPLY'), 'RGBA')
    # roughness / metallic
    if 'rough' in maps:
        r = lum(blended('rough'))
    elif 'gloss' in maps:
        r = t.sub(1.0, lum(blended('gloss')))
    elif orm is not None:
        r = orm[1]
    else:
        r = fill_r
    r = t.clamp01(t.add(r, adj['Roughness +/-']))
    if 'metal' in maps:
        m = lum(blended('metal'))
    elif orm is not None:
        m = orm[2]
    else:
        m = fill_m
    m = t.clamp01(t.add(m, adj['Metallic +/-']))
    # height (colour brightness when the set has none)
    if 'height' in maps:
        h = lum(blended('height'))
    elif 'color' in maps:
        h = lum(col)
    else:
        h = t.add(0.5, 0.0)
    # normal
    if 'normal' in maps:
        sgn = t.madd(flip, -2.0, 1.0)
        if kind == 'TRI':
            bx, by, bz = t.sep(base_n)
            sm = samples('normal')
            acc = None
            for i, (nd, w) in enumerate(sm):
                tx, ty, _ = t.sep(t.o(nd, 'Color'))
                tx = t.mul(t.madd(tx, 2.0, -1.0), nstr)
                ty = t.mul(t.mul(t.madd(ty, 2.0, -1.0), sgn), nstr)
                if i == 0:
                    v = t.xyz(bx, t.add(by, tx), t.add(bz, ty))
                elif i == 1:
                    v = t.xyz(t.add(bx, tx), by, t.add(bz, ty))
                else:
                    v = t.xyz(t.add(bx, tx), t.add(by, ty), bz)
                v = t.vscale(v, w)
                acc = v if acc is None else t.vadd(acc, v)
            nrm = t.norm(acc)
        else:
            c = t.o(samples('normal')[0][0], 'Color')
            cx, cy, cz = t.sep(c)
            cy = t.mix(flip, cy, t.sub(1.0, cy))
            nm_ = t.node('ShaderNodeNormalMap', {'Color': t.xyz(cx, cy, cz), 'Strength': nstr})
            nm_.space = 'TANGENT'
            nm_.uv_map = 'UVMap'
            vt = t.node('ShaderNodeVectorTransform', {'Vector': t.o(nm_)}, vector_type='NORMAL',
                        convert_from='WORLD', convert_to='OBJECT')
            nrm = t.norm(t.o(vt))
    else:
        nrm = base_n
    if 'alpha' in maps:
        alpha = lum(blended('alpha'))
    if kind == 'UV' and alpha is None:
        # no alpha in the set: a procedural leaf outline on the card (0 = plain square card)
        shape = t.inp('Leaf Shape', 'FLOAT', 1.0, 0.0, 1.0, subtype='FACTOR',
                      desc="Cut the card into a leaf outline (used when the texture set has no opacity)")
        u, v, _ = t.sep(t.vmath('FRACTION', uv))
        half = t.mul(t.math('POWER', t.math('SINE', t.mul(v, 3.14159)), 0.8), 0.42)
        d = t.math('ABSOLUTE', t.sub(u, 0.5))
        leaf = t.sub(1.0, t.smooth(d, t.sub(half, 0.02), half))
        card = t.node('ShaderNodeAttribute', attribute_type='GEOMETRY', attribute_name='nf_card').outputs['Fac']
        alpha = t.mix(t.mul(shape, card), 1.0, leaf)       # leaf cards only, never the canopy clumps
    t.out('Color', 'COLOR', col)
    t.out('Roughness', 'FLOAT', r)
    t.out('Metallic', 'FLOAT', m)
    t.out('Normal', 'VEC', nrm)
    t.out('Height', 'FLOAT', h)
    t.out('Alpha', 'FLOAT', alpha if alpha is not None else t.add(1.0, 0.0))
    t.layout(dx=240)
    return ng


# ---------------------------------------------------------------------------------------------- material
def layers_of(mat):
    try:
        return json.loads(mat.get('nf_layers', '[]'))
    except ValueError:
        return []


def _save_layers(mat, layers):
    mat['nf_layers'] = json.dumps(layers)


def new_layer(name="Layer", sid=None, preset=None, color=None, rough=None):
    L = {'uid': uuid.uuid4().hex[:8], 'name': name, 'set': sid}
    if preset:
        p = presets.LAYER[preset]
        L['name'] = p['label']
        L['init'] = {'fill': list(p['color']), 'rough': p['rough'], 'mask': p['mask']}
    elif color is not None:
        L['init'] = {'fill': list(color), 'rough': rough if rough is not None else 0.8}
    return L


def _snapshot(nt):
    snap = {}
    for n in nt.nodes:
        if n.name.startswith('NF:') and n.bl_idname == 'ShaderNodeGroup':
            snap[n.name] = {s.name: _val(s) for s in n.inputs if not s.is_linked and hasattr(s, 'default_value')}
    return snap


def _val(s):
    v = s.default_value
    return tuple(v) if hasattr(v, '__len__') and not isinstance(v, str) else v


def _restore(node, vals):
    for s in node.inputs:
        if s.name in vals and not s.is_linked:
            try:
                s.default_value = vals[s.name]
            except (TypeError, ValueError):
                pass


def _attr(t, name, kind='VEC'):
    nd = t.node('ShaderNodeAttribute', attribute_type='GEOMETRY', attribute_name=name)
    return nd.outputs['Vector' if kind == 'VEC' else 'Fac' if kind == 'FLOAT' else 'Color']


class _Mat(nk.Tree):
    """nk.Tree on a material node tree (no group input / output nodes)"""

    def __init__(self, mat):
        self.ng = mat.node_tree
        self.N = self.ng.nodes
        self.L = self.ng.links
        self.geo = False
        self._panels, self._in = {}, {}
        self.gin = self.gout = None

    def layout(self, dx=260, dy=-220):
        nodes = list(self.N)
        incoming = {n: [] for n in nodes}
        for l in self.L:
            incoming[l.to_node].append(l.from_node)
        depth = {n: 0 for n in nodes}
        for _ in range(len(nodes)):
            changed = False
            for n in nodes:
                d = max((depth[m] + 1 for m in incoming[n]), default=0)
                if d != depth[n] and d < len(nodes):
                    depth[n], changed = d, True
            if not changed:
                break
        cols = {}
        for n in nodes:
            cols.setdefault(depth[n], []).append(n)
        for d, ns in cols.items():
            for i, n in enumerate(ns):
                n.location = (d * dx, i * dy)


def build_material(mat, layers=None, kind='TRI'):
    """(re)generate the material node tree from its layer list; slider values are kept"""
    if layers is None:
        layers = layers_of(mat)
    else:
        _save_layers(mat, layers)
    mat['nf_kind'] = kind
    if hasattr(mat, 'use_nodes') and not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree
    snap = _snapshot(nt)
    nt.nodes.clear()
    t = _Mat(mat)
    out = t.node('ShaderNodeOutputMaterial')
    bsdf = t.node('ShaderNodeBsdfPrincipled')
    bsdf.name = 'NF:bsdf'
    geo_n = t.o(t.node('ShaderNodeNewGeometry'), 'Normal')
    n_obj = t.o(t.node('ShaderNodeVectorTransform', {'Vector': geo_n}, vector_type='NORMAL',
                       convert_from='WORLD', convert_to='OBJECT'))
    if kind == 'TRI':
        rest, rest_n = _attr(t, 'nf_rest'), _attr(t, 'nf_rest_n')
        gco, gw = _attr(t, 'nf_uvw'), _attr(t, 'nf_w')
        up_shape = t.sep(t.o(t.node('ShaderNodeVectorTransform', {'Vector': rest_n}, vector_type='NORMAL',
                                    convert_from='OBJECT', convert_to='WORLD')))[2]
    else:
        uv = t.o(t.node('ShaderNodeUVMap', uv_map='UVMap'))
        rest = t.o(t.node('ShaderNodeTexCoord'), 'Object')
        up_shape = t.sep(geo_n)[2]
    curv = _attr(t, 'nf_curv', 'FLOAT')
    hgt = _attr(t, 'nf_height', 'FLOAT')
    acc = None
    for i, L in enumerate(layers):
        ts = library.get(L['set']) if L.get('set') else None
        if L.get('set') and ts is None and L.get('ts'):
            ts = L['ts']                                   # the set travels with the material
            library.remember(ts)
        if ts is not None:
            L['ts'] = {k: ts[k] for k in ('id', 'name', 'maps', 'normal_dx', 'category', 'dir') if k in ts}
        g = t.group(ts_group(ts, kind))
        g.name = f"NF:layer:{L['uid']}"
        g.label = L['name']
        if kind == 'TRI':
            t.put(g, 'Position', rest)
            t.put(g, 'Rest Normal', rest_n)
            t.put(g, 'GN Coords', gco)
            t.put(g, 'GN Weights', gw)
            g.inputs['Follow Displacement'].default_value = 1.0 if i == 0 else 0.0
        else:
            t.put(g, 'UV', uv)
        t.put(g, 'Base Normal', n_obj)
        init = L.pop('init', None)
        if init:
            g.inputs['Fill Color'].default_value = init['fill']
            g.inputs['Fill Roughness'].default_value = init['rough']
        if g.name in snap:
            _restore(g, snap[g.name])
        if i == 0:
            if kind == 'TRI':
                g.inputs['Follow Displacement'].default_value = 1.0
            acc = {n: t.o(g, n) for n, _ in CH_OUT}
            continue
        mk = t.group(mask_group())
        mk.name = f"NF:mask:{L['uid']}"
        mk.label = L['name'] + " Mask"
        up_det = t.sep(t.o(t.node('ShaderNodeVectorTransform', {'Vector': acc['Normal']}, vector_type='NORMAL',
                                  convert_from='OBJECT', convert_to='WORLD')))[2]
        paint = _attr(t, f'nf_paint_{min(i + 1, 4)}', 'FLOAT') if i <= 3 else 0.5
        for k, v in (('Up Shape', up_shape), ('Up Detail', up_det), ('Curvature', curv), ('Object Height', hgt),
                     ('Position', rest), ('Base Height', acc['Height']), ('Paint', paint)):
            t.put(mk, k, v)
        if init and init.get('mask'):
            for k, v in init['mask'].items():
                if k in mk.inputs:
                    mk.inputs[k].default_value = v
        if mk.name in snap:
            _restore(mk, snap[mk.name])
        mx = t.group(mix_group())
        mx.name = f"NF:mix:{L['uid']}"
        t.put(mx, 'Factor', t.o(mk))
        for n, _ in CH_OUT:
            t.put(mx, n + ' A', acc[n])
            t.put(mx, n + ' B', t.o(g, n))
        acc = {n: t.o(mx, n) for n, _ in CH_OUT}
    _save_layers(mat, layers)
    sh = t.group(finish_group(), {'Color': acc['Color'], 'Curvature': curv})
    sh.name = 'NF:shade'
    if kind != 'TRI':
        sh.inputs['Cavity Darken'].default_value = 0.0
        sh.inputs['Edge Lighten'].default_value = 0.0
    if 'NF:shade' in snap:
        _restore(sh, snap['NF:shade'])
    t.put(bsdf, 'Base Color', t.o(sh))
    t.put(bsdf, 'Roughness', acc['Roughness'])
    t.put(bsdf, 'Metallic', acc['Metallic'])
    t.put(bsdf, 'Alpha', acc['Alpha'])
    nw = t.node('ShaderNodeVectorTransform', {'Vector': acc['Normal']}, vector_type='NORMAL',
                convert_from='OBJECT', convert_to='WORLD')
    t.put(bsdf, 'Normal', t.o(nw))
    t.put(out, 'Surface', bsdf)
    t.layout()
    if kind == 'UV' and hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'DITHERED'
    elif kind == 'UV' and hasattr(mat, 'blend_method'):
        mat.blend_method = 'HASHED'
    return mat


def create_material(name, kind='TRI', defaults='ROCK'):
    mat = bpy.data.materials.new(name)
    if hasattr(mat, 'use_nodes'):
        mat.use_nodes = True
    layers = []
    for d in presets.DEFAULT_LAYERS[defaults]:
        layers.append(new_layer(preset=d['preset']) if 'preset' in d else
                      new_layer(d['name'], color=d['color'], rough=d['rough']))
    build_material(mat, layers, kind)
    mat['nf_defaults'] = defaults
    return mat


def layer_nodes(mat, L):
    nt = mat.node_tree
    return nt.nodes.get(f"NF:layer:{L['uid']}"), nt.nodes.get(f"NF:mask:{L['uid']}")


def base_height_image(mat):
    """the image the Geometry Nodes should displace with: the base layer's height map (or its colour)"""
    layers = layers_of(mat)
    if not layers or not layers[0].get('set'):
        return None, None
    ts = library.get(layers[0]['set']) or layers[0].get('ts')
    if not ts:
        return None, None
    maps = ts['maps']
    if 'height' in maps:
        return load_image(maps['height'], True), 'height'
    if 'color' in maps:
        return load_image(maps['color'], False), 'color'
    return None, None


def is_nf(mat):
    return mat is not None and 'nf_layers' in mat


# ---------------------------------------------------------------------------------------------- asset materials
def use_orm(maps):
    """the packed AO / roughness / metal map only when AO or roughness is missing (loading a 4K ARM just for its
    near-black metal channel adds speckles, not detail)"""
    return 'orm' in maps and ('ao' not in maps or ('rough' not in maps and 'gloss' not in maps))


def missing_files(ts):
    return [p for p in ts['maps'].values() if not os.path.isfile(bpy.path.abspath(p))]


def asset_material(ts, mark_asset=True, mat=None, hand=False):
    """a plain UV / Principled material for a texture set: every map connected, height on the Displacement output,
    mlm_relief_depth for the Mixer's 3D Surface. Library / asset use shares one material per set; hand=True builds
    an object's own material (mat = rebuild that one). Key nodes are named NF:* so the UI can show their sliders."""
    miss = missing_files(ts)
    if miss:
        raise FileNotFoundError(f"Texture files missing: {miss[0]}")
    name = ts['name']
    if mat is None and not hand:
        mat = next((m for m in bpy.data.materials if m.get('nf_asset') == ts['id'] and not m.get('nf_hand_mat')),
                   None)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat['nf_asset'] = ts['id']
    if hand:
        mat['nf_hand_mat'] = True
    if hasattr(mat, 'use_nodes'):
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    t = _Mat(mat)
    out = t.node('ShaderNodeOutputMaterial')
    b = t.node('ShaderNodeBsdfPrincipled')
    b.name = 'NF:bsdf'
    maps = ts['maps']
    uv = t.o(t.node('ShaderNodeTexCoord'), 'UV')

    def tex(chan):
        nd = t.node('ShaderNodeTexImage', {'Vector': uv})
        nd.image = load_image(maps[chan], chan in library.DATA)
        nd.label = chan
        return nd
    orm = t.sep(t.o(tex('orm'), 'Color')) if use_orm(maps) else None   # packed AO / rough / metal (R / G / B)
    if 'color' in maps:
        col = t.o(tex('color'), 'Color')
        hsv = t.node('ShaderNodeHueSaturation', {'Color': col})
        hsv.name = 'NF:hsv'
        col = t.o(hsv)
        if 'ao' in maps or orm is not None:
            ao = t.o(tex('ao'), 'Color') if 'ao' in maps else t.xyz(orm[0], orm[0], orm[0])
            mx = t.node('ShaderNodeMix', data_type='RGBA', blend_type='MULTIPLY', clamp_factor=True)
            t.put(mx, 'Factor', 1.0)
            t.put(mx, 'A', col)
            t.put(mx, 'B', ao)
            mx.name = 'NF:ao'
            col = t.o(mx, 'Result')
        t.put(b, 'Base Color', col)
    rough = None
    if 'rough' in maps:
        rough = t.o(tex('rough'), 'Color')
    elif 'gloss' in maps:
        rough = t.sub(1.0, t.o(tex('gloss'), 'Color'))
    elif orm is not None:
        rough = orm[1]
    if rough is not None:
        ra = t.node('ShaderNodeMath', operation='ADD', use_clamp=True)
        t.put(ra, 0, rough)
        t.put(ra, 1, 0.0)
        ra.name = 'NF:rough'
        t.put(b, 'Roughness', t.o(ra))
    if 'metal' in maps:
        t.put(b, 'Metallic', t.o(tex('metal'), 'Color'))
    elif orm is not None:
        t.put(b, 'Metallic', orm[2])
    if 'alpha' in maps:
        t.put(b, 'Alpha', t.o(tex('alpha'), 'Color'))
    if 'normal' in maps:
        c = t.o(tex('normal'), 'Color')
        if ts.get('normal_dx'):
            cx, cy, cz = t.sep(c)
            c = t.xyz(cx, t.sub(1.0, cy), cz)
        nm = t.node('ShaderNodeNormalMap', {'Color': c})
        nm.name = 'NF:normal'
        t.put(b, 'Normal', t.o(nm))
    if 'height' in maps:
        d = t.node('ShaderNodeDisplacement', {'Height': t.o(tex('height'), 'Color'),
                                              'Midlevel': float(ts.get('info', {}).get('mid', 0.5)),
                                              'Scale': float(ts.get('info', {}).get('depth', 0.03))})
        d.name = 'NF:disp'
        t.put(out, 'Displacement', d)
    t.put(out, 'Surface', b)
    t.layout()
    mat['mlm_relief_depth'] = float(ts.get('info', {}).get('depth', 0.03))
    if mark_asset and not hand and mat.asset_data is None:
        mat.asset_mark()
        if not bpy.app.background:                  # preview rendering needs a GPU context
            try:
                mat.asset_generate_preview()
            except (AttributeError, RuntimeError):
                pass
    if mat.asset_data is not None and ts.get('category'):
        try:
            if ts['category'] not in [tg.name for tg in mat.asset_data.tags]:
                mat.asset_data.tags.new(ts['category'])
        except (AttributeError, RuntimeError):
            pass
    return mat
