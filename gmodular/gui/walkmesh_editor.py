"""
GModular — Walkmesh Editor (WOK)
Parses and displays KotOR .WOK walkmesh files.
Provides tools to:
  - Visualize walkable faces in the viewport (overlaid on grid)
  - Display walk-surface types (walk, non-walk, trigger, grass, etc.)
  - Export a simplified override .WOK

Reference: reone src/game/area/wok.cpp
           xoreos src/engines/aurora/walkmesh.cpp
"""
from __future__ import annotations
import struct
import logging
import math
from typing import List, Tuple, Optional, Dict
from pathlib import Path
from dataclasses import dataclass, field

# Shared MDL/WOK header reader — avoids duplicating struct offsets
try:
    from ..formats.mdl_parser import read_mdl_base_header as _read_mdl_header
    _HAS_MDL_HELPER = True
except ImportError:
    _HAS_MDL_HELPER = False
    _read_mdl_header = None

try:
    from qtpy.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QPushButton, QTableWidget, QTableWidgetItem, QFrame,
        QGroupBox, QFormLayout, QComboBox, QFileDialog,
        QScrollArea, QSplitter, QAbstractItemView, QCheckBox,
        QDoubleSpinBox, QMessageBox,
    )
    from qtpy.QtCore import Qt, Signal
    from qtpy.QtGui import QFont, QColor
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QWidget = object  # type: ignore[misc,assignment]
    class Signal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def __set_name__(self, owner, name): pass

log = logging.getLogger(__name__)


# ── Walk Surface Type Constants ───────────────────────────────────────────────

WALK_TYPE_NAMES: Dict[int, str] = {
    0:  "Non-Walk",
    1:  "Walk",
    2:  "Dirt",
    3:  "Grass",
    4:  "Stone",
    5:  "Wood",
    6:  "Water",
    7:  "Non-Walk (Trigger)",
    8:  "Trigger",
    9:  "Shallow Water",
    10: "Carpet",
    11: "Metal",
    12: "Puddles",
    13: "Sand",
    14: "Ice",
    15: "Snow",
    16: "Quicksand",
    17: "Lava",
    18: "Hot Ground",
    19: "Grass (Tall)",
}

WALK_TYPE_COLORS: Dict[int, str] = {
    0:  "#ff4444",   # non-walk: red
    1:  "#44ff44",   # walk: green
    2:  "#aa6622",   # dirt: brown
    3:  "#22aa44",   # grass: dark green
    4:  "#aaaaaa",   # stone: grey
    5:  "#886622",   # wood: tan-brown
    6:  "#2244ff",   # water: blue
    7:  "#ff8844",   # non-walk trigger: orange
    8:  "#ffaa00",   # trigger boundary: gold
    9:  "#44aaff",   # shallow water: light blue
    10: "#cc88cc",   # carpet: purple
    11: "#888888",   # metal: silver-grey
    12: "#336699",   # puddles: dark blue
    13: "#ddcc66",   # sand: yellow
    14: "#aaddff",   # ice: pale cyan
    15: "#eeeeff",   # snow: near-white
    16: "#cc8800",   # quicksand: dark amber
    17: "#ff2200",   # lava: bright red-orange
    18: "#ff6600",   # hot ground: orange
    19: "#66cc44",   # tall grass: bright green
}


# ── WOK Binary Parser ─────────────────────────────────────────────────────────

@dataclass
class WOKFace:
    v0: Tuple[float, float, float] = (0, 0, 0)
    v1: Tuple[float, float, float] = (0, 0, 0)
    v2: Tuple[float, float, float] = (0, 0, 0)
    walk_type: int = 0
    material: int = 0

    @property
    def center(self) -> Tuple[float, float, float]:
        return (
            (self.v0[0] + self.v1[0] + self.v2[0]) / 3,
            (self.v0[1] + self.v1[1] + self.v2[1]) / 3,
            (self.v0[2] + self.v1[2] + self.v2[2]) / 3,
        )

    @property
    def is_walkable(self) -> bool:
        # 0=Non-Walk, 6=Water (swim), 7=Trigger (walk-through), 8=Trigger (non-walk)
        # 16=Quicksand, 17=Lava, 18=Hot Ground — passable terrain types in KotOR
        return self.walk_type in (1, 2, 3, 4, 5, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19)


@dataclass
class WOKData:
    model_name: str = ""
    walk_type: int = 0
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    faces: List[WOKFace] = field(default_factory=list)
    aabb_min: Tuple[float, float, float] = (0, 0, 0)
    aabb_max: Tuple[float, float, float] = (0, 0, 0)

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def walkable_face_count(self) -> int:
        return sum(1 for f in self.faces if f.is_walkable)

    @property
    def bounds_size(self) -> Tuple[float, float, float]:
        return (
            self.aabb_max[0] - self.aabb_min[0],
            self.aabb_max[1] - self.aabb_min[1],
            self.aabb_max[2] - self.aabb_min[2],
        )


