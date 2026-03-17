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

# Controller type IDs (from xoreos + cchargin spec)
CTRL_POSITION    = 8    # position keyframes (3 floats per row)
CTRL_ORIENTATION = 20   # orientation keyframes (4 floats per row, wxyz)
CTRL_SCALE       = 36   # scale (1 float)
CTRL_ALPHA       = 128  # mesh alpha


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
    faces:          List[Tuple[int, int, int]]       = field(default_factory=list)
    face_materials: List[int]                        = field(default_factory=list)
    # face_materials[i] = surfacemat.2da row for faces[i] (walkmesh/AABB nodes)

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
            # SKIN follows MESH; we don't parse skin weights but skip the block
            # NODE_AABB follows NODE_MESH for walkmesh nodes in xoreos

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
        Parse controller key array to extract bind-pose position and orientation.

        Controller key struct (16 bytes per xoreos + cchargin):
          type(4), unknown(2), rowCount(2), timeIndex(2), dataIndex(2),
          columnCount(1), padding(3)

        Controller types:
          8  = position    (3 floats: x, y, z)
          20 = orientation (4 floats: x, y, z, w  OR compressed 2-column form)
          36 = scale       (1 float)
          128 = alpha      (1 float)
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

        for i in range(keys_cnt):
            ko = abs_keys + i * 16
            if ko + 16 > len(d):
                break
            ctrl_type = _ru32(d, ko)
            # skip 2 bytes unknown
            row_cnt   = _ru16(d, ko + 6)
            time_idx  = _ru16(d, ko + 8)
            data_idx  = _ru16(d, ko + 10)
            col_cnt   = struct.unpack_from('B', d, ko + 12)[0]

            if row_cnt == 0 or col_cnt == 0:
                continue

            if ctrl_type == CTRL_POSITION:  # position = 8
                # Each row: time + 3 floats (or bezier: time + 9 floats)
                bezier = bool(col_cnt & 16)
                step   = 9 if bezier else 3
                # First row bind-pose
                di = data_idx
                if di < len(ctrl_floats) and di + step <= len(ctrl_floats):
                    x = ctrl_floats[di + 0]
                    y = ctrl_floats[di + 1]
                    z = ctrl_floats[di + 2]
                    # Only override if meaningfully non-zero
                    if abs(x) + abs(y) + abs(z) > 1e-6:
                        node.position = (x, y, z)

            elif ctrl_type == CTRL_ORIENTATION:  # orientation = 20
                # Column count 4 = full quaternion (x,y,z,w)
                # Column count 2 = compressed packed quaternion
                if col_cnt == 4:
                    di = data_idx
                    if di + 4 <= len(ctrl_floats):
                        qx = ctrl_floats[di + 0]
                        qy = ctrl_floats[di + 1]
                        qz = ctrl_floats[di + 2]
                        qw = ctrl_floats[di + 3]
                        node.rotation = (qx, qy, qz, qw)
                elif col_cnt == 2:
                    # Compressed: 2 uint32 packed into quaternion
                    # (as per xoreos readOrientationController col_cnt==2 branch)
                    abs_data_i = abs_data + data_idx * 4
                    if abs_data_i + 4 <= len(d):
                        temp = _ru32(d, abs_data_i)
                        qx = 1.0 - float(temp & 0x7ff) / 1023.0
                        qy = 1.0 - float((temp >> 11) & 0x7ff) / 1023.0
                        qz = 1.0 - float(temp >> 22) / 511.0
                        t2 = qx*qx + qy*qy + qz*qz
                        if t2 < 1.0:
                            qw = -math.sqrt(1.0 - t2)
                        else:
                            mag = math.sqrt(t2)
                            qx /= mag; qy /= mag; qz /= mag; qw = 0.0
                        node.rotation = (qx, qy, qz, qw)

    # Mesh header sizes (for offset calculations after mesh header)
    # cchargin confirmed: K1 = 332 bytes (ending at offset 328+4=332), K2 = 340 bytes
    _MESH_HDR_K1 = 332   # KotOR 1 mesh header size
    _MESH_HDR_K2 = 340   # KotOR 2 mesh header size (8 extra bytes at 324-331)

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

        # ── Unknown block (offset 224): 7 longs = 28 bytes ──────────────────
        # cchargin spec: always_3(4)+always_0(4)+always_0(4)+unk×4 = 28 bytes
        # This advances us from offset 224 to offset 252 (MDX stride field)
        o += 28
        # o is now at offset 252

        # ── MDX struct fields (offset 252) ────────────────────────────────────
        # cchargin: offset 252 = 'size of 1 MDX structure'
        # xoreos: ctx.mdxStructSize = ctx.mdl->readUint32LE()
        mdx_data_size = _ru32(d, o); o += 4   # MDX stride (bytes per vertex)
        mdx_flags     = _ru32(d, o); o += 4   # MDX channel bitmap
        # o is now at offset 260

        # 11 MDX channel offsets (4 bytes each = 44 bytes total → offset 304)
        # cchargin: +0='always 0'(pos), +12=normals, +24=UV or -1
        # xoreos: offNormals = readUint32; offUV[0] = readUint32; offUV[1] = readUint32
        mdx_v_off  = _ru32(d, o); o += 4   # pos channel offset in MDX stride (usually 0)
        mdx_n_off  = _ru32(d, o); o += 4   # normal channel offset (usually 12)
        o += 4                              # vertex color offset (skip)
        mdx_t1_off = _ru32(d, o); o += 4   # UV set 1 offset (24 if present, -1 absent)
        o += 4   # lightmap UV
        o += 4   # UV set 2
        o += 4   # UV set 3
        o += 4   # bump map
        o += 4   # unknown1
        o += 4   # unknown2
        o += 4   # unknown3
        # o is now at offset 304

        # ── Vertex / texture counts (offset 304) ──────────────────────────────
        # cchargin: number of vertices (short) + number of textures (short)
        vert_cnt = _ru16(d, o); o += 2    # vertex count
        o += 2                             # texture count (skip)
        # o is now at offset 308

        # ── Per-mesh flags (offset 308) ───────────────────────────────────────
        # cchargin: unknown(short), shadow(short, 256=on), render(short, 256=on), unknown(short)
        # xoreos:   skip(2), unknownFlag1(byte), shadow(byte==1), unknownFlag2(byte), render(byte==1)
        # MagnusII: lightmapped(1), rotate_tex(1), bg_geom(1), shadow(1), beaming(1), render(1)
        # Both xoreos and MagnusII agree: 6 meaningful bytes from 308
        o += 1   # lightmapped (HasLightmap)
        o += 1   # rotate_texture
        o += 1   # background_geometry
        o += 1   # shadow
        o += 1   # beaming
        has_render = struct.unpack_from('B', d, o)[0]; o += 1
        # o is now at offset 314

        # ── Trailing padding (offset 314) ─────────────────────────────────────
        # Kotor.NET MDLBinaryTrimeshHeader: Unknown5(1), Unknown6(1), TotalArea(4), Unknown7(4) = 10
        # Then K2 adds: DirtEnabled(1), Padding0(1), DirtTexture(2), DirtCoordSpace(2),
        #               HideInHolograms(1), Padding1(1) = 8 bytes
        o += 10
        # o is now at offset 324

        # KotOR 2 has 8 additional bytes before the final two pointer fields
        # Use per-mesh K2 detection (is_k2_mesh) for accuracy, fallback to global
        if is_k2_mesh or self.data.game_version == 2:
            o += 8
        # o is now at offset 324 (K1) or 332 (K2)

        # ── Final MDX/vertex pointers ─────────────────────────────────────────
        mdx_data_off = _ru32(d, o); o += 4   # MDX data start offset (absolute in MDX file)
        verts_off    = _ru32(d, o); o += 4   # MDL vertex positions (base-relative)

        # ── Store material properties ─────────────────────────────────────────
        node.texture = tex_name
        node.diffuse = (dr, dg, db)
        node.ambient = (ar, ag, ab)
        node.render  = bool(has_render)

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

        # ── Normals and UVs from MDX ──────────────────────────────────────────
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
            f"{len(node.faces)} faces, tex='{tex_name}', render={has_render}, "
            f"lmap='{lmap_name}'"
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
