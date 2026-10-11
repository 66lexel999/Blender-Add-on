# Nature Forge - sidebar UI (3D View > N panel > Forge)
from bpy.types import Panel, Menu
from . import library, shading, presets
from .ops import nf_mod, kind_of, inputs, nf_materials, ctx_ob, mlm_available
from .props import prefs

CAT = "Forge"
MAT_PANELS = {'Texture Displacement', 'Bark Texture', 'Material'}
LEAF_ITEMS = ('None', 'Cards', 'Clumps')


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CAT


def draw_input(layout, md, it, text=None):
    name = text or it.name
    if it.socket_type == 'NodeSocketMenu':
        row = layout.row(align=True)
        row.label(text=name)
        try:
            cur = int(md[it.identifier])
        except (KeyError, TypeError, ValueError):
            cur = 0
        for i, nm in enumerate(LEAF_ITEMS):
            op = row.operator('nf.set_input', text=nm, depress=cur == i)
            op.name, op.value = it.name, i
        return
    try:
        layout.prop(md, f'["{it.identifier}"]', text=name)
    except TypeError:
        pass


def iter_panels(ng):
    """-> [(panel name or '', [socket items])] in interface order"""
    out, top = [], []
    for it in ng.interface.items_tree:
        if getattr(it, 'item_type', '') == 'PANEL':
            out.append((it.name, [c for c in it.interface_items if getattr(c, 'item_type', '') == 'SOCKET'
                                  and c.in_out == 'INPUT']))
        elif getattr(it, 'item_type', '') == 'SOCKET' and it.in_out == 'INPUT' and \
                (it.parent is None or getattr(it.parent, 'name', '') == ''):
            if it.socket_type != 'NodeSocketGeometry':
                top.append(it)
    return [('', top)] + out


# ---------------------------------------------------------------------------------------------- create
def _add_menu(kind):
    class M(Menu):
        bl_idname = f'NF_MT_add_{kind.lower()}'
        bl_label = f"{kind.title()} Presets"

        def draw(self, context):
            for key, (label, desc, _) in presets.GEN[kind].items():
                op = self.layout.operator('nf.add', text=label)
                op.kind, op.preset = kind, key
    M.__name__ = f'NF_MT_add_{kind.lower()}'
    return M


ADD_MENUS = [_add_menu(k[0]) for k in presets.KINDS]


class NF_PT_main(_Base, Panel):
    bl_label = "Nature Forge"
    bl_idname = 'NF_PT_main'

    def draw(self, context):
        l = self.layout
        col = l.column(align=True)
        col.label(text="Create (at the 3D cursor):")
        for kind, label, desc, icon in presets.KINDS:
            row = col.row(align=True)
            row.scale_y = 1.25
            op = row.operator('nf.add', text=label, icon=icon)
            op.kind, op.preset = kind, next(iter(presets.GEN[kind]))
            row.menu(f'NF_MT_add_{kind.lower()}', text="", icon='PRESET')
        l.operator('nf.rockify', icon='MOD_DISPLACE')
        ob = ctx_ob(context)
        if ob is not None and nf_mod(ob) is None and not ob.get('nf_final') and not nf_materials(ob):
            l.label(text="Rockify: your own model + texture height", icon='INFO')


# ---------------------------------------------------------------------------------------------- shape
class NF_PT_shape(_Base, Panel):
    bl_label = "Shape"
    bl_idname = 'NF_PT_shape'
    bl_parent_id = 'NF_PT_main'
    bl_options = set()

    @classmethod
    def poll(cls, context):
        return nf_mod(ctx_ob(context)) is not None

    def draw_header(self, context):
        k = kind_of(ctx_ob(context))
        self.layout.label(icon=presets.KIND_ICON.get(k, 'MODIFIER'))

    def draw(self, context):
        l = self.layout
        ob = ctx_ob(context)
        md = nf_mod(ob)
        ng = md.node_group
        kind = ng['nf_kind']
        its = inputs(md)
        row = l.row(align=True)
        if 'Seed' in its:
            row.prop(md, f'["{its["Seed"].identifier}"]', text="Seed")
            op = row.operator('nf.seed', text="", icon='TRIA_LEFT')
            op.step = -1
            op = row.operator('nf.seed', text="", icon='TRIA_RIGHT')
            op.step = 1
            op = row.operator('nf.seed', text="", icon='FILE_REFRESH')
            op.step = 0
        if presets.GEN.get(kind) and len(presets.GEN[kind]) > 1:
            l.operator_menu_enum('nf.preset', 'preset', text="Preset: " + presets.GEN[kind].get(
                ob.get('nf_preset', ''), ("Choose",))[0], icon='PRESET')
        for pname, items in iter_panels(ng):
            items = [it for it in items if it.name != 'Seed' and
                     it.socket_type not in ('NodeSocketMaterial', 'NodeSocketImage', 'NodeSocketGeometry')]
            if not items or pname in MAT_PANELS:
                continue
            if pname == '':
                col = l.column(align=True)
                for it in items:
                    draw_input(col, md, it)
                continue
            header, body = l.panel(f'nf_{kind}_{pname}', default_closed=pname not in ('Shape', 'Trunk', 'Size'))
            header.label(text=pname)
            if body is not None:
                col = body.column(align=True)
                for it in items:
                    draw_input(col, md, it)
                op = body.operator('nf.reset_inputs', text="Reset", icon='LOOP_BACK', emboss=False)
                op.panel = pname
        dg = context.evaluated_depsgraph_get()
        me = ob.evaluated_get(dg).data
        n = len(me.polygons)
        l.label(text=f"{n:,} faces  /  {len(me.vertices):,} vertices", icon='ERROR' if n > 1_500_000 else 'MESH_DATA')


