"""
GModular — KotOR WOK (Walkmesh) Parser
========================================
Parses KotOR room walkmesh (.wok) binary files.

WOK files share the same binary format as MDL mesh nodes but without
the full model header — they are stored as raw trimesh data. The room
walkmesh defines which triangles are walkable for pathfinding.

Format reference:
  https://kotor-modding.fandom.com/wiki/MDL_Format
  xoreos src/graphics/aurora/walkmesh.cpp
  The WOK is essentially an AABB-node mesh extracted from the room MDL.

WOK walkability is encoded via surface material IDs in each face:
  The material field in the face struct references surfacemat.2da.
  Rows 0-6 are typically walkable; 7+ typically non-walkable.

Key surfacemat.2da walkability flags (column: Walk):
  0  = Dirt         (walkable)
  1  = Obscuring    (walkable)
  2  = Grass        (walkable)
  3  = Stone        (walkable)
  4  = Wood         (walkable)
  5  = Water        (walkable)
  6  = NonWalk      (NOT walkable)
  7  = Transparent  (NOT walkable)
  8  = Carpet       (walkable)
  9  = Metal        (walkable)
  10 = Puddles      (walkable)
  11 = Swamp        (walkable)
  12 = Mud          (walkable)
  13 = Leaves       (walkable)
  14 = Lava         (NOT walkable)
  15 = BottomlessPit (NOT walkable)
  16 = DeepWater    (NOT walkable)
  17 = Door         (walkable when open)
  18 = Snow         (walkable)
  19 = Sand         (walkable)

GModular uses the WOK data to:
  1. Render the walkmesh overlay (walkable=green, non-walkable=red)
  2. Constrain NPC/player pathfinding in play-mode
  3. Detect valid placement positions for objects
"""
from __future__ import annotations

import struct
import math
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path

log = logging.getLogger(__name__)

# ── Walkability material map ──────────────────────────────────────────────────
# True = walkable, False = not walkable
# Indexed by surfacemat.2da row ID (0-based)
_SURF_WALKABLE: List[bool] = [
    True,   # 0  Dirt
    True,   # 1  Obscuring
    True,   # 2  Grass
    True,   # 3  Stone
    True,   # 4  Wood
    True,   # 5  Water
    False,  # 6  NonWalk
    False,  # 7  Transparent
    True,   # 8  Carpet
    True,   # 9  Metal
    True,   # 10 Puddles
    True,   # 11 Swamp
    True,   # 12 Mud
    True,   # 13 Leaves
    False,  # 14 Lava
    False,  # 15 BottomlessPit
    False,  # 16 DeepWater
    True,   # 17 Door (walkable when open; we treat as walkable)
    True,   # 18 Snow
    True,   # 19 Sand
]

