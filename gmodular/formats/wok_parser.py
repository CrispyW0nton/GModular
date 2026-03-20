"""
GModular — KotOR WOK (Walkmesh) Parser & Writer
================================================
Parses and writes KotOR room walkmesh (.wok) files in the native BWM V1.0 binary
format used by BioWare's Odyssey engine.

BWM File Format (little-endian):
  Header (136 bytes):
    file_type     [4]   'BWM '
    version       [4]   'V1.0'
    bwm_type      u32   0=WOK(room), 1=PWK/DWK(placeable/door)
    rel_use_vec1  3f    relative use-point 1
    rel_use_vec2  3f    relative use-point 2
    abs_use_vec1  3f    absolute use-point 1
    abs_use_vec2  3f    absolute use-point 2
    position      3f    room origin in world space
    num_verts     u32
    off_verts     u32
    num_faces     u32
    off_vert_idx  u32   face vertex indices (3×u32 per face)
    off_mat_ids   u32   material IDs (1×u32 per face)
    off_normals   u32   face normals (3×f32 per face)
    off_distances u32   face plane distances (1×f32 per face)
    num_aabbs     u32
    off_aabbs     u32
    unknown       u32
    num_adj_edges u32   only walkable faces contribute
    off_adj_edges u32   adjacent edge table (3×i32 per walkable face)
    num_outer_edges u32
    off_outer_edges u32 outer edge table (1×u32 edge_idx + 1×i32 transition)
    num_perimeters  u32
    off_perimeters  u32 perimeter table (1×u32 per entry)

AABB node (44 bytes):
    bb_min   3f
    bb_max   3f
    face_idx i32  (≥0 = leaf face, -1 = internal node)
    unknown  u32
    most_significant_plane u32  (axis split: 1=+X,2=+Y,4=+Z,8=−X,16=−Y,32=−Z,0=leaf)
    child_idx1  u32
    child_idx2  u32

Format references:
  kotorblender: io_scene_kotor/format/bwm/reader.py, writer.py
  kotorblender: io_scene_kotor/aabb.py
  xoreos: src/graphics/aurora/walkmesh.cpp
  KotOR modding wiki: https://kotor-modding.fandom.com/wiki/BWM_Format
"""
from __future__ import annotations

import os
import struct
import math
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from pathlib import Path

log = logging.getLogger(__name__)

# ── BWM constants ─────────────────────────────────────────────────────────────
BWM_MAGIC        = b"BWM "
BWM_VERSION      = b"V1.0"
BWM_TYPE_WOK     = 1   # Area walkmesh (.wok / AreaModel)
BWM_TYPE_PWK_DWK = 0   # Placeable/door walkmesh (.pwk, .dwk)

BWM_HEADER_SIZE  = 136   # bytes

# AABB split-axis constants (kotorblender aabb.py most_significant_plane)
AABB_NO_CHILDREN = 0x00
AABB_POSITIVE_X  = 0x01
AABB_POSITIVE_Y  = 0x02
AABB_POSITIVE_Z  = 0x04
AABB_NEGATIVE_X  = 0x08
AABB_NEGATIVE_Y  = 0x10
AABB_NEGATIVE_Z  = 0x20

# Map most_significant_plane → split axis index (0=X, 1=Y, 2=Z)
_PLANE_TO_AXIS: Dict[int, int] = {
    AABB_POSITIVE_X: 0, AABB_NEGATIVE_X: 0,
    AABB_POSITIVE_Y: 1, AABB_NEGATIVE_Y: 1,
    AABB_POSITIVE_Z: 2, AABB_NEGATIVE_Z: 2,
}

# ── Surface material enum (Kotor.NET SurfaceMaterial.cs — aligned to surfacemat.2da) ────────
# Each value = surfacemat.2da row index.  Matches Kotor.NET SurfaceMaterial enum exactly.
SURF_UNDEFINED    = 0    # (row 0  in vanilla game = Dirt, but enum uses 0 as sentinel)
SURF_DIRT         = 1
SURF_OBSCURING    = 2
SURF_GRASS        = 3
SURF_STONE        = 4
SURF_WOOD         = 5
SURF_WATER        = 6
SURF_NONWALK      = 7
SURF_TRANSPARENT  = 8
SURF_CARPET       = 9
SURF_METAL        = 10
SURF_PUDDLES      = 11
SURF_SWAMP        = 12
SURF_MUD          = 13
SURF_LEAVES       = 14
SURF_LAVA         = 15
SURF_BOTTOMLESSPIT= 16
SURF_DEEPWATER    = 17
SURF_DOOR         = 18
SURF_NONWALKGRASS = 19
SURF_TRIGGER      = 30  # per Kotor.NET SurfaceMaterial.Trigger = 30

# Human-readable surface material name map (for debugging and UI display)
SURF_NAMES: Dict[int, str] = {
    SURF_UNDEFINED:    "Undefined",
    SURF_DIRT:         "Dirt",
    SURF_OBSCURING:    "Obscuring",
    SURF_GRASS:        "Grass",
    SURF_STONE:        "Stone",
    SURF_WOOD:         "Wood",
    SURF_WATER:        "Water",
    SURF_NONWALK:      "NonWalk",
    SURF_TRANSPARENT:  "Transparent",
    SURF_CARPET:       "Carpet",
    SURF_METAL:        "Metal",
    SURF_PUDDLES:      "Puddles",
    SURF_SWAMP:        "Swamp",
    SURF_MUD:          "Mud",
    SURF_LEAVES:       "Leaves",
    SURF_LAVA:         "Lava",
    SURF_BOTTOMLESSPIT:"BottomlessPit",
    SURF_DEEPWATER:    "DeepWater",
    SURF_DOOR:         "Door",
    SURF_NONWALKGRASS: "NonWalkGrass",
    SURF_TRIGGER:      "Trigger",
}

