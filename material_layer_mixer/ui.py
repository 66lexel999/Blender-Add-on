# Material Layer Mixer - UI: 3D View > Sidebar (N) > Mixer
import bpy
from bpy.types import Panel, UIList, Menu
from .ops import (ctx_object, ctx_stack, active_layer, active_mask, tri_count_cached, paint_mask_of, paint_target,
                  painting_mask, first_paintable, uv_coverage_cached)
from .props import MASK_ICONS, MASK_TYPES
from . import presets, brushes

SRC_ICON = {'MATERIAL': 'MATERIAL', 'TEXTURE': 'TEXTURE', 'FILL': 'COLOR'}


def layer_icon(layout, L):
    try:
        if L.source_type == 'MATERIAL' and L.material is not None:
            return {'icon_value': layout.icon(L.material)}
        if L.source_type == 'TEXTURE' and L.ts_color is not None:
            return {'icon_value': layout.icon(L.ts_color)}
    except (TypeError, RuntimeError):
        pass
    return {'icon': SRC_ICON.get(L.source_type, 'COLOR')}


class MLM_UL_layers(UIList):
    bl_idname = 'MLM_UL_layers'

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if index > 0:
            row.prop(item, 'enabled', text='', emboss=False, icon='HIDE_OFF' if item.enabled else 'HIDE_ON')
        else:
            row.label(text='', icon='LAYER_ACTIVE')
        row.prop(item, 'name', text='', emboss=False, **layer_icon(layout, item))
        if index == 0:
            row.label(text='Base')
            return
        if len(item.masks):
            row.label(text='', icon=MASK_ICONS.get(item.masks[0].type, 'MOD_MASK'))
        sub = row.row(align=True)
        sub.scale_x = 0.7
        sub.prop(item, 'opacity', text='', slider=True)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        n = len(items)
        return [self.bitflag_filter_item] * n, [n - 1 - i for i in range(n)]       # top layer first


class MLM_UL_masks(UIList):
    bl_idname = 'MLM_UL_masks'

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='', emboss=False, icon='CHECKBOX_HLT' if item.enabled else 'CHECKBOX_DEHLT')
        row.prop(item, 'name', text='', emboss=False, icon=MASK_ICONS.get(item.type, 'MOD_MASK'))
        if index > 0:
            sub = row.row()
            sub.scale_x = 0.9
            sub.prop(item, 'op', text='')
        if item.invert:
            row.label(text='', icon='ARROW_LEFTRIGHT')


class MLM_MT_add_layer(Menu):
    bl_idname = 'MLM_MT_add_layer'
    bl_label = "Add Layer"

    def draw(self, context):
        l = self.layout
        l.operator_context = 'INVOKE_DEFAULT'
        l.operator('mlm.layer_add_material', text="Material...", icon='MATERIAL')
        l.operator('mlm.layer_add_texture_set', text="Texture Set (image files)...", icon='TEXTURE')
        op = l.operator('mlm.layer_add', text="Fill (color)", icon='COLOR')
        op.source = 'FILL'
        l.separator()
        l.menu('MLM_MT_smart', icon='SHADERFX')
        l.separator()
        l.label(text="Asset Browser: right-click a material", icon='ASSET_MANAGER')
        l.label(text="     > Add as Mixer Layer")


class MLM_MT_smart(Menu):
    bl_idname = 'MLM_MT_smart'
    bl_label = "Smart Layers"

    def draw(self, context):
        for k, v in presets.SMART.items():
            self.layout.operator('mlm.layer_add_smart', text=v['label'], icon=v['icon']).preset = k


class MLM_MT_add_mask(Menu):
    bl_idname = 'MLM_MT_add_mask'
    bl_label = "Add Mask"

    def draw(self, context):
        for t in MASK_TYPES:
            self.layout.operator('mlm.mask_add', text=t[1], icon=t[3]).type = t[0]


class MLM_MT_uv(Menu):
    bl_idname = 'MLM_MT_uv'
    bl_label = "Create Paint / Bake UV"

    def draw(self, context):
        for m, label in (('AUTO', "Automatic"), ('SMART', "Smart UV Project (props)"),
                         ('REPACK', "Re-pack Your UV Map"), ('TOP', "Top-Down (terrain)"),
                         ('LIGHTMAP', "Lightmap Pack"), ('COPY', "Copy Render UV")):
            self.layout.operator('mlm.make_uv', text=label).method = m


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Mixer'


