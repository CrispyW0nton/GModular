# GModular — Development Log

This file tracks major development iterations. For the full technical spec and IPC contract see [GHOSTWORKS_BLUEPRINT.md](GHOSTWORKS_BLUEPRINT.md).

---

## Iteration 20+ (2026-03-17) — MDL Deep-Dive + Kotor.NET Analysis

**Branch**: `genspark_ai_developer`

### Research
- Reviewed NickHugi/Kotor.NET rework branch C# source for MDL/GFF/TPC/2DA/ERF/RIM/LYT parsers
- Verified node header field order: NodeType(u16) @ 0, NodeIndex(u16) @ 2, NameIndex(u16) @ 4
- Mapped trimesh function pointer constants for K1/K2 PC/Xbox per-mesh detection

### Fixes — MDL Parser (`gmodular/formats/mdl_parser.py`)
- **NameIndex bug**: was reading from offset +2 (NodeIndex), now correctly reads from offset +4
- **Per-mesh K2 detection**: added `is_k2_mesh()` using trimesh FP values instead of global model FP
- **Model header fields**: added `model_type`, `fog`, `animation_scale`, `mdx_size`, `child_model_count`, `classification` to `MeshData`
- Added K1/K2 Xbox geometry function pointer constants

### Fixes — WOK Parser (`gmodular/formats/wok_parser.py`)
- Added `height_at_any(x, y)` — checks all triangles, not just above-plane hits
- Added `face_at(x, y)` — returns the `WalkFace` at a given XY position
- Added `surface_material_at(x, y)` — returns material ID at a position
- Added `bounds` property — `(min_x, min_y, max_x, max_y)` bounding box
- Added `walkable_region_center()` — centroid of walkable face centres
- Added `material_counts()` — dict of material ID → face count
- Added `clamp_to_walkmesh(x, y)` — snaps a point to the nearest walkable face centre

### Fixes — TPC Reader (`gmodular/formats/tpc_reader.py`)
- Added `is_cubemap` property (Height/Width == 6, matching Kotor.NET `TPCBinaryFileHeader`)
- Added `mip_count` property
- Added `mipmap_at(level)` — access specific mip level
- Added `get_rgba_at_level(level)` — RGBA bytes for a given mip level

### Fixes — MDL Renderer (`gmodular/engine/mdl_renderer.py`)
- Fixed MVP matrix transposition: now uses `.T.astype('f4').tobytes()` matching the viewport's column-major convention

### Tests
- Added 42 new tests covering all of the above
- **Total: 641 tests, 100% pass rate**

---

## Iteration 19 (2026-03-16) — .MOD/.ERF/.RIM Module Import

### Added
- Full `.MOD`/`.ERF`/`.RIM` archive import dialog
- Archive contents browser with resource type filtering
- `mod_packager.py` — builds `.mod` export archives

---

## Iteration 18 (2026-03-15) — Comprehensive 3D Rendering Overhaul

### Added
- `MDLRenderer` class — ModernGL VAO pipeline, Phong lighting, LRU cache
- Two render modes: **Solid** (lit Phong + texture) and **Wireframe**
- Frustum culling via 6 half-space tests against the VP matrix
- Door hook detection from MDL node names
- Walkmesh overlay (AABB nodes rendered in separate pass)
- `ViewportWidget` updated: orbit/pan/zoom, `F` to frame-all, object picking

---

## Iteration 3 (2026-03-06) — GFF Writer Fix & Test Suite Foundation

### Critical Fix
- `GFFWriter.to_bytes()` was a no-op stub → replaced with BFS two-phase encoder
- All GFF round-trips now produce byte-identical output

### Added
- `tests/test_gff.py` — initial 44 tests

---

## Architecture Notes

### GFF BFS Two-Phase Writer
KotOR's GFF format requires all struct indices to be stable before field data can reference them. GModular uses:

**Phase 1 — BFS Collect**: Walk tree breadth-first, assign stable index to every `GFFStruct`.
**Phase 2 — Encode Fields**: Encode all fields in BFS order; LIST/STRUCT fields embed pre-assigned indices.

### MDL Parser Design
The parser produces lightweight Python dataclasses (`MeshData`, `MeshNode`) with no OpenGL dependency, allowing it to be imported by tests and tools without a display context. The renderer (`MDLRenderer`) handles all GPU operations separately.

### WOK Surface Materials
Walkability is determined by `surfacemat.2da` row index stored in each face. Row 0 (Dirt), 1 (Obscuring), etc. The `_WALKABLE` table in `wok_parser.py` reflects the standard KotOR surface material definitions.

## Known Issues / Next Steps

1. Animation controller playback not yet wired to the viewport timeline
2. NWScript compiler integration requires GhostScripter running on port 5002
3. Walkmesh bake/export stub only — full export pending
4. DLG dialogue tree editor not yet implemented (read-only)
