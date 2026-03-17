# GModular — Development Log

This file tracks major development iterations. For the full technical spec and IPC contract see [PIPELINE_SPEC.md](PIPELINE_SPEC.md).

---

## Iteration 21 (2026-03-17) — Repository Audit & v2.0 Release

### Documentation
- Updated README.md, DEVELOPMENT.md, and PIPELINE_SPEC.md to accurately reflect
  all completed features (P1–P10 all implemented)
- Corrected IPC port references throughout docs: 7001 (GhostRigger), 7002 (GhostScripter), 7003 (GModular)
- Renamed `GHOSTWORKS_BLUEPRINT.md` → `PIPELINE_SPEC.md`

### Version Bump: 1.0.0-MVP → 2.0.0
- `gmodular/__init__.py` — `__version__` = `"2.0.0"`
- `gmodular/gui/main_window.py` — `APP_VERSION` = `"2.0.0"`
- `gmodular/ipc/callback_server.py` — `GMODULAR_VERSION` = `"2.0.0"`
- `setup.py` — version `"2.0.0"`, classifier `Production/Stable`, MIT license, Python 3.10–3.12

### License fix
- `setup.py` was incorrectly set to GPL-3.0 — corrected to MIT (matching LICENSE file and README)

### Branding clean-up
- Replaced `KotorModTools` org name with `GModular` in `main.py` and `setup.py`
- Updated About dialog: `GPL-3.0` → `MIT License`, `KotorModTools Suite` → `Ghostworks Pipeline`
- Renamed `GHOSTWORKS BLUEPRINT` references in `bridges.py` and `tests/test_new_features.py`
  to `PIPELINE_SPEC`

---

## Iteration 20+ (2026-03-17) — MDL Deep-Dive + Kotor.NET Analysis

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

## Iteration 19 (2026-03-16) — .MOD/.ERF/.RIM Module Import + Module Packager

### Added
- Full `.MOD`/`.ERF`/`.RIM` archive import dialog with resource type filtering
- `mod_packager.py` (750 lines) — dependency walker, full validation engine, ERF/MOD export
- `mod_packager_dialog.py` (415 lines) — UI for the packager with checklist, size estimate, warnings
- Module Validation Report (Module → Validate): tag uniqueness, ResRef length, script presence, door links, patrol waypoints, object bounds

---

## Iteration 18 (2026-03-15) — Comprehensive 3D Rendering Overhaul

### Added
- `MDLRenderer` class — ModernGL VAO pipeline, Phong lighting, LRU cache (max 64 models)
- Two render modes: **Solid** (lit Phong + texture) and **Wireframe**
- Frustum culling via 6 half-space tests against the VP matrix
- Door hook detection from MDL node names
- Walkmesh overlay (AABB nodes rendered in separate pass)
- `ViewportWidget` updated: orbit/pan/zoom, `F` to frame-all, object picking
- Transform gizmo (translate/rotate with gimbal snap keys)
- Play mode: FPS camera + walkmesh collision (`player_controller.py`)

---

## Iteration 17 (2026-03-14) — Pipeline Integration (P1/P4/P6/P7/P8/P9/P10)

### Added
- **P1 — Room Assembly Grid** (`room_assembly.py`, 1240 lines): drag-and-drop 2D top-down grid, auto-generates `.lyt` and `.vis`, door-hook scanning, room connection indicators, zoom controls
- **P4 — Patrol Waypoint Editor** (`patrol_editor.py`, 245 lines): click-to-place waypoints, auto-naming (WP_[TAG]_01…), dashed path preview in viewport, NWScript hint generation
- **P6 — Module Packager**: dependency walker starting from `.git`, collects all UTx, scripts, textures; `ERFWriter` for ERF/MOD/RIM output
- **P7 — Script IPC pencil buttons**: every script ResRef field in the Inspector has a pencil icon that calls `open_script` on GhostScripter via IPC
- **P8 — 2DA Lookup Layer** (`twoda_loader.py`, 559 lines + `TwoDAComboBox`): full 2DA parser with typed getters, column search, fallback built-in tables; Inspector shows "Gamorrean Guard (row 47)" instead of "47" for 2DA-backed fields
- **P9 — Blueprint IPC**: Inspector "Edit in GhostRigger" button calls `open_utc`/`open_utp`/`open_utd` on GhostRigger; `blueprint_saved` callback refreshes the viewport
- **P10 — Module Validation Report**: standalone panel with severity-sorted issues (error/warning/info)
- **Content Browser** (`content_browser.py`, 1057 lines): tile/list view toggle, category tree, live search, drag-to-place, asset icons, right-click context menu

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

### Walkmesh Export (GWOK)
The Walkmesh Editor exports to GModular's own **GWOK** binary interchange format (magic `"GWOK"`), not the native KotOR binary `.wok`. GhostRigger reads GWOK to rebuild KotOR-compatible geometry. Native `.wok` round-trip export requires GhostRigger.

---

## Known Gaps / Next Steps

1. **Animation playback** — MDL controller keyframes (position, orientation, scale) are fully parsed into `MeshNode` and stored; a timeline scrubber wired to the viewport render loop is the remaining work
2. **Native KotOR .wok export** — GWOK export works; producing a byte-for-byte valid KotOR `.wok` binary requires the AABB tree writer, which is a GhostRigger responsibility
3. **DLG dialogue tree editor** — `.dlg` GFF files are fully readable and writable; a visual node-graph editor (QGraphicsView canvas) for building dialogue trees is not yet built
4. **NWScript compiler** — the GhostScripter IPC bridge is complete and tested; the compiler itself lives in GhostScripter, which must be running on port 7002