class MLM_PT_main(_Base, Panel):
    bl_idname = 'MLM_PT_main'
    bl_label = "Material Mixer"

    def draw(self, context):
        l = self.layout
        ob = ctx_object(context)
        if ob is None:
            l.label(text="Select a mesh object", icon='INFO')
            return
        if ob.get('mlm_source'):
            box = l.box()
            box.label(text=f"Baked copy of '{ob['mlm_source']}'", icon='CHECKMARK')
            box.operator('mlm.toggle_baked', icon='ARROW_LEFTRIGHT', text="Show Layered Original")
            from .relief import source_of, draw_lod_switch
            src = source_of(ob)
            if src is not None:
                draw_lod_switch(box, src)
            return
        if len(ob.material_slots) > 1:
            l.template_list('MATERIAL_UL_matslots', '', ob, 'material_slots', ob, 'active_material_index', rows=2)
        ob_, mat, st = ctx_stack(context)
        if st is None:
            col = l.column(align=True)
            col.scale_y = 1.4
            if mat is not None:
                col.operator('mlm.stack_create', text="Make Layered Material", icon='NODE_MATERIAL').mode = 'CONVERT'
            col.operator('mlm.stack_create', text="New Layered Material", icon='ADD').mode = 'NEW'
            box = l.box()
            box.label(text="Stack whole materials in layers:", icon='INFO')
            for t in ("color, roughness, metal, normal, height", "mixed by painted + procedural masks,",
                      "then bake to textures for Roblox."):
                box.label(text=t)
            return
        row = l.row()
        row.template_list('MLM_UL_layers', '', st, 'layers', st, 'active', rows=6)
        col = row.column(align=True)
        col.menu('MLM_MT_add_layer', text='', icon='ADD')
        col.operator('mlm.layer_remove', text='', icon='REMOVE')
        col.separator()
        col.operator('mlm.layer_duplicate', text='', icon='DUPLICATE')
        col.separator()
        col.operator('mlm.layer_move', text='', icon='TRIA_UP').direction = 'UP'
        col.operator('mlm.layer_move', text='', icon='TRIA_DOWN').direction = 'DOWN'
        col.separator()
        col.operator('mlm.refresh', text='', icon='FILE_REFRESH')
        row = l.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator('mlm.layer_add_material', text="Material", icon='ADD')
        row.operator('mlm.layer_add_texture_set', text="Textures", icon='ADD')
        row.menu('MLM_MT_smart', text="Smart", icon='SHADERFX')
        L = active_layer(st)
        if context.mode in ('PAINT_TEXTURE', 'PAINT_VERTEX'):
            draw_paint_tools(context, l, st)
        elif st.active > 0 and L is not None:
            row = l.row()
            row.scale_y = 1.3
            if paint_mask_of(L) is not None:
                row.operator('mlm.paint', text=f"Paint '{L.name}'", icon='BRUSH_DATA')
            else:
                row.operator('mlm.mask_add', text="Add Paint Mask", icon='BRUSH_DATA').type = 'PAINT'
        l.prop(st, 'preview', text="View")
        me = ob.data
        if context.mode == 'OBJECT' and me.uv_layers.get(st.paint_uv) is not None:
            cov = uv_coverage_cached(me, st.paint_uv)
            if cov < 0.3:
                box = l.box()
                box.label(text=f"Paint UV uses only {cov:.0%} of the mask texture:", icon='ERROR')
                box.label(text="painting and bakes come out blurry / chopped")
                box.operator('mlm.paint_uv_rebuild', icon='UV_SYNC_SELECT')


def draw_paint_warning(context, l, st):
    """the brush is NOT on a layer mask: say where the strokes go and offer the fix"""
    img, attr = paint_target(context)
    w = l.box()
    col = w.column(align=True)
    col.alert = True
    if img is not None:
        col.label(text=f"Brush paints on '{img.name}' itself!", icon='ERROR')
    else:
        col.label(text="Brush is not on a layer mask", icon='ERROR')
    if st.active == 0:
        col.label(text="The Base layer can't be painted -")
        col.label(text="select the layer you want to paint in.")
    k = st.active if st.active > 0 and paint_mask_of(active_layer(st)) is not None else first_paintable(st)
    row = w.row()
    row.scale_y = 1.4
    if k > 0:
        row.operator('mlm.paint', text=f"Paint '{st.layers[k].name}'", icon='BRUSH_DATA').layer = k
    elif st.active > 0:
        row.operator('mlm.mask_add', text="Add Paint Mask", icon='BRUSH_DATA').type = 'PAINT'
    else:
        w.label(text="Add a layer first (+ Material / Textures / Smart)", icon='INFO')
    if img is not None and img.is_dirty and not img.get('mlm_mask') and img.source == 'FILE':
        w.operator('mlm.image_revert', text=f"Undo strokes on '{img.name}'", icon='LOOP_BACK').image = img.name


def draw_brush_styles(context, box):
    B = context.scene.mlm_brush
    cur = brushes.active_style(context)
    col = box.column(align=True)
    col.label(text="Brush Style" if cur else "Brush Style (natural strokes):")
    grid = col.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    for key, lab in brushes.LABEL.items():
        grid.operator('mlm.brush_style', text=lab, icon=brushes.ICON[key], depress=(cur == key)).style = key
    if cur is not None:
        row = col.row(align=True)
        row.prop(B, 'variation', slider=True)
        row.operator('mlm.brush_reroll', text="", icon='FILE_REFRESH')


def draw_paint_tools(context, l, st):
    hit = painting_mask(context, st)
    if hit is None:
        draw_paint_warning(context, l, st)
    box = l.box()
    box.label(text=f"Painting: {hit[0].name}  >  {hit[1].name}" if hit else "Painting: (not a layer mask)",
              icon='BRUSH_DATA')
    row = box.row(align=True)
    row.scale_y = 1.3
    row.operator('mlm.paint_color', text="Reveal", icon='SNAP_FACE').color = 'WHITE'
    row.operator('mlm.paint_color', text="Hide", icon='SELECT_SUBTRACT').color = 'BLACK'
    row.operator('mlm.paint_color', text="", icon='ARROW_LEFTRIGHT').color = 'SWAP'
    ts = context.tool_settings
    p = ts.vertex_paint if context.mode == 'PAINT_VERTEX' else ts.image_paint
    ups = getattr(p, 'unified_paint_settings', None)
    if context.mode == 'PAINT_TEXTURE':
        draw_brush_styles(context, box)
    col = box.column(align=True)
    if ups is not None:
        col.prop(ups, 'color', text="")
        col.prop(ups, 'size', text="Size")
        if ups.use_unified_strength or p.brush is None:
            col.prop(ups, 'strength', text="Strength", slider=True)
        else:
            col.prop(p.brush, 'strength', text="Strength", slider=True)
    row = box.row(align=True)
    row.operator('mlm.mask_edit', text="Fill").action = 'FILL'
    row.operator('mlm.mask_edit', text="Clear").action = 'CLEAR'
    row.operator('mlm.mask_edit', text="Invert").action = 'INVERT'
    row.operator('mlm.mask_edit', text="Soften").action = 'BLUR'
    if hit is not None:
        draw_breakup(box, hit[1])
    box.operator('mlm.paint_exit', icon='CHECKMARK')


def _stack_poll(context):
    return ctx_stack(context)[2] is not None and len(ctx_stack(context)[2].layers) > 0


