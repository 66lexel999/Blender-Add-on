# Nature Forge - Geometry Nodes groups shared by every generator.
#
#   NF Store Rest        remembers the surface BEFORE the texture displacement (position, normal, triplanar
#                        coordinates + blend weights). The material samples its textures at exactly these
#                        coordinates, so the colour / normal map always sits on the bumps the height map made.
#   NF Texture Displace  triplanar height map -> real geometry (the "make it rocky with the displacement map" trick)
#   NF Surface Masks     cavity / edge / height attributes for the auto-paint masks + neutral paint attributes
#   NF Facet Cuts        random planar cuts: chiselled, broken-rock faces
from . import nodekit as nk

GV = 4                                   # bump to rebuild the shared groups in old files
PAINT_ATTRS = ('nf_paint_2', 'nf_paint_3', 'nf_paint_4')


def _lum(t, col):
    return t.dot(col, (0.2126, 0.7152, 0.0722))


def tri_weights(t, n, blend):
    """|n|^k normalised; blend 0 = hard switch between the 3 projections, 1 = very soft"""
    k = t.mrange(blend, 0.0, 1.0, 14.0, 1.0)
    x, y, z = t.sep(t.vmath('ABSOLUTE', n))
    wx, wy, wz = (t.math('POWER', c, k) for c in (x, y, z))
    s = t.math('MAXIMUM', t.add(t.add(wx, wy), wz), 1e-6)
    return t.xyz(t.div(wx, s), t.div(wy, s), t.div(wz, s))


def store_rest():
    ng, t = nk.new_group('NF Store Rest', version=GV)
    if t is None:
        return ng
    geo = t.inp('Geometry', 'GEO')
    tile = t.inp('Tile Size', 'FLOAT', 1.0, 0.001, None, subtype='DISTANCE')
    blend = t.inp('Blend', 'FLOAT', 0.35, 0.0, 1.0, subtype='FACTOR')
    p, n = t.pos(), t.normal()
    g = t.store(geo, 'nf_rest', p, 'FLOAT_VECTOR')
    g = t.store(g, 'nf_rest_n', n, 'FLOAT_VECTOR')
    g = t.store(g, 'nf_uvw', t.vscale(p, t.div(1.0, t.math('MAXIMUM', tile, 1e-4))), 'FLOAT_VECTOR')
    g = t.store(g, 'nf_w', tri_weights(t, n, blend), 'FLOAT_VECTOR')
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng


def tex_displace():
    ng, t = nk.new_group('NF Texture Displace', version=GV)
    if t is None:
        return ng
    geo = t.inp('Geometry', 'GEO')
    img = t.inp('Height Map', 'IMAGE')
    depth = t.inp('Depth', 'FLOAT', 0.05, None, None, subtype='DISTANCE')
    mid = t.inp('Mid Level', 'FLOAT', 0.5, 0.0, 1.0, subtype='FACTOR')
    inv = t.inp('Invert', 'BOOL', False)
    x, y, z = t.sep(t.named('nf_uvw', 'FLOAT_VECTOR'))
    wx, wy, wz = t.sep(t.named('nf_w', 'FLOAT_VECTOR'))
    rn = t.named('nf_rest_n', 'FLOAT_VECTOR')

    def sample(u, v):
        tx = t.node('GeometryNodeImageTexture', {'Image': img, 'Vector': t.xyz(u, v, 0.0)},
                    interpolation='Linear', extension='REPEAT')
        return _lum(t, t.o(tx, 'Color'))
    h = t.add(t.add(t.mul(sample(y, z), wx), t.mul(sample(x, z), wy)), t.mul(sample(x, y), wz))
    h = t.switch('FLOAT', inv, h, t.sub(1.0, h))
    info = t.node('GeometryNodeImageInfo', {'Image': img})
    has = t.compare('GREATER_THAN', t.o(info, 'Width'), 0.5)
    d = t.switch('FLOAT', has, 0.0, t.mul(t.sub(h, mid), depth))
    g = t.setpos(geo, offset=t.vscale(rn, d))
    g = t.store(g, 'nf_tex_h', t.switch('FLOAT', has, 0.5, h))
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng


def surface_masks():
    """cavity (> 0.5) / edge (< 0.5) from position vs. its blurred neighbourhood, normalised by its own spread so the
    masks behave the same on a pebble and on a mountain; height 0..1 over the object; neutral paint layers"""
    ng, t = nk.new_group('NF Surface Masks', version=GV)
    if t is None:
        return ng
    geo = t.inp('Geometry', 'GEO')
    spread = t.inp('Cavity Spread', 'INT', 3, 1, 30)
    mat = t.inp('Material', 'MAT')
    use_mat = t.inp('Set Material', 'BOOL', True)
    smooth = t.inp('Shade Smooth', 'BOOL', True)
    p, n = t.pos(), t.normal()
    d = t.dot(t.vsub(t.blur(p, spread), p), n)
    st = t.stat(geo, d)
    curv = t.mrange(t.div(t.sub(d, t.o(st, 'Mean')), t.math('MAXIMUM', t.mul(t.o(st, 'Standard Deviation'), 5.0), 1e-7)),
                    -0.5, 0.5, 0.0, 1.0)
    g = t.store(geo, 'nf_curv', curv)
    z = t.sep(p)[2]
    sz = t.stat(geo, z)
    g = t.store(g, 'nf_height', t.mrange(z, t.o(sz, 'Min'), t.o(sz, 'Max'), 0.0, 1.0))
    for a in PAINT_ATTRS:
        g = t.store(g, a, (0.5, 0.5, 0.5, 1.0), 'FLOAT_COLOR')
    g = shade_smooth(t, g, smooth)
    sm = t.node('GeometryNodeSetMaterial', {'Geometry': g, 'Material': mat})
    g = t.switch('GEOMETRY', use_mat, g, t.o(sm))
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng


def shade_smooth(t, g, smooth=True):
    ss = t.node('GeometryNodeSetShadeSmooth')
    t.put(ss, 0, g)                              # 'Geometry' in 4.2, 'Mesh' in 5.x
    t.put(ss, 'Shade Smooth', smooth)
    return ss.outputs[0]


def facet_cuts():
    """Count random planes; whatever pokes out past a plane is pushed back onto it -> flat broken faces.
    Extent = half size of the shape (plane offsets follow an ellipsoid's support function)"""
    ng, t = nk.new_group('NF Facet Cuts', version=GV)
    if t is None:
        return ng
    geo = t.inp('Geometry', 'GEO')
    count = t.inp('Count', 'INT', 10, 0, 200)
    depth = t.inp('Depth', 'FLOAT', 0.25, 0.0, 1.0, subtype='FACTOR')
    flat = t.inp('Flatness', 'FLOAT', 0.85, 0.0, 1.0, subtype='FACTOR')
    seed = t.inp('Seed', 'INT', 0)
    ext = t.inp('Extent', 'VEC', (1.0, 1.0, 1.0))
    center = t.inp('Center', 'VEC', (0.0, 0.0, 0.0))
    side = t.inp('Side Bias', 'FLOAT', 0.0, 0.0, 1.0, subtype='FACTOR',
                 desc="1 = only steep cuts (cliff walls), 0 = any direction")
    box = t.inp('Box Shape', 'FLOAT', 0.0, 0.0, 1.0, subtype='FACTOR',
                desc="0 = cuts follow a rounded (ellipsoid) shape, 1 = a box (keeps the corners of walls)")
    rin, rout, it = t.repeat(count, [('GEOMETRY', 'Geometry', geo)])
    rv = t.rand((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), 'FLOAT_VECTOR', id=it, seed=seed)
    rx, ry, rz = t.sep(rv)
    dvec = t.norm(t.xyz(rx, ry, t.mul(rz, t.sub(1.0, t.mul(side, 0.85)))))
    ex, ey, ez = t.sep(t.vmath('ABSOLUTE', t.vmul(dvec, ext)))
    e = t.mix(box, t.length(t.vmul(dvec, ext)), t.add(t.add(ex, ey), ez))
    cut = t.mul(e, t.sub(1.0, t.mul(depth, t.rand(0.25, 1.0, id=it, seed=t.add(seed, 77)))))
    rel = t.vsub(t.pos(), center)
    s = t.math('MAXIMUM', t.sub(t.dot(rel, dvec), cut), 0.0)
    g = t.setpos(t.o(rin, 'Geometry'), offset=t.vscale(dvec, t.mul(s, t.mul(flat, -1.0))))
    t.link(g, rout.inputs[0])
    t.out('Geometry', 'GEO', t.o(rout, 'Geometry'))
    t.layout()
    return ng


def ensure_all():
    return {'rest': store_rest(), 'disp': tex_displace(), 'masks': surface_masks(), 'facets': facet_cuts()}


def texture_inputs(t, panel='Texture Displacement', depth=0.05, tile=1.0):
    """the standard texture-displacement inputs every generator exposes (names the add-on relies on)"""
    return dict(
        img=t.inp('Height Map', 'IMAGE', panel=panel,
                  desc="Height / displacement map of your texture set (set automatically by 'Apply')"),
        depth=t.inp('Texture Depth', 'FLOAT', depth, None, None, panel=panel, subtype='DISTANCE',
                    desc="How far the height map pushes the surface out (real 3D detail)"),
        mid=t.inp('Mid Level', 'FLOAT', 0.5, 0.0, 1.0, panel=panel, subtype='FACTOR',
                  desc="Height value that stays on the original surface"),
        tile=t.inp('Tile Size', 'FLOAT', tile, 0.001, None, panel=panel, subtype='DISTANCE',
                   desc="Real-world size of one texture repeat (the material's base layer follows it)"),
        blend=t.inp('Projection Blend', 'FLOAT', 0.35, 0.0, 1.0, panel=panel, subtype='FACTOR',
                    desc="Triplanar projection: soft (1) or sharp (0) transitions between the 3 directions"),
        inv=t.inp('Invert Height', 'BOOL', False, panel=panel),
    )


def finish_inputs(t, panel='Material'):
    return dict(
        mat=t.inp('Material', 'MAT', panel=panel),
        spread=t.inp('Cavity Spread', 'INT', 3, 1, 30, panel=panel,
                     desc="How wide the cavity / edge masks look (used by the auto-paint layers)"),
    )


def rest_and_displace(t, g, tx):
    G = ensure_all()
    g = t.o(t.group(G['rest'], {'Geometry': g, 'Tile Size': tx['tile'], 'Blend': tx['blend']}))
    return g


def displace(t, g, tx):
    G = ensure_all()
    return t.o(t.group(G['disp'], {'Geometry': g, 'Height Map': tx['img'], 'Depth': tx['depth'],
                                   'Mid Level': tx['mid'], 'Invert': tx['inv']}))


def finish(t, g, fi, set_material=True):
    G = ensure_all()
    return t.o(t.group(G['masks'], {'Geometry': g, 'Cavity Spread': fi['spread'], 'Material': fi['mat'],
                                    'Set Material': set_material}))
