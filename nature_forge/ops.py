# Nature Forge - operators
import random
import bpy
from bpy.props import StringProperty, EnumProperty, IntProperty, BoolProperty, CollectionProperty, FloatProperty
from bpy.types import Operator, OperatorFileListElement
from . import gen_rock, gen_cliff, gen_mountain, gen_tree, gen_rockify
from . import library, shading, presets
from .props import prefs

MOD = "Nature Forge"
BUILDERS = {'ROCK': gen_rock.build, 'CLIFF': gen_cliff.build, 'MOUNTAIN': gen_mountain.build,
            'TREE': gen_tree.build, 'ROCKIFY': gen_rockify.build}
NAMES = {'ROCK': "Rock", 'CLIFF': "Cliff", 'MOUNTAIN': "Mountain", 'TREE': "Tree", 'ROCKIFY': "Rocky"}
# GN material inputs per generator -> (material role, shading kind, default layers)
MAT_INPUTS = {'ROCK': [('Material', 'TRI', 'ROCK')], 'CLIFF': [('Material', 'TRI', 'CLIFF')],
              'MOUNTAIN': [('Material', 'TRI', 'MOUNTAIN')], 'ROCKIFY': [('Material', 'TRI', 'ROCKIFY')],
              'TREE': [('Bark Material', 'TRI', 'BARK'), ('Leaf Material', 'UV', 'LEAVES')]}


# ---------------------------------------------------------------------------------------------- helpers
def nf_mod(ob):
    if ob is None or ob.type != 'MESH':
        return None
    md = ob.modifiers.get(MOD)
    if md is not None and md.type == 'NODES' and md.node_group is not None and md.node_group.get('nf_kind'):
        return md
    return next((m for m in ob.modifiers if m.type == 'NODES' and m.node_group is not None
                 and m.node_group.get('nf_kind')), None)


def kind_of(ob):
    md = nf_mod(ob)
    if md is not None:
        return md.node_group['nf_kind']
    return ob.get('nf_kind') if ob is not None else None


def inputs(md):
    return {it.name: it for it in md.node_group.interface.items_tree
            if getattr(it, 'item_type', 'SOCKET') == 'SOCKET' and it.in_out == 'INPUT'}


def get_input(md, name, default=None):
    it = inputs(md).get(name)
    if it is None:
        return default
    try:
        return md[it.identifier]
    except KeyError:
        return default


def set_input(md, name, value):
    it = inputs(md).get(name)
    if it is None:
        return False
    if it.socket_type == 'NodeSocketMenu' and isinstance(value, str):
        value = ['None', 'Cards', 'Clumps'].index(value) if value in ('None', 'Cards', 'Clumps') else 0
    try:
        md[it.identifier] = value
    except (TypeError, ValueError, OverflowError):
        try:
            md[it.identifier] = type(md[it.identifier])(value)
        except Exception:
            return False
    return True


def touch(ob):
    ob.update_tag()
    if bpy.context.screen:
        for a in bpy.context.screen.areas:
            a.tag_redraw()


def ctx_ob(context):
    ob = context.object or context.view_layer.objects.active
    return ob if ob is not None and ob.type == 'MESH' else None


def nf_materials(ob):
    """-> [(role label, material)] the add-on manages on this object"""
    out = []
    md = nf_mod(ob)
    if md is not None:
        for name, kind, _ in MAT_INPUTS.get(md.node_group['nf_kind'], []):
            m = get_input(md, name)
            if shading.is_nf(m):
                out.append((name, m))
    for s in ob.material_slots:
        if shading.is_nf(s.material) and s.material not in [m for _, m in out]:
            out.append(("Material", s.material))
    return out


def ensure_slot(ob, mat):
    if mat.name not in [m.name for m in ob.data.materials if m]:
        ob.data.materials.append(mat)