class MLM_PT_layer(_Base, Panel):
    bl_idname = 'MLM_PT_layer'
    bl_label = "Layer"
    bl_parent_id = 'MLM_PT_main'

    @classmethod
    def poll(cls, context):
        return _stack_poll(context)

    def draw_header_preset(self, context):
        L = active_layer(ctx_stack(context)[2])
        if L is not None:
            self.layout.label(text=L.name, **layer_icon(self.layout, L))

    def draw(self, context):
        l = self.layout
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        is_base = st.active == 0
        l.row().prop(L, 'source_type', expand=True)
        if L.source_type == 'MATERIAL':
            l.template_ID_preview(L, 'material', rows=3, cols=6)
            row = l.row(align=True)
            row.operator_context = 'INVOKE_DEFAULT'
            op = row.operator('mlm.layer_add_material', text="Pick Material...", icon='VIEWZOOM')
            op.replace = True
            row.operator('mlm.refresh', text="Re-read", icon='FILE_REFRESH')
            if L.material is None:
                l.label(text="Pick a material (any Principled material works)", icon='ERROR')
            else:
                draw_randomize(l, L)
        elif L.source_type == 'TEXTURE':
            row = l.row()
            row.operator_context = 'INVOKE_DEFAULT'
            op = row.operator('mlm.layer_add_texture_set', text="Load Texture Set...", icon='FILE_FOLDER')
            op.replace = True
            col = l.column(align=True)
            for k in ('ts_color', 'ts_normal', 'ts_rough', 'ts_metal', 'ts_ao', 'ts_height', 'ts_emit'):
                col.prop(L, k)
            row = l.row(align=True)
            row.prop(L, 'ts_normal_dx')
            row.prop(L, 'ts_gloss')
            if L.ts_ao is not None:
                l.prop(L, 'ts_ao_strength')
            draw_randomize(l, L)
        else:
            col = l.column(align=True)
            col.prop(L, 'fill_color')
            col.prop(L, 'fill_rough')
            col.prop(L, 'fill_metal')
            col.prop(L, 'fill_height')
            col.prop(L, 'fill_emission')
        row = l.row(align=True)
        if not is_base:
            row.prop(L, 'opacity', slider=True)
        row.prop(L, 'relief_geo', text="")


def draw_randomize(l, L):
    """the anti-tiling switch: random rotated / shifted patches of the same material, no visible repeats"""
    box = l.box()
    box.prop(L, 'rand_amount', slider=True, text="Randomize (no tiling)")
    if L.rand_amount > 0:
        col = box.column(align=True)
        col.prop(L, 'rand_patch')
        if L.projection != 'BOX':
            col.prop(L, 'rand_rotation')
        row = col.row(align=True)
        row.prop(L, 'rand_seed')
        row.prop(L, 'rand_value', text="Brightness", slider=True)


class MLM_PT_layer_random(_Base, Panel):
    bl_idname = 'MLM_PT_layer_random'
    bl_label = "Randomize: More"
    bl_parent_id = 'MLM_PT_layer'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        L = active_layer(ctx_stack(context)[2])
        return L is not None and L.source_type != 'FILL' and L.rand_amount > 0

    def draw(self, context):
        L = active_layer(ctx_stack(context)[2])
        col = self.layout.column(align=True)
        col.use_property_split = True
        for k in ('rand_offset', 'rand_scale', 'rand_sharpness', 'rand_hue', 'rand_value', 'rand_var_size'):
            col.prop(L, k)


class MLM_PT_layer_blend(_Base, Panel):
    bl_idname = 'MLM_PT_layer_blend'
    bl_label = "Channels & Blending"
    bl_parent_id = 'MLM_PT_layer'

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and st.active > 0

    def draw(self, context):
        l = self.layout
        L = active_layer(ctx_stack(context)[2])
        row = l.row(align=True)
        for k in ('use_color', 'use_roughness', 'use_metallic'):
            row.prop(L, k, toggle=True)
        row = l.row(align=True)
        for k in ('use_normal', 'use_height', 'use_emission'):
            row.prop(L, k, toggle=True)
        col = l.column()
        col.use_property_split = True
        col.prop(L, 'blend_mode')
        col.prop(L, 'normal_mode')
        col.prop(L, 'height_mode')
        col.prop(L, 'relief_geo')


class MLM_PT_layer_mapping(_Base, Panel):
    bl_idname = 'MLM_PT_layer_mapping'
    bl_label = "Mapping"
    bl_parent_id = 'MLM_PT_layer'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        L = active_layer(st)
        return L is not None and L.source_type != 'FILL'

    def draw(self, context):
        l = self.layout
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        l.row().prop(L, 'projection', expand=True)
        col = l.column(align=True)
        col.use_property_split = True
        if L.projection == 'UV':
            col.prop_search(L, 'uv_map', ob.data, 'uv_layers', text="UV Map")
            col.prop(L, 'uv_scale')
        else:
            col.prop(L, 'tile_size')
            col.prop(L, 'box_blend')
        col.prop(L, 'rotation')
        col.prop(L, 'offset')


