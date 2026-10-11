# Material Layer Mixer - smart layers: ready made detail layers (fill + procedural mask stack).
# The fill can be swapped for any material / texture set afterwards, the masks stay.
SMART = {
    'DIRT_CAVITY': dict(
        label="Dirt in Cavities", icon='MOD_SMOOTH', desc="Dark grime collecting in cracks, corners and crevices",
        fill=(0.09, 0.065, 0.045, 1), rough=0.95, channels=('color', 'roughness'),
        masks=[dict(type='CAVITY', distance=0.25, low=0.05, high=0.45),
               dict(type='NOISE', noise='CLOUDS', scale=2.5, op='OVERLAY', low=0.3, high=0.7)]),
    'EDGE_WEAR': dict(
        label="Edge Wear", icon='MOD_BEVEL', desc="Worn, lighter edges and corners (chipped paint, rubbed wood)",
        fill=(0.78, 0.68, 0.52, 1), rough=0.5, channels=('color', 'roughness'),
        masks=[dict(type='EDGES', distance=0.04, low=0.05, high=0.3),
               dict(type='NOISE', noise='CLOUDS', scale=10.0, op='OVERLAY', low=0.35, high=0.65)]),
    'MOSS_TOP': dict(
        label="Moss on Top", icon='OUTLINER_OB_FORCE_FIELD', desc="Moss growing on upward facing surfaces",
        fill=(0.13, 0.26, 0.05, 1), rough=0.9, height=0.62, height_mode='ADD',
        channels=('color', 'roughness', 'height', 'normal'), normal_mode='DETAIL',
        masks=[dict(type='DIRECTION', amount=0.35, dir_smooth=0.2),
               dict(type='NOISE', noise='CLOUDS', scale=1.5, op='MULTIPLY', low=0.35, high=0.65)]),
    'SNOW_TOP': dict(
        label="Snow / Dust on Top", icon='FREEZE', desc="White cover on everything facing up",
        fill=(0.9, 0.93, 0.97, 1), rough=0.6, channels=('color', 'roughness', 'metallic'),
        masks=[dict(type='DIRECTION', amount=0.45, dir_smooth=0.12),
               dict(type='NOISE', noise='CLOUDS', scale=2.0, op='OVERLAY', low=0.3, high=0.7)]),
    'GRASS_FLAT': dict(
        label="Grass on Flat Ground", icon='IPO_EASE_IN_OUT', desc="Terrain: green where the ground is flat",
        fill=(0.2, 0.38, 0.08, 1), rough=0.9, channels=('color', 'roughness', 'normal', 'height', 'metallic'),
        masks=[dict(type='SLOPE', angle_from=0.0, angle_to=24.0, angle_smooth=6.0),
               dict(type='NOISE', noise='CLOUDS', scale=0.4, op='OVERLAY', low=0.3, high=0.7)]),
    'ROCK_STEEP': dict(
        label="Rock on Steep Slopes", icon='MOD_SOLIDIFY', desc="Terrain: rock / cliff where it gets steep",
        fill=(0.42, 0.4, 0.37, 1), rough=0.85, channels=('color', 'roughness', 'normal', 'height', 'metallic'),
        masks=[dict(type='SLOPE', angle_from=38.0, angle_to=180.0, angle_smooth=7.0),
               dict(type='NOISE', noise='CLOUDS', scale=0.5, op='OVERLAY', low=0.3, high=0.7)]),
    'SAND_LOW': dict(
        label="Sand on Low Ground", icon='EMPTY_SINGLE_ARROW', desc="Terrain: sand below a height (beaches)",
        fill=(0.84, 0.74, 0.52, 1), rough=0.9, channels=('color', 'roughness', 'normal', 'height', 'metallic'),
        masks=[dict(type='POSITION', axis='Z', pos_from=-1000.0, pos_to=1.5, pos_smooth=0.6),
               dict(type='NOISE', noise='CLOUDS', scale=0.6, op='OVERLAY', low=0.3, high=0.7)]),
    'WET': dict(
        label="Wet / Darken (paint)", icon='MOD_FLUIDSIM', desc="Paint wet, darker and glossy areas",
        fill=(0.55, 0.55, 0.55, 1), rough=0.12, blend='MULTIPLY', channels=('color', 'roughness'),
        masks=[dict(type='PAINT')]),
    'RUST': dict(
        label="Rust / Stains", icon='SEQ_HISTOGRAM', desc="Patchy rust or stains, more on the edges",
        fill=(0.42, 0.17, 0.06, 1), rough=0.85, metal=0.0, channels=('color', 'roughness', 'metallic'),
        masks=[dict(type='NOISE', noise='CLOUDS', scale=3.0, detail=8.0, low=0.5, high=0.62),
               dict(type='EDGES', distance=0.04, low=0.05, high=0.3, op='ADD', opacity=0.7)]),
    'CRACKS': dict(
        label="Cracks", icon='MOD_DECIM', desc="Dark crack lines with depth (height + normal)",
        fill=(0.07, 0.055, 0.045, 1), rough=0.95, height=0.1, height_mode='ADD',
        channels=('color', 'roughness', 'height'),
        masks=[dict(type='NOISE', noise='CRACKS', scale=2.0, warp=0.45, low=0.0, high=0.035, invert=True),
               dict(type='NOISE', noise='CLOUDS', scale=1.2, op='MULTIPLY', low=0.45, high=0.55)]),
    'STREAKS': dict(
        label="Leaking Streaks", icon='MOD_OCEAN', desc="Vertical water / dirt streaks running down walls",
        fill=(0.16, 0.13, 0.1, 1), rough=0.8, blend='MULTIPLY', channels=('color', 'roughness'),
        masks=[dict(type='NOISE', noise='STRIPES', axis='X', scale=3.0, detail=3.0, warp=0.15, low=0.55, high=0.95,
                    opacity=0.6),
               dict(type='NOISE', noise='CLOUDS', scale=1.0, op='MULTIPLY', low=0.3, high=0.7)]),
    'PATCHES': dict(
        label="Color Variation", icon='SEQ_SPLITVIEW', desc="Large soft color patches - breaks up tiling",
        fill=(0.6, 0.55, 0.45, 1), rough=0.8, blend='OVERLAY', channels=('color',), opacity=0.5,
        masks=[dict(type='NOISE', noise='CLOUDS', scale=0.35, detail=2.0, low=0.3, high=0.75)]),
    'PAINT_COLOR': dict(
        label="Paint Color", icon='BRUSH_DATA', desc="Empty paint layer: paint a color where you want",
        fill=(0.7, 0.12, 0.08, 1), rough=0.5, channels=('color', 'roughness', 'metallic'),
        masks=[dict(type='PAINT')]),
}

SMART_ITEMS = [(k, v['label'], v['desc'], v['icon'], i) for i, (k, v) in enumerate(SMART.items())]
