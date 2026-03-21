"""
GModular — EGL Renderer (offscreen ModernGL)
=============================================
Extracted from viewport.py.  Contains ``_EGLRenderer``, the ModernGL / EGL
offscreen rendering back-end used by ``ViewportWidget``.

Keeping it in its own file makes the viewport widget code much easier to
navigate and allows the renderer to be tested (or replaced) independently.

Imports needed by the class:
  * moderngl / EGL bootstrap  — provided by viewport.py at import time,
    guarded by _HAS_MODERNGL
  * numpy                     — guarded by _HAS_NUMPY
  * All shader strings        — re-imported from viewport_shaders
  * Geometry helpers          — re-imported from viewport.py
"""
from __future__ import annotations

import os
import math
import logging
import struct
import ctypes
from typing import Optional, List, Tuple, Dict

log = logging.getLogger(__name__)

# ─── numpy ────────────────────────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore

# ─── ModernGL ─────────────────────────────────────────────────────────────────
_HAS_MODERNGL = False
try:
    import moderngl
    _HAS_MODERNGL = True
except ImportError:
    pass

# ─── Shaders ──────────────────────────────────────────────────────────────────
from .viewport_shaders import (
    _VERT_FLAT, _FRAG_FLAT,
    _VERT_LIT, _FRAG_LIT,
    _VERT_LIT_NO_UV, _FRAG_LIT_NO_UV,
    _VERT_UNIFORM, _FRAG_UNIFORM,
    _VERT_OUTLINE, _FRAG_OUTLINE,
    _VERT_PICKER, _FRAG_PICKER,
    _VERT_TEXTURED, _FRAG_TEXTURED,
    _VERT_SKINNED, _FRAG_SKINNED,
)

# ─── Camera helpers ───────────────────────────────────────────────────────────
from .viewport_camera import _look_at, _perspective

# ─── Geometry helpers (defined in viewport.py, imported lazily to avoid circular) ─
# These are injected by viewport.py after this module loads, so we declare
# module-level stubs that get replaced.  This avoids a circular import.
def _grid_verts(n: int = 25, step: float = 1.0):  # type: ignore
    raise RuntimeError("_grid_verts not yet injected by viewport.py")

def _box_solid(cx, cy, cz, hw, hh, hd, color):  # type: ignore
    raise RuntimeError("_box_solid not yet injected by viewport.py")

def _box_wire(cx, cy, cz, hw, hh, hd, color):  # type: ignore
    raise RuntimeError("_box_wire not yet injected by viewport.py")

def _translation(tx, ty, tz):  # type: ignore
    raise RuntimeError("_translation not yet injected by viewport.py")

# Colour constants — injected by viewport.py after loading
_COLOR_PLACEABLE = (0.2, 0.6, 1.0)
_COLOR_CREATURE  = (1.0, 0.4, 0.2)
_COLOR_DOOR      = (0.8, 0.7, 0.1)
_COLOR_TRIGGER   = (0.2, 1.0, 0.5)
_COLOR_WAYPOINT  = (0.8, 0.2, 0.8)
_COLOR_SOUND     = (0.2, 0.9, 0.9)
_COLOR_STORE     = (0.2, 0.9, 0.3)
_COLOR_SELECTED  = (1.0, 1.0, 0.0)

def _inject_helpers(grid_fn, box_solid_fn, box_wire_fn, translation_fn):
    """Called by viewport.py after import to inject geometry helpers."""
    global _grid_verts, _box_solid, _box_wire, _translation
    _grid_verts  = grid_fn
    _box_solid   = box_solid_fn
    _box_wire    = box_wire_fn
    _translation = translation_fn


# ═=============================================================================
#  _EGLRenderer (extracted verbatim from viewport.py)
# ═=============================================================================

