# Nature Forge - texture library: finds every texture set in your folders, no manual map hookup.
#
# Understands both common layouts:
#   one folder per material, maps named by channel only  ->  V7_Wood_Base_02/color.jpg, normal.jpg, height.png, ...
#   many materials in one folder, maps named by prefix   ->  Textures/Food_Cheese_basecolor.png, Food_Cheese_normal.png
# Channel words: basecolor / albedo / diffuse / color, normal (GL / DX), roughness / gloss, metallic, ao,
# height / displacement / bump, opacity / alpha, emission, packed ORM / ARM. An info.json next to the maps is read
# for name / category / displacement depth / real-world size / normal convention when it has them.
import hashlib
import json
import os
import re
import bpy

EXTS = ('.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff', '.exr', '.webp', '.bmp')
WORDS = [
    ('normal', {'normal', 'normals', 'nor', 'nrm', 'norm', 'normalgl', 'normaldx', 'nml', 'normalmap', 'normalogl',
                'normalopengl', 'normaldirectx', 'nrmgl', 'nrmogl', 'nrmdx'}),
    ('rough', {'roughness', 'rough', 'rgh'}),
    ('gloss', {'gloss', 'glossiness', 'smoothness'}),
    ('metal', {'metallic', 'metalness', 'metal', 'mtl'}),
    ('ao', {'ao', 'ambientocclusion', 'occlusion', 'ambient', 'occ'}),
    ('height', {'height', 'displacement', 'disp', 'displace', 'bump', 'heightmap', 'depth', 'dispmap'}),
    ('emit', {'emission', 'emissive', 'emit', 'glow', 'selfillum'}),
    ('alpha', {'opacity', 'alpha', 'transparency', 'cutout'}),
    ('orm', {'orm', 'arm', 'occlusionroughnessmetallic'}),
    ('preview', {'preview', 'thumbnail', 'thumb', 'render', 'sphere', 'swatch', 'icon'}),
    ('ignore', {'specular', 'spec', 'reflection', 'refl', 'sss', 'translucency', 'cavity', 'curvature', 'idmap',
                'id', 'mask', 'scattering', 'sheen', 'coat'}),
    ('color', {'basecolor', 'base', 'albedo', 'diffuse', 'diff', 'color', 'colour', 'col', 'basecolour', 'alb'}),
]
SHORT = {'n': 'normal', 'r': 'rough', 'm': 'metal', 'h': 'height', 'd': 'color', 'c': 'color', 'e': 'emit',
         'a': 'color', 'g': 'gloss', 'o': 'alpha'}
HINTS = {'gl', 'dx', 'opengl', 'directx', 'ogl'}
GL_HINT = {'gl', 'ogl', 'opengl', 'normalgl', 'normalogl', 'normalopengl', 'nrmgl', 'nrmogl'}
DX_HINT = {'dx', 'directx', 'normaldx', 'normaldirectx', 'nrmdx'}
RES = re.compile(r'^(\d+k|\d{3,5}(px)?|\d+x\d+|lod\d*|hd|sd|png|jpg)$')
DATA = {'normal', 'rough', 'gloss', 'metal', 'ao', 'height', 'alpha', 'orm'}
CHANNELS = ('color', 'normal', 'rough', 'gloss', 'metal', 'ao', 'height', 'alpha', 'emit', 'orm')
EVERY = set().union(*(w for _, w in WORDS))
SKIP_DIRS = {'__pycache__', '.git', 'sbs', 'sbsar', 'thumbnails', '.thumbnails', 'previews'}


def tokens(name):
    stem = os.path.splitext(name)[0]
    stem = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', stem)
    return [t for t in re.split(r'[^a-z0-9]+', stem.lower()) if t]