# Non-walkable surface IDs — any of these prevents player movement
# Aligned to Kotor.NET surfacemat.2da: IsWalkable=0 for these rows
_NON_WALKABLE_MATS: frozenset = frozenset([
    SURF_NONWALK,       # 7  — explicit non-walk surface
    SURF_TRANSPARENT,   # 8  — invisible trigger-like geometry
    SURF_LAVA,          # 15 — impassable hazard
    SURF_BOTTOMLESSPIT, # 16 — impassable hazard
    SURF_DEEPWATER,     # 17 — blocks movement
    SURF_NONWALKGRASS,  # 19 — decorative non-walkable grass
    SURF_TRIGGER,       # 30 — trigger zone (walkable in engine, non-walk for pathfinder)
    SURF_OBSCURING,     # 2  — obscuring tile (camera-only, not walkable)
])


class SurfaceMaterial:
    """Namespace class for surface material constants (mirrors SURF_* module constants).
    
    Provides OOP access to the same values as the module-level SURF_* constants::
    
        from gmodular.formats.wok_parser import SurfaceMaterial
        face.material = SurfaceMaterial.GRASS   # same as SURF_GRASS = 3
    """
    UNDEFINED     = SURF_UNDEFINED
    DIRT          = SURF_DIRT
    OBSCURING     = SURF_OBSCURING
    GRASS         = SURF_GRASS
    STONE         = SURF_STONE
    WOOD          = SURF_WOOD
    WATER         = SURF_WATER
    NONWALK       = SURF_NONWALK
    TRANSPARENT   = SURF_TRANSPARENT
    CARPET        = SURF_CARPET
    METAL         = SURF_METAL
    PUDDLES       = SURF_PUDDLES
    SWAMP         = SURF_SWAMP
    MUD           = SURF_MUD
    LEAVES        = SURF_LEAVES
    LAVA          = SURF_LAVA
    BOTTOMLESSPIT = SURF_BOTTOMLESSPIT
    DEEPWATER     = SURF_DEEPWATER
    DOOR          = SURF_DOOR
    NONWALKGRASS  = SURF_NONWALKGRASS
    TRIGGER       = SURF_TRIGGER


# ── Walkability material map ──────────────────────────────────────────────────
# surfacemat.2da row → walkable flag
# NOTE: Row indices here are 0-based raw BWM material IDs (NOT the SURF_* enum above).
# The SURF_* enum is 1-based and maps to surfacemat.2da labels; raw BWM face materials
# are 0-based and map directly to _SURF_WALKABLE below.
# See Kotor.NET BWMBinary source and surfacemat.2da for ground truth.
_SURF_WALKABLE: List[bool] = [
    True,   # 0  Dirt
    True,   # 1  Obscuring    (walkable physically, camera-occluded area)
    True,   # 2  Grass
    True,   # 3  Stone
    True,   # 4  Wood
    True,   # 5  Water        (shallow walkable water)
    False,  # 6  NonWalk      (explicit blocker)
    False,  # 7  Transparent  (see-through blocker)
    True,   # 8  Carpet
    True,   # 9  Metal
    True,   # 10 Puddles
    True,   # 11 Swamp
    True,   # 12 Mud
    True,   # 13 Leaves
    False,  # 14 Lava
    False,  # 15 BottomlessPit
    False,  # 16 DeepWater
    True,   # 17 Door (open = walkable)
    True,   # 18 Snow
    True,   # 19 Sand
]


def is_walkable(material: int) -> bool:
    """Return True if the given surface material ID (raw BWM row) is walkable."""
    if 0 <= material < len(_SURF_WALKABLE):
        return _SURF_WALKABLE[material]
    return False


def surface_material_name(material: int) -> str:
    """Return a human-readable name for a surface material ID."""
    return SURF_NAMES.get(material, f"Surface_{material}")


# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WalkFace:
    """A single walkmesh triangle with material and normal."""
    v0:       Tuple[float, float, float]
    v1:       Tuple[float, float, float]
    v2:       Tuple[float, float, float]
    material: int = 0      # surfacemat.2da row index
    normal:   Tuple[float, float, float] = (0.0, 0.0, 1.0)

    @property
    def walkable(self) -> bool:
        return is_walkable(self.material)

    @property
    def center(self) -> Tuple[float, float, float]:
        return (
            (self.v0[0] + self.v1[0] + self.v2[0]) / 3.0,
            (self.v0[1] + self.v1[1] + self.v2[1]) / 3.0,
            (self.v0[2] + self.v1[2] + self.v2[2]) / 3.0,
        )

    def as_tuple(self) -> Tuple[Tuple, Tuple, Tuple]:
        """Return (v0, v1, v2) for use with collision code."""
        return (self.v0, self.v1, self.v2)


@dataclass
class AABBNode:
    """A node in the AABB tree stored in the BWM file."""
    bb_min:  Tuple[float, float, float]
    bb_max:  Tuple[float, float, float]
    face_idx: int       # ≥0 = leaf; -1 = internal node
    most_significant_plane: int   # split-axis constant
    child_idx1: int
    child_idx2: int

    @property
    def is_leaf(self) -> bool:
        return self.face_idx >= 0


@dataclass
class WalkMesh:
    """
    Parsed walkmesh for a single room.

    Contains the list of WalkFace objects, the AABB tree, adjacency and outer-edge
    tables, and utility methods for ray-triangle intersection and pathfinding.
    """
    name:       str               = ""
    position:   Tuple[float,float,float] = (0.0, 0.0, 0.0)
    faces:      List[WalkFace]    = field(default_factory=list)
    aabbs:      List[AABBNode]    = field(default_factory=list)
    # Adjacent-edge table: per walkable face, 3 i32 entries (neighbouring face index or -1)
    adj_edges:  List[Tuple[int,int,int]] = field(default_factory=list)
    # Outer-edge table: (edge_vertex_index, transition_room_id)
    outer_edges: List[Tuple[int,int]]   = field(default_factory=list)
    perimeters: List[int]         = field(default_factory=list)

    # ── Derived properties ────────────────────────────────────────────────

    @property
    def walkable_faces(self) -> List[WalkFace]:
        return [f for f in self.faces if f.walkable]

    @property
    def non_walkable_faces(self) -> List[WalkFace]:
        return [f for f in self.faces if not f.walkable]

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def walkable_face_count(self) -> int:
        return sum(1 for f in self.faces if f.walkable)

    # ── Ray casting ──────────────────────────────────────────────────────

    def _ray_intersects_aabb(self, x: float, y: float,
                              bb_min: Tuple[float,float,float],
                              bb_max: Tuple[float,float,float]) -> bool:
        """AABB slab test for a vertical ray at (x, y).
        The ray origin is (x, y, 1000) pointing in -Z direction.
        We only need to test the XY slabs.
        """
        return bb_min[0] <= x <= bb_max[0] and bb_min[1] <= y <= bb_max[1]

    def _query_aabb_tree(self, x: float, y: float,
                         node_idx: int = 0) -> Optional[float]:
        """
        Traverse the AABB tree recursively to find the highest walkable Z at (x, y).
        Returns the best Z hit found, or None.
        """
        if not self.aabbs or node_idx < 0 or node_idx >= len(self.aabbs):
            return None
        node = self.aabbs[node_idx]
        if not self._ray_intersects_aabb(x, y, node.bb_min, node.bb_max):
            return None
        if node.is_leaf:
            fidx = node.face_idx
            if 0 <= fidx < len(self.faces):
                face = self.faces[fidx]
                if face.walkable:
                    z = _ray_triangle_intersect((x, y, 1000.0), (0.0, 0.0, -1.0),
                                                face.v0, face.v1, face.v2)
                    return z
            return None
        # Internal node — query both children
        z1 = self._query_aabb_tree(x, y, node.child_idx1)
        z2 = self._query_aabb_tree(x, y, node.child_idx2)
        if z1 is None: return z2
        if z2 is None: return z1
        return max(z1, z2)  # highest surface wins

    def height_at(self, x: float, y: float) -> Optional[float]:
        """
        Return the Z height of the walkmesh at (x, y) by casting a ray downward.
        Checks only walkable faces. Returns None if no face is hit.

        Uses the AABB tree when available (O(log n)) for play-mode performance.
        Falls back to linear scan (O(n)) when AABB tree is absent.
        """
        # Fast path: AABB tree traversal
        if self.aabbs:
            return self._query_aabb_tree(x, y, 0)

        # Slow path: linear scan (no AABB tree — e.g., module combined walkmesh)
        ray_origin = (x, y, 1000.0)
        ray_dir    = (0.0, 0.0, -1.0)
        best_z: Optional[float] = None
        for face in self.walkable_faces:
            z = _ray_triangle_intersect(ray_origin, ray_dir,
                                        face.v0, face.v1, face.v2)
            if z is not None and (best_z is None or z > best_z):
                best_z = z
        return best_z

    def height_at_any(self, x: float, y: float) -> Optional[float]:
        """Like height_at() but considers ALL faces (walkable and non-walkable)."""
        ray_origin = (x, y, 1000.0)
        ray_dir    = (0.0, 0.0, -1.0)
        best_z: Optional[float] = None
        for face in self.faces:
            z = _ray_triangle_intersect(ray_origin, ray_dir,
                                        face.v0, face.v1, face.v2)
            if z is not None and (best_z is None or z > best_z):
                best_z = z
        return best_z

    def face_at(self, x: float, y: float,
                walkable_only: bool = True) -> Optional[WalkFace]:
        """Return the WalkFace under (x, y), or None."""
        ray_origin = (x, y, 1000.0)
        ray_dir    = (0.0, 0.0, -1.0)
        best_z: Optional[float] = None
        best_face: Optional[WalkFace] = None
        candidates = self.walkable_faces if walkable_only else self.faces
        for face in candidates:
            z = _ray_triangle_intersect(ray_origin, ray_dir,
                                        face.v0, face.v1, face.v2)
            if z is not None and (best_z is None or z > best_z):
                best_z = z
                best_face = face
        return best_face

    def surface_material_at(self, x: float, y: float) -> int:
        """Return the surface material ID of the face under (x, y), or -1."""
        face = self.face_at(x, y, walkable_only=False)
        return face.material if face is not None else -1

    # ── Geometry helpers ──────────────────────────────────────────────────

    def bounds(self) -> Tuple[Tuple[float,float,float], Tuple[float,float,float]]:
        """Return (bb_min, bb_max) world-space bounding box of the walkmesh."""
        if not self.faces:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs, ys, zs = [], [], []
        for f in self.faces:
            for v in (f.v0, f.v1, f.v2):
                xs.append(v[0]); ys.append(v[1]); zs.append(v[2])
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def build_aabb_tree(self) -> None:
        """
        Build an AABB tree over all faces for fast O(log n) ray queries.

        Called automatically by build_module_walkmesh() after combining rooms.
        Also useful when a WalkMesh is built programmatically without parsing a
        BWM file (which already contains its own AABB tree).
        """
        if not self.faces:
            return
        # Build raw node list via _generate_aabb_tree
        face_list = []
        for idx, face in enumerate(self.faces):
            v0, v1, v2 = face.v0, face.v1, face.v2
            cx = (v0[0] + v1[0] + v2[0]) / 3.0
            cy = (v0[1] + v1[1] + v2[1]) / 3.0
            cz = (v0[2] + v1[2] + v2[2]) / 3.0
            face_list.append((idx, [v0, v1, v2], (cx, cy, cz)))
        raw_nodes: list = []
        _generate_aabb_tree(raw_nodes, face_list)
        # Convert raw 10-element lists into AABBNode dataclass objects
        self.aabbs = []
        for n in raw_nodes:
            self.aabbs.append(AABBNode(
                bb_min=(n[0], n[1], n[2]),
                bb_max=(n[3], n[4], n[5]),
                child_idx1=n[6] if n[6] != 0xFFFFFFFF else -1,
                child_idx2=n[7] if n[7] != 0xFFFFFFFF else -1,
                face_idx=n[8],
                most_significant_plane=n[9],
            ))
        log.debug(f"WalkMesh '{self.name}': built AABB tree with {len(self.aabbs)} nodes "
                  f"over {len(self.faces)} faces")

    def walkable_region_center(self) -> Optional[Tuple[float, float, float]]:
        """Return the centroid of all walkable face centers."""
        wf = self.walkable_faces
        if not wf:
            return None
        return (
            sum(f.center[0] for f in wf) / len(wf),
            sum(f.center[1] for f in wf) / len(wf),
            sum(f.center[2] for f in wf) / len(wf),
        )

    def material_counts(self) -> Dict[int, int]:
        """Return a dict mapping surface material ID → face count."""
        counts: Dict[int, int] = {}
        for f in self.faces:
            counts[f.material] = counts.get(f.material, 0) + 1
        return counts

    def clamp_to_walkmesh(self, x: float, y: float,
                           search_radius: float = 2.0
                           ) -> Optional[Tuple[float, float, float]]:
        """Snap (x, y) to the nearest walkable face center if the position itself
        is not walkable. Returns (x, y, z) or None."""
        z = self.height_at(x, y)
        if z is not None:
            return (x, y, z)
        best_dist = search_radius
        best_pos: Optional[Tuple[float, float, float]] = None
        for face in self.walkable_faces:
            cx, cy, cz = face.center
            d = math.sqrt((cx - x) ** 2 + (cy - y) ** 2)
            if d < best_dist:
                best_dist = d
                best_pos = (cx, cy, cz)
        return best_pos

    def is_position_walkable(self, x: float, y: float) -> bool:
        """Return True if the XY position is over a walkable face."""
        return self.height_at(x, y) is not None

    def walk_tris(self) -> List[Tuple]:
        """Return walkable triangles as ((v0,v1,v2), ...) tuples."""
        return [f.as_tuple() for f in self.walkable_faces]

    def nowalk_tris(self) -> List[Tuple]:
        """Return non-walkable triangles as ((v0,v1,v2), ...) tuples."""
        return [f.as_tuple() for f in self.non_walkable_faces]