def is_walkable(material: int) -> bool:
    """Return True if the given surface material ID is walkable."""
    if 0 <= material < len(_SURF_WALKABLE):
        return _SURF_WALKABLE[material]
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WalkFace:
    """A single walkmesh triangle."""
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
class WalkMesh:
    """
    Parsed walkmesh for a single room.

    Contains the list of WalkFace objects and utility methods for
    ray-triangle intersection (for placement height queries).
    """
    name:  str = ""
    faces: List[WalkFace] = field(default_factory=list)

    @property
    def walkable_faces(self) -> List[WalkFace]:
        return [f for f in self.faces if f.walkable]

    @property
    def non_walkable_faces(self) -> List[WalkFace]:
        return [f for f in self.faces if not f.walkable]

    @property
    def face_count(self) -> int:
        return len(self.faces)

    def height_at(self, x: float, y: float) -> Optional[float]:
        """
        Return the Z height of the walkmesh at (x, y) by casting a ray downward.
        Checks only walkable faces. Returns None if no face is hit.
        """
        ray_origin = (x, y, 1000.0)
        ray_dir    = (0.0, 0.0, -1.0)
        best_z: Optional[float] = None

        for face in self.walkable_faces:
            z = _ray_triangle_intersect(ray_origin, ray_dir,
                                        face.v0, face.v1, face.v2)
            if z is not None:
                if best_z is None or z > best_z:
                    best_z = z

        return best_z

    def height_at_any(self, x: float, y: float) -> Optional[float]:
        """
        Like height_at() but considers ALL faces (walkable and non-walkable).
        Useful for finding the ground elevation at a position.
        """
        ray_origin = (x, y, 1000.0)
        ray_dir    = (0.0, 0.0, -1.0)
        best_z: Optional[float] = None

        for face in self.faces:
            z = _ray_triangle_intersect(ray_origin, ray_dir,
                                        face.v0, face.v1, face.v2)
            if z is not None:
                if best_z is None or z > best_z:
                    best_z = z

        return best_z

    def face_at(self, x: float, y: float, walkable_only: bool = True
                ) -> Optional[WalkFace]:
        """
        Return the WalkFace under (x, y), or None.
        If walkable_only=True, only walkable faces are searched.
        """
        ray_origin = (x, y, 1000.0)
        ray_dir    = (0.0, 0.0, -1.0)
        best_z: Optional[float] = None
        best_face: Optional[WalkFace] = None

        candidates = self.walkable_faces if walkable_only else self.faces
        for face in candidates:
            z = _ray_triangle_intersect(ray_origin, ray_dir,
                                        face.v0, face.v1, face.v2)
            if z is not None:
                if best_z is None or z > best_z:
                    best_z = z
                    best_face = face

        return best_face

    def surface_material_at(self, x: float, y: float) -> int:
        """
        Return the surface material ID of the face under (x, y).
        Returns -1 if no face is found.
        """
        face = self.face_at(x, y, walkable_only=False)
        return face.material if face is not None else -1

    def clamp_to_walkmesh(self, x: float, y: float,
                           search_radius: float = 2.0) -> Optional[Tuple[float, float, float]]:
        """
        Snap (x, y) to the nearest walkable face center if the position itself
        is not walkable. Returns (x, y, z) or None if nothing walkable found.

        Useful for NPC placement and pathfinding spawn-point snapping.
        """
        # First try exact position
        z = self.height_at(x, y)
        if z is not None:
            return (x, y, z)

        # Search walkable face centers within radius
        best_dist = search_radius
        best_pos: Optional[Tuple[float, float, float]] = None
        for face in self.walkable_faces:
            cx, cy, cz = face.center
            dx = cx - x; dy = cy - y
            d = math.sqrt(dx*dx + dy*dy)
            if d < best_dist:
                best_dist = d
                best_pos  = (cx, cy, cz)

        return best_pos

    def bounds(self) -> Tuple[Tuple[float,float,float], Tuple[float,float,float]]:
        """Return (bb_min, bb_max) world-space bounding box of the walkmesh."""
        if not self.faces:
            return (0.0,0.0,0.0), (0.0,0.0,0.0)
        xs, ys, zs = [], [], []
        for f in self.faces:
            for v in (f.v0, f.v1, f.v2):
                xs.append(v[0]); ys.append(v[1]); zs.append(v[2])
        return (min(xs),min(ys),min(zs)), (max(xs),max(ys),max(zs))

    def walkable_region_center(self) -> Optional[Tuple[float, float, float]]:
        """Return the centroid of all walkable face centers."""
        wf = self.walkable_faces
        if not wf:
            return None
        cx = sum(f.center[0] for f in wf) / len(wf)
        cy = sum(f.center[1] for f in wf) / len(wf)
        cz = sum(f.center[2] for f in wf) / len(wf)
        return (cx, cy, cz)

    def material_counts(self) -> dict:
        """Return a dict mapping surface material ID → face count."""
        counts: dict = {}
        for f in self.faces:
            counts[f.material] = counts.get(f.material, 0) + 1
        return counts

    def is_position_walkable(self, x: float, y: float,
                             tolerance: float = 0.5) -> bool:
        """
        Return True if the XY position is over a walkable face.
        Tolerance: how far above/below the face to search (meters).
        """
        return self.height_at(x, y) is not None

    def walk_tris(self) -> List[Tuple]:
        """Return walkable triangles as ((v0,v1,v2), ...) tuples."""
        return [f.as_tuple() for f in self.walkable_faces]

    def nowalk_tris(self) -> List[Tuple]:
        """Return non-walkable triangles as ((v0,v1,v2), ...) tuples."""
        return [f.as_tuple() for f in self.non_walkable_faces]


# ─────────────────────────────────────────────────────────────────────────────
#  WOK Binary Parser
# ─────────────────────────────────────────────────────────────────────────────

