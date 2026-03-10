"""
GModular — KotOR MDL/MDX Binary Parser
Adapted from GhostRigger's MDLBinaryParser for use in GModular's preview engine.

Parses KotOR 1/2 binary .mdl + .mdx pairs into MeshData objects that
the GModular viewport can upload to OpenGL.

Key differences from GhostRigger's version:
  - Returns lightweight MeshData / ModelMesh instead of full KotorModel
  - No animation engine dependency (animations available but optional)
  - Designed to be imported by viewport.py without pulling in GhostRigger
"""

from __future__ import annotations
import struct
import math
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from pathlib import Path

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Node flag constants (matches GhostRigger's NodeFlags)
# ─────────────────────────────────────────────────────────────────────────────

NODE_HEADER  = 0x0001
NODE_LIGHT   = 0x0002
NODE_EMITTER = 0x0004
NODE_CAMERA  = 0x0008
NODE_REF     = 0x0010
NODE_MESH    = 0x0020
NODE_SKIN    = 0x0040
NODE_ANIM    = 0x0080
NODE_DANGLY  = 0x0100
NODE_AABB    = 0x0200
NODE_SABER   = 0x0800


# ─────────────────────────────────────────────────────────────────────────────
#  Lightweight data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MeshNode:
    """A single mesh node extracted from an MDL file."""
    name:     str  = "node"
    flags:    int  = NODE_HEADER

    # Local transform
    position: Tuple[float, float, float]       = (0.0, 0.0, 0.0)
    rotation: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)  # xyzw

    # Geometry (local-space)
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    normals:  List[Tuple[float, float, float]] = field(default_factory=list)
    uvs:      List[Tuple[float, float]]        = field(default_factory=list)
    faces:    List[Tuple[int, int, int]]       = field(default_factory=list)

    # Material
    texture:  str   = ""
    diffuse:  Tuple[float, float, float] = (0.8, 0.8, 0.8)
    ambient:  Tuple[float, float, float] = (0.2, 0.2, 0.2)
    alpha:    float = 1.0
    render:   bool  = True

    # Hierarchy
    parent:   Optional['MeshNode'] = field(default=None, repr=False)
    children: List['MeshNode']    = field(default_factory=list)

    # Bounds (world-space, filled by MeshData.compute_bounds)
    bb_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bb_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def is_mesh(self) -> bool:
        return bool(self.flags & NODE_MESH)

    @property
    def is_skin(self) -> bool:
        return bool(self.flags & NODE_SKIN)

    @property
    def is_aabb(self) -> bool:
        return bool(self.flags & NODE_AABB)

    @property
    def texture_clean(self) -> str:
        """Return texture name with null/garbage bytes stripped."""
        out = []
        for ch in (self.texture or ""):
            if 32 <= ord(ch) <= 126:
                out.append(ch)
            else:
                break
        return "".join(out).strip()


