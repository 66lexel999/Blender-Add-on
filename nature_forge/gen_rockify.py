# Nature Forge - "Rockify": the displacement-texture trick for ANY mesh you modelled yourself.
# subdivide -> (optional) procedural noise -> the texture set's height map pushes the surface out (triplanar, no UVs
# needed) -> masks. The material then puts the colour / normal / roughness maps exactly on those bumps.
from . import nodekit as nk
from . import gn_shared as sh

NAME, VERSION = 'NF Rockify', 2


def build(force=False):
    ng, t = nk.new_group(NAME, version=VERSION, force=force)
    if t is None:
        return ng
    ng['nf_kind'] = 'ROCKIFY'
    geo = t.inp('Geometry', 'GEO')
    P = 'Subdivision'
    lvl = t.inp('Subdivisions', 'INT', 3, 0, 6, panel=P, desc="More = finer displacement (4x faces per level)")
    smooth = t.inp('Smooth Shape', 'BOOL', False, panel=P,
                   desc="Round the shape while subdividing (Catmull-Clark) or keep it exactly (Simple)")
    P = 'Procedural'
    seed = t.inp('Seed', 'INT', 0, panel=P)
    lumps = t.inp('Lumps', 'FLOAT', 0.0, 0.0, None, panel=P, subtype='DISTANCE', desc="Big soft bumps (depth)")
    lscale = t.inp('Lump Scale', 'FLOAT', 1.0, 0.01, 100.0, panel=P)
    snoise = t.inp('Surface Noise', 'FLOAT', 0.0, 0.0, None, panel=P, subtype='DISTANCE')
    nscale = t.inp('Noise Scale', 'FLOAT', 6.0, 0.01, 200.0, panel=P)
    tx = sh.texture_inputs(t, depth=0.05, tile=1.0)
    P = 'Material'
    mat = t.inp('Material', 'MAT', panel=P, desc="Empty = keep the object's own materials")
    use_mat = t.inp('Replace Material', 'BOOL', False, panel=P)
    spread = t.inp('Cavity Spread', 'INT', 3, 1, 30, panel=P)

    cc = t.node('GeometryNodeSubdivisionSurface', {'Mesh': geo, 'Level': lvl})
    sm = t.node('GeometryNodeSubdivideMesh', {'Mesh': geo, 'Level': lvl})
    g = t.switch('GEOMETRY', smooth, t.o(sm), t.o(cc))
    g = sh.rest_and_displace(t, g, tx)
    seedv = t.rand((-100.0, -100.0, -100.0), (100.0, 100.0, 100.0), 'FLOAT_VECTOR', id=0, seed=seed)
    p, n = t.pos(), t.normal()
    f1 = t.noise(t.vadd(p, seedv), lscale, 2.0, 0.5)[0]
    f2 = t.noise(t.vadd(p, seedv), nscale, 6.0, 0.6)[0]
    off = t.add(t.mul(t.sub(f1, 0.5), t.mul(lumps, 2.0)), t.mul(t.sub(f2, 0.5), t.mul(snoise, 2.0)))
    g = t.setpos(g, offset=t.vscale(n, off))
    g = sh.displace(t, g, tx)
    g = sh.finish(t, g, {'spread': spread, 'mat': mat}, set_material=use_mat)
    t.out('Geometry', 'GEO', g)
    t.layout()
    return ng