def classify(name):
    """-> (set key, channel, normal convention 'GL'/'DX'/'', channel named explicitly?)
    key '' = the file name is only a channel word (color.jpg) -> the set is the folder"""
    tk = tokens(name) or ['image']
    chan, ci = None, None
    plain = [t for t in tk if t not in HINTS and not RES.match(t)]
    first = 0 if len(plain) <= 1 else 1           # a channel word at the very start is part of the name (metal_iron)
    for i in range(len(tk) - 1, first - 1, -1):
        for c, words in WORDS:
            if tk[i] in words:
                chan, ci = c, i
                break
        if chan:
            break
    explicit = chan is not None
    if chan is None and len(tk) > 1 and tk[-1] in SHORT:
        chan, ci, explicit = SHORT[tk[-1]], len(tk) - 1, True
    if chan is None:
        chan = 'color'
    conv = 'DX' if any(t in DX_HINT for t in tk) else 'GL' if any(t in GL_HINT for t in tk) else ''
    key = [t for j, t in enumerate(tk) if j != ci and t not in HINTS and not RES.match(t)
           and not (j >= first and j > 0 and t in EVERY)]
    if len(plain) <= 1 and explicit:
        key = []
    if explicit and key and all(t in EVERY or RES.match(t) or t in HINTS for t in key):
        key = []                                  # 'base_color_2k.png'
    return '_'.join(key), chan, conv, explicit


def display_name(fname):
    every = EVERY | HINTS | GL_HINT | DX_HINT
    parts = [p for p in re.split(r'[_\-. ]+', os.path.splitext(fname)[0]) if p]
    keep = [p for i, p in enumerate(parts) if not (i and (p.lower() in every or RES.match(p.lower())))]
    while len(keep) > 1 and keep[-1].lower() in SHORT:
        keep.pop()
    return '_'.join(keep) or os.path.splitext(fname)[0]


# ---------------------------------------------------------------------------------------------- info.json
def _walk_json(d, depth=0):
    if depth > 3:
        return
    if isinstance(d, dict):
        for k, v in d.items():
            yield str(k).lower(), v
            if isinstance(v, (dict, list)) and str(k).lower() not in ('files', 'maps', 'textures'):
                yield from _walk_json(v, depth + 1)
    elif isinstance(d, list):
        for v in d[:50]:
            if isinstance(v, (dict, list)):
                yield from _walk_json(v, depth + 1)


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, tuple)) and v and all(isinstance(x, (int, float)) for x in v[:3]):
        return float(max(v[:3]))
    if isinstance(v, str):
        m = re.match(r'^\s*([0-9]*\.?[0-9]+)\s*(mm|cm|m)?\s*$', v.lower())
        if m:
            f = float(m.group(1))
            return f * {'mm': 0.001, 'cm': 0.01, 'm': 1.0, None: 1.0}[m.group(2)]
    return None


def parse_info(path):
    """best effort: only clearly named keys are used, everything found is shown in the UI"""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    if isinstance(data, dict):
        # explicit map list, e.g. {"files": {"color": "color.jpg", "height": "height.png", ...}}
        fm = data.get('files') or data.get('maps') or data.get('textures')
        if isinstance(fm, dict):
            files = {}
            for k, v in fm.items():
                if not isinstance(v, str):
                    continue
                chan = next((c for c, words in WORDS if str(k).lower().replace('_', '') in words or
                             str(k).lower() == c), None)
                if chan and chan not in ('ignore', 'preview') and chan not in files:
                    files[chan] = v
            if files:
                out['files'] = files
        # mean height = the level that keeps the surface where it was (best displacement mid level)
        for key in ('h_mean', 'height_mean', 'mean_height', 'midlevel', 'mid_level'):
            f = _num(data.get(key))
            if f is not None and 0.0 <= f <= 1.0:
                out['mid'] = round(f, 4)
                break
        if isinstance(data.get('id'), str):
            out['id'] = data['id'].strip()[:80]
    for k, v in _walk_json(data):
        kk = k.replace(' ', '_').replace('-', '_')
        if kk in ('files', 'maps', 'textures'):
            continue
        if kk in ('name', 'title', 'display_name', 'material_name') and isinstance(v, str) and 'name' not in out:
            out['name'] = v.strip()[:80]
        elif kk in ('category', 'categories', 'type', 'group', 'collection', 'tags') and 'category' not in out:
            if isinstance(v, str):
                out['category'] = v.strip()[:40]
            elif isinstance(v, list) and v and isinstance(v[0], str):
                out['category'] = v[0].strip()[:40]
        elif any(w in kk for w in ('displacement', 'disp_', 'height_scale', 'heightscale', 'depth', 'disp')) \
                and 'depth' not in out:
            f = _num(v)
            if f is not None and 0.0 < f < 50.0:
                # values in centimetres are common in material packs: anything over 1 is treated as cm
                out['depth'] = f / 100.0 if f > 1.0 else f
        elif any(w in kk for w in ('real_size', 'realsize', 'physical_size', 'world_size', 'dimension', 'size_m',
                                   'tile_size', 'tilesize', 'real_world', 'scale_m')) and 'tile' not in out:
            f = _num(v)
            if f is not None and 0.0 < f < 1000.0:
                out['tile'] = f / 100.0 if f > 20.0 else f
        elif kk in ('size', 'scale', 'width') and 'tile' not in out:
            f = _num(v)
            if f is not None and 0.05 <= f <= 20.0:      # bigger numbers are usually pixels, not metres
                out['tile'] = f
        elif 'normal' in kk and isinstance(v, str) and 'normal_dx' not in out and not os.path.splitext(v)[1]:
            s = v.lower()
            if 'directx' in s or s in ('dx', 'd3d') or 'dx' in s.split():
                out['normal_dx'] = True
            elif 'opengl' in s or s in ('gl', 'ogl'):
                out['normal_dx'] = False
    return out


