"""
GModular — Viewport GLSL Shaders
==================================
All GLSL shader source strings extracted from viewport.py.

Keeping shaders in one place makes it easy to:
  * Update GLSL without touching 4k-line viewport.py
  * Import individual shaders in tests / offline tools
  * Swap in SPIR-V / Metal variants later

Canonical naming: ``_VERT_<NAME>`` / ``_FRAG_<NAME>``
Backward-compat aliases provided at the bottom of this file.
"""
from __future__ import annotations

# ── Flat colour-per-vertex (grid, wireframe boxes) ────────────────────────
_VERT_FLAT = """
#version 330 core
in vec3 in_position;
in vec3 in_color;
out vec3 v_color;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
    v_color = in_color;
}
"""

_FRAG_FLAT = """
#version 330 core
in vec3 v_color;
out vec4 fragColor;
void main() { fragColor = vec4(v_color, 1.0); }
"""

# ── Phong-lit with UV texture support ─────────────────────────────────────
# Based on PyKotor KOTOR_VSHADER / KOTOR_FSHADER + Kotor.NET standard.glsl
# Features:
#   - Proper normal transform (normal matrix from model)
#   - Multi-light Blinn-Phong (key + fill + rim + back + spec)
#   - Dual UV channels: diffuse UV + optional lightmap UV
#   - Alpha discard for punch-through transparency
_VERT_LIT = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
out vec3 v_normal;
out vec3 v_world_pos;
out vec2 v_uv;
uniform mat4 mvp;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    // Correct normal transform using normal matrix
    mat3 normal_mat = transpose(inverse(mat3(model)));
    v_normal    = normalize(normal_mat * in_normal);
    v_uv        = in_uv;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_LIT = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
in vec2 v_uv;
out vec4 fragColor;
uniform vec3  diffuse_color;
uniform vec3  light_dir;
uniform float ambient;
uniform float alpha;
uniform bool  has_texture;
uniform sampler2D tex0;
void main() {
    if (has_texture) {
        // Texture passthrough: Kotor.NET style — output texture directly
        vec4 tex_sample = texture(tex0, v_uv);
        if (tex_sample.a < 0.05) discard;
        fragColor = vec4(tex_sample.rgb, tex_sample.a * alpha);
    } else {
        // No texture: apply Phong to diffuse_color
        vec3 n = normalize(v_normal);
        vec3 key       = normalize(light_dir);
        float NdL_key  = max(dot(n, key), 0.0);
        vec3 fill      = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.4));
        float NdL_fill = max(dot(n, fill), 0.0) * 0.30;
        float NdL_rim  = max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0) * 0.10;
        float back     = max(dot(-n, key), 0.0) * 0.12;
        vec3 view_dir  = normalize(-v_world_pos);
        vec3 half_vec  = normalize(key + view_dir);
        float spec     = pow(max(dot(n, half_vec), 0.0), 48.0) * 0.04;
        float light_total = ambient + (NdL_key + NdL_fill + NdL_rim + back) * (1.0 - ambient);
        float ao       = 0.88 + 0.12 * abs(n.z);
        vec3 col       = diffuse_color * light_total * ao + vec3(spec);
        fragColor      = vec4(clamp(col, 0.0, 1.0), alpha);
    }
}
"""

# ── Lit with no UV (positions + normals only — for nodes without UVs) ─────
_VERT_LIT_NO_UV = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
out vec3 v_normal;
out vec3 v_world_pos;
uniform mat4 mvp;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    v_normal    = normalize(mat3(transpose(inverse(model))) * in_normal);
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_LIT_NO_UV = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
out vec4 fragColor;
uniform vec3  diffuse_color;
uniform vec3  light_dir;
uniform float ambient;
uniform float alpha;
void main() {
    vec3 n = normalize(v_normal);
    vec3 key       = normalize(light_dir);
    float NdL_key  = max(dot(n, key), 0.0);
    vec3 fill      = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.4));
    float NdL_fill = max(dot(n, fill), 0.0) * 0.30;
    float NdL_rim  = max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0) * 0.10;
    float back     = max(dot(-n, key), 0.0) * 0.12;
    vec3 view_dir  = normalize(-v_world_pos);
    vec3 half_vec  = normalize(key + view_dir);
    float spec     = pow(max(dot(n, half_vec), 0.0), 48.0) * 0.04;
    float light_total = ambient + (NdL_key + NdL_fill + NdL_rim + back) * (1.0 - ambient);
    float ao       = 0.88 + 0.12 * abs(n.z);
    vec3 col       = diffuse_color * light_total * ao + vec3(spec);
    fragColor      = vec4(clamp(col, 0.0, 1.0), alpha);
}
"""

