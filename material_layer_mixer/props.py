# Material Layer Mixer - data model. The layer stack lives on the material (Material.mlm), so it is saved with the
# .blend, travels with append/link and survives undo. Bake settings live on the scene (Scene.mlm_bake).
import math
import bpy
from bpy.props import (StringProperty, BoolProperty, FloatProperty, IntProperty, EnumProperty, PointerProperty,
                       CollectionProperty, FloatVectorProperty)
from bpy.types import PropertyGroup
from . import nodes


def upd_sync(self, context):
    if not nodes.suspended():
        nodes.sync(self.id_data)


def upd_build(self, context):
    if not nodes.suspended():
        nodes.rebuild(self.id_data)


def upd_active(self, context):
    if nodes.suspended():
        return
    if self.preview in ('MASK', 'LAYER'):
        nodes.rebuild(self.id_data)
    from . import paint_guard                    # painting follows the selected layer
    paint_guard.retarget(context)


def upd_active_mask(self, context):
    if not nodes.suspended():
        from . import paint_guard
        paint_guard.retarget(context)


def poll_source_material(self, mat):
    return not mat.mlm.is_stack and not mat.is_grease_pencil


MASK_TYPES = [
    ('PAINT', "Paint", "Hand-painted mask (image painted in Texture Paint mode, sharp)", 'BRUSH_DATA', 0),
    ('VERTEX', "Vertex Paint", "Mask painted into a color attribute (Vertex Paint mode, as sharp as the mesh)",
     'VPAINT_HLT', 1),
    ('NOISE', "Procedural Noise", "Clouds, cells, spots, cracks, stripes or grain - breaks up any mask",
     'FORCE_TURBULENCE', 2),
    ('IMAGE', "Grunge Texture", "Use any black/white image (grunge, stains, scratches) as a mask", 'IMAGE_DATA', 3),
    ('SLOPE', "Slope", "Steepness of the surface: e.g. grass on flat ground, rock on steep slopes", 'IPO_EASE_IN_OUT', 4),
    ('POSITION', "Height Gradient", "Position along an axis: e.g. sand below 2 m, snow above 30 m",
     'EMPTY_SINGLE_ARROW', 5),
    ('DIRECTION', "Direction", "Faces pointing a direction: moss/snow/dust on top, sun-bleached sides",
     'ORIENTATION_GLOBAL', 6),
    ('EDGES', "Edges (wear)", "Convex edges and corners - edge wear, chipped paint", 'MOD_BEVEL', 7),
    ('CAVITY', "Cavity (dirt)", "Cracks, corners and crevices - dirt, grime, moss in gaps", 'MOD_SMOOTH', 8),
    ('HEIGHT', "Height of Layers Below", "Low or high areas of the layers below: sand/water fills gaps between stones",
     'MOD_DISPLACE', 9),
]
MASK_ICONS = {t[0]: t[3] for t in MASK_TYPES}

MASK_OPS = [
    ('MIX', "Replace", "Replace the masks above (blended by opacity)"),
    ('MULTIPLY', "Multiply", "Keep only where both are white (intersect)"),
    ('ADD', "Add", "Combine both (union)"),
    ('SUBTRACT', "Subtract", "Cut this mask out"),
    ('MAX', "Max", "Lighter of both"),
    ('MIN', "Min", "Darker of both"),
    ('OVERLAY', "Breakup Edges", "Overlay: only roughens the soft transitions, keeps full black and white"),
    ('SCREEN', "Screen", "Soft add"),
    ('DIFFERENCE', "Difference", "Difference"),
]

NOISE_TYPES = [
    ('CLOUDS', "Clouds", "Soft fractal noise patches"),
    ('CELLS', "Cells", "Random patches (Voronoi cells) - stones, tiles, patchy color"),
    ('SPOTS', "Spots", "Round spots (Voronoi distance) - use Invert for dots"),
    ('CRACKS', "Cracks", "Crack lines (Voronoi edges) - set Levels high to ~0.05 and Invert"),
    ('STRIPES', "Stripes / Streaks", "Bands, streaks, leaking stains"),
    ('GRAIN', "Grain", "Per-point random speckles"),
]