# ---------------------------------------------------------------------------------------------- scanning
def _sid(path, key):
    return hashlib.md5(f"{os.path.normcase(path)}|{key}".encode('utf-8', 'replace')).hexdigest()[:12]


def scan_dir(dirpath, files, root):
    imgs = sorted(f for f in files if f.lower().endswith(EXTS))
    if not imgs:
        return []
    groups = {}
    for f in imgs:
        key, chan, conv, explicit = classify(f)
        if chan == 'ignore':
            continue
        g = groups.setdefault(key, {'files': [], 'explicit_color': False})
        g['files'].append((f, chan, conv))
        if chan == 'color' and explicit:
            g['explicit_color'] = True
    info = {}
    for jf in ('info.json', 'material.json', 'meta.json', 'metadata.json'):
        if jf in files:
            info = parse_info(os.path.join(dirpath, jf))
            break
    folder = os.path.basename(dirpath.rstrip('/\\'))
    rel = os.path.relpath(dirpath, root) if root else ''
    out = []
    for key, g in groups.items():
        maps, normals, preview = {}, {}, None
        for f, chan, conv in g['files']:
            p = os.path.join(dirpath, f)
            if chan == 'preview':
                preview = preview or p
            elif chan == 'normal':
                normals.setdefault(conv, p)
            elif chan not in maps:
                maps[chan] = p
        if info.get('files') and (key == '' or len(groups) == 1):
            for chan, fn in info['files'].items():          # the json knows best which file is which
                p = os.path.join(dirpath, fn)
                if os.path.isfile(p):
                    if chan == 'normal':
                        normals = {'': p}
                    else:
                        maps[chan] = p
        dx = False
        if normals:
            if 'GL' in normals:
                maps['normal'] = normals['GL']
            elif '' in normals:
                maps['normal'] = normals['']
            else:
                maps['normal'], dx = normals['DX'], True
        if not any(c in maps for c in ('color', 'normal', 'height')):
            continue
        if len(maps) < 2 and not g['explicit_color']:
            continue                               # a lone picture (Material Index.jpg, a reference photo ...)
        is_dir_set = key == '' or key.replace('_', '') == re.sub(r'[^a-z0-9]', '', folder.lower())
        if is_dir_set:
            name = folder if key == '' else display_name(next(f for f, c, _ in g['files'] if c != 'preview'))
            cat = os.path.dirname(rel) if rel not in ('', '.') else ''
        else:
            first = next(f for f, c, _ in g['files'] if c != 'preview')
            name = display_name(first)
            cat = rel if rel not in ('', '.') else ''
        cat = cat.replace('\\', '/').strip('/')
        cat = '/'.join(cat.split('/')[-2:]) if cat else os.path.basename(root.rstrip('/\\')) if root else ''
        if 'normal_dx' in info:
            dx = info['normal_dx']
        s = {'id': _sid(dirpath, key), 'name': (info.get('name') if is_dir_set and info.get('name') else name),
             'category': info.get('category') or cat or 'Library', 'dir': dirpath, 'maps': maps, 'normal_dx': dx,
             'preview': preview or maps.get('color') or maps.get('normal') or maps.get('height'), 'info': info}
        out.append(s)
    return out