class MLM_PT_layer_adjust(_Base, Panel):
    bl_idname = 'MLM_PT_layer_adjust'
    bl_label = "Adjust"
    bl_parent_id = 'MLM_PT_layer'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _stack_poll(context)

    def draw(self, context):
        l = self.layout
        L = active_layer(ctx_stack(context)[2])
        col = l.column(align=True)
        col.use_property_split = True
        for k in ('hue', 'saturation', 'value', 'brightness', 'contrast'):
            col.prop(L, k, slider=True)
        col = l.column(align=True)
        col.use_property_split = True
        col.prop(L, 'tint')
        col.prop(L, 'tint_amount')
        col = l.column(align=True)
        col.use_property_split = True
        for k in ('rough_offset', 'metal_offset', 'normal_strength', 'height_scale', 'height_offset',
                  'emission_strength'):
            col.prop(L, k)


class MLM_PT_mask(_Base, Panel):
    bl_idname = 'MLM_PT_mask'
    bl_label = "Mask"
    bl_parent_id = 'MLM_PT_main'

    @classmethod
    def poll(cls, context):
        st = ctx_stack(context)[2]
        return st is not None and st.active > 0 and len(st.layers) > 1

    def draw_header(self, context):
        self.layout.label(icon='MOD_MASK')

    def draw(self, context):
        l = self.layout
        ob, mat, st = ctx_stack(context)
        L = active_layer(st)
        row = l.row()
        row.template_list('MLM_UL_masks', '', L, 'masks', L, 'active_mask', rows=3)
        col = row.column(align=True)
        col.menu('MLM_MT_add_mask', text='', icon='ADD')
        col.operator('mlm.mask_remove', text='', icon='REMOVE')
        col.separator()
        col.operator('mlm.mask_move', text='', icon='TRIA_UP').direction = 'UP'
        col.operator('mlm.mask_move', text='', icon='TRIA_DOWN').direction = 'DOWN'
        if not len(L.masks):
            l.label(text="No mask: the layer covers everything", icon='INFO')
        M = active_mask(L)
        if M is not None:
            draw_mask_item(context, l, ob, st, L, M)
        box = l.box()
        box.label(text="Transition", icon='IPO_EASE_IN_OUT')
        col = box.column(align=True)
        col.prop(L, 'height_influence', slider=True)
        col.prop(L, 'sharpness')
        if len(L.masks) and not (len(L.masks) == 1 and L.masks[0].type == 'PAINT'):
            l.operator('mlm.mask_freeze', icon='FREEZE')


def draw_breakup(box, M):
    row = box.row(align=True)
    row.prop(M, 'breakup', slider=True)
    if M.breakup > 0:
        row.prop(M, 'breakup_scale', text="Scale")