BLEND_MODES = [
    ('MIX', "Normal", ""), ('MULTIPLY', "Multiply", ""), ('OVERLAY', "Overlay", ""), ('SCREEN', "Screen", ""),
    ('ADD', "Add", ""), ('SUBTRACT', "Subtract", ""), ('DARKEN', "Darken", ""), ('LIGHTEN', "Lighten", ""),
    ('SOFT_LIGHT', "Soft Light", ""), ('LINEAR_LIGHT', "Linear Light", ""), ('BURN', "Color Burn", ""),
    ('DODGE', "Color Dodge", ""), ('DIFFERENCE', "Difference", ""), ('HUE', "Hue", ""),
    ('SATURATION', "Saturation", ""), ('COLOR', "Color", ""), ('VALUE', "Value", ""),
]


class MLM_MaskItem(PropertyGroup):
    name: StringProperty(name="Name", default="Mask")
    uid: StringProperty()
    type: EnumProperty(name="Type", items=MASK_TYPES, default='PAINT', update=upd_build)
    enabled: BoolProperty(name="Enabled", default=True, update=upd_build,
                          description="A disabled mask drops out of the stack")
    op: EnumProperty(name="Combine", items=MASK_OPS, default='MULTIPLY', update=upd_sync,
                     description="How this mask combines with the masks listed above it")
    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    invert: BoolProperty(name="Invert", default=False, update=upd_sync)
    low: FloatProperty(name="Black Point", default=0.0, soft_min=0.0, soft_max=1.0, update=upd_sync,
                       description="Levels: values at or below this become black")
    high: FloatProperty(name="White Point", default=1.0, soft_min=0.0, soft_max=1.0, update=upd_sync,
                        description="Levels: values at or above this become white (move both close together "
                                    "for a sharp edge)")
    image: PointerProperty(name="Image", type=bpy.types.Image, update=upd_build)
    attr: StringProperty(name="Color Attribute", update=upd_sync)
    projection: EnumProperty(name="Projection", items=[
        ('BOX', "Triplanar", "Object space box projection - no UVs needed, no seams"),
        ('UV', "UV", "Use the UV map")], default='BOX', update=upd_build)
    scale: FloatProperty(name="Scale", default=4.0, min=0.0001, soft_max=100.0, update=upd_sync,
                         description="Pattern frequency (repeats per metre in object space)")
    noise: EnumProperty(name="Pattern", items=NOISE_TYPES, default='CLOUDS', update=upd_build)
    detail: FloatProperty(name="Detail", default=4.0, min=0.0, max=15.0, update=upd_sync)
    roughness: FloatProperty(name="Roughness", default=0.55, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    warp: FloatProperty(name="Warp", default=0.2, min=0.0, soft_max=3.0, update=upd_sync,
                        description="Distorts the pattern for a more organic look")
    seed: FloatProperty(name="Seed", default=0.0, update=upd_sync)
    axis: EnumProperty(name="Axis", items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")], default='Z',
                       update=upd_build)
    space: EnumProperty(name="Space", items=[('WORLD', "World", ""), ('OBJECT', "Object", "")], default='WORLD',
                        update=upd_build)
    angle_from: FloatProperty(name="From", default=0.0, min=0.0, max=180.0, update=upd_sync,
                              description="Lowest slope angle in degrees (0 = flat ground)")
    angle_to: FloatProperty(name="To", default=30.0, min=0.0, max=180.0, update=upd_sync,
                            description="Highest slope angle in degrees (90 = vertical wall)")
    angle_smooth: FloatProperty(name="Smooth", default=6.0, min=0.01, max=90.0, update=upd_sync)
    pos_from: FloatProperty(name="From", default=-1000.0, unit='LENGTH', update=upd_sync)
    pos_to: FloatProperty(name="To", default=2.0, unit='LENGTH', update=upd_sync)
    pos_smooth: FloatProperty(name="Smooth", default=0.5, min=0.001, unit='LENGTH', update=upd_sync)
    direction: FloatVectorProperty(name="Direction", size=3, default=(0.0, 0.0, 1.0), subtype='DIRECTION',
                                   update=upd_sync)
    amount: FloatProperty(name="Coverage", default=0.3, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    dir_smooth: FloatProperty(name="Smooth", default=0.15, min=0.001, max=1.0, update=upd_sync)
    distance: FloatProperty(name="Distance", default=0.05, min=0.0, soft_max=2.0, unit='LENGTH', update=upd_sync,
                            description="How far the edge / cavity detection reaches")
    samples: IntProperty(name="Samples", default=16, min=1, max=128, update=upd_sync)
    breakup: FloatProperty(name="Edge Breakup", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_build,
                           description="Roughens the painted edges with grunge noise: natural, random looking "
                                       "transitions instead of smooth brush blobs (works after painting, any time)")
    breakup_scale: FloatProperty(name="Breakup Scale", default=6.0, min=0.01, soft_max=50.0, update=upd_sync,
                                 description="Size of the breakup grunge (higher = finer)")


class MLM_Layer(PropertyGroup):
    name: StringProperty(name="Name", default="Layer", update=upd_sync)
    uid: StringProperty()
    enabled: BoolProperty(name="Visible", default=True, update=upd_sync)
    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    source_type: EnumProperty(name="Source", items=[
        ('MATERIAL', "Material", "Use any existing material (its color, roughness, metal, normal, height, emission)",
         'MATERIAL', 0),
        ('TEXTURE', "Texture Set", "Color / normal / roughness / metal / AO / height image files", 'TEXTURE', 1),
        ('FILL', "Fill", "Flat color and values", 'COLOR', 2)], default='MATERIAL', update=upd_build)
    material: PointerProperty(name="Material", type=bpy.types.Material, poll=poll_source_material, update=upd_build)
    ts_color: PointerProperty(name="Color", type=bpy.types.Image, update=upd_build)
    ts_normal: PointerProperty(name="Normal", type=bpy.types.Image, update=upd_build)
    ts_rough: PointerProperty(name="Roughness", type=bpy.types.Image, update=upd_build)
    ts_metal: PointerProperty(name="Metallic", type=bpy.types.Image, update=upd_build)
    ts_ao: PointerProperty(name="Ambient Occlusion", type=bpy.types.Image, update=upd_build)
    ts_height: PointerProperty(name="Height", type=bpy.types.Image, update=upd_build)
    ts_emit: PointerProperty(name="Emission", type=bpy.types.Image, update=upd_build)
    ts_normal_dx: BoolProperty(name="DirectX Normal", default=False, update=upd_build,
                               description="Normal map uses the DirectX convention (green flipped)")
    ts_gloss: BoolProperty(name="Glossiness", default=False, update=upd_build,
                           description="The roughness slot holds a glossiness map (inverted)")
    ts_ao_strength: FloatProperty(name="AO Strength", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
                                  update=upd_sync)
    fill_color: FloatVectorProperty(name="Color", size=4, subtype='COLOR', default=(0.5, 0.5, 0.5, 1.0), min=0.0,
                                    max=1.0, update=upd_sync)
    fill_metal: FloatProperty(name="Metallic", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    fill_rough: FloatProperty(name="Roughness", default=0.6, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    fill_height: FloatProperty(name="Height", default=0.5, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync,
                               description="0.5 = neutral, lower = dents, higher = raised")
    fill_emission: FloatVectorProperty(name="Emission", size=4, subtype='COLOR', default=(0.0, 0.0, 0.0, 1.0),
                                       min=0.0, max=1.0, update=upd_sync)
    # mapping
    projection: EnumProperty(name="Projection", items=[
        ('UV', "UV", "Use the mesh UV map"),
        ('BOX', "Triplanar", "Object space box projection - no stretching on terrain / cliffs, no UVs needed")],
        default='UV', update=upd_build)
    uv_map: StringProperty(name="UV Map", default="", update=upd_build,
                           description="UV map for this layer's textures (empty = the material's default)")
    uv_scale: FloatVectorProperty(name="Tiling", size=2, default=(1.0, 1.0), update=upd_sync,
                                  description="How often the textures repeat over the UV space")
    tile_size: FloatProperty(name="Tile Size", default=2.0, min=0.001, unit='LENGTH', update=upd_sync,
                             description="Real world size of one texture tile (triplanar)")
    rotation: FloatProperty(name="Rotation", default=0.0, subtype='ANGLE', update=upd_sync)
    offset: FloatVectorProperty(name="Offset", size=2, default=(0.0, 0.0), update=upd_sync)
    box_blend: FloatProperty(name="Blend", default=0.25, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    # adjustments
    hue: FloatProperty(name="Hue", default=0.0, min=-0.5, max=0.5, update=upd_sync)
    saturation: FloatProperty(name="Saturation", default=1.0, min=0.0, max=2.0, update=upd_sync)
    value: FloatProperty(name="Value", default=1.0, min=0.0, max=3.0, update=upd_sync)
    brightness: FloatProperty(name="Brightness", default=0.0, min=-1.0, max=1.0, update=upd_sync)
    contrast: FloatProperty(name="Contrast", default=0.0, min=-1.0, max=1.0, update=upd_sync)
    tint: FloatVectorProperty(name="Tint", size=4, subtype='COLOR', default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0,
                              update=upd_sync)
    tint_amount: FloatProperty(name="Tint Amount", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync)
    rough_offset: FloatProperty(name="Roughness +/-", default=0.0, min=-1.0, max=1.0, update=upd_sync)
    metal_offset: FloatProperty(name="Metallic +/-", default=0.0, min=-1.0, max=1.0, update=upd_sync)
    normal_strength: FloatProperty(name="Normal Strength", default=1.0, min=0.0, soft_max=3.0, update=upd_sync)
    height_scale: FloatProperty(name="Height Scale", default=1.0, soft_min=0.0, soft_max=5.0, update=upd_sync)
    height_offset: FloatProperty(name="Height Offset", default=0.0, min=-1.0, max=1.0, update=upd_sync)
    emission_strength: FloatProperty(name="Emission Strength", default=1.0, min=0.0, soft_max=50.0, update=upd_sync)
    # randomize (anti-tiling)
    rand_amount: FloatProperty(name="Randomize", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_build,
                               description="Breaks up visible tiling: the texture is mixed with randomly rotated / "
                                           "shifted copies of itself along natural borders, so it never repeats the "
                                           "same way (0 = off)")
    rand_patch: FloatProperty(name="Patch Size", default=1.75, min=0.25, soft_max=10.0, update=upd_sync,
                              description="Size of the random patches, in texture repeats")
    rand_rotation: FloatProperty(name="Rotation", default=math.pi, min=0.0, max=math.pi, subtype='ANGLE',
                                 update=upd_sync,
                                 description="Maximum random rotation of the patches (UV projection only; "
                                             "normal maps are rotated correctly with them)")
    rand_offset: FloatProperty(name="Offset", default=1.0, min=0.0, max=1.0, subtype='FACTOR', update=upd_sync,
                               description="Maximum random shift of the patches, in texture repeats")
    rand_scale: FloatProperty(name="Scale Variation", default=0.1, min=0.0, max=0.5, subtype='FACTOR',
                              update=upd_sync, description="Random size difference between the patches")
    rand_seed: IntProperty(name="Seed", default=0, min=0, update=upd_sync, description="Re-roll the random pattern")
    rand_sharpness: FloatProperty(name="Border Sharpness", default=48.0, min=1.0, max=100.0, update=upd_sync,
                                  description="1 = soft cross-fade, 100 = hard cut. Borders follow the texture's "
                                              "height (stones / cracks), so they stay natural either way")
    rand_hue: FloatProperty(name="Hue Variation", default=0.0, min=0.0, max=0.2, update=upd_sync,
                            description="Large-scale random hue shifts")
    rand_value: FloatProperty(name="Brightness Variation", default=0.08, min=0.0, max=0.5, update=upd_sync,
                              description="Large-scale random brightness shifts (breaks up flat terrain)")
    rand_var_size: FloatProperty(name="Variation Size", default=8.0, min=1.0, soft_max=50.0, update=upd_sync,
                                 description="Size of the hue / brightness blotches, in texture repeats")
    # channels / blending
    use_color: BoolProperty(name="Color", default=True, update=upd_sync)
    use_metallic: BoolProperty(name="Metal", default=True, update=upd_sync)
    use_roughness: BoolProperty(name="Rough", default=True, update=upd_sync)
    use_normal: BoolProperty(name="Normal", default=True, update=upd_sync)
    use_height: BoolProperty(name="Height", default=True, update=upd_sync)
    use_emission: BoolProperty(name="Emit", default=True, update=upd_sync)
    blend_mode: EnumProperty(name="Color Blend", items=BLEND_MODES, default='MIX', update=upd_sync)
    normal_mode: EnumProperty(name="Normal", items=[
        ('REPLACE', "Replace", "Layer normals replace the normals below"),
        ('DETAIL', "Add Detail", "Layer normal detail is added on top of the normals below")],
        default='REPLACE', update=upd_sync)
    height_mode: EnumProperty(name="Height", items=[
        ('REPLACE', "Replace", "Layer height replaces the height below"),
        ('ADD', "Add", "Layer height (around 0.5) is added on top")], default='REPLACE', update=upd_sync)
    # enum on the same name as the 1.3.0 bool: saved False (0) -> OFF, True (1) -> COVER
    relief_geo: EnumProperty(name="3D Geometry", items=[
        ('OFF', "Texture Only", "Paints color / roughness / normals only - the 3D shape stays as it is "
                                "(stains, mould, moss painted over the existing shape)", 'TEXTURE', 0),
        ('COVER', "Cover", "Follows the big shape below (a rock stays a rock) and swaps in this material's own "
                           "relief (bark ridges instead of grass leaves)", 'MOD_DISPLACE', 1),
        ('REPLACE', "Replace", "This material's height replaces the shape below (a flat bark patch in the grass - "
                               "rocks under it disappear)", 'MOD_SHRINKWRAP', 2),
        ('ADD', "Add", "This material's relief is added on top of the shape below (bumps on bumps)", 'ADD', 3)],
        default='COVER', description="How this layer shapes the real 3D surface (3D Surface panel)")
    height_influence: FloatProperty(name="Height Blend", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
                                    update=upd_sync,
                                    description="Raised parts of this layer appear first (e.g. stones poke through "
                                                "sand, grass fills the gaps). Needs a height map")
    sharpness: FloatProperty(name="Edge Sharpness", default=1.0, min=1.0, soft_max=30.0, update=upd_sync,
                             description="Hardens the mask transition (1 = as painted)")
    masks: CollectionProperty(type=MLM_MaskItem)
    active_mask: IntProperty(default=0, update=upd_active_mask)


class MLM_Stack(PropertyGroup):
    is_stack: BoolProperty(default=False)
    mid: StringProperty()
    layers: CollectionProperty(type=MLM_Layer)
    active: IntProperty(name="Active Layer", default=0, update=upd_active)
    uv_map: StringProperty(name="Texture UV", default="", update=upd_build,
                           description="Default UV map for layer textures (empty = the mesh's render UV map)")
    paint_uv: StringProperty(name="Paint / Bake UV", default="MLM_UV", update=upd_sync,
                             description="Non-overlapping UV map used by painted masks and baking")
    mask_res: EnumProperty(name="Mask Resolution", items=[('1024', "1024", ""), ('2048', "2048", ""),
                                                          ('4096', "4096", "")], default='2048')
    bump_strength: FloatProperty(name="Height to Normal", default=0.35, min=0.0, max=1.0, subtype='FACTOR',
                                 update=upd_sync,
                                 description="How strongly the combined height channel bends the normals")
    bump_distance: FloatProperty(name="Bump Distance", default=0.02, min=0.0, soft_max=1.0, unit='LENGTH',
                                 update=upd_sync)
    disp_method: EnumProperty(name="Displacement", items=[
        ('BUMP', "Bump Only", "Height only changes the shading normals (fast, bakes into the normal map)"),
        ('DISPLACEMENT', "True Displacement", "Moves the surface (needs a dense / subdivided mesh)"),
        ('BOTH', "Displacement + Bump", "Moves the surface and keeps the fine detail as bump")],
        default='BUMP', update=upd_build)
    disp_scale: FloatProperty(name="Displacement Scale", default=0.1, soft_min=0.0, soft_max=2.0, unit='LENGTH',
                              update=upd_sync)
    preview: EnumProperty(name="Preview", items=[
        ('OFF', "Final", "Show the finished material", 'SHADING_RENDERED', 0),
        ('MASK', "Mask", "Show the active layer's final mask (white = visible)", 'MOD_MASK', 1),
        ('LAYER', "Layer", "Show the active layer's color without mask", 'RENDERLAYERS', 2),
        ('ROUGHNESS', "Roughness", "", 'NODE_MATERIAL', 3),
        ('METALLIC', "Metallic", "", 'NODE_MATERIAL', 4),
        ('HEIGHT', "Height", "", 'MOD_DISPLACE', 5),
        ('NORMAL', "Normal", "", 'NORMALS_FACE', 6)], default='OFF', update=upd_build)


class MLM_BakeSettings(PropertyGroup):
    resolution: EnumProperty(name="Size", items=[('256', "256", ""), ('512', "512", ""),
                                                 ('1024', "1024", "Small props / mobile"),
                                                 ('2048', "2048", "PC / console quality (recommended for hero props)"),
                                                 ('4096', "4096", "Roblox maximum: large or close-up assets")],
                             default='2048')
    samples: IntProperty(name="Samples", default=16, min=1, max=1024,
                         description="Anti-aliasing samples (raise for edge / cavity masks)")
    use_gpu: BoolProperty(name="Use GPU", default=True,
                          description="Bake on the GPU when one is enabled in Preferences > System (much faster)")
    margin: IntProperty(name="Margin", default=16, min=0, max=64, subtype='PIXEL',
                        description="Pixels the colors bleed past UV island edges (avoids seams in mipmaps)")
    use_color: BoolProperty(name="Color", default=True)
    use_normal: BoolProperty(name="Normal", default=True)
    use_roughness: BoolProperty(name="Roughness", default=True)
    use_metallic: BoolProperty(name="Metalness", default=True)
    use_emission: BoolProperty(name="Emission", default=False)
    use_height: BoolProperty(name="Height", default=False)
    ao_into_color: BoolProperty(name="Bake AO into Color", default=True,
                                description="Roblox has no AO map: darken the color in corners and crevices")
    ao_strength: FloatProperty(name="AO Strength", default=0.5, min=0.0, max=1.0, subtype='FACTOR')
    ao_distance: FloatProperty(name="AO Distance", default=0.5, min=0.001, soft_max=10.0, unit='LENGTH')
    output_dir: StringProperty(name="Folder", default="//MLM_Baked/", subtype='DIR_PATH')
    uv_name: StringProperty(name="Bake UV", default="MLM_BakeUV",
                            description="Non-overlapping UV map the textures are baked into (created when missing or "
                                        "when it wastes most of the texture; never the paint UV)")
    split_parts: BoolProperty(name="Split for Roblox", default=True,
                              description="Export meshes over 20,000 triangles as several parts (Roblox's per-mesh "
                                          "limit) that share the same textures; normals stay seamless")
    hide_original: BoolProperty(name="Hide Original", default=True,
                                description="After baking, hide the layered original and show the baked copy")
    export_format: EnumProperty(name="Format", items=[('GLB', "glTF Binary (.glb)", "One file, textures inside"),
                                                      ('FBX', "FBX (.fbx)", "Textures embedded")], default='GLB')


CLASSES = (MLM_MaskItem, MLM_Layer, MLM_Stack, MLM_BakeSettings)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Material.mlm = PointerProperty(type=MLM_Stack)
    bpy.types.Scene.mlm_bake = PointerProperty(type=MLM_BakeSettings)


def unregister():
    del bpy.types.Scene.mlm_bake
    del bpy.types.Material.mlm
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
