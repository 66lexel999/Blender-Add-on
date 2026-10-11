# Nature Forge - starting points. Generator presets only set modifier inputs (everything stays editable); layer
# presets are ready made auto-paint layers (fill colour + mask weights) - pick a texture set for them any time.
import math

GEN = {
    'ROCK': {
        'BOULDER': ("Boulder", "Chunky rock with flat broken faces", {}),
        'PEBBLE': ("Pebble / River Stone", "Smooth, round and polished",
                   {'Size': (0.5, 0.4, 0.25), 'Facets': 0, 'Lumps': 0.25, 'Smooth Edges': 6, 'Cracks': 0.0,
                    'Chips': 0.0, 'Surface Noise': 0.05, 'Flat Bottom': 0.15, 'Smooth Subdivision': True}),
        'SLAB': ("Flat Slab", "Layered flat stone (slate, sandstone)",
                 {'Size': (2.4, 1.6, 0.45), 'Facets': 7, 'Strata': 0.7, 'Strata Scale': 10.0, 'Cracks': 0.35,
                  'Flat Bottom': 0.5}),
        'SHARD': ("Shard / Spire", "Tall, sharp, crystal-like rock",
                  {'Size': (0.9, 0.8, 2.4), 'Facets': 22, 'Facet Depth': 0.55, 'Taper': 0.55, 'Lumps': 0.15,
                   'Smooth Edges': 0, 'Chips': 0.2, 'Cracks': 0.15}),
        'CHISELLED': ("Chiselled Block", "Many sharp cuts, quarry rock",
                      {'Size': (1.4, 1.2, 1.1), 'Facets': 40, 'Facet Depth': 0.35, 'Lumps': 0.1, 'Smooth Edges': 1,
                       'Chips': 0.6, 'Cracks': 0.3}),
        'WEATHERED': ("Weathered Boulder", "Old, rounded, cracked",
                      {'Size': (2.2, 1.8, 1.4), 'Facets': 6, 'Smooth Edges': 8, 'Cracks': 0.55, 'Crack Scale': 1.2,
                       'Lumps': 0.5, 'Chips': 0.15, 'Smooth Subdivision': True}),
    },
    'CLIFF': {
        'WALL': ("Rock Wall", "Default cliff piece", {}),
        'LAYERED': ("Layered Cliff", "Strong horizontal rock layers",
                    {'Ledges': 0.9, 'Ledge Count': 9.0, 'Ledge Sharpness': 0.85, 'Cracks': 0.25,
                     'Vertical Cracks': 0.2}),
        'BASALT': ("Basalt Columns", "Vertical columns and joints",
                   {'Ledges': 0.05, 'Vertical Cracks': 0.97, 'Crack Scale': 0.8, 'Crack Width': 0.09, 'Cracks': 0.8,
                    'Masses': 0.3, 'Height': 9.0, 'Width': 7.0}),
        'OVERHANG': ("Overhang", "Top leans out over the base",
                     {'Overhang': 0.6, 'Taper': -0.2, 'Height': 7.0, 'Thickness': 4.0, 'Masses': 0.8}),
        'BAY': ("Curved Bay", "Concave wall around a path or beach",
                {'Width': 16.0, 'Curve': 0.5, 'Height': 8.0, 'Thickness': 4.0, 'Flat Back': 0.6}),
    },
    'MOUNTAIN': {
        'PEAK': ("Alpine Peak", "One big ridged mountain", {}),
        'RANGE': ("Mountain Range", "Long ridged range",
                  {'Size X': 300.0, 'Size Y': 120.0, 'Range Stretch': 0.8, 'Peak Falloff': 0.6, 'Height': 60.0,
                   'Resolution': 320}),
        'HILLS': ("Rolling Hills", "Soft, low terrain to the borders",
                  {'Peak Falloff': 0.0, 'Height': 12.0, 'Sharpness': 0.8, 'Ridges': 0.1, 'Erosion': 6,
                   'Feature Size': 60.0, 'Valleys': 0.8}),
        'MESA': ("Mesa / Table Mountain", "Flat top, terraced sides",
                 {'Plateau': 0.35, 'Terraces': 7.0, 'Terrace Strength': 0.7, 'Ridges': 0.2, 'Sharpness': 0.8}),
        'VOLCANO': ("Volcanic Cone", "Steep isolated cone",
                    {'Ridges': 0.85, 'Sharpness': 2.2, 'Peak Falloff': 1.0, 'Feature Size': 25.0, 'Warp': 0.15,
                     'Height': 60.0}),
        'TERRAIN': ("Terrain Tile", "Open terrain, no falloff",
                    {'Peak Falloff': 0.0, 'Solid Base': False, 'Height': 25.0, 'Erosion': 3}),
    },
    'TREE': {
        'OAK': ("Broadleaf (Oak)", "Round crown, leaf cards", {}),
        'STYLIZED': ("Stylized Blob Tree", "Low-poly canopy clumps, no textures needed",
                     {'Leaf Type': 2, 'Branch Count': 18, 'Twig Count': 2, 'Clump Size': 0.9}),
        'PINE': ("Pine / Conifer", "Cone shaped, hanging branches",
                 {'Height': 11.0, 'Crown Shape': 0.0, 'Branch Count': 70, 'Start Height': 0.12,
                  'Branch Angle': 1.75, 'Branch Length': 0.3, 'Droop': 0.35, 'Upturn': 0.15, 'Twig Count': 8,
                  'Twig Length': 0.3, 'Leaves per Twig': 14, 'Leaf Size': 0.14, 'Trunk Radius': 0.22,
                  'Top Radius': 0.05}),
        'BIRCH': ("Birch", "Tall, slim, light crown",
                  {'Height': 9.0, 'Trunk Radius': 0.13, 'Branch Count': 30, 'Branch Angle': 0.7, 'Start Height': 0.4,
                   'Droop': 0.5, 'Branch Thickness': 0.35, 'Leaf Size': 0.14, 'Leaves per Twig': 12}),
        'UMBRELLA': ("Umbrella (Acacia)", "Flat, wide, top heavy crown",
                     {'Crown Shape': 1.0, 'Start Height': 0.55, 'Branch Angle': 1.25, 'Branch Length': 0.65,
                      'Upturn': 0.6, 'Droop': 0.0, 'Bend': 0.5, 'Leaf Type': 2, 'Clump Size': 0.75}),
        'DEAD': ("Dead Tree", "Bare twisted branches",
                 {'Leaf Type': 0, 'Bend': 0.8, 'Wiggle': 0.5, 'Branch Count': 14, 'Twig Count': 3}),
    },
    'ROCKIFY': {
        'DEFAULT': ("Rocky Surface", "Subdivide + texture displacement", {}),
    },
}