def draw_mask_item(context, l, ob, st, L, M):
    box = l.box()
    t = M.type
    if t == 'PAINT':
        row = box.row(align=True)
        row.scale_y = 1.4
        painting = context.mode == 'PAINT_TEXTURE' and context.tool_settings.image_paint.canvas == M.image \
            and M.image is not None
        if not painting:
            row.operator('mlm.paint', text="Paint Mask" if M.image else "Start Painting", icon='BRUSH_DATA')
        else:
            row.label(text="Painting this mask", icon='CHECKMARK')
        if M.image is None:
            box.label(text="Hidden until painted: white = show this layer", icon='INFO')
        else:
            box.template_ID(M, 'image')
        if ob.data.uv_layers.get(st.paint_uv) is None:
            box.label(text=f"A paint UV '{st.paint_uv}' is made automatically", icon='UV')
        row = box.row(align=True)
        row.operator('mlm.mask_edit', text="Fill").action = 'FILL'
        row.operator('mlm.mask_edit', text="Clear").action = 'CLEAR'
        row.operator('mlm.mask_edit', text="Invert").action = 'INVERT'
        row.operator('mlm.mask_edit', text="Soften").action = 'BLUR'
        draw_breakup(box, M)
    elif t == 'VERTEX':
        row = box.row(align=True)
        row.scale_y = 1.4
        if context.mode != 'PAINT_VERTEX':
            row.operator('mlm.paint', text="Vertex Paint Mask", icon='VPAINT_HLT')
        box.prop_search(M, 'attr', ob.data, 'color_attributes', text="Attribute")
        row = box.row(align=True)
        row.operator('mlm.mask_edit', text="Fill").action = 'FILL'
        row.operator('mlm.mask_edit', text="Clear").action = 'CLEAR'
        row.operator('mlm.mask_edit', text="Invert").action = 'INVERT'
        row.operator('mlm.mask_edit', text="Soften").action = 'BLUR'
        draw_breakup(box, M)
    elif t == 'NOISE':
        box.prop(M, 'noise')
        col = box.column(align=True)
        col.prop(M, 'scale')
        if M.noise in ('CLOUDS', 'CELLS', 'SPOTS', 'CRACKS', 'STRIPES'):
            col.prop(M, 'detail')
        if M.noise in ('CLOUDS', 'CELLS', 'SPOTS', 'CRACKS'):
            col.prop(M, 'roughness')
        col.prop(M, 'warp')
        col.prop(M, 'seed')
        if M.noise == 'STRIPES':
            box.row().prop(M, 'axis', expand=True)
        box.row().prop(M, 'projection', expand=True)
    elif t == 'IMAGE':
        box.template_ID(M, 'image', open='image.open')
        box.prop(M, 'scale', text="Tiling")
        box.row().prop(M, 'projection', expand=True)
    elif t == 'SLOPE':
        col = box.column(align=True)
        col.prop(M, 'angle_from', text="From (deg)")
        col.prop(M, 'angle_to', text="To (deg)")
        col.prop(M, 'angle_smooth')
        box.label(text="0 = flat ground, 90 = vertical wall", icon='INFO')
    elif t == 'POSITION':
        box.row().prop(M, 'axis', expand=True)
        box.row().prop(M, 'space', expand=True)
        col = box.column(align=True)
        col.prop(M, 'pos_from')
        col.prop(M, 'pos_to')
        col.prop(M, 'pos_smooth')
    elif t == 'DIRECTION':
        box.prop(M, 'direction', text="")
        col = box.column(align=True)
        col.prop(M, 'amount', slider=True)
        col.prop(M, 'dir_smooth')
    elif t in ('EDGES', 'CAVITY'):
        col = box.column(align=True)
        col.prop(M, 'distance')
        col.prop(M, 'samples')
        box.label(text="Shows in Cycles and in the bake.", icon='INFO')
        box.label(text="For EEVEE / painting over: Freeze Masks")
    elif t == 'HEIGHT':
        box.label(text="Uses the height of the layers below", icon='INFO')
    col = box.column(align=True)
    row = col.row(align=True)
    row.prop(M, 'low', text="Black")
    row.prop(M, 'high', text="White")
    row = col.row(align=True)
    row.prop(M, 'invert', toggle=True)
    row.prop(M, 'opacity', slider=True)


class MLM_PT_surface(_Base, Panel):
    bl_idname = 'MLM_PT_surface'
    bl_label = "Surface & UVs"
    bl_parent_id = 'MLM_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return ctx_stack(context)[2] is not None

    def draw(self, context):
        l = self.layout
        ob, mat, st = ctx_stack(context)
        col = l.column(align=True)
        col.use_property_split = True
        col.prop(st, 'bump_strength', slider=True)
        col.prop(st, 'bump_distance')
        col.separator()
        col.prop(st, 'disp_method')
        if st.disp_method != 'BUMP':
            col.prop(st, 'disp_scale')
            l.label(text="True displacement needs a dense or subdivided mesh", icon='INFO')
        col = l.column(align=True)
        col.use_property_split = True
        col.prop_search(st, 'uv_map', ob.data, 'uv_layers')
        col.prop(st, 'paint_uv')
        col.prop(st, 'mask_res')
        if ob.data.uv_layers.get(st.paint_uv) is not None:
            l.label(text=f"Paint UV uses {uv_coverage_cached(ob.data, st.paint_uv):.0%} of the mask texture",
                    icon='UV')
        l.operator('mlm.paint_uv_rebuild', icon='UV_SYNC_SELECT')
        l.menu('MLM_MT_uv', icon='UV')


