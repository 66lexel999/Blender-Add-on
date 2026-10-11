# Nature Forge - Tree generator (Geometry Nodes).
# trunk curve (bend / lean / root flare) -> branches spiralling up the trunk (golden angle), length from the crown
# shape (cone = pine ... round = oak ... top heavy = umbrella), droop / upturn / wiggle -> twigs -> one bark mesh
# (curve to mesh, triplanar bark texture + height map) -> leaves: cards (alpha textures, UV mapped) or stylized
# clumps (low-poly canopy blobs)
from . import nodekit as nk
from . import gn_shared as sh

NAME, VERSION = 'NF Tree', 3
LEAF_TYPES = ('None', 'Cards', 'Clumps')


def _radius_to_mesh(t, c2m):
    """4.2 scales the profile by the curve radius itself, 5.x needs the radius on 'Scale'"""
    if any(s.name == 'Scale' for s in c2m.inputs):
        t.put(c2m, 'Scale', t.o(t.node('GeometryNodeInputRadius')))


def _line(t, segments):
    line = t.node('GeometryNodeCurvePrimitiveLine', {'Start': (0.0, 0.0, 0.0), 'End': (0.0, 0.0, 1.0)})
    rs = t.node('GeometryNodeResampleCurve', {'Curve': t.o(line), 'Count': segments})
    nk.opt(rs, 'mode', 'COUNT', 'Mode', 'Count')
    return t.o(rs)


def _points(t, curve, count):
    nd = t.node('GeometryNodeCurveToPoints', {'Curve': curve, 'Count': count}, mode='COUNT')
    return nd