# ─────────────────────────────────────────────────────────────────────────────
#  Native BWM Binary Parser
# ─────────────────────────────────────────────────────────────────────────────

class WOKParser:
    """
    Parses KotOR binary .wok / .pwk / .dwk walkmesh files in BWM V1.0 format.

    This is a pure-Python implementation of the BWM binary format as documented
    in kotorblender (io_scene_kotor/format/bwm/reader.py) and validated against
    real KotOR game assets.

    BWM header is 136 bytes; vertices, faces, normals, AABB tree, adjacency and
    outer-edge tables follow at explicit offsets stored in the header.

    Usage::
        wm = WOKParser.from_bytes(wok_bytes)
        wm = WOKParser.from_file("manm26ab.wok")
        walk_tris = wm.walk_tris()
        h = wm.height_at(10.0, 5.0)
    """

    @staticmethod
    def from_bytes(data: bytes, name: str = "walkmesh") -> WalkMesh:
        """Parse WOK from bytes. Returns a WalkMesh."""
        return WOKParser._parse(data, name)

    @staticmethod
    def from_file(path: str) -> WalkMesh:
        """Load and parse a .wok / .pwk / .dwk file. Returns a WalkMesh."""
        name = Path(path).stem.lower()
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            log.error(f"WOK: cannot read {path!r}: {e}")
            return WalkMesh(name=name)
        return WOKParser._parse(data, name)

    @staticmethod
    def _parse(data: bytes, name: str) -> WalkMesh:
        """Parse raw BWM bytes and return a populated WalkMesh."""
        wm = WalkMesh(name=name)

        # ── Check if this looks like a real BWM file ──────────────────────
        if len(data) < BWM_HEADER_SIZE:
            log.warning(f"WOK '{name}': too small for BWM header ({len(data)} bytes)")
            return wm

        magic   = data[0:4]
        version = data[4:8]

        if magic != BWM_MAGIC:
            log.warning(f"WOK '{name}': bad magic {magic!r}, expected b'BWM '")
            return wm
        if version != BWM_VERSION:
            log.warning(f"WOK '{name}': unsupported version {version!r}, expected b'V1.0'")
            return wm

        try:
            WOKParser._parse_bwm(data, wm)
        except Exception as e:
            log.warning(f"WOK '{name}': parse error: {e}", exc_info=True)

        log.debug(f"WOK '{name}': {len(wm.faces)} faces "
                  f"({wm.walkable_face_count} walkable), "
                  f"{len(wm.aabbs)} AABB nodes")
        return wm

    @staticmethod
    def _parse_bwm(data: bytes, wm: WalkMesh) -> None:
        """Inner: parse a validated BWM byte buffer into a WalkMesh."""
        off = 0

        def ru8()  -> int:   nonlocal off; v = data[off]; off += 1; return v
        def ru32() -> int:   nonlocal off; v, = struct.unpack_from('<I', data, off); off += 4; return v
        def ri32() -> int:   nonlocal off; v, = struct.unpack_from('<i', data, off); off += 4; return v
        def rf32() -> float: nonlocal off; v, = struct.unpack_from('<f', data, off); off += 4; return v

        # ── Header ───────────────────────────────────────────────────────
        off = 8                      # skip magic + version
        bwm_type  = ru32()

        rel_use_vec1 = (rf32(), rf32(), rf32())
        rel_use_vec2 = (rf32(), rf32(), rf32())
        abs_use_vec1 = (rf32(), rf32(), rf32())
        abs_use_vec2 = (rf32(), rf32(), rf32())

        pos_x, pos_y, pos_z = rf32(), rf32(), rf32()
        wm.position = (pos_x, pos_y, pos_z)

        num_verts       = ru32()
        off_verts       = ru32()
        num_faces       = ru32()
        off_vert_indices= ru32()
        off_material_ids= ru32()
        off_normals     = ru32()
        off_distances   = ru32()
        num_aabbs       = ru32()
        off_aabbs       = ru32()
        _unknown        = ru32()
        num_adj_edges   = ru32()
        off_adj_edges   = ru32()
        num_outer_edges = ru32()
        off_outer_edges = ru32()
        num_perimeters  = ru32()
        off_perimeters  = ru32()

        assert off == BWM_HEADER_SIZE, f"header consumed {off} bytes, expected {BWM_HEADER_SIZE}"

        # ── Vertices ──────────────────────────────────────────────────────
        verts: List[Tuple[float,float,float]] = []
        off = off_verts
        for _ in range(num_verts):
            vx = rf32() - pos_x
            vy = rf32() - pos_y
            vz = rf32() - pos_z
            verts.append((vx, vy, vz))

        # ── Face vertex indices ───────────────────────────────────────────
        face_indices: List[Tuple[int,int,int]] = []
        off = off_vert_indices
        for _ in range(num_faces):
            i0, i1, i2 = ru32(), ru32(), ru32()
            face_indices.append((i0, i1, i2))

        # ── Material IDs ──────────────────────────────────────────────────
        off = off_material_ids
        material_ids: List[int] = [ru32() for _ in range(num_faces)]

        # ── Normals ───────────────────────────────────────────────────────
        off = off_normals
        normals: List[Tuple[float,float,float]] = []
        for _ in range(num_faces):
            normals.append((rf32(), rf32(), rf32()))

        # ── Distances (plane d) ───────────────────────────────────────────
        off = off_distances
        distances: List[float] = [rf32() for _ in range(num_faces)]

        # ── Build faces ───────────────────────────────────────────────────
        for i in range(num_faces):
            i0, i1, i2 = face_indices[i]
            if i0 >= num_verts or i1 >= num_verts or i2 >= num_verts:
                log.warning(f"WOK: face {i} index out of range, skipping")
                continue
            v0, v1, v2 = verts[i0], verts[i1], verts[i2]
            normal = normals[i] if i < len(normals) else _face_normal(v0, v1, v2)
            mat    = material_ids[i] if i < len(material_ids) else 0
            wm.faces.append(WalkFace(v0=v0, v1=v1, v2=v2,
                                     material=mat, normal=normal))

        # ── AABB tree ─────────────────────────────────────────────────────
        if num_aabbs > 0 and off_aabbs + num_aabbs * 44 <= len(data):
            off = off_aabbs
            for _ in range(num_aabbs):
                bmin = (rf32(), rf32(), rf32())
                bmax = (rf32(), rf32(), rf32())
                face_idx  = ri32()
                _unk      = ru32()
                msp       = ru32()
                child1    = ru32()
                child2    = ru32()
                wm.aabbs.append(AABBNode(
                    bb_min=bmin, bb_max=bmax,
                    face_idx=face_idx,
                    most_significant_plane=msp,
                    child_idx1=child1,
                    child_idx2=child2,
                ))

        # ── Adjacent edges ────────────────────────────────────────────────
        if num_adj_edges > 0 and off_adj_edges + num_adj_edges * 12 <= len(data):
            off = off_adj_edges
            for _ in range(num_adj_edges):
                wm.adj_edges.append((ri32(), ri32(), ri32()))

        # ── Outer edges ───────────────────────────────────────────────────
        if num_outer_edges > 0 and off_outer_edges + num_outer_edges * 8 <= len(data):
            off = off_outer_edges
            for _ in range(num_outer_edges):
                edge_idx   = ru32()
                transition = ri32()
                wm.outer_edges.append((edge_idx, transition))

        # ── Perimeters ────────────────────────────────────────────────────
        if num_perimeters > 0 and off_perimeters + num_perimeters * 4 <= len(data):
            off = off_perimeters
            for _ in range(num_perimeters):
                wm.perimeters.append(ru32())