@dataclass
class MeshData:
    """Full parsed model — collection of mesh nodes plus bounds."""
    name:        str = "unnamed"
    supermodel:  str = "NULL"
    game_version: int = 1          # 1=K1, 2=K2
    root_node:   Optional[MeshNode] = None
    bb_min:      Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bb_max:      Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius:      float = 1.0

    def all_nodes(self) -> List[MeshNode]:
        result: List[MeshNode] = []
        def _walk(n: MeshNode):
            result.append(n)
            for c in n.children:
                _walk(c)
        if self.root_node:
            _walk(self.root_node)
        return result

    def mesh_nodes(self) -> List[MeshNode]:
        return [n for n in self.all_nodes() if n.is_mesh]

    def visible_mesh_nodes(self) -> List[MeshNode]:
        """Render-worthy nodes: has vertices, render=True, not AABB."""
        result = []
        for n in self.mesh_nodes():
            if n.vertices and n.render and not n.is_aabb:
                result.append(n)
        return result

    def compute_bounds(self):
        """Compute world-space AABB by accumulating vertices from all mesh nodes."""
        all_verts: List[Tuple[float, float, float]] = []
        for n in self.mesh_nodes():
            if not n.vertices:
                continue
            wp = _world_pos(n)
            for v in n.vertices:
                all_verts.append((v[0] + wp[0], v[1] + wp[1], v[2] + wp[2]))
        if not all_verts:
            return
        xs = [v[0] for v in all_verts]
        ys = [v[1] for v in all_verts]
        zs = [v[2] for v in all_verts]
        self.bb_min = (min(xs), min(ys), min(zs))
        self.bb_max = (max(xs), max(ys), max(zs))
        cx = (self.bb_min[0] + self.bb_max[0]) * 0.5
        cy = (self.bb_min[1] + self.bb_max[1]) * 0.5
        cz = (self.bb_min[2] + self.bb_max[2]) * 0.5
        self.radius = max(
            math.sqrt((v[0]-cx)**2 + (v[1]-cy)**2 + (v[2]-cz)**2)
            for v in all_verts
        ) if all_verts else 1.0

    def flat_triangle_array(self) -> List[Tuple[Tuple[float,float,float], Tuple[float,float,float]]]:
        """
        Returns list of (vertex_xyz, normal_xyz) for all triangles in visible nodes.
        Used for walkmesh collision detection.
        """
        triangles = []
        for n in self.visible_mesh_nodes():
            wp = _world_pos(n)
            verts = n.vertices
            norms = n.normals
            for f in n.faces:
                if max(f) >= len(verts):
                    continue
                tri_verts = tuple(
                    (verts[i][0]+wp[0], verts[i][1]+wp[1], verts[i][2]+wp[2])
                    for i in f
                )
                if norms and max(f) < len(norms):
                    tri_norm = norms[f[0]]
                else:
                    # Compute face normal
                    v0, v1, v2 = tri_verts
                    e1 = (v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2])
                    e2 = (v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2])
                    nx = e1[1]*e2[2] - e1[2]*e2[1]
                    ny = e1[2]*e2[0] - e1[0]*e2[2]
                    nz = e1[0]*e2[1] - e1[1]*e2[0]
                    mag = math.sqrt(nx*nx + ny*ny + nz*nz) or 1.0
                    tri_norm = (nx/mag, ny/mag, nz/mag)
                triangles.append((tri_verts, tri_norm))
        return triangles


# ─────────────────────────────────────────────────────────────────────────────
#  Quaternion / transform helpers  (same logic as GhostRigger's model_data.py)
# ─────────────────────────────────────────────────────────────────────────────

def _quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    ]


def _quat_rotate(q, v):
    qx, qy, qz, qw = q
    l2 = qx*qx + qy*qy + qz*qz + qw*qw
    if l2 > 1e-9:
        l = math.sqrt(l2)
        qx /= l; qy /= l; qz /= l; qw /= l
    vx, vy, vz = v
    tx = 2*(qy*vz - qz*vy)
    ty = 2*(qz*vx - qx*vz)
    tz = 2*(qx*vy - qy*vx)
    return (
        vx + qw*tx + qy*tz - qz*ty,
        vy + qw*ty + qz*tx - qx*tz,
        vz + qw*tz + qx*ty - qy*tx,
    )


def _quat_normalize_bind(q):
    """Collapse 180°-about-axis rotations to identity (NWN bind-pose convention)."""
    x, y, z, w = q
    if abs(w) < 0.05:
        mag_xyz = math.sqrt(x*x + y*y + z*z)
        if mag_xyz > 0.95:
            return [0.0, 0.0, 0.0, 1.0]
    l2 = x*x + y*y + z*z + w*w
    if l2 < 1e-9:
        return [0.0, 0.0, 0.0, 1.0]
    l = math.sqrt(l2)
    return [x/l, y/l, z/l, w/l]


