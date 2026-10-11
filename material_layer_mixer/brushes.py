# Material Layer Mixer - one-click randomized mask brushes (verified against Blender 5.1).
# Blender 5 brushes are assets: the essentials brushes are linked (edits are lost on reload and they can't use
# local textures), so every style is our own local, asset-marked brush. All randomness comes from the brush's
# MASK texture - the colour texture and colour jitter do nothing to black (Hide) strokes.
import math
import random
import numpy as np
import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import Operator, PropertyGroup

STYLE_ITEMS = [
    ('SOFT', "Soft", "Smooth falloff, even stroke (Variation: cloudy strength)", 'BRUSH_DATA', 0),
    ('HARD', "Hard", "Crisp edge (Variation: cloudy strength)", 'SNAP_FACE', 1),
    ('GRUNGE', "Grunge", "Noise breaks up the stroke - worn, dirty, natural edges", 'MOD_NOISE', 2),
    ('SPLATTER', "Splatter", "Scattered blobs, every dab rotated / offset at random", 'STICKY_UVS_DISABLE', 3),
    ('SPRAY', "Spray", "Fine random speckles", 'PARTICLES', 4),
]
LABEL = {i[0]: i[1] for i in STYLE_ITEMS}
ICON = {i[0]: i[3] for i in STYLE_ITEMS}

STYLE = {   # brush settings per style (everything else stays at the brushes.new() defaults)
    'SOFT': dict(curve_distance_falloff_preset='SMOOTH', spacing=10, jitter=0.0),
    'HARD': dict(curve_distance_falloff_preset='CONSTANT', spacing=10, jitter=0.0),
    'GRUNGE': dict(curve_distance_falloff_preset='SMOOTH', spacing=25, jitter=0.15),
    'SPLATTER': dict(curve_distance_falloff_preset='CONSTANT', spacing=55, jitter=0.9),
    'SPRAY': dict(curve_distance_falloff_preset='CONSTANT', spacing=30, jitter=0.8),
}
# TILED (screen space) for Soft/Hard variation on purpose: per-dab RANDOM strength washes out because overlapping
# dabs of one stroke combine by maximum
MASK_MODE = {'SOFT': 'TILED', 'HARD': 'TILED', 'GRUNGE': 'RANDOM', 'SPLATTER': 'VIEW_PLANE', 'SPRAY': 'VIEW_PLANE'}


def _ramp(tex, lo, hi, floor=0.0):
    """mask textures use the ramp ALPHA as intensity -> alpha must go floor..1"""
    tex.use_color_ramp = True
    cr = tex.color_ramp
    cr.elements[0].position = lo
    cr.elements[0].color = (floor, floor, floor, floor)
    cr.elements[1].position = hi
    cr.elements[1].color = (1.0, 1.0, 1.0, 1.0)


def _stamp(name, seed, dots, rmin, rmax, sigma, shrink, n=256):
    """packed RGBA stamp of random soft dots (IMAGE mask textures read alpha = coverage)"""
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, n, n, alpha=True)
    elif tuple(img.size) != (n, n):
        img.scale(n, n)
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) / (n - 1) * 2.0 - 1.0
    a = np.zeros((n, n), np.float32)
    for _ in range(dots):
        r = abs(rng.normal(0.0, sigma))
        if r > 0.9:
            continue
        t = rng.uniform(0.0, math.tau)
        rad = rng.uniform(rmin, rmax) * (1.0 - shrink * r)
        d = np.sqrt((xx - r * math.cos(t)) ** 2 + (yy - r * math.sin(t)) ** 2)
        a = np.maximum(a, np.clip((rad - d) / (rad * 0.4), 0.0, 1.0) * rng.uniform(0.7, 1.0))
    try:
        img.colorspace_settings.name = 'Non-Color'
    except TypeError:
        pass
    img.pixels.foreach_set(np.stack([a, a, a, a], -1).ravel())
    img.update()
    img.pack()                                   # generated pixels are lost on save unless packed
    return img


def _texture(style, seed, variation):
    name = f"MLM Brush {LABEL[style]}"
    if style == 'GRUNGE':
        t = bpy.data.textures.get(name) or bpy.data.textures.new(name, type='CLOUDS')
        t.noise_scale = 0.35
        t.noise_depth = 4
        t.noise_basis = 'BLENDER_ORIGINAL'
        lo = 0.30 + 0.25 * variation             # more variation -> more holes
        _ramp(t, lo, lo + 0.2)
    elif style in ('SPLATTER', 'SPRAY'):
        t = bpy.data.textures.get(name) or bpy.data.textures.new(name, type='IMAGE')
        key = (seed, style)
        if t.get('mlm_seed') != str(key) or t.image is None:
            if style == 'SPLATTER':
                t.image = _stamp("MLM Splatter Stamp", seed, 38, 0.03, 0.16, 0.42, 0.6)
            else:
                t.image = _stamp("MLM Spray Stamp", seed, 140, 0.008, 0.035, 0.5, 0.0)
            t['mlm_seed'] = str(key)
        t.extension = 'CLIP'                     # VIEW_PLANE: the stamp fills the dab, nothing outside it
    else:                                        # SOFT / HARD: big soft blotches = cloudy strength variation
        t = bpy.data.textures.get("MLM Brush Variation") or bpy.data.textures.new("MLM Brush Variation",
                                                                                   type='CLOUDS')
        t.noise_scale = 1.0
        t.noise_depth = 2
        _ramp(t, 0.25, 0.75, floor=max(0.0, 1.0 - variation))
    return t