def sync_base_height(ob, mat, report=None, suggest=False):
    """the generator displaces with the base layer's height map; the first time a set comes with info.json depth /
    size, those become the texture depth / tile size"""
    md = nf_mod(ob)
    if md is None:
        return
    role = next((n for n, m in nf_materials(ob) if m == mat), None)
    if role not in ('Material', 'Bark Material'):
        return
    img, src = shading.base_height_image(mat)
    set_input(md, 'Height Map', img)
    if suggest:
        L = shading.layers_of(mat)[0]
        ts = library.get(L.get('set')) or L.get('ts') or {}
        info = ts.get('info', {})
        if 'depth' in info:
            set_input(md, 'Texture Depth', float(info['depth']))
        if 'tile' in info:
            set_input(md, 'Tile Size', float(info['tile']))
        if 'mid' in info:
            set_input(md, 'Mid Level', float(info['mid']))
    if report is not None and src == 'color':
        report({'INFO'}, "This set has no height map: displacing with the colour brightness")
    touch(ob)


def new_object(context, kind, name):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    coll = context.collection or context.scene.collection
    coll.objects.link(ob)
    ob.location = context.scene.cursor.location
    for o in context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob
    return ob


def setup_generator(context, ob, kind, preset=None):
    ng = BUILDERS[kind]()
    md = ob.modifiers.new(MOD, 'NODES')
    md.node_group = ng
    ob['nf_kind'] = kind
    for inp, skind, defaults in MAT_INPUTS[kind]:
        mat = shading.create_material(f"NF_{ob.name}" + ("" if inp == 'Material' else "_" + inp.split()[0]),
                                      skind, defaults)
        set_input(md, inp, mat)
        ensure_slot(ob, mat)
        if kind == 'ROCKIFY':
            set_input(md, 'Replace Material', True)
    if preset:
        apply_preset(md, kind, preset)
    touch(ob)
    return md


def apply_preset(md, kind, key):
    p = presets.GEN.get(kind, {}).get(key)
    if p is None:
        return
    base = presets.GEN[kind][next(iter(presets.GEN[kind]))][2]
    keep = {'Seed', 'Material', 'Bark Material', 'Leaf Material', 'Height Map', 'Texture Depth', 'Tile Size',
            'Mid Level', 'Projection Blend', 'Invert Height'}
    for name, it in inputs(md).items():
        if name in keep or it.socket_type in ('NodeSocketGeometry', 'NodeSocketMaterial', 'NodeSocketImage'):
            continue
        dv = getattr(it, 'default_value', None)
        if dv is None:
            continue
        if it.socket_type == 'NodeSocketMenu':
            dv = 'Cards'
        set_input(md, name, tuple(dv) if hasattr(dv, '__len__') and not isinstance(dv, str) else dv)
    for k, v in dict(base, **p[2]).items():
        set_input(md, k, v)
    md.id_data['nf_preset'] = key


# ---------------------------------------------------------------------------------------------- create
def _preset_items(self, context):
    items = presets.gen_items(self.kind)
    return items or [('NONE', "Default", "")]


class NF_OT_add(Operator):
    bl_idname = 'nf.add'
    bl_label = "Add Generator"
    bl_description = "Add a live, fully adjustable Geometry Nodes object at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(items=[(k[0], k[1], k[2]) for k in presets.KINDS])
    preset: EnumProperty(name="Preset", items=_preset_items)
    seed: IntProperty(name="Seed", default=-1, min=-1, description="-1 = random")

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        ob = new_object(context, self.kind, NAMES[self.kind])
        md = setup_generator(context, ob, self.kind, None if self.preset == 'NONE' else self.preset)
        set_input(md, 'Seed', self.seed if self.seed >= 0 else random.randint(0, 9999))
        touch(ob)
        return {'FINISHED'}


def rockify(context, ob, budget=400_000):
    """Rockify modifier with a subdivision level that keeps the result under ~budget faces"""
    md = setup_generator(context, ob, 'ROCKIFY')
    faces = max(len(ob.data.polygons), 1)
    lvl = 0
    while lvl < 4 and faces * 4 ** (lvl + 1) <= budget:
        lvl += 1
    set_input(md, 'Subdivisions', lvl)
    touch(ob)
    return md