# ---------------------------------------------------------------------------------------------- material
class NF_MT_layer_add(Menu):
    bl_idname = 'NF_MT_layer_add'
    bl_label = "Add Paint Layer"

    def draw(self, context):
        mat = getattr(context, 'material', None)
        for key, v in presets.LAYER.items():
            op = self.layout.operator('nf.layer_add', text=v['label'], icon=v['icon'])
            op.preset = key
            op.material = mat.name if mat else ''


def _prop(layout, node, name, text=None, **kw):
    if node is not None and name in node.inputs:
        layout.prop(node.inputs[name], 'default_value', text=text or name, **kw)


def draw_layer(layout, ob, md, role, mat, i, L, nlayers):
    g, mk = shading.layer_nodes(mat, L)
    ts = (library.get(L['set']) or L.get('ts')) if L.get('set') else None
    header, body = layout.panel(f"nf_layer_{mat.name}_{L['uid']}", default_closed=i > 0)
    header.label(text=("Base: " if i == 0 else f"{i + 1}. ") + L['name'],
                 icon='TEXTURE' if ts else 'COLOR')
    sub = header.row(align=True)
    op = sub.operator('nf.apply_set', text="", icon='IMPORT')
    op.material, op.layer = mat.name, i
    op = sub.operator('nf.apply_set_file', text="", icon='FILEBROWSER')
    op.material, op.layer = mat.name, i
    if i > 0:
        op = sub.operator('nf.layer_move', text="", icon='TRIA_UP')
        op.material, op.layer, op.direction = mat.name, i, 1
        op = sub.operator('nf.layer_move', text="", icon='TRIA_DOWN')
        op.material, op.layer, op.direction = mat.name, i, -1
        op = sub.operator('nf.layer_remove', text="", icon='X')
        op.material, op.layer = mat.name, i
    if body is None or g is None:
        return
    col = body.column(align=True)
    if ts:
        row = col.row(align=True)
        row.label(text=ts['name'], icon='TEXTURE')
        op = row.operator('nf.layer_clear_set', text="", icon='PANEL_CLOSE')
        op.material, op.layer = mat.name, i
        col.label(text=library.describe(ts))
    else:
        col.label(text="Fill colour  (import a texture set: arrow button)", icon='INFO')
        _prop(col, g, 'Fill Color', "Colour")
        _prop(col, g, 'Fill Roughness', "Roughness", slider=True)
        _prop(col, g, 'Fill Metallic', "Metallic", slider=True)
    col.separator()
    base_geo = i == 0 and md is not None and role in ('Material', 'Bark Material')
    if base_geo:
        its = inputs(md)
        box = col.box()
        box.label(text="3D Detail from the height map", icon='MOD_DISPLACE')
        bc = box.column(align=True)
        for n in ('Texture Depth', 'Mid Level', 'Tile Size', 'Projection Blend', 'Invert Height'):
            if n in its:
                draw_input(bc, md, its[n])
        if 'Height Map' in its:
            draw_input(bc, md, its['Height Map'], "Height")
        if md.node_group['nf_kind'] in ('ROCK', 'CLIFF', 'MOUNTAIN', 'ROCKIFY'):
            det = its.get('Detail Level') or its.get('Subdivisions')
            if det is not None:
                draw_input(bc, md, det, "Mesh Detail")
    elif 'Tile Size' in g.inputs:
        _prop(col, g, 'Tile Size')
        _prop(col, g, 'Blend', "Projection Blend", slider=True)
    elif 'UV Scale' in g.inputs:
        _prop(col, g, 'UV Scale')
        _prop(col, g, 'Leaf Shape', slider=True)
    if ts:
        _prop(col, g, 'Normal Strength')
        _prop(col, g, 'DirectX Normal', "DirectX Normal (flip green)", slider=True)
        _prop(col, g, 'AO Strength', slider=True)
    h2, b2 = body.panel(f"nf_adj_{mat.name}_{L['uid']}", default_closed=True)
    h2.label(text="Colour Adjust")
    if b2 is not None:
        c2 = b2.column(align=True)
        for n in ('Hue', 'Saturation', 'Value', 'Tint', 'Tint Amount', 'Roughness +/-', 'Metallic +/-'):
            _prop(c2, g, n, slider=n in ('Hue', 'Tint Amount'))
    if mk is not None:
        h3, b3 = body.panel(f"nf_mask_{mat.name}_{L['uid']}", default_closed=False)
        h3.label(text="Where it appears (auto-paint)", icon='MOD_MASK')
        if b3 is not None:
            c3 = b3.column(align=True)
            for n in ('Top', 'Cavity', 'Edges', 'Height'):
                _prop(c3, mk, n, slider=True)
            c3.separator()
            for n in ('Combine', 'Coverage', 'Sharpness', 'Noise', 'Noise Scale', 'Crack Fill', 'Detail', 'Opacity'):
                _prop(c3, mk, n, slider=n not in ('Noise Scale',))
            if i <= 3:
                op = b3.operator('nf.paint', text="Paint this Layer", icon='BRUSH_DATA')
                op.layer = i