def style_brush(style, create=True):
    """our local, asset-marked Texture Paint brush for a style"""
    name = f"MLM {LABEL[style]}"
    br = bpy.data.brushes.get(name)
    if br is not None and br.library is not None:
        br = None
    if br is None and create:
        br = bpy.data.brushes.new(name, mode='TEXTURE_PAINT')
    if br is not None and br.asset_data is None:
        br.asset_mark()                          # asset_activate(LOCAL) only finds asset-marked brushes
    return br


def active_style(context):
    """the style of the active Texture Paint brush when it is one of ours, else None"""
    br = context.tool_settings.image_paint.brush
    if br is None or br.library is not None:
        return None
    return next((k for k, lab in LABEL.items() if br.name == f"MLM {lab}"), None)


def apply_style(context, style, variation, seed, activate=True):
    """style button: reset the brush to the style (strength, falloff, spacing ...) + randomness, activate it"""
    ip = context.tool_settings.image_paint
    br = style_brush(style)
    br.blend = 'MIX'
    br.strength = 1.0
    br.stroke_method = 'SPACE'
    br.use_pressure_strength = True
    for k, v in STYLE[style].items():
        try:
            setattr(br, k, v)
        except (AttributeError, TypeError):
            pass
    apply_randomness(context, br, style, variation, seed)
    if activate and context.mode == 'PAINT_TEXTURE' and ip.brush != br:   # asset_activate crashes outside paint
        bpy.ops.brush.asset_activate(asset_library_type='LOCAL', asset_library_identifier="",
                                     relative_asset_identifier="Brush/" + br.name)
    return br


def apply_randomness(context, br, style, variation, seed):
    """only the random part (Variation / Seed): keeps the user's strength, size, spacing and falloff"""
    ip = context.tool_settings.image_paint
    if style == 'SPLATTER':
        br.jitter = 0.6 + 0.8 * variation        # variation scatters splatter wider
    elif style == 'SPRAY':
        br.jitter = 0.5 + 0.8 * variation
    tex = _texture(style, seed, variation)
    br.mask_texture = tex if (style not in ('SOFT', 'HARD') or variation > 0.001) else None
    ms = br.mask_texture_slot
    ms.mask_map_mode = MASK_MODE[style]
    ms.use_random = ms.mask_map_mode != 'TILED'  # random rotation per dab (TILED must stay fixed)
    ms.random_angle = math.tau
    ms.scale = (1.0, 1.0, 1.0)
    # the seed shifts the fixed TILED pattern; VIEW_PLANE stamps (Splatter / Spray) get a new stamp instead - an
    # offset there would sample outside the CLIP stamp and paint nothing
    rnd = random.Random(seed)
    tiled = MASK_MODE[style] == 'TILED'
    ms.offset = (rnd.uniform(-20, 20), rnd.uniform(-20, 20), 0.0) if seed and tiled else (0.0, 0.0, 0.0)
    br.texture = None                            # a colour texture only tints: black strokes ignore it
    ups = getattr(ip, 'unified_paint_settings', None)
    if ups is not None:
        ups.use_color_jitter = False             # jitter washes out the breakup of black (hide) strokes


def upd_settings(self, context):
    """Variation / Seed re-apply to the active Mixer brush (also one picked from the asset shelf)"""
    cur = active_style(context) if context.mode == 'PAINT_TEXTURE' else None
    if cur is not None:
        self.style = cur                         # no update callback on style
        apply_randomness(context, context.tool_settings.image_paint.brush, cur, self.variation, self.seed)


class MLM_BrushSettings(PropertyGroup):
    style: EnumProperty(name="Style", items=STYLE_ITEMS, default='SOFT')
    variation: FloatProperty(name="Variation", default=0.3, min=0.0, max=1.0, subtype='FACTOR', update=upd_settings,
                             description="Randomness: cloudy strength (Soft/Hard), holes (Grunge), scatter "
                                         "(Splatter/Spray)")
    seed: IntProperty(name="Seed", default=0, min=0, update=upd_settings,
                      description="Re-roll the random pattern")


class MLM_OT_brush_style(Operator):
    bl_idname = 'mlm.brush_style'
    bl_label = "Brush Style"
    bl_description = "Switch to a randomized mask brush so painted masks look natural instead of uniform blobs"
    bl_options = {'REGISTER', 'UNDO'}

    style: EnumProperty(name="Style", items=STYLE_ITEMS, default='SOFT')

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def execute(self, context):
        S = context.scene.mlm_brush
        S.style = self.style
        br = apply_style(context, self.style, S.variation, S.seed)
        if context.tool_settings.image_paint.brush != br:
            self.report({'WARNING'}, "Could not activate the brush")
            return {'CANCELLED'}
        return {'FINISHED'}


class MLM_OT_brush_reroll(Operator):
    bl_idname = 'mlm.brush_reroll'
    bl_label = "New Random Pattern"
    bl_description = "Re-roll the brush's random pattern (new splatter / spray stamp, shifted noise)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def execute(self, context):
        context.scene.mlm_brush.seed += 1
        return {'FINISHED'}


CLASSES = (MLM_BrushSettings, MLM_OT_brush_style, MLM_OT_brush_reroll)


def register():
    bpy.types.Scene.mlm_brush = PointerProperty(type=MLM_BrushSettings)


def unregister():
    del bpy.types.Scene.mlm_brush