KINDS = [('ROCK', "Rock", "Boulders, pebbles, slabs, shards", 'MESH_ICOSPHERE'),
         ('CLIFF', "Cliff", "Rock walls with ledges and cracks", 'MOD_BUILD'),
         ('MOUNTAIN', "Mountain", "Peaks, ranges, hills, mesas, terrain tiles", 'RNDCURVE'),
         ('TREE', "Tree", "Oak, pine, birch, stylized, dead trees", 'OUTLINER_OB_FORCE_FIELD')]
KIND_ICON = {k[0]: k[3] for k in KINDS}
KIND_ICON['ROCKIFY'] = 'MOD_DISPLACE'


def gen_items(kind):
    return [(k, v[0], v[1]) for k, v in GEN.get(kind, {}).items()]


# auto-paint layers: fill colour (used until you apply a texture set) + mask weights (see shading.mask_group)
LAYER = {
    'MOSS': dict(label="Moss on Top", icon='OUTLINER_OB_FORCE_FIELD', color=(0.11, 0.2, 0.04, 1), rough=0.9,
                 mask={'Top': 1.0, 'Cavity': 0.35, 'Coverage': 0.45, 'Sharpness': 0.55, 'Noise': 0.45,
                       'Noise Scale': 2.0, 'Crack Fill': 0.4}),
    'SNOW': dict(label="Snow", icon='FREEZE', color=(0.9, 0.92, 0.96, 1), rough=0.55,
                 mask={'Top': 0.8, 'Height': 1.0, 'Combine': 1.0, 'Coverage': 0.5, 'Sharpness': 0.75, 'Noise': 0.25,
                       'Noise Scale': 0.6, 'Crack Fill': 0.6}),
    'DIRT': dict(label="Dirt in Cracks", icon='MOD_SMOOTH', color=(0.1, 0.075, 0.05, 1), rough=0.95,
                 mask={'Cavity': 1.0, 'Coverage': 0.45, 'Sharpness': 0.4, 'Noise': 0.3, 'Noise Scale': 3.0}),
    'EDGES': dict(label="Worn Edges", icon='MOD_BEVEL', color=(0.62, 0.6, 0.55, 1), rough=0.6,
                  mask={'Edges': 1.0, 'Coverage': 0.3, 'Sharpness': 0.6, 'Noise': 0.35, 'Noise Scale': 6.0}),
    'GRASS': dict(label="Grass on Flat Ground", icon='IPO_EASE_IN_OUT', color=(0.13, 0.25, 0.05, 1), rough=0.9,
                  mask={'Top': 1.0, 'Height': -0.6, 'Combine': 0.6, 'Coverage': 0.55, 'Sharpness': 0.7,
                        'Noise': 0.3, 'Noise Scale': 0.08, 'Detail': 0.0}),
    'SAND': dict(label="Sand at the Bottom", icon='EMPTY_SINGLE_ARROW', color=(0.72, 0.62, 0.42, 1), rough=0.9,
                 mask={'Height': -1.0, 'Top': 0.5, 'Combine': 1.0, 'Coverage': 0.45, 'Sharpness': 0.6, 'Noise': 0.3,
                       'Noise Scale': 0.5}),
    'WET': dict(label="Wet / Dark Streaks", icon='MOD_FLUIDSIM', color=(0.05, 0.045, 0.04, 1), rough=0.15,
                mask={'Height': -1.0, 'Cavity': 0.5, 'Coverage': 0.35, 'Sharpness': 0.3, 'Noise': 0.5,
                      'Noise Scale': 1.5}),
    'PAINT': dict(label="Paint Only", icon='BRUSH_DATA', color=(0.35, 0.3, 0.25, 1), rough=0.8,
                  mask={'Coverage': 0.0}),
}
LAYER_ITEMS = [(k, v['label'], "", v['icon'], i) for i, (k, v) in enumerate(LAYER.items())]

# default material layers per generator (index 0 = base)
DEFAULT_LAYERS = {
    'ROCK': [dict(name="Rock", color=(0.36, 0.34, 0.31, 1), rough=0.85)],
    'CLIFF': [dict(name="Cliff Rock", color=(0.42, 0.38, 0.33, 1), rough=0.85)],
    'MOUNTAIN': [dict(name="Mountain Rock", color=(0.33, 0.31, 0.29, 1), rough=0.9),
                 dict(preset='GRASS'), dict(preset='SNOW')],
    'ROCKIFY': [dict(name="Rock", color=(0.4, 0.38, 0.35, 1), rough=0.85)],
    'BARK': [dict(name="Bark", color=(0.2, 0.14, 0.09, 1), rough=0.9)],
    'LEAVES': [dict(name="Leaves", color=(0.12, 0.3, 0.06, 1), rough=0.6)],
}
DEG = math.pi / 180.0