class WOKParser:
    """
    Parses KotOR .WOK binary files into WOKData.

    WOK files share the KotOR binary MDL format — they are MDL models whose
    mesh nodes carry walk-type per-face data instead of texture/material data.

    Binary layout reference (verified against xoreos source):
      File header (12 bytes):
        +0   uint32  reserved (0)
        +4   uint32  model-data section offset (absolute from file start)
        +8   uint32  model-data section size

      Model-data header (at mdl_data_off):
        +0   char[32]  model name
        +32  char[64]  file dependency
        +96  uint32    unknown
        +100 uint8     classification
        +101 uint8     subclassification
        +102 uint8     unknown
        +103 uint8     affected by fog
        +104 uint32    child model count
        +108 uint32[2] animation pointer array
        +116 uint32    parent model pointer (0 = no parent)
        +120 float     bounding box min X
        +124 float     bounding box min Y
        +128 float     bounding box min Z
        +132 float     bounding box max X
        +136 float     bounding box max Y
        +140 float     bounding box max Z
        +144 float     radius
        +148 float     scale
        +152 char[32]  supermodel name
        +184 uint32[2] node list pointer
        +192 uint32    node count

      All internal offsets are relative to BASE=12 (the file header size).

    Mesh / AABB node extra fields (after 80-byte base node header, mesh type 0x0020):
      +80 ... standard mesh header
      +0x60  uint32  vertex count
      +0x64  uint32  vertex offset  (BASE-relative)
      +0x68  uint32  face count
      +0x6C  uint32  face offset    (BASE-relative; 6 bytes/face: 3×uint16 vertex indices)
      +0x70  uint32  walk-type array offset (BASE-relative; 4 bytes/face: uint32)
      +0x74  uint32  adjacent face array offset (BASE-relative; 12 bytes/face: 3×int32)

    AABB-node tree (node type 0x0200):
      Each AABB node is 44 bytes:
        +0   float[3]  AABB min XYZ
        +12  float[3]  AABB max XYZ
        +24  uint32    left child offset  (BASE-relative; 0 = leaf)
        +28  uint32    right child offset (BASE-relative; 0 = leaf)
        +32  int32     partitioning plane (-1 = leaf)
        +36  uint32    face index          (leaf only)
        +40  ... (padding / unused)

    This AABB tree structure is used internally by KotOR for O(log N) walkmesh
    ray-casting. GModular builds its own Python AABBTree from the parsed faces.
    """

    BASE = 12   # All internal offsets are relative to this

    def __init__(self, data: bytes):
        self._data = data

    @classmethod
    def from_file(cls, path: str) -> "WOKParser":
        with open(path, "rb") as f:
            return cls(f.read())

    def parse(self) -> WOKData:
        """
        Parse WOK binary data into a WOKData object.
        Falls back to synthetic demo geometry if the file is invalid or empty.
        """
        wok = WOKData()
        data = self._data

        if len(data) < 12:
            raise ValueError("WOK file too small (< 12 bytes)")

        try:
            mdl_data_off = struct.unpack_from("<I", data, 4)[0]
        except struct.error:
            log.warning("WOK: corrupt file header")
            self._synthetic_demo_geometry(wok)
            return wok

        if mdl_data_off + 200 > len(data):
            log.warning("WOK: model-data section offset out of bounds")
            self._synthetic_demo_geometry(wok)
            return wok

        # Use shared MDL/WOK header reader when available — avoids duplicated
        # struct offsets between walkmesh_editor and mdl_parser.
        if _HAS_MDL_HELPER and _read_mdl_header is not None:
            try:
                hdr = _read_mdl_header(data, base=mdl_data_off)
                wok.model_name = hdr["name"]
                wok.aabb_min   = hdr["bb_min"]
                wok.aabb_max   = hdr["bb_max"]
            except Exception as e:
                log.debug(f"WOK header fallback: {e}")
                # Fallback: manual reads
                try:
                    name_raw = data[mdl_data_off: mdl_data_off + 32]
                    wok.model_name = name_raw.rstrip(b"\x00").decode("ascii", errors="replace").strip()
                except Exception:
                    pass
                try:
                    bmin = struct.unpack_from("<fff", data, mdl_data_off + 120)
                    bmax = struct.unpack_from("<fff", data, mdl_data_off + 132)
                    wok.aabb_min = bmin
                    wok.aabb_max = bmax
                except Exception:
                    pass
        else:
            # No shared helper: manual reads (legacy path)
            try:
                name_raw = data[mdl_data_off: mdl_data_off + 32]
                wok.model_name = name_raw.rstrip(b"\x00").decode("ascii", errors="replace").strip()
            except Exception:
                pass
            try:
                bmin = struct.unpack_from("<fff", data, mdl_data_off + 120)
                bmax = struct.unpack_from("<fff", data, mdl_data_off + 132)
                wok.aabb_min = bmin
                wok.aabb_max = bmax
            except Exception:
                pass

        self._parse_geometry(wok, mdl_data_off)

        if not wok.faces:
            log.debug("WOK: no faces found — using synthetic demo geometry")
            self._synthetic_demo_geometry(wok)
        else:
            # Recompute tight AABB from actual vertex positions
            if wok.vertices:
                xs = [v[0] for v in wok.vertices]
                ys = [v[1] for v in wok.vertices]
                zs = [v[2] for v in wok.vertices]
                wok.aabb_min = (min(xs), min(ys), min(zs))
                wok.aabb_max = (max(xs), max(ys), max(zs))

        return wok

    def _parse_geometry(self, wok: WOKData, mdl_data_off: int = None):
        """
        Walk the MDL node tree and extract walk-type geometry from mesh nodes.
        Populates wok.vertices and wok.faces in-place.

        All internal MDL offsets are BASE-relative (BASE = 12):
          node_arr_off = BASE + node_arr_rel  (absolute file offset)
          node_off     = BASE + node_rel      (absolute file offset)
        """
        data = self._data
        BASE = self.BASE  # BASE = 12 - all geometry pointers are BASE-relative

        if len(data) < 12:
            return

        if mdl_data_off is None:
            try:
                mdl_data_off = struct.unpack_from("<I", data, 4)[0]
            except struct.error:
                return

        # Node list: pointer array and count are at mdl_data_off+184
        try:
            node_arr_rel = struct.unpack_from("<I", data, mdl_data_off + 184)[0]
            node_count   = struct.unpack_from("<I", data, mdl_data_off + 192)[0]
        except struct.error:
            return

        if node_arr_rel == 0 or node_count == 0 or node_count > 4096:
            return

        node_arr_off = BASE + node_arr_rel  # absolute file offset of node pointer array

        for ni in range(min(node_count, 512)):
            ptr_off = node_arr_off + ni * 4
            if ptr_off + 4 > len(data):
                break
            node_rel = struct.unpack_from("<I", data, ptr_off)[0]
            if node_rel == 0:
                continue
            node_off = BASE + node_rel
            if node_off + 80 > len(data):
                continue

            # Node type word is at node_off+0
            node_type = struct.unpack_from("<H", data, node_off)[0]

            # We only care about mesh nodes (0x0020) - they carry walk-type data
            if not (node_type & 0x0020):
                continue

            self._parse_mesh_node(wok, node_off)

    def _parse_mesh_node(self, wok: WOKData, node_off: int):
        """
        Extract vertices, faces and walk-types from a single mesh node.

        KotOR MDL mesh node structure after the 80-byte base node header:
          Mesh header (MDL mesh fields start at node_off+80):
            +0   uint32[2] function pointers (skip)
            +8   uint32    faces offset (BASE-relative)
            +12  uint32    faces count
            +16  uint32    faces count2 (same)
            +20  float[3]  bb_min
            +32  float[3]  bb_max
            +44  float     radius
            +48  float[3]  average position
            +60  float[3]  diffuse
            +72  float[3]  ambient
            +84  uint32    transparency_hint
            +88  char[32]  texture name
            ...  (many more fields; see MDL format spec)

        For WOK nodes we primarily need:
          vertex count / offset: standard MDL vertex block
          face count / offset:   face triples (3 × uint16 per face)
          walk-type array:       1 uint32 per face

        In practice KotOR WOK face data is stored at known relative positions
        within the mesh-node header.  We use the same offsets as MDLParser.
        """
        data = self._data
        B    = self.BASE

        # The mesh-specific header begins right after the 80-byte base node header.
        mh = node_off + 80

        if mh + 0x80 > len(data):
            return

        try:
            # Skip 8 bytes (function pointers)
            # Face array
            faces_off_rel = struct.unpack_from("<I", data, mh + 8)[0]
            faces_cnt     = struct.unpack_from("<I", data, mh + 12)[0]

            if faces_cnt == 0 or faces_cnt > 100_000:
                return

            faces_off = B + faces_off_rel if faces_off_rel else 0

            # Walk ahead to vertex block (varies by game version; use MDLParser conventions)
            # Skip: bb_min(12) bb_max(12) radius(4) avg_pos(12) diffuse(12) ambient(12)
            #        transparency(4) tex_name(32) lightmap_name(32) ...
            # We find vertex count / offset by using the same layout as MDLParser._parse_mesh
            # The mesh header scan: 8+4+4+4 = 20 bytes to get to bb_min
            skip_to_colors = 8 + 12   # fp(8) + faces(12) = offset 20
            mh2 = mh + skip_to_colors

            # Skip bb_min, bb_max, radius, avg_pos, diffuse, ambient, transparency
            mh2 += 12 + 12 + 4 + 12  # = 40 bytes (bb_min, bb_max, radius, avg_pos)
            mh2 += 12 + 12 + 4       # = 28 bytes (diffuse, ambient, transparency)
            mh2 += 32 + 32 + 24 + 12 + 12 + 12 + 8 + 4 + 16  # texture, lightmap, unknowns

            if mh2 + 4 > len(data):
                return

            mdx_data_size   = struct.unpack_from("<I", data, mh2)[0]; mh2 += 4
            mdx_data_bitmap = struct.unpack_from("<I", data, mh2)[0]; mh2 += 4

            # 11 MDX channel offsets
            mdx_v_off  = struct.unpack_from("<I", data, mh2)[0]; mh2 += 4
            mh2 += 4  # normals
            mh2 += 4  # vert colors
            mh2 += 4  # UV1
            mh2 += 4  # lightmap UV
            mh2 += 4  # UV2
            mh2 += 4  # UV3
            mh2 += 4  # bump map
            mh2 += 4  # unk1
            mh2 += 4  # unk2
            mh2 += 4  # unk3

            vert_cnt = struct.unpack_from("<H", data, mh2)[0]; mh2 += 2
            mh2 += 2  # tex_cnt
            mh2 += 4  # various flags (has_lightmap, rotate_texture, bg, has_shadow, beaming, has_render)
            mh2 += 2  # 2 unknown
            mh2 += 4  # total_area
            mh2 += 4  # unknown

            mdx_data_off = struct.unpack_from("<I", data, mh2)[0]; mh2 += 4
            verts_off_rel = struct.unpack_from("<I", data, mh2)[0]

        except struct.error:
            return

        if vert_cnt == 0 or vert_cnt > 100_000:
            return

        # ── Read vertices ─────────────────────────────────────────────────────
        verts: List[Tuple[float, float, float]] = []
        verts_loaded = False

        # Prefer MDX data (interleaved with position at mdx_v_off)
        if (mdx_data_size > 0 and mdx_data_off > 0
                and mdx_v_off != 0xFFFFFFFF
                and mdx_data_off < len(data)):
            stride = mdx_data_size
            for i in range(vert_cnt):
                base = mdx_data_off + i * stride + mdx_v_off
                if base + 12 > len(data):
                    break
                verts.append(struct.unpack_from("<fff", data, base))
            verts_loaded = (len(verts) == vert_cnt)

        if not verts_loaded and verts_off_rel:
            va = B + verts_off_rel
            for i in range(vert_cnt):
                p = va + i * 12
                if p + 12 > len(data):
                    break
                verts.append(struct.unpack_from("<fff", data, p))

        if not verts:
            return

        # ── Read faces (3 × uint16 vertex indices each) ───────────────────────
        # KotOR MDL face record is 32 bytes:
        #   float[3]  face normal
        #   float     plane distance
        #   uint32    material / walk-type  ← for WOK this is the walk-type!
        #   int16[3]  adjacent face indices
        #   uint16[3] vertex indices
        if faces_cnt == 0 or faces_off == B:
            return

        node_verts_start = len(wok.vertices)
        for v in verts:
            wok.vertices.append(v)

        parsed_faces = 0
        for fi in range(min(faces_cnt, 100_000)):
            fp = faces_off + fi * 32
            if fp + 32 > len(data):
                break
            # normal (skip), plane_dist (skip), material/walk_type, adj_faces, vert_indices
            wtype      = struct.unpack_from("<I", data, fp + 16)[0]
            i0, i1, i2 = struct.unpack_from("<HHH", data, fp + 26)

            if i0 >= len(verts) or i1 >= len(verts) or i2 >= len(verts):
                continue

            # Clamp walk_type to known range
            wtype = wtype & 0xFF  # only low byte is meaningful
            wok.faces.append(WOKFace(
                v0=verts[i0], v1=verts[i1], v2=verts[i2],
                walk_type=wtype,
                material=wtype,
            ))
            parsed_faces += 1

        log.debug(f"WOK mesh node: {len(verts)} vertices, {parsed_faces} faces parsed")

    def _synthetic_demo_geometry(self, wok: WOKData):
        """Generate demo walkmesh geometry for visualization when no real WOK is available."""
        import random
        random.seed(42)

        # 8×8 grid of triangular faces with varied walk types
        walk_choices = [0, 1, 1, 1, 1, 2, 3, 4, 5, 9, 13]
        for row in range(-4, 4):
            for col in range(-4, 4):
                x0, y0 = float(col), float(row)
                wtype = random.choice(walk_choices)
                f1 = WOKFace(
                    v0=(x0, y0, 0),      v1=(x0+1, y0, 0),
                    v2=(x0+1, y0+1, 0),  walk_type=wtype,
                )
                f2 = WOKFace(
                    v0=(x0, y0, 0),      v1=(x0+1, y0+1, 0),
                    v2=(x0, y0+1, 0),    walk_type=wtype,
                )
                wok.faces.append(f1)
                wok.faces.append(f2)

        wok.aabb_min = (-4, -4, 0)
        wok.aabb_max = ( 4,  4, 0)
        for row in range(-4, 5):
            for col in range(-4, 5):
                wok.vertices.append((float(col), float(row), 0.0))