class NF_PT_material(_Base, Panel):
    bl_label = "Material & Textures"
    bl_idname = 'NF_PT_material'
    bl_parent_id = 'NF_PT_main'

    @classmethod
    def poll(cls, context):
        ob = ctx_ob(context)
        return ob is not None and (nf_mod(ob) is not None or bool(nf_materials(ob)))

    def draw_header(self, context):
        self.layout.label(icon='MATERIAL')

    def draw(self, context):
        l = self.layout
        ob = ctx_ob(context)
        md = nf_mod(ob)
        mats = nf_materials(ob)
        if not mats:
            l.label(text="No Nature Forge material", icon='INFO')
            return
        for role, mat in mats:
            box = l.box()
            row = box.row()
            row.label(text=f"{role}: {mat.name}", icon='MATERIAL')
            layers = shading.layers_of(mat)
            for i, L in enumerate(layers):
                draw_layer(box, ob, md, role, mat, i, L, len(layers))
            row = box.row(align=True)
            row.context_pointer_set('material', mat)
            if len(layers) < 4:
                row.menu('NF_MT_layer_add', text="Add Paint Layer", icon='ADD')
            sh = mat.node_tree.nodes.get('NF:shade') if mat.node_tree else None
            if sh is not None and mat.get('nf_kind') == 'TRI':
                c = box.column(align=True)
                _prop(c, sh, 'Cavity Darken', slider=True)
                _prop(c, sh, 'Edge Lighten', slider=True)


# ---------------------------------------------------------------------------------------------- library
class NF_PT_library(_Base, Panel):
    bl_label = "Texture Library"
    bl_idname = 'NF_PT_library'
    bl_parent_id = 'NF_PT_main'

    def draw_header(self, context):
        self.layout.label(icon='ASSET_MANAGER')

    def draw(self, context):
        l = self.layout
        st = context.window_manager.nf_lib
        p = prefs(context)
        sets = library.all_sets()
        row = l.row(align=True)
        row.label(text=f"{len(sets)} texture sets")
        row.prop(st, 'show_folders', text="", icon='FILE_FOLDER')
        row.operator('nf.lib_scan', text="", icon='FILE_REFRESH')
        if st.show_folders or (p is not None and not len(p.folders)):
            box = l.box()
            if p is not None:
                for i, f in enumerate(p.folders):
                    r = box.row(align=True)
                    r.prop(f, 'path', text="")
                    op = r.operator('nf.lib_remove_folder', text="", icon='X')
                    op.index = i
            box.operator('nf.lib_add_folder', icon='ADD')
            if p is not None and not len(p.folders):
                box.label(text="e.g. Paid Mats 3D/tex, 99 Pack/Textures", icon='INFO')
        if not sets:
            return
        row = l.row(align=True)
        row.prop(st, 'search', text="", icon='VIEWZOOM')
        row.prop(st, 'category', text="")
        l.template_icon_view(st, 'pick', show_labels=True, scale=7.0, scale_popup=5.0)
        sid = st.pick_id or (st.pick if st.pick != 'NONE' else '')
        ts = library.get(sid)
        if ts is not None:
            col = l.column(align=True)
            col.label(text=ts['name'], icon='TEXTURE')
            col.label(text=library.describe(ts))
            info = ts.get('info') or {}
            if info.get('depth') or info.get('tile'):
                col.label(text="info.json: " + ", ".join(
                    ([f"depth {info['depth'] * 100:.1f} cm"] if info.get('depth') else []) +
                    ([f"size {info['tile']:.2f} m"] if info.get('tile') else [])), icon='FILE_TEXT')
        ob = ctx_ob(context)
        mats = nf_materials(ob) if ob is not None else []
        if mats and ts is not None:
            box = l.box()
            box.label(text="Apply to:", icon='IMPORT')
            for role, mat in mats:
                grid = box.grid_flow(columns=2, even_columns=True, align=True)
                for i, L in enumerate(shading.layers_of(mat)):
                    op = grid.operator('nf.apply_set', text=(role.split()[0] + ": " if len(mats) > 1 else "") +
                                       ("Base" if i == 0 else L['name']))
                    op.material, op.layer, op.set_id = mat.name, i, ts['id']
        elif ob is not None and ts is not None and not mats:
            op = l.operator('nf.apply_set', text="Apply to Selected Mesh", icon='IMPORT')
            op.set_id = ts['id']
        l.operator('nf.asset_materials', icon='ASSET_MANAGER')


