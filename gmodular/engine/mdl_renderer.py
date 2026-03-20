"""
GModular — MDL Renderer
Uploads parsed MeshData (from mdl_parser.py) to ModernGL VAOs and
renders KotOR room models in the 3D viewport.

Architecture:
  MDLParser → MeshData → MDLRenderer.upload() → VAO list per model
  ViewportWidget.paintGL() → MDLRenderer.render_all()

Features:
  - Per-mesh VAO creation (vertex + normal + UV interleaved, 8 floats/vertex)
  - LRU eviction via ModelCache (max 64 models)
  - Frustum-sphere culling (skips off-screen rooms)
  - Per-mesh frustum culling (skips occluded sub-meshes)
  - Door-hook node detection (DW_* dummies for snap targets)
  - Wireframe overlay mode for debugging
  - Normal-visualisation mode (debug shader shows face normals as colours)
  - Texture-name registry for deferred TGA/TPC loading
  - Flat-colour fallback when ModernGL / numpy is absent
  - MDX normals and UVs already interleaved by MDLParser

Lit GLSL shaders are defined inline (no external .glsl files needed
in the frozen EXE).
"""
from __future__ import annotations
import math
import logging
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

log = logging.getLogger(__name__)

# ── Optional numpy import ───────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore

# ── Optional ModernGL import ────────────────────────────────────────────────
try:
    import moderngl
    _HAS_MGL = True
except ImportError:
    _HAS_MGL = False
    moderngl = None  # type: ignore

from ..formats.mdl_parser import MeshData, MeshNode, get_model_cache

# ─────────────────────────────────────────────────────────────────────────────
#  GLSL shaders
# ─────────────────────────────────────────────────────────────────────────────

# ── Lit Phong shader (KotOR Z-up coordinate system) ──────────────────────────
_VERT_LIT = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;

uniform mat4 u_mvp;
uniform mat4 u_model;

out vec3 v_normal_ws;
out vec3 v_pos_ws;
out vec2 v_uv;

void main() {
    vec4 world_pos = u_model * vec4(in_position, 1.0);
    v_pos_ws    = world_pos.xyz;
    v_normal_ws = mat3(transpose(inverse(u_model))) * in_normal;
    v_uv        = in_uv;
    gl_Position = u_mvp * vec4(in_position, 1.0);
}
"""

_FRAG_LIT = """
#version 330 core
in vec3 v_normal_ws;
in vec3 v_pos_ws;
in vec2 v_uv;

uniform vec3  u_diffuse;
uniform vec3  u_ambient;
uniform vec3  u_light_dir;   // world-space, normalised, pointing toward light
uniform float u_alpha;
uniform bool  u_use_tint;    // true = debug normal-as-color, false = material colour

out vec4 frag_color;

void main() {
    vec3 N   = normalize(v_normal_ws);
    // Sun light from above-right (KotOR default sky light direction)
    float NdL = max(dot(N, normalize(u_light_dir)), 0.0);
    vec3 col;
    if (u_use_tint) {
        // Normal debug: show normals as RGB
        col = N * 0.5 + 0.5;
    } else {
        col = u_ambient + u_diffuse * (0.5 + 0.5 * NdL);
    }
    frag_color = vec4(clamp(col, 0.0, 1.0), u_alpha);
}
"""

# ── Wireframe shader (solid-colour lines) ────────────────────────────────────
_VERT_WIRE = """
#version 330 core
in vec3 in_position;
uniform mat4 u_mvp;
void main() {
    gl_Position = u_mvp * vec4(in_position, 1.0);
}
"""

_FRAG_WIRE = """
#version 330 core
uniform vec4 u_color;
out vec4 frag_color;
void main() {
    frag_color = u_color;
}
"""

# ── Flat-colour billboard shader (for GIT object icons) ──────────────────────
_VERT_FLAT = """
#version 330 core
in vec3 in_position;
uniform mat4 u_mvp;
out vec2 v_uv;
void main() {
    v_uv = in_position.xy * 0.5 + 0.5;
    gl_Position = u_mvp * vec4(in_position, 1.0);
}
"""

_FRAG_FLAT = """
#version 330 core
in vec2 v_uv;
uniform vec4 u_color;
out vec4 frag_color;
void main() {
    frag_color = u_color;
}
"""

# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DoorHook:
    """A door-hook dummy node extracted from MDL (node name starts with DW_)."""
    name:     str
    position: Tuple[float, float, float]
    normal:   Tuple[float, float, float] = (0.0, 1.0, 0.0)   # facing direction


@dataclass
class UploadedMesh:
    """OpenGL resources for a single MeshNode."""
    vao:        Any                                           # moderngl.VertexArray
    vao_wire:   Any = None                                    # wire VAO (lines)
    index_cnt:  int = 0                                       # number of indices
    diffuse:    Tuple[float, float, float] = (0.8, 0.8, 0.8)
    ambient:    Tuple[float, float, float] = (0.2, 0.2, 0.2)
    alpha:      float = 1.0
    bb_center:  Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bb_radius:  float = 1.0
    texture_name: str = ""    # raw texture name from MDL (no extension)


@dataclass
class UploadedModel:
    """All GPU data for one parsed MDL model."""
    name:       str
    meshes:     List[UploadedMesh] = field(default_factory=list)
    door_hooks: List[DoorHook]    = field(default_factory=list)
    bb_min:     Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bb_max:     Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius:     float = 1.0

    @property
    def center(self) -> Tuple[float, float, float]:
        return (
            (self.bb_min[0] + self.bb_max[0]) * 0.5,
            (self.bb_min[1] + self.bb_max[1]) * 0.5,
            (self.bb_min[2] + self.bb_max[2]) * 0.5,
        )

    def all_texture_names(self) -> List[str]:
        """Return de-duplicated list of texture names used by this model."""
        names: List[str] = []
        seen = set()
        for m in self.meshes:
            t = m.texture_name
            if t and t not in seen:
                seen.add(t)
                names.append(t)
        return names


# ─────────────────────────────────────────────────────────────────────────────
#  Frustum sphere-cull helper
# ─────────────────────────────────────────────────────────────────────────────

def _frustum_cull(center_ws, radius: float, vp_mat) -> bool:
    """
    Returns True if the sphere should be CULLED (not rendered).
    Uses 6 half-space tests extracted from the view-projection matrix.
    center_ws is (x,y,z) in world space; vp_mat is a (4,4) float32 numpy array.

    Plane extraction: column-major VP → row[i] of VP^T gives plane equation coefficients.
    Sign convention: dist = dot(plane_normal, pos) + plane_d; cull if dist < -radius.
    """
    if not _HAS_NUMPY:
        return False
    cx, cy, cz = center_ws
    # 6 planes (left, right, bottom, top, near, far) extracted from VP rows
    planes = [
        vp_mat[3] + vp_mat[0],   # left
        vp_mat[3] - vp_mat[0],   # right
        vp_mat[3] + vp_mat[1],   # bottom
        vp_mat[3] - vp_mat[1],   # top
        vp_mat[3] + vp_mat[2],   # near
        vp_mat[3] - vp_mat[2],   # far
    ]
    for plane in planes:
        dist = plane[0]*cx + plane[1]*cy + plane[2]*cz + plane[3]
        if dist < -radius:
            return True   # outside this plane → cull
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Vertex buffer helpers
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_node(node: MeshNode):
    """
    Build interleaved float32 array (pos XYZ + normal XYZ + uv UV = 8 floats/vertex)
    and a uint16 index array from a MeshNode.

    Returns (vertex_buf: np.ndarray[f4, shape=(N*8,)],
             index_buf:  np.ndarray[u2, shape=(M,)])
    or (None, None) on failure.

    The MDX normals and UVs are already extracted into node.normals / node.uvs
    by MDLParser._parse_mesh(); this function just packages them.
    """
    if not _HAS_NUMPY:
        return None, None
    verts = node.vertices
    norms = node.normals
    uvs   = node.uvs
    faces = node.faces

    if not verts or not faces:
        return None, None

    n_verts = len(verts)
    has_n  = len(norms) == n_verts
    has_uv = len(uvs)   == n_verts

    # 8 floats per vertex: px py pz nx ny nz u v
    buf = np.zeros((n_verts, 8), dtype='f4')
    # Positions
    for i, v in enumerate(verts):
        buf[i, 0] = v[0]; buf[i, 1] = v[1]; buf[i, 2] = v[2]
    # Normals
    if has_n:
        for i, n in enumerate(norms):
            buf[i, 3] = n[0]; buf[i, 4] = n[1]; buf[i, 5] = n[2]
    else:
        # Compute per-face normals and assign to vertices
        face_normals = np.zeros((n_verts, 3), dtype='f4')
        face_counts  = np.zeros(n_verts, dtype='i4')
        verts_np = np.array(verts, dtype='f4')
        for f in faces:
            if max(f) >= n_verts:
                continue
            v0, v1, v2 = verts_np[f[0]], verts_np[f[1]], verts_np[f[2]]
            e1 = v1 - v0; e2 = v2 - v0
            fn = np.cross(e1, e2)
            mag = np.linalg.norm(fn)
            if mag > 1e-9:
                fn /= mag
            for vi in f:
                face_normals[vi] += fn
                face_counts[vi]  += 1
        for i in range(n_verts):
            if face_counts[i] > 0:
                n = face_normals[i] / face_counts[i]
                mag = np.linalg.norm(n)
                if mag > 1e-9:
                    n /= mag
                buf[i, 3:6] = n
        buf[:, 5] = np.where(buf[:, 5] == 0, 1.0, buf[:, 5])  # fallback Z-up

    # UVs
    if has_uv:
        for i, uv in enumerate(uvs):
            buf[i, 6] = uv[0]; buf[i, 7] = uv[1]

    # Index buffer (triangles)
    idx = []
    for f in faces:
        if max(f) < n_verts:
            idx.extend(f)
    if not idx:
        return None, None

    return buf.flatten(), np.array(idx, dtype='u2')


def _wireframe_indices(faces, n_verts: int):
    """
    Convert triangle face list to line-segment indices for wireframe rendering.
    Each triangle (a,b,c) → edges (a,b), (b,c), (c,a).
    De-duplicates edges so each edge is only drawn once.
    Returns np.ndarray[u2] or None.
    """
    if not _HAS_NUMPY:
        return None
    edges = set()
    for f in faces:
        if max(f) >= n_verts:
            continue
        a, b, c = f
        for e in ((min(a,b), max(a,b)), (min(b,c), max(b,c)), (min(a,c), max(a,c))):
            edges.add(e)
    if not edges:
        return None
    flat = []
    for e0, e1 in edges:
        flat.extend([e0, e1])
    return np.array(flat, dtype='u2')


# ─────────────────────────────────────────────────────────────────────────────
#  Door-hook extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_door_hooks(mesh_data: MeshData) -> List[DoorHook]:
    """
    Walk the MDL node tree looking for dummy nodes whose names start with
    'DW_' or 'doorway_' (KotOR door-hook convention).
    Returns a list of DoorHook objects with world-space position.

    Door-hook facing direction is derived from the node's rotation quaternion
    by rotating the local +Y axis into world space.
    """
    from ..formats.mdl_parser import _world_pos, _quat_rotate
    hooks = []
    for node in mesh_data.all_nodes():
        name_lower = node.name.lower()
        if name_lower.startswith('dw_') or name_lower.startswith('doorway_'):
            try:
                wp = _world_pos(node)
            except Exception:
                wp = node.position
            # Derive facing (local +Y rotated by node's quaternion)
            rx, ry, rz, rw = node.rotation
            q = (rx, ry, rz, rw)
            facing = _quat_rotate(q, (0.0, 1.0, 0.0))
            hooks.append(DoorHook(
                name=node.name,
                position=wp,
                normal=(facing[0], facing[1], facing[2]),
            ))
    return hooks


# ─────────────────────────────────────────────────────────────────────────────
#  Render modes
# ─────────────────────────────────────────────────────────────────────────────

class RenderMode:
    SOLID      = "solid"       # Phong-lit solid
    WIREFRAME  = "wireframe"   # Wireframe overlay (lines only)
    SOLID_WIRE = "solid_wire"  # Solid + wireframe overlay
    NORMALS    = "normals"     # Face normals as vertex colour


# ─────────────────────────────────────────────────────────────────────────────
#  MDLRenderer — main GPU upload / render class
# ─────────────────────────────────────────────────────────────────────────────

class MDLRenderer:
    """
    Manages GPU resources for KotOR room MDL models.

    Usage (inside ViewportWidget after initializeGL)::

        self._mdl_renderer = MDLRenderer(self.ctx)
        # Load a room model:
        self._mdl_renderer.load_model("manm26aa", mdl_bytes, mdx_bytes)
        # In paintGL:
        self._mdl_renderer.render_all(vp_matrix, light_dir=(0.5, 0.5, 1.0))

    Render modes:
        renderer.render_mode = RenderMode.WIREFRAME
        renderer.render_mode = RenderMode.NORMALS

    The renderer holds a reference to the ModernGL context but does NOT
    own it — the context is created by ViewportWidget.initializeGL().
    """

    MAX_MODELS = 64    # LRU eviction after this many models

    def __init__(self, ctx=None):
        self._ctx = ctx
        self._prog:      Optional[Any] = None   # lit Phong GLSL program
        self._prog_wire: Optional[Any] = None   # wireframe GLSL program
        self._models: Dict[str, UploadedModel] = {}
        self._order:  List[str] = []            # LRU insertion order
        self._ready   = False
        self._mode    = RenderMode.SOLID
        self._wire_color: Tuple[float,float,float,float] = (0.3, 0.3, 0.3, 0.4)

        if ctx is not None and _HAS_MGL and _HAS_NUMPY:
            self._init_shaders()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def render_mode(self) -> str:
        return self._mode

    @render_mode.setter
    def render_mode(self, mode: str):
        if mode in (RenderMode.SOLID, RenderMode.WIREFRAME,
                    RenderMode.SOLID_WIRE, RenderMode.NORMALS):
            self._mode = mode
        else:
            log.warning(f"MDLRenderer: unknown render mode {mode!r}")

    @property
    def wireframe_color(self) -> Tuple[float,float,float,float]:
        return self._wire_color

    @wireframe_color.setter
    def wireframe_color(self, rgba: Tuple[float,float,float,float]):
        self._wire_color = rgba

    # ── Shader initialisation ─────────────────────────────────────────────────

    def _init_shaders(self):
        if not _HAS_MGL or not _HAS_NUMPY:
            return
        try:
            self._prog = self._ctx.program(
                vertex_shader=_VERT_LIT,
                fragment_shader=_FRAG_LIT,
            )
            log.info("MDLRenderer: lit shader compiled")
        except Exception as e:
            log.error(f"MDLRenderer: lit shader compile failed: {e}")
            self._prog = None

        try:
            self._prog_wire = self._ctx.program(
                vertex_shader=_VERT_WIRE,
                fragment_shader=_FRAG_WIRE,
            )
            log.info("MDLRenderer: wireframe shader compiled")
        except Exception as e:
            log.warning(f"MDLRenderer: wireframe shader compile failed: {e}")
            self._prog_wire = None

        self._ready = self._prog is not None

    # ── Model loading ─────────────────────────────────────────────────────────

    def load_model(self, name: str, mdl_bytes: bytes, mdx_bytes: bytes = b'') -> Optional[UploadedModel]:
        """
        Parse an MDL/MDX pair, upload geometry to GPU, cache the result.
        Returns the UploadedModel on success, None on failure.
        Safe to call before initializeGL (returns None gracefully).
        """
        from ..formats.mdl_parser import MDLParser
        key = name.lower()

        if key in self._models:
            # Move to end of LRU order (most recently used)
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            return self._models[key]

        if not self._ready:
            return None

        try:
            parser = MDLParser(mdl_bytes, mdx_bytes)
            mesh_data = parser.parse()
        except Exception as e:
            log.warning(f"MDLRenderer: parse failed for '{name}': {e}")
            return None

        return self._upload_mesh_data(key, mesh_data)

    def load_from_cache(self, name: str, game_dir: str = '') -> Optional[UploadedModel]:
        """
        Try to load model from ModelCache (already-parsed MeshData).
        Falls back to scanning game_dir for .mdl/.mdx files.
        """
        key = name.lower()
        if key in self._models:
            return self._models[key]

        mc = get_model_cache()
        mesh_data = mc.get(key) if game_dir else None

        if mesh_data is None and game_dir:
            import os
            for subdir in ('models', 'Models', ''):
                mdl_path = os.path.join(game_dir, subdir, f'{key}.mdl') if subdir \
                           else os.path.join(game_dir, f'{key}.mdl')
                if os.path.exists(mdl_path):
                    mesh_data = mc.load(mdl_path)
                    break

        if mesh_data is None or not self._ready:
            return None

        return self._upload_mesh_data(key, mesh_data)

    def _upload_mesh_data(self, key: str, mesh_data: MeshData) -> Optional[UploadedModel]:
        """Build UploadedModel from parsed MeshData and register in LRU cache."""
        model = UploadedModel(
            name=key,
            bb_min=mesh_data.bb_min,
            bb_max=mesh_data.bb_max,
            radius=mesh_data.radius,
            door_hooks=_extract_door_hooks(mesh_data),
        )

        for node in mesh_data.visible_mesh_nodes():
            um = self._upload_node(node)
            if um is not None:
                model.meshes.append(um)

        if not model.meshes:
            log.debug(f"MDLRenderer: '{key}' has no renderable meshes")

        self._models[key] = model
        self._order.append(key)
        self._evict_lru()

        log.debug(f"MDLRenderer: loaded '{key}' "
                  f"({len(model.meshes)} meshes, {len(model.door_hooks)} door-hooks)")
        return model

    # ── GPU upload ────────────────────────────────────────────────────────────

    def _upload_node(self, node: MeshNode) -> Optional[UploadedMesh]:
        """Upload a single MeshNode to a VAO. Returns None if empty/invalid."""
        vbuf, ibuf = _flatten_node(node)
        if vbuf is None or ibuf is None or len(ibuf) == 0:
            return None
        if not self._ready:
            return None

        try:
            vbo = self._ctx.buffer(vbuf.astype('f4').tobytes())
            ibo = self._ctx.buffer(ibuf.astype('u2').tobytes())
            vao = self._ctx.vertex_array(
                self._prog,
                [(vbo, '3f 3f 2f', 'in_position', 'in_normal', 'in_uv')],
                ibo,
            )
        except Exception as e:
            log.debug(f"MDLRenderer: VAO upload failed for '{node.name}': {e}")
            return None

        # Build wireframe VAO (optional — only if wireframe shader compiled)
        vao_wire = None
        if self._prog_wire is not None:
            try:
                wire_idx = _wireframe_indices(node.faces, len(node.vertices))
                if wire_idx is not None and len(wire_idx) > 0:
                    wire_ibo = self._ctx.buffer(wire_idx.astype('u2').tobytes())
                    # Wire VAO uses only in_position (first 3 floats of each vertex)
                    vao_wire = self._ctx.vertex_array(
                        self._prog_wire,
                        [(vbo, '3f 20x', 'in_position')],
                        wire_ibo,
                    )
            except Exception as e:
                log.debug(f"MDLRenderer: wireframe VAO failed for '{node.name}': {e}")

        # Compute node bounding sphere
        n_verts = len(node.vertices)
        if n_verts > 0:
            verts_np = vbuf.reshape(-1, 8)[:n_verts, :3]
            center   = verts_np.mean(axis=0)
            dists    = np.linalg.norm(verts_np - center, axis=1)
            radius   = float(dists.max()) if len(dists) else 1.0
        else:
            center = np.zeros(3, dtype='f4')
            radius = 1.0

        return UploadedMesh(
            vao=vao,
            vao_wire=vao_wire,
            index_cnt=len(ibuf),
            diffuse=node.diffuse,
            ambient=node.ambient,
            alpha=node.alpha,
            bb_center=tuple(float(c) for c in center),
            bb_radius=radius,
            texture_name=node.texture_clean,
        )

    # ── LRU eviction ──────────────────────────────────────────────────────────

    def _evict_lru(self):
        while len(self._order) > self.MAX_MODELS:
            oldest = self._order.pop(0)
            model = self._models.pop(oldest, None)
            if model:
                for um in model.meshes:
                    self._release_mesh(um)
                log.debug(f"MDLRenderer: evicted '{oldest}' from GPU cache")

    def _release_mesh(self, um: UploadedMesh):
        """Release all GPU resources held by an UploadedMesh."""
        for obj in (um.vao, um.vao_wire):
            if obj is not None:
                try:
                    obj.release()
                except Exception as exc:
                    log.debug("mdl_renderer: VAO release failed: %s", exc)

    # ── Render ────────────────────────────────────────────────────────────────

    def render_model(self, name: str, mvp_mat, model_mat,
                     vp_mat=None,
                     light_dir: Tuple[float, float, float] = (0.57, 0.57, 0.57),
                     alpha_override: Optional[float] = None):
        """
        Render a previously-loaded model.

        Args:
            name:           Model resref (case-insensitive).
            mvp_mat:        numpy (4,4) float32 MVP matrix.
            model_mat:      numpy (4,4) float32 model→world matrix.
            vp_mat:         VP matrix for frustum culling (optional).
            light_dir:      World-space directional light (need not be normalised).
            alpha_override: Override mesh alpha (0..1); None = use mesh alpha.
        """
        if not self._ready:
            return
        key = name.lower()
        model = self._models.get(key)
        if model is None:
            return

        # Normalise light direction
        lx, ly, lz = light_dir
        mag = math.sqrt(lx*lx + ly*ly + lz*lz) or 1.0
        ld = (lx/mag, ly/mag, lz/mag)

        prog = self._prog
        # ModernGL mat4 uniform expects column-major order (same as OpenGL).
        # NumPy arrays are row-major, so we must transpose before writing.
        # The caller provides a standard row-major MVP; we transpose here.
        prog['u_mvp'].write(mvp_mat.T.astype('f4').tobytes())
        prog['u_model'].write(model_mat.T.astype('f4').tobytes())
        prog['u_light_dir'].value = ld
        prog['u_use_tint'].value  = (self._mode == RenderMode.NORMALS)

        # Whole-model frustum cull
        if vp_mat is not None:
            if _frustum_cull(model.center, model.radius, vp_mat):
                return

        draw_solid = self._mode in (RenderMode.SOLID, RenderMode.SOLID_WIRE,
                                    RenderMode.NORMALS)
        draw_wire  = self._mode in (RenderMode.WIREFRAME, RenderMode.SOLID_WIRE)

        for um in model.meshes:
            # Per-mesh frustum cull
            if vp_mat is not None and _frustum_cull(um.bb_center, um.bb_radius, vp_mat):
                continue

            alpha = alpha_override if alpha_override is not None else um.alpha

            # ── Solid pass ────────────────────────────────────────────────────
            if draw_solid:
                prog['u_diffuse'].value = um.diffuse
                prog['u_ambient'].value = um.ambient
                prog['u_alpha'].value   = alpha
                um.vao.render(moderngl.TRIANGLES)

            # ── Wireframe overlay ─────────────────────────────────────────────
            if draw_wire and um.vao_wire is not None and self._prog_wire is not None:
                prog_w = self._prog_wire
                prog_w['u_mvp'].write(mvp_mat.T.astype('f4').tobytes())
                prog_w['u_color'].value = self._wire_color
                um.vao_wire.render(moderngl.LINES)

    def render_all(self, mvp_by_name: Dict[str, Tuple[Any, Any]],
                   vp_mat=None,
                   light_dir: Tuple[float, float, float] = (0.57, 0.57, 0.57)):
        """
        Render all loaded models.

        Args:
            mvp_by_name: Dict mapping resref → (mvp_mat, model_mat) numpy arrays.
            vp_mat:      Optional VP matrix for frustum culling.
            light_dir:   World-space directional light.
        """
        for name, (mvp, model_m) in mvp_by_name.items():
            self.render_model(name, mvp, model_m, vp_mat=vp_mat, light_dir=light_dir)

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_door_hooks(self, name: str) -> List[DoorHook]:
        """Return door-hook positions for a loaded model."""
        key = name.lower()
        model = self._models.get(key)
        return model.door_hooks if model else []

    def get_bounds(self, name: str) -> Tuple[Tuple[float,float,float], Tuple[float,float,float]]:
        """Return (bb_min, bb_max) for a loaded model, or zeros."""
        key = name.lower()
        model = self._models.get(key)
        if model:
            return model.bb_min, model.bb_max
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)

    def is_loaded(self, name: str) -> bool:
        return name.lower() in self._models

    def model_count(self) -> int:
        return len(self._models)

    def get_texture_names(self, name: str) -> List[str]:
        """Return all unique texture names used by a loaded model."""
        key = name.lower()
        model = self._models.get(key)
        return model.all_texture_names() if model else []

    def release_model(self, name: str):
        """Release GPU resources for a single model."""
        key = name.lower()
        model = self._models.pop(key, None)
        if model:
            for um in model.meshes:
                self._release_mesh(um)
            if key in self._order:
                self._order.remove(key)

    def release_all(self):
        """Release all GPU resources. Call before destroying the GL context."""
        for model in self._models.values():
            for um in model.meshes:
                self._release_mesh(um)
        self._models.clear()
        self._order.clear()
        self._ready = False
        log.info("MDLRenderer: all GPU resources released")
