# GModular — Development Log

This file tracks major development iterations. For the full technical spec and IPC contract see [PIPELINE_SPEC.md](PIPELINE_SPEC.md).

---

## Iteration 25 (2026-03-18) — Resource Type Map Audit, 1,933 Tests Passing

### PyKotor/Kotor.NET Format Deep-Scan Findings

Continued the cross-reference audit of `gmodular/formats/archives.py` RES_TYPE_MAP against
the authoritative **PyKotor** `ResourceType` enum and **Kotor.NET** `KotorResourceType` enum.

**Confirmed Correct (no change needed):**

| ID   | Ext   | Source |
|------|-------|--------|
| 2016 | `wok` | PyKotor WOK = 2016 ✅ |
| 2023 | `git` | PyKotor GIT = 2023 ✅ |
| 2044 | `utp` | PyKotor UTP = 2044 ✅ |
| 3007 | `tpc` | PyKotor TPC = 3007 ✅ |
| 3000 | `lyt` | PyKotor LYT = 3000 ✅ |
| 3001 | `vis` | PyKotor VIS = 3001 ✅ |

**Stale Test Assertions Fixed:**
- `tests/test_archives.py`: assertions `git == 2026`, `wok == 2021` were incorrect legacy values
  carried over from the NWN2 era — corrected to `git == 2023`, `wok == 2016`
- `tests/test_formats.py`, `tests/test_mod_import.py`: assertions `lyt == 3006`, `vis == 3007`
  corrected to `lyt == 3000`, `vis == 3001`; `tpc == 2056` corrected to `tpc == 3007`
- `tests/test_mcp.py`: `test_build_index_module_entries` generator expression bug fixed
  (was using outer `entries` variable, now correctly uses per-iteration binding)

### SSF Slot Count Verified
- Kotor.NET `SSFBinaryStructure.cs` has a 40-entry table (padded for TSL future use);
  PyKotor confirms the **game format uses exactly 28 slots** (V1.1 spec) — our implementation
  is correct.

### NCS Opcode Verification
- Compared our `NCSOpcode` table against PyKotor `NCSByteCode` enum — 100% match for all
  0x01–0x2D opcodes; our `T (0x42)` and `WRITEARRAY (0xff)` extensions are also present
  in PyKotor as extended type qualifiers.

### LYT/VIS Implementation Note
- Kotor.NET `LYTReader.cs` currently throws `NotImplementedException` — our Python
  LYT implementation is **ahead** of the C# reference.

### Tests
- **Total: 1,933 tests, 100% pass rate** (0 regressions from all test fixes)

---

## Iteration 24 (2026-03-18) — Qt Designer .ui Files, main.py qtpy, Docstring Hygiene

### Honest Assessment of the Suggestions

The community suggestion to adopt **qtpy** first, then **Qt Designer files**, is the right order and we've now completed both steps:

1. **qtpy (done in iteration 23)**: costs ~1 hour, saves days when Qt6 migration becomes necessary. PyQt5 is feature-frozen; PyQt6 has better Wayland + ARM64 support. The `QT_API` env-var pattern lets users switch backends without touching any source code.

2. **Qt Designer .ui files (this iteration)**: the payoff is largest on the complex panels that accumulate hundreds of lines of manual layout code. `inspector.py` is a good first target — its content is dynamically populated anyway, but the outer chrome (scroll area, title, separator) is fixed and belongs in a `.ui` file. The `twoda_editor.py` and `dlg_editor.py` are also good candidates. The `ui_loader.py` fallback mechanism means migrating gradually is safe.

### Qt Designer Infrastructure
- Created `gmodular/gui/ui/` directory with four initial Designer files:
  - `inspector.ui` — Inspector sidebar (QScrollArea, dark VS Code style)
  - `twoda_editor.ui` — 2DA table editor (QTableView, toolbar, searchBox)
  - `dlg_editor.ui` — DLG node-graph (3-panel QSplitter, nodeTree QTreeView, canvas, properties)
  - `mod_import_dialog.ui` — Module import dialog (QDialogButtonBox, lstResources QListWidget)
- Created `gmodular/gui/ui/__init__.py` (package marker + file inventory)
- Created `gmodular/gui/ui_loader.py` utility:
  - `load_ui(filename, widget)` — loads `.ui` into an existing widget in-place; returns `False` gracefully on failure so Python fallback layouts still work
  - `load_ui_type(filename)` — returns `(Form, Base)` pair like `uic.loadUiType`
  - `list_ui_files()` — list all available `.ui` files in `ui/`
  - `ui_file(filename)` — absolute string path for a named `.ui` file
  - Falls back gracefully when `qtpy.uic` is unavailable

### main.py qtpy Migration
- `main.py` now imports Qt exclusively via `qtpy` — no bare `from PyQt5` anywhere in the runnable entry point
- Qt5-only attributes (`AA_EnableHighDpiScaling`, `AA_UseHighDpiPixmaps`, `QFont.PreferFullHinting`) guarded with `try/except AttributeError` for Qt6 compatibility
- Version string updated: `v1.0.0` → `v2.0.0`
- Install hint updated: mentions `qtpy`, not just `PyQt5`

### GModular.spec Updated
- Reads `QT_API` env var at spec-evaluation time to detect Qt backend
- Bundles `gmodular/gui/ui/` directory (reports count of `.ui` files)
- Adds `gmodular.gui.ui_loader` and `gmodular.gui.ui` to `hidden_imports`
- Adds `qtpy.uic` to `hidden_imports` (needed for runtime `.ui` loading)

### Docstring / Comment Hygiene
- `gmodular/gui/__init__.py`: "PyQt5 widgets" → "Qt widgets (via qtpy; works with PyQt5, PyQt6, PySide2, PySide6)"
- `gmodular/ipc/ghidra_bridge.py`: "If PyQt5 is available" → "If qtpy/Qt is available"
- `gmodular/formats/tpc_reader.py`: "if PyQt5 available" → "requires qtpy + Qt backend"

### Tests
- Added **33 new tests** in `tests/test_qtdesigner_uiloading.py`:
  - `TestUILoader` (8 tests) — import, `list_ui_files`, `load_ui` fallback, `load_ui_type` error
  - `TestUIFileXML` (12 tests) — XML validity, widget presence (`QScrollArea`, `QTableView`, `QSplitter`, `QDialogButtonBox`, etc.)
  - `TestMainPyQtpyMigration` (6 tests) — no bare PyQt5, Qt6 guards, version string, QT_API doc
  - `TestSpecUIBundling` (3 tests) — ui/ dir, `ui_loader`, `qtpy.uic` in hidden_imports
  - `TestDocstringHygiene` (4 tests) — all three updated docstrings validated
- **Total: 1,933 tests, 100% pass rate** (up from 1,900)

---

## Iteration 23 (2026-03-18) — qtpy Migration, LTR/NCS Formats, 79 MCP Tools

### Qt Compatibility Layer (qtpy)
- Migrated **all 21 files** with raw `from PyQt5…` imports to `from qtpy…`
- Replaced `pyqtSignal` / `pyqtSlot` with `qtpy.QtCore.Signal` / `Slot` throughout
- **`requirements.txt`**: added `qtpy>=2.4.0`
- **`setup.py`**: added `qtpy>=2.4.0` to `install_requires`
- **`GModular.spec`** (PyInstaller): `collect_all("PyQt5")` → `collect_all(_qt_backend)` where `_qt_backend` is determined by `$QT_API`; also collects the `qtpy` shim itself

### New Format Support (based on Kotor.NET + PyKotor deep scan)
- **LTR read/write** (`gmodular/formats/kotor_formats.py`):
  - `LTRData` dataclass — Markov chain name-generator (single/double/triple tables)
  - `read_ltr(data)` — parse "LTR V1.0" header + letter_count + three probability tables
  - `write_ltr(ltr)` — serialise back to binary; handles 26- and 28-letter variants
- **NCS write** (`write_ncs(ncs)`):
  - Re-assembler for patching disassembled scripts
  - Correctly sets the big-endian `total_size` field validated by the KotOR engine

### New MCP Tools (79 total)
| Tool | Description |
|------|-------------|
| `kotor_read_ltr` | Parse LTR file → letter count + start/mid/end probabilities |
| `kotor_write_ltr` | Build LTR binary from probability arrays |
| `kotor_write_ncs` | Re-assemble NCS from instruction list |

### Tests
- Added 22 new tests (LTR, NCS write, MCP tool registry)
- **Total: 1,900 tests, 100% pass rate**

---



### Qt Migration (Phase 1 — compatibility layer)
- Migrated 68 raw `from PyQt5…` imports to `from qtpy…` across 21 files
- Replaced `pyqtSignal` / `pyqtSlot` with `Signal` / `Slot`
- All GUI modules now Qt5/Qt6 agnostic at the import level

### New Modules
- **`gmodular/gui/dlg_editor.py`** — DLG visual node-graph editor
  - `DLGGraphData` / `DLGNodeData` pure-Python data model (mirrors PyKotor GFF structure)
  - `DLGCanvas` — qtpy widget with drag-to-connect node graph
  - `DLGEditorPanel` — toolbar (new/import/export), I/O bridges
  - MCP tools: `kotor_describe_dlg`, `kotor_dlg_parse`, `kotor_dlg_add_node`,
    `kotor_dlg_link_nodes`, `kotor_dlg_summarize`
- **`gmodular/ipc/nwscript_bridge.py`** — NWScript compile/decompile/check/format IPC bridge
  - Supports `nwnnsscomp`, DeNCS, PyKotor compiler, disasm fallback
  - MCP tools: `kotor_compile_nss`, `kotor_nss_check`, `kotor_nss_format`

### Format Library
- `TwoDAData.from_bytes()` — fixed magic check (`b"2DA "` + `b"V2.b"` properly detected)
- `_read_2da_binary_to_twoda()` — full binary 2DA reader
- `TwoDAData.headers` alias property for `columns`
- `TwoDALoader.from_bytes()` classmethod
- `TwoDAData` constructor `headers=` keyword alias
- All three `asyncio.get_event_loop()` calls in tests replaced with `asyncio.run()`

### Bug Fixes
- Architecture test `test_mcp_does_not_import_gui`: lazy imports added to `mcp/tools/__init__.py`
- `asyncio.get_event_loop()` → `asyncio.new_event_loop()` for Python 3.12 compat
- 2DA diff tools and roundtrip test failures resolved

### Tests
- **Total: 1,878 tests, 100% pass rate**

---



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