class NF_OT_rockify(Operator):
    bl_idname = 'nf.rockify'
    bl_label = "Rockify Selected"
    bl_description = ("Turn your own meshes rocky: subdivide them and let a texture set's height map push the "
                      "surface out (non-destructive modifier; the colour / normal maps follow the bumps)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' and nf_mod(o) is None and not o.nf_hand.active for o in context.selected_objects)

    def execute(self, context):
        n = 0
        for ob in [o for o in context.selected_objects if o.type == 'MESH' and nf_mod(o) is None
                   and not o.nf_hand.active]:
            rockify(context, ob)
            n += 1
        self.report({'INFO'}, f"Rockified {n} object(s) - pick a texture set in Texture Library")
        return {'FINISHED'}


class NF_OT_seed(Operator):
    bl_idname = 'nf.seed'
    bl_label = "New Seed"
    bl_description = "A new random variation (same settings)"
    bl_options = {'REGISTER', 'UNDO'}

    step: IntProperty(default=0, description="0 = random, otherwise add this to the seed")

    @classmethod
    def poll(cls, context):
        return nf_mod(ctx_ob(context)) is not None

    def execute(self, context):
        ob = ctx_ob(context)
        md = nf_mod(ob)
        cur = get_input(md, 'Seed', 0) or 0
        set_input(md, 'Seed', max(0, cur + self.step) if self.step else random.randint(0, 9999))
        touch(ob)
        return {'FINISHED'}


def _obj_preset_items(self, context):
    k = kind_of(ctx_ob(context))
    items = presets.gen_items(k) if k else []
    _obj_preset_items.cache = items or [('NONE', "None", "")]
    return _obj_preset_items.cache


_obj_preset_items.cache = []


class NF_OT_preset(Operator):
    bl_idname = 'nf.preset'
    bl_label = "Apply Preset"
    bl_description = "Reset the shape settings to a preset (textures, materials and seed are kept)"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=_obj_preset_items)

    @classmethod
    def poll(cls, context):
        return nf_mod(ctx_ob(context)) is not None

    def execute(self, context):
        ob = ctx_ob(context)
        md = nf_mod(ob)
        apply_preset(md, md.node_group['nf_kind'], self.preset)
        touch(ob)
        return {'FINISHED'}


class NF_OT_set_input(Operator):
    bl_idname = 'nf.set_input'
    bl_label = "Set"
    bl_description = "Set this generator option"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    name: StringProperty()
    value: IntProperty()

    def execute(self, context):
        ob = ctx_ob(context)
        md = nf_mod(ob)
        if md is None:
            return {'CANCELLED'}
        set_input(md, self.name, self.value)
        touch(ob)
        return {'FINISHED'}


class NF_OT_reset_input(Operator):
    bl_idname = 'nf.reset_inputs'
    bl_label = "Reset Section"
    bl_description = "Reset these settings to their defaults"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    panel: StringProperty()

    def execute(self, context):
        ob = ctx_ob(context)
        md = nf_mod(ob)
        if md is None:
            return {'CANCELLED'}
        for name, it in inputs(md).items():
            par = getattr(it, 'parent', None)
            if par is not None and getattr(par, 'name', '') == self.panel and hasattr(it, 'default_value') and \
                    it.socket_type not in ('NodeSocketImage', 'NodeSocketMaterial'):
                dv = it.default_value
                set_input(md, name, tuple(dv) if hasattr(dv, '__len__') and not isinstance(dv, str) else dv)
        touch(ob)
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- library
class NF_OT_lib_scan(Operator):
    bl_idname = 'nf.lib_scan'
    bl_label = "Scan Library"
    bl_description = "Find every texture set in your library folders (and their subfolders)"

    def execute(self, context):
        p = prefs(context)
        roots = [f.path for f in p.folders if f.path] if p else []
        if not roots:
            self.report({'WARNING'}, "Add a library folder first")
            return {'CANCELLED'}
        sets = library.scan(roots)
        library.set_sets(sets)
        st = context.window_manager.nf_lib
        st.category = 'ALL'
        if sets:
            st.pick_id = sets[0]['id']
        self.report({'INFO'}, f"{len(sets)} texture sets in {len(library.categories())} categories")
        return {'FINISHED'}


class NF_OT_lib_add_folder(Operator):
    bl_idname = 'nf.lib_add_folder'
    bl_label = "Add Library Folder"
    bl_description = ("Add a folder with texture sets (e.g. 'Paid Mats 3D/tex' or '99 Pack Vol (1)/Textures'). "
                      "Subfolders are scanned too")

    directory: StringProperty(subtype='DIR_PATH')
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        p = prefs(context)
        if p is None or not self.directory:
            return {'CANCELLED'}
        path = bpy.path.abspath(self.directory)
        if path not in [f.path for f in p.folders]:
            p.folders.add().path = path
            context.preferences.is_dirty = True
        bpy.ops.nf.lib_scan()
        return {'FINISHED'}