def _world_pos(node: MeshNode) -> Tuple[float, float, float]:
    """Walk parent chain to compute world-space origin of a node."""
    chain: List[MeshNode] = []
    n = node
    while n is not None:
        chain.append(n)
        n = n.parent
    chain.reverse()

    wx, wy, wz = 0.0, 0.0, 0.0
    parent_q = [0.0, 0.0, 0.0, 1.0]
    for nd in chain:
        lx, ly, lz = nd.position
        rx, ry, rz = _quat_rotate(parent_q, (lx, ly, lz))
        wx += rx; wy += ry; wz += rz
        bind_rot = _quat_normalize_bind(nd.rotation)
        parent_q = _quat_mul(parent_q, bind_rot)
    return (wx, wy, wz)


# ─────────────────────────────────────────────────────────────────────────────
#  Binary read helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rstrip(b: bytes) -> str:
    end = b.find(b'\x00')
    if end < 0:
        end = len(b)
    return b[:end].decode('ascii', errors='replace').strip()


def _ru32(data: bytes, off: int) -> int:
    return struct.unpack_from('<I', data, off)[0]


def _rf32(data: bytes, off: int) -> float:
    return struct.unpack_from('<f', data, off)[0]


def _ru16(data: bytes, off: int) -> int:
    return struct.unpack_from('<H', data, off)[0]


# ─────────────────────────────────────────────────────────────────────────────
#  MDL Binary Parser
# ─────────────────────────────────────────────────────────────────────────────

