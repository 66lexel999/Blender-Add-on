# Nature Forge - Rock / boulder generator (Geometry Nodes).
# ico sphere -> big lumps -> size / taper -> random planar facet cuts (chiselled faces) -> flat bottom -> rounded
# edges -> subdivision -> strata grooves, cracks, fine noise -> texture height map -> masks / material
from . import nodekit as nk
from . import gn_shared as sh

NAME, VERSION = 'NF Rock', 5


def build(force=False):
    ng, t = nk.new_group(NAME, version=VERSION, force=force)
    if t is None:
        return ng
    ng['nf_kind'] = 'ROCK'
    t.inp('Geometry', 'GEO')
    seed = t.inp('Seed', 'INT', 0, desc="Every seed is a different rock")
    P = 'Shape'
    size = t.inp('Size', 'VEC', (1.6, 1.3, 1.0), panel=P, subtype='TRANSLATION', desc="Width / depth / height")
    res = t.inp('Resolution', 'INT', 5, 1, 7, panel=P, desc="Base mesh density (ico sphere subdivisions)")
    lumps = t.inp('Lumps', 'FLOAT', 0.35, 0.0, 3.0, panel=P, desc="Big, soft bumps that break the round shape")
    lscale = t.inp('Lump Scale', 'FLOAT', 1.1, 0.05, 20.0, panel=P, desc="Size of the bumps (higher = more, smaller)")
    taper = t.inp('Taper', 'FLOAT', 0.1, -1.0, 1.0, panel=P, subtype='FACTOR',
                  desc="Narrower top (positive) or narrower bottom (negative)")
    flatb = t.inp('Flat Bottom', 'FLOAT', 0.35, 0.0, 1.0, panel=P, subtype='FACTOR',
                  desc="Cut the underside flat so the rock sits on the ground")
    ground = t.inp('Sit on Ground', 'BOOL', True, panel=P, desc="Move the rock so its lowest point is at the origin")
    P = 'Facets'
    nfac = t.inp('Facets', 'INT', 12, 0, 80, panel=P, desc="Number of flat, chiselled cuts (0 = round pebble)")
    fdepth = t.inp('Facet Depth', 'FLOAT', 0.4, 0.0, 1.0, panel=P, subtype='FACTOR', desc="How deep the cuts go")
    fflat = t.inp('Flatness', 'FLOAT', 0.85, 0.0, 1.0, panel=P, subtype='FACTOR',
                  desc="1 = perfectly flat cut faces, lower = softly pressed in")
    chips = t.inp('Chips', 'FLOAT', 0.35, 0.0, 2.0, panel=P,
                  desc="Small broken-off flakes: tilted flat patches with little steps between them")
    chscale = t.inp('Chip Scale', 'FLOAT', 3.0, 0.1, 50.0, panel=P, desc="Chips per metre")
    P = 'Erosion'
    smooth = t.inp('Smooth Edges', 'INT', 2, 0, 60, panel=P, desc="Rounds sharp edges and corners (weathering)")
    cracks = t.inp('Cracks', 'FLOAT', 0.25, 0.0, 1.0, panel=P, subtype='FACTOR', desc="Depth of the crack lines")
    cscale = t.inp('Crack Scale', 'FLOAT', 1.8, 0.01, 50.0, panel=P, desc="Crack pattern frequency")
    cwidth = t.inp('Crack Width', 'FLOAT', 0.1, 0.0, 0.5, panel=P, subtype='FACTOR')
    strata = t.inp('Strata', 'FLOAT', 0.0, 0.0, 1.0, panel=P, subtype='FACTOR',
                   desc="Sedimentary layer grooves (sandstone, slate)")
    sscale = t.inp('Strata Scale', 'FLOAT', 7.0, 0.1, 100.0, panel=P, desc="Layers per metre")
    stilt = t.inp('Strata Tilt', 'FLOAT', 0.15, -1.57, 1.57, panel=P, subtype='ANGLE')
    P = 'Surface Detail'
    dlevel = t.inp('Detail Level', 'INT', 1, 0, 4, panel=P,
                   desc="Extra subdivisions before the fine detail (needed for crisp texture displacement)")
    smoothsub = t.inp('Smooth Subdivision', 'BOOL', False, panel=P,
                      desc="Round subdivision (Catmull-Clark) or keep the faceted shape exactly (Simple)")
    snoise = t.inp('Surface Noise', 'FLOAT', 0.15, 0.0, 2.0, panel=P, desc="Procedural pits and bumps")
    nscale = t.inp('Noise Scale', 'FLOAT', 3.0, 0.01, 100.0, panel=P)
    tx = sh.texture_inputs(t, depth=0.06, tile=1.0)
    fi = sh.finish_inputs(t)

    seedv = t.rand((-100.0, -100.0, -100.0), (100.0, 100.0, 100.0), 'FLOAT_VECTOR', id=0, seed=seed)
    half = t.vscale(size, 0.5)
    hx, hy, hz = t.sep(half)
    avg = t.div(t.add(t.add(hx, hy), hz), 1.5)               # mean full size
    ico = t.node('GeometryNodeMeshIcoSphere', {'Radius': 1.0, 'Subdivisions': res})
    g = t.o(ico, 'Mesh')
    p, n = t.pos(), t.normal()
    fac, col = t.noise(t.vadd(p, seedv), lscale, 2.0, 0.5)
    bump = t.vadd(t.vscale(n, t.mul(t.sub(fac, 0.5), t.mul(lumps, 0.8))),
                  t.vscale(t.vsub(col, (0.5, 0.5, 0.5)), t.mul(lumps, 0.3)))
    q = t.vmul(t.vadd(p, bump), half)
    qx, qy, qz = t.sep(q)
    tz = t.mrange(qz, t.mul(hz, -1.0), hz, 0.0, 1.0)
    s = t.sub(1.0, t.mul(taper, tz))
    g = t.setpos(g, position=t.xyz(t.mul(qx, s), t.mul(qy, s), qz))
    fc = t.group(sh.facet_cuts(), {'Geometry': g, 'Count': nfac, 'Depth': fdepth, 'Flatness': fflat,
                                   'Seed': t.add(seed, 1), 'Extent': half})
    g = t.o(fc)
    zcut = t.mul(hz, t.madd(flatb, 0.65, -1.0))
    px, py, pz = t.sep(t.pos())
    g = t.setpos(g, position=t.xyz(px, py, t.math('MAXIMUM', pz, zcut)))
    g = t.setpos(g, position=t.blur(t.pos(), smooth))
    cc = t.node('GeometryNodeSubdivisionSurface', {'Mesh': g, 'Level': dlevel})
    sm = t.node('GeometryNodeSubdivideMesh', {'Mesh': g, 'Level': dlevel})
    g = t.switch('GEOMETRY', smoothsub, t.o(sm), t.o(cc))
    g = sh.rest_and_displace(t, g, tx)

    p, n = t.pos(), t.normal()
    # strata grooves along a tilted axis, wobbled by noise
    axis = t.xyz(t.math('SINE', stilt), 0.0, t.math('COSINE', stilt))
    wob = t.noise(t.vadd(p, seedv), 1.5, 2.0, 0.5)[0]
    band = t.math('SINE', t.mul(t.madd(t.dot(p, axis), sscale, t.mul(wob, 2.0)), 6.2831853))
    groove = t.math('POWER', t.sub(1.0, t.math('ABSOLUTE', band)), 6.0)
    off = t.mul(groove, t.mul(strata, t.mul(avg, -0.035)))
    # cracks: voronoi cell borders, warped
    warp = t.vscale(t.vsub(t.noise(t.vadd(p, seedv), t.mul(cscale, 1.7), 3.0, 0.5)[1], (0.5, 0.5, 0.5)),
                    t.div(0.35, cscale))
    vor = t.voronoi(t.vadd(t.vadd(p, warp), seedv), cscale, 'DISTANCE_TO_EDGE')
    crack = t.sub(1.0, t.smooth(t.o(vor, 'Distance'), 0.0, t.math('MAXIMUM', cwidth, 1e-4)))
    off = t.madd(crack, t.mul(cracks, t.mul(avg, -0.04)), off)
    # chips: inside every voronoi cell a tilted plane (linear in p) -> flat flakes with steps at the borders
    cv = t.voronoi(t.vadd(t.vadd(p, warp), seedv), chscale, 'F1')
    tilt = t.vsub(t.vscale(t.o(cv, 'Color'), 2.0), (1.0, 1.0, 1.0))
    chip = t.mul(t.dot(t.vsub(t.vadd(t.vadd(p, warp), seedv), t.o(cv, 'Position')), tilt), chscale)
    off = t.madd(t.math('MINIMUM', chip, 0.0), t.mul(chips, t.mul(avg, 0.03)), off)
    # fine pits and bumps
    nf = t.noise(t.vadd(p, seedv), nscale, 4.0, 0.55)[0]
    off = t.madd(t.sub(nf, 0.5), t.mul(snoise, t.mul(avg, 0.06)), off)
    g = t.setpos(g, offset=t.vscale(n, off))
    g = sh.displace(t, g, tx)
    g = sh.finish(t, g, fi)
    zmin = t.o(t.stat(g, t.sep(t.pos())[2]), 'Min')
    g = t.switch('GEOMETRY', ground, g, t.setpos(g, offset=t.xyz(0.0, 0.0, t.mul(zmin, -1.0))))
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng
