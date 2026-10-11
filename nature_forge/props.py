# Nature Forge - preferences (library folders) and UI state
import bpy
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty, CollectionProperty, PointerProperty
from bpy.types import PropertyGroup, AddonPreferences
from . import library

ADDON = __package__


def prefs(context=None):
    context = context or bpy.context
    a = context.preferences.addons.get(ADDON)
    return a.preferences if a else None


class NF_Folder(PropertyGroup):
    path: StringProperty(name="Folder", subtype='DIR_PATH')


def _cat_items(self, context):
    items = [('ALL', "All Categories", "")]
    for i, c in enumerate(library.categories()):
        items.append((str(i), c, ""))
    _cat_items.cache = items
    return items


_cat_items.cache = []


def filtered_sets(state):
    sets = library.all_sets()
    cats = library.categories()
    if state.category != 'ALL':
        try:
            cat = cats[int(state.category)]
            sets = [s for s in sets if s['category'] == cat]
        except (ValueError, IndexError):
            pass
    q = state.search.strip().lower()
    if q:
        words = q.split()
        sets = [s for s in sets if all(w in (s['name'] + ' ' + s['category']).lower() for w in words)]
    return sets


def _pick_items(self, context):
    items = []
    for i, s in enumerate(filtered_sets(self)[:600]):
        items.append((s['id'], s['name'], f"{s['category']}  -  {library.describe(s)}", library.icon(s), i))
    if not items:
        items = [('NONE', "No texture sets", "Add a folder and press Scan", 'ERROR', 0)]
    _pick_items.cache = items                    # Blender needs the list alive while the enum is shown
    return items


_pick_items.cache = []


def _upd_filter(self, context):
    items = _pick_items(self, context)
    if items and items[0][0] != 'NONE' and self.get('pick_id') not in [i[0] for i in items]:
        self.pick_id = items[0][0]


def _upd_pick(self, context):
    if self.pick not in ('', 'NONE'):
        self.pick_id = self.pick


class NF_LibState(PropertyGroup):
    search: StringProperty(name="Search", options={'TEXTEDIT_UPDATE'}, update=_upd_filter,
                           description="Filter the texture sets by name / category")
    category: EnumProperty(name="Category", items=_cat_items, update=_upd_filter)
    pick: EnumProperty(name="Texture Set", items=_pick_items, update=_upd_pick)
    pick_id: StringProperty()
    show_folders: BoolProperty(name="Folders", default=False)


class NF_Prefs(AddonPreferences):
    bl_idname = ADDON

    folders: CollectionProperty(type=NF_Folder)
    active_folder: IntProperty()
    auto_scan: BoolProperty(name="Scan on Startup", default=False,
                            description="Rescan the library folders every time Blender starts (otherwise the last "
                                        "scan is loaded from the cache)")
    keep_editable: BoolProperty(name="Keep Editable Copy", default=True,
                                description="'Make Real Mesh' keeps a hidden copy with the live generator, so you "
                                            "can go back and change the shape")

    def draw(self, context):
        l = self.layout
        l.label(text="Texture library folders (scanned recursively):")
        for i, f in enumerate(self.folders):
            row = l.row(align=True)
            row.prop(f, 'path', text="")
            op = row.operator('nf.lib_remove_folder', text="", icon='X')
            op.index = i
        row = l.row(align=True)
        row.operator('nf.lib_add_folder', icon='ADD')
        row.operator('nf.lib_scan', icon='FILE_REFRESH')
        l.prop(self, 'auto_scan')
        l.prop(self, 'keep_editable')


CLASSES = (NF_Folder, NF_LibState, NF_Prefs)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.nf_lib = PointerProperty(type=NF_LibState)


def unregister():
    del bpy.types.WindowManager.nf_lib
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