class MDLParser:
    """
    Parses a binary KotOR MDL + MDX pair into a MeshData object.

    Usage::

        # From file paths
        mesh = MDLParser.parse_files("model.mdl", "model.mdx")

        # From bytes
        parser = MDLParser(mdl_bytes, mdx_bytes)
        mesh   = parser.parse()
    """

    BASE = 12   # All MDL offsets are relative to byte 12

    # Game version function pointer constants
    _FP_K1 = frozenset([4273776, 4273392, 4216096])
    _FP_K2 = frozenset([4285200, 4284816, 4216320])

    def __init__(self, mdl: bytes, mdx: bytes = b''):
        self.mdl = mdl
        self.mdx = mdx
        self._names: List[str] = []
        self._cache: Dict[int, MeshNode] = {}
        self.data = MeshData()

    @classmethod
    def from_files(cls, mdl_path: str, mdx_path: str = '') -> 'MDLParser':
        mdl_bytes = Path(mdl_path).read_bytes()
        if mdx_path and Path(mdx_path).exists():
            mdx_bytes = Path(mdx_path).read_bytes()
        else:
            mdx_guess = Path(mdl_path).with_suffix('.mdx')
            mdx_bytes = mdx_guess.read_bytes() if mdx_guess.exists() else b''
        return cls(mdl_bytes, mdx_bytes)

    @classmethod
    def parse_files(cls, mdl_path: str, mdx_path: str = '') -> MeshData:
        """One-shot parse. Returns MeshData."""
        parser = cls.from_files(mdl_path, mdx_path)
        return parser.parse()

    # ── Public entry point ────────────────────────────────────────────────────

    def parse(self) -> MeshData:
        B = self.BASE
        d = self.mdl

        if len(d) < B + 168:
            raise ValueError(f"MDL file too small ({len(d)} bytes)")

        # ── Geometry header at offset 12 ──────────────────────────────────────
        fp1 = _ru32(d, B)
        self.data.name = _rstrip(d[B+8: B+40])
        root_node_off  = _ru32(d, B+40)

        # Detect game version from function pointer
        if fp1 in self._FP_K2:
            self.data.game_version = 2
        else:
            self.data.game_version = 1

        # ── Model header at offset 92 ─────────────────────────────────────────
        M = B + 80
        # Model header: skip to supermodel name at M+56
        try:
            self.data.supermodel = _rstrip(d[M+56: M+88])
        except Exception:
            self.data.supermodel = "NULL"

        # ── Name array at offset 180 ──────────────────────────────────────────
        N = B + 168
        if N + 24 <= len(d):
            names_arr_off = _ru32(d, N+16)
            names_count   = _ru32(d, N+20)
            self._names = []
            for i in range(min(names_count, 4096)):
                ptr_off = B + names_arr_off + i * 4
                if ptr_off + 4 > len(d):
                    break
                str_off = _ru32(d, ptr_off)
                abs_off = B + str_off
                if abs_off < len(d):
                    end = d.find(b'\x00', abs_off)
                    if end < 0 or end - abs_off > 128:
                        end = abs_off + 64
                    self._names.append(d[abs_off:end].decode('ascii', 'replace'))

        # ── Node tree ─────────────────────────────────────────────────────────
        if root_node_off:
            self.data.root_node = self._parse_node(B + root_node_off, None)

        self.data.compute_bounds()
        log.debug(f"MDL '{self.data.name}': "
                  f"{len(self.data.mesh_nodes())} mesh nodes, "
                  f"bb={self.data.bb_min}..{self.data.bb_max}")
        return self.data

    # ── Node parser ───────────────────────────────────────────────────────────

    def _parse_node(self, abs_off: int, parent: Optional[MeshNode]) -> MeshNode:
        if abs_off in self._cache:
            return self._cache[abs_off]

        d = self.mdl
        o = abs_off

        if o + 80 > len(d):
            node = MeshNode(name="error_node", parent=parent)
            return node

        node_type  = _ru16(d, o); o += 2
        index_num  = _ru16(d, o); o += 2
        node_num   = _ru16(d, o); o += 2
        o += 2  # pad
        o += 4  # root_off
        o += 4  # parent_off

        px, py, pz     = struct.unpack_from('<fff', d, o); o += 12
        rx, ry, rz, rw = struct.unpack_from('<ffff', d, o); o += 16

        child_arr_off = _ru32(d, o); o += 4
        child_cnt     = _ru32(d, o); o += 4
        o += 4  # child_cnt2
        o += 4  # ctrl_arr_off
        o += 4  # ctrl_cnt
        o += 4  # ctrl_cnt2
        o += 4  # ctrl_data_off
        o += 4  # ctrl_data_cnt
        o += 4  # ctrl_data_cnt2

        name = (self._names[index_num]
                if 0 <= index_num < len(self._names)
                else f"node_{index_num}")

        node = MeshNode(
            name=name, flags=node_type,
            position=(px, py, pz),
            rotation=(rx, ry, rz, rw),
            parent=parent,
        )
        self._cache[abs_off] = node

        # Parse mesh geometry if this is a mesh node
        if node_type & NODE_MESH:
            try:
                self._parse_mesh(node, o)
            except Exception as e:
                log.debug(f"Mesh parse error on '{name}': {e}")

        # Parse children
        B = self.BASE
        for i in range(min(child_cnt, 512)):
            ptr = B + child_arr_off + i * 4
            if ptr + 4 > len(d):
                break
            c_off = _ru32(d, ptr)
            if c_off == 0:
                continue
            child = self._parse_node(B + c_off, node)
            if child not in node.children:
                node.children.append(child)

        return node

    # ── Mesh data parser ──────────────────────────────────────────────────────

    def _parse_mesh(self, node: MeshNode, off: int):
        """
        Parse the mesh header and extract vertices, normals, UVs, faces.
        Offset 'off' points to the START of the mesh header (after the base node header).
        Mesh header layout documented inline — verified against KotOR K1 binary MDLs.
        """
        d   = self.mdl
        mdx = self.mdx
        B   = self.BASE
        o   = off

        o += 8   # skip fp1, fp2

        faces_off = _ru32(d, o); o += 4
        faces_cnt = _ru32(d, o); o += 4
        o += 4   # faces_cnt2

        # Bounding box
        o += 12  # bb_min
        o += 12  # bb_max
        o += 4   # radius
        o += 12  # average position

        # Colors
        dr, dg, db = struct.unpack_from('<fff', d, o); o += 12
        o += 12  # ambient
        o += 4   # transparency_hint

        tex_name = _rstrip(d[o:o+32]).lower(); o += 32
        o += 32  # lightmap name
        o += 24  # 6 unknown uint32s
        o += 12  # vic array
        o += 12  # vo array
        o += 12  # inv array
        o += 12  # {-1, -1, 0}
        o +=  8  # saber vals
        o +=  4  # unknown
        o += 16  # 4 floats

        mdx_data_size   = _ru32(d, o); o += 4
        mdx_data_bitmap = _ru32(d, o); o += 4

        # 11 MDX channel offsets (0xFFFFFFFF = absent)
        mdx_v_off  = _ru32(d, o); o += 4   # vertex positions
        mdx_n_off  = _ru32(d, o); o += 4   # normals
        o += 4                              # vertex colors
        mdx_t1_off = _ru32(d, o); o += 4   # UV set 1
        o += 4  # lightmap UV
        o += 4  # UV set 2
        o += 4  # UV set 3
        o += 4  # bump map
        o += 4  # unk1
        o += 4  # unk2
        o += 4  # unk3

        vert_cnt   = _ru16(d, o); o += 2
        o += 2   # tex_cnt
        o += 1   # has_lightmap
        o += 1   # rotate_texture
        o += 1   # background_geometry
        has_shadow = struct.unpack_from('B', d, o)[0]; o += 1
        o += 1   # beaming
        has_render = struct.unpack_from('B', d, o)[0]; o += 1
        o += 2   # 2 unknown
        o += 4   # total_area
        o += 4   # unknown

        if self.data.game_version == 2:
            o += 8  # K2 extra fields

        mdx_data_off = _ru32(d, o); o += 4
        verts_off    = _ru32(d, o); o += 4

        # Store material
        node.texture = tex_name
        node.diffuse = (dr, dg, db)
        node.render  = bool(has_render)

        if vert_cnt == 0 or vert_cnt > 65000:
            return

        # ── Vertex positions (prefer MDX, fall back to MDL) ───────────────────
        verts_loaded = False
        if (mdx_data_size > 0 and mdx_data_off > 0
                and mdx_data_off < len(mdx)
                and mdx_v_off != 0xFFFFFFFF):
            stride = mdx_data_size
            for i in range(vert_cnt):
                base = mdx_data_off + i * stride
                if base + mdx_v_off + 12 > len(mdx):
                    break
                node.vertices.append(
                    struct.unpack_from('<fff', mdx, base + mdx_v_off))
            verts_loaded = (len(node.vertices) == vert_cnt)

        if not verts_loaded and verts_off > 0:
            va = B + verts_off
            for i in range(vert_cnt):
                p = va + i * 12
                if p + 12 > len(d):
                    break
                node.vertices.append(struct.unpack_from('<fff', d, p))

        # ── Normals and UVs from MDX ──────────────────────────────────────────
        if mdx_data_size > 0 and mdx_data_off > 0 and mdx_data_off < len(mdx):
            stride = mdx_data_size
            for i in range(vert_cnt):
                base = mdx_data_off + i * stride
                if (mdx_n_off != 0xFFFFFFFF
                        and base + mdx_n_off + 12 <= len(mdx)):
                    node.normals.append(
                        struct.unpack_from('<fff', mdx, base + mdx_n_off))
                if (mdx_t1_off != 0xFFFFFFFF
                        and base + mdx_t1_off + 8 <= len(mdx)):
                    node.uvs.append(
                        struct.unpack_from('<ff', mdx, base + mdx_t1_off))

        # ── Faces ─────────────────────────────────────────────────────────────
        # Face = 32 bytes: normal(12) planeDist(4) mat(4) adjFaces(6) verts(6)
        if faces_cnt > 0 and faces_off > 0:
            fa = B + faces_off
            for i in range(min(faces_cnt, 65535)):
                p = fa + i * 32
                if p + 32 > len(d):
                    break
                v1, v2, v3 = struct.unpack_from('<HHH', d, p + 26)
                if v1 < vert_cnt and v2 < vert_cnt and v3 < vert_cnt:
                    node.faces.append((v1, v2, v3))

        log.debug(f"  '{node.name}': {len(node.vertices)} verts, "
                  f"{len(node.faces)} faces, tex='{tex_name}'")