# ─────────────────────────────────────────────────────────────────────────────
#  AABB Tree for O(log N) walkmesh ray-casting
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AABBNode:
    """One node of the AABB binary tree."""
    aabb_min: Tuple[float, float, float]
    aabb_max: Tuple[float, float, float]
    left:  Optional['AABBNode'] = field(default=None, repr=False)
    right: Optional['AABBNode'] = field(default=None, repr=False)
    face_indices: List[int] = field(default_factory=list)  # non-empty only for leaves

    @property
    def is_leaf(self) -> bool:
        return len(self.face_indices) > 0


class WOKAABBTree:
    """
    AABB binary tree built from a WOKData object for O(log N) ray-casting.

    Construction:
        tree = WOKAABBTree(wok_data)

    Ray query (vertical ray from above):
        face_idx = tree.raycast_vertical(x, y, z_start=10.0)
        if face_idx is not None:
            face = wok.faces[face_idx]

    Sphere overlap (footprint query):
        face_indices = tree.query_sphere(x, y, z, radius)
    """

    MAX_LEAF_FACES = 4    # maximum faces per leaf node before splitting

    def __init__(self, wok: WOKData, max_depth: int = 24):
        self._wok      = wok
        self._max_depth = max_depth
        self._root: Optional[AABBNode] = None
        if wok.faces:
            all_idx = list(range(len(wok.faces)))
            self._root = self._build(all_idx, depth=0)
        log.debug(f"WOKAABBTree: built from {len(wok.faces)} faces")

    # ── Tree construction ─────────────────────────────────────────────────────

    def _face_aabb(self, fi: int) -> Tuple[Tuple[float,float,float], Tuple[float,float,float]]:
        f = self._wok.faces[fi]
        verts = (f.v0, f.v1, f.v2)
        mn = (min(v[0] for v in verts), min(v[1] for v in verts), min(v[2] for v in verts))
        mx = (max(v[0] for v in verts), max(v[1] for v in verts), max(v[2] for v in verts))
        return mn, mx

    def _merge_aabbs(self, aabbs):
        mn_x = min(a[0][0] for a in aabbs)
        mn_y = min(a[0][1] for a in aabbs)
        mn_z = min(a[0][2] for a in aabbs)
        mx_x = max(a[1][0] for a in aabbs)
        mx_y = max(a[1][1] for a in aabbs)
        mx_z = max(a[1][2] for a in aabbs)
        return (mn_x, mn_y, mn_z), (mx_x, mx_y, mx_z)

    def _build(self, face_indices: List[int], depth: int) -> AABBNode:
        aabbs = [self._face_aabb(fi) for fi in face_indices]
        node_min, node_max = self._merge_aabbs(aabbs)

        # Leaf condition
        if len(face_indices) <= self.MAX_LEAF_FACES or depth >= self._max_depth:
            return AABBNode(
                aabb_min=node_min,
                aabb_max=node_max,
                face_indices=list(face_indices),
            )

        # Choose split axis (largest extent)
        extents = (
            node_max[0] - node_min[0],
            node_max[1] - node_min[1],
            node_max[2] - node_min[2],
        )
        axis = extents.index(max(extents))

        # Sort by face centroid along chosen axis
        def centroid(fi):
            f = self._wok.faces[fi]
            return (f.v0[axis] + f.v1[axis] + f.v2[axis]) / 3.0

        sorted_idx = sorted(face_indices, key=centroid)
        mid = len(sorted_idx) // 2

        node = AABBNode(aabb_min=node_min, aabb_max=node_max)
        node.left  = self._build(sorted_idx[:mid],  depth + 1)
        node.right = self._build(sorted_idx[mid:],  depth + 1)
        return node

    # ── AABB overlap helpers ──────────────────────────────────────────────────

    @staticmethod
    def _aabb_intersects_ray(mn, mx, ox, oy, oz, dx, dy, dz) -> bool:
        """
        Slab test for ray vs AABB.
        Ray: P(t) = O + t*D for t ≥ 0.
        """
        t_min = -1e38
        t_max =  1e38
        for o, d, lo, hi in ((ox, dx, mn[0], mx[0]),
                               (oy, dy, mn[1], mx[1]),
                               (oz, dz, mn[2], mx[2])):
            if abs(d) < 1e-12:
                if o < lo or o > hi:
                    return False
            else:
                t1 = (lo - o) / d
                t2 = (hi - o) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                t_min = max(t_min, t1)
                t_max = min(t_max, t2)
                if t_min > t_max:
                    return False
        return t_max >= 0

    @staticmethod
    def _aabb_intersects_sphere(mn, mx, cx, cy, cz, r) -> bool:
        """Closest-point-on-AABB-to-sphere-centre test."""
        dx = max(mn[0]-cx, 0.0, cx-mx[0])
        dy = max(mn[1]-cy, 0.0, cy-mx[1])
        dz = max(mn[2]-cz, 0.0, cz-mx[2])
        return dx*dx + dy*dy + dz*dz <= r*r

    @staticmethod
    def _ray_triangle(ox, oy, oz, dx, dy, dz, v0, v1, v2):
        """
        Möller-Trumbore ray-triangle intersection.
        Returns t ≥ 0 or None.
        """
        e1 = (v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2])
        e2 = (v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2])
        h  = (dy*e2[2]-dz*e2[1], dz*e2[0]-dx*e2[2], dx*e2[1]-dy*e2[0])
        a  = e1[0]*h[0] + e1[1]*h[1] + e1[2]*h[2]
        if abs(a) < 1e-9:
            return None
        inv_a = 1.0 / a
        s  = (ox-v0[0], oy-v0[1], oz-v0[2])
        u  = inv_a * (s[0]*h[0] + s[1]*h[1] + s[2]*h[2])
        if u < 0.0 or u > 1.0:
            return None
        q  = (s[1]*e1[2]-s[2]*e1[1], s[2]*e1[0]-s[0]*e1[2], s[0]*e1[1]-s[1]*e1[0])
        v  = inv_a * (dx*q[0] + dy*q[1] + dz*q[2])
        if v < 0.0 or u + v > 1.0:
            return None
        t  = inv_a * (e2[0]*q[0] + e2[1]*q[1] + e2[2]*q[2])
        return t if t >= 0.0 else None

    # ── Query API ─────────────────────────────────────────────────────────────

    def raycast(self, ox: float, oy: float, oz: float,
                dx: float, dy: float, dz: float,
                walkable_only: bool = True) -> Optional[Tuple[int, float]]:
        """
        Cast a ray against the walkmesh tree.

        Returns (face_index, t) of the closest hit, or None.
        If walkable_only=True, only walkable faces are tested.
        """
        if self._root is None:
            return None

        best_t:  Optional[float] = None
        best_fi: Optional[int]   = None

        stack = [self._root]
        while stack:
            node = stack.pop()
            if not self._aabb_intersects_ray(
                    node.aabb_min, node.aabb_max, ox, oy, oz, dx, dy, dz):
                continue

            if node.is_leaf:
                for fi in node.face_indices:
                    face = self._wok.faces[fi]
                    if walkable_only and not face.is_walkable:
                        continue
                    t = self._ray_triangle(ox, oy, oz, dx, dy, dz,
                                           face.v0, face.v1, face.v2)
                    if t is not None:
                        if best_t is None or t < best_t:
                            best_t = t
                            best_fi = fi
            else:
                if node.left  is not None: stack.append(node.left)
                if node.right is not None: stack.append(node.right)

        return (best_fi, best_t) if best_t is not None else None

    def raycast_vertical(self, x: float, y: float, z_start: float = 10.0,
                         walkable_only: bool = True) -> Optional[int]:
        """
        Cast a vertical ray downward from (x, y, z_start).
        Returns the index of the closest walkable face hit, or None.
        """
        result = self.raycast(x, y, z_start, 0.0, 0.0, -1.0, walkable_only)
        return result[0] if result else None

    def query_sphere(self, cx: float, cy: float, cz: float, radius: float,
                     walkable_only: bool = True) -> List[int]:
        """
        Return indices of all faces whose AABB overlaps the given sphere.
        Useful for footprint queries (player step, trigger detection).
        """
        results: List[int] = []
        if self._root is None:
            return results
        stack = [self._root]
        while stack:
            node = stack.pop()
            if not self._aabb_intersects_sphere(
                    node.aabb_min, node.aabb_max, cx, cy, cz, radius):
                continue
            if node.is_leaf:
                for fi in node.face_indices:
                    if walkable_only and not self._wok.faces[fi].is_walkable:
                        continue
                    results.append(fi)
            else:
                if node.left  is not None: stack.append(node.left)
                if node.right is not None: stack.append(node.right)
        return results

    def face_at(self, fi: int) -> Optional[WOKFace]:
        if 0 <= fi < len(self._wok.faces):
            return self._wok.faces[fi]
        return None

    def tree_depth(self) -> int:
        """Return the maximum depth of the built AABB tree."""
        def _depth(node):
            if node is None or node.is_leaf:
                return 0
            return 1 + max(_depth(node.left), _depth(node.right))
        return _depth(self._root)

    def node_count(self) -> int:
        """Count total nodes in the tree."""
        def _count(node):
            if node is None:
                return 0
            return 1 + _count(node.left) + _count(node.right)
        return _count(self._root)


