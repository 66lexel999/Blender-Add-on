# Nature Forge - tiny node graph builder shared by the Geometry Nodes generators and the shader groups.
# Works on Blender 4.2 ... 5.x: node options that became menu sockets in 5.x (Resample Mode, Merge Mode ...) are set
# through opt(), sockets are looked up by identifier first and then by name among the ENABLED sockets (Mix, Random
# Value and Compare keep one hidden socket per data type).
import bpy

SOCK = {
    'GEO': 'NodeSocketGeometry', 'FLOAT': 'NodeSocketFloat', 'INT': 'NodeSocketInt', 'BOOL': 'NodeSocketBool',
    'VEC': 'NodeSocketVector', 'COLOR': 'NodeSocketColor', 'IMAGE': 'NodeSocketImage',
    'MAT': 'NodeSocketMaterial', 'OBJECT': 'NodeSocketObject', 'COLL': 'NodeSocketCollection',
    'SHADER': 'NodeSocketShader', 'STRING': 'NodeSocketString', 'ROT': 'NodeSocketRotation',
    'MENU': 'NodeSocketMenu',
}


def find_socket(coll, key):
    for s in coll:
        if s.identifier == key and s.enabled:
            return s
    for s in coll:
        if s.name == key and s.enabled:
            return s
    for s in coll:
        if s.identifier == key or s.name == key:
            return s
    raise KeyError(f"socket '{key}' not found in {[s.name for s in coll]}")


def first_out(node):
    for s in node.outputs:
        if s.enabled:
            return s
    return node.outputs[0]


def opt(node, prop, value, sock=None, label=None):
    """set a node option that is a property in 4.x and a menu socket in 5.x"""
    if hasattr(node, prop):
        try:
            setattr(node, prop, value)
            return
        except (TypeError, AttributeError):
            pass
    if sock is not None and sock in node.inputs:
        try:
            node.inputs[sock].default_value = label if label is not None else value
        except (TypeError, ValueError):
            pass