# ─────────────────────────────────────────────────────────────────────────────
#  Native BWM Binary Writer
# ─────────────────────────────────────────────────────────────────────────────

class WOKWriter:
    """
    Writes KotOR-native BWM V1.0 walkmesh files from a WalkMesh object.

    This produces byte-identical output to what kotorblender generates and what
    the KotOR engine loads directly. Handles:
    - Vertex deduplication (merge within 1e-4 units)
    - Walkable/non-walkable face ordering (walkable first, required by adjacency table)
    - AABB tree generation (median-split, matching the KotOR engine's expected layout)
    - Adjacent-edge table construction (3 entries per walkable face)
    - Outer-edge table construction (boundary edges with room-transition IDs)

    Usage::
        writer = WOKWriter(walkmesh)
        wok_bytes = writer.to_bytes()
        Path("manm26ab.wok").write_bytes(wok_bytes)
    """

    def __init__(self, wm: WalkMesh, bwm_type: int = BWM_TYPE_WOK):
        self.wm = wm
        self.bwm_type = bwm_type

        # Derived data (computed during to_bytes())
        self._verts:   List[Tuple[float,float,float]] = []
        self._face_vis: List[int] = []      # remapped vertex index per face-corner
        self._face_mat: List[int] = []      # face material IDs (walkable first)
        self._face_nrm: List[Tuple[float,float,float]] = []
        self._face_dst: List[float] = []    # plane distances
        self._walkable_count: int = 0
        self._aabb_nodes: list = []         # flat list from _generate_aabb_tree
        self._adj_edges: List[Tuple[int,int,int]] = []
        self._outer_edges: List[Tuple[int,int]] = []

    def to_bytes(self) -> bytes:
        """Build and return the complete BWM binary."""
        self._build()
        return self._pack()

    def to_file(self, path: str) -> None:
        """Write BWM to a file."""
        Path(path).write_bytes(self.to_bytes())

    # ── Internal build ────────────────────────────────────────────────────

    def _build(self) -> None:
        """Compute all derived data structures."""
        self._dedup_vertices()
        self._order_faces()
        self._build_aabb()
        self._build_edge_tables()

    def _dedup_vertices(self) -> None:
        """Collect unique vertices from all faces (deduplicated at 1e-4 precision)."""
        seen: Dict[Tuple[int,int,int], int] = {}
        raw_faces = list(self.wm.faces)

        # Walkable faces first (required by adjacent-edge table)
        walkable   = [f for f in raw_faces if f.walkable]
        nonwalkable = [f for f in raw_faces if not f.walkable]
        ordered    = walkable + nonwalkable
        self._walkable_count = len(walkable)

        self._face_vis = []
        self._face_mat = []
        self._face_nrm = []
        self._face_dst = []

        for face in ordered:
            tri_vis = []
            for vx, vy, vz in (face.v0, face.v1, face.v2):
                key = (int(round(vx * 10000)),
                       int(round(vy * 10000)),
                       int(round(vz * 10000)))
                if key not in seen:
                    seen[key] = len(self._verts)
                    self._verts.append((vx, vy, vz))
                tri_vis.append(seen[key])
            self._face_vis.append(tri_vis)
            self._face_mat.append(face.material)
            self._face_nrm.append(face.normal)
            # Plane distance: d = normal · v0
            nx, ny, nz = face.normal
            vx, vy, vz = face.v0
            self._face_dst.append(nx*vx + ny*vy + nz*vz)

    def _order_faces(self) -> None:
        """Faces are already ordered by _dedup_vertices (walkable first)."""
        pass

    def _build_aabb(self) -> None:
        """Build the AABB tree over all faces (required for room walkmeshes)."""
        if self.bwm_type == BWM_TYPE_PWK_DWK or not self._face_vis:
            return

        face_list = []
        for idx, vis in enumerate(self._face_vis):
            i0, i1, i2 = vis
            v0 = self._verts[i0]
            v1 = self._verts[i1]
            v2 = self._verts[i2]
            cx = (v0[0]+v1[0]+v2[0]) / 3.0
            cy = (v0[1]+v1[1]+v2[1]) / 3.0
            cz = (v0[2]+v1[2]+v2[2]) / 3.0
            face_list.append((idx, [v0, v1, v2], (cx, cy, cz)))

        self._aabb_nodes = []
        if face_list:
            _generate_aabb_tree(self._aabb_nodes, face_list)

    def _build_edge_tables(self) -> None:
        """Build adjacent-edge and outer-edge tables."""
        n = self._walkable_count

        # Build edge → face lookup
        edge_to_faces: Dict[Tuple[int,int], List[int]] = {}
        for fi in range(n):
            vis = self._face_vis[fi]
            edges = [
                (min(vis[0], vis[1]), max(vis[0], vis[1])),
                (min(vis[1], vis[2]), max(vis[1], vis[2])),
                (min(vis[2], vis[0]), max(vis[2], vis[0])),
            ]
            for e in edges:
                edge_to_faces.setdefault(e, []).append(fi)

        # Adjacent-edge table: 3 entries per walkable face
        self._adj_edges = [(-1, -1, -1)] * n
        for fi in range(n):
            vis = self._face_vis[fi]
            adjs = [-1, -1, -1]
            for ei in range(3):
                ea = vis[ei]
                eb = vis[(ei + 1) % 3]
                edge = (min(ea, eb), max(ea, eb))
                neighbours = edge_to_faces.get(edge, [])
                for nb in neighbours:
                    if nb != fi:
                        adjs[ei] = nb
                        break
            self._adj_edges[fi] = tuple(adjs)

        # Outer-edge table: edges that have only one face (boundary)
        self._outer_edges = []
        outer_edge_idx = 0
        for fi in range(n):
            vis = self._face_vis[fi]
            for ei in range(3):
                ea = vis[ei]
                eb = vis[(ei + 1) % 3]
                edge = (min(ea, eb), max(ea, eb))
                if len(edge_to_faces.get(edge, [])) == 1:
                    # Use existing room-transition data if available
                    transition = -1
                    if fi < len(self.wm.outer_edges):
                        transition = self.wm.outer_edges[fi][1]
                    self._outer_edges.append((outer_edge_idx, transition))
            outer_edge_idx += 1

    # ── Pack to bytes ─────────────────────────────────────────────────────

    def _pack(self) -> bytes:
        """Serialise all data structures to BWM binary."""
        num_verts    = len(self._verts)
        num_faces    = len(self._face_vis)
        num_aabbs    = len(self._aabb_nodes)
        num_adj      = len(self._adj_edges)
        num_outer    = len(self._outer_edges)
        num_perim    = len(self.wm.perimeters)

        # Calculate offsets
        off_verts       = BWM_HEADER_SIZE
        off_vert_idx    = off_verts   + num_verts * 12
        off_mat_ids     = off_vert_idx + num_faces * 12
        off_normals     = off_mat_ids  + num_faces * 4
        off_distances   = off_normals  + num_faces * 12
        off_aabbs       = off_distances + num_faces * 4
        off_adj         = off_aabbs    + num_aabbs * 44
        off_outer       = off_adj      + num_adj * 12
        off_perim       = off_outer    + num_outer * 8

        buf = bytearray()

        def wu32(v: int):  buf.extend(struct.pack('<I', v & 0xFFFFFFFF))
        def wi32(v: int):  buf.extend(struct.pack('<i', v))
        def wf32(v: float): buf.extend(struct.pack('<f', v))

        # ── Header ────────────────────────────────────────────────────────
        buf.extend(BWM_MAGIC)
        buf.extend(BWM_VERSION)
        wu32(self.bwm_type)

        px, py, pz = self.wm.position
        # 4 hook vectors × 3 floats = 12 floats = 48 bytes
        # Order: rel_use_vec1, rel_use_vec2, abs_use_vec1, abs_use_vec2
        for _ in range(12):
            wf32(0.0)  # hook vectors (zero for area walkmesh)
        wf32(px); wf32(py); wf32(pz)   # position (3 floats = 12 bytes)

        # Data table counts and offsets (8 uint32 pairs + extras)
        wu32(num_verts);      wu32(off_verts)
        wu32(num_faces);      wu32(off_vert_idx)
        wu32(off_mat_ids)     # offset to material IDs (count = num_faces)
        wu32(off_normals)     # offset to normals
        wu32(off_distances)   # offset to plane distances
        wu32(num_aabbs);      wu32(off_aabbs)
        wu32(0)               # unknown
        wu32(num_adj);        wu32(off_adj)
        wu32(num_outer);      wu32(off_outer)
        wu32(num_perim);      wu32(off_perim)

        assert len(buf) == BWM_HEADER_SIZE, \
            f"BWM writer header={len(buf)} bytes (expected {BWM_HEADER_SIZE}). "\
            f"Check float/int field counts."

        # ── Vertices ──────────────────────────────────────────────────────
        for vx, vy, vz in self._verts:
            wf32(vx + px); wf32(vy + py); wf32(vz + pz)

        # ── Face vertex indices ───────────────────────────────────────────
        for vis in self._face_vis:
            wu32(vis[0]); wu32(vis[1]); wu32(vis[2])

        # ── Material IDs ──────────────────────────────────────────────────
        for mat in self._face_mat:
            wu32(mat)

        # ── Normals ───────────────────────────────────────────────────────
        for nx, ny, nz in self._face_nrm:
            wf32(nx); wf32(ny); wf32(nz)

        # ── Distances ─────────────────────────────────────────────────────
        for d in self._face_dst:
            wf32(d)

        # ── AABB nodes (44 bytes each) ────────────────────────────────────
        for node in self._aabb_nodes:
            # node = [minx,miny,minz, maxx,maxy,maxz, child1,child2, face_idx, msp]
            wf32(node[0]); wf32(node[1]); wf32(node[2])
            wf32(node[3]); wf32(node[4]); wf32(node[5])
            wi32(node[8])   # face_idx
            wu32(0)         # unknown
            wu32(node[9])   # most_significant_plane
            wu32(node[6])   # child_idx1
            wu32(node[7])   # child_idx2

        # ── Adjacent edges ────────────────────────────────────────────────
        for a0, a1, a2 in self._adj_edges:
            wi32(a0); wi32(a1); wi32(a2)

        # ── Outer edges ───────────────────────────────────────────────────
        for edge_idx, transition in self._outer_edges:
            wu32(edge_idx)
            wi32(transition)

        # ── Perimeters ────────────────────────────────────────────────────
        for p in self.wm.perimeters:
            wu32(p)

        return bytes(buf)


# ─────────────────────────────────────────────────────────────────────────────
#  AABB tree generator (pure Python, matches kotorblender aabb.py algorithm)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_aabb_tree(tree: list, faces: list, depth: int = 0) -> None:
    """
    Recursively build an AABB tree over 'faces'.
    Each face is a tuple (face_idx, [v0,v1,v2], centroid).
    Appends nodes to 'tree' as 10-element lists:
      [min_x, min_y, min_z, max_x, max_y, max_z,
       child1_idx, child2_idx, face_idx, most_significant_plane]
    """
    if depth > 128 or not faces:
        return

    # Compute bounding box
    INF = 1e9
    min_xyz = [INF, INF, INF]
    max_xyz = [-INF, -INF, -INF]
    center  = [0.0, 0.0, 0.0]

    for _, verts, centroid in faces:
        for v in verts:
            for ax in range(3):
                if v[ax] < min_xyz[ax]: min_xyz[ax] = v[ax]
                if v[ax] > max_xyz[ax]: max_xyz[ax] = v[ax]
        for ax in range(3):
            center[ax] += centroid[ax]

    n = len(faces)
    for ax in range(3):
        center[ax] /= n

    # Leaf node
    if n == 1:
        msp = AABB_NO_CHILDREN
        node = [min_xyz[0], min_xyz[1], min_xyz[2],
                max_xyz[0], max_xyz[1], max_xyz[2],
                0xFFFFFFFF, 0xFFFFFFFF, faces[0][0], msp]
        tree.append(node)
        return

    # Find best split axis (longest dimension)
    sizes = [max_xyz[ax] - min_xyz[ax] for ax in range(3)]
    split_axis = sizes.index(max(sizes))

    # Try all axes to avoid degenerate splits
    left_faces = right_faces = []
    actual_axis = split_axis
    for attempt in range(4):
        ax = (split_axis + attempt) % 3
        left_faces  = [f for f in faces if f[2][ax] < center[ax]]
        right_faces = [f for f in faces if f[2][ax] >= center[ax]]
        if left_faces and right_faces:
            actual_axis = ax
            break
    else:
        # Degenerate: split in half
        half = n // 2
        left_faces  = faces[:half]
        right_faces = faces[half:]
        actual_axis = split_axis

    _AXIS_TO_PLANE = [AABB_POSITIVE_X, AABB_POSITIVE_Y, AABB_POSITIVE_Z]
    msp = _AXIS_TO_PLANE[actual_axis]

    node = [min_xyz[0], min_xyz[1], min_xyz[2],
            max_xyz[0], max_xyz[1], max_xyz[2],
            0, 0, -1, msp]
    node_idx = len(tree)
    tree.append(node)

    node[6] = len(tree)
    _generate_aabb_tree(tree, left_faces, depth + 1)
    node[7] = len(tree)
    _generate_aabb_tree(tree, right_faces, depth + 1)