# ─────────────────────────────────────────────────────────────────────────────
#  WOK Editor Panel
# ─────────────────────────────────────────────────────────────────────────────

class WalkmeshPanel(QWidget):
    """
    Walkmesh editor panel.
    Displays WOK faces, walk types, and tools for editing/exporting.
    """

    wok_loaded   = Signal(object)    # WOKData
    wok_modified = Signal(object)    # WOKData

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wok:       Optional[WOKData]     = None
        self._wok_path:  Optional[str]         = None
        self._aabb_tree: Optional[WOKAABBTree] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        hdr = QLabel("Walkmesh Editor (WOK)")
        hdr.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:10pt;")
        layout.addWidget(hdr)

        # Load controls
        load_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Load a .wok file…")
        self._path_edit.setFont(QFont("Consolas", 8))
        self._path_edit.setReadOnly(True)
        self._path_edit.setStyleSheet(
            "QLineEdit { background:#3c3c3c; color:#d4d4d4; border:1px solid #555; "
            "border-radius:2px; padding:0 4px; }"
        )
        load_row.addWidget(self._path_edit)

        browse_btn = self._btn("Browse…", self._browse_wok)
        load_row.addWidget(browse_btn)
        layout.addLayout(load_row)

        # Stats group
        stats_grp = QGroupBox("File Info")
        stats_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        stats_form = QFormLayout(stats_grp)
        stats_form.setContentsMargins(8, 8, 8, 8)
        stats_form.setSpacing(4)

        self._lbl_model  = QLabel("-")
        self._lbl_faces  = QLabel("-")
        self._lbl_walk   = QLabel("-")
        self._lbl_bounds = QLabel("-")

        self._lbl_model.setFont(QFont("Consolas", 8))
        self._lbl_faces.setFont(QFont("Consolas", 8))
        self._lbl_walk.setFont(QFont("Consolas", 8))
        self._lbl_bounds.setFont(QFont("Consolas", 8))

        for w in (self._lbl_model, self._lbl_faces, self._lbl_walk, self._lbl_bounds):
            w.setStyleSheet("color:#9cdcfe;")

        stats_form.addRow("Model:", self._lbl_model)
        stats_form.addRow("Total Faces:", self._lbl_faces)
        stats_form.addRow("Walkable:", self._lbl_walk)
        stats_form.addRow("Bounds (X×Y):", self._lbl_bounds)
        layout.addWidget(stats_grp)

        # Surface types table
        surf_grp = QGroupBox("Surface Types")
        surf_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        surf_layout = QVBoxLayout(surf_grp)
        surf_layout.setContentsMargins(4, 8, 4, 4)

        self._surf_table = QTableWidget()
        self._surf_table.setColumnCount(3)
        self._surf_table.setHorizontalHeaderLabels(["Type", "Name", "Count"])
        self._surf_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background:#2d2d30; color:#969696; "
            "border:none; padding:2px; font-size:8pt; }"
        )
        self._surf_table.setFont(QFont("Consolas", 8))
        self._surf_table.setStyleSheet(
            "QTableWidget { background:#1e1e1e; color:#d4d4d4; "
            "gridline-color:#3c3c3c; border:none; }"
            "QTableWidget::item:selected { background:#094771; }"
        )
        self._surf_table.setMinimumHeight(120)
        self._surf_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._surf_table.verticalHeader().hide()
        surf_layout.addWidget(self._surf_table)
        layout.addWidget(surf_grp)

        # Face list with search
        face_grp = QGroupBox("Faces")
        face_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        face_layout = QVBoxLayout(face_grp)
        face_layout.setContentsMargins(4, 8, 4, 4)

        # Walk type filter
        filter_row = QHBoxLayout()
        self._type_filter = QComboBox()
        self._type_filter.addItem("All types")
        for tid, tname in sorted(WALK_TYPE_NAMES.items()):
            self._type_filter.addItem(f"{tname} ({tid})", tid)
        self._type_filter.currentIndexChanged.connect(self._apply_face_filter)
        filter_row.addWidget(QLabel("Filter:"))
        filter_row.addWidget(self._type_filter)
        filter_row.addStretch()
        face_layout.addLayout(filter_row)

        self._face_table = QTableWidget()
        self._face_table.setColumnCount(5)
        self._face_table.setHorizontalHeaderLabels(
            ["#", "Type", "Name", "Center X", "Center Y"])
        self._face_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background:#2d2d30; color:#969696; "
            "border:none; padding:2px; font-size:8pt; }"
        )
        self._face_table.setFont(QFont("Consolas", 8))
        self._face_table.setStyleSheet(
            "QTableWidget { background:#1e1e1e; color:#d4d4d4; "
            "gridline-color:#3c3c3c; border:none; }"
            "QTableWidget::item:selected { background:#094771; }"
        )
        self._face_table.verticalHeader().hide()
        self._face_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._face_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        face_layout.addWidget(self._face_table)
        layout.addWidget(face_grp, stretch=1)

        # Export buttons
        export_row = QHBoxLayout()
        export_row.addStretch()
        self._export_btn = self._btn("Export WOK…", self._export_wok)
        self._export_btn.setEnabled(False)
        export_row.addWidget(self._export_btn)
        layout.addLayout(export_row)

    def _btn(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setFixedHeight(24)
        b.setStyleSheet(
            "QPushButton { background:#3c3c3c; color:#cccccc; border:1px solid #555;"
            " border-radius:3px; padding:0 8px; font-size:8pt; }"
            "QPushButton:hover { background:#4a4a4a; color:white; }"
        )
        return b

    # ── File Operations ───────────────────────────────────────────────────────

    def _browse_wok(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open WOK File", "",
            "KotOR Walkmesh (*.wok);;All Files (*)"
        )
        if path:
            self.load_wok(path)

    def load_wok(self, path: str):
        try:
            parser = WOKParser.from_file(path)
            wok = parser.parse()
            self._wok = wok
            self._wok_path = path
            # Build AABB tree for fast ray-casting
            self._aabb_tree = WOKAABBTree(wok)
            self._path_edit.setText(path)
            self._refresh_ui()
            self._export_btn.setEnabled(True)
            self.wok_loaded.emit(wok)
            log.info(f"WOK loaded: {path} — {wok.face_count} faces, "
                     f"AABB tree depth={self._aabb_tree.tree_depth()}, "
                     f"nodes={self._aabb_tree.node_count()}")
        except Exception as e:
            log.error(f"WOK load error: {e}")
            QMessageBox.warning(self, "WOK Load Error",
                                f"Failed to load walkmesh:\n{e}")

    def load_wok_data(self, wok: WOKData):
        """Load a WOKData directly (e.g. from game archive)."""
        self._wok = wok
        self._aabb_tree = WOKAABBTree(wok)
        self._refresh_ui()
        self._export_btn.setEnabled(True)

    def _refresh_ui(self):
        wok = self._wok
        if not wok:
            return

        # Stats
        self._lbl_model.setText(wok.model_name or "(unknown)")
        self._lbl_faces.setText(str(wok.face_count))
        pct = (wok.walkable_face_count / max(wok.face_count, 1)) * 100
        self._lbl_walk.setText(
            f"{wok.walkable_face_count} ({pct:.0f}%)")
        sx, sy, sz = wok.bounds_size
        self._lbl_bounds.setText(f"{sx:.1f} × {sy:.1f}")

        # Surface type summary
        type_counts: Dict[int, int] = {}
        for f in wok.faces:
            type_counts[f.walk_type] = type_counts.get(f.walk_type, 0) + 1

        self._surf_table.setRowCount(len(type_counts))
        for row, (tid, count) in enumerate(sorted(type_counts.items())):
            name = WALK_TYPE_NAMES.get(tid, f"Unknown({tid})")
            color_str = WALK_TYPE_COLORS.get(tid, "#d4d4d4")

            self._surf_table.setItem(row, 0, QTableWidgetItem(str(tid)))
            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(color_str))
            self._surf_table.setItem(row, 1, name_item)
            self._surf_table.setItem(row, 2, QTableWidgetItem(str(count)))

        self._surf_table.resizeColumnsToContents()
        self._apply_face_filter()

    def _apply_face_filter(self):
        wok = self._wok
        if not wok:
            return

        filter_idx = self._type_filter.currentIndex()
        if filter_idx == 0:
            faces = wok.faces
        else:
            target_type = self._type_filter.currentData()
            faces = [f for f in wok.faces if f.walk_type == target_type]

        self._face_table.setRowCount(len(faces))
        for row, face in enumerate(faces):
            cx, cy, cz = face.center
            name = WALK_TYPE_NAMES.get(face.walk_type, f"Type {face.walk_type}")
            color_str = WALK_TYPE_COLORS.get(face.walk_type, "#d4d4d4")

            items = [
                QTableWidgetItem(str(row)),
                QTableWidgetItem(str(face.walk_type)),
                QTableWidgetItem(name),
                QTableWidgetItem(f"{cx:.2f}"),
                QTableWidgetItem(f"{cy:.2f}"),
            ]
            items[2].setForeground(QColor(color_str))
            for col, item in enumerate(items):
                self._face_table.setItem(row, col, item)

        self._face_table.resizeColumnsToContents()

    def _export_wok(self):
        if not self._wok:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Walkmesh", "",
            "KotOR Native Walkmesh (*.wok);;"
            "GhostRigger Interchange (*.gwok)"
        )
        if not path:
            return
        try:
            # Determine format from selected filter or extension
            use_native = path.lower().endswith(".wok") and "gwok" not in selected_filter.lower()
            if use_native:
                self._write_native_wok(path)
            else:
                self._write_wok(path)
            QMessageBox.information(self, "Export Complete",
                                    f"Walkmesh exported to:\n{path}")
            log.info(f"WOK exported ({('native' if use_native else 'GWOK')}): {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error",
                                f"Failed to export:\n{e}")

    def _write_native_wok(self, path: str) -> None:
        """
        Write a KotOR-native BWM V1.0 walkmesh binary using WOKWriter.

        This output is byte-compatible with what kotorblender generates and can
        be loaded directly by the KotOR engine (place in Modules/ alongside the
        module's MDL).
        """
        try:
            from ..formats.wok_parser import WOKWriter
        except ImportError:
            from gmodular.formats.wok_parser import WOKWriter

        writer = WOKWriter(self._wok)
        writer.to_file(path)
        log.debug(f"_write_native_wok: {len(self._wok.faces)} faces → {path}")

    def _write_wok(self, path: str):
        """
        Write walkmesh geometry to disk in **GModular's own GWOK format**.

        ⚠ WARNING: This is NOT a native KotOR .WOK file.
        KotOR's engine cannot load GWOK files directly.
        GWOK is a compact interchange format used internally by GModular tools
        (GhostRigger can read it to rebuild KotOR-compatible MDL/WOK geometry).

        Binary layout:
          4B  magic "GWOK"  (GModular WOK identifier)
          4B  version 0x0100
          4B  vertex_count
          4B  face_count
          32B model_name (ASCII, null-padded)
          Vertices: vertex_count × 12 bytes (3 × float32 x,y,z)
          Faces:    face_count   × 6  bytes (3 × uint16  vertex indices)
          Wtypes:   face_count   × 4  bytes (uint32 walk-type per face)
          Bounds:   6 × float32  (aabb_min xyz, aabb_max xyz)
        """
        wok = self._wok

        # Collect unique vertices and build face index list
        vert_map: Dict[Tuple, int] = {}
        verts: List[Tuple[float, float, float]] = []
        faces_idx: List[Tuple[int, int, int]] = []
        wtypes: List[int] = []

        for face in wok.faces:
            idxs = []
            for v in (face.v0, face.v1, face.v2):
                key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
                if key not in vert_map:
                    vert_map[key] = len(verts)
                    verts.append(v)
                idxs.append(vert_map[key])
            faces_idx.append(tuple(idxs))
            wtypes.append(face.walk_type)

        # Binary layout:
        #   4B  magic "GWOK" (GModular WOK)
        #   4B  version 0x0100
        #   4B  vertex_count
        #   4B  face_count
        #   4B  model_name (32 bytes, null-padded)
        #   Vertices: vert_count × 12 bytes (3 × float32)
        #   Faces:    face_count × 6  bytes (3 × uint16)
        #   Wtypes:   face_count × 4  bytes (uint32 each)
        #   Bounds:   6 × float32 (aabb_min xyz, aabb_max xyz)

        vc = len(verts)
        fc = len(faces_idx)
        name_raw = (wok.model_name or "").encode("ascii", errors="replace")[:31]
        name_raw = name_raw.ljust(32, b"\x00")

        header = struct.pack("<4sIII32s",
                             b"GWOK", 0x0100, vc, fc, name_raw)

        vert_blob = b"".join(
            struct.pack("<fff", float(v[0]), float(v[1]), float(v[2]))
            for v in verts
        )
        face_blob = b"".join(
            struct.pack("<HHH", i0, i1, i2)
            for i0, i1, i2 in faces_idx
        )
        wtype_blob = b"".join(struct.pack("<I", wt) for wt in wtypes)

        mn, mx = wok.aabb_min, wok.aabb_max
        bounds_blob = struct.pack("<6f",
                                  float(mn[0]), float(mn[1]), float(mn[2]),
                                  float(mx[0]), float(mx[1]), float(mx[2]))

        with open(path, "wb") as fout:
            fout.write(header)
            fout.write(vert_blob)
            fout.write(face_blob)
            fout.write(wtype_blob)
            fout.write(bounds_blob)

        log.debug(f"WOK written: {vc} vertices, {fc} faces → {path}")

    def get_walkable_geometry(self):
        """Return list of (v0, v1, v2, walk_type) tuples for viewport overlay."""
        if not self._wok:
            return []
        return [(f.v0, f.v1, f.v2, f.walk_type) for f in self._wok.faces]

    def get_aabb_tree(self) -> Optional[WOKAABBTree]:
        """Return the AABB tree for the currently-loaded walkmesh, or None."""
        return self._aabb_tree

    def raycast_floor(self, x: float, y: float, z_start: float = 10.0) -> Optional[float]:
        """
        Cast a vertical ray downward from (x, y, z_start) and return the Z
        height of the first walkable face hit, or None.
        Uses the WOKAABBTree for O(log N) performance.
        """
        tree = self._aabb_tree
        wok  = self._wok
        if tree is None or wok is None:
            return None
        result = tree.raycast(x, y, z_start, 0.0, 0.0, -1.0, walkable_only=True)
        if result is None:
            return None
        fi, t = result
        return z_start - t

    def get_walk_type_at(self, x: float, y: float, z_start: float = 10.0) -> Optional[int]:
        """
        Return the walk_type integer of the walkmesh face below (x, y), or None.
        """
        tree = self._aabb_tree
        wok  = self._wok
        if tree is None or wok is None:
            return None
        fi = tree.raycast_vertical(x, y, z_start)
        if fi is None:
            return None
        return wok.faces[fi].walk_type