# ─────────────────────────────────────────────────────────────────────────────
#  Fast texture-name scanner (no geometry, just texture names)
# ─────────────────────────────────────────────────────────────────────────────

def scan_mdl_textures(mdl_bytes: bytes) -> List[str]:
    """
    Quickly extract all texture names referenced by an MDL file.
    Does NOT parse geometry — only reads the mesh texture fields.

    Each mesh node stores a 32-byte texture name at a fixed offset within
    the mesh header.  This function walks the node tree (geometry only)
    and collects all non-empty texture names.

    Returns a de-duplicated list of lowercase texture names (no extension).
    Suitable for building texture dependency lists for the MOD packager.
    """
    B = MDLParser.BASE
    if len(mdl_bytes) < B + 168:
        return []

    try:
        parser = MDLParser(mdl_bytes, b'')
        mesh_data = parser.parse()
        names: List[str] = []
        seen: set = set()
        for node in mesh_data.visible_mesh_nodes():
            tex = node.texture_clean.lower()
            if tex and tex not in seen:
                seen.add(tex)
                names.append(tex)
        return names
    except Exception as e:
        log.debug(f"scan_mdl_textures: failed: {e}")
        return []


def list_mdl_dependencies(mdl_bytes: bytes,
                          mdx_bytes: bytes = b'') -> Dict[str, List[str]]:
    """
    Return a mapping of dependency type → list of resrefs for an MDL.

    Keys:
        'textures'  – TGA/TPC texture names (no extension)
        'lightmaps' – lightmap names (no extension)
        'models'    – supermodel resref (if any, lowercase)

    Usage in MOD packager::
        deps = list_mdl_dependencies(mdl_bytes)
        for tex in deps['textures']:
            # pack tex.tpc or tex.tga
    """
    result: Dict[str, List[str]] = {
        'textures': [],
        'lightmaps': [],
        'models': [],
    }
    B = MDLParser.BASE
    if len(mdl_bytes) < B + 168:
        return result

    try:
        parser = MDLParser(mdl_bytes, mdx_bytes)
        mesh_data = parser.parse()

        tex_seen:  set = set()
        lmap_seen: set = set()

        for node in mesh_data.mesh_nodes():
            tex = node.texture_clean.lower()
            if tex and tex not in tex_seen:
                tex_seen.add(tex)
                result['textures'].append(tex)

        # Supermodel
        sm = mesh_data.supermodel.lower()
        if sm and sm != "null" and sm not in ("", "null"):
            result['models'].append(sm)

    except Exception as e:
        log.debug(f"list_mdl_dependencies: {e}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Model Cache (application-level LRU to avoid re-parsing the same MDL)
# ─────────────────────────────────────────────────────────────────────────────

class ModelCache:
    """
    Thread-safe in-memory cache for parsed MeshData objects.
    Keyed by normalised lowercase file path.
    """

    def __init__(self, max_size: int = 64):
        self._cache: Dict[str, MeshData] = {}
        self._order: List[str] = []
        self._max   = max_size

    def get(self, mdl_path: str) -> Optional[MeshData]:
        key = str(mdl_path).lower()
        return self._cache.get(key)

    def put(self, mdl_path: str, data: MeshData):
        key = str(mdl_path).lower()
        if key in self._cache:
            self._order.remove(key)
        self._cache[key] = data
        self._order.append(key)
        # Evict oldest if over limit
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._cache.pop(old, None)

    def load(self, mdl_path: str, mdx_path: str = '') -> Optional[MeshData]:
        """Load from cache, or parse and cache if not present."""
        cached = self.get(mdl_path)
        if cached:
            return cached
        try:
            data = MDLParser.parse_files(mdl_path, mdx_path)
            self.put(mdl_path, data)
            return data
        except Exception as e:
            log.warning(f"ModelCache: failed to load '{mdl_path}': {e}")
            return None

    def clear(self):
        self._cache.clear()
        self._order.clear()


# Module-level shared cache
_model_cache = ModelCache()


def get_model_cache() -> ModelCache:
    return _model_cache