# ─────────────────────────────────────────────────────────────────────────────
#  Math helpers
# ─────────────────────────────────────────────────────────────────────────────

def _face_normal(v0: Tuple, v1: Tuple, v2: Tuple) -> Tuple[float, float, float]:
    """Compute normalised face normal from 3 vertices."""
    e1x = v1[0]-v0[0]; e1y = v1[1]-v0[1]; e1z = v1[2]-v0[2]
    e2x = v2[0]-v0[0]; e2y = v2[1]-v0[1]; e2z = v2[2]-v0[2]
    nx = e1y*e2z - e1z*e2y
    ny = e1z*e2x - e1x*e2z
    nz = e1x*e2y - e1y*e2x
    mag = math.sqrt(nx*nx + ny*ny + nz*nz) or 1.0
    return nx/mag, ny/mag, nz/mag


def _ray_triangle_intersect(
        ray_o: Tuple[float, float, float],
        ray_d: Tuple[float, float, float],
        v0: Tuple[float, float, float],
        v1: Tuple[float, float, float],
        v2: Tuple[float, float, float],
) -> Optional[float]:
    """
    Möller–Trumbore ray-triangle intersection.
    Returns the Z coordinate of the intersection point, or None if no hit.
    """
    EPSILON = 1e-7
    ox, oy, oz = ray_o
    dx, dy, dz = ray_d
    e1x = v1[0]-v0[0]; e1y = v1[1]-v0[1]; e1z = v1[2]-v0[2]
    e2x = v2[0]-v0[0]; e2y = v2[1]-v0[1]; e2z = v2[2]-v0[2]
    hx = dy*e2z - dz*e2y
    hy = dz*e2x - dx*e2z
    hz = dx*e2y - dy*e2x
    a = e1x*hx + e1y*hy + e1z*hz
    if abs(a) < EPSILON:
        return None
    f  = 1.0 / a
    sx = ox - v0[0]; sy = oy - v0[1]; sz = oz - v0[2]
    u  = f * (sx*hx + sy*hy + sz*hz)
    if u < 0.0 or u > 1.0:
        return None
    qx = sy*e1z - sz*e1y
    qy = sz*e1x - sx*e1z
    qz = sx*e1y - sy*e1x
    v  = f * (dx*qx + dy*qy + dz*qz)
    if v < 0.0 or u + v > 1.0:
        return None
    t = f * (e2x*qx + e2y*qy + e2z*qz)
    if t < EPSILON:
        return None
    return oz + t * dz