class Tree:
    """builds one node group; every helper returns the output socket(s) so graphs read like formulas"""

    def __init__(self, ng):
        self.ng = ng
        self.N = ng.nodes
        self.L = ng.links
        self.geo = ng.bl_idname == 'GeometryNodeTree'
        self.gin = self.N.new('NodeGroupInput')
        self.gout = self.N.new('NodeGroupOutput')
        self._panels = {}
        self._in = {}

    # ------------------------------------------------------------------ interface
    def panel(self, name, closed=True):
        p = self._panels.get(name)
        if p is None:
            p = self.ng.interface.new_panel(name, default_closed=closed)
            self._panels[name] = p
        return p

    def inp(self, name, kind, default=None, lo=None, hi=None, panel=None, desc='', subtype=None,
            hide_value=False, single=False):
        itf = self.ng.interface
        kw = {'in_out': 'INPUT', 'socket_type': SOCK[kind]}
        if panel:
            kw['parent'] = self.panel(panel)
        s = itf.new_socket(name, **kw)
        if desc:
            s.description = desc
        if subtype and hasattr(s, 'subtype'):
            try:
                s.subtype = subtype
            except TypeError:
                pass
        if lo is not None and hasattr(s, 'min_value'):
            s.min_value = lo
        if hi is not None and hasattr(s, 'max_value'):
            s.max_value = hi
        if default is not None and hasattr(s, 'default_value'):
            s.default_value = default
        if hide_value:
            s.hide_value = True
        if single and hasattr(s, 'force_non_field'):
            s.force_non_field = True
        sock = next(o for o in self.gin.outputs if o.identifier == s.identifier)
        self._in[name] = sock
        return sock

    def out(self, name, kind, src=None):
        s = self.ng.interface.new_socket(name, in_out='OUTPUT', socket_type=SOCK[kind])
        sock = next(i for i in self.gout.inputs if i.identifier == s.identifier)
        if src is not None:
            self.link(src, sock)
        return sock

    def __getitem__(self, name):
        return self._in[name]

    # ------------------------------------------------------------------ core
    def link(self, a, b):
        if isinstance(a, bpy.types.Node):
            a = first_out(a)
        self.L.new(a, b)

    def put(self, node, key, v):
        s = find_socket(node.inputs, key) if isinstance(key, str) else node.inputs[key]
        if isinstance(v, (bpy.types.NodeSocket, bpy.types.Node)):
            self.link(v, s)
        elif v is not None:
            s.default_value = v
        return s

    def node(self, idname, ins=None, **props):
        nd = self.N.new(idname)
        for k, v in props.items():
            setattr(nd, k, v)
        if ins:
            for k, v in (ins.items() if isinstance(ins, dict) else enumerate(ins)):
                self.put(nd, k, v)
        return nd

    def o(self, node, key=None):
        return first_out(node) if key is None else find_socket(node.outputs, key)

    # ------------------------------------------------------------------ math
    def math(self, op, a, b=None, c=None, clamp=False):
        nd = self.N.new('ShaderNodeMath')
        nd.operation = op
        nd.use_clamp = clamp
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.put(nd, i, v)
        return nd.outputs[0]

    def add(self, a, b):
        return self.math('ADD', a, b)

    def sub(self, a, b):
        return self.math('SUBTRACT', a, b)

    def mul(self, a, b):
        return self.math('MULTIPLY', a, b)

    def div(self, a, b):
        return self.math('DIVIDE', a, b)

    def madd(self, a, b, c):
        return self.math('MULTIPLY_ADD', a, b, c)

    def clamp01(self, a):
        return self.math('ADD', a, 0.0, clamp=True)

    def vmath(self, op, a, b=None, c=None, scale=None):
        nd = self.N.new('ShaderNodeVectorMath')
        nd.operation = op
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.put(nd, i, v)
        if scale is not None:
            self.put(nd, 'Scale', scale)
        if op in ('DOT_PRODUCT', 'LENGTH', 'DISTANCE'):
            return find_socket(nd.outputs, 'Value')
        return find_socket(nd.outputs, 'Vector')

    def vadd(self, a, b):
        return self.vmath('ADD', a, b)

    def vsub(self, a, b):
        return self.vmath('SUBTRACT', a, b)

    def vmul(self, a, b):
        return self.vmath('MULTIPLY', a, b)

    def vscale(self, a, s):
        return self.vmath('SCALE', a, scale=s)

    def dot(self, a, b):
        return self.vmath('DOT_PRODUCT', a, b)

    def norm(self, a):
        return self.vmath('NORMALIZE', a)

    def length(self, a):
        return self.vmath('LENGTH', a)

    def xyz(self, x=0.0, y=0.0, z=0.0):
        return self.node('ShaderNodeCombineXYZ', [x, y, z]).outputs[0]

    def sep(self, v):
        nd = self.node('ShaderNodeSeparateXYZ', [v])
        return nd.outputs[0], nd.outputs[1], nd.outputs[2]

    def mix(self, f, a, b, dtype='FLOAT', blend='MIX', clamp=True):
        nd = self.N.new('ShaderNodeMix')
        nd.data_type = dtype
        if dtype == 'RGBA':
            nd.blend_type = blend
        nd.clamp_factor = clamp
        self.put(nd, 'Factor', f)
        self.put(nd, 'A', a)
        self.put(nd, 'B', b)
        return self.o(nd, 'Result')

    def mrange(self, v, fmin=0.0, fmax=1.0, tmin=0.0, tmax=1.0, clamp=True, interp='LINEAR'):
        nd = self.N.new('ShaderNodeMapRange')
        nd.clamp = clamp
        nd.interpolation_type = interp
        for k, x in (('Value', v), ('From Min', fmin), ('From Max', fmax), ('To Min', tmin), ('To Max', tmax)):
            self.put(nd, k, x)
        return nd.outputs['Result']

    def smooth(self, v, e0, e1):
        """smoothstep(e0, e1, v)"""
        return self.mrange(v, e0, e1, 0.0, 1.0, True, 'SMOOTHSTEP')

    # ------------------------------------------------------------------ textures
    def noise(self, vec, scale=1.0, detail=2.0, rough=0.5, lac=2.0, distortion=0.0, ntype='FBM', dims='3D',
              w=None, normalize=True):
        nd = self.N.new('ShaderNodeTexNoise')
        nd.noise_dimensions = dims
        if hasattr(nd, 'noise_type'):
            nd.noise_type = ntype
        nd.normalize = normalize
        for k, x in (('Vector', vec), ('Scale', scale), ('Detail', detail), ('Roughness', rough),
                     ('Lacunarity', lac), ('Distortion', distortion), ('W', w)):
            if x is not None:
                self.put(nd, k, x)
        return find_socket(nd.outputs, 'Fac'), nd.outputs['Color']

    def voronoi(self, vec, scale=1.0, feature='F1', rand=1.0, dims='3D', detail=0.0, w=None, metric='EUCLIDEAN'):
        nd = self.N.new('ShaderNodeTexVoronoi')
        nd.voronoi_dimensions = dims
        nd.feature = feature
        nd.distance = metric
        for k, x in (('Vector', vec), ('Scale', scale), ('Randomness', rand), ('Detail', detail), ('W', w)):
            if x is not None:
                self.put(nd, k, x)
        return nd

    # ------------------------------------------------------------------ geometry nodes
    def pos(self):
        return self.node('GeometryNodeInputPosition').outputs[0]

    def normal(self):
        return self.node('GeometryNodeInputNormal').outputs[0]

    def index(self):
        return self.node('GeometryNodeInputIndex').outputs[0]

    def named(self, name, dtype='FLOAT'):
        nd = self.node('GeometryNodeInputNamedAttribute', {'Name': name}, data_type=dtype)
        return self.o(nd, 'Attribute')

    def store(self, geo, name, value, dtype='FLOAT', domain='POINT', sel=None):
        nd = self.node('GeometryNodeStoreNamedAttribute', data_type=dtype, domain=domain)
        self.put(nd, 'Geometry', geo)
        self.put(nd, 'Name', name)
        self.put(nd, 'Value', value)
        if sel is not None:
            self.put(nd, 'Selection', sel)
        return nd.outputs[0]

    def setpos(self, geo, position=None, offset=None, sel=None):
        nd = self.node('GeometryNodeSetPosition', {'Geometry': geo})
        if position is not None:
            self.put(nd, 'Position', position)
        if offset is not None:
            self.put(nd, 'Offset', offset)
        if sel is not None:
            self.put(nd, 'Selection', sel)
        return nd.outputs[0]

    def rand(self, lo, hi, dtype='FLOAT', id=None, seed=None, prob=None):
        nd = self.node('FunctionNodeRandomValue', data_type=dtype)
        if dtype == 'BOOLEAN':
            self.put(nd, 'Probability', prob if prob is not None else 0.5)
        else:
            self.put(nd, 'Min', lo)
            self.put(nd, 'Max', hi)
        if id is not None:
            if not isinstance(id, (bpy.types.NodeSocket, bpy.types.Node)):
                id = self.const_int(int(id))     # an unlinked ID socket is the implicit per-element index
            self.put(nd, 'ID', id)
        if seed is not None:
            self.put(nd, 'Seed', seed)
        return self.o(nd, 'Value')

    def compare_gt(self, a, b):
        """1.0 / 0.0 float (works in shader trees too)"""
        return self.math('GREATER_THAN', a, b)

    def const_int(self, v):
        nd = self.N.new('FunctionNodeInputInt')
        nd.integer = v
        return nd.outputs[0]

    def switch(self, dtype, cond, false, true):
        nd = self.node('GeometryNodeSwitch', input_type=dtype)
        self.put(nd, 'Switch', cond)
        self.put(nd, 'False', false)
        self.put(nd, 'True', true)
        return nd.outputs[0]

    def compare(self, op, a, b, dtype='FLOAT'):
        nd = self.node('FunctionNodeCompare', data_type=dtype, operation=op)
        self.put(nd, 'A', a)
        self.put(nd, 'B', b)
        return nd.outputs[0]

    def bool_op(self, op, a, b=None):
        nd = self.node('FunctionNodeBooleanMath', operation=op)
        self.put(nd, 0, a)
        if b is not None:
            self.put(nd, 1, b)
        return nd.outputs[0]

    def blur(self, value, iters, dtype='FLOAT_VECTOR', weight=None):
        nd = self.node('GeometryNodeBlurAttribute', data_type=dtype)
        self.put(nd, 'Value', value)
        self.put(nd, 'Iterations', iters)
        if weight is not None:
            self.put(nd, 'Weight', weight)
        return nd.outputs[0]

    def stat(self, geo, value, dtype='FLOAT', domain='POINT'):
        nd = self.node('GeometryNodeAttributeStatistic', data_type=dtype, domain=domain)
        self.put(nd, 'Geometry', geo)
        self.put(nd, 'Attribute', value)
        return nd

    def capture(self, geo, value, dtype='FLOAT', domain='POINT'):
        """-> (geometry, captured field) ; 4.2+ capture items API"""
        nd = self.N.new('GeometryNodeCaptureAttribute')
        nd.domain = domain
        if hasattr(nd, 'capture_items'):
            ctype = {'FLOAT_VECTOR': 'VECTOR', 'FLOAT_COLOR': 'RGBA'}.get(dtype, dtype)
            nd.capture_items.new(ctype, 'Value')
            self.put(nd, 'Geometry', geo)
            self.link(value, nd.inputs[1])
            return nd.outputs[0], nd.outputs[1]
        nd.data_type = dtype
        self.put(nd, 'Geometry', geo)
        self.put(nd, 'Value', value)
        return nd.outputs[0], self.o(nd, 'Attribute')

    def group(self, ng, ins=None):
        nd = self.N.new('GeometryNodeGroup' if self.geo else 'ShaderNodeGroup')
        nd.node_tree = ng
        if ins:
            for k, v in ins.items():
                self.put(nd, k, v)
        return nd

    def join(self, *geos):
        nd = self.N.new('GeometryNodeJoinGeometry')
        for g in reversed(geos):              # the multi-input socket stacks the newest link first
            if g is not None:
                self.link(g, nd.inputs[0])
        return nd.outputs[0]

    def repeat(self, iterations, items):
        """-> (rin, rout, step) ; items = [(socket_type, name, initial)] - the first item is the Geometry.
        step = iteration counter (an extra INT item: 4.2 has no 'Iteration' output yet); it is incremented here"""
        rin = self.N.new('GeometryNodeRepeatInput')
        rout = self.N.new('GeometryNodeRepeatOutput')
        rin.pair_with_output(rout)
        ri = rout.repeat_items
        for i, (stype, name, init) in enumerate(items):
            if i == 0 and stype == 'GEOMETRY' and len(ri) and ri[0].socket_type == 'GEOMETRY':
                ri[0].name = name
            else:
                ri.new(stype, name)
        ri.new('INT', 'Step')
        self.put(rin, 'Iterations', iterations)
        for i, (stype, name, init) in enumerate(items):
            if init is not None:
                self.put(rin, name, init)
        step = find_socket(rin.outputs, 'Step')
        self.link(self.math('ADD', step, 1.0), find_socket(rout.inputs, 'Step'))
        return rin, rout, step

    # ------------------------------------------------------------------ layout
    def layout(self, dx=220, dy=-170):
        """arrange nodes in columns by their distance from the group input (readable when someone opens it)"""
        nodes = list(self.N)
        depth = {n: 0 for n in nodes}
        incoming = {n: [] for n in nodes}
        for l in self.L:
            incoming[l.to_node].append(l.from_node)
        for _ in range(len(nodes)):
            changed = False
            for n in nodes:
                d = max((depth[m] + 1 for m in incoming[n]), default=0)
                if d != depth[n] and d < len(nodes):
                    depth[n] = d
                    changed = True
            if not changed:
                break
        cols = {}
        for n in nodes:
            if n.bl_idname == 'NodeGroupOutput':
                continue
            cols.setdefault(depth[n], []).append(n)
        last = max(cols) + 1 if cols else 1
        depth[self.gout] = last
        cols[last] = [self.gout]
        for d, ns in cols.items():
            for i, n in enumerate(ns):
                n.location = (d * dx, i * dy)


def new_group(name, kind='GeometryNodeTree', version=None, force=False):
    """-> (node group, Tree builder) or (node group, None) when an up-to-date version exists"""
    ng = bpy.data.node_groups.get(name)
    if ng is not None and not force and version is not None and ng.get('nf_ver') == version:
        return ng, None
    if ng is None:
        ng = bpy.data.node_groups.new(name, kind)
    ng.nodes.clear()
    ng.interface.clear()
    if version is not None:
        ng['nf_ver'] = version
    return ng, Tree(ng)
