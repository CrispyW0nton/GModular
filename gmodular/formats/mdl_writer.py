"""
GModular — KotOR MDL/MDX Binary Writer
=======================================
Produces binary KotOR 1/2 .mdl + .mdx pairs from a MeshData object.

Architecture adapted from:
  - KotorBlender io_scene_kotor/format/mdl/writer.py (GPL 3.0, seedhartha)
  - cchargin's MDL format specification (mdl_info.html)
  - xoreos model_kotor.cpp (reference implementation)
  - Kotor.NET MDLBinaryWriter.cs (KobaltBlu)
  - GModular's own mdl_parser.py (field-for-field mirror)

Binary layout (all offsets relative to BASE = 12):

  File Header  [0..11]   : sig(4)=0, mdl_size(4), mdx_size(4)
  Geo Header   [12..91]  : fnptr1(4), fnptr2(4), name[32], root_off(4),
                           node_count(4), runtime[8](28), geo_type(1), pad[3]
  Model Header [92..207] : classification(1), subclass(1), unk(1), fog(1),
                           child_count(4), anim_array[12], supermodel_ref(4),
                           bb[24], radius(4), anim_scale(4), supermodel[32],
                           anim_root_off(4), pad(4), mdx_size(4), mdx_off(4),
                           name_array[12]
  Names        : name_offset_array[n*4] + null-terminated strings
  Animations   : offset array + animation headers + animation nodes
  Nodes        : depth-first flat list of node data blocks

This writer targets K1-PC binary format only (K2/XBOX paths guarded by tsl/xbox flags).
Round-trip tested: parse → write → parse, all fields within float32 tolerance.

License: MIT (GModular project) — portions of binary layout derived from
KotorBlender (GPL 3.0). No KotorBlender code is copied; only the binary
layout specification is referenced.
"""

from __future__ import annotations

import math
import struct
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

log = logging.getLogger(__name__)

# ── We import MeshData / MeshNode from the parser at call time to avoid
#   circular imports.  Type hints use string literals.

# ─────────────────────────────────────────────────────────────────────────────
#  Game-version function-pointer values (from KotorBlender types.py)
#  These are magic constants that tell the engine which C++ vtable slots to use.
# ─────────────────────────────────────────────────────────────────────────────

_FP_MODEL_K1_PC  = (4273776, 4216096)
_FP_MODEL_K2_PC  = (4285200, 4216320)

_FP_MESH_K1_PC   = (4216656, 4216672)
_FP_MESH_K2_PC   = (4216880, 4216896)

_FP_SKIN_K1_PC   = (4216592, 4216608)
_FP_SKIN_K2_PC   = (4216816, 4216832)

_FP_DANGLY_K1_PC = (4216640, 4216624)
_FP_DANGLY_K2_PC = (4216864, 4216848)

_FP_ANIM_K1_PC   = (4273392, 4451552)
_FP_ANIM_K2_PC   = (4284816, 4522928)

# ── Node type flags (same as mdl_parser.py constants) ──────────────────────
NODE_HEADER  = 0x0001
NODE_LIGHT   = 0x0002
NODE_EMITTER = 0x0004
NODE_REF     = 0x0010
NODE_MESH    = 0x0020
NODE_SKIN    = 0x0040
NODE_DANGLY  = 0x0100
NODE_AABB    = 0x0200
NODE_SABER   = 0x0800

# ── Controller type IDs (mirror of mdl_parser.py) ──────────────────────────
CTRL_POSITION    = 8
CTRL_ORIENTATION = 20
CTRL_SCALE       = 36
CTRL_SELF_ILLUM  = 100
CTRL_ALPHA       = 132
CTRL_ALPHA_OLD   = 128

# ── Emitter controller IDs (mirror of mdl_parser.py CTRL_EMITTER_*) ────────
CTRL_EM_ALPHA_END   = 80
CTRL_EM_ALPHA_START = 84
CTRL_EM_BIRTHRATE   = 88
CTRL_EM_FPS         = 104
CTRL_EM_FRAME_END   = 108
CTRL_EM_FRAME_START = 112
CTRL_EM_GRAVITY     = 116
CTRL_EM_LIFE_EXP    = 120
CTRL_EM_MASS        = 124
CTRL_EM_SIZE_START  = 144
CTRL_EM_SIZE_END    = 148
CTRL_EM_VELOCITY    = 168
CTRL_EM_X_SIZE      = 172
CTRL_EM_Y_SIZE      = 176
CTRL_EM_SPREAD      = 160
CTRL_EM_COLOR_START = 392
CTRL_EM_COLOR_MID   = 284
CTRL_EM_COLOR_END   = 380

# ── MDX vertex channel bitmask ──────────────────────────────────────────────
MDX_POSITION = 0x0001
MDX_NORMALS  = 0x0020
MDX_UV1      = 0x0002
MDX_UV2      = 0x0004

# ── AABB split-axis encoding ────────────────────────────────────────────────
_SPLIT_AXIS = {
    -3: 0x20,  # NEGATIVE_Z
    -2: 0x10,  # NEGATIVE_Y
    -1: 0x08,  # NEGATIVE_X
     0: 0x00,  # NO_CHILDREN
     1: 0x01,  # POSITIVE_X
     2: 0x02,  # POSITIVE_Y
     3: 0x04,  # POSITIVE_Z
}

# ── Classification byte → model-type string ─────────────────────────────────
_CLASS_TO_BYTE = {
    "other": 0, "effect": 1, "tile": 2, "character": 4,
    "door": 8, "lightsaber": 16, "placeable": 32, "flyer": 64,
}

# ─────────────────────────────────────────────────────────────────────────────
#  Low-level binary writer helper
# ─────────────────────────────────────────────────────────────────────────────

class _BW:
    """Simple little-endian binary buffer with position tracking."""
    def __init__(self) -> None:
        self._buf = bytearray()

    @property
    def pos(self) -> int:
        return len(self._buf)

    def pad_to(self, alignment: int) -> None:
        n = self.pos % alignment
        if n:
            self._buf.extend(b'\x00' * (alignment - n))

    def u8(self, v: int) -> None:
        self._buf.append(v & 0xFF)

    def u16(self, v: int) -> None:
        self._buf += struct.pack('<H', v & 0xFFFF)

    def i16(self, v: int) -> None:
        self._buf += struct.pack('<h', v)

    def u32(self, v: int) -> None:
        self._buf += struct.pack('<I', v & 0xFFFFFFFF)

    def i32(self, v: int) -> None:
        self._buf += struct.pack('<i', v)

    def f32(self, v: float) -> None:
        self._buf += struct.pack('<f', float(v))

    def bytes_(self, data: bytes) -> None:
        self._buf += data

    def cstr(self, s: str, length: int) -> None:
        """Write a fixed-length null-padded ASCII string."""
        enc = s.encode('ascii', 'replace')[:length]
        enc = enc + b'\x00' * (length - len(enc))
        self._buf += enc

    def patch_u32(self, offset: int, value: int) -> None:
        struct.pack_into('<I', self._buf, offset, value & 0xFFFFFFFF)

    def put_array_def(self, offset: int, count: int) -> None:
        """Write 12-byte array definition: offset(4) + count(4) + count(4)."""
        self.u32(offset)
        self.u32(count)
        self.u32(count)

    def getvalue(self) -> bytes:
        return bytes(self._buf)

    def __len__(self) -> int:
        return len(self._buf)

    def write_null_bytes(self, n: int) -> None:
        self._buf += b'\x00' * n


# ─────────────────────────────────────────────────────────────────────────────
#  AABB tree builder (pure Python, no mathutils dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _build_aabb_tree(faces, verts):
    """
    Build a balanced AABB tree for walkmesh faces.
    Returns a flat list of nodes: each entry is a dict with keys:
      bb_min, bb_max, face_idx (-1 for branch), split_axis, left, right
    """
    if not faces:
        return []

    def _aabb(face_indices):
        all_v = []
        for fi in face_indices:
            for vi in faces[fi]:
                all_v.append(verts[vi])
        xs = [v[0] for v in all_v]
        ys = [v[1] for v in all_v]
        zs = [v[2] for v in all_v]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def _centroid(fi):
        vs = [verts[vi] for vi in faces[fi]]
        return (
            sum(v[0] for v in vs) / 3.0,
            sum(v[1] for v in vs) / 3.0,
            sum(v[2] for v in vs) / 3.0,
        )

    flat = []

    def _build(face_indices):
        bb_min, bb_max = _aabb(face_indices)
        if len(face_indices) == 1:
            idx = len(flat)
            flat.append({
                'bb_min': bb_min, 'bb_max': bb_max,
                'face_idx': face_indices[0],
                'split_axis': 0, 'left': -1, 'right': -1
            })
            return idx

        # Choose split axis (longest extent)
        dx = bb_max[0] - bb_min[0]
        dy = bb_max[1] - bb_min[1]
        dz = bb_max[2] - bb_min[2]
        if dx >= dy and dx >= dz:
            axis, sax = 0, 1
        elif dy >= dx and dy >= dz:
            axis, sax = 1, 2
        else:
            axis, sax = 2, 3

        centroids = sorted(face_indices, key=lambda fi: _centroid(fi)[axis])
        mid = max(1, len(centroids) // 2)
        left_faces = centroids[:mid]
        right_faces = centroids[mid:]

        idx = len(flat)
        flat.append(None)  # placeholder

        left_idx = _build(left_faces)
        right_idx = _build(right_faces)

        flat[idx] = {
            'bb_min': bb_min, 'bb_max': bb_max,
            'face_idx': -1,
            'split_axis': sax, 'left': left_idx, 'right': right_idx
        }
        return idx

    _build(list(range(len(faces))))
    return flat


# ─────────────────────────────────────────────────────────────────────────────
#  Main MDL Binary Writer
# ─────────────────────────────────────────────────────────────────────────────

class MDLWriter:
    """
    Serialize a ``MeshData`` object to KotOR binary MDL + MDX bytes.

    Usage::

        writer = MDLWriter(mesh_data, tsl=False)
        mdl_bytes, mdx_bytes = writer.build()
        Path("model.mdl").write_bytes(mdl_bytes)
        Path("model.mdx").write_bytes(mdx_bytes)

    Or use the convenience class method::

        MDLWriter.write_files(mesh_data, "model.mdl")
    """

    BASE = 12  # all MDL offsets relative to byte 12

    def __init__(self, mesh_data, tsl: bool = False) -> None:
        # Import here to avoid circular dependency
        from gmodular.formats.mdl_parser import MeshData, MeshNode  # noqa: F401
        self.data = mesh_data
        self.tsl  = tsl  # True → K2 function pointers
        self._mdl = _BW()
        self._mdx = _BW()

        # Flat node list (depth-first, root first)
        self._nodes: list = []
        self._parent_idx: List[int] = []
        self._child_idxs: List[List[int]] = []
        self._node_idx_by_name: Dict[str, int] = {}

        # Offsets computed during peek phase
        self._node_offsets: List[int] = []
        self._children_offsets: List[int] = []
        self._ctrl_offsets: List[int] = []
        self._ctrl_data_offsets: List[int] = []
        self._ctrl_keys: List[list] = []
        self._ctrl_data: List[list] = []

        # Mesh-node per-ndoe geometry data offsets
        self._faces_off: Dict[int, int] = {}
        self._verts_off: Dict[int, int] = {}
        self._idx_off:   Dict[int, int] = {}    # index offset offset
        self._idx_cnt_off: Dict[int, int] = {}  # index count offset
        self._inv_cnt_off: Dict[int, int] = {}  # inverted counter offset
        self._mdx_off:   Dict[int, int] = {}
        self._bb:        Dict[int, tuple] = {}
        self._radius_map: Dict[int, float] = {}
        self._avg_map:   Dict[int, tuple] = {}
        self._area_map:  Dict[int, float] = {}

        # Skin node offsets
        self._bonemap_off: Dict[int, int] = {}
        self._qbone_off:   Dict[int, int] = {}
        self._tbone_off:   Dict[int, int] = {}
        self._skin_garbage_off: Dict[int, int] = {}

        # Dangly node offsets
        self._constraints_off: Dict[int, int] = {}
        self._dangly_verts_off: Dict[int, int] = {}

        # AABB node data
        self._aabb_trees: Dict[int, list] = {}
        self._aabb_off:   Dict[int, List[int]] = {}

        # Animation data
        self._anims = list(getattr(mesh_data, 'animations', []) or [])
        self._anim_offsets: List[int] = []
        self._anim_events_off: List[int] = []
        self._anim_node_lists: List[list] = []
        self._anim_node_offsets: List[List[int]] = []
        self._anim_child_off: List[List[int]] = []
        self._anim_parent_idx: List[List[int]] = []
        self._anim_child_idx: List[List[List[int]]] = []
        self._anim_ctrl_keys: List[List[list]] = []
        self._anim_ctrl_data: List[List[list]] = []
        self._anim_ctrl_off: List[List[int]] = []
        self._anim_ctrl_data_off: List[List[int]] = []
        self._anim_ctrl_cnt: List[List[int]] = []
        self._anim_ctrl_data_cnt: List[List[int]] = []

        # Name array offsets
        self._off_name_offsets = 0
        self._off_anim_offsets = 0
        self._name_str_offsets: List[int] = []
        self._mdl_size = 0
        self._mdx_size = 0

    # ─────────────────────────────────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────────────────────────────────

    def build(self) -> Tuple[bytes, bytes]:
        """Build and return (mdl_bytes, mdx_bytes)."""
        self._flatten_nodes()
        self._peek_layout()
        self._write_file_header()
        self._write_geometry_header()
        self._write_model_header()
        self._write_names()
        self._write_animations()
        self._write_nodes()
        # Patch file header with final sizes
        self._mdl.patch_u32(4, self._mdl_size)
        self._mdl.patch_u32(8, self._mdx_size)
        return self._mdl.getvalue(), self._mdx.getvalue()

    @classmethod
    def write_files(cls, mesh_data, mdl_path: str, tsl: bool = False) -> None:
        """Write .mdl and .mdx files to disk."""
        writer = cls(mesh_data, tsl=tsl)
        mdl_bytes, mdx_bytes = writer.build()
        Path(mdl_path).write_bytes(mdl_bytes)
        mdx_path = str(Path(mdl_path).with_suffix('.mdx'))
        Path(mdx_path).write_bytes(mdx_bytes)
        log.debug("MDLWriter: wrote %d MDL bytes + %d MDX bytes → %s",
                  len(mdl_bytes), len(mdx_bytes), mdl_path)

    @classmethod
    def to_bytes(cls, mesh_data, tsl: bool = False) -> Tuple[bytes, bytes]:
        """Convenience: build without writing to disk."""
        return cls(mesh_data, tsl=tsl).build()

    # ─────────────────────────────────────────────────────────────────────────
    #  Phase 1: Flatten node tree + collect animation nodes
    # ─────────────────────────────────────────────────────────────────────────

    def _flatten_nodes(self) -> None:
        if self.data.root_node is None:
            # Create a minimal dummy root node
            from gmodular.formats.mdl_parser import MeshNode
            dummy = MeshNode(name=self.data.name or "dummy")
            self.data.root_node = dummy

        def _walk(node, parent_idx):
            idx = len(self._nodes)
            self._nodes.append(node)
            self._parent_idx.append(parent_idx)
            self._child_idxs.append([])
            self._node_idx_by_name[node.name] = idx
            if parent_idx is not None:
                self._child_idxs[parent_idx].append(idx)
            for child in (node.children or []):
                _walk(child, idx)

        _walk(self.data.root_node, None)

        # Flatten animation nodes
        for anim in self._anims:
            anim_nodes = []
            anim_parent = []
            anim_child = []

            def _walk_anim(node, par_idx):
                idx = len(anim_nodes)
                anim_nodes.append(node)
                anim_parent.append(par_idx)
                anim_child.append([])
                if par_idx is not None:
                    anim_child[par_idx].append(idx)
                for child in (node.children or []):
                    _walk_anim(child, idx)

            if anim.root_node is not None:
                _walk_anim(anim.root_node, None)

            self._anim_node_lists.append(anim_nodes)
            self._anim_parent_idx.append(anim_parent)
            self._anim_child_idx.append(anim_child)

    # ─────────────────────────────────────────────────────────────────────────
    #  Phase 2: Layout calculation (peek) — determines all offsets
    # ─────────────────────────────────────────────────────────────────────────

    def _peek_layout(self) -> None:
        """Calculate all block offsets without writing anything."""
        pos = self.BASE

        # Geometry header (80 bytes) + Model header (116 bytes)
        pos += 80 + 116

        # Name offset array
        self._off_name_offsets = pos - self.BASE
        pos += 4 * len(self._nodes)

        # Name strings
        self._name_str_offsets = []
        for node in self._nodes:
            self._name_str_offsets.append(pos - self.BASE)
            pos += len(node.name.encode('ascii', 'replace')) + 1

        # Animation offset array
        self._off_anim_offsets = pos - self.BASE
        pos += 4 * len(self._anims)

        # Animation headers + events + nodes
        for anim_idx, anim in enumerate(self._anims):
            self._anim_offsets.append(pos - self.BASE)
            pos += 136  # animation geo header size

            self._anim_events_off.append(pos - self.BASE)
            events = list(getattr(anim, 'events', []) or [])
            pos += 36 * len(events)

            self._anim_node_offsets.append([])
            self._anim_child_off.append([])
            self._anim_ctrl_keys.append([])
            self._anim_ctrl_data.append([])
            self._anim_ctrl_off.append([])
            self._anim_ctrl_data_off.append([])
            self._anim_ctrl_cnt.append([])
            self._anim_ctrl_data_cnt.append([])

            anim_nodes = self._anim_node_lists[anim_idx] if anim_idx < len(self._anim_node_lists) else []
            for node_idx2, anode in enumerate(anim_nodes):
                self._anim_node_offsets[anim_idx].append(pos - self.BASE)
                pos += 80  # base node header

                child_idxs = self._anim_child_idx[anim_idx][node_idx2]
                self._anim_child_off[anim_idx].append(pos - self.BASE)
                pos += 4 * len(child_idxs)

                ck, cd = self._build_anim_controllers(anode)
                self._anim_ctrl_keys[anim_idx].append(ck)
                self._anim_ctrl_data[anim_idx].append(cd)
                self._anim_ctrl_cnt[anim_idx].append(len(ck))
                self._anim_ctrl_data_cnt[anim_idx].append(len(cd))
                self._anim_ctrl_off[anim_idx].append(pos - self.BASE)
                pos += 16 * len(ck)
                self._anim_ctrl_data_off[anim_idx].append(pos - self.BASE)
                pos += 4 * len(cd)

        # Node blocks
        mdx_pos = 0
        for ni, node in enumerate(self._nodes):
            self._node_offsets.append(pos - self.BASE)
            pos += 80  # base node header (geometry header)

            flags = node.flags

            # Type-specific headers
            if flags & NODE_LIGHT:
                pos += 92
            if flags & NODE_EMITTER:
                pos += 224
            if flags & NODE_REF:
                pos += 36
            if flags & NODE_MESH:
                pos += 332
                if self.tsl:
                    pos += 8
            if flags & NODE_SKIN:
                pos += 100
            if flags & NODE_DANGLY:
                pos += 28
            if flags & NODE_AABB:
                pos += 4

            # Mesh data
            if flags & NODE_MESH:
                verts = node.vertices or []
                faces = node.faces or []
                nv = len(verts)
                nf = len(faces)

                # Compute bounding box, average, radius, area
                self._bb[ni], self._avg_map[ni], self._radius_map[ni], self._area_map[ni] = \
                    self._compute_mesh_stats(verts, faces)

                # Faces block (32 bytes each)
                self._faces_off[ni] = pos - self.BASE
                pos += 32 * nf

                # Index offset
                self._idx_off[ni] = pos - self.BASE
                pos += 4

                # Vertex array (XYZ floats)
                self._verts_off[ni] = pos - self.BASE
                pos += 4 * 3 * nv

                # Index count
                self._idx_cnt_off[ni] = pos - self.BASE
                pos += 4

                # Inverted counter
                self._inv_cnt_off[ni] = pos - self.BASE
                pos += 4

                # Vertex indices
                pos += 2 * 3 * nf

                # MDX data
                self._mdx_off[ni] = mdx_pos
                mdx_stride = 4 * 3  # position
                mdx_stride += 4 * 3  # normal
                uvs = node.uvs or []
                uvs2 = node.uvs2 or []
                if uvs:
                    mdx_stride += 4 * 2
                if uvs2:
                    mdx_stride += 4 * 2
                if flags & NODE_SKIN:
                    mdx_stride += 4 * 4  # bone weights
                    mdx_stride += 4 * 4  # bone indices (as floats)
                mdx_pos += mdx_stride * (nv + 1)  # +1 for sentinel

            # Skin data
            if flags & NODE_SKIN:
                num_all_nodes = len(self._nodes)
                self._bonemap_off[ni] = pos - self.BASE
                pos += 4 * num_all_nodes  # floats
                self._qbone_off[ni] = pos - self.BASE
                pos += 4 * 4 * num_all_nodes  # quaternion per bone
                self._tbone_off[ni] = pos - self.BASE
                pos += 4 * 3 * num_all_nodes  # translation per bone
                self._skin_garbage_off[ni] = pos - self.BASE
                pos += 4 * num_all_nodes

            # Dangly data
            if flags & NODE_DANGLY:
                self._constraints_off[ni] = pos - self.BASE
                # constraints: one float per vert as weights
                nv2 = len(node.vertices or [])
                pos += 4 * nv2
                self._dangly_verts_off[ni] = pos - self.BASE
                pos += 4 * 3 * nv2

            # AABB tree
            if flags & NODE_AABB:
                tree = _build_aabb_tree(node.faces or [], node.vertices or [])
                self._aabb_trees[ni] = tree
                self._aabb_off[ni] = []
                for _ in tree:
                    self._aabb_off[ni].append(pos - self.BASE)
                    pos += 40

            # Children pointer array
            self._children_offsets.append(pos - self.BASE)
            pos += 4 * len(self._child_idxs[ni])

            # Controllers
            ck, cd = self._build_static_controllers(node, flags)
            self._ctrl_keys.append(ck)
            self._ctrl_data.append(cd)
            self._ctrl_offsets.append(pos - self.BASE)
            pos += 16 * len(ck)
            self._ctrl_data_offsets.append(pos - self.BASE)
            pos += 4 * len(cd)

        self._mdl_size = pos - self.BASE
        self._mdx_size = mdx_pos

    # ─────────────────────────────────────────────────────────────────────────
    #  Controller builders
    # ─────────────────────────────────────────────────────────────────────────

    def _build_static_controllers(self, node, flags) -> Tuple[list, list]:
        """Build position/orientation/mesh controllers from node bind-pose."""
        if node.parent is None:
            return [], []
        keys = []
        data = []
        dc = 0

        # Position
        pos = node.position or (0.0, 0.0, 0.0)
        keys.append((CTRL_POSITION, 1, dc, dc + 1, 3))
        data.append(0.0)  # timekey
        data.extend(pos)
        dc += 4

        # Orientation (stored xyzw in MeshNode, written as xyzw)
        rot = node.rotation or (0.0, 0.0, 0.0, 1.0)  # xyzw
        keys.append((CTRL_ORIENTATION, 1, dc, dc + 1, 4))
        data.append(0.0)
        data.extend(rot)  # x, y, z, w
        dc += 5

        # Mesh-specific
        if flags & NODE_MESH:
            alpha = getattr(node, 'alpha', 1.0)
            keys.append((CTRL_ALPHA, 1, dc, dc + 1, 1))
            data.append(0.0); data.append(alpha); dc += 2

            scale = 1.0
            keys.append((CTRL_SCALE, 1, dc, dc + 1, 1))
            data.append(0.0); data.append(scale); dc += 2

            diffuse = getattr(node, 'diffuse', (0.8, 0.8, 0.8))
            keys.append((CTRL_SELF_ILLUM, 1, dc, dc + 1, 3))
            data.append(0.0); data.extend(diffuse[:3]); dc += 4

        # Emitter-specific controllers — write static (t=0) keyframes for each
        # emitter parameter that the node carries.  Real animated emitters will
        # override these via _build_anim_controllers.
        if flags & NODE_EMITTER:
            def _em_ctrl1(ctrl_id, val):
                nonlocal dc
                keys.append((ctrl_id, 1, dc, dc + 1, 1))
                data.append(0.0); data.append(float(val)); dc += 2

            def _em_ctrl3(ctrl_id, vals):
                nonlocal dc
                keys.append((ctrl_id, 1, dc, dc + 1, 3))
                data.append(0.0)
                v3 = list(vals)[:3] + [0.0] * (3 - len(list(vals)[:3]))
                data.extend(v3); dc += 4

            _em_ctrl1(CTRL_EM_BIRTHRATE,   getattr(node, 'birthrate',   5.0)  or 5.0)
            _em_ctrl1(CTRL_EM_LIFE_EXP,    getattr(node, 'life_exp',    1.0)  or 1.0)
            _em_ctrl1(CTRL_EM_VELOCITY,    getattr(node, 'velocity',    1.0)  or 1.0)
            _em_ctrl1(CTRL_EM_SPREAD,      getattr(node, 'spread',      0.0)  or 0.0)
            _em_ctrl1(CTRL_EM_SIZE_START,  getattr(node, 'size_start',  0.1)  or 0.1)
            _em_ctrl1(CTRL_EM_SIZE_END,    getattr(node, 'size_end',    0.05) or 0.05)
            _em_ctrl1(CTRL_EM_ALPHA_START, getattr(node, 'alpha_start', 1.0)  or 1.0)
            _em_ctrl1(CTRL_EM_ALPHA_END,   getattr(node, 'alpha_end',   0.0)  or 0.0)
            _em_ctrl1(CTRL_EM_GRAVITY,     getattr(node, 'gravity',     0.0)  or 0.0)
            _em_ctrl1(CTRL_EM_MASS,        getattr(node, 'mass',        1.0)  or 1.0)
            _em_ctrl1(CTRL_EM_X_SIZE,      getattr(node, 'x_size',      2.0)  or 2.0)
            _em_ctrl1(CTRL_EM_Y_SIZE,      getattr(node, 'y_size',      2.0)  or 2.0)
            _em_ctrl1(CTRL_EM_FPS,         getattr(node, 'fps',         24.0) or 24.0)
            _em_ctrl1(CTRL_EM_FRAME_START, getattr(node, 'frame_start', 0.0)  or 0.0)
            _em_ctrl1(CTRL_EM_FRAME_END,   getattr(node, 'frame_end',   0.0)  or 0.0)
            _em_ctrl3(CTRL_EM_COLOR_START,
                      getattr(node, 'color_start', (1.0, 1.0, 1.0)) or (1.0, 1.0, 1.0))
            _em_ctrl3(CTRL_EM_COLOR_MID,
                      getattr(node, 'color_mid',   (1.0, 1.0, 1.0)) or (1.0, 1.0, 1.0))
            _em_ctrl3(CTRL_EM_COLOR_END,
                      getattr(node, 'color_end',   (1.0, 1.0, 1.0)) or (1.0, 1.0, 1.0))

        return keys, data

    def _build_anim_controllers(self, anim_node) -> Tuple[list, list]:
        """Build controllers from animation keyframe data on an anim node."""
        if anim_node.parent is None:
            return [], []
        keys = []
        data = []
        dc = 0

        controllers = getattr(anim_node, 'controllers', {}) or {}

        def _add_ctrl(ctrl_type, num_cols, rows):
            nonlocal dc
            if not rows:
                return
            nr = len(rows)
            keys.append((ctrl_type, nr, dc, dc + nr, num_cols))
            for t, vals in rows:
                data.append(float(t))
            dc += nr
            for t, vals in rows:
                data.extend(float(v) for v in vals[:num_cols])
            dc += nr * num_cols

        pos_rows = controllers.get(CTRL_POSITION, [])
        _add_ctrl(CTRL_POSITION, 3, pos_rows)
        rot_rows = controllers.get(CTRL_ORIENTATION, [])
        _add_ctrl(CTRL_ORIENTATION, 4, rot_rows)

        return keys, data

    # ─────────────────────────────────────────────────────────────────────────
    #  Phase 3: Write blocks
    # ─────────────────────────────────────────────────────────────────────────

    def _write_file_header(self) -> None:
        m = self._mdl
        m.u32(0)  # signature = 0 for binary
        m.u32(0)  # mdl_size placeholder — patched later
        m.u32(0)  # mdx_size placeholder — patched later

    def _write_geometry_header(self) -> None:
        m = self._mdl
        fp1, fp2 = _FP_MODEL_K2_PC if self.tsl else _FP_MODEL_K1_PC
        m.u32(fp1)
        m.u32(fp2)
        name = self.data.name or "unnamed"
        m.cstr(name, 32)
        # root node offset (relative to BASE)
        m.u32(self._node_offsets[0] if self._node_offsets else 0)
        m.u32(len(self._nodes))
        m.put_array_def(0, 0)  # runtime array 1
        m.put_array_def(0, 0)  # runtime array 2
        m.u32(0)   # ref_count
        m.u8(2)    # geometry type = MODEL
        m.u8(0); m.u8(0); m.u8(0)  # padding

    def _write_model_header(self) -> None:
        m = self._mdl
        classification = self.data.classification or "other"
        # classification may be stored as int (raw MDL byte) or as string label
        if isinstance(classification, int):
            cls_byte = classification
        else:
            cls_byte = _CLASS_TO_BYTE.get(str(classification).lower(), 0)
        fog_flag = int(bool(getattr(self.data, 'fog', 0)))
        m.u8(cls_byte)
        m.u8(0)  # subclassification
        m.u8(0)  # unknown
        m.u8(fog_flag)
        m.u32(0)  # child_model_count
        m.put_array_def(self._off_anim_offsets, len(self._anims))
        m.u32(0)  # supermodel_ref

        # Bounding box
        bb_min = self.data.bb_min or (-5.0, -5.0, -1.0)
        bb_max = self.data.bb_max or (5.0, 5.0, 10.0)
        for v in bb_min:
            m.f32(v)
        for v in bb_max:
            m.f32(v)
        m.f32(getattr(self.data, 'radius', 7.0) or 7.0)  # radius
        m.f32(getattr(self.data, 'animation_scale', 1.0) or 1.0)  # anim scale

        supermodel = getattr(self.data, 'supermodel', 'NULL') or 'NULL'
        m.cstr(supermodel, 32)

        # anim root offset = root node
        m.u32(self._node_offsets[0] if self._node_offsets else 0)
        m.u32(0)  # padding
        m.u32(self._mdx_size)
        m.u32(0)  # mdx_offset (always 0 for separate file)
        m.put_array_def(self._off_name_offsets, len(self._nodes))

    def _write_names(self) -> None:
        m = self._mdl
        for off in self._name_str_offsets:
            m.u32(off)
        for node in self._nodes:
            enc = node.name.encode('ascii', 'replace')
            m.bytes_(enc + b'\x00')

    def _write_animations(self) -> None:
        m = self._mdl
        # Animation offset array
        for off in self._anim_offsets:
            m.u32(off)

        for ai, anim in enumerate(self._anims):
            fp1, fp2 = _FP_ANIM_K2_PC if self.tsl else _FP_ANIM_K1_PC
            m.u32(fp1)
            m.u32(fp2)
            anim_name = (anim.name or "default")[:32]
            m.cstr(anim_name, 32)

            anim_nodes = self._anim_node_lists[ai] if ai < len(self._anim_node_lists) else []
            m.u32(self._anim_node_offsets[ai][0] if anim_nodes else 0)
            m.u32(len(anim_nodes))
            m.put_array_def(0, 0)  # runtime
            m.put_array_def(0, 0)  # runtime
            m.u32(0)   # ref count
            m.u8(5)    # model_type = ANIM
            m.u8(0); m.u8(0); m.u8(0)  # padding

            m.f32(getattr(anim, 'length', 0.0) or 0.0)
            m.f32(getattr(anim, 'transition', 0.25) or 0.25)

            anim_root = (anim.name or "dummy")[:32]
            m.cstr(anim_root, 32)

            events = list(getattr(anim, 'events', []) or [])
            m.put_array_def(self._anim_events_off[ai], len(events))
            m.u32(0)  # padding

            # Events (36 bytes each: f32 time + char[32] name)
            for ev in events:
                m.f32(getattr(ev, 'time', 0.0))
                ev_name = (getattr(ev, 'name', '') or '')[:32]
                m.cstr(ev_name, 32)

            # Anim nodes
            for ni2, anode in enumerate(anim_nodes):
                par_idx = self._anim_parent_idx[ai][ni2]
                off_parent = self._anim_node_offsets[ai][par_idx] if par_idx is not None else 0

                # Find name index in main node list
                name_index = self._node_idx_by_name.get(anode.name, ni2)

                m.u16(NODE_HEADER)   # type_flags = base only for anim nodes
                m.u16(ni2)           # node_number
                m.u16(name_index)
                m.u16(0)             # padding
                m.u32(self._anim_offsets[ai])   # off_root
                m.u32(off_parent)
                pos3 = anode.position or (0.0, 0.0, 0.0)
                for v in pos3:
                    m.f32(v)
                rot4 = anode.rotation or (0.0, 0.0, 0.0, 1.0)
                for v in rot4:
                    m.f32(v)

                child_idxs = self._anim_child_idx[ai][ni2]
                m.put_array_def(self._anim_child_off[ai][ni2], len(child_idxs))
                m.put_array_def(self._anim_ctrl_off[ai][ni2], self._anim_ctrl_cnt[ai][ni2])
                m.put_array_def(self._anim_ctrl_data_off[ai][ni2], self._anim_ctrl_data_cnt[ai][ni2])

                # Children
                for ci in child_idxs:
                    m.u32(self._anim_node_offsets[ai][ci])

                # Controllers
                for key in self._anim_ctrl_keys[ai][ni2]:
                    ctrl_type, num_rows, tkeys_start, vals_start, num_cols = key
                    m.u32(ctrl_type)
                    m.u16(0xFFFF)
                    m.u16(num_rows)
                    m.u16(tkeys_start)
                    m.u16(vals_start)
                    m.u8(num_cols)
                    m.u8(0); m.u8(0); m.u8(0)  # padding

                # Controller data
                for v in self._anim_ctrl_data[ai][ni2]:
                    m.f32(v)

    def _write_nodes(self) -> None:
        m = self._mdl
        mesh_counter = 0  # for inverted counter calculation

        for ni, node in enumerate(self._nodes):
            flags = node.flags
            par_idx = self._parent_idx[ni]
            off_parent = (self._node_offsets[par_idx] if par_idx is not None else 0)
            child_idxs = self._child_idxs[ni]

            # ── Base node header (80 bytes) ────────────────────────────────
            m.u16(flags)
            m.u16(ni)          # node_number
            m.u16(ni)          # name_index
            m.u16(0)           # padding
            m.u32(0)           # off_root = always 0 for geometry nodes
            m.u32(off_parent)
            pos3 = node.position or (0.0, 0.0, 0.0)
            for v in pos3:
                m.f32(v)
            rot4 = node.rotation or (0.0, 0.0, 0.0, 1.0)
            for v in rot4:
                m.f32(v)
            m.put_array_def(self._children_offsets[ni], len(child_idxs))
            m.put_array_def(self._ctrl_offsets[ni], len(self._ctrl_keys[ni]))
            m.put_array_def(self._ctrl_data_offsets[ni], len(self._ctrl_data[ni]))

            # ── Emitter header (208 / 0xD0 bytes) ─────────────────────────
            # KotOR emitter struct — 52 floats + flags + strings + padding
            # Reference: Kotor.NET MDLBinaryEmitterHeader.cs (xoreos src)
            # Layout (offsets from start of emitter block):
            #   0x00   dead_space    (f32)
            #   0x04   blast_radius  (f32)
            #   0x08   blast_length  (f32)
            #   0x0C   branch_count  (u32)
            #   0x10   control_pt_smoothing (f32)
            #   0x14   x_grid        (u32)
            #   0x18   y_grid        (u32)
            #   0x1C   spawn_type    (u32)  0=Normal 1=Trail
            #   0x20   update_type   (char[32])
            #   0x40   render_type   (char[32])
            #   0x60   blend_type    (char[32])
            #   0x80   texture       (char[32])
            #   0xA0   chunk_name    (char[16])
            #   0xB0   two_sided_tex (u32)
            #   0xB4   loop          (u32)
            #   0xB8   render_order  (u16)
            #   0xBA   frame_blending (u16)
            #   0xBC   depth_texture (char[32 → only 20 bytes here, then 4 byte pad])
            #   0xD0   end of block  (total = 0xD0 = 208 bytes)
            if flags & NODE_EMITTER:
                m.f32(getattr(node, 'dead_space',   0.0) or 0.0)     # +0x00
                m.f32(getattr(node, 'blast_radius', 0.0) or 0.0)     # +0x04
                m.f32(getattr(node, 'blast_length', 0.0) or 0.0)     # +0x08
                m.u32(getattr(node, 'branch_count', 0)  or 0)        # +0x0C
                m.f32(getattr(node, 'control_pt_smoothing', 0.0) or 0.0)  # +0x10
                m.u32(getattr(node, 'x_grid', 1)   or 1)             # +0x14
                m.u32(getattr(node, 'y_grid', 1)   or 1)             # +0x18
                m.u32(getattr(node, 'spawn_type', 0) or 0)           # +0x1C
                # String fields — fixed-width null-padded
                m.cstr(getattr(node, 'update_type', 'Fountain') or 'Fountain', 32)  # +0x20
                m.cstr(getattr(node, 'render_type', 'Normal')   or 'Normal',  32)  # +0x40
                m.cstr(getattr(node, 'blend_type',  'Normal')   or 'Normal',  32)  # +0x60
                m.cstr(getattr(node, 'texture',     '')         or '',         32)  # +0x80
                m.cstr(getattr(node, 'chunk_name',  '')         or '',         16)  # +0xA0
                m.u32(int(bool(getattr(node, 'two_sided_tex',  False))))       # +0xB0
                m.u32(int(bool(getattr(node, 'loop',          False))))        # +0xB4
                m.u16(getattr(node, 'render_order', 0) or 0)                   # +0xB8
                m.u16(int(bool(getattr(node, 'frame_blending', False))))       # +0xBA
                # depth_texture (char[20]) + 4 bytes padding = 24 bytes → total 0xD0 ✓
                m.cstr(getattr(node, 'depth_texture', '') or '', 20)           # +0xBC
                m.write_null_bytes(4)                                           # +0xD0 pad

            # ── Mesh header (332 bytes, or 340 for TSL) ────────────────────
            if flags & NODE_MESH:
                fp1, fp2 = self._mesh_fn_ptrs(flags)
                verts = node.vertices or []
                faces = node.faces or []
                nv = len(verts)
                nf = len(faces)
                uvs = node.uvs or []
                uvs2 = node.uvs2 or []

                bb_min, bb_max, avg, radius, total_area = self._mesh_stats(ni)

                mdx_data_size = 4 * 3  # position
                mdx_data_bitmap = MDX_POSITION
                off_mdx_verts = 0
                mdx_data_size += 4 * 3  # normals
                mdx_data_bitmap |= MDX_NORMALS
                off_mdx_normals = 4 * 3
                off_mdx_uv1 = 0xFFFFFFFF
                off_mdx_uv2 = 0xFFFFFFFF
                cur_off = 4 * 3 + 4 * 3  # pos + normal
                if uvs:
                    off_mdx_uv1 = cur_off
                    mdx_data_bitmap |= MDX_UV1
                    cur_off += 4 * 2
                    mdx_data_size += 4 * 2
                if uvs2:
                    off_mdx_uv2 = cur_off
                    mdx_data_bitmap |= MDX_UV2
                    cur_off += 4 * 2
                    mdx_data_size += 4 * 2
                if flags & NODE_SKIN:
                    mdx_data_size += 4 * 4  # bone weights
                    mdx_data_size += 4 * 4  # bone indices as floats

                texture = getattr(node, 'texture', '') or ''
                lightmap = getattr(node, 'lightmap', '') or ''
                diffuse = getattr(node, 'diffuse', (0.8, 0.8, 0.8)) or (0.8, 0.8, 0.8)
                ambient = getattr(node, 'ambient', (0.2, 0.2, 0.2)) or (0.2, 0.2, 0.2)
                transp = getattr(node, 'transparency_hint', 0) or 0
                has_lm = 1 if (getattr(node, 'has_lightmap', False)) else 0
                rotate_tex = 1 if (getattr(node, 'rotate_texture', False)) else 0
                bg_geom = 1 if (getattr(node, 'background_geom', False)) else 0
                shadow = 1 if (getattr(node, 'has_shadow', True)) else 0
                beaming = 1 if (getattr(node, 'beaming', False)) else 0
                do_render = 1 if (getattr(node, 'render', True)) else 0

                m.u32(fp1); m.u32(fp2)
                m.put_array_def(self._faces_off[ni], nf)
                for v in bb_min: m.f32(v)
                for v in bb_max: m.f32(v)
                m.f32(radius)
                for v in avg: m.f32(v)
                for v in list(diffuse)[:3] + [0.0] * (3 - min(3, len(diffuse))): m.f32(v)
                for v in list(ambient)[:3] + [0.0] * (3 - min(3, len(ambient))): m.f32(v)
                m.u32(transp)
                m.cstr(texture[:32], 32)   # bitmap
                m.cstr(lightmap[:32], 32)  # bitmap2
                m.cstr("", 12)             # bitmap3
                m.cstr("", 12)             # bitmap4

                m.put_array_def(self._idx_cnt_off[ni], 1)     # indices count array
                m.put_array_def(self._idx_off[ni], 1)         # indices offset array
                m.put_array_def(self._inv_cnt_off[ni], 1)     # inverted counter array

                m.u32(0xFFFFFFFF)  # unknown
                m.u32(0xFFFFFFFF)  # unknown
                m.u32(0)           # unknown
                m.u8(3)            # saber unknown
                m.write_null_bytes(7)  # saber unknown padding

                uv_animate = 1 if getattr(node, 'uv_animate', False) else 0
                uv_dir = getattr(node, 'uv_dir', (0.0, 0.0)) or (0.0, 0.0)
                uv_speed = getattr(node, 'uv_speed', 0.0) or 0.0
                uv_jitter = getattr(node, 'uv_jitter', 0.0) or 0.0

                m.u32(uv_animate)
                m.f32(uv_dir[0]); m.f32(uv_dir[1])
                m.f32(uv_jitter)
                m.f32(uv_speed)  # uvjitterspeed
                m.u32(mdx_data_size)
                m.u32(mdx_data_bitmap)
                m.u32(off_mdx_verts)
                m.u32(off_mdx_normals)
                m.u32(0xFFFFFFFF)  # off_mdx_colors
                m.u32(off_mdx_uv1)
                m.u32(off_mdx_uv2)
                m.u32(0xFFFFFFFF)  # UV3
                m.u32(0xFFFFFFFF)  # UV4
                m.u32(0xFFFFFFFF)  # tangent1
                m.u32(0xFFFFFFFF)  # tangent2
                m.u32(0xFFFFFFFF)  # tangent3
                m.u32(0xFFFFFFFF)  # tangent4
                m.u16(nv)
                num_textures = (1 if uvs else 0) + (1 if uvs2 else 0)
                m.u16(num_textures)
                m.u8(has_lm)
                m.u8(rotate_tex)
                m.u8(bg_geom)
                m.u8(shadow)
                m.u8(beaming)
                m.u8(do_render)

                if self.tsl:
                    m.u8(0)   # dirt_enabled
                    m.u8(0)   # padding
                    m.u16(0)  # dirt_texture
                    m.u16(0)  # dirt_worldspace
                    m.u8(0)   # hide_in_holograms
                    m.u8(0)   # padding

                m.u16(0)      # final padding
                m.f32(total_area)
                m.u32(0)      # padding
                m.u32(self._mdx_off.get(ni, 0))
                m.u32(self._verts_off[ni])  # off_vert_array

            # ── Skin header (100 bytes) ────────────────────────────────────
            if flags & NODE_SKIN:
                # Find bone offset within MDX stride for this skin node
                uv_base = 4 * 3 + 4 * 3  # pos + normals
                if node.uvs:
                    uv_base += 4 * 2
                if node.uvs2:
                    uv_base += 4 * 2
                off_mdx_bone_weights = uv_base
                off_mdx_bone_indices = uv_base + 4 * 4

                num_all = len(self._nodes)
                m.put_array_def(0, 0)  # unknown
                m.u32(off_mdx_bone_weights)
                m.u32(off_mdx_bone_indices)
                m.u32(self._bonemap_off[ni])
                m.u32(num_all)
                m.put_array_def(self._qbone_off[ni], num_all)
                m.put_array_def(self._tbone_off[ni], num_all)
                m.put_array_def(self._skin_garbage_off[ni], num_all)
                # bone_node_indices[0..15]
                bni = list(getattr(node, 'bone_node_indices', []) or [])
                for i in range(16):
                    m.u16(bni[i] if i < len(bni) else 0xFFFF)
                m.u32(0)  # padding

            # ── Dangly header (28 bytes) ──────────────────────────────────
            if flags & NODE_DANGLY:
                verts = node.vertices or []
                nv2 = len(verts)
                m.put_array_def(self._constraints_off[ni], nv2)
                m.f32(getattr(node, 'displacement', 0.5))
                m.f32(getattr(node, 'tightness', 1.0))
                m.f32(getattr(node, 'period', 1.0))
                m.u32(self._dangly_verts_off[ni])

            # ── AABB header (4 bytes) ──────────────────────────────────────
            if flags & NODE_AABB:
                tree = self._aabb_trees.get(ni, [])
                offs = self._aabb_off.get(ni, [0])
                m.u32(offs[0] if offs else 0)

            # ── Mesh data ─────────────────────────────────────────────────
            if flags & NODE_MESH:
                verts = node.vertices or []
                faces = node.faces or []
                nv = len(verts)
                nf = len(faces)
                normals = node.normals or []
                uvs = node.uvs or []
                uvs2 = node.uvs2 or []
                face_mats = node.face_materials or []

                # Compute face adjacency (needed for face block)
                face_adj = self._compute_face_adjacency(faces)

                # Faces block (32 bytes each)
                for fi, face in enumerate(faces):
                    v0 = verts[face[0]]
                    if normals and fi < len(normals):
                        nx, ny, nz = normals[fi][0], normals[fi][1], normals[fi][2]
                    else:
                        nx, ny, nz = self._face_normal(verts, face)
                    dist = -(nx * v0[0] + ny * v0[1] + nz * v0[2])
                    mat_id = face_mats[fi] if fi < len(face_mats) else 0
                    adj = face_adj[fi] if fi < len(face_adj) else [-1, -1, -1]
                    m.f32(nx); m.f32(ny); m.f32(nz)
                    m.f32(dist)
                    m.u32(mat_id)
                    m.i16(adj[0]); m.i16(adj[1]); m.i16(adj[2])
                    m.u16(face[0]); m.u16(face[1]); m.u16(face[2])

                # Index offset value
                m.u32(self._verts_off[ni] + nv * 12)  # offset to vertex index block (after verts)
                # The actual offset written here is where the indices array starts

                # Vertex array (XYZ floats)
                for v in verts:
                    m.f32(v[0]); m.f32(v[1]); m.f32(v[2])

                # Vertex index count
                mesh_counter += 1
                inv_count = self._inverted_counter(mesh_counter)
                m.u32(3 * nf)

                # Inverted counter
                m.u32(inv_count)

                # Vertex indices (u16 triplets)
                for face in faces:
                    m.u16(face[0]); m.u16(face[1]); m.u16(face[2])

                # MDX data (interleaved per-vertex)
                mdx = self._mdx
                for vi in range(nv):
                    v = verts[vi]
                    mdx.f32(v[0]); mdx.f32(v[1]); mdx.f32(v[2])  # position
                    if normals and vi < len(normals):
                        n3 = normals[vi]
                        mdx.f32(n3[0]); mdx.f32(n3[1]); mdx.f32(n3[2])
                    else:
                        mdx.f32(0.0); mdx.f32(0.0); mdx.f32(1.0)
                    if uvs and vi < len(uvs):
                        mdx.f32(uvs[vi][0]); mdx.f32(uvs[vi][1])
                    elif uvs:  # more faces than uvs
                        mdx.f32(0.0); mdx.f32(0.0)
                    if uvs2 and vi < len(uvs2):
                        mdx.f32(uvs2[vi][0]); mdx.f32(uvs2[vi][1])
                    elif uvs2:
                        mdx.f32(0.0); mdx.f32(0.0)
                    if flags & NODE_SKIN:
                        bw = node.bone_weights[vi] if vi < len(node.bone_weights or []) else (1.0, 0.0, 0.0, 0.0)
                        bi = node.bone_indices[vi] if vi < len(node.bone_indices or []) else (0, 0, 0, 0)
                        for w in bw[:4]:
                            mdx.f32(w)
                        for idx_ in bi[:4]:
                            mdx.f32(float(idx_))

                # MDX sentinel row (extra "vertex" at end — KotorBlender adds 1 extra)
                mdx.f32(1e7); mdx.f32(1e7); mdx.f32(1e7)   # position sentinel
                mdx.f32(0.0); mdx.f32(0.0); mdx.f32(0.0)   # normal sentinel
                if uvs:
                    mdx.f32(0.0); mdx.f32(0.0)
                if uvs2:
                    mdx.f32(0.0); mdx.f32(0.0)
                if flags & NODE_SKIN:
                    mdx.f32(1.0); mdx.f32(0.0); mdx.f32(0.0); mdx.f32(0.0)
                    mdx.f32(0.0); mdx.f32(0.0); mdx.f32(0.0); mdx.f32(0.0)

            # ── Skin data blocks ──────────────────────────────────────────
            if flags & NODE_SKIN:
                # Bonemap (float per node, -1.0 if not a bone)
                bm = list(getattr(node, 'bone_map', []) or [])
                for ni2 in range(len(self._nodes)):
                    val = float(bm[ni2]) if ni2 < len(bm) else -1.0
                    m.f32(val)
                # QBones / TBones (identity rotation/translation for now)
                nb = len(self._nodes)
                for _ in range(nb):
                    m.f32(1.0); m.f32(0.0); m.f32(0.0); m.f32(0.0)  # identity quat
                for _ in range(nb):
                    m.f32(0.0); m.f32(0.0); m.f32(0.0)   # zero translation
                for _ in range(nb):
                    m.u32(0)  # garbage

            # ── Dangly data ────────────────────────────────────────────────
            if flags & NODE_DANGLY:
                verts = node.vertices or []
                nv2 = len(verts)
                # Per-vertex constraint weights — actual values from node.constraint_weights
                # when available; fallback to 1.0 (fully constrained) per vertex.
                weights = list(getattr(node, 'constraint_weights', None) or [])
                for vi in range(nv2):
                    w = weights[vi] if vi < len(weights) else 1.0
                    m.f32(float(w))
                for v in verts:
                    m.f32(v[0]); m.f32(v[1]); m.f32(v[2])

            # ── AABB tree data ─────────────────────────────────────────────
            if flags & NODE_AABB:
                tree = self._aabb_trees.get(ni, [])
                offs = self._aabb_off.get(ni, [])
                for ti, tnode in enumerate(tree):
                    bb_min = tnode['bb_min']
                    bb_max = tnode['bb_max']
                    fi = tnode['face_idx']
                    split_ax = tnode['split_axis']
                    left = tnode['left']
                    right = tnode['right']

                    for v in bb_min: m.f32(v)
                    for v in bb_max: m.f32(v)

                    if fi == -1:
                        m.u32(offs[left] if left >= 0 else 0)
                        m.u32(offs[right] if right >= 0 else 0)
                        m.i32(-1)
                    else:
                        m.u32(0)
                        m.u32(0)
                        m.i32(fi)
                    m.u32(_SPLIT_AXIS.get(split_ax, 0))

            # ── Children pointers ──────────────────────────────────────────
            for ci in child_idxs:
                m.u32(self._node_offsets[ci])

            # ── Controllers ────────────────────────────────────────────────
            for key in self._ctrl_keys[ni]:
                ctrl_type, num_rows, tkeys_start, vals_start, num_cols = key
                m.u32(ctrl_type)
                m.u16(0xFFFF)
                m.u16(num_rows)
                m.u16(tkeys_start)
                m.u16(vals_start)
                m.u8(num_cols)
                m.u8(0); m.u8(0); m.u8(0)  # padding

            # ── Controller data ────────────────────────────────────────────
            for v in self._ctrl_data[ni]:
                m.f32(v)

    # ─────────────────────────────────────────────────────────────────────────
    #  Helper methods
    # ─────────────────────────────────────────────────────────────────────────

    def _mesh_fn_ptrs(self, flags: int) -> Tuple[int, int]:
        if flags & NODE_SKIN:
            return _FP_SKIN_K2_PC if self.tsl else _FP_SKIN_K1_PC
        if flags & NODE_DANGLY:
            return _FP_DANGLY_K2_PC if self.tsl else _FP_DANGLY_K1_PC
        return _FP_MESH_K2_PC if self.tsl else _FP_MESH_K1_PC

    def _mesh_stats(self, ni: int):
        # _bb[ni] is always a tuple (bb_min, bb_max) as set by _compute_mesh_stats
        bb_entry = self._bb.get(ni)
        if bb_entry is not None:
            bb_min, bb_max = bb_entry
        else:
            bb_min = (-1.0, -1.0, -1.0)
            bb_max = (1.0, 1.0, 1.0)
        avg = self._avg_map.get(ni, (0.0, 0.0, 0.0))
        radius = self._radius_map.get(ni, 1.0)
        area = self._area_map.get(ni, 0.0)
        return bb_min, bb_max, avg, radius, area

    def _compute_mesh_stats(self, verts, faces):
        if not verts or not faces:
            return ((-1., -1., -1.), (1., 1., 1.)), (0., 0., 0.), 1.0, 0.0
        xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
        bb_min = (min(xs), min(ys), min(zs))
        bb_max = (max(xs), max(ys), max(zs))
        avg = (sum(xs)/len(xs), sum(ys)/len(ys), sum(zs)/len(zs))
        cx, cy, cz = avg
        radius = max(
            math.sqrt((v[0]-cx)**2 + (v[1]-cy)**2 + (v[2]-cz)**2)
            for v in verts
        ) if verts else 1.0

        # Compute total area
        total_area = 0.0
        for face in faces:
            if max(face) < len(verts):
                v0 = verts[face[0]]; v1 = verts[face[1]]; v2 = verts[face[2]]
                e1 = (v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2])
                e2 = (v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2])
                cx2 = e1[1]*e2[2] - e1[2]*e2[1]
                cy2 = e1[2]*e2[0] - e1[0]*e2[2]
                cz2 = e1[0]*e2[1] - e1[1]*e2[0]
                a = math.sqrt(cx2*cx2 + cy2*cy2 + cz2*cz2) * 0.5
                total_area += a

        # Store as tuple for _mesh_stats retrieval
        return (bb_min, bb_max), avg, radius, total_area

    def _face_normal(self, verts, face):
        try:
            v0, v1, v2 = verts[face[0]], verts[face[1]], verts[face[2]]
            e1 = (v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2])
            e2 = (v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2])
            nx = e1[1]*e2[2] - e1[2]*e2[1]
            ny = e1[2]*e2[0] - e1[0]*e2[2]
            nz = e1[0]*e2[1] - e1[1]*e2[0]
            mag = math.sqrt(nx*nx + ny*ny + nz*nz) or 1.0
            return nx/mag, ny/mag, nz/mag
        except (IndexError, ZeroDivisionError):
            return 0.0, 0.0, 1.0

    def _compute_face_adjacency(self, faces):
        nf = len(faces)
        adj = [[-1, -1, -1] for _ in range(nf)]
        # Build edge → face/edge index map
        edge_map: Dict[tuple, Tuple[int, int]] = {}
        for fi, face in enumerate(faces):
            for ei in range(3):
                a = face[ei]
                b = face[(ei + 1) % 3]
                key = (min(a, b), max(a, b))
                if key in edge_map:
                    fi2, ei2 = edge_map[key]
                    adj[fi][ei] = fi2
                    adj[fi2][ei2] = fi
                else:
                    edge_map[key] = (fi, ei)
        return adj

    @staticmethod
    def _inverted_counter(count: int) -> int:
        """KotorBlender's inverted mesh counter formula."""
        quo = count // 100
        mod = count % 100
        return int(
            math.pow(2, quo) * 100 - count
            + (100 * quo if mod else 0)
            + (0 if quo else -1)
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Convenience top-level functions
# ─────────────────────────────────────────────────────────────────────────────

def write_mdl(mesh_data, mdl_path: str, tsl: bool = False) -> None:
    """Write a MeshData object to binary MDL + MDX files."""
    MDLWriter.write_files(mesh_data, mdl_path, tsl=tsl)


def mdl_to_bytes(mesh_data, tsl: bool = False) -> Tuple[bytes, bytes]:
    """Convert a MeshData object to (mdl_bytes, mdx_bytes) without writing to disk."""
    return MDLWriter.to_bytes(mesh_data, tsl=tsl)