class NF_OT_lib_remove_folder(Operator):
    bl_idname = 'nf.lib_remove_folder'
    bl_label = "Remove Folder"
    bl_description = "Remove this folder from the library (no files are deleted)"

    index: IntProperty()

    def execute(self, context):
        p = prefs(context)
        if p is None or not (0 <= self.index < len(p.folders)):
            return {'CANCELLED'}
        p.folders.remove(self.index)
        context.preferences.is_dirty = True
        roots = [f.path for f in p.folders if f.path]
        library.set_sets(library.scan(roots) if roots else [])
        return {'FINISHED'}


def _mat_by_name(name):
    return bpy.data.materials.get(name) if name else None


def apply_set_to_layer(ob, mat, index, ts, report=None):
    layers = shading.layers_of(mat)
    if not (0 <= index < len(layers)):
        return False
    first_set = not layers[index].get('set')
    layers[index]['set'] = ts['id']
    layers[index]['ts'] = {k: ts[k] for k in ('id', 'name', 'maps', 'normal_dx', 'category', 'dir', 'info')
                           if k in ts}
    if index == 0 or layers[index]['name'] in ("Layer",) or first_set:
        layers[index]['name'] = ts['name'] if index == 0 else layers[index]['name']
    library.remember(ts)
    shading.build_material(mat, layers, mat.get('nf_kind', 'TRI'))
    g, _ = shading.layer_nodes(mat, layers[index])
    if g is not None and 'DirectX Normal' in g.inputs:
        g.inputs['DirectX Normal'].default_value = 1.0 if ts.get('normal_dx') else 0.0
    if index > 0 and g is not None and 'tile' in ts.get('info', {}) and 'Tile Size' in g.inputs:
        g.inputs['Tile Size'].default_value = float(ts['info']['tile'])
    if index == 0 and ob is not None:
        sync_base_height(ob, mat, report, suggest=first_set)
    return True


class NF_OT_apply_set(Operator):
    bl_idname = 'nf.apply_set'
    bl_label = "Apply Texture Set"
    bl_description = ("Use the texture set picked in the library for this layer: colour, normal, roughness, metal, "
                      "AO and height are connected automatically (base layer: the height map becomes 3D detail)")
    bl_options = {'REGISTER', 'UNDO'}

    material: StringProperty()
    layer: IntProperty(default=0)
    set_id: StringProperty(description="Empty = the set picked in the library")

    @classmethod
    def poll(cls, context):
        return ctx_ob(context) is not None

    def execute(self, context):
        ob = ctx_ob(context)
        st = context.window_manager.nf_lib
        sid = self.set_id or st.pick_id or (st.pick if st.pick != 'NONE' else '')
        ts = library.get(sid)
        if ts is None:
            self.report({'ERROR'}, "Pick a texture set in the Texture Library first")
            return {'CANCELLED'}
        if ob.nf_hand.active:                           # a Step by Step rock: hand the set to the guide
            return bpy.ops.nf.hand_set_library(set_id=ts['id'])
        mat = _mat_by_name(self.material) or (nf_materials(ob)[0][1] if nf_materials(ob) else None)
        if mat is None:
            if nf_mod(ob) is None and 'nf_rest' not in ob.data.attributes:
                rockify(context, ob)                  # plain mesh: the material needs the stored mapping
                self.report({'INFO'}, f"{ob.name}: Rockify modifier added")
            mat = nf_materials(ob)[0][1] if nf_materials(ob) else None
            if mat is None:
                mat = shading.create_material(f"NF_{ob.name}", 'TRI', 'ROCKIFY')
                ensure_slot(ob, mat)
        if not apply_set_to_layer(ob, mat, self.layer, ts, self.report):
            return {'CANCELLED'}
        self.report({'INFO'}, f"{ts['name']}: {library.describe(ts)}")
        touch(ob)
        return {'FINISHED'}