# ---------------------------------------------------------------------------------------------- finish
class NF_PT_finish(_Base, Panel):
    bl_label = "Paint, Finish & Bake"
    bl_idname = 'NF_PT_finish'
    bl_parent_id = 'NF_PT_main'

    @classmethod
    def poll(cls, context):
        ob = ctx_ob(context)
        return ob is not None and (nf_mod(ob) is not None or bool(nf_materials(ob)) or ob.get('nf_final'))

    def draw_header(self, context):
        self.layout.label(icon='BRUSH_DATA')

    def draw(self, context):
        l = self.layout
        ob = ctx_ob(context)
        md = nf_mod(ob)
        if context.mode == 'PAINT_VERTEX':
            box = l.box()
            k = context.window_manager.get('nf_paint_layer', 1)
            box.label(text=f"Painting layer {k + 1}", icon='BRUSH_DATA')
            row = box.row(align=True)
            row.scale_y = 1.3
            op = row.operator('nf.paint_color', text="Paint In", icon='ADD')
            op.value = 1.0
            op = row.operator('nf.paint_color', text="Paint Out", icon='REMOVE')
            op.value = 0.0
            ts = context.tool_settings
            ups = getattr(ts.vertex_paint, 'unified_paint_settings', None) or getattr(ts, 'unified_paint_settings',
                                                                                     None)
            br = ts.vertex_paint.brush
            col = box.column(align=True)
            if ups is not None and getattr(ups, 'use_unified_size', False):
                col.prop(ups, 'size', text="Radius")
            elif br is not None:
                col.prop(br, 'size', text="Radius")
            if br is not None:
                col.prop(br, 'strength', text="Strength")
            paint_buttons(box, ob)
            box.operator('nf.paint_done', icon='CHECKMARK')
            return
        if md is not None:
            col = l.column(align=True)
            col.operator('nf.finalize', icon='MESH_DATA')
            col.label(text="Live generator: every slider still works", icon='INFO')
        elif ob.get('nf_final'):
            l.operator('nf.restore', icon='LOOP_BACK')
        paint_buttons(l, ob)
        box = l.box()
        box.label(text="Game export", icon='EXPORT')
        if mlm_available():
            box.operator('nf.mlm_bake', icon='RENDER_STILL')
            box.label(text="LOD levels / sizes: Mixer tab")
        else:
            box.label(text="Enable Material Layer Mixer to bake")
            box.label(text="low-poly LODs with textures.")


def paint_buttons(layout, ob):
    rows = []
    for role, mat in nf_materials(ob):
        for i, L in enumerate(shading.layers_of(mat)):
            if 0 < i <= 3:
                rows.append((i, L['name']))
    if not rows:
        layout.label(text="Add a paint layer to paint it", icon='INFO')
        return
    col = layout.column(align=True)
    for i, name in rows:
        op = col.operator('nf.paint', text=f"Paint: {name}", icon='BRUSH_DATA')
        op.layer = i


CLASSES = tuple(ADD_MENUS) + (NF_MT_layer_add, NF_PT_main, NF_PT_shape, NF_PT_material, NF_PT_library, NF_PT_finish)