class MLM_PT_bake(_Base, Panel):
    bl_idname = 'MLM_PT_bake'
    bl_label = "Bake & Export (Roblox)"
    bl_parent_id = 'MLM_PT_main'
    bl_order = 10
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return ctx_object(context) is not None

    def draw(self, context):
        l = self.layout
        S = context.scene.mlm_bake
        col = l.column(align=True)
        col.use_property_split = True
        col.prop(S, 'resolution')
        col.prop(S, 'samples')
        col.prop(S, 'margin')
        col.prop(S, 'use_gpu')
        col.prop(S, 'uv_name')
        grid = l.grid_flow(columns=3, align=True)
        for k in ('use_color', 'use_normal', 'use_roughness', 'use_metallic', 'use_emission', 'use_height'):
            grid.prop(S, k, toggle=True)
        col = l.column(align=True)
        col.prop(S, 'ao_into_color')
        if S.ao_into_color:
            row = col.row(align=True)
            row.prop(S, 'ao_strength', slider=True)
            row.prop(S, 'ao_distance')
        l.prop(S, 'output_dir')
        l.prop(S, 'hide_original')
        row = l.row()
        row.scale_y = 1.5
        row.operator('mlm.bake', icon='RENDER_STILL')
        row = l.row(align=True)
        row.operator('mlm.toggle_baked', icon='ARROW_LEFTRIGHT')
        row = l.row(align=True)
        row.prop(S, 'export_format', text="")
        row.operator('mlm.export_roblox', icon='EXPORT', text="Export")
        l.prop(S, 'split_parts')
        ob = ctx_object(context)
        if ob is not None:
            n = tri_count_cached(ob)
            l.label(text=f"{ob.name}: {n:,} triangles" + ("  - export splits it into parts" if n > 20000 else ""),
                    icon='INFO' if n > 20000 else 'MESH_DATA')
        l.label(text="Roblox: textures up to 4096, 20k triangles per mesh", icon='INFO')
        if ob is not None:
            me = ob.data
            uvn = S.uv_name or 'MLM_BakeUV'
            if me.uv_layers.get(uvn) is not None:
                cov = uv_coverage_cached(me, uvn)
                if cov < 0.3:
                    l.label(text=f"Bake UV uses only {cov:.0%} of the texture: it is redone", icon='ERROR')


class MLM_PT_paint_header(_Base, Panel):
    """painting tools also when the main panel is scrolled away (Tool tab)"""
    bl_idname = 'MLM_PT_paint_tool'
    bl_label = "Mixer Mask Painting"
    bl_category = 'Tool'

    @classmethod
    def poll(cls, context):
        return context.mode in ('PAINT_TEXTURE', 'PAINT_VERTEX') and ctx_stack(context)[2] is not None

    def draw(self, context):
        draw_paint_tools(context, self.layout, ctx_stack(context)[2])


def asset_menu(self, context):
    assets = getattr(context, 'selected_assets', None)
    if assets and any(a.id_type == 'MATERIAL' for a in assets):
        self.layout.separator()
        self.layout.operator('mlm.add_asset_layers', icon='SHADERFX')


CLASSES = (MLM_UL_layers, MLM_UL_masks, MLM_MT_add_layer, MLM_MT_smart, MLM_MT_add_mask, MLM_MT_uv, MLM_PT_main,
           MLM_PT_layer, MLM_PT_layer_random, MLM_PT_layer_blend, MLM_PT_layer_mapping, MLM_PT_layer_adjust, MLM_PT_mask, MLM_PT_surface,
           MLM_PT_bake, MLM_PT_paint_header)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for name in ('ASSETBROWSER_MT_context_menu',):
        mt = getattr(bpy.types, name, None)
        if mt is not None:
            mt.append(asset_menu)


def unregister():
    for name in ('ASSETBROWSER_MT_context_menu',):
        mt = getattr(bpy.types, name, None)
        if mt is not None:
            mt.remove(asset_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
