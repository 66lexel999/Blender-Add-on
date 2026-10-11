# Paint guard: Texture Paint always targets the selected layer's mask, however the mode was entered
# (header dropdown, Ctrl+Tab pie, "Texture Paint" workspace tab, scripts, undo/redo, file load).
# Blender's MATERIAL paint mode collects EVERY Image Texture node of the material incl. nested groups
# (= the layer source JPGs) and paints slot 0 -> we force IMAGE mode with canvas = mask (or None).
import bpy
from bpy.app.handlers import persistent
from . import ops

_OWNER = object()                 # msgbus owner, lives as long as the module
_state = {"busy": False, "pending": False}
_flipped = set()                  # image editors we switched to VIEW by clearing their image (area.as_pointer())

_MSG_KEYS = (
    (bpy.types.Object, 'mode'),                   # mode_set / pie / dropdown / texture_paint_toggle / workspace tab
    (bpy.types.ImagePaint, 'mode'),               # user flips Texture Slots > Mode back to Material
    (bpy.types.ImagePaint, 'canvas'),             # user picks another canvas image
    (bpy.types.LayerObjects, 'active'),           # other object made active while painting
    (bpy.types.Object, 'active_material_index'),  # other material slot (= other stack)
)


def _target(context):
    """-> None (not a layered material: leave Blender alone) or (ob, image|None, uv_name|None, needs_setup)"""
    ob, mat, st = ops.ctx_stack(context)
    if st is None:
        return None
    L = ops.active_layer(st)
    M = ops.paint_mask_of(L) if L is not None and st.active > 0 else None      # base layer: never painted
    if M is None or M.type != 'PAINT':                                         # VERTEX masks: no image target
        return ob, None, None, False
    uv = ob.data.uv_layers.get(st.paint_uv)
    if M.image is None or uv is None:
        return ob, None, None, True                                            # mlm.paint must create them
    return ob, M.image, uv.name, False


def _paint_editors(context):
    """Image Editors in Paint mode (or ones we flipped to View) - collect BEFORE writing ip.canvas:
    a canvas change to None clears them itself and flips them to View"""
    out = []
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                sp = area.spaces.active
                if not sp.use_image_pin and (sp.mode == 'PAINT' or area.as_pointer() in _flipped):
                    out.append((area.as_pointer(), sp))
    return out


def _sync_image_editors(editors, img):
    """2D painting paints whatever the Image Editor shows: never leave a source texture there"""
    for key, sp in editors:
        if img is None:
            if sp.image is not None:
                sp.image = None                         # flips the editor to View: 2D strokes impossible
            _flipped.add(key)
        else:
            if sp.image != img:
                sp.image = img
            if sp.mode != 'PAINT':
                sp.mode = 'PAINT'
            _flipped.discard(key)


def retarget(context=None, allow_setup=True):
    """idempotent: writes only what differs (every RNA write re-publishes msgbus -> one extra no-op call)"""
    context = context or bpy.context
    if _state["busy"]:
        return
    ob = ops.ctx_object(context)
    if ob is not None and ob.mode == 'VERTEX_PAINT':
        _retarget_vertex(context)
        return
    if ob is None or ob.mode != 'TEXTURE_PAINT':
        return
    t = _target(context)
    if t is None:                                       # not a layered material: hand Blender its default back,
        ip = context.scene.tool_settings.image_paint    # but never keep painting another object's mask
        if ip.canvas is not None and ip.canvas.get('mlm_mask'):
            ip.canvas = None
            ip.mode = 'MATERIAL'
        return
    ob, img, uv_name, need_setup = t
    _state["busy"] = True
    try:
        editors = _paint_editors(context)
        ip = context.scene.tool_settings.image_paint
        if ip.mode != 'IMAGE':
            ip.mode = 'IMAGE'
        if ip.canvas != img:
            ip.canvas = img                             # None = harmless: 3D strokes are CANCELLED
        uvs = ob.data.uv_layers
        if img is not None and (uvs.active is None or uvs.active.name != uv_name):
            uvs.active = uvs[uv_name]                   # IMAGE mode paints through the ACTIVE uv map
        _sync_image_editors(editors, img)
    finally:
        _state["busy"] = False
    if need_setup and allow_setup and not _state["pending"]:
        _state["pending"] = True
        bpy.app.timers.register(_deferred_setup, first_interval=0.0)


def _retarget_vertex(context):
    """Vertex Paint paints the active color attribute: make it the selected layer's vertex mask"""
    ob, mat, st = ops.ctx_stack(context)
    if st is None or st.active == 0:
        return
    M = ops.paint_mask_of(ops.active_layer(st))
    if M is None or M.type != 'VERTEX' or not M.attr:
        return
    ca = ob.data.color_attributes
    a = ca.get(M.attr)
    if a is not None and ca.active_color != a:
        _state["busy"] = True
        try:
            ca.active_color = a
        finally:
            _state["busy"] = False


def _view3d(context):
    wins = [context.window] if context.window else []
    wins += [w for w in context.window_manager.windows if w not in wins]
    for w in wins:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                r = next((r for r in a.regions if r.type == 'WINDOW'), None)
                if r is not None:
                    return w, a, r
    return None, None, None


def _deferred_setup():
    """operators (mode_set EDIT / smart_project / image creation) never run inside msgbus or undo handlers"""
    _state["pending"] = False
    context = bpy.context
    ob = ops.ctx_object(context)
    t = _target(context) if ob is not None and ob.mode == 'TEXTURE_PAINT' else None
    if t is None or not t[3]:
        return None
    win, area, region = _view3d(context)
    if area is None:
        return None                                     # canvas stays None (harmless) until the Paint button
    _state["busy"] = True
    try:
        with context.temp_override(window=win, area=area, region=region):
            bpy.ops.mlm.paint()                         # creates paint UV + mask image, IMAGE mode, canvas, active UV
            bpy.ops.ed.undo_push(message="Mixer: Paint Mask Setup")   # bpy.ops from a timer push NO undo step
    except RuntimeError as e:
        print("Material Layer Mixer: paint setup failed:", e)
    finally:
        _state["busy"] = False
    retarget(context, allow_setup=False)                # image editors etc.
    return None


def _on_msg(*_):
    retarget(bpy.context)


def _subscribe():
    bpy.msgbus.clear_by_owner(_OWNER)                   # same owner+key twice = callback runs twice
    for key in _MSG_KEYS:
        bpy.msgbus.subscribe_rna(key=key, owner=_OWNER, args=(), notify=_on_msg)


@persistent
def _on_load_post(*_):
    _flipped.clear()
    _subscribe()                                        # file load drops ALL msgbus subscriptions (even PERSISTENT)
    retarget(bpy.context)                               # file saved in Texture Paint loads straight into it


@persistent
def _on_undo_redo(*_):
    # undo/redo never publishes Object.mode; memfile undo restores the mesh (active UV) but keeps tool settings
    retarget(bpy.context, allow_setup=False)


def _initial():
    retarget(bpy.context)                               # add-on enabled while already painting
    return None


_HANDLERS = (("load_post", _on_load_post), ("undo_post", _on_undo_redo), ("redo_post", _on_undo_redo))


def register():
    _subscribe()
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)
    bpy.app.timers.register(_initial, first_interval=0.0)


def unregister():
    bpy.msgbus.clear_by_owner(_OWNER)
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    for fn in (_deferred_setup, _initial):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    _state["pending"] = False
    _flipped.clear()
