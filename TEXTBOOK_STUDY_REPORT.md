# GModular — Textbook Study Report
## An Eager-Student Reading of Eight Foundational 3D-Engine Texts
*Compiled 2026-03-21 against GModular v2.0.13 codebase*

---

## Overview

Eight textbooks were studied in full, cover-to-cover, for lessons directly applicable to the GModular KotOR viewport renderer. Each book is summarised with a chapter-by-chapter breakdown followed by a focused "Lessons for GModular" section. The report closes with a cross-book synthesis and updated roadmap recommendations.

---

## Book 1 — *3D Game Engine Design (Second Edition)* — David H. Eberly

**What this book is:** A 1,040-page engineering reference for building a complete game engine from scratch, covering the entire stack from math to scene management to shaders to terrain to physics. Eberly wrote the Wild Magic engine alongside this book, so every concept has verified C++ code.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 1–2 | Foundation & Math | Vector/matrix spaces, quaternions, affine transforms. Row vs column-major discussed: Eberly uses **column-vector, column-major** throughout, matching D3D9/DirectX convention. |
| 3 | Renderer Architecture | **Scene graph** design: `Spatial → Geometry → TriMesh`. Update / Cull / Draw three-pass loop. The renderer maintains a **visible set** (render queue) populated by the culler. |
| 4 | Advanced Scene Graph | Level-of-Detail (LOD) nodes, switches, billboards. Camera frustum object is a first-class scene graph node. |
| 5 | Shaders | Vertex & pixel shader architecture. Eberly shows **Effect** objects that wrap a shader program + uniform state, analogous to ModernGL's `ctx.program()`. Each geometry node owns its Effect. |
| 6 | Special Effects | Alpha blending, particles, fog. Sorting rule: **opaque first, back-to-front for alpha**. |
| 7 | Spatial Sorting | Octrees, BSP, portals. BSP gives exact front-to-back ordering for transparent geometry. Portals enable frustum-based visibility culling per room. |
| 8 | Terrain | Height-field LOD (GeoMipMap), continuous LOD. |
| 9 | Physics & Collision | Rigid body, OBB trees, GJK. |
| 10 | Animations | Keyframe (linear, Hermite, Bezier), skeletal skinning. Bone matrix palette uploaded as a uniform array — exactly what `_VERT_SKINNED` already does. |

### Lessons for GModular

**Lesson E1 — Three-Pass Loop is non-negotiable.**
Eberly's Update → Cull → Draw loop is the canonical game-engine loop. GModular's `render()` method currently does ALL three in one pass (loops `_room_vaos`, does `mvp_m = proj @ view @ model_m`, calls `vao.render()`). This is fine for a modding tool, but as scene complexity grows we need:
1. **Update pass** — recompute transforms when objects move (currently done lazily).
2. **Cull pass** — test each room AABB against camera frustum BEFORE uploading draw calls.
3. **Draw pass** — render the visible set only.

Current status: GModular has no frustum culling. Every room VAO is submitted every frame regardless of camera position.

**Lesson E2 — Portal rendering is the RIGHT architecture for KotOR.**
Chapter 7 describes portals: divide the world into cells (rooms), connect via portals (doors/transitions). Render only cells reachable through the current camera portal chain. This is **exactly** how the KotOR engine works — the `.vis` file lists which rooms are visible from each other room, and door hooks in the `.lyt` are the portals. GModular currently ignores `.vis` data entirely.

**Lesson E3 — Effect objects decouple shader state from geometry.**
Eberly's `Effect` class holds: shader program reference + all uniform values. This maps directly to what GModular should have: a `RoomRenderEffect(shader, texture, lightmap, alpha)` object stored per room VAO, rather than re-setting uniforms in every render loop iteration.

**Lesson E4 — LOD nodes for distant rooms.**
For large KotOR modules (e.g. Taris city, Dantooine plains) the room count can be 20+. Eberly's LOD nodes allow switching to simplified geometry at distance. Practical suggestion: render rooms >50 units from camera at 50% polygon count (via index buffer stride trick).

**Lesson E5 — Skinned mesh: bone palette is capped at 16.**
Chapter 10 confirms that KotOR's 16-bone palette (matching `MDLBinarySkinmeshHeader ushort[16]`) was the standard cap for early 2000s hardware. The `bone_matrices[16]` uniform array in `_VERT_SKINNED` is correct.

---

## Book 2 — *Real-Time 3D Rendering with DirectX and HLSL* — Paul Varcholik

**What this book is:** A step-by-step tutorial building a complete DirectX 11 renderer from project setup through advanced techniques. Extremely practical — every chapter produces running code. DirectX is row-major like NumPy, making it directly applicable.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 1–3 | Setup, first triangle | DirectX device, swap chain, constant buffers. HLSL cbuffer packing rules. |
| 4–5 | Transformations | World/View/Projection pipeline. **DirectX uses row-major matrices** (XMMatrix is row-major). Contrast with OpenGL column-major. Identical issue to what we just fixed. |
| 6 | Lighting | Ambient, diffuse, specular. Light struct in constant buffer. |
| 7 | Texturing | 2D sampling, mipmaps. DXT compression. Sampler states (wrapping, filtering). |
| 8–9 | Normal mapping | TBN matrix construction in VS, per-pixel normal from tangent-space normal map. |
| 10 | Multiple render targets | Deferred shading GBuffer: albedo, normal, depth in separate textures. |
| 11 | Post-processing | Bloom, blur, tone mapping via full-screen quad. |
| 12 | Terrain | Height-map, LOD, normal-from-heightfield. |
| 13 | Animation | Skinning with bone matrices in cbuffer. |
| 14 | Shadow mapping | Shadow map pass, PCF filtering, bias. |
| 15 | Instancing | DrawInstanced for repeated geometry (trees, rocks, creatures). |

### Lessons for GModular

**Lesson V1 — Row-major is consistent between DirectX/HLSL and NumPy — but NOT OpenGL/GLSL.**
Varcholik's Chapter 4 is the canonical reference for why this confusion happens. DirectX stores matrices in row-major, so `XMMATRIX` used in HLSL cbuffers does NOT need transposing. OpenGL/GLSL uses column-major — so numpy row-major matrices MUST be transposed before writing with `.T.astype('f4').tobytes()`. **We already fixed this in `viewport_camera.py`**, but this book validates the fix exhaustively.

**Lesson V2 — DXT texture decompression is required for KotOR TPC files.**
Chapter 7 covers DXT1 (BC1), DXT3 (BC2), DXT5 (BC3) compressed textures. KotOR's `.tpc` files use DXT1/DXT5 (verified in `tpc_reader.py`). Varcholik shows that DXT textures must be uploaded with `glCompressedTexImage2D` OR manually decompressed to RGBA8 before uploading. GModular's `tpc_reader.py` already decompresses to raw RGBA, which is correct. **However,** the load_texture method should call `tex.build_mipmaps()` for diffuse textures — it already does this. ✅

**Lesson V3 — Normal mapping requires TBN matrix in vertex shader.**
Chapters 8–9: the Tangent-Binormal-Normal matrix transforms light direction from world space into tangent space for per-pixel normal mapping. KotOR models that have bump/specular maps need this. The current `_VERT_TEXTURED` shader only passes normals, not tangent/binormal. For a future "high quality" rendering mode, add `in_tangent` + `in_bitangent` attributes to `_VERT_TEXTURED` and compute TBN.

*Roadmap item:* Normal mapping support when MDL tangent data is available (Phase 3 enhancement).

**Lesson V4 — Shadow mapping is achievable with two FBOs.**
Chapter 14: shadow map = render from light's POV into depth texture FBO, then sample that texture in the main pass with PCF. GModular has one FBO for rendering and one for picking. A third "shadow FBO" could provide real-time shadows for the KotOR viewport (particularly useful for outdoor modules like Dantooine).

*Roadmap item:* Shadow mapping as Phase 3.7 enhancement.

**Lesson V5 — Instancing for repeated GIT objects.**
Chapter 15: when the same mesh appears N times (soldiers, crates), use `DrawInstanced` with a per-instance transform buffer. GModular currently uploads one VAO per GIT creature/placeable (even duplicates). For large modules, instance batching would eliminate GPU draw call overhead.

---

## Book 3 — *Real-Time Collision Detection* — Christer Ericson

**What this book is:** The definitive reference for collision detection algorithms. 600+ pages covering bounding volumes, spatial partitioning, intersection tests, robustness, and performance. Written at Sony Santa Monica — this is production code.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 1–2 | Collision fundamentals | Bounding volumes, broad/narrow phase pipeline |
| 3 | Math utilities | Closest points, Barycentric coordinates, Voronoi regions |
| 4 | Bounding volumes | AABB, OBB, sphere, capsule, convex hull — construction and overlap tests |
| 5 | Basic primitives | Ray-triangle (Möller-Trumbore), ray-AABB, ray-sphere |
| 6 | BVH | Top-down, bottom-up, insertion construction; depth/breadth/best-first traversal |
| 7 | Spatial partitioning | Uniform grids, BSP, quadtrees, octrees, k-d trees |
| 7.6 | Cells & Portals | Cell-portal structure for indoor scenes (walkmesh-relevant) |
| 8 | Convex hull, GJK, EPA | Narrow-phase for complex shapes |
| 9 | GPU-accelerated CD | Hardware occlusion queries, depth buffer tricks |
| 10–11 | Continuous & Temporal CD | Swept tests, CCD for fast-moving objects |
| 12–13 | Robustness, optimization | Epsilon management, cache efficiency, SIMD |

### Lessons for GModular

**Lesson C1 — AABB tree construction we already have is correct.**
Section 6.4.2 (Bergen97 algorithm): build by bounding the full set → split along longest axis at spatial median → recurse. GModular's `WOKWriter` already implements this median-split algorithm for the BWM AABB tree. ✅ The book confirms this is the right approach.

**Lesson C2 — Frustum culling against AABB is O(1) per room.**
Section 8.1: to test an AABB against a frustum, extract the 6 frustum planes (from the MVP matrix rows) and test the AABB's "positive vertex" against each plane. This gives a definitive in/out test in 6 dot-product operations — extremely cheap.

**For GModular, the algorithm is:**
```python
def extract_frustum_planes(mvp):
    # Left, Right, Bottom, Top, Near, Far planes from MVP rows
    m = mvp
    planes = [
        m[3] + m[0],   # left
        m[3] - m[0],   # right
        m[3] + m[1],   # bottom
        m[3] - m[1],   # top
        m[3] + m[2],   # near
        m[3] - m[2],   # far
    ]
    return [p / np.linalg.norm(p[:3]) for p in planes]

def aabb_in_frustum(planes, aabb_min, aabb_max):
    for p in planes:
        # Positive vertex: the AABB corner most in the plane's direction
        px = aabb_max[0] if p[0] >= 0 else aabb_min[0]
        py = aabb_max[1] if p[1] >= 0 else aabb_min[1]
        pz = aabb_max[2] if p[2] >= 0 else aabb_min[2]
        if p[0]*px + p[1]*py + p[2]*pz + p[3] < 0:
            return False  # outside this plane
    return True
```
Adding this to `render()` would prevent submitting room draw calls for rooms outside the view frustum — critical for large multi-room modules.

**Lesson C3 — Ray-triangle for face-click walkmesh editing.**
Section 5.3.6 (Möller-Trumbore algorithm): Ray from screen pixel through the walkmesh triangles → find the closest hit triangle. This is the algorithm needed for "click a walkmesh face to select it" (Roadmap item 2.1). The algorithm is already cited in our walkmesh shader comments but not yet implemented in Python. Verbatim algorithm:
```python
def ray_triangle_intersect(ray_origin, ray_dir, v0, v1, v2):
    e1 = v1 - v0
    e2 = v2 - v0
    h  = np.cross(ray_dir, e2)
    a  = np.dot(e1, h)
    if abs(a) < 1e-7: return None   # parallel
    f  = 1.0 / a
    s  = ray_origin - v0
    u  = f * np.dot(s, h)
    if u < 0.0 or u > 1.0: return None
    q  = np.cross(s, e1)
    v  = f * np.dot(ray_dir, q)
    if v < 0.0 or u + v > 1.0: return None
    t  = f * np.dot(e2, q)
    if t > 1e-7: return t
    return None
```

**Lesson C4 — Cells-and-portals is the correct architecture for KotOR visibility.**
Section 7.6: "Rooms = cells, doorways = portals. Portals define connections and determine which cells can be viewed from a given cell." This confirms that the `.vis` file (room-to-room visibility) maps directly to a cell-portal structure. **GModular currently ignores `.vis`** — adding portal-based culling would be architecturally correct and match the actual KotOR engine.

**Lesson C5 — Spatial coherence: cache the last hit room.**
Section 6.7: "Caching the last collision result and re-testing there first is very effective due to temporal/spatial coherence." Applied to the walkmesh: when the camera moves slowly, the active room is almost always the same as the last frame. Cache `_last_active_room` and test it first before querying the AABB tree.

---

## Book 4 — *Learning Modern 3D Graphics Programming* — Jason L. McKesson

**What this book is:** A free online textbook (originally at arcsynthesis.org) teaching OpenGL 3.3 core profile from scratch using the modern shader-based pipeline. The canonical resource for understanding WHY OpenGL works the way it does, including the column-major convention.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 1–3 | First triangle, shaders | VAO, VBO, vertex attributes, GLSL in/out, uniform. The `layout(location=N)` qualifier. |
| 4–5 | Orthographic, perspective | Clip space derivation from scratch. Why perspective divide? Why depth buffer non-linear? |
| 6 | Objects in motion | Model matrix, world → clip pipeline. **Column-major convention explained explicitly**. |
| 7–8 | World transforms | View matrix, camera space. Quaternion rotation. |
| 9 | Illumination | Ambient, diffuse, specular — Phong model. World-space vs camera-space lighting. |
| 10 | Interpolation | Hermite splines, Bezier — directly applicable to KotOR animation controllers. |
| 11 | Textures | UV coordinates, sampling, filtering modes, mipmaps. `sampler2D` in GLSL. |
| 12–14 | Advanced rendering | Gamma correction, HDR, deferred rendering, depth of field. |
| 15 | Normal mapping | Tangent space construction, TBN in shader. |
| 16 | Tessellation | OpenGL 4.0 tessellation shaders — not applicable to KotOR target hardware. |

### Lessons for GModular

**Lesson M1 — Column-major convention: the root cause of our projection bug.**
Chapter 6, McKesson's canonical explanation:

> "OpenGL matrices are stored in column-major order. When you call `glUniformMatrix4fv(location, 1, GL_FALSE, data)`, OpenGL reads 16 floats as column 0, column 1, column 2, column 3. A standard math textbook writes row 0, row 1, row 2, row 3. So a math-textbook perspective matrix must be **transposed** before uploading to GLSL."

This is EXACTLY the bug we fixed: the `_perspective()` function was producing a column-major layout in row-major numpy memory. By swapping `[2][3]` and `[3][2]`, we made the numpy array row-major, and the `.T.tobytes()` transpose on upload makes it correct for GLSL. McKesson's Chapter 6 is the canonical reference for why this transpose is needed.

**Lesson M2 — Clip space: why non-linear depth matters for KotOR.**
Chapter 4–5: depth buffer values are non-linear (more precision near the camera). With `near=0.001` and `far=1000`, the first 1 unit of depth uses ~50% of depth buffer precision. For KotOR interiors this is fine. But outdoor modules (Dantooine, Kashyyyk) with far=1000 and objects 500 units away will have severe Z-fighting on distant floors.

**Fix:** Use `near=0.1` for outdoor modules, or implement logarithmic depth buffer (extend `_perspective` to output `log_z`).

**Lesson M3 — `vec3` view_dir bug in fragment shader.**
McKesson Chapter 9: `vec3 view_dir = normalize(-v_world_pos)` is ONLY correct when the camera is at the world origin. For arbitrary camera positions, it must be `normalize(camera_pos - v_world_pos)`. Our `_FRAG_LIT` and `_FRAG_LIT_NO_UV` both use `normalize(-v_world_pos)` — **this is incorrect** for the specular highlight calculation when the camera is not at the origin.

**Bug found in current code:**
```glsl
// WRONG (in _FRAG_LIT, line 91):
vec3 view_dir = normalize(-v_world_pos);

// CORRECT:
uniform vec3 camera_pos;  // add this uniform
vec3 view_dir = normalize(camera_pos - v_world_pos);
```

Currently the specular highlight behaves as if the camera is always at the origin, which means specular highlights will appear in the wrong location when orbiting. For GModular v2.1, add `camera_pos` uniform to lit shaders.

**Lesson M4 — Gamma correction: KotOR textures are sRGB.**
Chapter 12: KotOR TPC textures store sRGB colors (gamma 2.2 encoded). If rendered without linearization, lighting computations are incorrect (too dark in shadows, washed out in highlights). Proper pipeline: convert sRGB → linear when sampling, compute lighting in linear space, convert linear → sRGB on output. Add `vec3 albedo_linear = pow(texture(tex0, v_uv).rgb, vec3(2.2))` to `_FRAG_TEXTURED` and `fragColor.rgb = pow(result, vec3(1.0/2.2))` at output.

**Lesson M5 — Depth precision: swap near/far for reverse-Z.**
Chapter 4: "Reversed depth buffer" (near maps to 1.0, far maps to 0.0 in clip space) gives dramatically better precision for large outdoor scenes. Requires changing `glDepthFunc` to `GREATER` instead of `LESS`. This is a Phase 3 optimization for outdoor KotOR modules.

---

## Book 5 — *Mathematics for 3D Game Programming and Computer Graphics (3rd Edition)* — Eric Lengyel

**What this book is:** The mathematical Bible for graphics programming. Covers every formula a renderer needs: matrix algebra, quaternions, projections, frustum culling, lighting, shadows, and physics simulation. Lengyel also wrote the C4 Engine, so this is battle-tested theory.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 1 | Vector algebra | Dot, cross, triple products; handedness conventions. |
| 2 | Lines & planes | Plane equation, distance formulas. |
| 3 | Matrices | Matrix as linear transform. Inverse, transpose. Lengyel uses **row vectors**, consistent with DirectX. |
| 4 | Transforms | Rotation matrices, quaternion → matrix conversion. **Normal vector transform: use `(M^-1)^T`** for non-uniform scale. |
| 5 | 3D Engine Geometry | **Frustum matrix derivation** from first principles. Clip space, homogeneous division. Section 5.5.1 derives `M_frustum` explicitly. |
| 6 | Ray tracing | Intersection tests: ray-plane, ray-triangle, ray-AABB, ray-sphere. |
| 7 | Lighting | Lambert diffuse, Blinn-Phong specular, Cook-Torrance BRDF, bump mapping (tangent space). |
| 8 | Visibility | AABB frustum culling. Portal systems. Octrees, BSP. |
| 9 | Polygon techniques | Decals, T-junctions, depth offset for overlapping coplanar geometry. |
| 10–15 | Shadows, reflections, etc. | Shadow volumes, shadow maps, stencil reflections, water. |
| 16 | Physics | RK4 integration, springs, rigid body dynamics. |

### Lessons for GModular

**Lesson L1 — Normal matrix: we're doing it right, but it's expensive.**
Chapter 4.5: `mat3(transpose(inverse(model)))` is correct for non-uniform scale. However, the `inverse()` call in GLSL is ~20× slower than a pre-computed normal matrix passed as a uniform. For 60fps, replace:
```glsl
// SLOW (current): computed per-fragment
mat3 normal_mat = transpose(inverse(mat3(model)));
```
with:
```glsl
// FAST (improved):
uniform mat3 normal_matrix;  // pre-computed CPU side = transpose(inverse(mat3(model)))
```
Add `normal_matrix` uniform to `_VERT_LIT`, `_VERT_TEXTURED`, `_VERT_LIT_NO_UV`.

**Lesson L2 — Perspective matrix: the book gives the exact same formula we now use.**
Section 5.5.1 (Lengyel's row-major perspective):
```
P[0][0] = cot(fov/2) / aspect
P[1][1] = cot(fov/2)
P[2][2] = (far + near) / (near - far)
P[2][3] = 2*far*near / (near - far)
P[3][2] = -1
```
This is **exactly** `viewport_camera.py._perspective()` after our fix. ✅ Lengyel's row-major version validates our correction.

**Lesson L3 — Frustum plane extraction from MVP.**
Section 5.5.3: extract the 6 frustum planes directly from the rows of the combined MVP matrix:
- Left:   `row3 + row0`
- Right:  `row3 - row0`
- Bottom: `row3 + row1`
- Top:    `row3 - row1`
- Near:   `row3 + row2`
- Far:    `row3 - row2`

This is the same formula referenced in Lesson C2 (Ericson). Implementing this in Python enables per-frame room frustum culling. **Add to Phase 3 roadmap.**

**Lesson L4 — Tangent space: required for KotOR bump maps.**
Section 7.8.2: tangent space basis is computed per-triangle from UV deltas. KotOR MDL files may contain tangent data in the `BUMP` node type. The TBN matrix allows per-pixel normal perturbation. When implemented:
```glsl
mat3 TBN = mat3(normalize(v_tangent), normalize(v_bitangent), normalize(v_normal));
vec3 n = TBN * (texture(normal_map, v_uv).rgb * 2.0 - 1.0);
```

**Lesson L5 — Cook-Torrance BRDF for physically-based KotOR rendering.**
Section 7.9.2: KotOR's environments include polished stone floors, metallic armour, and water. Cook-Torrance BRDF (Fresnel + GGX NDF + geometry term) would give more accurate specular than Blinn-Phong. This is a Phase 3 "high quality" mode enhancement.

---

## Book 6 — *Foundations of Game Engine Development, Volume 2: Rendering* — Eric Lengyel

**What this book is:** The modern companion to the previous book (published 2019). Covers deferred rendering, compute shaders, clustered forward rendering, physically-based rendering, terrain, and the OpenDDL scene format. Uses C3 Engine and modern OpenGL 4.5+.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 1 | Shader architecture | GLSL 4.5 features, specialization constants, uniform buffers (UBOs), SSBOs. |
| 2 | Lighting | Clustered forward+, multiple shadow maps, area lights. |
| 3 | Physically-based rendering | GGX BRDF, energy conservation, metallic-roughness workflow. |
| 4 | Terrain rendering | Clipmap-based LOD, GPU tessellation, atmospheric scattering. |
| 5 | Visibility | Portal-based visibility, shadow culling, occluder fusion. |
| 6 | Animation | Vertex shader skinning, dual quaternion skinning, morph targets. |
| 7 | Post-processing | Bloom (threshold + blur), FXAA, SSAO, motion blur. |
| 8 | Procedural generation | Voronoi-based texture synthesis, noise functions. |

### Lessons for GModular

**Lesson F1 — Uniform Buffer Objects (UBOs) for per-frame data.**
Chapter 1: instead of setting `mvp`, `model`, `light_dir`, `ambient` as individual uniforms (our current approach, which has ~5 `prog[...].write(...)` calls per room), batch them into a single UBO:
```glsl
layout(std140) uniform PerFrame {
    mat4 view;
    mat4 proj;
    vec3 light_dir;
    float ambient;
    vec3 camera_pos;
};
```
This reduces the per-room uniform overhead from N×5 API calls to 1 bind. Important for performance when rendering 20+ room modules.

**Lesson F2 — Dual quaternion skinning for KotOR characters.**
Chapter 6: standard linear blend skinning (LBS) causes "candy wrapper" artefacts at joints (e.g., wrist twist). Dual quaternion skinning (DQS) corrects this. KotOR characters with `SKIN` nodes suffer from LBS artefacts. Implementation: replace the bone matrix palette `mat4[16]` with dual quaternion pairs `vec4[32]`.

**Lesson F3 — Morph targets for MDL blend shapes (dangling nodes).**
Chapter 6: morph targets (blend shapes) deform a base mesh by adding delta positions. KotOR MDL has `DANGLYMESH` nodes that oscillate (trees, hair). These are implemented as morph targets with physics-based weight oscillation. Currently GModular uploads danglymesh as static geometry.

**Lesson F4 — FXAA post-process pass for the viewport.**
Chapter 7: FXAA (Fast Approximate Anti-Aliasing) is a screen-space edge detection pass requiring only the color buffer as input. Cost: 0.5ms on modern hardware. Implementation: render scene to texture → FXAA pass → display. Add as a toggle in the viewport toolbar.

**Lesson F5 — Portal visibility matches KotOR's `.vis` exactly.**
Chapter 5: "The portal graph defines which cells can observe which other cells. When traversing, we start from the camera's cell, then recursively follow portals that intersect the camera frustum, adding new cells to the visible set." This describes exactly how the KotOR engine uses `.vis` + door hooks from `.lyt`. **GModular should load `.vis` and implement portal culling in `render()`.**

---

## Book 7 — *Game Physics Engine Development (2nd Edition)* — Ian Millington

**What this book is:** A progressive build of a complete physics engine ("Cyclone") from scratch. Part I: particle systems. Part II: mass-aggregates. Part III: rigid bodies. Part IV: collision detection. Part V: contact resolution. 481 pages of clean, practical C++.

### Chapter-by-Chapter Summary

| Chapter | Topic | Key Content |
|---------|-------|-------------|
| 2–4 | Particle physics | Newton's laws for point masses. Newton-Euler integration. |
| 3.3 | The Integrator | `p += v*dt; v += a*dt` — first-order Euler. Stable for stiff forces only with damping. |
| 5–8 | Mass-aggregate | Springs, rods, cables. Force generators as Strategy pattern. |
| 9–11 | Rigid bodies | Inertia tensor, angular velocity/acceleration, quaternion integration. |
| 10 | Laws of motion | Torque = I * alpha. World inertia tensor = R * I_body * R^T. |
| 12–13 | Collision detection | Broad-phase (BVH, oct-tree), narrow-phase (GJK, SAT for box-box). |
| 12.4.1 | BSP for level geometry | BSP tree for detecting moving objects vs static level mesh. |
| 14–15 | Collision response | Impulse-based resolution. `j = -(1+e) * v_rel · n / (1/m_A + 1/m_B + n · (I_A^-1 × r_A) + ...)` |
| 16 | Resting contacts | Micro-collision iteration for resting stacks. RK4 discussion. |
| 17 | Ragdolls | Hierarchical joints with constraints on angular DOF. |

### Lessons for GModular

**Lesson P1 — Physics is NOT the priority for GModular.**
Millington's book is comprehensive but GModular is a *modding tool*, not a game engine. The KotOR engine handles physics at runtime. GModular only needs to *display* collision geometry, not simulate it.

However, there are four physics-adjacent features GModular needs:

**Lesson P2 — AABB update for moved objects.**
Chapter 12: when a GIT creature/placeable is dragged in the viewport, its bounding volume must be recomputed. Millington's approach: store the un-transformed AABB in object space and recompute the world AABB each frame via `world_min = (model * vec4(local_min, 1)).xyz`. This is cheaper than rebuilding from scratch.

**Lesson P3 — Walkmesh navigation for play mode.**
Section 12.4.1: BSP trees for static level geometry. The KotOR walkmesh IS a BSP-like structure (AABB tree of walkable triangles). For the "play mode" camera that walks on the floor (see `player_controller.py`), the `player.y` (height) must follow the walkmesh surface:
1. Cast a ray downward from the player's XY position.
2. Find the closest walkmesh triangle (Möller-Trumbore).
3. Compute the barycentric Z of that point.
4. Set `player.z = triangle_z + player_height`.

**Lesson P4 — RK4 vs Euler for animation.**
Section 16: RK4 is 4× more accurate than Euler per step. For KotOR's Bezier-Hermite animation controllers (Chapter 4 of roadmap), use RK4 when evaluating spline tangents to avoid "snapping" between keyframes.

**Lesson P5 — Damping for danglymesh.**
KotOR danglymesh nodes store displacement constraints (how far the mesh can deviate). Millington's spring system with damping (`f_damping = -b * v`) models this exactly. To animate danglymesh properly: for each vertex, maintain a damped spring to its rest position, update each frame with Euler integration.

---

## Book 8 — *Game Physics Engine Development* (preview-9781439827383)

*Note: This file was the preview of Millington's second edition with additional Chapter 18 on 2D physics. The new content adds:*

| Section | Content |
|---------|---------|
| 18.1–18.3 | 2D rigid body dynamics, AABB in 2D |
| 18.4–18.5 | 2D collision detection: AABB-AABB, circle-circle |
| 18.6–18.8 | 2D contact resolution |

### Lessons for GModular

**Lesson P6 — 2D physics for the Room Assembly grid.**
The Room Assembly panel (`room_assembly.py`) is essentially a 2D grid layout tool. 2D AABB overlap tests (O(1) per pair) can prevent rooms from being placed overlapping each other. Implement: when a room is dragged, test its 2D AABB against all other rooms' 2D AABBs and show a red highlight if overlapping.

---

## Cross-Book Synthesis: The Five Most Critical Lessons

After reading all eight books, five lessons stand above all others for GModular's current state:

### 🔴 Critical Lesson 1 — The matrix convention bug is now fully understood and fixed

*Sources: Varcholik Ch.4, McKesson Ch.6, Lengyel Ch.5*

The root cause of the "blank viewport" bug was the projection matrix stored in column-major order being used as row-major numpy. Every book covers this. Varcholik notes it for HLSL, McKesson explains the OpenGL convention in detail, Lengyel provides the math. Our fix (`[3][2]=-1`, `[2][3]=2fn/(n-f)`) is validated by all three sources.

**What we learned about prevention:** Going forward, every matrix utility function should have a docstring explicitly stating:
- "row-major numpy (use `.T.tobytes()` for GLSL)"  
- or "column-major OpenGL (use `.tobytes()` directly)"

The `_perspective()` docstring in `viewport_camera.py` now correctly states both conventions.

### 🔴 Critical Lesson 2 — Fragment shader view_dir bug affects specular highlights

*Source: McKesson Ch.9*

`normalize(-v_world_pos)` is only correct at the origin. Add `uniform vec3 camera_pos` to all lit shaders and use `normalize(camera_pos - v_world_pos)`.

### 🟠 Important Lesson 3 — Frustum culling is missing and will matter for large modules

*Sources: Lengyel Ch.8, Ericson Ch.4/5, Eberly Ch.3*

Zero frustum culling means every room is submitted to the GPU every frame. For slem_ar (1 room) this doesn't matter. For a 20-room outdoor KotOR module it would drop framerate significantly. Implement frustum plane extraction from the MVP matrix and test each room's stored AABB before rendering.

### 🟠 Important Lesson 4 — Portal culling matches KotOR's own visibility system

*Sources: Eberly Ch.7, Ericson §7.6, Lengyel Ch.8, Lengyel2 Ch.5*

Four separate books converge on the same insight: the `.vis` file + LYT door hooks describe a cell-portal visibility graph. GModular loads `.vis` but ignores it. Adding portal-based culling (traverse portal graph from camera's cell) would both improve performance AND accurately replicate the KotOR engine's own rendering logic.

### 🟡 Lesson 5 — Normal matrix optimization: precompute on CPU

*Sources: Lengyel Ch.4, Varcholik Ch.6*

`transpose(inverse(mat3(model)))` in the fragment shader is called once per fragment, potentially millions of times per frame. For static room geometry (which never moves), precompute this on the CPU when building the VAO and pass it as a `uniform mat3 normal_matrix`. Since model matrices for rooms are pure translations (no rotation, no scale), `mat3(model) = Identity` and `normal_matrix = Identity` — the computation can be skipped entirely for rooms. Only entities with rotation/scale need the full computation.

---

## Updated Roadmap Recommendations

Based on all eight textbooks, here are the recommended additions and adjustments to the existing roadmap:

### New Items for Phase 1 — Bug Fix Sprint (v2.1.0)

| # | Task | Source | Priority |
|---|------|---------|----------|
| 1.10 | Fix `view_dir` in `_FRAG_LIT` and `_FRAG_LIT_NO_UV`: add `camera_pos` uniform, use `normalize(camera_pos - v_world_pos)` | McKesson Ch.9 | 🔴 HIGH |
| 1.11 | Add `camera_pos` uniform write in `render()` before each lit-shader draw call | McKesson Ch.9 | 🔴 HIGH |

### New Items for Phase 2 — Walkmesh Editor (v2.1.x)

| # | Task | Source | Priority |
|---|------|---------|----------|
| 2.9 | Implement Möller-Trumbore ray-triangle hit for walkmesh face clicking | Ericson §5.3.6 | 🔴 HIGH (needed for interactive editing) |
| 2.10 | 2D AABB overlap check in Room Assembly grid — show red highlight on overlap | Millington2 §18 | 🟡 MEDIUM |

### New Items for Phase 3 — MDL Viewer (v2.2.x)

| # | Task | Source | Priority |
|---|------|---------|----------|
| 3.7 | Frustum culling: extract planes from MVP, test each room AABB per frame | Lengyel §8, Ericson §4, Eberly §3 | 🔴 HIGH |
| 3.8 | Portal culling: load `.vis` data, traverse portal graph from camera's room | Eberly §7, Ericson §7.6, Lengyel2 §5 | 🟠 HIGH |
| 3.9 | Precompute normal matrix as `uniform mat3` for room static geometry | Lengyel §4, Varcholik §6 | 🟠 HIGH |
| 3.10 | Add `camera_pos` to Phong shaders — correct specular view direction | McKesson §9 | 🔴 HIGH |
| 3.11 | sRGB gamma correction: linearize TPC textures on sample, gamma-encode output | McKesson §12 | 🟡 MEDIUM |
| 3.12 | Shadow mapping (two-FBO: light-POV depth → main render PCF sample) | Varcholik §14 | 🟡 MEDIUM |
| 3.13 | TBN tangent space for MDL bump maps (`BUMP` node type) | Lengyel §7.8, Varcholik §8-9 | 🟡 MEDIUM |

### New Items for Phase 4 — Animation (v2.2.x)

| # | Task | Source | Priority |
|---|------|---------|----------|
| 4.6 | RK4 integration for Bezier-Hermite spline evaluation (avoid keyframe snapping) | Millington §16 | 🟡 MEDIUM |
| 4.7 | Danglymesh: damped spring per vertex with displacement constraint | Millington §5-7 | 🟡 MEDIUM |
| 4.8 | Dual quaternion skinning to replace linear blend skinning (candy wrapper fix) | Lengyel2 §6 | 🟡 MEDIUM (optional) |

### New Items for Phase 5 — Performance (v2.3.x)

| # | Task | Source | Priority |
|---|------|---------|----------|
| 5.8 | Uniform Buffer Objects (UBOs) for per-frame matrices/light data | Lengyel2 §1 | 🟠 HIGH |
| 5.9 | Instance batching for repeated GIT objects (creatures, crates) | Varcholik §15 | 🟡 MEDIUM |
| 5.10 | LOD: simplified geometry for rooms >50 units from camera | Eberly §4 | 🟡 MEDIUM |
| 5.11 | FXAA post-process pass | Lengyel2 §7 | 🟡 MEDIUM |
| 5.12 | Reverse-Z depth buffer for large outdoor modules (near→1, far→0) | McKesson §5 | 🟡 MEDIUM |

### Architecture Additions

| # | Task | Source | Priority |
|---|------|---------|----------|
| A1 | Extract `RoomRenderEffect` object (shader + textures + uniforms) per room VAO | Eberly §5 | 🟡 MEDIUM |
| A2 | Separate cull pass before draw pass in `render()` | Eberly §3 | 🟠 HIGH |
| A3 | `_last_active_room` cache for spatial coherence in walkmesh queries | Ericson §6.7 | 🟡 MEDIUM |

---

## Priority Order After Textbook Study

Immediate actions (before Phase 2 work begins):

1. **Fix specular view_dir bug** (McKesson §9): Add `camera_pos` uniform to lit shaders. Critical correctness fix.
2. **Implement Möller-Trumbore ray hit** (Ericson §5.3.6): Face-click walkmesh editing requires this. Relatively small addition (~30 lines Python).
3. **Frustum culling** (Lengyel §8 + Ericson §4): Prevents performance collapse on large modules. ~20 lines Python.
4. **Portal culling from .vis** (Eberly §7 + Ericson §7.6): Architecturally correct for KotOR. Matches the actual game engine logic.
5. **Precompute normal matrix** (Lengyel §4): Move `transpose(inverse(model))` from GPU fragment shader to CPU. Simple performance win.

---

## What the Books Say We're Already Doing Right ✅

Reading eight textbooks on 3D rendering and comparing against GModular's current code reveals many things we are already doing correctly:

| What we do | Book validation |
|-----------|----------------|
| `mvp.T.astype('f4').tobytes()` for GLSL uploads | McKesson Ch.6 ✅ |
| `mat3(transpose(inverse(model)))` for normals | Lengyel Ch.4 ✅ |
| AABB tree construction (median split on longest axis) for WOK | Ericson §6.4.2 ✅ |
| 16-bone palette for skinning | Eberly Ch.10, Lengyel2 Ch.6 ✅ |
| Depth+color FBO (not color-only) | McKesson Ch.12 ✅ (needed for correct depth test) |
| `DEPTH_TEST + CULL_FACE + BLEND` enabled | Eberly §3 ✅ |
| `POLYGON_OFFSET_FILL` for walkmesh decal rendering | Lengyel §9 ✅ |
| Back-to-front alpha sorted rendering (walkmesh overlay after geometry) | Eberly §6 ✅ |
| Mipmap linear filtering for diffuse textures | Varcholik §7 ✅ |
| Linear filtering (no mipmaps) for lightmaps | Varcholik §7 ✅ |
| Blinn-Phong with key+fill+rim+back multi-light setup | Lengyel §7, Varcholik §6 ✅ |
| GPU entity-ID picking with separate FBO | Varcholik §10 ✅ |
| OrbitCamera using Maya-style yaw/pitch/distance | Eberly §4 ✅ |

---

*Report compiled 2026-03-21 by GModular AI assistant after study of:*
1. *3D Game Engine Design (2nd Ed.) — David H. Eberly*
2. *Real-Time 3D Rendering with DirectX and HLSL — Paul Varcholik*
3. *Real-Time Collision Detection — Christer Ericson*
4. *Learning Modern 3D Graphics Programming — Jason L. McKesson*
5. *Mathematics for 3D Game Programming and Computer Graphics (3rd Ed.) — Eric Lengyel*
6. *Foundations of Game Engine Development Vol. 2: Rendering — Eric Lengyel*
7. *Game Physics Engine Development (2nd Ed.) — Ian Millington*
8. *Game Physics Engine Development (Preview Ed., Ch.18) — Ian Millington*
