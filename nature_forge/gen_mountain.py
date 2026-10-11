# Nature Forge - Mountain / terrain generator (Geometry Nodes).
# grid -> warped fractal + ridge noise -> radial falloff (single peak / island / open terrain) -> normalise ->
# sharpness, valleys, floor, plateau, terraces -> erosion smoothing -> optional solid skirt -> texture height map
from . import nodekit as nk
from . import gn_shared as sh

NAME, VERSION = 'NF Mountain', 2


def build(force=False):
    ng, t = nk.new_group(NAME, version=VERSION, force=force)
    if t is None:
        return ng
    ng['nf_kind'] = 'MOUNTAIN'
    t.inp('Geometry', 'GEO')
    seed = t.inp('Seed', 'INT', 0, desc="Every seed is a different mountain")
    P = 'Size'
    SX = t.inp('Size X', 'FLOAT', 120.0, 1.0, None, panel=P, subtype='DISTANCE')
    SY = t.inp('Size Y', 'FLOAT', 120.0, 1.0, None, panel=P, subtype='DISTANCE')
    HT = t.inp('Height', 'FLOAT', 45.0, 0.0, None, panel=P, subtype='DISTANCE')
    res = t.inp('Resolution', 'INT', 200, 8, 2048, panel=P, desc="Vertices along the longer side")
    P = 'Shape'
    fall = t.inp('Peak Falloff', 'FLOAT', 0.85, 0.0, 1.0, panel=P, subtype='FACTOR',
                 desc="1 = one mountain fading to the edges, 0 = open terrain to the borders")
    sharp = t.inp('Sharpness', 'FLOAT', 1.4, 0.2, 5.0, panel=P, desc="Pointy peaks (high) or round hills (low)")
    ridges = t.inp('Ridges', 'FLOAT', 0.6, 0.0, 1.0, panel=P, subtype='FACTOR', desc="Sharp mountain ridges")
    feat = t.inp('Feature Size', 'FLOAT', 45.0, 0.1, None, panel=P, subtype='DISTANCE',
                 desc="Size of the main ridges and valleys")
    detail = t.inp('Detail', 'FLOAT', 7.0, 0.0, 15.0, panel=P)
    rough = t.inp('Roughness', 'FLOAT', 0.5, 0.0, 1.0, panel=P, subtype='FACTOR')
    warp = t.inp('Warp', 'FLOAT', 0.35, 0.0, 3.0, panel=P, desc="Twists the shapes for a natural look")
    stretch = t.inp('Range Stretch', 'FLOAT', 0.0, 0.0, 1.0, panel=P, subtype='FACTOR',
                    desc="Stretch along X for long mountain ranges")
    P = 'Erosion'
    erosion = t.inp('Erosion', 'INT', 2, 0, 50, panel=P, desc="Smooths the terrain (softened by weather)")
    valleys = t.inp('Valleys', 'FLOAT', 0.35, 0.0, 3.0, panel=P, desc="Wider, flatter valley floors")
    floor = t.inp('Floor Level', 'FLOAT', 0.0, 0.0, 0.95, panel=P, subtype='FACTOR',
                  desc="Everything below becomes flat ground")
    plateau = t.inp('Plateau', 'FLOAT', 0.0, 0.0, 0.95, panel=P, subtype='FACTOR',
                    desc="Cut the peaks flat (mesa / table mountain)")
    terr = t.inp('Terraces', 'FLOAT', 0.0, 0.0, 60.0, panel=P, desc="Number of terrace steps (0 = off)")
    tstr = t.inp('Terrace Strength', 'FLOAT', 0.6, 0.0, 1.0, panel=P, subtype='FACTOR')
    P = 'Base'
    solid = t.inp('Solid Base', 'BOOL', True, panel=P, desc="Close the sides with a skirt (a solid chunk)")
    bdepth = t.inp('Base Depth', 'FLOAT', 4.0, 0.0, None, panel=P, subtype='DISTANCE')
    P = 'Surface Detail'
    dlevel = t.inp('Detail Level', 'INT', 0, 0, 3, panel=P, desc="Extra subdivisions for the texture displacement")
    snoise = t.inp('Surface Noise', 'FLOAT', 0.3, 0.0, 5.0, panel=P, subtype='DISTANCE')
    nscale = t.inp('Noise Scale', 'FLOAT', 0.3, 0.001, 20.0, panel=P)
    tx = sh.texture_inputs(t, depth=0.4, tile=8.0)
    fi = sh.finish_inputs(t)

    seedv = t.rand((-100.0, -100.0, -100.0), (100.0, 100.0, 100.0), 'FLOAT_VECTOR', id=0, seed=seed)
    big = t.math('MAXIMUM', SX, SY)
    vx = t.math('MAXIMUM', t.math('ROUND', t.mul(res, t.div(SX, big))), 3.0)
    vy = t.math('MAXIMUM', t.math('ROUND', t.mul(res, t.div(SY, big))), 3.0)
    grid = t.node('GeometryNodeMeshGrid', {'Size X': SX, 'Size Y': SY, 'Vertices X': vx, 'Vertices Y': vy})
    g = t.o(grid, 'Mesh')
    p = t.pos()
    px, py, _ = t.sep(p)
    inv_f = t.div(1.0, feat)
    q = t.vscale(p, inv_f)
    qx, qy, _ = t.sep(q)
    q = t.xyz(t.mul(qx, t.sub(1.0, t.mul(stretch, 0.75))), qy, 0.0)
    wcol = t.noise(t.vadd(t.vscale(q, 0.5), seedv), 1.0, 2.0, 0.5)[1]
    q = t.vadd(q, t.vscale(t.vsub(wcol, (0.5, 0.5, 0.5)), warp))
    fbm = t.noise(t.vadd(q, seedv), 1.0, detail, rough)[0]
    r2 = t.noise(t.vadd(t.vadd(q, seedv), (31.7, 17.3, 5.1)), 0.8, detail, rough)[0]
    ridge = t.math('POWER', t.sub(1.0, t.math('ABSOLUTE', t.madd(r2, 2.0, -1.0))), 2.0)
    nval = t.mix(ridges, fbm, t.madd(ridge, 0.75, t.mul(fbm, 0.35)))
    # radial falloff, its edge wobbled by the noise
    u, v = t.div(px, t.mul(SX, 0.5)), t.div(py, t.mul(SY, 0.5))
    rad = t.math('SQRT', t.add(t.mul(u, u), t.mul(v, v)))
    rad = t.madd(t.sub(t.sep(wcol)[0], 0.5), t.mul(warp, 0.4), rad)
    fo = t.sub(1.0, t.smooth(rad, 0.0, 1.0))
    fo = t.mix(fall, 1.0, fo)
    h = t.mul(nval, fo)
    hmax = t.o(t.stat(g, h), 'Max')
    h = t.div(h, t.math('MAXIMUM', hmax, 1e-5))
    h = t.math('POWER', t.math('MAXIMUM', h, 0.0), t.mul(sharp, t.add(1.0, t.mul(valleys, 0.5))))
    h = t.div(t.math('MAXIMUM', t.sub(h, floor), 0.0), t.sub(1.0, floor))
    top = t.sub(1.0, plateau)
    h = t.div(t.math('MINIMUM', h, top), top)
    # terraces: floor(t) + a smooth step inside each band
    tt = t.mul(h, terr)
    tf = t.math('FRACT', tt)
    step = t.div(t.add(t.math('FLOOR', tt), t.smooth(tf, 0.35, 0.65)), t.math('MAXIMUM', terr, 1e-3))
    h = t.mix(t.mul(tstr, t.compare('GREATER_THAN', terr, 0.5)), h, step)
    g = t.setpos(g, position=t.xyz(px, py, t.mul(h, HT)))
    px, py, pz = t.sep(t.pos())
    g = t.setpos(g, position=t.xyz(px, py, t.blur(pz, erosion, 'FLOAT')))
    # solid skirt: extrude the border edges straight down
    edges_n = t.o(t.node('GeometryNodeInputMeshEdgeNeighbors'), 'Face Count')
    border = t.compare('LESS_EQUAL', edges_n, 1.5)
    ex = t.node('GeometryNodeExtrudeMesh', {'Mesh': g, 'Selection': border, 'Offset': (0.0, 0.0, -1.0),
                                             'Offset Scale': 0.0}, mode='EDGES')
    px, py, pz = t.sep(t.pos())
    sk = t.setpos(t.o(ex, 'Mesh'), position=t.xyz(px, py, t.mul(bdepth, -1.0)), sel=t.o(ex, 'Top'))
    sk = t.o(t.node('GeometryNodeMergeByDistance', {'Geometry': sk, 'Distance': 1e-5}))
    g = t.switch('GEOMETRY', solid, g, sk)
    g = t.o(t.node('GeometryNodeSubdivideMesh', {'Mesh': g, 'Level': dlevel}))
    g = sh.rest_and_displace(t, g, tx)
    p, n = t.pos(), t.normal()
    nf = t.noise(t.vadd(p, seedv), nscale, 6.0, 0.55)[0]
    g = t.setpos(g, offset=t.vscale(n, t.mul(t.sub(nf, 0.5), snoise)))
    g = sh.displace(t, g, tx)
    g = sh.finish(t, g, fi)
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng
