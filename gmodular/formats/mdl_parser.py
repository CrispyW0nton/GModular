"""
GModular — KotOR MDL/MDX Binary Parser
Adapted from GhostRigger's MDLBinaryParser for use in GModular's preview engine.

Parses KotOR 1/2 binary .mdl + .mdx pairs into MeshData objects that
the GModular viewport can upload to OpenGL.

Key differences from GhostRigger's version:
  - Returns lightweight MeshData / ModelMesh instead of full KotorModel
  - No animation engine dependency (animations available but optional)
  - Designed to be imported by viewport.py without pulling in GhostRigger

Binary format reference:
  - KoTOR1MDL.bt / KoTOR2MDL.bt by Enrico Horn (Farmboy0)
  - cchargin's MDL format specification (mdl_info.html)
  - xoreos model_kotor.cpp (authoritative open-source implementation)
  - KotOR Modding Wiki MDL Format page
  - Deadlystream: Kotor/TSL Model Format Technical Details

Verified against KotOR1 and KotOR2 game assets and the xoreos source.
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
#  Node flag constants (content_node bitfield — matches KotOR1MDL.bt / xoreos)
# ─────────────────────────────────────────────────────────────────────────────

NODE_HEADER  = 0x0001   # has_header (base node — always set)
NODE_LIGHT   = 0x0002   # has_light  (extra 0x5C bytes; xoreos skips)
NODE_EMITTER = 0x0004   # has_emitter (particle emitter — variable size)
NODE_CAMERA  = 0x0008   # has_camera  (unused in KotOR; skip 0x50)
NODE_REF     = 0x0010   # has_reference (super-model reference; skip 0x44)
NODE_MESH    = 0x0020   # has_mesh (trimesh geometry; 332 or 340 bytes)
NODE_SKIN    = 0x0040   # has_skin (skinmesh; follows mesh; 100 bytes K1)
NODE_ANIM    = 0x0080   # has_anim (animation node; skip 0x38)
NODE_DANGLY  = 0x0100   # has_dangly (dangly mesh; follows mesh; 28 bytes)
NODE_AABB    = 0x0200   # has_aabb (walkmesh AABB tree; 4-byte root ptr)
NODE_SABER   = 0x0800   # has_lightsaber_effect (saber mesh)

# Controller type IDs (from xoreos + cchargin spec + Kotor.NET MDLBinaryControllerType.cs)
CTRL_POSITION    = 8    # position keyframes (3 floats per row)
CTRL_ORIENTATION = 20   # orientation keyframes (4 floats per row, wxyz OR compressed 2-col)
CTRL_SCALE       = 36   # scale (1 float)
CTRL_SELF_ILLUM  = 100  # mesh self-illumination colour (3 floats RGB)
CTRL_ALPHA       = 132  # mesh alpha (from Kotor.NET: type 132, not 128)
CTRL_ALPHA_OLD   = 128  # legacy alpha type seen in some K1 assets

# Light node controller IDs (Kotor.NET MDLBinaryControllerType.cs — Light section)
CTRL_LIGHT_COLOR              = 76   # light colour (3 floats RGB)
CTRL_LIGHT_RADIUS             = 88   # light radius (1 float)
CTRL_LIGHT_SHADOW_RADIUS      = 96   # shadow radius (1 float)
CTRL_LIGHT_VERT_DISPLACEMENT  = 100  # vertical displacement (1 float)
CTRL_LIGHT_MULTIPLIER         = 140  # intensity multiplier (1 float)

# Emitter controller IDs (Kotor.NET MDLBinaryControllerType.cs — Emitter section)
CTRL_EMITTER_ALPHA_END   = 80
CTRL_EMITTER_ALPHA_START = 84
CTRL_EMITTER_BIRTHRATE   = 88
CTRL_EMITTER_FPS         = 104
CTRL_EMITTER_FRAME_END   = 108
CTRL_EMITTER_FRAME_START = 112
CTRL_EMITTER_GRAVITY     = 116
CTRL_EMITTER_LIFE_EXP    = 120
CTRL_EMITTER_MASS        = 124
CTRL_EMITTER_SIZE_START  = 144
CTRL_EMITTER_SIZE_END    = 148
CTRL_EMITTER_VELOCITY    = 168
CTRL_EMITTER_X_SIZE      = 172
CTRL_EMITTER_Y_SIZE      = 176
CTRL_EMITTER_SPREAD      = 160
CTRL_EMITTER_COLOR_START = 392
CTRL_EMITTER_COLOR_MID   = 284
CTRL_EMITTER_COLOR_END   = 380

# MDX vertex channel bitmask flags (Kotor.NET MDLBinaryMDXVertexBitmask.cs)
MDX_FLAG_POSITION = 0x0001  # XYZ position (3 floats)
MDX_FLAG_UV1      = 0x0002  # Texture UV1 (2 floats)
MDX_FLAG_UV2      = 0x0004  # Texture UV2 / lightmap UVs (2 floats)
MDX_FLAG_UV3      = 0x0008  # UV3
MDX_FLAG_UV4      = 0x0010  # UV4
MDX_FLAG_NORMALS  = 0x0020  # Normals (3 floats)
MDX_FLAG_COLOURS  = 0x0040  # Vertex colours (4 floats RGBA)
MDX_FLAG_TANGENT1 = 0x0080  # Tangent / bump 1
MDX_FLAG_TANGENT2 = 0x0100  # Tangent / bump 2
MDX_FLAG_TANGENT3 = 0x0200  # Tangent / bump 3
MDX_FLAG_TANGENT4 = 0x0400  # Tangent / bump 4

# Surface material IDs (Kotor.NET SurfaceMaterial.cs — matches surfacemat.2da)
SURF_UNDEFINED    = 0
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
SURF_TRIGGER      = 30

# Non-walkable surface IDs (any of these means the face blocks movement)
_NON_WALKABLE_MATS = frozenset([
    SURF_NONWALK, SURF_TRANSPARENT, SURF_LAVA, SURF_BOTTOMLESSPIT,
    SURF_DEEPWATER, SURF_TRIGGER, SURF_OBSCURING,
])


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
    vertices:       List[Tuple[float, float, float]] = field(default_factory=list)
    normals:        List[Tuple[float, float, float]] = field(default_factory=list)
    uvs:            List[Tuple[float, float]]        = field(default_factory=list)
    uvs2:           List[Tuple[float, float]]        = field(default_factory=list)  # lightmap UVs
    faces:          List[Tuple[int, int, int]]       = field(default_factory=list)
    face_materials: List[int]                        = field(default_factory=list)
    # face_materials[i] = surfacemat.2da row for faces[i] (walkmesh/AABB nodes)

    # Skinmesh bone data (NODE_SKIN nodes only)
    bone_indices: List[Tuple[int,int,int,int]]   = field(default_factory=list)  # per-vertex 4 bone indices
    bone_weights: List[Tuple[float,float,float,float]] = field(default_factory=list)  # per-vertex 4 weights
    bone_map:     List[int]                      = field(default_factory=list)  # bonemap[mdxBoneIdx] → nodeIndex
    bone_node_indices: List[int]                 = field(default_factory=list)  # up to 16 node indices

    # UV animation (from AnimateUV + UVDirection + UVSpeed in mesh header)
    uv_animate:   bool  = False
    uv_dir:       Tuple[float, float] = (0.0, 0.0)
    uv_speed:     float = 0.0
    uv_jitter:    float = 0.0

    # Mesh flags
    has_lightmap:      bool = False
    rotate_texture:    bool = False
    background_geom:   bool = False
    has_shadow:        bool = True
    beaming:           bool = False
    transparency_hint: int  = 0

    # Material
    texture:   str   = ""
    lightmap:  str   = ""   # lightmap texture name (Kotor.NET: Texture2)
    diffuse:   Tuple[float, float, float] = (0.8, 0.8, 0.8)
    ambient:   Tuple[float, float, float] = (0.2, 0.2, 0.2)
    alpha:     float = 1.0
    render:    bool  = True

    # Controller animation keyframes (from Kotor.NET BaseNode.cs)
    # Format: list of (time_key, [values...]) pairs, type-keyed
    controllers: Dict[int, List[Tuple[float, List[float]]]] = field(default_factory=dict)

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
    def is_light(self) -> bool:
        return bool(self.flags & NODE_LIGHT)

    @property
    def is_emitter(self) -> bool:
        return bool(self.flags & NODE_EMITTER)

    @property
    def is_dangly(self) -> bool:
        return bool(self.flags & NODE_DANGLY)

    @property
    def is_walkmesh(self) -> bool:
        """True when this is an AABB walkmesh node (faces have material IDs)."""
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

    @property
    def lightmap_clean(self) -> str:
        """Return lightmap texture name with null/garbage bytes stripped."""
        out = []
        for ch in (self.lightmap or ""):
            if 32 <= ord(ch) <= 126:
                out.append(ch)
            else:
                break
        return "".join(out).strip()

    def get_anim_position(self, time_key: float) -> Optional[Tuple[float, float, float]]:
        """Sample the position controller at time_key; returns interpolated position or None."""
        rows = self.controllers.get(CTRL_POSITION)
        if not rows:
            return None
        return _sample_vec3_controller(rows, time_key, self.position)

    def get_anim_rotation(self, time_key: float) -> Optional[Tuple[float, float, float, float]]:
        """Sample the orientation controller at time_key; returns interpolated rotation or None."""
        rows = self.controllers.get(CTRL_ORIENTATION)
        if not rows:
            return None
        return _sample_quat_controller(rows, time_key, self.rotation)

    def get_anim_alpha(self, time_key: float) -> float:
        """Sample the alpha controller at time_key; returns alpha value."""
        for ctype in (CTRL_ALPHA, CTRL_ALPHA_OLD):
            rows = self.controllers.get(ctype)
            if rows:
                for t, vals in rows:
                    if vals:
                        return float(vals[0])
        return self.alpha


@dataclass
class AnimationEvent:
    """A named event at a specific time within an animation (from MDLBinaryAnimationEvent)."""
    time:  float = 0.0
    name:  str   = ""


@dataclass
class AnimationData:
    """
    One named animation clip from an MDL file.

    Mirrors Kotor.NET Animation class (Graphics/Model/Animation.cs):
      Name, Length, Transition, Root node tree, Events list.
    """
    name:       str  = "default"
    length:     float = 0.0
    transition: float = 0.25  # blend-out transition time
    root_node:  Optional[MeshNode] = None
    events:     List[AnimationEvent] = field(default_factory=list)

    def find_node(self, node_name: str) -> Optional[MeshNode]:
        """Recursively find a node by name in this animation's node tree."""
        if self.root_node is None:
            return None
        stack = [self.root_node]
        while stack:
            n = stack.pop()
            if n.name == node_name:
                return n
            stack.extend(n.children)
        return None


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
    # Extra model header fields (from MDLBinaryModelHeader — Kotor.NET)
    model_type:     int   = 0      # ModelType byte (2 = geometry, etc.)
    fog:            bool  = False  # DisableFog flag
    animation_scale: float = 1.0  # AnimationScale float
    mdx_size:       int   = 0     # MDXSize (total MDX data size)
    child_model_count: int = 0    # ChildModelCount
    classification: str   = "other"  # human-readable model type
    # Animations (Kotor.NET: KModel.Animations list)
    animations:     List[AnimationData] = field(default_factory=list)

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
        """Render-worthy nodes: has vertices, render=True, not AABB walkmesh."""
        result = []
        for n in self.mesh_nodes():
            if n.vertices and n.render and not n.is_aabb:
                result.append(n)
        return result

    def walkmesh_nodes(self) -> List[MeshNode]:
        """AABB walkmesh nodes (flags & NODE_AABB). Faces have material IDs."""
        return [n for n in self.all_nodes() if n.is_aabb and n.vertices]

    def aabb_nodes(self) -> List[MeshNode]:
        """Alias for walkmesh_nodes()."""
        return self.walkmesh_nodes()

    def scan_textures(self) -> List[str]:
        """
        Return a deduplicated list of all non-empty, non-NULL texture names
        used across all mesh nodes in this model.

        Useful for discovering which TPC/TGA texture files a model depends on.
        """
        seen: set = set()
        result: List[str] = []
        for n in self.mesh_nodes():
            tx = n.texture_clean
            if tx and tx.upper() != 'NULL' and tx not in seen:
                seen.add(tx)
                result.append(tx)
        return result

    def compute_bounds(self):
        """
        Compute AABB from all mesh node vertices.

        KotOR MDL vertex positions are pre-transformed to world space (they are
        absolute coordinates baked during model compilation).  The node ``position``
        field (from the controller key array) describes the bind-pose transform
        used for animation—NOT an additional offset for static room geometry.

        For room models all node positions cancel out in the vertex data (vertices
        are stored in area-local absolute coordinates), so we skip ``_world_pos``
        accumulation and use raw vertex data directly.

        For character / placeable models where vertices ARE in node-local space
        (they typically have root node position = 0), using raw data still gives
        a correct overall AABB because the local positions are what's rendered.
        """
        all_verts: List[Tuple[float, float, float]] = []
        for n in self.mesh_nodes():
            if not n.vertices:
                continue
            all_verts.extend(n.vertices)
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


def _uncompress_quaternion(packed: int) -> Tuple[float, float, float, float]:
    """
    Decompress a 32-bit packed quaternion into (x, y, z, w).

    Matches Kotor.NET QuaternionExtensions.UncompressQuaternion():
      X = bits [0..10]  (11 bits), scale 1/1023, shift by -1
      Y = bits [11..21] (11 bits), scale 1/1023, shift by -1
      Z = bits [22..31] (10 bits), scale 1/511,  shift by -1
      W = sqrt(1 - x² - y² - z²) or normalised fallback
    """
    QUAT_X_MASK  = 0x07FF
    QUAT_Y_MASK  = 0x07FF
    QUAT_Z_MASK  = 0x03FF
    QUAT_X_SCALE = 1.0 / 1023.0
    QUAT_Z_SCALE = 1.0 / 511.0
    QUAT_Y_SHIFT = 11
    QUAT_Z_SHIFT = 22

    x = (packed & QUAT_X_MASK) * QUAT_X_SCALE - 1.0
    y = ((packed >> QUAT_Y_SHIFT) & QUAT_Y_MASK) * QUAT_X_SCALE - 1.0
    z = ((packed >> QUAT_Z_SHIFT) & QUAT_Z_MASK) * QUAT_Z_SCALE - 1.0
    fSq = x*x + y*y + z*z

    if fSq < 1e-10:
        return (0.0, 0.0, 0.0, 1.0)
    elif fSq < 1.0:
        w = -math.sqrt(1.0 - fSq)
        # Normalise
        mag = math.sqrt(fSq + w*w)
        if mag > 1e-9:
            return (x/mag, y/mag, z/mag, w/mag)
        return (0.0, 0.0, 0.0, 1.0)
    else:
        inv = 1.0 / math.sqrt(fSq)
        return (x*inv, y*inv, z*inv, 0.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _quat_slerp(qa, qb, t: float):
    """Spherical linear interpolation between two quaternions."""
    ax, ay, az, aw = qa
    bx, by, bz, bw = qb
    dot = ax*bx + ay*by + az*bz + aw*bw
    if dot < 0.0:
        bx, by, bz, bw = -bx, -by, -bz, -bw
        dot = -dot
    dot = min(dot, 1.0)
    if dot > 0.9995:
        # Linear interpolation + normalise
        rx = ax + t*(bx - ax)
        ry = ay + t*(by - ay)
        rz = az + t*(bz - az)
        rw = aw + t*(bw - aw)
    else:
        theta0 = math.acos(dot)
        theta  = theta0 * t
        sin0   = math.sin(theta0)
        sin_t  = math.sin(theta)
        s0 = math.cos(theta) - dot * sin_t / sin0
        s1 = sin_t / sin0
        rx = s0*ax + s1*bx
        ry = s0*ay + s1*by
        rz = s0*az + s1*bz
        rw = s0*aw + s1*bw
    mag = math.sqrt(rx*rx + ry*ry + rz*rz + rw*rw)
    if mag > 1e-9:
        rx /= mag; ry /= mag; rz /= mag; rw /= mag
    return (rx, ry, rz, rw)


def _sample_vec3_controller(
        rows: List[Tuple[float, List[float]]],
        t: float,
        default: Tuple[float,float,float]
) -> Tuple[float,float,float]:
    """
    Sample a vec3 controller (position / colour) at time t.
    Interpolates between adjacent keyframes (Kotor.NET BaseNode.GetAnimPosition).
    """
    if not rows:
        return default
    # Find surrounding keyframes
    row_a = None
    for row in rows:
        if row[0] <= t:
            row_a = row
        else:
            break
    if row_a is None:
        row_a = rows[-1]
    # Find next keyframe
    row_b = None
    for row in rows:
        if row[0] > row_a[0]:
            row_b = row
            break

    va = row_a[1]
    if len(va) < 3:
        return default
    if row_b is None:
        return (va[0], va[1], va[2])

    vb = row_b[1]
    if len(vb) < 3:
        return (va[0], va[1], va[2])

    dur = row_b[0] - row_a[0]
    if dur < 1e-9:
        return (va[0], va[1], va[2])
    w = (t - row_a[0]) / dur
    return (
        _lerp(va[0], vb[0], w),
        _lerp(va[1], vb[1], w),
        _lerp(va[2], vb[2], w),
    )


def _sample_quat_controller(
        rows: List[Tuple[float, List[float]]],
        t: float,
        default: Tuple[float,float,float,float]
) -> Tuple[float,float,float,float]:
    """
    Sample a quaternion controller (orientation) at time t.
    Uses SLERP for smooth interpolation (Kotor.NET BaseNode.GetAnimRotation).
    """
    if not rows:
        return default
    row_a = None
    for row in rows:
        if row[0] <= t:
            row_a = row
        else:
            break
    if row_a is None:
        row_a = rows[-1]
    row_b = None
    for row in rows:
        if row[0] > row_a[0]:
            row_b = row
            break

    def _row_quat(row) -> Tuple[float,float,float,float]:
        vals = row[1]
        if len(vals) >= 4:
            return (vals[0], vals[1], vals[2], vals[3])
        return default

    qa = _row_quat(row_a)
    if row_b is None:
        return qa

    qb = _row_quat(row_b)
    dur = row_b[0] - row_a[0]
    if dur < 1e-9:
        return qa
    w = (t - row_a[0]) / dur
    return _quat_slerp(qa, qb, w)


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


# ── Public header-read helper (shared with walkmesh_editor.py) ────────────────

def read_mdl_base_header(data: bytes, base: int = 12):
    """Extract name, bounding box, and geometry-section offset from a binary
    KotOR MDL/WOK file header.

    Both MDL and WOK files share the same binary geometry header layout:
      +0  FunctionPtr1   (4 bytes)
      +4  FunctionPtr2   (4 bytes)
      +8  ModelName[32]
      +40 RootNodeOffset (4 bytes, relative to *base*)
      +80 … ModelHeader extension …
      +80+24 BoundingBoxMin float[3]
      +80+36 BoundingBoxMax float[3]

    The ``base`` offset (default 12) is the byte at which the geometry header
    begins inside the file; it equals the value stored at file offset 4.

    Returns a dict with keys:
      ``model_data_off`` – value of file[4] (i.e. the *base*)
      ``name``           – ASCII model name (up to 32 chars)
      ``bb_min``         – (x, y, z) float tuple or (0,0,0) if unreadable
      ``bb_max``         – (x, y, z) float tuple or (0,0,0) if unreadable
      ``root_node_off``  – absolute root-node offset in file

    Raises ``ValueError`` if ``data`` is too small to contain a valid header.
    """
    if len(data) < base + 80 + 50:
        raise ValueError(f"MDL/WOK file too small for header (got {len(data)} bytes, "
                         f"need at least {base + 130})")

    name = _rstrip(data[base + 8: base + 40])
    root_node_rel = _ru32(data, base + 40)
    root_node_abs = base + root_node_rel if root_node_rel else 0

    # Model-header extension begins at base + 80
    M = base + 80
    try:
        bb_min = struct.unpack_from('<3f', data, M + 24)
        bb_max = struct.unpack_from('<3f', data, M + 36)
    except struct.error:
        bb_min = (0.0, 0.0, 0.0)
        bb_max = (0.0, 0.0, 0.0)

    return {
        "model_data_off": base,
        "name": name,
        "bb_min": bb_min,
        "bb_max": bb_max,
        "root_node_off": root_node_abs,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  MDL Binary Parser
# ─────────────────────────────────────────────────────────────────────────────

class MDLParser:
    """
    Parses a binary KotOR MDL + MDX pair into a MeshData object.

    Binary format: KoTOR1MDL.bt / KoTOR2MDL.bt by Enrico Horn (Farmboy0).

    File layout (offsets from start of file):
      [0..11]   header_file:  bin_mdl_id(4), mdl_length(4), mdx_length(4)
      [12..]    All other offsets stored in file are RELATIVE to byte 12 (BASE=12).

    Binary format verified against KotOR Modding Wiki MDL Format page.

    Geometry Header (at BASE+0, 80 bytes):
      FunctionPointer(4), FunctionPointer(4), ModelName[32], RootNodeOffset(4),
      NodeCount(4), Unknown[7](28), GeometryType(1), Padding[3]

    Model Header extension (at BASE+80, total model header = 168 bytes with geo):
      ModelType(1), Unknown(1), Padding(1), DisableFog(1),
      ChildModelCount(4), AnimationArrayOffset(4), AnimationCount(4), AnimCountDup(4),
      Unknown(4), BoundingBoxMin Float[3](12), BoundingBoxMax Float[3](12),
      Radius(4), AnimationScale(4), SupermodelName[32]
      --> Supermodel name at BASE+136 = (BASE+80)+56

    Post-model-header section (at BASE+168, 28 bytes):
      OffsetToRootNode(4), Unused(4), MDXFileSize(4), MDXOffset(4),
      NamesArrayOffset(4), NamesCount(4), NamesCountDup(4)
      --> NamesArrayOffset field at BASE+168+16 = BASE+184 (_NAMES_OFF)

    Node header (80 bytes) per KotOR Modding Wiki:
      NodeType(2), IndexNumber/NameIdx(2), NodeNumber/SeqIdx(2), Padding(2),
      RootNodeOffset(4), ParentNodeOffset(4), Position Float[3](12),
      Rotation Float[4](16),
      ChildArrayOffset(4), ChildCount(4), ChildCountDup(4),
      ControllerArrayOffset(4), ControllerCount(4), ControllerCountDup(4),
      ControllerDataOffset(4), ControllerDataCount(4), ControllerDataCountDup(4)

    Mesh header (332 bytes K1, 340 bytes K2) per KotOR Modding Wiki:
      FunctionPointer(4), FunctionPointer(4), FacesOffset(4), FacesCount(4),
      FacesCountDup(4), BoundingBoxMin Float[3](12), BoundingBoxMax Float[3](12),
      Radius(4), AveragePoint Float[3](12), DiffuseColour Float[3](12),
      AmbientColour Float[3](12), TransparencyHint(4), TextureName[32],
      LightmapName[32], Unknown[24], VertexIndicesCountArrayOffset(4),
      ...count(4), ...countdup(4), VertexOffsetsArrayOffset(4), ...count(4),
      ...countdup(4), InvertedCountersOffset(4), ...count(4), ...countdup(4),
      Unknown[3](12), SaberValues[8], Unknown(4), Unknown Float[4](16),
      MDXDataSize(4), MDXDataBitmap(4),
      [11 MDX channel offsets 4 bytes each = 44 bytes: Vertices, Normals, Colors,
       Texture1UVs, LightmapUVs, Texture2UVs, Texture3UVs, Unknown, Unk, Unk, Unk],
      VertexCount(2), TextureCount(2), HasLightmap(1), RotateTexture(1),
      BackgroundGeometry(1), HasShadow(1), Beaming(1), HasRender(1),
      Unknown(1), Unknown(1), TotalArea Float(4), Unknown(4),
      [K2 only: Unknown(4), Unknown(4)],
      MDXDataOffset(4), VerticesOffset(4)
      --> Total: 332 bytes (K1), 340 bytes (K2)

    Face struct (32 bytes):
      Normal Float[3](12), PlaneCoefficient Float(4), Material UInt32(4),
      FaceAdjacency[3] UInt16(6), VertexIndex[3] UInt16(6)
      --> VertexIndex at offset +26 within each 32-byte face

    Usage::
        mesh = MDLParser.parse_files("model.mdl", "model.mdx")
        parser = MDLParser(mdl_bytes, mdx_bytes)
        mesh   = parser.parse()
    """

    BASE = 12   # All MDL offsets stored in the file are relative to byte 12

    # ── Geometry header (at BASE) ─────────────────────────────────────────────
    # sizeof(header_geometry) = 4+4+32+4+4+12+12+4+1+3 = 80 bytes
    # (p_func1, p_func2, model_name[32], p_node_header, count_nodes,
    #  unknown1 array_def[12], unknown2 array_def[12], ref_count, type, padding[3])
    _GEO_HDR_SIZE    = 80    # sizeof(header_geometry) = 80 bytes (KotOR Modding Wiki)

    # Model header extension = 88 bytes (KotOR Modding Wiki: total model header = 168 bytes)
    # + post-header navigation section = 16 bytes (RootNodePtr+Unused+MDXSize+MDXOffset)
    # = 104 bytes total offset from _GEO_HDR_SIZE to _NAMES_OFF
    # This gives _NAMES_OFF = 80 + 104 = 184 (NamesArrayOffset field position)
    _MODEL_HDR_SIZE  = 104   # bytes from geo-header end to NamesArrayOffset field

    # NamesArrayOffset field position = BASE + 168 (geo+model) + 16 (nav section) = BASE+184
    _NAMES_OFF       = _GEO_HDR_SIZE + _MODEL_HDR_SIZE  # = 184

    # Supermodel name at BASE+136 = (BASE+80)+56
    _SUPERMODEL_OFF  = 56    # offset from M (=BASE+80) to supermodel_name[32]

    # Game version function pointer constants (fp1 value at BASE+0)
    # From MDLBinaryGeometryHeader.cs and MDLBinaryTrimeshHeader.cs (Kotor.NET rework)
    # Geometry/model headers:
    #   K1 PC:   model=4273776, anim=4273392
    #   K1 Xbox: model=4254992, anim=4253536
    #   K2 PC:   model=4285200, anim=4284816
    #   K2 Xbox: model=4285872, anim=4285488
    # Trimesh headers (mesh node fp1):
    #   K1 PC:   mesh=4216656, skin=4216592, dangly=4216640
    #   K1 Xbox: mesh=4267376, skin=4264032, dangly=4266736
    #   K2 PC:   mesh=4216880, skin=4216816, dangly=4216848
    #   K2 Xbox: mesh=4216576, skin=4216512, dangly=4216560
    _FP_K1 = frozenset([
        4273776, 4273392,          # K1 PC geometry
        4254992, 4253536,          # K1 Xbox geometry
        4216656, 4216592, 4216640, # K1 PC trimesh
        4267376, 4264032, 4266736, # K1 Xbox trimesh
    ])
    _FP_K2 = frozenset([
        4285200, 4284816,          # K2 PC geometry
        4285872, 4285488,          # K2 Xbox geometry
        4216880, 4216816, 4216848, # K2 PC trimesh
        4216576, 4216512, 4216560, # K2 Xbox trimesh
    ])
    # For per-mesh K2 detection (trimesh fp1 values per Kotor.NET MDLBinaryTrimeshHeader)
    _FP_K2_MESH = frozenset([4216880, 4216816, 4216848, 4216576, 4216512, 4216560])

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

        if len(d) < B + self._NAMES_OFF + 12:
            raise ValueError(f"MDL file too small ({len(d)} bytes)")

        # ── Geometry header at BASE+0 ─────────────────────────────────────────
        # Per KotOR Modding Wiki + Kotor.NET MDLBinaryGeometryHeader.cs:
        # Geometry Header (80 bytes at BASE+0):
        #   FunctionPointer1(4), FunctionPointer2(4), ModelName[32],
        #   RootNodeOffset(4), NodeCount(4), Unknown[7](28), GeometryType(1), Padding[3]
        fp1            = _ru32(d, B + 0)
        self.data.name = _rstrip(d[B + 8: B + 40])
        root_node_off  = _ru32(d, B + 40)

        # Detect game version from function pointer (expanded constants from Kotor.NET)
        self.data.game_version = 2 if fp1 in self._FP_K2 else 1

        # ── Model header extension at BASE+80 ─────────────────────────────────
        # Per Kotor.NET MDLBinaryModelHeader.cs (SIZE = GeoHdr.SIZE + 116):
        # After GeometryHeader (80 bytes), Model Header adds 116 bytes:
        #   ModelType(1), Unknown1(1), Padding1(1), Fog(1)  [M+0..3]
        #   ChildModelCount(4)                               [M+4]
        #   AnimationOffsetArrayOffset(4)                    [M+8]
        #   AnimationCount(4), AnimationCount2(4)            [M+12..19]
        #   Unknown2(4)                                      [M+20]
        #   BoundingBoxMin Float[3](12)                      [M+24]
        #   BoundingBoxMax Float[3](12)                      [M+36]
        #   Radius(4)                                        [M+48]
        #   AnimationScale(4)                                [M+52]
        #   SupermodelName[32]                               [M+56..87]
        #   OffsetToRootNode(4)                              [M+88]
        #   Unused1(4)                                       [M+92]
        #   MDXSize(4)                                       [M+96]
        #   MDXOffset(4)                                     [M+100]
        #   OffsetToNameOffsetArray(4)                       [M+104]  = _NAMES_OFF
        #   NamesArrayCount(4), NamesArrayCount2(4)          [M+108..115]
        M = B + self._GEO_HDR_SIZE  # = B + 80
        try:
            model_type = d[M + 0] if M < len(d) else 0
            fog_flag   = d[M + 3] if M + 3 < len(d) else 0
            if M + 4 < len(d):
                child_cnt  = struct.unpack_from('<I', d, M + 4)[0]
            else:
                child_cnt = 0
            if M + 52 < len(d):
                anim_scale = struct.unpack_from('<f', d, M + 52)[0]
            else:
                anim_scale = 1.0
            if M + 96 < len(d):
                mdx_size   = struct.unpack_from('<I', d, M + 96)[0]
            else:
                mdx_size = 0

            self.data.model_type  = int(model_type)
            self.data.fog         = bool(fog_flag)
            self.data.child_model_count = int(child_cnt) if 0 <= child_cnt < 1000 else 0
            self.data.animation_scale   = float(anim_scale) if math.isfinite(anim_scale) else 1.0
            self.data.mdx_size    = int(mdx_size)
            # Map model_type to classification (per KotOR community docs):
            #   0=other, 2=geometry, 4=character(body), 6=door, 65=effects/tile
            _TYPE_MAP = {2: 'geometry', 4: 'character', 6: 'door', 65: 'effect'}
            self.data.classification = _TYPE_MAP.get(int(model_type), 'other')

            # supermodel_name at BASE+136 = M+_SUPERMODEL_OFF = M+56
            _sm_off = self._SUPERMODEL_OFF
            self.data.supermodel = _rstrip(d[M + _sm_off: M + _sm_off + 32])
        except Exception:
            self.data.supermodel = "NULL"

        # ── Name string_array at BASE+184 ─────────────────────────────────────
        # After 168-byte model header, a 28-byte section contains:
        #   OffsetToRootNode(4), Unused(4), MDXFileSize(4), MDXOffset(4),
        #   NamesArrayOffset(4), NamesCount(4), NamesCountDup(4)
        # NamesArrayOffset field is at BASE+168+16 = BASE+184 (_NAMES_OFF=184)
        N = B + self._NAMES_OFF  # = B + 184
        if N + 12 <= len(d):
            names_arr_ptr = _ru32(d, N + 0)   # pointer to uint32[] name offsets (rel to BASE)
            names_count   = _ru32(d, N + 4)   # number of names
            self._names   = []
            if 0 < names_count <= 4096 and names_arr_ptr > 0:
                for i in range(names_count):
                    ptr_off = B + names_arr_ptr + i * 4
                    if ptr_off + 4 > len(d):
                        break
                    str_off = _ru32(d, ptr_off)   # offset of string from BASE
                    abs_off = B + str_off          # absolute file position
                    if 0 < abs_off < len(d):
                        end = d.find(b'\x00', abs_off)
                        if end < 0 or end - abs_off > 128:
                            end = abs_off + 64
                        name_str = d[abs_off:end].decode('ascii', 'replace').strip()
                        self._names.append(name_str)
                    else:
                        self._names.append(f"node_{i}")

        # ── Node tree ─────────────────────────────────────────────────────────
        if root_node_off:
            self.data.root_node = self._parse_node(B + root_node_off, None)

        self.data.compute_bounds()
        log.debug(
            f"MDL '{self.data.name}': {len(self.data.mesh_nodes())} mesh nodes, "
            f"visible={len(self.data.visible_mesh_nodes())}, "
            f"bb={self.data.bb_min}..{self.data.bb_max}"
        )
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

        # Node header (80 bytes) per KotOR Modding Wiki + Kotor.NET verification:
        # Kotor.NET MDLBinaryNodeHeader.cs confirms layout:
        #   NodeType(2) + NodeIndex(2) + NameIndex(2) + Padding(2)
        #   + RootNodeOffset(4) + ParentNodeOffset(4)
        #   + Position Float[3](12) + Rotation Float[4](16)
        #   + ChildArrayOffset(4) + ChildCount(4) + ChildCountDup(4)
        #   + ControllerArrayOffset(4) + ControllerCount(4) + ControllerCountDup(4)
        #   + ControllerDataOffset(4) + ControllerDataCount(4) + ControllerDataCountDup(4)
        # Total = 80 bytes
        #
        # IMPORTANT: NodeIndex (offset 2) is the sequential index in the scene graph.
        #            NameIndex (offset 4) is the index into the names string array.
        # Deserializer uses binaryNode.NodeHeader.NameIndex for names lookup.
        node_type  = _ru16(d, o); o += 2   # NodeType bitfield (NODE_HEADER|NODE_MESH etc)
        _seq_idx   = _ru16(d, o); o += 2   # NodeIndex  = sequential node index
        index_num  = _ru16(d, o); o += 2   # NameIndex  = index into names[] string array
        o += 2                              # Padding
        o += 4                              # RootNodeOffset (self-referential)
        o += 4                              # ParentNodeOffset

        px, py, pz     = struct.unpack_from('<fff',  d, o); o += 12
        rx, ry, rz, rw = struct.unpack_from('<ffff', d, o); o += 16

        child_arr_off   = _ru32(d, o); o += 4
        child_cnt       = _ru32(d, o); o += 4
        o += 4                              # child nr_alloc
        ctrl_keys_off   = _ru32(d, o); o += 4  # controller key array offset
        ctrl_keys_cnt   = _ru32(d, o); o += 4  # controller key count
        o += 4                              # ctrl_keys nr_alloc
        ctrl_data_off   = _ru32(d, o); o += 4  # controller data array offset
        ctrl_data_cnt   = _ru32(d, o); o += 4  # controller data count
        o += 4                              # ctrl_data nr_alloc
        # o is now at abs_off + 80 = start of type-specific data

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

        # ── Read controller data to get bind-pose position/orientation ─────────
        # Controllers override the header position/rotation for some models.
        # We read controller type 8 (position) and 20 (orientation) first-row values.
        if ctrl_keys_cnt > 0 and ctrl_keys_off > 0 and ctrl_data_cnt > 0 and ctrl_data_off > 0:
            try:
                self._parse_controllers(node, ctrl_keys_off, ctrl_keys_cnt,
                                        ctrl_data_off, ctrl_data_cnt)
            except Exception as e:
                log.debug(f"Controller parse error on '{name}': {e}")

        # ── Type-specific data blocks ─────────────────────────────────────────
        # Process node type flags in xoreos order: Light→Emitter→Ref→Mesh→Skin→Anim→Dangly→AABB→Saber
        type_off = o  # save position after node header

        if node_type & NODE_LIGHT:
            # Light node: fixed 0x5C (92) bytes (xoreos skips these)
            type_off += 0x5C

        if node_type & NODE_EMITTER:
            # Emitter: fixed 0xD0 (208) bytes (xoreos reads full emitter struct)
            type_off += 0xD0

        if node_type & NODE_REF:
            # Reference: fixed 0x44 (68) bytes (xoreos skips)
            type_off += 0x44

        if node_type & NODE_MESH:
            try:
                self._parse_mesh(node, type_off)
            except Exception as e:
                log.debug(f"Mesh parse error on '{name}': {e}")
            # After mesh header, SKIN follows for NODE_SKIN nodes
            if node_type & NODE_SKIN:
                try:
                    mesh_size = (self._MESH_HDR_K2 if self.data.game_version == 2
                                 else self._MESH_HDR_K1)
                    skin_off = type_off + mesh_size
                    self._parse_skin(node, skin_off)
                except Exception as e:
                    log.debug(f"Skin parse error on '{name}': {e}")

        if node_type & NODE_DANGLY:
            # Dangly mesh: follows MESH header; 28 bytes per MDLBinaryDanglyHeader
            # We skip it but note the node is dangly
            pass  # already marked via is_dangly property

        if node_type & NODE_AABB:
            # AABB walkmesh: 4-byte pointer to AABB tree root (MDLBinaryWalkmeshHeader)
            # The triangles themselves come from the mesh faces above
            # Just mark the node - faces were already read by _parse_mesh
            try:
                mesh_size = (self._MESH_HDR_K2 if self.data.game_version == 2
                             else self._MESH_HDR_K1)
                aabb_off  = type_off + mesh_size
                if aabb_off + 4 <= len(d):
                    _aabb_root = _ru32(d, aabb_off)  # AABB tree root pointer
                    log.debug(f"  '{name}': AABB walkmesh node, root=0x{_aabb_root:08x}")
            except Exception as e:
                log.debug(f"AABB parse error on '{name}': {e}")

        # ── Parse children ─────────────────────────────────────────────────────
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

    # ── Controller parser ──────────────────────────────────────────────────────

    def _parse_controllers(self, node: MeshNode,
                           keys_off: int, keys_cnt: int,
                           data_off: int, data_cnt: int):
        """
        Parse controller key array — stores ALL keyframes for animation playback
        and extracts bind-pose position/orientation.

        Controller key struct (16 bytes per xoreos + cchargin + Kotor.NET MDLBinaryControllerHeader):
          type(4), unknown(2), rowCount(2), timeKeyOffset(2), dataOffset(2),
          columnCount(1), padding(3)

        Controller types (Kotor.NET MDLBinaryControllerType.cs):
          8  = position    (3 floats: x, y, z)
          20 = orientation (4 floats: x, y, z, w  OR compressed 2-column form)
          36 = scale       (1 float)
          100 = SelfIlluminationColour (3 floats RGB)
          128/132 = alpha  (1 float)
        """
        d = self.mdl
        B = self.BASE
        abs_keys = B + keys_off
        abs_data = B + data_off

        # Sanity check
        if abs_keys + keys_cnt * 16 > len(d):
            return
        if data_cnt > 16384:
            return

        # Read all controller data as float32 array
        data_size = data_cnt * 4
        if abs_data + data_size > len(d):
            data_cnt = (len(d) - abs_data) // 4
        if data_cnt <= 0:
            return

        ctrl_floats = list(struct.unpack_from(f'<{data_cnt}f', d, abs_data))
        # Also access raw int32 data for compressed quaternion
        ctrl_ints   = list(struct.unpack_from(f'<{data_cnt}I', d, abs_data))

        for i in range(keys_cnt):
            ko = abs_keys + i * 16
            if ko + 16 > len(d):
                break
            ctrl_type = _ru32(d, ko)
            # skip 2 bytes unknown (ko+4 to ko+5)
            row_cnt   = _ru16(d, ko + 6)
            time_idx  = _ru16(d, ko + 8)   # index into data array for time keys
            data_idx  = _ru16(d, ko + 10)  # index into data array for values
            col_cnt   = struct.unpack_from('B', d, ko + 12)[0]

            if row_cnt == 0 or col_cnt == 0:
                continue

            # Kotor.NET: "if controllerType == 20 && columnCount == 2, realColumnCount = 1"
            real_col_cnt = 1 if (ctrl_type == CTRL_ORIENTATION and col_cnt == 2) else col_cnt

            # Build keyframe list for this controller
            keyframes: List[Tuple[float, List[float]]] = []

            for j in range(row_cnt):
                t_di = time_idx + j
                d_di = data_idx + j * real_col_cnt

                if t_di >= len(ctrl_floats):
                    break
                time_key = ctrl_floats[t_di]

                if ctrl_type == CTRL_ORIENTATION and col_cnt == 2:
                    # Compressed quaternion — 1 uint32 per row
                    if d_di >= len(ctrl_ints):
                        break
                    packed = ctrl_ints[d_di]
                    qx, qy, qz, qw = _uncompress_quaternion(packed)
                    keyframes.append((time_key, [qx, qy, qz, qw]))
                else:
                    if d_di + real_col_cnt > len(ctrl_floats):
                        break
                    vals = ctrl_floats[d_di: d_di + real_col_cnt]
                    keyframes.append((time_key, list(vals)))

            if keyframes:
                if ctrl_type not in node.controllers:
                    node.controllers[ctrl_type] = []
                node.controllers[ctrl_type].extend(keyframes)

                # Bind-pose: use first keyframe to set node position/rotation
                first_vals = keyframes[0][1]
                if ctrl_type == CTRL_POSITION and len(first_vals) >= 3:
                    x, y, z = first_vals[0], first_vals[1], first_vals[2]
                    if abs(x) + abs(y) + abs(z) > 1e-6:
                        node.position = (x, y, z)
                elif ctrl_type == CTRL_ORIENTATION and len(first_vals) >= 4:
                    node.rotation = (first_vals[0], first_vals[1],
                                     first_vals[2], first_vals[3])
                elif ctrl_type in (CTRL_ALPHA, CTRL_ALPHA_OLD) and len(first_vals) >= 1:
                    node.alpha = float(first_vals[0])

    # Mesh header sizes (for offset calculations after mesh header)
    # cchargin confirmed: K1 = 332 bytes (ending at offset 328+4=332), K2 = 340 bytes
    _MESH_HDR_K1 = 332   # KotOR 1 mesh header size
    _MESH_HDR_K2 = 340   # KotOR 2 mesh header size (8 extra bytes at 324-331)

    # ── Skin mesh parser ──────────────────────────────────────────────────────

    def _parse_skin(self, node: MeshNode, off: int):
        """
        Parse the skinmesh extension header (NODE_SKIN nodes).

        Kotor.NET MDLBinarySkinmeshHeader (SIZE = 96 bytes):
          Unknown0(4), Unknown1(4), Unknown2(4)
          MDXWeightValueStride(4) — byte offset in MDX stride for bone weights
          MDXWeightIndexStride(4) — byte offset in MDX stride for bone indices
          BonemapOffset(4), BonemapCount(4)
          QBonesOffset(4), QBonesCount(4), QBonesCount2(4)
          TBonesOffset(4), TBonesCount(4), TBonesCount2(4)
          Array8Offset(4), Array8Count(4), Array8Count2(4)
          BoneIndex1..16 (16 × UInt16 = 32 bytes)

        The skinmesh header is located at: node_abs + 80 (node hdr) + mesh_hdr_size
        """
        d = self.mdl
        B = self.BASE
        o = off

        if o + 96 > len(d):
            return

        _u0     = struct.unpack_from('<I', d, o)[0]; o += 4
        _u1     = struct.unpack_from('<I', d, o)[0]; o += 4
        _u2     = struct.unpack_from('<I', d, o)[0]; o += 4
        wt_val_stride = struct.unpack_from('<I', d, o)[0]; o += 4  # weight-values offset in MDX
        wt_idx_stride = struct.unpack_from('<I', d, o)[0]; o += 4  # weight-indices offset in MDX
        bonemap_off   = struct.unpack_from('<I', d, o)[0]; o += 4
        bonemap_cnt   = struct.unpack_from('<I', d, o)[0]; o += 4
        _qb_off       = struct.unpack_from('<I', d, o)[0]; o += 4  # QBones
        _qb_cnt       = struct.unpack_from('<I', d, o)[0]; o += 4
        _qb_cnt2      = struct.unpack_from('<I', d, o)[0]; o += 4
        _tb_off       = struct.unpack_from('<I', d, o)[0]; o += 4  # TBones
        _tb_cnt       = struct.unpack_from('<I', d, o)[0]; o += 4
        _tb_cnt2      = struct.unpack_from('<I', d, o)[0]; o += 4
        _a8_off       = struct.unpack_from('<I', d, o)[0]; o += 4  # Array8
        _a8_cnt       = struct.unpack_from('<I', d, o)[0]; o += 4
        _a8_cnt2      = struct.unpack_from('<I', d, o)[0]; o += 4
        # BoneIndex[1..16] = 16 UInt16
        bone_node_indices = list(struct.unpack_from('<16H', d, o))
        node.bone_node_indices = bone_node_indices

        # Read bonemap: uint8 array[bonemap_cnt]
        if bonemap_cnt > 0 and bonemap_off > 0:
            ba = B + bonemap_off
            end = min(ba + bonemap_cnt, len(d))
            node.bone_map = list(d[ba:end])

        # Extract per-vertex bone weights and indices from MDX
        # The MDX stride/channel info is from the mesh header (stored in the node)
        # wt_val_stride = offset within MDX record for 4 float32 weights
        # wt_idx_stride = offset within MDX record for 4 float32 bone indices
        # We need mdx_data_off and mdx_data_size from the already-parsed mesh.
        # Since _parse_mesh sets node.vertices, we can infer vertex count.
        mdx = self.mdx
        if not mdx or wt_val_stride == 0xFFFFFFFF or wt_idx_stride == 0xFFFFFFFF:
            return

        vert_cnt = len(node.vertices)
        if vert_cnt == 0:
            return

        # Find MDX data offset from MDL mesh header pointer field.
        # It was stored 4 bytes before verts_off at the end of the mesh header.
        # Re-read from the mesh header area (off - 8 bytes from end of mesh header)
        mesh_size = self._MESH_HDR_K2 if self.data.game_version == 2 else self._MESH_HDR_K1
        mesh_start = off - mesh_size  # node_abs + 80 + mesh_size - mesh_size = node_abs + 80
        mdx_data_off_ptr = off - 8   # MDXOffsetToData is 8 bytes before end of mesh hdr
        if mdx_data_off_ptr < 0 or mdx_data_off_ptr + 4 > len(d):
            return

        # Re-read MDX stride from mesh header (MDXDataSize at off - mesh_size + 252)
        # More robustly: use the node's already-read vertex positions to find stride
        # We need the MDX data offset (already parsed in _parse_mesh).
        # The quickest approach: scan the MDX with the mesh start offset.
        mdx_ptr_off = off - 8   # MDXOffsetToData field at offset [mesh_size - 8] within mesh hdr
        mdx_size_off = off - mesh_size + 252   # MDXDataSize at mesh_hdr_start + 252
        if mdx_size_off + 4 > len(d) or mdx_ptr_off + 4 > len(d):
            return

        mdx_stride = struct.unpack_from('<I', d, mdx_size_off)[0]
        mdx_data_off = struct.unpack_from('<I', d, mdx_ptr_off)[0]

        if mdx_stride == 0 or mdx_data_off >= len(mdx):
            return

        _ABSENT = 0xFFFFFFFF
        if wt_val_stride == _ABSENT or wt_idx_stride == _ABSENT:
            return

        for i in range(vert_cnt):
            base = mdx_data_off + i * mdx_stride
            # Bone weights: 4 float32 values
            if base + wt_val_stride + 16 <= len(mdx):
                w0, w1, w2, w3 = struct.unpack_from('<4f', mdx, base + wt_val_stride)
                node.bone_weights.append((w0, w1, w2, w3))
            else:
                node.bone_weights.append((1.0, 0.0, 0.0, 0.0))
            # Bone indices: 4 float32 (stored as floats, used as indices)
            if base + wt_idx_stride + 16 <= len(mdx):
                i0, i1, i2, i3 = struct.unpack_from('<4f', mdx, base + wt_idx_stride)
                node.bone_indices.append((int(i0), int(i1), int(i2), int(i3)))
            else:
                node.bone_indices.append((0, 0, 0, 0))

        log.debug(
            f"  '{node.name}' (skin): {len(node.bone_weights)} bone weight sets, "
            f"bonemap_cnt={bonemap_cnt}"
        )

    # ── Mesh data parser ──────────────────────────────────────────────────────

    def _parse_mesh(self, node: MeshNode, off: int):
        """
        Parse the mesh header and extract vertices, normals, UVs, faces.

        Offset 'off' points to the first byte AFTER the 80-byte node header,
        i.e. the start of the type-specific mesh header extension.

        Mesh header layout verified against:
          - cchargin's mdl_info.html specification
          - xoreos model_kotor.cpp readMesh() function
          - KotOR1MDL.bt Binary Template (Farmboy0)

        Layout (KotOR1 = 332 bytes, KotOR2 = 340 bytes):
          offset   0: p_func1(4), p_func2(4)           [skip]
          offset   8: FacesOffset(4), FacesCount(4), FacesCountDup(4)  [face structs]
          offset  20: bb_min Float[3](12), bb_max Float[3](12), radius(4), average Float[3](12)
          offset  60: diffuse Float[3](12), ambient Float[3](12), transparency_hint(4)
          offset  88: texture1[32], lightmap[32]        [texture names]
          offset 152: unknown[6*4=24]                   [skip]
          offset 176: VertexIndicesCountArray(4,4,4)    [ptr,cnt,dup — vic_array_def]
          offset 188: VertexOffsetsArray(4,4,4)         [ptr,cnt,dup — offOffVerts]
          offset 200: InvertedCountersArray(4,4,4)      [skip]
          offset 212: always_minus1(4), always_minus1(4), always_zero(4)   [skip 12]
          offset 224: unknown_3(4), unknown_0(4), unknown_0(4)  [skip 12; 3 longs]
          offset 236: unknown(4) × 4                            [skip 16; 4 longs]
          offset 252: MDXDataSize(4)                    [stride; matches cchargin]
          offset 256: MDXDataBitmap(4)                  [channel flags]
          offset 260: [11 MDX channel offsets 4 bytes each = 44 bytes]
                       Vertices(0), Normals(12), Colors, UV1(24), LightmapUV,
                       UV2, UV3, Bump, Unk, Unk, Unk
          offset 304: VertexCount(2), TextureCount(2)   [counts]
          offset 308: HasLightmap(1), RotateTexture(1), BackgroundGeom(1),
                       HasShadow(1), Beaming(1), HasRender(1)
          offset 314: unknown(2), unknown(4), unknown(4)  [skip 10 bytes]
          [K2 only]:   unknown(4), unknown(4)           [extra 8 bytes]
          offset 324:  MDXDataOffset(4)                 [into MDX file]
          offset 328:  VerticesOffset(4)                [MDL vertex positions]

        NOTE: Our byte offset accounting is in absolute file positions.
        The 'o' variable below accumulates from 'off' (= node_abs + 80).
        """
        d   = self.mdl
        mdx = self.mdx
        B   = self.BASE
        o   = off   # absolute file position at mesh header start

        # ── Offset bookmarks (for vo_array fallback) ─────────────────────────
        mesh_base = o  # remember start of mesh header

        # ── Per-mesh K2 detection via TrimeshHeader function pointers ─────────
        # Kotor.NET MDLBinaryTrimeshHeader.cs: IsTSL = fp1 in K2 PC/Xbox FP sets
        # This is more reliable than the global model fp1 for detecting K2 nodes.
        if o + 8 <= len(d):
            mesh_fp1 = _ru32(d, o)
            is_k2_mesh = mesh_fp1 in self._FP_K2_MESH
        else:
            is_k2_mesh = (self.data.game_version == 2)

        # Skip function pointers (2×4 = 8 bytes)
        o += 8

        # ── Faces array_def (offset 8 from mesh header start) ────────────────
        # This is the 32-byte face struct array:
        #   Normal(12) + PlaneCoeff(4) + Material(4) + Adjacency(6) + VertexIDs(6)
        faces_off = _ru32(d, o); o += 4   # base-relative ptr to face[] structs
        faces_cnt = _ru32(d, o); o += 4   # number of face structs
        o += 4                             # nr_alloc (dup)

        # ── Bounding box (offset 20) ──────────────────────────────────────────
        o += 12  # bb_min Float[3]
        o += 12  # bb_max Float[3]
        o += 4   # radius Float
        o += 12  # average Float[3]
        # o is now at offset 60

        # ── Colors (offset 60) ───────────────────────────────────────────────
        dr, dg, db = struct.unpack_from('<fff', d, o); o += 12  # diffuse RGB
        ar, ag, ab = struct.unpack_from('<fff', d, o); o += 12  # ambient RGB
        o += 4   # transparency_hint (uint32)
        # o is now at offset 88

        # ── Texture names (offset 88) ─────────────────────────────────────────
        tex_name  = _rstrip(d[o:o+32]).lower(); o += 32   # primary texture
        lmap_name = _rstrip(d[o:o+32]).lower(); o += 32   # lightmap / texture2
        # o is now at offset 152

        # ── Unknown[6] = 24 bytes (offset 152) ───────────────────────────────
        o += 24   # always-zero block
        # o is now at offset 176

        # ── vic_array_def (offset 176): vertex-indices-count array ────────────
        # cchargin: "location of number of verts", "number of items (always 1)"
        vic_ptr   = _ru32(d, o); o += 4
        o += 4   # vic count (always 1)
        o += 4   # vic dup
        # o is now at offset 188

        # ── vo_array_def (offset 188): vertex-offsets array ───────────────────
        # cchargin: "location of location of verts" — double pointer!
        # This is the xoreos offOffVerts path: mdl[vo_ptr] → mdl[offVerts] → uint16[] face indices
        vo_ptr    = _ru32(d, o); o += 4
        o += 4   # vo count (always 1)
        o += 4   # vo dup
        # o is now at offset 200

        # ── Inverted counters array (offset 200) ──────────────────────────────
        o += 12   # unknown array_def
        # o is now at offset 212

        # ── Always-minus1 block (offset 212) ─────────────────────────────────
        o += 12   # always_minus1(4) × 2 + always_zero(4)
        # o is now at offset 224

        # ── Unknown saber block + UV animation fields (offset 224) ──────────
        # Kotor.NET MDLBinaryTrimeshHeader (verified field order):
        #   UnknownSaberValues[8](8)  → offset 224..231
        #   AnimateUV(4)              → offset 232
        #   UVDirection Float[2](8)   → offset 236..243
        #   UVSpeed Float(4)          → offset 244
        #   UVJitterSpeed Float(4)    → offset 248
        # Total: 8+4+8+4+4 = 28 bytes  → offset 252
        o += 8   # UnknownSaberValues[8]
        # o is now at offset 232
        try:
            animate_uv   = struct.unpack_from('<I', d, o)[0]  # AnimateUV int
            uv_dir_u     = struct.unpack_from('<f', d, o + 4)[0]  # UVDirection.X
            uv_dir_v     = struct.unpack_from('<f', d, o + 8)[0]  # UVDirection.Y
            uv_speed     = struct.unpack_from('<f', d, o + 12)[0] # UVSpeed
            uv_jitter    = struct.unpack_from('<f', d, o + 16)[0] # UVJitterSpeed
            if math.isfinite(uv_dir_u) and math.isfinite(uv_dir_v):
                node.uv_animate = bool(animate_uv)
                node.uv_dir     = (uv_dir_u, uv_dir_v)
                node.uv_speed   = float(uv_speed) if math.isfinite(uv_speed) else 0.0
                node.uv_jitter  = float(uv_jitter) if math.isfinite(uv_jitter) else 0.0
        except Exception as _e:
            log.debug("MDLParser: UV-animate fields unreadable at offset %d: %s", o, _e)
        o += 20  # AnimateUV(4)+UVDirection(8)+UVSpeed(4)+UVJitter(4)
        # o is now at offset 252

        # ── MDX struct fields (offset 252) ────────────────────────────────────
        # cchargin: offset 252 = 'size of 1 MDX structure'
        # xoreos: ctx.mdxStructSize = ctx.mdl->readUint32LE()
        mdx_data_size = _ru32(d, o); o += 4   # MDX stride (bytes per vertex)
        mdx_flags     = _ru32(d, o); o += 4   # MDX channel bitmap
        # o is now at offset 260

        # 11 MDX channel offsets (4 bytes each = 44 bytes total → offset 304)
        # Kotor.NET MDLBinaryTrimeshHeader fields (confirmed names/order):
        #   MDXPositionStride(4), MDXNormalStride(4), MDXColourStride(4),
        #   MDXTexture1Stride(4), MDXTexture2Stride(4), MDXTexture3Stride(4),
        #   MDXTexture4Stride(4), MDXTangent1Stride(4), MDXTangent2Stride(4),
        #   MDXTangent3Stride(4), MDXTangent4Stride(4)
        # Texture2 = lightmap UVs; Texture1 = primary diffuse UVs
        mdx_v_off  = _ru32(d, o); o += 4   # MDXPositionStride (usually 0)
        mdx_n_off  = _ru32(d, o); o += 4   # MDXNormalStride   (usually 12)
        o += 4                              # MDXColourStride   (vertex color, skip)
        mdx_t1_off = _ru32(d, o); o += 4   # MDXTexture1Stride (UV set 1 — primary diffuse)
        mdx_t2_off = _ru32(d, o); o += 4   # MDXTexture2Stride (UV set 2 — lightmap UVs)
        o += 4   # MDXTexture3Stride
        o += 4   # MDXTexture4Stride
        o += 4   # MDXTangent1Stride (bump map tangent 1)
        o += 4   # MDXTangent2Stride
        o += 4   # MDXTangent3Stride
        o += 4   # MDXTangent4Stride
        # o is now at offset 304

        # ── Vertex / texture counts (offset 304) ──────────────────────────────
        # cchargin: number of vertices (short) + number of textures (short)
        vert_cnt  = _ru16(d, o); o += 2    # vertex count
        tex_count = _ru16(d, o); o += 2    # texture count
        # o is now at offset 308

        # ── Per-mesh flags (offset 308) ────────────────────────────────────────
        # Kotor.NET MDLBinaryTrimeshHeader (confirmed byte layout):
        #   HasLightmap(1), RotateTexture(1), BackgroundGeometry(1),
        #   HasShadow(1), Beaming(1), DoesRender(1), Unknown5(1), Unknown6(1)
        has_lightmap   = struct.unpack_from('B', d, o)[0]; o += 1
        rotate_texture = struct.unpack_from('B', d, o)[0]; o += 1
        bg_geom        = struct.unpack_from('B', d, o)[0]; o += 1
        has_shadow     = struct.unpack_from('B', d, o)[0]; o += 1
        beaming        = struct.unpack_from('B', d, o)[0]; o += 1
        has_render     = struct.unpack_from('B', d, o)[0]; o += 1
        o += 2  # Unknown5, Unknown6
        # o is now at offset 316
        # TotalArea(4) + Unknown7(4) = 8 bytes
        o += 8
        # o is now at offset 324

        # KotOR 2 adds 8 extra bytes before MDX/vertex pointers:
        # DirtEnabled(1), Padding0(1), DirtTexture(2), DirtCoordSpace(2),
        # HideInHolograms(1), Padding1(1) = 8 bytes
        # Use per-mesh K2 detection (is_k2_mesh) for accuracy, fallback to global
        if is_k2_mesh or self.data.game_version == 2:
            o += 8
        # o is now at offset 324 (K1) or 332 (K2)

        # ── Final MDX/vertex pointers ─────────────────────────────────────────
        mdx_data_off = _ru32(d, o); o += 4   # MDX data start offset (absolute in MDX file)
        verts_off    = _ru32(d, o); o += 4   # MDL vertex positions (base-relative)

        # ── Store material properties ─────────────────────────────────────────
        node.texture       = tex_name
        node.lightmap      = lmap_name
        node.diffuse       = (dr, dg, db)
        node.ambient       = (ar, ag, ab)
        node.render        = bool(has_render)
        node.has_lightmap  = bool(has_lightmap)
        node.rotate_texture = bool(rotate_texture)
        node.background_geom = bool(bg_geom)
        node.has_shadow    = bool(has_shadow)
        node.beaming       = bool(beaming)
        node.transparency_hint = _ru32(d, mesh_base + 84) if mesh_base + 88 <= len(d) else 0

        if vert_cnt == 0 or vert_cnt > 65000:
            return

        # ── Vertex positions ──────────────────────────────────────────────────
        # Priority order:
        # 1. MDX interleaved data (most accurate, includes normals + UVs)
        # 2. MDL p_data fallback (positions only)
        # 3. vo_array flat-index path (xoreos-style double pointer)

        _ABSENT = 0xFFFFFFFF  # sentinel for absent MDX channel (stored as -1 = 0xFFFFFFFF)
        verts_loaded = False

        # Path 1: MDX interleaved
        # Note: mdx_data_off CAN be 0 (first node data starts at beginning of MDX file)
        # We only skip if MDX is empty or the offset is out of bounds
        if (mdx_data_size > 0
                and len(mdx) > 0
                and mdx_data_off < len(mdx)
                and mdx_v_off != _ABSENT):
            stride = mdx_data_size
            for i in range(vert_cnt):
                base = mdx_data_off + i * stride
                if base + mdx_v_off + 12 > len(mdx):
                    break
                node.vertices.append(
                    struct.unpack_from('<fff', mdx, base + mdx_v_off))
            verts_loaded = (len(node.vertices) == vert_cnt)

        # Path 2: MDL p_data (XYZ float32 triples, base-relative pointer)
        if not verts_loaded and verts_off > 0:
            va = B + verts_off
            for i in range(vert_cnt):
                p = va + i * 12
                if p + 12 > len(d):
                    break
                node.vertices.append(struct.unpack_from('<fff', d, p))
            verts_loaded = (len(node.vertices) == vert_cnt)

        # ── Normals, UV1 and lightmap UV2 from MDX ────────────────────────────
        if (mdx_data_size > 0
                and len(mdx) > 0
                and mdx_data_off < len(mdx)):
            stride = mdx_data_size
            for i in range(vert_cnt):
                base = mdx_data_off + i * stride
                if (mdx_n_off != _ABSENT
                        and base + mdx_n_off + 12 <= len(mdx)):
                    node.normals.append(
                        struct.unpack_from('<fff', mdx, base + mdx_n_off))
                if (mdx_t1_off != _ABSENT
                        and base + mdx_t1_off + 8 <= len(mdx)):
                    node.uvs.append(
                        struct.unpack_from('<ff', mdx, base + mdx_t1_off))
                # Lightmap UVs (Texture2 channel = MDXTexture2Stride)
                if (mdx_t2_off != _ABSENT
                        and base + mdx_t2_off + 8 <= len(mdx)):
                    node.uvs2.append(
                        struct.unpack_from('<ff', mdx, base + mdx_t2_off))

        # ── Faces ─────────────────────────────────────────────────────────────
        # Strategy A: 32-byte face structs (authoritative — includes material IDs)
        #   Face struct layout (KotOR Modding Wiki + xoreos verified):
        #     Normal Float[3](12), PlaneCoeff Float(4), Material UInt32(4),
        #     FaceAdjacency[3] UInt16(6), VertexIndex[3] UInt16(6)
        #     → VertexIndex at offset +26, Material at offset +12
        #
        # Strategy B: Flat uint16 array via double-pointer (xoreos rendering path)
        #   vo_ptr → uint32 offset → uint16[facesCount * 3]
        #   This is what xoreos uses for rendering; same indices, no material info.

        faces_loaded = False

        # Strategy A: Read from 32-byte face struct array
        if faces_cnt > 0 and faces_off > 0:
            fa = B + faces_off
            for i in range(min(faces_cnt, 65535)):
                p = fa + i * 32
                if p + 32 > len(d):
                    break
                material = struct.unpack_from('<I', d, p + 12)[0]   # surfacemat row
                vi0, vi1, vi2 = struct.unpack_from('<HHH', d, p + 26)
                if vi0 < vert_cnt and vi1 < vert_cnt and vi2 < vert_cnt:
                    node.faces.append((vi0, vi1, vi2))
                    node.face_materials.append(material)
            faces_loaded = len(node.faces) > 0

        # Strategy B: Flat index array via double pointer (xoreos path)
        # Used as fallback when strategy A yields no faces.
        # vo_ptr → uint32(offVerts relative to BASE) → uint16[facesCount * 3]
        if not faces_loaded and faces_cnt > 0 and vo_ptr > 0:
            off_off_verts_abs = B + vo_ptr
            if off_off_verts_abs + 4 <= len(d):
                flat_idx_off = B + _ru32(d, off_off_verts_abs)
                expected = faces_cnt * 3
                if flat_idx_off > 0 and flat_idx_off + expected * 2 <= len(d):
                    flat = struct.unpack_from(f'<{expected}H', d, flat_idx_off)
                    for i in range(faces_cnt):
                        vi0 = flat[i*3 + 0]
                        vi1 = flat[i*3 + 1]
                        vi2 = flat[i*3 + 2]
                        if vi0 < vert_cnt and vi1 < vert_cnt and vi2 < vert_cnt:
                            node.faces.append((vi0, vi1, vi2))
                            node.face_materials.append(0)  # no material info in flat path
                    faces_loaded = len(node.faces) > 0
                    if faces_loaded:
                        log.debug(f"  '{node.name}': used flat index fallback ({faces_cnt} faces)")

        log.debug(
            f"  '{node.name}': {len(node.vertices)} verts, "
            f"{len(node.faces)} faces, tex='{tex_name}', lmap='{lmap_name}', "
            f"render={has_render}, has_lmap={has_lightmap}, "
            f"uvs={len(node.uvs)}, uvs2={len(node.uvs2)}"
        )


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
    if len(mdl_bytes) < MDLParser.BASE + MDLParser._NAMES_OFF + 12:
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
    if len(mdl_bytes) < MDLParser.BASE + MDLParser._NAMES_OFF + 12:
        return result

    try:
        parser = MDLParser(mdl_bytes, mdx_bytes)
        mesh_data = parser.parse()

        tex_seen:  set = set()

        for node in mesh_data.mesh_nodes():
            tex = node.texture_clean.lower()
            if tex and tex not in tex_seen:
                tex_seen.add(tex)
                result['textures'].append(tex)

        # Supermodel
        sm = mesh_data.supermodel.lower()
        if sm and sm not in ("", "null"):
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
        if cached is not None:
            return cached
        try:
            data = MDLParser.parse_files(mdl_path, mdx_path)
            if data:
                self.put(mdl_path, data)
            return data
        except Exception as e:
            log.debug(f"ModelCache.load({mdl_path}): {e}")
            return None

    def clear(self):
        self._cache.clear()
        self._order.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# ── Module-level singleton ────────────────────────────────────────────────────

_model_cache: Optional[ModelCache] = None


def get_model_cache() -> ModelCache:
    """Return the module-level ModelCache singleton."""
    global _model_cache
    if _model_cache is None:
        _model_cache = ModelCache(max_size=64)
    return _model_cache
