# Nature Forge - Cliff / rock wall generator (Geometry Nodes).
# dense box (bottom at z = 0, front faces -Y) -> rounded corners -> taper / overhang / curve -> big rock masses +
# planar cuts -> flat back / bottom -> subdivision -> horizontal ledges, vertical cracks (columns), fine noise ->
# texture height map -> masks / material
from . import nodekit as nk
from . import gn_shared as sh

NAME, VERSION = 'NF Cliff', 5


def build(force=False):
    ng, t = nk.new_group(NAME, version=VERSION, force=force)
    if t is None:
        return ng
    ng['nf_kind'] = 'CLIFF'
    t.inp('Geometry', 'GEO')
    seed = t.inp('Seed', 'INT', 0, desc="Every seed is a different cliff")
    P = 'Shape'
    W = t.inp('Width', 'FLOAT', 8.0, 0.1, None, panel=P, subtype='DISTANCE')
    D = t.inp('Thickness', 'FLOAT', 3.0, 0.1, None, panel=P, subtype='DISTANCE', desc="Depth front to back")
    H = t.inp('Height', 'FLOAT', 6.0, 0.1, None, panel=P, subtype='DISTANCE')
    dens = t.inp('Resolution', 'FLOAT', 5.0, 0.5, 40.0, panel=P,
                 desc="Base vertices per metre (higher = more detail, slower)")
    rnd = t.inp('Roundness', 'FLOAT', 0.12, 0.0, 1.0, panel=P, subtype='FACTOR', desc="Rounded corners and edges")
    taper = t.inp('Taper', 'FLOAT', 0.15, -1.0, 1.0, panel=P, subtype='FACTOR', desc="Narrower towards the top")
    over = t.inp('Overhang', 'FLOAT', 0.2, -1.0, 1.0, panel=P, subtype='FACTOR',
                 desc="Top leans forward (positive) or back (negative)")
    curve = t.inp('Curve', 'FLOAT', 0.0, -1.0, 1.0, panel=P, subtype='FACTOR',
                  desc="Bend the wall around you (positive = concave bay)")
    back = t.inp('Flat Back', 'FLOAT', 0.0, 0.0, 1.0, panel=P, subtype='FACTOR',
                 desc="Flatten the back so the piece sits against terrain")
    P = 'Rock Masses'
    mass = t.inp('Masses', 'FLOAT', 0.7, 0.0, 2.0, panel=P, desc="Big bulging rock masses")
    mscale = t.inp('Mass Scale', 'FLOAT', 0.2, 0.01, 10.0, panel=P, desc="Masses per metre")
    nfac = t.inp('Facets', 'INT', 24, 0, 120, panel=P, desc="Big flat broken faces")
    fdepth = t.inp('Facet Depth', 'FLOAT', 0.3, 0.0, 1.0, panel=P, subtype='FACTOR')
    smooth = t.inp('Smooth Edges', 'INT', 2, 0, 60, panel=P)
    P = 'Ledges'
    ledges = t.inp('Ledges', 'FLOAT', 0.3, 0.0, 2.0, panel=P, desc="Horizontal rock layers stepping out")
    lcount = t.inp('Ledge Count', 'FLOAT', 4.0, 0.0, 100.0, panel=P, desc="Layers over the full height")
    lsharp = t.inp('Ledge Sharpness', 'FLOAT', 0.7, 0.0, 1.0, panel=P, subtype='FACTOR')
    ltilt = t.inp('Ledge Tilt', 'FLOAT', 0.05, -1.0, 1.0, panel=P, subtype='ANGLE')
    lwarp = t.inp('Ledge Warp', 'FLOAT', 0.9, 0.0, 3.0, panel=P, desc="Wavy, irregular layers")
    P = 'Cracks'
    cracks = t.inp('Cracks', 'FLOAT', 0.4, 0.0, 2.0, panel=P, subtype='FACTOR', desc="Crack depth")
    cscale = t.inp('Crack Scale', 'FLOAT', 0.35, 0.01, 20.0, panel=P, desc="Cracks per metre")
    cwidth = t.inp('Crack Width', 'FLOAT', 0.06, 0.0, 0.5, panel=P, subtype='FACTOR')
    cvert = t.inp('Vertical Cracks', 'FLOAT', 0.45, 0.0, 1.0, panel=P, subtype='FACTOR',
                  desc="Stretch the cracks vertically (basalt columns, granite joints)")
    P = 'Surface Detail'
    dlevel = t.inp('Detail Level', 'INT', 1, 0, 4, panel=P)
    snoise = t.inp('Surface Noise', 'FLOAT', 0.25, 0.0, 3.0, panel=P)
    nscale = t.inp('Noise Scale', 'FLOAT', 1.5, 0.01, 50.0, panel=P)
    tx = sh.texture_inputs(t, depth=0.12, tile=2.0)
    fi = sh.finish_inputs(t)

    seedv = t.rand((-100.0, -100.0, -100.0), (100.0, 100.0, 100.0), 'FLOAT_VECTOR', id=0, seed=seed)
    hw, hd, hh = t.mul(W, 0.5), t.mul(D, 0.5), t.mul(H, 0.5)
    vcount = [t.math('MAXIMUM', t.math('ROUND', t.mul(L, dens)), 3.0) for L in (W, D, H)]
    cube = t.node('GeometryNodeMeshCube', {'Size': t.xyz(W, D, H), 'Vertices X': vcount[0],
                                           'Vertices Y': vcount[1], 'Vertices Z': vcount[2]})
    g = t.o(cube, 'Mesh')
    # rounded box: clamp to the inner box, push out along the corner direction by the radius
    half = t.xyz(hw, hd, hh)
    r = t.mul(rnd, t.math('MINIMUM', t.math('MINIMUM', hw, hd), hh))
    inner = t.vsub(half, t.xyz(r, r, r))
    q = t.pos()
    qc = t.vmath('MINIMUM', t.vmath('MAXIMUM', q, t.vscale(inner, -1.0)), inner)
    q = t.vadd(qc, t.vscale(t.norm(t.vsub(q, qc)), r))
    qx, qy, qz = t.sep(q)
    z = t.add(qz, hh)                                         # bottom at 0
    tz = t.div(z, H)
    s = t.sub(1.0, t.mul(taper, tz))
    x = t.mul(qx, s)
    y = t.mul(qy, s)
    y = t.sub(y, t.mul(t.mul(over, D), t.mul(tz, tz)))       # front is -Y
    u = t.div(x, hw)
    y = t.sub(y, t.mul(t.mul(curve, hw), t.mul(u, u)))
    g = t.setpos(g, position=t.xyz(x, y, z))
    # rock masses: noise stretched vertically -> buttresses and gullies on the walls, softer on top
    p, n = t.pos(), t.normal()
    px, py, pz = t.sep(p)
    mv = t.xyz(t.mul(px, mscale), t.mul(py, mscale), t.mul(pz, t.mul(mscale, t.sub(1.0, t.mul(cvert, 0.7)))))
    fac, col = t.noise(t.vadd(mv, seedv), 1.0, 3.0, 0.55)
    mdim = t.math('MINIMUM', D, W)
    wall = t.madd(t.sub(1.0, t.math('ABSOLUTE', t.sep(n)[2])), 0.75, 0.25)
    g = t.setpos(g, offset=t.vadd(t.vscale(n, t.mul(t.sub(fac, 0.5), t.mul(mass, t.mul(mdim, t.mul(wall, 0.6))))),
                                  t.vscale(t.vsub(col, (0.5, 0.5, 0.5)), t.mul(mass, t.mul(mdim, 0.12)))))
    fc = t.group(sh.facet_cuts(), {'Geometry': g, 'Count': nfac, 'Depth': fdepth, 'Flatness': 0.9,
                                   'Seed': t.add(seed, 3), 'Extent': half, 'Center': t.xyz(0.0, 0.0, hh),
                                   'Side Bias': 0.6, 'Box Shape': 0.85})
    g = t.o(fc)
    px, py, pz = t.sep(t.pos())
    ymax = t.mul(hd, t.sub(1.0, t.mul(back, 0.9)))
    g = t.setpos(g, position=t.xyz(px, t.math('MINIMUM', py, ymax), t.math('MAXIMUM', pz, 0.0)))
    g = t.setpos(g, position=t.blur(t.pos(), smooth))
    smoothsub = t.inp('Smooth Subdivision', 'BOOL', False, panel='Surface Detail',
                      desc="Round subdivision (Catmull-Clark) or keep the broken shape crisp (Simple)")
    cc = t.node('GeometryNodeSubdivisionSurface', {'Mesh': g, 'Level': dlevel})
    sm = t.node('GeometryNodeSubdivideMesh', {'Mesh': g, 'Level': dlevel})
    g = t.switch('GEOMETRY', smoothsub, t.o(sm), t.o(cc))
    g = sh.rest_and_displace(t, g, tx)

    p, n = t.pos(), t.normal()
    px, py, pz = t.sep(p)
    nz = t.sep(n)[2]
    side = t.sub(1.0, t.math('ABSOLUTE', nz))                # ledges only step out of the walls
    wv = t.noise(t.vadd(t.vscale(p, 0.25), seedv), 1.0, 2.0, 0.5)[0]
    hz = t.madd(t.div(pz, H), lcount, t.madd(t.sub(wv, 0.5), lwarp, t.mul(t.math('TANGENT', ltilt),
                                                                           t.div(px, H))))
    f = t.math('FRACT', hz)
    prof = t.smooth(f, 0.0, t.math('MAXIMUM', t.sub(1.0, t.mul(lsharp, 0.97)), 0.02))
    lvar = t.smooth(t.noise(t.vadd(t.vscale(p, 0.15), seedv), 1.0, 1.0, 0.5)[0], 0.35, 0.65)
    off = t.mul(t.mul(t.sub(prof, 0.5), t.mul(ledges, t.mul(D, 0.08))), t.mul(side, lvar))
    # cracks (stretched vertically -> columns / joints)
    warp = t.vscale(t.vsub(t.noise(t.vadd(p, seedv), t.mul(cscale, 2.0), 3.0, 0.5)[1], (0.5, 0.5, 0.5)),
                    t.div(0.4, cscale))
    cv = t.vadd(t.vadd(p, warp), seedv)
    cx, cy, cz = t.sep(cv)
    cv = t.xyz(cx, cy, t.mul(cz, t.sub(1.0, t.mul(cvert, 0.85))))
    vor = t.voronoi(cv, cscale, 'DISTANCE_TO_EDGE', detail=1.0)
    crack = t.sub(1.0, t.smooth(t.o(vor, 'Distance'), 0.0, t.math('MAXIMUM', cwidth, 1e-4)))
    off = t.madd(crack, t.mul(cracks, t.mul(mdim, -0.06)), off)
    nf = t.noise(t.vadd(p, seedv), nscale, 6.0, 0.6)[0]
    off = t.madd(t.sub(nf, 0.5), t.mul(snoise, 0.25), off)
    g = t.setpos(g, offset=t.vscale(n, off))
    g = sh.displace(t, g, tx)
    px, py, pz = t.sep(t.pos())
    g = t.setpos(g, position=t.xyz(px, py, t.math('MAXIMUM', pz, 0.0)))   # keep the foot on the ground
    g = sh.finish(t, g, fi)
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng
