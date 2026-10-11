# Material Layer Mixer - finds the maps of a texture set from file names
# (basecolor / albedo / diffuse, normal (gl/dx), roughness / gloss, metallic, ao, height / displacement, emission).
# Works with the usual naming schemes:  sand_01_basecolor_2k.png, Rock_Normal-OGL.jpg, wood_n.png, wood_r.png ...
import os
import re
import bpy

EXTS = ('.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff', '.exr', '.webp', '.bmp', '.hdr')
WORDS = [
    ('normal', {'normal', 'normals', 'nor', 'nrm', 'norm', 'normalgl', 'normaldx', 'nml', 'normalmap', 'normalogl',
                'normalopengl', 'normaldirectx', 'nrmgl', 'nrmogl', 'nrmdx'}),
    ('rough', {'roughness', 'rough', 'rgh'}),
    ('gloss', {'gloss', 'glossiness', 'smoothness'}),
    ('metal', {'metallic', 'metalness', 'metal', 'mtl'}),
    ('ao', {'ao', 'ambientocclusion', 'occlusion', 'ambient', 'occ'}),
    ('height', {'height', 'displacement', 'disp', 'displace', 'bump', 'heightmap', 'depth'}),
    ('emit', {'emission', 'emissive', 'emit', 'glow', 'selfillum'}),
    ('ignore', {'opacity', 'alpha', 'specular', 'spec', 'reflection', 'refl', 'mask', 'sss', 'translucency',
                'preview', 'thumbnail', 'thumb', 'arm', 'orm', 'cavity', 'curvature', 'idmap', 'id'}),
    ('color', {'basecolor', 'base', 'albedo', 'diffuse', 'diff', 'color', 'colour', 'col', 'basecolour', 'alb'}),
]
SHORT = {'n': 'normal', 'r': 'rough', 'm': 'metal', 'h': 'height', 'd': 'color', 'c': 'color', 'e': 'emit',
         'a': 'color', 'g': 'gloss'}
HINTS = {'gl', 'dx', 'opengl', 'directx', 'ogl'}
GL_HINT = {'gl', 'ogl', 'opengl', 'normalgl', 'normalogl', 'normalopengl', 'nrmgl', 'nrmogl'}
DX_HINT = {'dx', 'directx', 'normaldx', 'normaldirectx', 'nrmdx'}
RES = re.compile(r'^(\d+k|\d{3,5}(px)?|\d+x\d+|lod\d*|hd|sd)$')
DATA = {'normal', 'rough', 'gloss', 'metal', 'ao', 'height'}


def tokens(name):
    stem = os.path.splitext(name)[0]
    stem = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', stem)
    return [t for t in re.split(r'[^a-z0-9]+', stem.lower()) if t]


def classify(name):
    """-> (set key, channel, normal convention: 'GL' / 'DX' when the name says so, else '')"""
    tk = tokens(name) or ['image']
    chan, ci = None, None
    first = 0 if len(tk) == 1 else 1             # a channel word at the very start is part of the name (metal_iron)
    for i in range(len(tk) - 1, first - 1, -1):
        for c, words in WORDS:
            if tk[i] in words:
                chan, ci = c, i
                break
        if chan:
            break
    if chan is None and len(tk) > 1 and tk[-1] in SHORT:
        chan, ci = SHORT[tk[-1]], len(tk) - 1
    if chan is None:
        chan = 'color'
    conv = 'DX' if any(t in DX_HINT for t in tk) else 'GL' if any(t in GL_HINT for t in tk) else ''
    every = set().union(*(w for _, w in WORDS))
    key = [t for j, t in enumerate(tk) if j != ci and t not in HINTS and not RES.match(t)
           and not (j >= first and j > 0 and t in every)]
    return '_'.join(key) or tk[0], chan, conv


def display_name(fname):
    """file name without its channel / resolution / convention tokens, original casing: Stylized_MetalGrid_01"""
    every = set().union(*(w for _, w in WORDS)) | HINTS | GL_HINT | DX_HINT
    parts = [p for p in re.split(r'[_\-. ]+', os.path.splitext(fname)[0]) if p]
    keep = [p for i, p in enumerate(parts) if not (i and (p.lower() in every or RES.match(p.lower())))]
    while len(keep) > 1 and keep[-1].lower() in SHORT:
        keep.pop()
    return '_'.join(keep) or os.path.splitext(fname)[0]


def scan_folder(folder):
    sets = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return sets
    for f in names:
        if not f.lower().endswith(EXTS):
            continue
        key, chan, conv = classify(f)
        if chan == 'ignore':
            continue
        s = sets.setdefault(key, {})
        s.setdefault('name', display_name(f))
        path = os.path.join(folder, f)
        if chan == 'normal':
            s.setdefault('_normals', {}).setdefault(conv, path)
        elif chan not in s:
            s[chan] = path
    for s in sets.values():                      # prefer OpenGL (Blender / Roblox) normals
        nr = s.pop('_normals', None)
        if not nr:
            continue
        if 'GL' in nr:
            s['normal'], s['normal_dx'] = nr['GL'], False
        elif '' in nr:                           # unlabeled: OpenGL, unless an explicit OpenGL twin exists (handled above)
            s['normal'], s['normal_dx'] = nr[''], False
        else:
            s['normal'], s['normal_dx'] = nr['DX'], True
    # a set needs at least a color or normal map
    return {k: v for k, v in sets.items() if 'color' in v or 'normal' in v}


def sets_for_files(folder, filenames):
    allsets = scan_folder(folder)
    want = []
    for f in filenames:
        if not f:
            continue
        key = classify(f)[0]
        if key in allsets and key not in want:
            want.append(key)
    if not want and len(allsets) == 1:
        want = list(allsets)
    return [(k, allsets[k]) for k in want], allsets


def load_image(path, data):
    img = bpy.data.images.load(path, check_existing=True)
    want = 'Non-Color' if data else 'sRGB'
    try:
        if img.colorspace_settings.name != want:
            img.colorspace_settings.name = want
    except TypeError:
        pass
    return img


def apply_set(L, s):
    """fill a layer's texture slots from a scanned set"""
    slot = {'color': 'ts_color', 'normal': 'ts_normal', 'rough': 'ts_rough', 'gloss': 'ts_rough',
            'metal': 'ts_metal', 'ao': 'ts_ao', 'height': 'ts_height', 'emit': 'ts_emit'}
    for k in ('ts_color', 'ts_normal', 'ts_rough', 'ts_metal', 'ts_ao', 'ts_height', 'ts_emit'):
        setattr(L, k, None)
    for chan, prop in slot.items():
        if chan in s and getattr(L, prop) is None:
            setattr(L, prop, load_image(s[chan], chan in DATA))
    L.ts_gloss = 'rough' not in s and 'gloss' in s
    L.ts_normal_dx = bool(s.get('normal_dx'))