def build(force=False):
    ng, t = nk.new_group(NAME, version=VERSION, force=force)
    if t is None:
        return ng
    ng['nf_kind'] = 'TREE'
    t.inp('Geometry', 'GEO')
    seed = t.inp('Seed', 'INT', 0, desc="Every seed is a different tree")
    P = 'Trunk'
    H = t.inp('Height', 'FLOAT', 7.0, 0.1, None, panel=P, subtype='DISTANCE')
    R = t.inp('Trunk Radius', 'FLOAT', 0.25, 0.005, None, panel=P, subtype='DISTANCE')
    topr = t.inp('Top Radius', 'FLOAT', 0.2, 0.0, 1.0, panel=P, subtype='FACTOR', desc="Radius at the top (x base)")
    flare = t.inp('Root Flare', 'FLOAT', 0.6, 0.0, 3.0, panel=P, desc="Wider foot where the roots start")
    bend = t.inp('Bend', 'FLOAT', 0.25, 0.0, 2.0, panel=P, desc="Random curving of the trunk")
    bscale = t.inp('Bend Scale', 'FLOAT', 0.5, 0.01, 10.0, panel=P, desc="Curves per metre")
    lean = t.inp('Lean', 'FLOAT', 0.0, -1.0, 1.0, panel=P, subtype='FACTOR')
    tseg = t.inp('Trunk Segments', 'INT', 32, 3, 256, panel=P)
    sides = t.inp('Bark Sides', 'INT', 12, 3, 64, panel=P, desc="Vertices around trunk and branches")
    P = 'Branches'
    bcount = t.inp('Branch Count', 'INT', 26, 0, 500, panel=P)
    bstart = t.inp('Start Height', 'FLOAT', 0.3, 0.0, 0.95, panel=P, subtype='FACTOR',
                   desc="Where the crown begins on the trunk")
    bangle = t.inp('Branch Angle', 'FLOAT', 0.95, 0.0, 3.1, panel=P, subtype='ANGLE',
                   desc="Angle from the trunk (small = upright, 90 = flat, more = hanging)")
    bavar = t.inp('Angle Variation', 'FLOAT', 0.25, 0.0, 1.0, panel=P, subtype='FACTOR')
    blen = t.inp('Branch Length', 'FLOAT', 0.42, 0.0, 3.0, panel=P, desc="Longest branch, relative to the height")
    shape = t.inp('Crown Shape', 'FLOAT', 0.5, 0.0, 1.0, panel=P, subtype='FACTOR',
                  desc="0 = cone (pine), 0.5 = round (oak), 1 = top heavy (umbrella)")
    droop = t.inp('Droop', 'FLOAT', 0.2, -1.0, 2.0, panel=P, desc="Branches bend down under their weight")
    upturn = t.inp('Upturn', 'FLOAT', 0.25, -1.0, 2.0, panel=P, desc="Branch tips curve up towards the light")
    wiggle = t.inp('Wiggle', 'FLOAT', 0.15, 0.0, 2.0, panel=P, desc="Random kinks along the branches")
    brad = t.inp('Branch Thickness', 'FLOAT', 0.5, 0.0, 2.0, panel=P, desc="Relative to the trunk where they start")
    bseg = t.inp('Branch Segments', 'INT', 10, 2, 64, panel=P)
    P = 'Twigs'
    tcount = t.inp('Twig Count', 'INT', 6, 0, 64, panel=P, desc="Twigs per branch")
    tlen = t.inp('Twig Length', 'FLOAT', 0.35, 0.0, 2.0, panel=P, desc="Relative to the branch")
    tangle = t.inp('Twig Angle', 'FLOAT', 0.8, 0.0, 3.1, panel=P, subtype='ANGLE')
    P = 'Leaves'
    ltype = t.inp('Leaf Type', 'MENU', panel=P, desc="Cards: textured leaf planes (alpha). Clumps: stylized "
                                                       "canopy blobs (low poly, no textures needed)")
    lcount = t.inp('Leaves per Twig', 'INT', 10, 0, 200, panel=P)
    lsize = t.inp('Leaf Size', 'FLOAT', 0.22, 0.001, None, panel=P, subtype='DISTANCE')
    csize = t.inp('Clump Size', 'FLOAT', 0.7, 0.01, None, panel=P, subtype='DISTANCE')
    ccount = t.inp('Clumps per Branch', 'INT', 4, 0, 64, panel=P)
    clumps = t.inp('Clump Lumps', 'FLOAT', 0.35, 0.0, 2.0, panel=P)
    cdet = t.inp('Clump Detail', 'INT', 2, 1, 4, panel=P)
    lmat = t.inp('Leaf Material', 'MAT', panel=P)
    tx = sh.texture_inputs(t, 'Bark Texture', depth=0.015, tile=0.8)
    P = 'Material'
    bmat = t.inp('Bark Material', 'MAT', panel=P)
    bnoise = t.inp('Bark Ridges', 'FLOAT', 0.01, 0.0, None, panel=P, subtype='DISTANCE',
                   desc="Procedural vertical bark ridges (depth)")
    spread = t.inp('Cavity Spread', 'INT', 2, 1, 30, panel=P)

    seedv = t.rand((-100.0, -100.0, -100.0), (100.0, 100.0, 100.0), 'FLOAT_VECTOR', id=0, seed=seed)
    # ---------------------------------------------------------------- trunk
    trunk = _line(t, tseg)
    f = t.o(t.node('GeometryNodeSplineParameter'), 'Factor')
    bn = t.noise(t.vadd(t.xyz(0.0, 0.0, t.mul(t.mul(f, H), bscale)), seedv), 1.0, 2.0, 0.5)[1]
    bx, by, _ = t.sep(t.vsub(bn, (0.5, 0.5, 0.5)))
    amp = t.mul(t.mul(bend, H), t.mul(f, 0.35))
    trunk = t.setpos(trunk, position=t.xyz(t.madd(bx, amp, t.mul(t.mul(lean, t.mul(H, 0.5)), t.mul(f, f))),
                                           t.mul(by, amp), t.mul(f, H)))
    trunk = t.store(trunk, 'nf_t', f)
    fl = t.math('POWER', t.sub(1.0, t.smooth(f, 0.0, 0.15)), 2.0)
    r = t.mul(t.mul(R, t.mix(f, 1.0, topr)), t.madd(fl, flare, 1.0))
    trunk = t.o(t.node('GeometryNodeSetCurveRadius', {'Curve': trunk, 'Radius': r}))
    # ---------------------------------------------------------------- branches
    trim = t.node('GeometryNodeTrimCurve', {'Curve': trunk, 'Start': bstart, 'End': 0.97}, mode='FACTOR')
    bp = _points(t, t.o(trim), bcount)
    pts = t.o(bp, 'Points')
    idx = t.index()
    tt = t.named('nf_t')
    trel = t.mrange(tt, bstart, 0.97, 0.0, 1.0)
    az = t.madd(idx, 2.39996, t.rand(-0.6, 0.6, id=idx, seed=t.add(seed, 11)))
    th = t.mul(bangle, t.madd(t.rand(-1.0, 1.0, id=idx, seed=t.add(seed, 12)), bavar, 1.0))
    th = t.mul(th, t.sub(1.0, t.mul(trel, 0.35)))
    sth = t.math('SINE', th)
    dvec = t.xyz(t.mul(sth, t.math('COSINE', az)), t.mul(sth, t.math('SINE', az)), t.math('COSINE', th))
    cone = t.sub(1.0, t.mul(trel, 0.8))
    rnd = t.math('MAXIMUM', t.math('SINE', t.mul(3.14159, t.mix(trel, 0.25, 1.0))), 0.15)
    tophv = t.madd(trel, 0.6, 0.4)
    prof = t.mix(t.clamp01(t.madd(shape, 2.0, -1.0)), t.mix(t.clamp01(t.mul(shape, 2.0)), cone, rnd), tophv)
    L = t.mul(t.mul(t.mul(blen, H), prof), t.rand(0.75, 1.1, id=idx, seed=t.add(seed, 13)))
    rot = t.node('FunctionNodeAlignRotationToVector', {'Vector': dvec}, axis='Z')
    pts = t.store(pts, 'nf_pr', t.mul(t.mul(t.o(t.node('GeometryNodeInputRadius')), brad),
                                       t.mix(trel, 1.0, 0.45)))
    pts = t.store(pts, 'nf_len', L)
    inst = t.node('GeometryNodeInstanceOnPoints', {'Points': pts, 'Instance': _line(t, bseg),
                                                   'Rotation': t.o(rot), 'Scale': t.xyz(L, L, L)})
    br = t.o(t.node('GeometryNodeRealizeInstances', {'Geometry': t.o(inst)}))
    f = t.o(t.node('GeometryNodeSplineParameter'), 'Factor')
    Lb = t.named('nf_len')
    wn = t.noise(t.vadd(t.pos(), seedv), 1.2, 2.0, 0.5)[1]
    off = t.vscale(t.vsub(wn, (0.5, 0.5, 0.5)), t.mul(t.mul(wiggle, t.mul(Lb, 0.6)), f))
    dz = t.mul(Lb, t.sub(t.mul(upturn, t.mul(f, t.mul(f, f))), t.mul(droop, t.mul(f, f))))
    br = t.setpos(br, offset=t.vadd(off, t.xyz(0.0, 0.0, dz)))
    br = t.store(br, 'nf_f', f)
    br = t.o(t.node('GeometryNodeSetCurveRadius', {'Curve': br,
                                                   'Radius': t.mul(t.named('nf_pr'), t.mix(f, 1.0, 0.2))}))
    # ---------------------------------------------------------------- twigs
    tp = _points(t, br, t.add(tcount, 1))
    tf = t.named('nf_f')
    tid = t.index()
    tan = t.o(tp, 'Tangent')
    rv = t.rand((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), 'FLOAT_VECTOR', id=tid, seed=t.add(seed, 21))
    perp = t.norm(t.vsub(rv, t.vscale(tan, t.dot(rv, tan))))
    tdir = t.norm(t.vadd(t.vadd(t.vscale(tan, t.math('COSINE', tangle)), t.vscale(perp, t.math('SINE', tangle))),
                         (0.0, 0.0, 0.3)))
    # the tangent belongs to the point domain of the unfiltered points: capture it before deleting
    tpts0 = t.o(tp, 'Points')
    tpts0, tdir_c = t.capture(tpts0, tdir, 'FLOAT_VECTOR')
    tpts = t.o(t.node('GeometryNodeDeleteGeometry', {'Geometry': tpts0,
                                                     'Selection': t.compare('LESS_THAN', tf, 0.2)}, domain='POINT'))
    Lt = t.mul(t.mul(t.mul(tlen, t.named('nf_len')), t.rand(0.6, 1.0, id=tid, seed=t.add(seed, 22))),
               t.madd(tf, -0.5, 1.0))
    trot = t.node('FunctionNodeAlignRotationToVector', {'Vector': tdir_c}, axis='Z')
    tpts = t.store(tpts, 'nf_pr', t.mul(t.o(t.node('GeometryNodeInputRadius')), 0.55))
    tpts = t.store(tpts, 'nf_len', Lt)
    tinst = t.node('GeometryNodeInstanceOnPoints', {'Points': tpts, 'Instance': _line(t, 4),
                                                    'Rotation': t.o(trot), 'Scale': t.xyz(Lt, Lt, Lt)})
    tw = t.o(t.node('GeometryNodeRealizeInstances', {'Geometry': t.o(tinst)}))
    f2 = t.o(t.node('GeometryNodeSplineParameter'), 'Factor')
    tw = t.setpos(tw, offset=t.xyz(0.0, 0.0, t.mul(t.named('nf_len'), t.mul(t.mul(droop, -0.3), t.mul(f2, f2)))))
    tw = t.store(tw, 'nf_f', f2)
    tw = t.o(t.node('GeometryNodeSetCurveRadius', {'Curve': tw,
                                                   'Radius': t.mul(t.named('nf_pr'), t.mix(f2, 1.0, 0.25))}))
    # ---------------------------------------------------------------- bark mesh
    allc = t.join(trunk, br, tw)
    prof_c = t.node('GeometryNodeCurvePrimitiveCircle', {'Resolution': sides, 'Radius': 1.0})
    c2m = t.node('GeometryNodeCurveToMesh', {'Curve': allc, 'Profile Curve': t.o(prof_c, 'Curve'),
                                             'Fill Caps': True})
    _radius_to_mesh(t, c2m)
    bark = sh.rest_and_displace(t, t.o(c2m), tx)
    p, n = t.pos(), t.normal()
    px, py, pz = t.sep(p)
    rv2 = t.voronoi(t.vadd(t.xyz(t.mul(px, 6.0), t.mul(py, 6.0), t.mul(pz, 0.8)), seedv), 1.0, 'DISTANCE_TO_EDGE')
    ridge = t.smooth(t.o(rv2, 'Distance'), 0.0, 0.25)
    bark = t.setpos(bark, offset=t.vscale(n, t.mul(t.sub(ridge, 0.5), bnoise)))
    bark = sh.displace(t, bark, tx)
    bark = sh.finish(t, bark, {'spread': spread, 'mat': bmat})
    # ---------------------------------------------------------------- leaves
    lp = _points(t, tw, lcount)
    lpts = t.o(lp, 'Points')
    lid = t.index()
    grid = t.node('GeometryNodeMeshGrid', {'Size X': 1.0, 'Size Y': 1.0, 'Vertices X': 2, 'Vertices Y': 2})
    card = t.store(t.o(grid, 'Mesh'), 'UVMap', t.o(grid, 'UV Map'), 'FLOAT2', 'CORNER')
    card = t.o(t.node('GeometryNodeTransform', {'Geometry': card, 'Translation': (0.0, 0.5, 0.0)}))
    card = t.o(t.node('GeometryNodeSetMaterial', {'Geometry': card, 'Material': lmat}))
    erot = t.rand((-3.1416, -3.1416, -3.1416), (3.1416, 3.1416, 3.1416), 'FLOAT_VECTOR', id=lid,
                  seed=t.add(seed, 31))
    ls = t.mul(lsize, t.rand(0.7, 1.3, id=lid, seed=t.add(seed, 32)))
    linst = t.node('GeometryNodeInstanceOnPoints', {'Points': lpts, 'Instance': card,
                                                    'Rotation': t.o(t.node('FunctionNodeEulerToRotation',
                                                                           {'Euler': erot})),
                                                    'Scale': t.xyz(ls, ls, ls)})
    cards = t.o(t.node('GeometryNodeRealizeInstances', {'Geometry': t.o(linst)}))
    cards = sh.shade_smooth(t, cards, False)
    cards = t.store(cards, 'nf_card', 1.0)
    # clumps along the outer part of every branch + at the twig tips
    cp = _points(t, br, t.add(ccount, 1))
    cpts = t.o(t.node('GeometryNodeDeleteGeometry', {'Geometry': t.o(cp, 'Points'),
                                                     'Selection': t.compare('LESS_THAN', t.named('nf_f'), 0.3)},
                      domain='POINT'))
    ico = t.node('GeometryNodeMeshIcoSphere', {'Radius': 1.0, 'Subdivisions': cdet})
    blob = t.store(t.o(ico, 'Mesh'), 'UVMap', t.o(ico, 'UV Map'), 'FLOAT2', 'CORNER')
    bfac = t.noise(t.pos(), 1.3, 2.0, 0.5)[0]
    blob = t.setpos(blob, offset=t.vscale(t.normal(), t.mul(t.sub(bfac, 0.5), t.mul(clumps, 1.4))))
    blob = t.o(t.node('GeometryNodeSetMaterial', {'Geometry': blob, 'Material': lmat}))
    cid = t.index()
    crot = t.rand((-3.1416, -3.1416, -3.1416), (3.1416, 3.1416, 3.1416), 'FLOAT_VECTOR', id=cid,
                  seed=t.add(seed, 41))
    cs = t.mul(t.mul(csize, t.rand(0.6, 1.1, id=cid, seed=t.add(seed, 42))), t.madd(t.named('nf_f'), 0.5, 0.6))
    cinst = t.node('GeometryNodeInstanceOnPoints', {'Points': cpts, 'Instance': blob,
                                                    'Rotation': t.o(t.node('FunctionNodeEulerToRotation',
                                                                           {'Euler': crot})),
                                                    'Scale': t.xyz(cs, cs, t.mul(cs, 0.8))})
    cl = t.o(t.node('GeometryNodeRealizeInstances', {'Geometry': t.o(cinst)}))
    cl = sh.shade_smooth(t, cl, True)
    cl = t.store(cl, 'nf_card', 0.0)
    sw = t.N.new('GeometryNodeMenuSwitch')
    sw.data_type = 'GEOMETRY'
    sw.enum_items[0].name, sw.enum_items[1].name = LEAF_TYPES[0], LEAF_TYPES[1]
    sw.enum_items.new(LEAF_TYPES[2])
    t.link(ltype, sw.inputs['Menu'])
    t.link(cards, nk.find_socket(sw.inputs, 'Cards'))
    t.link(cl, nk.find_socket(sw.inputs, 'Clumps'))
    item = next(i for i in ng.interface.items_tree if getattr(i, 'name', '') == 'Leaf Type')
    item.default_value = 'Cards'
    leaves = t.store(sw.outputs[0], 'nf_leaf', 1.0)
    for a in sh.PAINT_ATTRS:
        leaves = t.store(leaves, a, (0.5, 0.5, 0.5, 1.0), 'FLOAT_COLOR')
    t.out('Geometry', 'GEO', t.join(bark, leaves))
    t.layout()
    return ng