class WOKParser:
    """
    Parses KotOR binary .wok walkmesh files.

    The WOK format is structurally identical to an MDL AABB mesh node but
    stored as a standalone file starting with a simplified header:

    WOK File Layout:
      The binary layout is the same as the MDL binary format (same BASE=12,
      same geometry header at BASE+0), but the root node is always an AABB
      mesh type. We reuse MDLParser internals to avoid duplication.

    Usage::
        wm = WOKParser.from_bytes(wok_bytes)
        wm = WOKParser.from_file("room_name.wok")
        walk_tris = wm.walk_tris()
        nowalk_tris = wm.nowalk_tris()
    """

    @staticmethod
    def from_bytes(data: bytes, name: str = "walkmesh") -> WalkMesh:
        """Parse WOK from bytes. Returns a WalkMesh."""
        return WOKParser._parse(data, name)

    @staticmethod
    def from_file(path: str) -> WalkMesh:
        """Load and parse a .wok file. Returns a WalkMesh."""
        name = Path(path).stem.lower()
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            log.error(f"WOK: cannot read {path!r}: {e}")
            return WalkMesh(name=name)
        return WOKParser._parse(data, name)

    @staticmethod
    def _parse(data: bytes, name: str) -> WalkMesh:
        wm = WalkMesh(name=name)

        try:
            from .mdl_parser import MDLParser
            parser = MDLParser(data, b'')
            mesh_data = parser.parse()

            all_nodes = mesh_data.all_nodes()
            for node in all_nodes:
                if not node.vertices or not node.faces:
                    continue

                n_verts = len(node.vertices)
                has_materials = len(node.face_materials) == len(node.faces)

                for face_idx, f in enumerate(node.faces):
                    v0i, v1i, v2i = int(f[0]), int(f[1]), int(f[2])
                    if v0i >= n_verts or v1i >= n_verts or v2i >= n_verts:
                        continue

                    v0 = node.vertices[v0i]
                    v1 = node.vertices[v1i]
                    v2 = node.vertices[v2i]

                    # Compute face normal
                    if node.normals and v0i < len(node.normals):
                        nx, ny, nz = node.normals[v0i]
                    else:
                        nx, ny, nz = _face_normal(v0, v1, v2)

                    # Get material from parsed face data (reliable)
                    if has_materials:
                        mat = node.face_materials[face_idx]
                    else:
                        # Heuristic: upward-facing = walkable floor
                        mat = 0 if nz > 0.5 else 6

                    wm.faces.append(WalkFace(
                        v0=v0, v1=v1, v2=v2,
                        material=mat,
                        normal=(nx, ny, nz),
                    ))

        except Exception as e:
            log.warning(f"WOK parse error for '{name}': {e}", exc_info=False)

        log.debug(f"WOK '{name}': {len(wm.faces)} faces, "
                  f"{len(wm.walkable_faces)} walkable, "
                  f"{len(wm.non_walkable_faces)} non-walkable")
        return wm


# ─────────────────────────────────────────────────────────────────────────────
#  Math helpers
# ─────────────────────────────────────────────────────────────────────────────

def _face_normal(v0, v1, v2) -> Tuple[float, float, float]:
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
        room_placements: Iterable of RoomPlacement (or objects with
                         .resref and .position attributes).
        resource_manager: ResourceManager to load WOK data from archives.
        game_dir:         Fallback: directory to search for loose .wok files.

    Returns a WalkMesh with all face positions translated to world space.
    """
    combined = WalkMesh(name="module")

    for rp in room_placements:
        resref = getattr(rp, 'resref', getattr(rp, 'name', '')).lower()
        pos    = getattr(rp, 'position', (0.0, 0.0, 0.0))
        wx, wy, wz = float(pos[0]), float(pos[1]), float(pos[2])

        wok_data = _load_wok(resref, resource_manager, game_dir)
        if not wok_data:
            continue

        room_wm = WOKParser.from_bytes(wok_data, name=resref)

        # Translate all faces to world space
        for face in room_wm.faces:
            combined.faces.append(WalkFace(
                v0=(face.v0[0]+wx, face.v0[1]+wy, face.v0[2]+wz),
                v1=(face.v1[0]+wx, face.v1[1]+wy, face.v1[2]+wz),
                v2=(face.v2[0]+wx, face.v2[1]+wy, face.v2[2]+wz),
                material=face.material,
                normal=face.normal,
            ))

    log.info(f"Module walkmesh: {len(combined.faces)} total faces from "
             f"{len(list(room_placements) if hasattr(room_placements, '__len__') else '?')} rooms")
    return combined


def _load_wok(resref: str, resource_manager=None, game_dir: str = "") -> Optional[bytes]:
    """Load WOK bytes from resource manager or filesystem."""
    if resource_manager is not None:
        data = resource_manager.get_file(resref, 'wok')
        if data:
            return data

    # Fallback: filesystem
    if game_dir:
        for subdir in ('', 'models', 'Models'):
            base = os.path.join(game_dir, subdir) if subdir else game_dir
            for ext in ('.wok', '.WOK'):
                p = os.path.join(base, resref + ext)
                if os.path.exists(p):
                    try:
                        with open(p, 'rb') as f:
                            return f.read()
                    except OSError:
                        pass
    return None


import os  # needed for _load_wok