# ── Uniform-colour + alpha (walkmesh fill, selection overlay) ─────────────
_VERT_UNIFORM = """
#version 330 core
in vec3 in_position;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_UNIFORM = """
#version 330 core
out vec4 fragColor;
uniform vec4 u_color;
void main() { fragColor = u_color; }
"""

# ── Selection highlight / outline shader ─────────────────────────────────
# Renders a screen-space outline effect around selected objects via
# an additive pulsing glow (matches UE5 selection highlight style).
_VERT_OUTLINE = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
uniform mat4 mvp;
uniform float outline_scale;  // expand along normals for silhouette
void main() {
    vec3 offset = in_position + in_normal * outline_scale;
    gl_Position = mvp * vec4(offset, 1.0);
}
"""

_FRAG_OUTLINE = """
#version 330 core
out vec4 fragColor;
uniform vec4 outline_color;
uniform float time;
void main() {
    // Subtle pulse (0.6 to 1.0 alpha) for UE5-style selection glow
    float pulse = 0.6 + 0.4 * abs(sin(time * 2.5));
    fragColor = vec4(outline_color.rgb, outline_color.a * pulse);
}
"""

# ── Object ID picker (matches Kotor.NET picker/fragment.glsl) ─────────────
# Encodes entity ID into RGBA bytes for GPU readback picking.
_VERT_PICKER = """
#version 330 core
in vec3 in_position;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_PICKER = """
#version 330 core
out vec4 fragColor;
uniform uint entity_id;
void main() {
    // Kotor.NET intToColor: MSB→R, LSB→A (big-endian RGBA encoding)
    float r = float((entity_id >> 24u) & 0xFFu) / 255.0;
    float g = float((entity_id >> 16u) & 0xFFu) / 255.0;
    float b = float((entity_id >> 8u)  & 0xFFu) / 255.0;
    float a = float( entity_id         & 0xFFu)  / 255.0;
    fragColor = vec4(r, g, b, a);
}
"""

# Aliases for backward-compat and tests (canonical names are _VERT_PICKER / _FRAG_PICKER)
_VERT_PICK = _VERT_PICKER
_FRAG_PICK = _FRAG_PICKER

# ── Textured mesh shader (dual-sampler: albedo tex0 + optional lightmap tex1) ───────
# Architecture: Kotor.NET standard.glsl approach
#   - Vertex shader: separate entity + mesh matrices (like Kotor.NET entity/mesh uniforms)
#   - Fragment shader: pure texture passthrough with optional lightmap modulation
#   - When texture present: output texture directly (Kotor.NET: FragColor = diffuseColor)
#   - When lightmap present: modulate albedo by lightmap (baked lighting = realism)
#   - Minimal Phong only applied when NO texture (fallback for untextured meshes)
_VERT_TEXTURED = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
in vec2 in_uv2;
out vec3 v_normal;
out vec3 v_world_pos;
out vec2 v_uv;
out vec2 v_uv2;
uniform mat4 mvp;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    mat3 normal_mat = transpose(inverse(mat3(model)));
    v_normal    = normalize(normal_mat * in_normal);
    v_uv        = in_uv;
    v_uv2       = in_uv2;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_TEXTURED = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
in vec2 v_uv;
in vec2 v_uv2;
out vec4 fragColor;
uniform sampler2D tex0;        // albedo / diffuse texture
uniform sampler2D tex1;        // lightmap texture
uniform int  use_texture;      // 1 = sample tex0, 0 = use diffuse_color
uniform int  use_lightmap;     // 1 = multiply by lightmap baked light
uniform vec3 diffuse_color;
uniform vec3 light_dir;
uniform float ambient;
uniform float u_alpha;
void main() {
    vec4 albedo;
    if (use_texture == 1) {
        // Kotor.NET approach: pure texture passthrough — FragColor = texture(texture1, texCoord1)
        albedo = texture(tex0, v_uv);
        if (albedo.a < 0.05) discard;
        // Apply lightmap modulation when available (baked lighting)
        if (use_lightmap == 1) {
            vec3 lm = texture(tex1, v_uv2).rgb;
            // KotOR lightmap formula: lm * 1.8 + 0.2 to avoid pure black areas
            albedo.rgb = albedo.rgb * clamp(lm * 1.8 + 0.2, 0.0, 1.5);
        }
        // No Phong dimming on textured + lit meshes — texture IS the full colour
        // This matches exactly what Kotor.NET does: just output the texture
        fragColor = vec4(clamp(albedo.rgb, 0.0, 1.0), albedo.a * u_alpha);
    } else {
        // Untextured mesh: apply Phong lighting to diffuse_color
        vec3 n = normalize(v_normal);
        vec3 key  = normalize(light_dir);
        float NdL = max(dot(n, key), 0.0);
        vec3 fill = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.4));
        float NdF = max(dot(n, fill), 0.0) * 0.28;
        float NdR = max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0) * 0.08;
        float back = max(dot(-n, key), 0.0) * 0.10;
        float light = ambient + (NdL + NdF + NdR + back) * (1.0 - ambient);
        fragColor = vec4(clamp(diffuse_color * light, 0.0, 1.0), u_alpha);
    }
}
"""

# ── Skinned mesh vertex shader (bone matrix palette — Kotor.NET SkinmeshNode) ──────────────
# Supports up to 16 bone matrices (matching Kotor.NET MDLBinarySkinmeshHeader ushort[16]).
# Blend weight: 4 weights per vertex.
_VERT_SKINNED = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
in vec4 in_bone_weights;
in ivec4 in_bone_indices;
out vec3 v_normal;
out vec3 v_world_pos;
out vec2 v_uv;
uniform mat4 mvp;
uniform mat4 model;
uniform mat4 bone_matrices[16];
void main() {
    // Weighted bone transform
    mat4 skin = mat4(0.0);
    skin += in_bone_weights.x * bone_matrices[in_bone_indices.x];
    skin += in_bone_weights.y * bone_matrices[in_bone_indices.y];
    skin += in_bone_weights.z * bone_matrices[in_bone_indices.z];
    skin += in_bone_weights.w * bone_matrices[in_bone_indices.w];
    vec4 world = model * skin * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    v_normal    = normalize(mat3(model) * mat3(skin) * in_normal);
    v_uv        = in_uv;
    gl_Position = mvp * vec4((skin * vec4(in_position, 1.0)).xyz, 1.0);
}
"""

# The skinned fragment shader reuses the same KotOR two-light Phong as _FRAG_TEXTURED
# but without lightmap (too expensive to skin lightmap UVs without engine support).
_FRAG_SKINNED = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D tex0;
uniform int  use_texture;
uniform vec3 diffuse_color;
uniform vec3 light_dir;
uniform float ambient;
uniform float u_alpha;
void main() {
    vec3 n = normalize(v_normal);
    vec3 key  = normalize(light_dir);
    float NdL = max(dot(n, key), 0.0);
    vec3 fill = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.35));
    float NdF = max(dot(n, fill), 0.0) * 0.22;
    float light = ambient + (NdL + NdF) * (1.0 - ambient);
    vec4 albedo;
    if (use_texture == 1) {
        albedo = texture(tex0, v_uv);
        if (albedo.a < 0.05) discard;
    } else {
        albedo = vec4(diffuse_color, 1.0);
    }
    fragColor = vec4(albedo.rgb * light, albedo.a * u_alpha);
}
"""

# ── Public listing for tools / tests ─────────────────────────────────────────
ALL_SHADERS = {
    "flat":      (_VERT_FLAT,      _FRAG_FLAT),
    "lit":       (_VERT_LIT,       _FRAG_LIT),
    "lit_no_uv": (_VERT_LIT_NO_UV, _FRAG_LIT_NO_UV),
    "uniform":   (_VERT_UNIFORM,   _FRAG_UNIFORM),
    "outline":   (_VERT_OUTLINE,   _FRAG_OUTLINE),
    "picker":    (_VERT_PICKER,    _FRAG_PICKER),
    "textured":  (_VERT_TEXTURED,  _FRAG_TEXTURED),
    "skinned":   (_VERT_SKINNED,   _FRAG_SKINNED),
}