def scan(roots, max_sets=20000):
    sets, seen = [], set()
    for root in roots:
        root = bpy.path.abspath(root) if root else ''
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith('.'))
            for s in scan_dir(dirpath, files, root):
                if s['id'] not in seen:
                    seen.add(s['id'])
                    sets.append(s)
            if len(sets) >= max_sets:
                break
    sets.sort(key=lambda s: (s['category'].lower(), s['name'].lower()))
    return sets


# ---------------------------------------------------------------------------------------------- cache + previews
_STATE = {'sets': [], 'by_id': {}, 'loaded': False}
_PREVIEWS = {'coll': None}


def cache_path():
    d = bpy.utils.user_resource('CONFIG', path='nature_forge', create=True)
    return os.path.join(d, 'library.json')


def set_sets(sets, save=True):
    _STATE['sets'] = sets
    _STATE['by_id'] = {s['id']: s for s in sets}
    _STATE['loaded'] = True
    if save:
        try:
            with open(cache_path(), 'w', encoding='utf-8') as fh:
                json.dump({'version': 1, 'sets': sets}, fh)
        except OSError as e:
            print("Nature Forge: could not write the library cache:", e)


def load_cache():
    if _STATE['loaded']:
        return
    _STATE['loaded'] = True
    try:
        with open(cache_path(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        sets = data.get('sets', [])
        _STATE['sets'] = sets
        _STATE['by_id'] = {s['id']: s for s in sets}
    except (OSError, ValueError):
        pass


def all_sets():
    load_cache()
    return _STATE['sets']


def get(sid):
    load_cache()
    return _STATE['by_id'].get(sid)


def remember(s):
    """a set picked from disk outside the library (file browser) still resolves by id later"""
    if s['id'] not in _STATE['by_id']:
        _STATE['by_id'][s['id']] = s


def categories():
    cats = []
    for s in all_sets():
        if s['category'] not in cats:
            cats.append(s['category'])
    return cats


def icon(s):
    pc = _PREVIEWS['coll']
    if pc is None:
        import bpy.utils.previews
        pc = _PREVIEWS['coll'] = bpy.utils.previews.new()
    p = s.get('preview')
    if not p:
        return 0
    if s['id'] not in pc:
        try:
            pc.load(s['id'], p, 'IMAGE')
        except (KeyError, RuntimeError):
            return 0
    return pc[s['id']].icon_id


def set_from_files(directory, filenames):
    """the File Browser path: the set(s) one or more picked map files belong to"""
    directory = bpy.path.abspath(directory)
    try:
        files = os.listdir(directory)
    except OSError:
        return []
    found = scan_dir(directory, files, os.path.dirname(directory.rstrip('/\\')))
    if not filenames or len(found) <= 1:
        return found
    keys = {classify(f)[0] for f in filenames if f}
    picked = [s for s in found if s['id'] in {_sid(directory, k) for k in keys}]
    return picked or found


def describe(s):
    order = [('color', "Color"), ('normal', "Normal"), ('rough', "Rough"), ('gloss', "Gloss"), ('metal', "Metal"),
             ('ao', "AO"), ('height', "Height"), ('alpha', "Alpha"), ('orm', "ORM"), ('emit', "Emit")]
    return ', '.join(lab + (" DX" if c == 'normal' and s.get('normal_dx') else "")
                     for c, lab in order if c in s['maps'])


def unregister():
    pc = _PREVIEWS['coll']
    if pc is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(pc)
        _PREVIEWS['coll'] = None