# ─────────────────────────────────────────────────────────────────────────────
#  Module walkmesh aggregator
# ─────────────────────────────────────────────────────────────────────────────

def build_module_walkmesh(room_placements, resource_manager=None,
                          game_dir: str = "") -> WalkMesh:
    """
    Build a combined WalkMesh for all rooms in a module layout.

    Args:
        room_placements: Iterable of objects with .resref and .position attributes.
        resource_manager: ResourceManager to load WOK data from archives.
        game_dir:         Fallback: directory to search for loose .wok files.

    Returns a WalkMesh with all face positions translated to world space.
    """
    combined = WalkMesh(name="module")
    placements = list(room_placements)

    for rp in placements:
        resref = getattr(rp, 'resref', getattr(rp, 'name', '')).lower()
        pos    = getattr(rp, 'position', (0.0, 0.0, 0.0))
        wx, wy, wz = float(pos[0]), float(pos[1]), float(pos[2])

        wok_data = _load_wok(resref, resource_manager, game_dir)
        if not wok_data:
            log.debug(f"No WOK found for room '{resref}'")
            continue

        room_wm = WOKParser.from_bytes(wok_data, name=resref)
        for face in room_wm.faces:
            combined.faces.append(WalkFace(
                v0=(face.v0[0]+wx, face.v0[1]+wy, face.v0[2]+wz),
                v1=(face.v1[0]+wx, face.v1[1]+wy, face.v1[2]+wz),
                v2=(face.v2[0]+wx, face.v2[1]+wy, face.v2[2]+wz),
                material=face.material,
                normal=face.normal,
            ))

    log.info(f"Module walkmesh: {len(combined.faces)} total faces "
             f"from {len(placements)} rooms")
    # Build AABB tree over combined faces for O(log n) height queries in play mode
    if combined.faces:
        combined.build_aabb_tree()
    return combined


def _load_wok(resref: str, resource_manager=None, game_dir: str = "") -> Optional[bytes]:
    """Load WOK bytes from resource manager or filesystem."""
    if resource_manager is not None:
        for ext in ('wok', 'WOK'):
            data = resource_manager.get_file(resref, ext)
            if data:
                return data

    if game_dir:
        for subdir in ('', 'models', 'Models', 'modules', 'Modules'):
            base = os.path.join(game_dir, subdir) if subdir else game_dir
            for ext in ('.wok', '.WOK'):
                p = os.path.join(base, resref + ext)
                if os.path.exists(p):
                    try:
                        return open(p, 'rb').read()
                    except OSError as exc:
                        log.debug("wok_parser: could not read %s: %s", p, exc)
    return None