class _EGLRenderer:
    """
    Manages the ModernGL EGL context, shaders, VAOs, textures and FBO.

    Shader programs (upgraded from Kotor.NET analysis):
      _prog_flat       — colour-per-vertex (grid, wireframes)
      _prog_lit        — Phong-lit with UV texture (primary mesh shader)
      _prog_lit_no_uv  — Phong-lit without UV (nodes without UVs)
      _prog_uniform    — uniform colour + alpha (walkmesh overlays)
      _prog_picker     — entity-ID picker (GPU picking readback)

    Textures are cached in _tex_cache: resref→moderngl.Texture
    """

    def __init__(self):
        self.ctx = None          # moderngl.Context
        self._prog_flat      = None   # colour-per-vertex shader
        self._prog_lit       = None   # Phong-lit with UV texture
        self._prog_lit_no_uv = None   # Phong-lit without UV
        self._prog_uniform   = None   # uniform colour + alpha (overlay)
        self._prog_picker    = None   # GPU entity-ID picker shader
        self._prog_outline   = None   # selection outline/highlight shader
        # New shaders (Kotor.NET rework analysis)
        self._prog_textured  = None   # textured Phong (tex0 + optional lightmap tex1)
        self._prog_skinned   = None   # skinned mesh (bone palette) shader
        self._prog_pick      = None   # alias for pick tests (points to _prog_picker)
        self._fbo = None         # current FBO
        self._fbo_size = (0, 0)  # (W, H) of current FBO
        self._depth_rbo = None   # depth renderbuffer attached to _fbo
        self._color_rbo = None   # color renderbuffer attached to _fbo
        self._pick_fbo = None    # separate FBO for picker pass
        self._pick_fbo_size = (0, 0)
        self._grid_vao   = None
        self._grid_count = 0
        self._object_vaos: List[dict] = []
        self._room_vaos: List[dict]   = []
        self._walk_vaos: List[dict]   = []   # walkable tris (green overlay)
        self._nowalk_vaos: List[dict] = []   # non-walkable tris (red overlay)
        self._mdl_vaos: List[dict]    = []   # play-mode only
        self._entity_vaos: List[dict] = []   # entity MDL models (creatures/doors)
        self._skin_vaos:   List[dict] = []   # skinned-mesh nodes requiring bone matrices
        # Texture cache: lowercase resref → moderngl.Texture (or None if failed)
        self._tex_cache: Dict[str, object] = {}
        # Lightmap cache: separate from diffuse textures
        self._lmap_cache: Dict[str, object] = {}
        # Placeholder magenta 4×4 texture for missing textures
        self._placeholder_tex = None
        self._show_walkmesh = True
        self.ready = False
        # Time counter for animated effects (outline pulse, etc.)
        self._render_time: float = 0.0
        # Grid visibility (G key toggles)
        self._show_grid: bool = True

    # ── Initialisation ────────────────────────────────────────────────────────

    def init(self) -> bool:
        """
        Create a ModernGL standalone context + compile shaders.
        Tries multiple backends in order:
          1. EGL (Linux/headless — surfaceless, no display required)
          2. Default (Windows WGL / macOS CGL — uses whatever is available)
        Returns True on success.
        """
        if self.ready:
            return True
        if not _HAS_MODERNGL:
            log.warning("moderngl not installed — 3D rendering unavailable")
            return False

        ctx = None
        backend_used = "none"

        # ── Attempt 1: EGL (Linux headless) ──────────────────────────────────
        if os.name == "posix":
            try:
                import moderngl
                os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
                os.environ.setdefault("MESA_GL_VERSION_OVERRIDE", "3.3")
                os.environ.setdefault("MESA_GLSL_VERSION_OVERRIDE", "330")
                os.environ.setdefault("EGL_PLATFORM", "surfaceless")
                ctx = moderngl.create_standalone_context(backend="egl")
                backend_used = "egl"
                log.debug("GL: EGL backend initialised")
            except Exception as e:
                log.debug(f"EGL init failed ({e}), trying default backend")
                ctx = None

        # ── Attempt 2: Default backend (Windows WGL / macOS / fallback) ─────
        if ctx is None:
            try:
                import moderngl
                ctx = moderngl.create_standalone_context()
                backend_used = "default"
                log.debug("GL: default backend initialised")
            except Exception as e:
                log.error(f"GL default backend failed: {e}")
                return False

        try:
            import moderngl
            self.ctx = ctx
            self.ctx.enable(moderngl.DEPTH_TEST)
            self.ctx.enable(moderngl.CULL_FACE)
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            # ── Compile required shaders ───────────────────────────────────────
            self._prog_flat = self.ctx.program(
                vertex_shader=_VERT_FLAT, fragment_shader=_FRAG_FLAT)

            # Phong-lit with UV (primary mesh shader — Kotor.NET standard)
            try:
                self._prog_lit = self.ctx.program(
                    vertex_shader=_VERT_LIT, fragment_shader=_FRAG_LIT)
            except Exception as e:
                log.warning(f"Lit-UV shader failed, will use flat fallback: {e}")
                self._prog_lit = None

            # Phong-lit without UV (for mesh nodes that have no UV data)
            try:
                self._prog_lit_no_uv = self.ctx.program(
                    vertex_shader=_VERT_LIT_NO_UV, fragment_shader=_FRAG_LIT_NO_UV)
            except Exception as e:
                log.warning(f"Lit-no-UV shader failed: {e}")
                self._prog_lit_no_uv = None

            # Uniform colour + alpha overlay (walkmesh, selection)
            try:
                self._prog_uniform = self.ctx.program(
                    vertex_shader=_VERT_UNIFORM, fragment_shader=_FRAG_UNIFORM)
            except Exception as e:
                log.warning(f"Uniform shader failed: {e}")
                self._prog_uniform = None

            # GPU object picker (entity-ID encoding — matches Kotor.NET picker)
            try:
                self._prog_picker = self.ctx.program(
                    vertex_shader=_VERT_PICKER, fragment_shader=_FRAG_PICKER)
            except Exception as e:
                log.warning(f"Picker shader failed: {e}")
                self._prog_picker = None

            # Alias _prog_pick → _prog_picker (for API tests + backward compat)
            self._prog_pick = self._prog_picker

            # ── Optional: full dual-texture Phong (_prog_textured) ───────────
            try:
                self._prog_textured = self.ctx.program(
                    vertex_shader=_VERT_TEXTURED, fragment_shader=_FRAG_TEXTURED)
            except Exception as e:
                log.debug(f"Textured shader (optional): {e}")
                self._prog_textured = None

            # ── Optional: selection outline shader (_prog_outline) ───────────
            try:
                self._prog_outline = self.ctx.program(
                    vertex_shader=_VERT_OUTLINE, fragment_shader=_FRAG_OUTLINE)
            except Exception as e:
                log.debug(f"Outline shader (optional): {e}")
                self._prog_outline = None

            # ── Optional: skinned mesh shader (_prog_skinned) ────────────────
            try:
                self._prog_skinned = self.ctx.program(
                    vertex_shader=_VERT_SKINNED, fragment_shader=_FRAG_SKINNED)
            except Exception as e:
                log.debug(f"Skinned shader (optional): {e}")
                self._prog_skinned = None

            # ── Build placeholder texture — neutral gray checkerboard ────────
            # Uses a 4×4 gray checkerboard so geometry is always visible but
            # clearly marked as "texture not loaded yet". Much less distracting
            # than the Kotor.NET magenta which overwrites wood/stone textures.
            try:
                # 4×4 RGBA checkerboard: light gray (200,200,200) / medium gray (140,140,140)
                _A, _B = [200, 200, 200, 255], [140, 140, 140, 255]
                ph_pixels = (
                    _A + _B + _A + _B +
                    _B + _A + _B + _A +
                    _A + _B + _A + _B +
                    _B + _A + _B + _A
                )
                ph_data = bytes(ph_pixels)
                ph_tex  = self.ctx.texture((4, 4), 4, ph_data)
                ph_tex.filter = moderngl.NEAREST, moderngl.NEAREST
                ph_tex.repeat_x = True
                ph_tex.repeat_y = True
                self._placeholder_tex = ph_tex
            except Exception as e:
                log.debug(f"Placeholder texture creation failed: {e}")

            self._build_grid()
            self.ready = True
            info = self.ctx.info
            global _GL_BACKEND
            _GL_BACKEND = backend_used
            log.info(f"GL renderer ready [{backend_used}]: {info['GL_RENDERER']} "
                     f"({info['GL_VERSION']})")
            return True
        except Exception as e:
            log.error(f"GL shader/grid init failed: {e}")
            try:
                self.ctx.release()
            except Exception:
                pass
            self.ctx = None
            return False

    def ensure_fbo(self, W: int, H: int):
        """Resize FBO (and pick FBO) if dimensions changed.

        Uses a proper RGBA8 + D24 framebuffer so that DEPTH_TEST works
        correctly during headless (EGL) rendering.  simple_framebuffer()
        creates a colour-only FBO, which silently disables depth testing
        and makes every face render as the same flat colour.
        """
        if not self.ctx:
            return
        W, H = max(W, 1), max(H, 1)
        if (W, H) == self._fbo_size:
            return

        # Release old FBO and its backing renderbuffers
        for attr in ('_fbo', '_pick_fbo'):
            obj = getattr(self, attr, None)
            if obj:
                try: obj.release()
                except Exception: pass
        for attr in ('_depth_rbo', '_color_rbo'):
            obj = getattr(self, attr, None)
            if obj:
                try: obj.release()
                except Exception: pass
        self._depth_rbo = None
        self._color_rbo = None

        # Build a proper FBO with colour + depth renderbuffers
        try:
            color_rbo = self.ctx.renderbuffer((W, H), components=4)
            depth_rbo = self.ctx.depth_renderbuffer((W, H))
            self._fbo = self.ctx.framebuffer(
                color_attachments=[color_rbo],
                depth_attachment=depth_rbo,
            )
            self._color_rbo = color_rbo
            self._depth_rbo = depth_rbo
        except Exception as exc:
            # Fallback: colour-only (depth test won't work but at least renders)
            log.warning("ensure_fbo: depth renderbuffer failed (%s) — "
                        "falling back to colour-only FBO", exc)
            self._fbo = self.ctx.simple_framebuffer((W, H), components=4)

        # Pick FBO uses same size; colour-only is fine (depth not needed)
        try:
            self._pick_fbo = self.ctx.simple_framebuffer((W, H), components=4)
        except Exception:
            self._pick_fbo = None
        self._fbo_size = (W, H)

    # ── Grid ──────────────────────────────────────────────────────────────────

    def _build_grid(self):
        """Build an enhanced UE5-style ground grid with 25-unit radius, 1m step."""
        verts = _grid_verts(n=25, step=1.0)
        vbo = self.ctx.buffer(verts.tobytes())
        self._grid_vao = self.ctx.vertex_array(
            self._prog_flat, [(vbo, "3f 3f", "in_position", "in_color")])
        self._grid_count = len(verts) // 6

    # ── VAO helpers ───────────────────────────────────────────────────────────

    def _upload_flat(self, verts: "np.ndarray") -> dict:
        vbo = self.ctx.buffer(verts.tobytes())
        vao = self.ctx.vertex_array(
            self._prog_flat, [(vbo, "3f 3f", "in_position", "in_color")])
        return {"vao": vao, "vbo": vbo, "count": len(verts) // 6}

    def _upload_positions_only(self, positions: list) -> Optional[dict]:
        """Upload raw position-only triangles for uniform-colour overlay."""
        if not positions or not self._prog_uniform:
            return None
        arr = np.array(positions, dtype='f4').flatten()
        vbo = self.ctx.buffer(arr.tobytes())
        vao = self.ctx.vertex_array(
            self._prog_uniform, [(vbo, "3f", "in_position")])
        return {"vao": vao, "vbo": vbo, "count": len(positions)}

    def _upload_lit_or_flat(self, positions, normals, color: tuple,
                             uvs=None, tex_resref: str = "") -> Optional[dict]:
        """
        Upload mesh triangles with normals (lit) or baked colour (flat).

        If uvs and _prog_lit are available → Phong-lit with UV pass.
        If only normals → Phong-lit without UV (_prog_lit_no_uv).
        Fallback → flat colour-per-vertex.

        tex_resref: lowercase texture name without extension. If provided and
                    the texture is loaded into _tex_cache, it is attached to
                    the returned entry for use during render().
        """
        if not positions:
            return None
        r, g, b = color

        has_uvs   = uvs is not None and len(uvs) == len(positions)
        has_norms = normals is not None and len(normals) == len(positions)
        prog_lit  = self._prog_lit
        prog_nlit = self._prog_lit_no_uv

        # Path A: Phong-lit with UV texture
        if prog_lit and has_norms and has_uvs:
            v = []
            for (px,py,pz),(nx,ny,nz),(u,v_) in zip(positions, normals, uvs):
                v.extend([px,py,pz, nx,ny,nz, u,v_])
            arr = np.array(v, dtype='f4')
            vbo = self.ctx.buffer(arr.tobytes())
            vao = self.ctx.vertex_array(
                prog_lit,
                [(vbo, "3f 3f 2f", "in_position", "in_normal", "in_uv")])
            # Resolve texture
            tex_obj = (self._tex_cache.get(tex_resref.lower())
                       if tex_resref else None)
            return {"vao": vao, "vbo": vbo, "count": len(v) // 8,
                    "lit": True, "lit_uv": True, "color": color,
                    "tex": tex_obj, "tex_resref": tex_resref}

        # Path B: Phong-lit without UV
        if prog_nlit and has_norms:
            v = []
            for (px,py,pz),(nx,ny,nz) in zip(positions, normals):
                v.extend([px,py,pz, nx,ny,nz])
            arr = np.array(v, dtype='f4')
            vbo = self.ctx.buffer(arr.tobytes())
            vao = self.ctx.vertex_array(
                prog_nlit,
                [(vbo, "3f 3f", "in_position", "in_normal")])
            return {"vao": vao, "vbo": vbo, "count": len(v) // 6,
                    "lit": True, "lit_uv": False, "color": color,
                    "tex": None, "tex_resref": ""}

        # Path C: Flat colour-per-vertex fallback
        v = []
        for (px,py,pz) in positions:
            v.extend([px, py, pz, r, g, b])
        arr = np.array(v, dtype='f4')
        vbo = self.ctx.buffer(arr.tobytes())
        vao = self.ctx.vertex_array(
            self._prog_flat, [(vbo, "3f 3f", "in_position", "in_color")])
        return {"vao": vao, "vbo": vbo, "count": len(v) // 6,
                "lit": False, "lit_uv": False, "color": color,
                "tex": None, "tex_resref": ""}

    def _upload_textured_mesh(self, positions: list, normals: list,
                              uvs: list, uvs2: list,
                              color: tuple) -> Optional[dict]:
        """
        Upload a textured mesh VAO for the new dual-sampler textured Phong shader
        (_prog_textured).

        Interleaves: position(3f) + normal(3f) + uv(2f) + uv2(2f) = 10 floats/vert.
        Falls back to _upload_lit_or_flat when:
          - The textured shader is not yet compiled (_prog_textured is None), or
          - Positions list is empty.

        Returns a dict with:
          vao, vbo, count, textured=True|False, color, has_uv, has_uv2
        or None if positions is empty.
        """
        if not positions:
            return None

        has_n   = (len(normals) == len(positions))
        has_uv  = (len(uvs)     == len(positions))
        has_uv2 = (len(uvs2)    == len(positions))

        if self._prog_textured and (has_uv or has_n):
            v = []
            zero2 = (0.0, 0.0)
            zero3 = (0.0, 0.0, 1.0)
            for i, pos in enumerate(positions):
                px, py, pz = pos
                nx, ny, nz = normals[i] if has_n  else zero3
                u1, u2     = uvs[i]     if has_uv  else zero2
                u3, u4     = uvs2[i]    if has_uv2 else zero2
                v.extend([px, py, pz, nx, ny, nz, u1, u2, u3, u4])
            arr = np.array(v, dtype='f4')
            vbo = self.ctx.buffer(arr.tobytes())
            vao = self.ctx.vertex_array(
                self._prog_textured, [(
                    vbo, "3f 3f 2f 2f",
                    "in_position", "in_normal", "in_uv", "in_uv2"
                )])
            return {"vao": vao, "vbo": vbo,
                    "count": len(positions),
                    "textured": True, "color": color,
                    "has_uv": has_uv, "has_uv2": has_uv2}

        # Fallback to standard lit/flat path
        return self._upload_lit_or_flat(
            positions, normals, color, uvs=(uvs if has_uv else None))

    def _upload_skinned_mesh(self, positions: list, normals: list,
                              uvs: list,
                              bone_weights: list, bone_indices: list,
                              color: tuple) -> Optional[dict]:
        """
        Upload a skinned-mesh VAO for the bone-palette vertex shader.

        Vertex layout: 3f pos + 3f normal + 2f uv + 4f bone_weights + 4i bone_indices
                       = 16 floats (64 bytes) per vertex.

        bone_weights: list of (w0,w1,w2,w3) per triangle-vertex
        bone_indices: list of (i0,i1,i2,i3) per triangle-vertex  (int, 0-based into bone_matrices)

        Returns entry dict with skinned=True, or falls back to lit/flat on error.
        """
        if not positions or self._prog_skinned is None:
            return self._upload_lit_or_flat(positions, normals, color)
        try:
            import moderngl
            n = len(positions)
            has_n  = (len(normals)       == n)
            has_uv = (len(uvs)           == n)
            has_bw = (len(bone_weights)  == n)
            has_bi = (len(bone_indices)  == n)
            v: list = []
            for i in range(n):
                px, py, pz = positions[i]
                nx, ny, nz = normals[i]      if has_n  else (0.0, 0.0, 1.0)
                u,  tv     = uvs[i]          if has_uv else (0.0, 0.0)
                w0,w1,w2,w3 = bone_weights[i] if has_bw else (1.0, 0.0, 0.0, 0.0)
                b0,b1,b2,b3 = bone_indices[i] if has_bi else (0,   0,   0,   0)
                # Store bone indices as floats so they share the same buffer
                # (the shader uses ivec4 but we cast in the format string)
                v.extend([px, py, pz, nx, ny, nz, u, tv,
                           w0, w1, w2, w3,
                           float(b0), float(b1), float(b2), float(b3)])
            arr = np.array(v, dtype='f4')
            vbo = self.ctx.buffer(arr.tobytes())
            vao = self.ctx.vertex_array(
                self._prog_skinned,
                [(vbo, "3f 3f 2f 4f 4f",
                  "in_position", "in_normal", "in_uv",
                  "in_bone_weights", "in_bone_indices")])
            return {
                "vao": vao, "vbo": vbo, "count": n,
                "skinned": True, "color": color,
            }
        except Exception as ex:
            log.debug(f"_upload_skinned_mesh fallback: {ex}")
            return self._upload_lit_or_flat(positions, normals, color)

    def load_texture(self, tex_resref: str, rgba_data: bytes,
                     width: int, height: int,
                     is_lightmap: bool = False) -> bool:
        """
        Upload a texture into the GL context and cache it.

        rgba_data: raw RGBA bytes (4 components, width*height*4 bytes).
        is_lightmap: if True, stores in lightmap cache with different filtering.
        Returns True on success.
        Called by the viewport when TPCReader decodes a texture.
        """
        if not self.ctx:
            return False
        key = tex_resref.lower()
        try:
            import moderngl
            tex = self.ctx.texture((width, height), 4, rgba_data)
            # Lightmaps use linear filtering without mipmaps for smooth gradients
            # Diffuse textures use mipmap linear for proper LOD (PyKotor approach)
            if is_lightmap:
                tex.filter = moderngl.LINEAR, moderngl.LINEAR
            else:
                tex.filter = moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR
                try:
                    tex.build_mipmaps()
                except Exception:
                    pass
            tex.repeat_x = True
            tex.repeat_y = True
            cache = self._lmap_cache if is_lightmap else self._tex_cache
            # Release previous texture if overwriting
            old = cache.get(key)
            if old is not None:
                try: old.release()
                except Exception: pass
            cache[key] = tex
            log.debug(f"Texture loaded: '{key}' ({width}×{height}) lightmap={is_lightmap}")
            return True
        except Exception as e:
            log.debug(f"load_texture '{key}' failed: {e}")
            self._tex_cache[key] = None   # mark as failed (avoid re-try)
            return False

    def _release_texture_cache(self):
        """Release all cached GL textures (diffuse + lightmap)."""
        for cache in (self._tex_cache, self._lmap_cache):
            for tex in cache.values():
                if tex is not None:
                    try: tex.release()
                    except Exception: pass
            cache.clear()
        if self._placeholder_tex is not None:
            try: self._placeholder_tex.release()
            except Exception: pass
            self._placeholder_tex = None

    def _release_list(self, lst: list):
        for e in lst:
            try: e["vbo"].release()
            except Exception: pass
            try: e["vao"].release()
            except Exception: pass
        lst.clear()

    def render_thumbnail(self, W: int, H: int, camera: 'OrbitCamera') -> Optional[bytes]:
        """
        Render an isometric thumbnail of the current room geometry.
        Returns raw RGBA bytes (OpenGL bottom-row first) or None if not ready.
        Used for content browser module preview thumbnails.
        """
        if not self.ctx or not self._room_vaos:
            return None
        try:
            return self.render(W, H, camera, show_walkmesh=False)
        except Exception:
            return None

    def pick_at(self, sx: int, sy: int, W: int, H: int,
                camera: 'OrbitCamera') -> int:
        """
        GPU-accelerated object picking.

        Renders the scene into the pick FBO with each object encoded as its
        1-based index using the picker shader (_FRAG_PICKER).  Reads back the
        pixel at (sx, sy) and decodes the entity ID.

        Matches Kotor.NET GLEngine.Pick():
          GL.ReadPixels → bytes[3]+(bytes[2]<<8)+(bytes[1]<<16)+(bytes[0]<<24)

        Returns the 1-based index of the picked object (0 = nothing picked).
        """
        if not self.ready or not self.ctx or not self._prog_picker:
            return 0
        if not self._pick_fbo:
            return 0
        import moderngl
        try:
            self.ensure_fbo(W, H)
            if not self._pick_fbo:
                return 0

            self._pick_fbo.use()
            self.ctx.viewport = (0, 0, W, H)
            # Clear to white = ID 0xFFFFFFFF = background
            self.ctx.clear(1.0, 1.0, 1.0, 1.0)

            aspect = W / max(H, 1)
            proj = camera.projection_matrix(aspect)
            view = camera.view_matrix()
            vp   = proj @ view

            # Write MVP for all objects (per-object entity_id written below)
            self._prog_picker["mvp"].write(vp.T.astype('f4').tobytes())

            # Draw each solid (non-wire) object VAO with unique entity ID (1-based)
            self.ctx.disable(moderngl.CULL_FACE)
            self.ctx.disable(moderngl.BLEND)
            seen_objs: List[object] = []
            for e in self._object_vaos:
                if e.get("wire"):
                    continue  # skip wireframe duplicates
                obj = e.get("obj")
                if obj not in seen_objs:
                    seen_objs.append(obj)
                eid = seen_objs.index(obj) + 1
                vao, count = e.get("vao"), e.get("count", 0)
                if not vao or count == 0:
                    continue
                try:
                    self._prog_picker["entity_id"].value = eid
                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception:
                    pass
            self.ctx.enable(moderngl.CULL_FACE)
            self.ctx.enable(moderngl.BLEND)

            # Restore main FBO
            if self._fbo:
                self._fbo.use()

            # Read back pixel — OpenGL Y is bottom-up, screen Y is top-down
            gl_y = H - 1 - sy
            if sx < 0 or sx >= W or gl_y < 0 or gl_y >= H:
                return 0

            data = self._pick_fbo.read(viewport=(sx, gl_y, 1, 1), components=4)
            if not data or len(data) < 4:
                return 0
            r, g, b, a = data[0], data[1], data[2], data[3]
            # Kotor.NET decode: bytes[3]+(bytes[2]<<8)+(bytes[1]<<16)+(bytes[0]<<24)
            # _FRAG_PICKER encodes: r=bits[24..31], g=bits[16..23], b=bits[8..15], a=bits[0..7]
            entity_id = (r << 24) | (g << 16) | (b << 8) | a
            if entity_id == 0xFFFFFFFF:  # background (white clear)
                return 0
            return entity_id
        except Exception as ex:
            log.debug(f"pick_at: {ex}")
        return 0

    # ── Object VAOs ───────────────────────────────────────────────────────────

    def rebuild_object_vaos(self, state, selected_obj):
        self._release_list(self._object_vaos)
        if not self.ctx or not state or not state.git:
            return

        def _add(obj, hw, hh, hd, base_color):
            is_sel = obj is selected_obj
            col = _COLOR_SELECTED if is_sel else base_color
            # Solid fill
            verts = _box_solid(obj.position.x, obj.position.y,
                               obj.position.z, hw, hh, hd, col)
            e = self._upload_flat(verts)
            e["obj"] = obj
            self._object_vaos.append(e)
            # Wireframe outline — brighter than fill, always drawn
            wire_r = min(col[0] + 0.45, 1.0)
            wire_g = min(col[1] + 0.45, 1.0)
            wire_b = min(col[2] + 0.45, 1.0)
            wire_col = (wire_r, wire_g, wire_b)
            wverts = _box_wire(obj.position.x, obj.position.y,
                               obj.position.z, hw, hh, hd, wire_col)
            ew = self._upload_flat(wverts)
            ew["obj"]  = obj
            ew["wire"] = True
            self._object_vaos.append(ew)

        # UE-style object sizes: taller, clearer shapes
        for p in state.git.placeables: _add(p, .35,.35,.50, _COLOR_PLACEABLE)
        for c in state.git.creatures:  _add(c, .30,.30,.90, _COLOR_CREATURE)
        for d in state.git.doors:      _add(d, .60,.15,.95, _COLOR_DOOR)
        for w in state.git.waypoints:  _add(w, .18,.18,.65, _COLOR_WAYPOINT)
        for t in state.git.triggers:   _add(t, .60,.60,.06, _COLOR_TRIGGER)
        for s in state.git.sounds:     _add(s, .22,.22,.22, _COLOR_SOUND)
        for st in state.git.stores:    _add(st,.35,.35,.55, _COLOR_STORE)

    def rebuild_entity_vaos(self, entity_registry) -> int:
        """
        Upload MDL mesh geometry for all entities that have a loaded model.

        Returns the count of entity VAOs uploaded (sum of regular + skinned).

        Each entry in _entity_vaos contains:
            vao, vbo, count, lit, color, entity_id,
            base_x, base_y, base_z, bearing
        The render loop multiplies in a per-entity model matrix (translate +
        rotate around Z) so entities render at their correct world positions.

        Skin nodes (NODE_SKIN) are uploaded separately to _skin_vaos with:
            vao, vbo, count, skinned, color, entity_id,
            base_x, base_y, base_z, bearing,
            bone_node_names  — ordered list of bone-node names (index = bone slot)
        The render loop feeds AnimationPlayer.node_transforms into bone_matrices.
        """
        self._release_list(self._entity_vaos)
        self._release_list(self._skin_vaos)
        if not self.ctx or entity_registry is None:
            return 0

        n = 0
        for ent in entity_registry.entities:
            if ent.mesh_data is None or not getattr(ent, 'visible', True):
                continue
            try:
                # Build a flat node list for bone index resolution
                all_nodes = (ent.mesh_data.all_nodes()
                             if hasattr(ent.mesh_data, 'all_nodes')
                             else getattr(ent.mesh_data, 'nodes', []))
                node_name_by_idx = {i: getattr(nd, 'name', '').lower()
                                    for i, nd in enumerate(all_nodes)}

                mesh_nodes = (ent.mesh_data.visible_mesh_nodes()
                              if hasattr(ent.mesh_data, 'visible_mesh_nodes')
                              else [nd for nd in all_nodes
                                    if getattr(nd, 'vertices', None)])

                for node in mesh_nodes:
                    verts = getattr(node, 'vertices', None)
                    faces = getattr(node, 'faces', None)
                    if verts is None or faces is None or len(verts) == 0:
                        continue

                    # Build triangle soup from face indices
                    positions, normals_list, uvs_list = [], [], []
                    bw_list, bi_list = [], []
                    nrms    = getattr(node, 'normals',      None)
                    uvs     = getattr(node, 'uvs',          None)
                    b_wts   = getattr(node, 'bone_weights', None)
                    b_idx   = getattr(node, 'bone_indices', None)
                    is_skin = getattr(node, 'is_skin',      False)

                    for fi in faces:
                        for vi in fi[:3]:
                            if vi >= len(verts):
                                continue
                            positions.append(verts[vi][:3])
                            if nrms is not None and vi < len(nrms):
                                normals_list.append(nrms[vi][:3])
                            if uvs is not None and vi < len(uvs):
                                uvs_list.append(uvs[vi][:2])
                            if b_wts is not None and vi < len(b_wts):
                                bw_list.append(b_wts[vi])
                            if b_idx is not None and vi < len(b_idx):
                                bi_list.append(b_idx[vi])

                    if not positions:
                        continue

                    # Entity colour by type
                    ec = {1: (0.35, 0.35, 0.70),   # creature
                          3: (0.50, 0.20, 0.90),   # door
                          4: (0.35, 0.45, 0.55),   # placeable
                          }.get(ent.entity_type, (0.55, 0.55, 0.55))

                    use_normals = normals_list if len(normals_list) == len(positions) else None
                    use_uvs     = uvs_list     if len(uvs_list)     == len(positions) else None

                    # ── Skinned-mesh path ────────────────────────────────────
                    if is_skin and bw_list and bi_list and self._prog_skinned:
                        use_bw = bw_list if len(bw_list) == len(positions) else None
                        use_bi = bi_list if len(bi_list) == len(positions) else None
                        entry = self._upload_skinned_mesh(
                            positions, use_normals or [], use_uvs or [],
                            use_bw or [], use_bi or [], ec)
                        if entry:
                            # Map bone slot -> node name for runtime transform lookup
                            bni = getattr(node, 'bone_node_indices', [])
                            bone_node_names = [
                                node_name_by_idx.get(bni[slot], '')
                                for slot in range(min(16, len(bni)))
                            ]
                            entry["entity_id"]       = ent.entity_id
                            entry["base_x"]          = ent._x
                            entry["base_y"]          = ent._y
                            entry["base_z"]          = ent._z
                            entry["bearing"]         = ent._bearing
                            entry["entity"]          = ent
                            entry["bone_node_names"] = bone_node_names
                            self._skin_vaos.append(entry)
                            n += 1
                    else:
                        # ── Regular (non-skinned) path ───────────────────────
                        entry = self._upload_lit_or_flat(
                            positions, use_normals, ec, uvs=use_uvs)
                        if entry:
                            entry["entity_id"] = ent.entity_id
                            entry["base_x"]    = ent._x
                            entry["base_y"]    = ent._y
                            entry["base_z"]    = ent._z
                            entry["bearing"]   = ent._bearing
                            entry["entity"]    = ent
                            self._entity_vaos.append(entry)
                            n += 1
            except Exception as ex:
                log.debug(f"rebuild_entity_vaos {ent.entity_id}: {ex}")

        log.debug(f"EntityVAOs: {n} mesh entries "
                  f"({len(self._skin_vaos)} skinned) for "
                  f"{len(entity_registry.entities)} entities")
        return n

    def rebuild_walkmesh_vaos(self, walk_tris: list, nowalk_tris: list):
        """
        Upload walkmesh triangles as position-only geometry for overlay rendering.

        walk_tris  : list of ((x1,y1,z1),(x2,y2,z2),(x3,y3,z3)) walkable faces
        nowalk_tris: list of ((x1,y1,z1),(x2,y2,z2),(x3,y3,z3)) non-walkable faces

        Each element is a 3-tuple of (x,y,z) vertex coordinate tuples.
        The overlay is rendered in paintEvent using the Unreal-style green fill.
        """
        self._release_list(self._walk_vaos)
        self._release_list(self._nowalk_vaos)
        if not self.ctx or not self._prog_uniform:
            return

        def _tris_to_flat_positions(tris):
            """Convert list of triangles to flat list of [x,y,z] vertex positions."""
            pts = []
            for tri in tris:
                if not tri or len(tri) < 3:
                    continue
                for v in tri[:3]:
                    try:
                        pts.append([float(v[0]), float(v[1]), float(v[2])])
                    except (TypeError, IndexError, ValueError):
                        pts.append([0.0, 0.0, 0.0])
            return pts

        walk_pos   = _tris_to_flat_positions(walk_tris)
        nowalk_pos = _tris_to_flat_positions(nowalk_tris)

        if walk_pos:
            e = self._upload_positions_only(walk_pos)
            if e:
                self._walk_vaos.append(e)
        if nowalk_pos:
            e = self._upload_positions_only(nowalk_pos)
            if e:
                self._nowalk_vaos.append(e)

        n_walk   = len(walk_pos) // 3
        n_nowalk = len(nowalk_pos) // 3
        log.debug(f"Walkmesh VAOs: {n_walk} walkable + {n_nowalk} non-walkable triangles")

    # ── Room VAOs ─────────────────────────────────────────────────────────────

    def rebuild_room_vaos(self, room_instances: list, game_dir: str):
        """
        Build GPU VAOs for all room meshes.

        Tries to load real MDL/MDX geometry for each room.
        Falls back to a solid placeholder box + wireframe outline when no MDL found.

        game_dir can be the KotOR install directory OR the extract_dir from
        a .mod import — the method searches for .mdl files in multiple locations.
        """
        self._release_list(self._room_vaos)
        if not self.ctx:
            return

        # Warm colour palette — rooms get distinct soft colours for easy ID
        PALETTE = [
            (0.55, 0.52, 0.45),   # warm stone
            (0.40, 0.48, 0.56),   # cool slate
            (0.46, 0.54, 0.40),   # earthy green
            (0.54, 0.42, 0.50),   # dusty rose
            (0.48, 0.50, 0.38),   # olive
            (0.38, 0.44, 0.52),   # denim
        ]

        import moderngl

        # Build a case-insensitive filename index of the game_dir for fast lookup
        dir_file_index: dict = {}
        if game_dir and os.path.isdir(game_dir):
            try:
                for fname in os.listdir(game_dir):
                    dir_file_index[fname.lower()] = os.path.join(game_dir, fname)
            except OSError:
                pass

        def _resolve_mdl(name: str, explicit_path: str) -> str:
            """Return the filesystem path to the .mdl file, or '' if not found."""
            # 1. Explicit path already set on the RoomInstance
            if explicit_path and os.path.exists(explicit_path):
                return explicit_path
            if not game_dir:
                return ''
            n_lo = name.lower()
            # 2. Direct game_dir lookup (case-insensitive) — covers extract_dir
            for candidate_name in (n_lo + '.mdl', name + '.mdl',
                                   name.upper() + '.MDL', n_lo + '.MDL'):
                p = dir_file_index.get(candidate_name.lower())
                if p and os.path.exists(p):
                    return p
            # 3. Standard KotOR sub-directories
            for subdir in ('models', 'Models', 'override', 'Override', ''):
                base = os.path.join(game_dir, subdir) if subdir else game_dir
                for ext in ('.mdl', '.MDL'):
                    p = os.path.join(base, n_lo + ext)
                    if os.path.exists(p):
                        return p
            return ''

        def _resolve_mdx(mdl_path: str) -> str:
            """Find the matching .mdx file next to the .mdl file."""
            if not mdl_path:
                return ''
            stem = os.path.splitext(mdl_path)[0]
            for ext in ('.mdx', '.MDX'):
                p = stem + ext
                if os.path.exists(p):
                    return p
            return ''

        total_mesh_count = 0

        for idx, ri in enumerate(room_instances):
            name = (getattr(ri, 'mdl_name',   None) or
                    getattr(ri, 'model_name', None) or
                    getattr(ri, 'name',       None) or f'room{idx}')
            name = str(name).strip()

            # Use explicit None-checking to preserve 0.0 values correctly.
            # The `or` pattern would skip 0.0 which is valid for LYT positions.
            def _room_coord(obj, *attrs):
                for a in attrs:
                    v = getattr(obj, a, None)
                    if v is not None:
                        try: return float(v)
                        except (TypeError, ValueError): pass
                return 0.0
            tx = _room_coord(ri, 'world_x', 'x') or _room_coord(ri, 'grid_x') * 10.0
            ty = _room_coord(ri, 'world_y', 'y') or _room_coord(ri, 'grid_y') * 10.0
            tz = _room_coord(ri, 'world_z', 'z')

            color    = PALETTE[idx % len(PALETTE)]
            mdl_path = _resolve_mdl(name, getattr(ri, 'mdl_path', '') or '')
            mdx_path = _resolve_mdx(mdl_path)

            # ── Load real MDL geometry ────────────────────────────────────
            # Note: _HAS_MDL is injected by viewport.py; when this module is
            # used directly (e.g. headless rendering) it may not be set.
            # We use a try/except import guard instead so the MDL path is
            # always attempted if the file exists on disk.
            loaded_mesh_count = 0
            if mdl_path:
                try:
                    from ..formats.mdl_parser import MDLParser, get_model_cache
                    cache = get_model_cache()
                    mesh = cache.get(mdl_path)
                    if mesh is None:
                        # Load with MDX for normals/UVs
                        mdl_bytes = open(mdl_path, 'rb').read()
                        mdx_bytes = open(mdx_path, 'rb').read() if mdx_path else b''
                        parser    = MDLParser(mdl_bytes, mdx_bytes)
                        mesh      = parser.parse()
                        cache.put(mdl_path, mesh)

                    if mesh:
                        # Filter nodes: skip AABB (collision tree), skin, and
                        # non-renderable dummies. Only render true mesh nodes
                        # with actual triangles and render=True.
                        if hasattr(mesh, 'visible_mesh_nodes'):
                            visible_nodes = mesh.visible_mesh_nodes()
                        else:
                            from ..formats.mdl_parser import NODE_AABB, NODE_SKIN
                            visible_nodes = [
                                n for n in mesh.mesh_nodes()
                                if not (n.flags & NODE_AABB) and
                                   not (n.flags & NODE_SKIN) and
                                   getattr(n, 'render', True)
                            ]
                        for node in visible_nodes:
                            verts_raw = node.vertices or []
                            faces_raw = node.faces    or []
                            norms_raw = node.normals   or []
                            uvs_raw   = getattr(node, 'uvs',  []) or []
                            uvs2_raw  = getattr(node, 'uvs2', []) or []
                            if not verts_raw or not faces_raw:
                                continue

                            n_verts = len(verts_raw)
                            has_n   = (len(norms_raw) == n_verts)
                            has_uv  = (len(uvs_raw)   == n_verts)
                            has_uv2 = (len(uvs2_raw)  == n_verts)
                            positions, normals, uvs_out, uvs2_out = [], [], [], []

                            for f in faces_raw:
                                if len(f) < 3:
                                    continue
                                a, b, c = int(f[0]), int(f[1]), int(f[2])
                                if a >= n_verts or b >= n_verts or c >= n_verts:
                                    continue
                                for vi in (a, b, c):
                                    positions.append(verts_raw[vi])
                                    if has_uv:
                                        uvs_out.append(uvs_raw[vi])
                                    if has_uv2:
                                        uvs2_out.append(uvs2_raw[vi])
                                    if has_n:
                                        normals.append(norms_raw[vi])
                                    else:
                                        # Compute face normal inline
                                        v0 = verts_raw[a]; v1 = verts_raw[b]; v2 = verts_raw[c]
                                        ex = v1[0]-v0[0]; ey = v1[1]-v0[1]; ez = v1[2]-v0[2]
                                        fx = v2[0]-v0[0]; fy = v2[1]-v0[1]; fz = v2[2]-v0[2]
                                        nx = ey*fz - ez*fy
                                        ny = ez*fx - ex*fz
                                        nz = ex*fy - ey*fx
                                        mag = (nx*nx+ny*ny+nz*nz)**0.5 or 1.0
                                        normals.append((nx/mag, ny/mag, nz/mag))

                            if not positions:
                                continue

                            # Use node diffuse colour if available, else palette
                            node_col = getattr(node, 'diffuse', None)
                            if node_col and len(node_col) >= 3 and max(node_col) > 0.05:
                                render_color = (
                                    max(0.15, min(1.0, node_col[0])),
                                    max(0.15, min(1.0, node_col[1])),
                                    max(0.15, min(1.0, node_col[2])),
                                )
                            else:
                                render_color = color

                            # Get clean texture name for cache lookup
                            # texture_clean is a @property on MeshNode — use it directly
                            _tc = getattr(node, 'texture_clean', None)
                            if isinstance(_tc, str):
                                tex_name_key = _tc.lower()
                            else:
                                _tx_raw = getattr(node, 'texture', '') or ''
                                tex_name_key = ''.join(
                                    c for c in _tx_raw if 32 <= ord(c) <= 126
                                ).split('\x00')[0].strip().lower()

                            # lightmap_clean is also a @property, not a callable
                            _lc = getattr(node, 'lightmap_clean', None)
                            if isinstance(_lc, str):
                                lmap_name_key = _lc.lower()
                            else:
                                lmap_name_key = ''

                            # ── Upload mesh via the full dual-sampler textured path ──
                            # Fix: always use _upload_textured_mesh so that:
                            #  1. _prog_textured is used (albedo tex0 + lightmap tex1)
                            #  2. Both UV channels are interleaved into the VAO
                            #  3. The render loop can bind tex0 + tex1 properly
                            # Previously _upload_lit_or_flat was used here which
                            # set textured=False and bypassed _prog_textured entirely.
                            e = self._upload_textured_mesh(
                                positions, normals,
                                uvs_out if uvs_out else [],
                                uvs2_out if uvs2_out else [],
                                render_color)
                            if e:
                                # Store texture/lightmap names for runtime binding
                                e.update({
                                    "name":      name,
                                    "tx":        tx, "ty": ty, "tz": tz,
                                    "tex_name":  tex_name_key,
                                    "lmap_name": lmap_name_key,
                                    "alpha":     float(getattr(node, 'alpha', 1.0)),
                                })
                                self._room_vaos.append(e)
                                loaded_mesh_count += 1

                        if loaded_mesh_count > 0:
                            total_mesh_count += loaded_mesh_count
                            log.debug(f"Room '{name}' @ ({tx:.1f},{ty:.1f},{tz:.1f}): "
                                      f"{loaded_mesh_count} mesh(es) from {os.path.basename(mdl_path)}")
                        else:
                            log.debug(f"Room '{name}': MDL parsed but no renderable mesh nodes")

                except Exception as exc:
                    log.warning(f"Room '{name}' MDL load error: {exc}", exc_info=False)
                    loaded_mesh_count = 0

            if loaded_mesh_count == 0:
                # ── Placeholder: solid box + brighter wireframe outline ───
                # LYT world_x/world_y are the room's origin corner.
                # KotOR rooms are typically ~10 units wide.
                # Center the box AT the tx,ty position (rooms are placed at corner).
                rw = float(getattr(ri, 'width',  10.0) or 10.0)
                rh = float(getattr(ri, 'height', 10.0) or 10.0)
                # Center box: tx/ty are corner → add half-width to center
                cx = tx + rw * 0.5
                cy = ty + rh * 0.5

                solid_verts = _box_solid(cx, cy, tz, rw*0.5, rh*0.5, 2.0, color)
                e_solid = self._upload_flat(solid_verts)
                e_solid.update({"name": name, "tx": 0., "ty": 0., "tz": 0.,
                                "primitive": "triangles"})
                self._room_vaos.append(e_solid)

                # Wireframe outline — brighter version of the same palette colour
                wire_c = (min(color[0]+0.40, 1.), min(color[1]+0.40, 1.),
                          min(color[2]+0.40, 1.))
                wire_verts = _box_wire(cx, cy, tz, rw*0.5, rh*0.5, 2.0, wire_c)
                e_wire = self._upload_flat(wire_verts)
                e_wire.update({"name": name + "_outline", "tx": 0., "ty": 0.,
                               "tz": 0., "primitive": "lines"})
                self._room_vaos.append(e_wire)

                if mdl_path:
                    log.debug(f"Room '{name}': MDL found at {mdl_path} but no meshes — placeholder box")
                else:
                    log.debug(f"Room '{name}' @ ({tx:.1f},{ty:.1f}): no MDL — placeholder box")

        log.info(f"Room VAOs: {len(self._room_vaos)} entries "
                 f"({total_mesh_count} real meshes) from {len(room_instances)} rooms")

    def render(self, W: int, H: int, camera: OrbitCamera,
               play_session=None, show_walkmesh: bool = True,
               selected_vaos: list = None) -> Optional[bytes]:
        """
        Render one frame into the FBO and return the raw RGBA pixel bytes
        (bottom-row first, i.e. OpenGL convention — caller must flip).
        Returns None if not ready.
        """
        if not self.ctx or not self._prog_flat:
            return None
        import moderngl, time as _time

        # Advance render time for animated effects
        self._render_time += 0.016

        self.ensure_fbo(W, H)
        if not self._fbo:
            return None

        self._fbo.use()
        self.ctx.viewport = (0, 0, W, H)
        # Deep navy-blue background
        self.ctx.clear(0.07, 0.08, 0.14, 1.0)

        aspect = W / max(H, 1)
        if play_session:
            proj = camera.projection_matrix(aspect)
            try:
                eye  = play_session.player_eye
                look = play_session.player.look_at_target(0.0)
            except Exception:
                eye  = camera.eye().tolist()
                look = camera.target.tolist()
            view = _look_at(
                np.array(eye,  dtype='f4'),
                np.array(look, dtype='f4'),
                np.array([0., 0., 1.], dtype='f4'),
            )
        else:
            proj = camera.projection_matrix(aspect)
            view = camera.view_matrix()

        vp = proj @ view

        # ── Ground grid ───────────────────────────────────────────────────────
        # Respects _show_grid flag (G key toggles it)
        if self._grid_vao and getattr(self, '_show_grid', True):
            self._prog_flat["mvp"].write(vp.T.astype('f4').tobytes())
            self._grid_vao.render(moderngl.LINES, vertices=self._grid_count)

        # ── Room geometry ─────────────────────────────────────────────────────
        # Disable back-face culling so interior faces show
        self.ctx.disable(moderngl.CULL_FACE)
        # KotOR interior lighting: warm key light from upper-right,
        # matches in-game feel. Based on PyKotor scene.py + Kotor.NET GeometryRenderer.
        # Light from slightly off-axis to give good wall/floor contrast.
        light_dir = np.array([0.60, 0.40, 0.75], dtype='f4')
        light_dir = light_dir / np.linalg.norm(light_dir)

        # KotOR interior ambient: 0.55 matches the dark stone dungeon feel
        # while still being clearly visible. Based on observed game lighting.
        ROOM_AMBIENT = 0.55

        for e in self._room_vaos:
            vao, count = e["vao"], e["count"]
            if not vao or count == 0:
                continue
            tx, ty, tz = e.get("tx", 0.), e.get("ty", 0.), e.get("tz", 0.)
            model_m = _translation(tx, ty, tz)
            mvp_m   = proj @ view @ model_m
            color   = e.get("color", (.55,.52,.48))
            alpha   = float(e.get("alpha", 1.0))
            primitive_hint = e.get("primitive", "triangles")
            is_textured = e.get("textured", False)
            lit_uv  = e.get("lit_uv", False)
            lit     = e.get("lit", False)

            # ── PRIMARY PATH: dual-sampler textured shader (_prog_textured) ────
            # This is the Kotor.NET approach: texture1 bound to tex0, lightmap
            # bound to tex1. Pure texture passthrough — no Phong dimming on
            # textured meshes. Lightmap modulation applied when available.
            if is_textured and self._prog_textured:
                try:
                    prog = self._prog_textured
                    prog["mvp"].write(mvp_m.T.astype('f4').tobytes())
                    prog["model"].write(model_m.T.astype('f4').tobytes())
                    prog["diffuse_color"].write(np.array(color, dtype='f4').tobytes())
                    prog["light_dir"].write(light_dir.tobytes())
                    prog["ambient"].value = ROOM_AMBIENT
                    prog["u_alpha"].value = alpha

                    # Bind diffuse / albedo texture (tex0, location 0)
                    tex_key = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
                    tex_obj = (self._tex_cache.get(tex_key) if tex_key else None)
                    # Fall back to placeholder so mesh is always visible
                    if tex_obj is None and self._placeholder_tex:
                        tex_obj = self._placeholder_tex
                    if tex_obj is not None:
                        try:
                            tex_obj.use(location=0)
                            prog["tex0"].value     = 0
                            prog["use_texture"].value = 1
                        except Exception:
                            prog["use_texture"].value = 0
                    else:
                        prog["use_texture"].value = 0

                    # Bind lightmap texture (tex1, location 1)
                    lmap_key = e.get("lmap_name", "").lower()
                    lmap_obj = (self._lmap_cache.get(lmap_key) if lmap_key else None)
                    # Also update live reference if loaded after VAO was built
                    if lmap_obj is None and lmap_key:
                        lmap_obj = self._lmap_cache.get(lmap_key)
                    if lmap_obj is not None:
                        try:
                            lmap_obj.use(location=1)
                            prog["tex1"].value        = 1
                            prog["use_lightmap"].value = 1
                        except Exception:
                            prog["use_lightmap"].value = 0
                    else:
                        prog["use_lightmap"].value = 0

                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception as ex:
                    log.debug(f"room textured render: {ex}")

            # ── FALLBACK A: Phong-lit with UV + optional single texture ───────
            elif lit_uv and self._prog_lit:
                try:
                    prog = self._prog_lit
                    prog["mvp"].write(mvp_m.T.astype('f4').tobytes())
                    prog["model"].write(model_m.T.astype('f4').tobytes())
                    prog["diffuse_color"].write(np.array(color, dtype='f4').tobytes())
                    prog["light_dir"].write(light_dir.tobytes())
                    prog["ambient"].value = ROOM_AMBIENT
                    prog["alpha"].value   = alpha
                    tex_key = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
                    tex_obj = (self._tex_cache.get(tex_key) if tex_key else None)
                    if tex_obj is None and self._placeholder_tex:
                        tex_obj = self._placeholder_tex
                    if tex_obj is not None:
                        try:
                            tex_obj.use(location=0)
                            prog["has_texture"].value = True
                            prog["tex0"].value = 0
                        except Exception:
                            prog["has_texture"].value = False
                    else:
                        prog["has_texture"].value = False
                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception as ex:
                    log.debug(f"room lit-uv render: {ex}")

            # ── FALLBACK B: Phong-lit without UV ─────────────────────────────
            elif lit and self._prog_lit_no_uv:
                try:
                    prog = self._prog_lit_no_uv
                    prog["mvp"].write(mvp_m.T.astype('f4').tobytes())
                    prog["model"].write(model_m.T.astype('f4').tobytes())
                    prog["diffuse_color"].write(np.array(color, dtype='f4').tobytes())
                    prog["light_dir"].write(light_dir.tobytes())
                    prog["ambient"].value = ROOM_AMBIENT
                    prog["alpha"].value   = alpha
                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception as ex:
                    log.debug(f"room lit render: {ex}")

            # ── FALLBACK C: Flat colour-per-vertex ────────────────────────────
            else:
                try:
                    self._prog_flat["mvp"].write(mvp_m.T.astype('f4').tobytes())
                    prim = moderngl.LINES if primitive_hint == "lines" else moderngl.TRIANGLES
                    vao.render(prim, vertices=count)
                except Exception as ex:
                    log.debug(f"room flat render: {ex}")

        self.ctx.enable(moderngl.CULL_FACE)

        # ── Walkmesh overlay — Unreal Engine-style navmesh ────────────────────
        # UE5 navmesh style: translucent teal/green fill + bright edge outlines.
        # Rendered AFTER room geometry with depth-write disabled so it floats
        # on top without disturbing depth buffer for objects drawn later.
        # Uses polygon offset to avoid z-fighting with coplanar floor geometry.
        if show_walkmesh and self._prog_uniform:
            self.ctx.depth_mask = False
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.ctx.disable(moderngl.CULL_FACE)

            # Polygon offset: strongly push navmesh in front of floor tris
            # (factor=-2, units=-2 works better than -1,-1 on software rasterizer)
            try:
                self.ctx.enable(moderngl.POLYGON_OFFSET_FILL)
                self.ctx.polygon_offset = (-2.0, -2.0)
            except Exception:
                pass

            walk_mvp = vp.T.astype('f4').tobytes()

            # ── Walkable: UE5 teal-green fill ──────────────────────────
            if self._walk_vaos:
                try:
                    self._prog_uniform["mvp"].write(walk_mvp)
                    # UE5 navmesh: #00C8A0 style teal-green at 45% opacity
                    self._prog_uniform["u_color"].write(
                        np.array([0.00, 0.78, 0.63, 0.45], dtype='f4').tobytes())
                    for e in self._walk_vaos:
                        if e.get("vao") and e.get("count", 0):
                            e["vao"].render(moderngl.TRIANGLES, vertices=e["count"])
                except Exception as ex:
                    log.debug(f"walk fill overlay: {ex}")

                # Bright edge outlines — thinner, more UE5-like
                try:
                    self._prog_uniform["u_color"].write(
                        np.array([0.00, 0.96, 0.78, 0.80], dtype='f4').tobytes())
                    self.ctx.line_width = 1.0  # crisp 1px lines
                    for e in self._walk_vaos:
                        if e.get("vao") and e.get("count", 0):
                            e["vao"].render(moderngl.LINES, vertices=e["count"])
                except Exception as ex:
                    log.debug(f"walk edge overlay: {ex}")

            # ── Non-walkable: red fill + red edges ──────────────────────
            if self._nowalk_vaos:
                try:
                    self._prog_uniform["mvp"].write(walk_mvp)
                    self._prog_uniform["u_color"].write(
                        np.array([0.90, 0.10, 0.08, 0.40], dtype='f4').tobytes())
                    for e in self._nowalk_vaos:
                        if e.get("vao") and e.get("count", 0):
                            e["vao"].render(moderngl.TRIANGLES, vertices=e["count"])
                except Exception as ex:
                    log.debug(f"nowalk fill overlay: {ex}")
                try:
                    self._prog_uniform["u_color"].write(
                        np.array([1.00, 0.20, 0.10, 0.80], dtype='f4').tobytes())
                    for e in self._nowalk_vaos:
                        if e.get("vao") and e.get("count", 0):
                            e["vao"].render(moderngl.LINES, vertices=e["count"])
                except Exception as ex:
                    log.debug(f"nowalk edge overlay: {ex}")

            try:
                self.ctx.disable(moderngl.POLYGON_OFFSET_FILL)
                self.ctx.polygon_offset = (0.0, 0.0)
            except Exception:
                pass
            self.ctx.enable(moderngl.CULL_FACE)
            self.ctx.depth_mask = True

        # ── GIT object boxes ──────────────────────────────────────────────────
        self._prog_flat["mvp"].write(vp.T.astype('f4').tobytes())
        self.ctx.disable(moderngl.CULL_FACE)
        for e in self._object_vaos:
            try:
                if e.get("wire"):
                    e["vao"].render(moderngl.LINES, vertices=e["count"])
                else:
                    e["vao"].render(moderngl.TRIANGLES, vertices=e["count"])
            except Exception:
                pass
        self.ctx.enable(moderngl.CULL_FACE)

        # ── Entity MDL models (creatures / doors / placeables with mesh_data) ─
        _ent_prog = self._prog_lit_no_uv or self._prog_lit
        if self._entity_vaos and _ent_prog:
            self.ctx.disable(moderngl.CULL_FACE)
            try:
                _ent_prog["light_dir"].write(light_dir.tobytes())
                _ent_prog["ambient"].value = 0.4
                if "alpha" in _ent_prog:
                    _ent_prog["alpha"].value = 1.0
            except Exception:
                pass
            for e in self._entity_vaos:
                vao   = e.get("vao")
                count = e.get("count", 0)
                ent   = e.get("entity")
                if not vao or not count:
                    continue
                try:
                    # Build per-entity model matrix: translate + rotate Z
                    bx = e.get("base_x", 0.0)
                    by = e.get("base_y", 0.0)
                    bz = e.get("base_z", 0.0)
                    bearing = e.get("bearing", 0.0)

                    # Apply root-node translation from animation player if present
                    dx, dy, dz = 0.0, 0.0, 0.0
                    if ent is not None:
                        player = getattr(ent, '_animation_player', None)
                        if player and player.node_transforms:
                            root_name = getattr(
                                ent.mesh_data, 'name', None) or ''
                            root_tf = (player.node_transforms.get(root_name.lower())
                                       or player.node_transforms.get('rootdummy')
                                       or player.node_transforms.get('root'))
                            if root_tf is not None:
                                dx, dy, dz = (float(root_tf.position[0]),
                                              float(root_tf.position[1]),
                                              float(root_tf.position[2]))

                    cos_b = float(np.cos(bearing))
                    sin_b = float(np.sin(bearing))
                    model_mat = np.array([
                        [ cos_b, sin_b, 0, 0],
                        [-sin_b, cos_b, 0, 0],
                        [     0,     0, 1, 0],
                        [bx+dx, by+dy, bz+dz, 1],
                    ], dtype='f4')
                    mvp_ent = model_mat @ vp
                    _ent_prog["mvp"].write(mvp_ent.T.astype('f4').tobytes())
                    if "model" in _ent_prog:
                        _ent_prog["model"].write(model_mat.T.tobytes())
                    col = e.get("color", (0.55, 0.55, 0.55))
                    if "diffuse_color" in _ent_prog:
                        _ent_prog["diffuse_color"].write(
                            np.array(col, dtype='f4').tobytes())
                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception:
                    pass
            self.ctx.enable(moderngl.CULL_FACE)

        # ── Skinned entity meshes (bone-palette GPU skinning) ─────────────────
        if self._skin_vaos and self._prog_skinned:
            _skin_prog = self._prog_skinned
            self.ctx.disable(moderngl.CULL_FACE)
            _IDENT16 = np.eye(4, dtype='f4')
            _ZERO_MATS = np.zeros((16, 4, 4), dtype='f4')
            for i in range(16):
                _ZERO_MATS[i] = _IDENT16   # default = identity per bone slot
            try:
                _skin_prog["light_dir"].write(light_dir.tobytes())
                _skin_prog["ambient"].value = 0.4
                if "alpha" in _skin_prog:
                    _skin_prog["alpha"].value = 1.0
            except Exception:
                pass
            for e in self._skin_vaos:
                vao   = e.get("vao")
                count = e.get("count", 0)
                ent   = e.get("entity")
                if not vao or not count:
                    continue
                try:
                    bx = e.get("base_x", 0.0)
                    by = e.get("base_y", 0.0)
                    bz = e.get("base_z", 0.0)
                    bearing = e.get("bearing", 0.0)
                    cos_b = float(np.cos(bearing))
                    sin_b = float(np.sin(bearing))
                    model_mat = np.array([
                        [ cos_b, sin_b, 0, 0],
                        [-sin_b, cos_b, 0, 0],
                        [     0,     0, 1, 0],
                        [bx,    by,    bz, 1],
                    ], dtype='f4')
                    mvp_ent = model_mat @ vp
                    _skin_prog["mvp"].write(mvp_ent.T.astype('f4').tobytes())
                    if "model" in _skin_prog:
                        _skin_prog["model"].write(model_mat.T.tobytes())
                    col = e.get("color", (0.55, 0.55, 0.55))
                    if "diffuse_color" in _skin_prog:
                        _skin_prog["diffuse_color"].write(
                            np.array(col, dtype='f4').tobytes())

                    # ── Feed bone matrices from AnimationPlayer ───────────────
                    bone_mats = _ZERO_MATS.copy()
                    if ent is not None:
                        player = getattr(ent, '_animation_player', None)
                        bone_node_names = e.get("bone_node_names", [])
                        if player and player.node_transforms and bone_node_names:
                            nt = player.node_transforms
                            for slot, bname in enumerate(bone_node_names):
                                if slot >= 16 or not bname:
                                    continue
                                tf = nt.get(bname)
                                if tf is None:
                                    continue
                                # Build 4x4 from quaternion + translation
                                qx,qy,qz,qw = tf.orientation
                                tx,ty,tz    = tf.position
                                s = tf.scale
                                # Quaternion to rotation matrix (column-major)
                                x2,y2,z2 = 2*qx*qx,2*qy*qy,2*qz*qz
                                xy,xz,yz = 2*qx*qy,2*qx*qz,2*qy*qz
                                wx,wy,wz = 2*qw*qx,2*qw*qy,2*qw*qz
                                bone_mats[slot] = np.array([
                                    [s*(1-y2-z2), s*(xy+wz),   s*(xz-wy),   0],
                                    [s*(xy-wz),   s*(1-x2-z2), s*(yz+wx),   0],
                                    [s*(xz+wy),   s*(yz-wx),   s*(1-x2-y2), 0],
                                    [tx,          ty,          tz,          1],
                                ], dtype='f4')

                    # Write bone_matrices[16] as a flat 16*16 float array
                    if "bone_matrices" in _skin_prog:
                        _skin_prog["bone_matrices"].write(
                            bone_mats.astype('f4').tobytes())
                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception:
                    pass
            self.ctx.enable(moderngl.CULL_FACE)

        # ── Play-mode MDL models ──────────────────────────────────────────────
        _play_prog = self._prog_lit_no_uv or self._prog_lit
        if play_session and self._mdl_vaos and _play_prog:
            ident = np.eye(4, dtype='f4')
            try:
                _play_prog["mvp"].write(vp.T.astype('f4').tobytes())
                _play_prog["model"].write(ident.T.tobytes())
                _play_prog["light_dir"].write(light_dir.tobytes())
                _play_prog["ambient"].value = 0.3
                if "alpha" in _play_prog:
                    _play_prog["alpha"].value = 1.0
            except Exception:
                pass
            self.ctx.disable(moderngl.CULL_FACE)
            for e in self._mdl_vaos:
                vao, count, color = e["vao"], e["count"], e.get("color", (.6,.6,.6))
                if vao and count:
                    try:
                        _play_prog["diffuse_color"].write(
                            np.array(color, dtype='f4').tobytes())
                        vao.render(moderngl.TRIANGLES, vertices=count)
                    except Exception:
                        pass
            self.ctx.enable(moderngl.CULL_FACE)

        return self._fbo.read(components=4)

    def release(self):
        if not self.ctx:
            return
        self._release_list(self._object_vaos)
        self._release_list(self._room_vaos)
        self._release_list(self._walk_vaos)
        self._release_list(self._nowalk_vaos)
        self._release_list(self._mdl_vaos)
        self._release_list(self._entity_vaos)
        self._release_list(self._skin_vaos)
        self._release_texture_cache()
        try: self._fbo.release()
        except Exception: pass
        try: self._pick_fbo.release()
        except Exception: pass
        try: self._depth_rbo.release()
        except Exception: pass
        try: self._color_rbo.release()
        except Exception: pass
        try: self.ctx.release()
        except Exception: pass
        self.ctx = None
        self.ready = False