class NF_OT_apply_set_file(Operator):
    bl_idname = 'nf.apply_set_file'
    bl_label = "Load Texture Set from Disk"
    bl_description = ("Pick any ONE map of a texture set (or the folder): the other maps next to it are found and "
                      "connected automatically")
    bl_options = {'REGISTER', 'UNDO'}

    material: StringProperty(options={'HIDDEN'})
    layer: IntProperty(default=0, options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    files: CollectionProperty(type=OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ob = ctx_ob(context)
        found = library.set_from_files(self.directory, [f.name for f in self.files])
        if not found:
            self.report({'ERROR'}, "No texture set found there (need at least a colour, normal or height map)")
            return {'CANCELLED'}
        ts = found[0]
        mat = _mat_by_name(self.material) or (nf_materials(ob)[0][1] if ob and nf_materials(ob) else None)
        if mat is None:
            return {'CANCELLED'}
        apply_set_to_layer(ob, mat, self.layer, ts, self.report)
        context.window_manager.nf_lib.pick_id = ts['id']
        self.report({'INFO'}, f"{ts['name']}: {library.describe(ts)}")
        touch(ob)
        return {'FINISHED'}


class NF_OT_asset_materials(Operator):
    bl_idname = 'nf.asset_materials'
    bl_label = "Make Asset Materials"
    bl_description = ("Create a regular material for every texture set shown (filtered) and mark them as assets: "
                      "they appear in the Asset Browser and work with the Material Layer Mixer")
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        from .props import filtered_sets
        n = len(filtered_sets(context.window_manager.nf_lib))
        return context.window_manager.invoke_confirm(self, event, message=f"Create {n} asset materials?") \
            if bpy.app.version >= (4, 1, 0) else context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from .props import filtered_sets
        n, skipped = 0, 0
        for ts in filtered_sets(context.window_manager.nf_lib):
            try:
                shading.asset_material(ts)
                n += 1
            except (FileNotFoundError, RuntimeError):
                skipped += 1
        self.report({'INFO'}, f"{n} asset materials in this file (save it; add it as an asset library to reuse)" +
                    (f" - {skipped} skipped (files missing, rescan the library)" if skipped else ""))
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- layers
class NF_OT_layer_add(Operator):
    bl_idname = 'nf.layer_add'
    bl_label = "Add Paint Layer"
    bl_description = "Add an auto-painted layer on top (moss, snow, dirt ... ) - pick a texture set for it any time"
    bl_options = {'REGISTER', 'UNDO'}

    material: StringProperty()
    preset: EnumProperty(name="Layer", items=presets.LAYER_ITEMS)

    def execute(self, context):
        mat = _mat_by_name(self.material)
        if mat is None:
            return {'CANCELLED'}
        layers = shading.layers_of(mat)
        if len(layers) >= 4:
            self.report({'WARNING'}, "Up to 4 layers (base + 3) per material")
            return {'CANCELLED'}
        layers.append(shading.new_layer(preset=self.preset))
        shading.build_material(mat, layers, mat.get('nf_kind', 'TRI'))
        return {'FINISHED'}


class NF_OT_layer_remove(Operator):
    bl_idname = 'nf.layer_remove'
    bl_label = "Remove Layer"
    bl_description = "Remove this layer"
    bl_options = {'REGISTER', 'UNDO'}

    material: StringProperty()
    layer: IntProperty()

    def execute(self, context):
        mat = _mat_by_name(self.material)
        layers = shading.layers_of(mat) if mat else []
        if not (0 < self.layer < len(layers)):
            return {'CANCELLED'}
        layers.pop(self.layer)
        shading.build_material(mat, layers, mat.get('nf_kind', 'TRI'))
        return {'FINISHED'}


class NF_OT_layer_move(Operator):
    bl_idname = 'nf.layer_move'
    bl_label = "Move Layer"
    bl_description = "Move this layer up / down (the base layer stays at the bottom)"
    bl_options = {'REGISTER', 'UNDO'}

    material: StringProperty()
    layer: IntProperty()
    direction: IntProperty(default=1)

    def execute(self, context):
        mat = _mat_by_name(self.material)
        layers = shading.layers_of(mat) if mat else []
        j = self.layer + self.direction
        if not (0 < self.layer < len(layers) and 0 < j < len(layers)):
            return {'CANCELLED'}
        layers[self.layer], layers[j] = layers[j], layers[self.layer]
        shading.build_material(mat, layers, mat.get('nf_kind', 'TRI'))
        return {'FINISHED'}


class NF_OT_layer_clear_set(Operator):
    bl_idname = 'nf.layer_clear_set'
    bl_label = "Use Fill Colour"
    bl_description = "Drop the texture set of this layer and use the plain fill colour"
    bl_options = {'REGISTER', 'UNDO'}

    material: StringProperty()
    layer: IntProperty()

    def execute(self, context):
        mat = _mat_by_name(self.material)
        layers = shading.layers_of(mat) if mat else []
        if not (0 <= self.layer < len(layers)):
            return {'CANCELLED'}
        layers[self.layer]['set'] = None
        layers[self.layer].pop('ts', None)
        shading.build_material(mat, layers, mat.get('nf_kind', 'TRI'))
        ob = ctx_ob(context)
        if self.layer == 0 and ob is not None:
            sync_base_height(ob, mat)
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------- finish / paint
def finalize(context, ob, keep=True):
    md = nf_mod(ob)
    if md is None:
        return None
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    backup = None
    if keep:
        backup = ob.copy()
        backup.data = ob.data.copy()
        backup.name = ob.name + "_Editable"
        coll = bpy.data.collections.get("NF Editable Copies")
        if coll is None:
            coll = bpy.data.collections.new("NF Editable Copies")
            context.scene.collection.children.link(coll)
        coll.objects.link(backup)
        backup.hide_render = True
        backup['nf_backup_of'] = ob.name
        try:
            backup.hide_set(True)
        except RuntimeError:
            pass
    mats = [m for _, m in nf_materials(ob)]
    kind = kind_of(ob)
    if len(ob.modifiers) == 1:
        dg = context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
        old = ob.data
        ob.modifiers.remove(md)
        ob.data = me
        me.name = old.name
        if old.users == 0:
            bpy.data.meshes.remove(old)
    else:                                         # other modifiers stay live: apply only ours
        with context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
            bpy.ops.object.modifier_apply(modifier=md.name)
    for m in mats:
        ensure_slot(ob, m)
    ob['nf_final'] = kind or ob.get('nf_kind', '')
    return backup


class NF_OT_finalize(Operator):
    bl_idname = 'nf.finalize'
    bl_label = "Make Real Mesh"
    bl_description = ("Turn the generator into a normal mesh (for painting, sculpting, export). A hidden editable "
                      "copy is kept in 'NF Editable Copies' when enabled in the preferences")
    bl_options = {'REGISTER', 'UNDO'}

    keep: BoolProperty(name="Keep Editable Copy", default=True)

    @classmethod
    def poll(cls, context):
        return nf_mod(ctx_ob(context)) is not None

    def invoke(self, context, event):
        p = prefs(context)
        self.keep = p.keep_editable if p else True
        return self.execute(context)

    def execute(self, context):
        ob = ctx_ob(context)
        n = len(ob.evaluated_get(context.evaluated_depsgraph_get()).data.polygons)
        finalize(context, ob, self.keep)
        self.report({'INFO'}, f"{ob.name}: real mesh with {n:,} faces" +
                    (" (editable copy kept)" if self.keep else ""))
        return {'FINISHED'}


class NF_OT_restore(Operator):
    bl_idname = 'nf.restore'
    bl_label = "Back to Generator"
    bl_description = "Bring back the hidden editable copy (the real mesh, and the painting on it, is replaced)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = ctx_ob(context)
        return ob is not None and any(o.get('nf_backup_of') == ob.name for o in bpy.data.objects)

    def execute(self, context):
        ob = ctx_ob(context)
        bk = next(o for o in bpy.data.objects if o.get('nf_backup_of') == ob.name)
        name, mw = ob.name, ob.matrix_world.copy()
        colls = list(ob.users_collection)
        bpy.data.objects.remove(ob)
        for c in list(bk.users_collection):
            c.objects.unlink(bk)
        for c in colls:
            c.objects.link(bk)
        del bk['nf_backup_of']
        bk.name = name
        bk.matrix_world = mw
        bk.hide_render = False
        bk.hide_set(False)
        bk.select_set(True)
        context.view_layer.objects.active = bk
        return {'FINISHED'}


def set_brush_color(context, rgb):
    ts = context.tool_settings
    p = ts.vertex_paint
    for ups in (getattr(p, 'unified_paint_settings', None), getattr(ts, 'unified_paint_settings', None)):
        if ups is not None:
            try:
                ups.use_unified_color = True
                ups.color = rgb
            except (AttributeError, TypeError):
                pass
    if p is not None and p.brush is not None:
        try:
            p.brush.color = rgb
        except (AttributeError, TypeError, RuntimeError):
            pass


class NF_OT_paint(Operator):
    bl_idname = 'nf.paint'
    bl_label = "Paint Layer"
    bl_description = ("Paint where this layer shows (white = more, black = less, on top of its automatic mask). "
                      "Generators are turned into a real mesh first")
    bl_options = {'REGISTER', 'UNDO'}

    layer: IntProperty(default=1)

    @classmethod
    def poll(cls, context):
        return ctx_ob(context) is not None

    def invoke(self, context, event):
        ob = ctx_ob(context)
        if nf_mod(ob) is not None:
            return context.window_manager.invoke_props_dialog(self, width=360)
        return self.execute(context)

    def draw(self, context):
        l = self.layout
        l.label(text="Painting needs a real mesh:", icon='INFO')
        l.label(text="the generator is applied (an editable copy is kept).")

    def execute(self, context):
        ob = ctx_ob(context)
        if nf_mod(ob) is not None:
            p = prefs(context)
            finalize(context, ob, p.keep_editable if p else True)
        name = f'nf_paint_{min(self.layer + 1, 4)}'
        me = ob.data
        a = me.color_attributes.get(name)
        if a is None:
            a = me.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')
            import numpy as np
            a.data.foreach_set('color', np.full(len(a.data) * 4, 0.5, np.float32))
        me.color_attributes.active_color = a
        if context.mode != 'PAINT_VERTEX':
            bpy.ops.object.mode_set(mode='VERTEX_PAINT')
        set_brush_color(context, (1.0, 1.0, 1.0))
        context.window_manager['nf_paint_layer'] = self.layer
        if context.space_data and context.space_data.type == 'VIEW_3D' and \
                context.space_data.shading.type in ('SOLID', 'WIREFRAME'):
            context.space_data.shading.type = 'MATERIAL'
        self.report({'INFO'}, "Paint white to add the layer, black to remove it")
        return {'FINISHED'}


class NF_OT_paint_color(Operator):
    bl_idname = 'nf.paint_color'
    bl_label = "Brush Colour"
    bl_description = "White = paint the layer in, black = paint it out"
    bl_options = {'REGISTER', 'INTERNAL'}

    value: FloatProperty(default=1.0)

    def execute(self, context):
        set_brush_color(context, (self.value,) * 3)
        return {'FINISHED'}


class NF_OT_paint_done(Operator):
    bl_idname = 'nf.paint_done'
    bl_label = "Done Painting"
    bl_description = "Back to Object mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}


def mlm_available():
    return hasattr(bpy.types, 'MLM_OT_bake_lods')


class NF_OT_mlm_bake(Operator):
    bl_idname = 'nf.mlm_bake'
    bl_label = "Bake Game LODs (Mixer)"
    bl_description = ("Material Layer Mixer: bake this object (as it looks now) onto low-poly LODs with normal / "
                      "colour / roughness / metal textures for your game. Levels and sizes: Mixer tab > 3D Surface "
                      "+ LOD Bake")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return mlm_available() and ctx_ob(context) is not None

    def execute(self, context):
        return bpy.ops.mlm.bake_lods('INVOKE_DEFAULT')


CLASSES = (NF_OT_add, NF_OT_rockify, NF_OT_seed, NF_OT_preset, NF_OT_set_input, NF_OT_reset_input, NF_OT_lib_scan,
           NF_OT_lib_add_folder, NF_OT_lib_remove_folder, NF_OT_apply_set, NF_OT_apply_set_file,
           NF_OT_asset_materials, NF_OT_layer_add, NF_OT_layer_remove, NF_OT_layer_move, NF_OT_layer_clear_set,
           NF_OT_finalize, NF_OT_restore, NF_OT_paint, NF_OT_paint_color, NF_OT_paint_done, NF_OT_mlm_bake)
