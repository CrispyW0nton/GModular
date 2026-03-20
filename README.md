# GModular — KotOR 1 & 2 Module Editor

> **The Unreal Engine of KotOR modding — a community-built, open-source module editor for Star Wars: Knights of the Old Republic 1 & 2.**

GModular is a standalone Python/Qt desktop application that handles the complete KotOR mod pipeline: reading and writing every KotOR binary format, 3D model rendering, walkmesh editing, a DLG conversation-tree visual editor, an NWScript compile/decompile bridge, a 2DA table editor, an AI-powered MCP server, and exporting a complete `.mod` file ready to drop into `Modules/`.

It is designed to work alongside [GhostScripter](https://github.com/CrispyW0nton/GhostScripter-K1-K2) (NWScript IDE) and [GhostRigger](https://github.com/CrispyW0nton/Kotor-3D-Model-Converter/tree/genspark_ai_developer/GhostRigger-K1-K2) (model rigging tool) via a lightweight IPC bridge — together they form the **Ghostworks Pipeline**, a replacement for the fragmented toolchain the KotOR modding community currently uses.

Research sources driving this implementation:
- **[Kotor.NET](https://github.com/NickHugi/Kotor.NET)** — C# reference implementations for every binary format
- **[PyKotor](https://github.com/OldRepublicDevs/PyKotor)** — comprehensive Python KotOR library with DLG, NCS compiler, 2DA R/W

---

## Table of Contents

1. [What's New](#whats-new)
2. [Features](#features)
3. [System Architecture](#system-architecture)
4. [MCP Server — 103 Tools](#mcp-server)
5. [Format Support](#format-support)
6. [Qt Compatibility (qtpy)](#qt-compatibility)
7. [Qt Designer .ui Files](#qt-designer-ui-files)
8. [IPC Integration](#ipc-integration)
9. [Project Structure](#project-structure)
10. [Installation](#installation)
11. [Usage](#usage)
12. [Running Tests](#running-tests)
13. [Roadmap](#roadmap)
14. [Contributing](#contributing)
15. [License](#license)

---

## What's New

### 2026-03-20 — v2.0.10: build.bat qtpy Fix, Walkmesh Overlay Fix, slem_ar Scenario

| Change | Detail |
|--------|--------|
| **🔴 CRITICAL: build.bat now installs `qtpy`** | `qtpy` is the Qt compatibility shim imported by every GUI file in GModular. Previous versions of `build.bat` (v2.0.8/v2.0.9) never installed it — launching `GModular.exe` or `python main.py` immediately crashed with `ModuleNotFoundError: No module named 'qtpy'`. Fixed: Step 5 now installs `"PyQt5>=5.15.0,<6.0" "qtpy>=2.4.0"` together. `typing_extensions>=4.0` added to Step 6. Version string updated to v2.0.10. |
| **Walkmesh overlay critical fix** | `main_window._auto_load_walkmesh_from_dir()`: removed incorrect `.parse()` call on `WOKParser.from_file()` result (it already returns a `WalkMesh` — no parser object returned). Fixed `face.is_walkable` → `face.walkable`. The walkmesh overlay was silently broken in every prior version. |
| **`slem_ar.mod` scenario: 11/12 PASS** | Full headless pipeline test: `ModuleIO().load_from_mod()` → ARE/IFO/GIT/LYT inspect → WOK parse (32 faces, 28 walkable) → edit 4 non-walkable faces (SURF_GRASS) → `WOKWriter.to_bytes()` (4,744 bytes) → `GITCreature` add → `ERFWriter` repack (5,934 bytes). All backend steps pass. One partial: `save_are()` does not yet exist in `gff_writer.py`. |
| **API correctness audit** | Documented correct API: `ModuleIO()` is an instance method (not static); `ERFWriter.add_resource(resref, ext, data)` not `(name, data)`; `ERFWriter.to_file(path)` not `.write()`; `SURF_GRASS` constant not `SurfaceMaterial.GRASS`; `ModuleLoadResult.lyt_text` not `.lyt`; MCP via `get_all_tools()` not `MCPServer()`. All documented in ROADMAP.md. |
| **ROADMAP.md updated** | Scenario results table added; build.bat qtpy fix marked DONE; `save_are()` added as Priority #1 for next sprint; version targets updated to reflect 2,378 tests. |
| **2,378 tests, 0 failures** | Test discovery change from v2.0.9: `slem_ar.mod` scenario .mod files correctly excluded from collection. |

### 2026-03 — v2.0.9: Ghostworks IPC Bridge, End-to-End Pipeline, 103 MCP Tools, 2,552 Tests

| Change | Detail |
|--------|--------|
| **`ghostworks_bridge.py`** | Pure-`urllib` HTTP bridge (`urllib`-only, zero Qt/requests deps) exposing `ghostrigger_ping`, `ghostrigger_open_blueprint`, `ghostrigger_get_blueprint`, `ghostrigger_set_field`, `ghostrigger_save_blueprint`, `ghostrigger_list_blueprints`, `ghostscripter_ping`, `ghostscripter_open_script`, `ghostscripter_get_script`, `ghostscripter_compile`, `ghostscripter_list_scripts`. |
| **`gmodular/mcp/tools/ghostworks.py`** | 12 new MCP tools (`ghostrigger_ping`, `ghostrigger_open_blueprint`, `ghostrigger_get_blueprint`, `ghostrigger_set_field`, `ghostrigger_set_fields_bulk`, `ghostrigger_save_blueprint`, `ghostrigger_list_blueprints`, `ghostscripter_ping`, `ghostscripter_open_script`, `ghostscripter_get_script`, `ghostscripter_compile`, `ghostscripter_list_scripts`). All gracefully return `{"error": …}` when the companion app is offline — no crash. |
| **103 MCP tools total** | Up from 91 (+12 Ghostworks IPC tools). Now covers all three Ghostworks programs. |
| **End-to-end pipeline test** | `tests/test_ghostworks_pipeline_e2e.py` — 25 tests: full GhostRigger+GhostScripter lifecycle (start live servers, open blueprints, field set/get, save, compile scripts, offline-error handling) plus MCP dispatcher integration. |
| **`tests/test_ghostworks_bridge.py`** | 52 tests covering bridge API surface, live GhostRigger+GhostScripter IPC round-trips, MCP handler dispatch. |
| **NWScript token library** | `ghostscripter/ghostscripter/gui/nwscript_tokens.py` — complete KotOR 1 & 2 NWScript token database (keywords, stdlib, game-specific constants) used by `NWScriptHighlighter` and the new `FunctionBrowserPanel`. |
| **`FunctionBrowserPanel`** | Full NWScript function browser Qt widget: category tree (Action, Engine, Script, Constant), signature label, docstring pane, clipboard-copy. Wired into GhostScripter `MainWindow`. |
| **GhostRigger `BlueprintFieldEditor`** | Qt form widget rendering UTC/UTP/UTD fields (Name, Tag, Resref, HP, Faction, Appearance, etc.) with IPC `set_field`/`get_field` handlers and a "Save" button. |
| **Event-loop robustness** | `_run_async` helpers in all async test helpers use `asyncio.run()` fallback so tests are safe regardless of pytest-asyncio loop state. |
| **Tool-count guards updated** | All historical tool-count guard tests (`test_total_76`, `test_total_tool_count_85`, `test_total_tool_count_88`, `test_total_tool_count_91`) updated to 103. |
| **2,552 tests, 0 failures** | +168 new tests vs v2.0.8 (2,384 → 2,552). |

### 2026-03 — v2.0.8: GhostRigger + GhostScripter Stubs, .ui Migration, Dangly/Emitter Write-back

| Change | Detail |
|--------|--------|
| **GhostScripter `main_window.py`** | Full NWScript IDE Qt window: syntax-highlighted editor (`NWScriptHighlighter`), compile output panel, script registry sidebar, IPC status indicator. Matches Ghostworks dark-theme contract. |
| **GhostScripter test suite** | `ghostscripter/tests/test_ghostscripter.py` — 54 tests covering script dataclass, registry thread-safety, NWScriptCompiler stub, live IPC server round-trips, headless window construction. |
| **GhostRigger IPC test fix** | `ghostrigger/tests/test_ghostrigger.py` — all handlers now properly registered in `setUpClass`; 29 tests, 0 failures. |
| **Qt `.ui` migration (Phase 2)** | `InspectorPanel`, `TwoDAEditorPanel`, and `DLGEditorPanel` each attempt `load_ui()` at startup (`self._ui_loaded` flag); Python layout remains as complete fallback. `tests/test_ui_migration.py` — 34 tests covering API surface, XML validity of all 4 `.ui` files, `_ui_loaded` attribute presence. |
| **Dangly constraint-weight write-back** | `MDLWriter` now writes actual `node.constraint_weights` per-vertex; defaults to `1.0` for missing entries. |
| **Emitter node header + controllers** | Full 208-byte KotOR emitter header written (`update_type`, `render_type`, `blend_type`, `texture`, etc.) plus 18 static emitter controllers (`birthrate`, `life_exp`, `velocity`, `spread`, `color_start/mid/end`, …). |
| **Emitter controller IDs** | `CTRL_EM_*` constants added to `mdl_writer.py` matching `mdl_parser.py` source. |
| **20 new dangly tests** | `TestMDLWriterDangly` — verifies flag bits, default/custom weights, `displacement`/`tightness`/`period`, partial-weight fallback. |
| **12 new emitter tests** | `TestMDLWriterEmitter` — verifies flag bit, texture/render-type strings, blast-radius float, controller values, block-size growth. |
| **build.bat v2.0.8** | GhostRigger + GhostScripter self-test steps added (Steps 11b/c); non-fatal so GModular build still succeeds if sub-projects absent. |
| **GModular.spec v2.0.8** | Viewport_renderer already in hidden imports; version bumped. |
| **2,384 tests, 0 failures** | +137 new tests vs v2.0.6 (2,247 → 2,384). |

### 2026-03 — v2.0.7: EGLRenderer Extraction, Viewport Refactor Complete

| Change | Detail |
|--------|--------|
| **`viewport_renderer.py`** | `_EGLRenderer` class (1,507 lines) extracted from `viewport.py`. Manages ModernGL EGL context, all shader programs, FBOs, VAOs, texture caches, and render pipeline. Fully backward-compatible re-export. |
| **`viewport.py` shrunk** | From 4,295 → 2,798 lines (−35%). Imports `_EGLRenderer` from `viewport_renderer`. |
| **48 viewport refactor tests** | `tests/test_viewport_refactor.py` — `OrbitCamera`, shader dict, matrix shapes, renderer method surface. |
| **`build.bat` / `GModular.spec`** | `viewport_renderer` already in hidden imports; GhostRigger/GhostScripter stubs reflected. |
| **2,247 tests, 0 failures** | +20 vs v2.0.6. |

### 2026-03 — Deep Scan Pass 6: Complete Format Write Coverage + 2,084 Tests

| Change | Detail |
|--------|--------|
| **`kotor_write_lip` MCP tool** | Serialise a lip-sync animation JSON (duration + keyframes) back to binary `.lip` format (`LIP V1.0`). Accepts shape names (`neutral`, `ee`, `ah`, …) or integer IDs 0–15. Full round-trip verified. |
| **`kotor_write_vis` MCP tool** | Serialise a room-visibility graph JSON back to a KotOR `.vis` ASCII file. Returns the text and base64-encoded bytes. Full round-trip verified. |
| **`kotor_write_txi` MCP tool** | Serialise a TXI key-value dict back to a `.txi` ASCII file. Supports all fields: `envmaptexture`, `blending`, `fps`, `numx`, `numy`, `decal`, `clamp`, etc. Full round-trip verified. |
| **DLG `Script2` round-trip** | KotOR 2's second conditional script field (`Script2`) is now written (GFF RESREF) and read back in `to_gff_bytes` / `from_gff_bytes`. Also propagated through `to_dict` / `from_dict` for MCP tools. |
| **91 MCP tools total** | Up from 88. Every KotOR format that has a reader now has a writer exposed via MCP. |
| **49 new tests** | `tests/test_roadmap_pass6.py` — LIP/VIS/TXI round-trips, DLG Script2, format-level unit tests, tool-count guards. |
| **2,084 tests, 0 failures** | |

### 2026-03 — Deep Scan Pass 5: Resource Map Corrections + 3 Write Tools + 2,035 Tests

| Change | Detail |
|--------|--------|
| **Resource-type map corrected** | Critical fixes from canonical KotOR wiki audit: `nss=2009`, `ncs=2010` (were swapped!), `pth=3003` (not 3003 as 'res'), `lip=3004` (was missing), `rim=3002` (was 'rev'). Added `erf=9997`, `bif=9998`, `key=9999`. |
| **`kotor_write_pth` MCP tool** | Serialise a waypoint graph JSON to a binary `.pth` GFF file. Full round-trip (write → read) verified. |
| **`kotor_write_bwm` MCP tool** | Export a mesh JSON to a native BWM V1.0 binary (`.wok`/`.dwk`/`.pwk`). Supports `walkable` flag and `material` override, all three BWM subtypes. |
| **`kotor_write_lyt` MCP tool** | Serialise a room layout to a canonical `.lyt` text file (CRLF, BioWare-format). Full round-trip verified. |
| **`write_pth_to_bytes()` function** | Low-level GFF writer for PTH data, using typed `GFFField` constructors throughout. |
| **DLG AnimList GUI** | `DLGPropertiesPanel` now shows CameraStyle field (editable) and AnimList group box with add/remove entries. |
| **88 MCP tools total** | Up from 85. |
| **69 new tests** | `tests/test_roadmap_pass5.py` — resource map correctness, PTH/BWM/LYT write round-trips, DLG AnimList/CameraStyle round-trips, tool count guard. |
| **2,035 tests, 0 failures** | |

### 2026-03 — Deep Scan Pass 4: DLG Write-Back + PTH + AnimList/CameraStyle + 1,966 Tests

| Change | Detail |
|--------|--------|
| **`DLGGraphData.to_gff_bytes()`** | Full DLG GFF V3.2 serialiser: EntryList, ReplyList, StartingList, all metadata fields. Produces byte-level KotOR-compatible DLG files. Round-trip verified (parse → write → parse). |
| **`kotor_dlg_write` MCP tool** | Serialize an edited DLG graph JSON back to a native GFF binary. AI agents can now create or modify conversations end-to-end without a running game. |
| **`kotor_read_pth` MCP tool** | Parse `.pth` (path graph) GFF files. Returns all waypoints (X/Y) and their connections as JSON adjacency list. |
| **DLGNodeData extensions** | Added `camera_style` (K2 per-node camera), `anim_list` (K2 AnimList entries), `listener`, `sound` fields. All serialised/deserialised in to_gff_bytes/from_gff_bytes. |
| **85 MCP tools total** | Up from 83. |
| **33 new tests** | `tests/test_roadmap_pass4.py` — DLG round-trip, MCP handler round-trips, PTH parse, AnimList/CameraStyle fields. |
| **1,966 tests, 0 failures** | |

### 2026-03 — Deep Scan Pass 3: Resource Map Audit + 4 New MCP Tools

| Change | Detail |
|--------|--------|
| **RES_TYPE_MAP fully corrected** | Complete audit against PyKotor `ResourceType` enum (authoritative game IDs). All 70+ type IDs now match exactly: `utp=2044`, `utd=2042`, `gic=2046`, `gui=2047`, `utm=2051`, `ssf=2060`, `tpc=3007`, and more. Previously 12 IDs were off by 1–4 positions. |
| **New BTE/BTD/BTP entries** | Base template types now properly mapped: `bte=2039`, `btd=2041`, `btp=2043`, `btm=2050`. |
| **`kotor_read_lyt` MCP tool** | Parse a `.lyt` room-layout file → structured JSON (rooms, door hooks, tracks, obstacles). |
| **`kotor_read_bwm` MCP tool** | Parse a `.wok`/`.dwk`/`.pwk` walkmesh → face data, materials, adjacency, AABB summary. |
| **`kotor_resource_type_lookup` MCP tool** | Resolve extension ↔ type-ID ↔ category. Full table or single-entry lookup. |
| **`kotor_read_tpc_info` MCP tool** | Read TPC header only (128 bytes): width, height, format (DXT1/DXT5/RGB/RGBA/Greyscale), mipmap count, TXI embed. |
| **83 MCP tools total** | Up from 79. |
| **1,933 tests, 0 failures** | |

### 2026-03 — Major Overhaul (Pass 2): qtpy + Qt Designer

| Change | Detail |
|--------|--------|
| **main.py → qtpy** | Entry point now uses `qtpy` throughout — no bare `PyQt5` imports remain in runnable code. Qt6 fully launchable via `QT_API=pyqt6`. |
| **Qt Designer .ui files** | `gmodular/gui/ui/` directory with four Designer files: `inspector.ui`, `twoda_editor.ui`, `dlg_editor.ui`, `mod_import_dialog.ui`. |
| **ui_loader utility** | `gmodular.gui.ui_loader` — `load_ui()`, `load_ui_type()`, `list_ui_files()`, `ui_file()`. Graceful fallback when `.ui` is missing. |
| **Docstring hygiene** | All `"PyQt5"` references replaced with `qtpy`/`Qt` across `gui/__init__.py`, `ipc/ghidra_bridge.py`, `formats/tpc_reader.py`. |
| **GModular.spec updated** | Detects Qt backend from `QT_API`; bundles `gmodular/gui/ui/`; adds `qtpy.uic` and `gmodular.gui.ui_loader` to hidden imports. |
| **33 new tests** | `tests/test_qtdesigner_uiloading.py` — XML validity, widget presence, main.py import checks, spec bundling assertions. |

### 2026-03 — Major Overhaul (Pass 1): qtpy + DLG + NWScript

| Change | Detail |
|--------|--------|
| **qtpy migration** | All 68 raw `PyQt5` imports replaced with `qtpy` across 21 files. `pyqtSignal` → `Signal`, `pyqtSlot` → `Slot`. |
| **DLG visual editor** | Full conversation-tree node-graph editor: drag nodes, connect links, edit text/scripts/VO, import/export GFF. |
| **NWScript bridge** | Compile/decompile pipeline: nwnnsscomp → PyKotor PLY compiler → error; DeNCS → PyKotor → disasm fallback. |
| **TPC writer** | `write_tpc_from_rgba` / `write_tpc_from_tga` — export raw pixel data as native KotOR `.tpc`. |
| **2DA round-trip** | `TwoDAData.from_bytes` + binary parser mirroring Kotor.NET `TwoDABinaryReader`. |
| **2DA table editor** | Qt dockable editor: load/save binary or ASCII 2DA, undo/redo, add/remove rows & columns, CSV export. |
| **NCS decoder fixed** | RETN (0x20), SAVEBP (0x2A), RESTOREBP (0x2B), NOP (0x2D) now correctly decoded as zero-operand. |
| **LTR read/write** | `LTRData` + `read_ltr` / `write_ltr` — Markov chain name-generator (26 or 28 letters). |
| **NCS write** | `write_ncs(ncs)` — re-assembler for patching disassembled scripts. |
| **79 MCP tools** | Up from 26 at project start. |
| **1,933 tests** | 0 failures. 0 deprecation warnings. |

---

## Features

### ✅ Complete

| Feature | Detail |
|---------|--------|
| **GFF V3.2 binary reader/writer** | All 18 field types — full round-trip (GIT, ARE, IFO, DLG, UTC, UTD, UTE, UTI, UTM, UTP, UTS, UTT, UTW, JRL, FAC, GUI…) |
| **MDL/MDX parser** | KotOR 1 & 2, controller keyframes, mesh+skin data |
| **3D rendering** | ModernGL VAO pipeline, Phong lighting, wireframe, frustum culling, LRU model cache |
| **Walkmesh** | .wok AABB tree, ray-cast height/face queries, paint editor, BWM export |
| **TPC reader + writer** | DXT1, DXT5, RGBA, mip-maps, cubemaps; `write_tpc_from_rgba` |
| **2DA R/W** | Binary V2.b and ASCII V2.0; `TwoDAData.from_bytes` round-trip; Qt editor panel |
| **ERF/RIM/MOD/BIF/KEY** | Archives — read, list, extract, write |
| **SSF binary R/W** | Soundset read + write; 28-entry V1.1 format (PyKotor-verified) |
| **LIP binary R/W** | Lip-sync phoneme data read + write |
| **TXI parser** | ASCII texture metadata, all properties |
| **VIS parser/writer** | Room visibility data read + write |
| **LYT parser/writer** | Room layout read + canonical write; `kotor_write_lyt` MCP tool; structured Track/Obstacle entries |
| **NCS decoder + writer** | Full opcode table incl. RETN/SAVEBP/NOP zero-operand; disassembly text; `write_ncs` re-assembler |
| **LTR read/write** | `LTRData` Markov chain name-generator; `read_ltr` / `write_ltr`; 26 or 28-letter variants |
| **DLG visual editor** | Node-graph canvas, properties panel, GFF import/export, full write-back (`to_gff_bytes`), MCP tools |
| **DLG write-back** | `DLGGraphData.to_gff_bytes()` — full GFF V3.2 DLG serialiser; K2 AnimList + CameraStyle fields supported |
| **PTH path graph** | `kotor_read_pth` + `kotor_write_pth` MCP tools; `PTHData` + `write_pth_to_bytes()` binary serialiser |
| **NWScript bridge** | Compile NSS→NCS, decompile NCS→NSS, syntax check, auto-format |
| **GFF diff / patch** | Field-level diff + JSON patch application |
| **2DA diff** | Row/column/cell-level diff (Kotor.NET algorithm) |
| **TLK diff** | String-entry-level diff |
| **Room Assembly** | Drag-and-drop 2D layout → LYT + VIS |
| **Animation panel** | Play/stop/scrub MDL animations |
| **BWM binary exporter** | `WOKWriter` full BWM V1.0 binary; `kotor_write_bwm` MCP tool (wok/dwk/pwk) with walkable flag support |
| **MCP server** | 103 tools, SSE transport, JSON-RPC 2.0 |
| **AI agent tools** | `get_resource`, `search`, `get_creature`, `get_conversation`, `get_script`, … |
| **Ghidra integration** | Decompile/symbol search via AgentDecompile bridge |
| **IPC bridges** | GhostScripter + GhostRigger via JSON-RPC socket |
| **Mod packager** | Build `.mod` from project, manifest JSON, dependency check |
| **Qt Designer .ui files** | `inspector.ui`, `twoda_editor.ui`, `dlg_editor.ui`, `mod_import_dialog.ui` — loaded via `ui_loader.py` |
| **Resource type map** | Full 70+ type-ID ↔ extension table; corrected against canonical wiki: `nss=2009`, `ncs=2010`, `pth=3003`, `lip=3004`, `rim=3002` |

### ✅ Newly Completed (v2.0.7 / v2.0.8)

| Feature | Status |
|---------|--------|
| **MDL binary writer** | Full round-trip KotOR binary MDL; dangly constraint-weight write-back; 208-byte emitter header + 18 controllers |
| **Qt Designer .ui adoption** | All 4 panels now attempt `load_ui()` on startup (`_ui_loaded` flag); Python layout is complete fallback |
| **GhostRigger stub** | Main window, blueprint registry, IPC server (port 7001); 29 tests |
| **GhostScripter stub** | Main window with NWScript syntax highlighter, script registry, IPC server (port 7002); 54 tests |
| **Viewport renderer extracted** | `_EGLRenderer` extracted to `viewport_renderer.py` (−35 % viewport.py) |

### 🔄 In Progress

| Feature | Status |
|---------|--------|
| **Walkmesh overlay fix** | Fixed: `WOKParser.from_file()` now correctly used (no `.parse()` call); `face.walkable` instead of `face.is_walkable` |
| Viewport render loop | Geometry renders; lighting needs polish |
| GhostRigger field editor | Blueprint field editing (UTC/UTP/UTD) via IPC — wired in `BlueprintFieldEditor` widget |
| GhostScripter syntax highlighting | `NWScriptHighlighter` + full token library (`nwscript_tokens.py`) |
| GModular → GhostRigger IPC bridge | `ghostrigger_*` MCP tools live; `ghostworks_bridge.py` implemented |
| GModular → GhostScripter IPC bridge | `ghostscripter_*` MCP tools live; `ghostworks_bridge.py` implemented |
| Animation timeline scrubber | Controller data parsed; `AnimationClipSignal.emit/connect` stubs need wiring |

---

## System Architecture

```
┌──────────────────────────────────────────────────────┐
│  gmodular/gui/       Qt UI (qtpy — Qt5 or Qt6)        │
│    main_window.py    viewport.py    dlg_editor.py      │
│    inspector.py      twoda_editor.py                   │
│    ui/  ←── Qt Designer .ui files + ui_loader.py      │
└────────────────────┬─────────────────────────────────┘
                     │  uses
┌────────────────────▼─────────────────────────────────┐
│  gmodular/engine/    Game-data logic (no Qt)          │
│    module_state.py   app_controller.py               │
└────────────────────┬─────────────────────────────────┘
                     │  uses
┌────────────────────▼─────────────────────────────────┐
│  gmodular/formats/   Binary I/O (no Qt, no MCP)      │
│    archives.py       gff_reader.py    gff_writer.py   │
│    mdl_parser.py     tpc_reader.py    wok_parser.py   │
│    lyt_vis.py        kotor_formats.py twoda_loader.py │
│    tlk_reader.py     resource_port.py mod_packager.py │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  gmodular/mcp/       MCP server (no Qt)               │
│    server.py         state.py         _indexer.py     │
│    tools/  ←── 103 tool handlers across 13 modules   │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  gmodular/ipc/       IPC bridges                      │
│    ghidra_bridge.py  nwscript_bridge.py               │
└──────────────────────────────────────────────────────┘
```

**Strict dependency rules** (enforced by `tests/test_architecture.py`):
- `formats/` must not import Qt or MCP
- `mcp/` must not import `gui/` at module level
- `engine/` must not import MCP state

---

## MCP Server

103 registered tools across 13 categories:

### High-Level AI Tools (composite.py)
| Tool | Purpose |
|------|---------|
| `get_resource` | Fetch any KotOR resource by game+type+resref |
| `get_quest` | Look up journal quest by tag |
| `get_creature` | Fetch UTC with appearance, stats, inventory |
| `get_conversation` | Summarise a DLG tree |
| `get_area` | Describe an ARE (ambient track, fog, scripts) |
| `get_script` | Disassemble an NCS |
| `search` | Full-text search across all loaded resources |

### Format Library (formats.py, scripts.py, diff_tools.py)
| Tool | Purpose |
|------|---------|
| `kotor_read_gff` | GFF → JSON dict |
| `kotor_read_2da` | 2DA → JSON table |
| `kotor_read_tlk` | TLK entry lookup |
| `kotor_read_ssf` | SSF → soundset dict (28 entries, V1.1) |
| `kotor_read_lip` | LIP → phoneme list |
| `kotor_read_txi` | TXI → property dict |
| `kotor_read_vis` | VIS → visibility dict |
| `kotor_read_ncs` | NCS disassembly |
| `kotor_read_ltr` | LTR → letter-count + probabilities |
| `kotor_read_lyt` | LYT → room layout JSON (rooms, hooks, tracks) |
| `kotor_read_bwm` | WOK/DWK/PWK walkmesh → face data JSON |
| `kotor_read_tpc_info` | TPC header → width/height/format/mipmaps |
| `kotor_read_pth` | PTH path graph → waypoints + connections JSON **NEW** |
| `kotor_resource_type_lookup` | Extension ↔ type-ID ↔ category lookup |
| `kotor_write_ssf` | Build SSF from dict |
| `kotor_write_2da_csv` | Export 2DA as CSV |
| `kotor_write_tlk_patch` | Generate TLK patch |
| `kotor_write_ltr` | Build LTR from probability arrays |
| `kotor_write_ncs` | Re-assemble NCS from instruction list |
| `kotor_disasm_script` | NCS → annotated disassembly |
| `kotor_compile_script` | NSS → NCS (external compiler) |
| `kotor_decompile_script` | NCS → NSS (best-effort) |
| `kotor_ncs_info` | NCS opcode histogram + stats |
| `kotor_gff_diff` | Field-level GFF comparison |
| `kotor_2da_diff` | Row/cell-level 2DA comparison |
| `kotor_tlk_diff` | String-entry TLK comparison |
| `kotor_patch_gff` | Apply JSON patch to GFF |
| `kotor_describe_ssf` | Human-readable soundset description |

### DLG Conversation Tools
| Tool | Purpose |
|------|---------|
| `kotor_dlg_parse` | DLG GFF → graph JSON |
| `kotor_dlg_add_node` | Add Entry/Reply node |
| `kotor_dlg_link_nodes` | Connect two nodes with optional condition |
| `kotor_dlg_summarize` | Human-readable tree walkthrough |
| `kotor_dlg_write` | Graph JSON → DLG GFF binary (write-back) **NEW** |

### NWScript Tools
| Tool | Purpose |
|------|---------|
| `kotor_compile_nss` | NSS source → NCS bytes |
| `kotor_decompile_ncs` | NCS bytes → NSS source |
| `kotor_nss_check` | Syntax validation |
| `kotor_nss_format` | Auto-indent formatter |

*(Plus installation, discovery, gamedata, archives, modules, refs, walkmesh, animation, Ghidra tools — 83 total)*

---

## Format Support

| Format | Ext | Read | Write | Notes |
|--------|-----|------|-------|-------|
| GFF | .dlg .utc .git .are .ifo … | ✅ | ✅ | All 18 field types; CExoLocString multi-lang aware |
| 2DA | .2da | ✅ | ✅ | Binary V2.b + ASCII V2.0 |
| TLK | .tlk | ✅ | ✅ | |
| MDL/MDX | .mdl .mdx | ✅ | — | KotOR 1 & 2; binary writer planned (Phase 3) |
| TPC | .tpc | ✅ | ✅ | DXT1/DXT5/RGBA/Greyscale; mip-maps; `write_tpc_from_rgba` |
| TGA | .tga | ✅ | — | |
| TXI | .txi | ✅ | ✅ | ASCII line-value pairs |
| ERF/MOD/SAV | .erf .mod .sav | ✅ | ✅ | V1.0 and V1.1 |
| RIM | .rim | ✅ | ✅ | |
| BIF | .bif | ✅ | — | |
| KEY | .key | ✅ | — | |
| SSF | .ssf | ✅ | ✅ | 28 sounds; V1.1; verified against PyKotor |
| LIP | .lip | ✅ | ✅ | Phoneme keyframes |
| LYT | .lyt | ✅ | ✅ | Structured rooms, tracks, obstacles, door hooks |
| VIS | .vis | ✅ | ✅ | Room visibility pairs |
| WOK/DWK/PWK | .wok .dwk .pwk | ✅ | ✅ | Walkmesh AABB; MCP tool NEW |
| LTR | .ltr | ✅ | ✅ | Markov name-generator; 26 or 28 letters |
| NCS | .ncs | ✅ | ✅ | Disassemble + re-assemble |
| NSS | .nss | ✅ | ✅ | Via NWScript bridge |
| PTH | .pth | ✅ | ✅ | Pathfinding network GFF |
| DLG | .dlg | ✅ | ✅ | Full node-graph; visual editor |
| JRL | .jrl | ✅ | ✅ | Journal |

**Resource type coverage**: 70+ type IDs fully mapped (see `gmodular/formats/archives.py → RES_TYPE_MAP`), verified against PyKotor `ResourceType` enum and Kotor.NET `KotorResourceType`.

---

## Qt Compatibility

GModular uses **[qtpy](https://github.com/spyder-ide/qtpy)** as a shim layer, so the same codebase works with either PyQt5 or PyQt6.

```python
# All GUI code uses qtpy — never bare PyQt5:
from qtpy.QtWidgets import QWidget, QVBoxLayout
from qtpy.QtCore    import Qt, Signal, Slot
from qtpy.QtGui     import QColor, QPixmap
```

### Backend Selection

| Backend | How to activate |
|---------|----------------|
| PyQt5 (default) | `pip install PyQt5` — auto-detected |
| PyQt6 | `pip install PyQt6 && QT_API=pyqt6 gmodular-gui` |
| PySide2 | `pip install PySide2 && QT_API=pyside2 gmodular-gui` |
| PySide6 | `pip install PySide6 && QT_API=pyside6 gmodular-gui` |

### Why qtpy instead of direct PyQt5?

The honest assessment of the qtpy suggestion from code review:

**Pros (significant):**
- Zero code changes to migrate from Qt5→Qt6 (just swap the env var)
- `Signal`/`Slot` naming works across all four backends
- PyInstaller spec already detects backend from `QT_API`
- Future-proofs against PyQt5's end-of-life (Python 3.13+)

**Cons (minor):**
- One extra import resolution per Qt symbol (negligible overhead)
- qtpy version mismatches can cause subtle API differences — pin `qtpy>=2.4.0`
- Slightly harder to grep for "what Qt classes does this file use?"

**Verdict:** The migration is complete and correct. The qtpy layer is the right long-term choice for a tool that needs to stay installable on Python 3.13+ where PyQt5 wheels may not be available.

---

## Qt Designer .ui Files

UI layout is separated from logic via `.ui` files in `gmodular/gui/ui/`:

### Files

| File | Panel |
|------|-------|
| `inspector.ui` | Inspector / properties panel |
| `twoda_editor.ui` | 2DA table editor |
| `dlg_editor.ui` | DLG conversation-tree editor |
| `mod_import_dialog.ui` | Module import dialog |

### Usage

```python
from gmodular.gui.ui_loader import load_ui, load_ui_type

# Runtime load (no Python class needed):
widget = load_ui("inspector.ui", parent=self)
widget.nameLabel.setText("Darth Revan")

# Compile-time type (IDE completion):
InspectorBase = load_ui_type("inspector.ui")
class InspectorPanel(InspectorBase):
    ...
```

### Adding a New Panel

1. Design in Qt Designer → save as `gmodular/gui/ui/mypanel.ui`
2. Load with `load_ui("mypanel.ui", parent=self)`
3. Add a test in `tests/test_qtdesigner_uiloading.py`
4. `GModular.spec` auto-bundles everything in `gmodular/gui/ui/`

---

## IPC Integration

### NWScript Bridge

```
GModular ──► NWSScriptBridge ──► nwnnsscomp.exe  (compile)
                              ──► PyKotor PLY     (fallback)
                              ──► DeNCS           (decompile)
                              ──► PyKotor decompiler (fallback)
```

### DLG Editor (Python API)

```python
from gmodular.gui.dlg_editor import DLGEditorPanel, DLGData

dlg = DLGData.from_gff_dict(gff_dict)
panel = DLGEditorPanel()
panel.load_dlg(dlg)
panel.show()
```

### GhostScripter / GhostRigger

Port contract (fixed, not configurable):

| Service | Port |
|---------|------|
| GhostRigger | 7001 |
| GhostScripter | 7002 |
| GModular | 7003 |
| AgentDecompile | 8080 (remote) |

---

## Project Structure

```
gmodular/
├── formats/           Binary I/O — no Qt, no MCP imports
│   ├── archives.py    KEY/BIF, ERF/MOD/RIM reader+writer; RES_TYPE_MAP (70+ IDs)
│   ├── gff_reader.py  GFF binary reader (all 18 types)
│   ├── gff_writer.py  GFF binary writer + IFO/GIT/ARE/UTx writers
│   ├── gff_types.py   GFF field type enum + dataclasses
│   ├── mdl_parser.py  MDL/MDX KotOR 1 & 2 parser
│   ├── tpc_reader.py  TPC reader + DXT decoder + qtpy QImage bridge
│   ├── wok_parser.py  WOK/BWM walkmesh reader + AABB builder
│   ├── lyt_vis.py     LYT/VIS room layout parser/writer; structured Track/Obstacle
│   ├── kotor_formats.py  SSF, LIP, TXI, VIS, PTH, LTR, NCS, 2DA, TLK, TPC
│   ├── tlk_reader.py  TLK reader
│   └── twoda_loader.py  2DA binary+ASCII parser
│
├── gui/               Qt widgets — uses qtpy, never bare PyQt5
│   ├── ui/            Qt Designer .ui files
│   │   ├── inspector.ui
│   │   ├── twoda_editor.ui
│   │   ├── dlg_editor.ui
│   │   └── mod_import_dialog.ui
│   ├── ui_loader.py   load_ui() / load_ui_type() / list_ui_files()
│   ├── main_window.py
│   ├── viewport.py
│   ├── inspector.py
│   ├── dlg_editor.py
│   ├── twoda_editor.py
│   └── ...
│
├── engine/            Game-data logic — no Qt, no MCP
│   ├── module_state.py
│   └── app_controller.py
│
├── mcp/               MCP server — no Qt
│   ├── server.py
│   ├── state.py
│   ├── _indexer.py
│   └── tools/         103 tool handlers
│       ├── composite.py    High-level AI tools
│       ├── formats.py      SSF/LIP/TXI/VIS/LYT/BWM/TPC info/LTR/NCS
│       ├── scripts.py      NCS/NSS compile/decompile
│       ├── diff_tools.py   GFF/2DA/TLK diff + patch
│       ├── walkmesh.py     Walkmesh info + validation
│       ├── archives.py     ERF/KEY list+extract
│       ├── modules.py      Module describe/list
│       ├── discovery.py    Resource search
│       ├── gamedata.py     2DA/TLK lookup
│       ├── installation.py Installation detection
│       ├── refs.py         Cross-reference tools
│       ├── animation.py    Animation control
│       └── agentdecompile.py  Ghidra bridge
│
└── ipc/               IPC bridges
    ├── ghidra_bridge.py
    └── nwscript_bridge.py
```

---

## Installation

```bash
git clone https://github.com/YOUR_REPO/gmodular
cd gmodular
pip install -e ".[dev]"
```

**Dependencies** (auto-installed):

| Package | Version | Purpose |
|---------|---------|---------|
| qtpy | ≥2.4.0 | Qt abstraction layer |
| PyQt5 | ≥5.15.0 | Default Qt backend |
| moderngl | ≥5.8.0 | OpenGL rendering |
| numpy | ≥1.21.0 | Array math |
| watchdog | ≥2.0.0 | File-change detection |
| requests | ≥2.28.0 | HTTP (AgentDecompile) |

### Optional: Qt6

```bash
pip install PyQt6
QT_API=pyqt6 gmodular-gui
```

### Optional: NWScript compiler

Download `nwnnsscomp` from [nwntools.com](https://nwntools.com) and put it on `PATH`. The bridge falls back to PyKotor's PLY compiler if the external tool is missing.

---

## Usage

### GUI Quick Start

```bash
# Launch the full GUI
gmodular-gui

# Open a specific .git file
gmodular-gui --open /path/to/mymod.git

# Load a project
gmodular-gui --project /path/to/mymod.json

# Point at a KotOR installation
gmodular-gui --game-dir "C:/Program Files/KOTOR"
```

### MCP Server

```bash
# Start MCP server (SSE transport on port 7003)
gmodular --mcp

# In your AI agent / Claude Desktop config:
{
  "mcpServers": {
    "gmodular": {
      "url": "http://localhost:7003/sse"
    }
  }
}
```

**Example agent queries:**
```
"List all creatures in module_k1_001."
"What does the script k_con_dialog1 do?"
"Show me the 2DA row for creature appearance 183."
"Read the LYT for module danm13 and show room positions."
"Look up resource type ID 2044."
```

---

## Running Tests

```bash
# All tests (2,084 — must be 0 failures)
pytest

# With verbose output
pytest -v

# Specific test file
pytest tests/test_kotor_formats.py -v

# Architecture boundary checks
pytest tests/test_architecture.py -v

# Qt Designer UI tests (headless)
pytest tests/test_qtdesigner_uiloading.py -v
```

**Test coverage categories:**

| File | What it tests |
|------|--------------|
| `test_kotor_formats.py` | SSF, LIP, TXI, VIS, PTH, NCS, LTR, 2DA, TLK, MCP tools |
| `test_archives.py` | KEY/BIF, ERF/MOD/RIM, resource type map (70+ IDs) |
| `test_mcp.py` | All 85 MCP tool schemas, indexer, installation |
| `test_gff.py` | GFF binary round-trip (all 18 field types) |
| `test_mdl.py` | MDL/MDX parser |
| `test_wok.py` | WOK walkmesh + AABB |
| `test_lyt_vis.py` | LYT/VIS parser/writer |
| `test_architecture.py` | Dependency boundary enforcement |
| `test_qtdesigner_uiloading.py` | Qt Designer XML validity + ui_loader |
| `test_roadmap_*.py` | Feature-level integration tests |
| `test_module_state.py` | ModuleState loading/caching |

---

## Roadmap

> **See [ROADMAP.md](ROADMAP.md) for the full detailed roadmap** generated from a complete source audit,
> `slem_ar.mod` scenario testing, and cross-reference with all OldRepublicDevs repos.

### Phase 1 — Core Formats & Rendering ✅

- [x] GFF, 2DA, TLK, MDL, TPC, ERF/RIM/BIF/KEY parsers
- [x] NCS decoder + disassembler + **writer** (re-assembler)
- [x] **LTR read/write** (Markov name-generator, 26/28 letters)
- [x] WOK/BWM walkmesh read/write + AABB tree
- [x] 3D rendering (ModernGL)
- [x] Walkmesh AABB tree + editor
- [x] MCP server with **103 tools**
- [x] DLG visual node-graph editor
- [x] NWScript compile/decompile bridge
- [x] TPC writer (`write_tpc_from_rgba`)
- [x] 2DA binary round-trip
- [x] **qtpy compatibility layer** (Qt5 + Qt6, 21 files migrated, `main.py` fixed)
- [x] **Qt Designer .ui files** (`inspector.ui`, `twoda_editor.ui`, `dlg_editor.ui`, `mod_import_dialog.ui`) + `ui_loader.py`
- [x] **RES_TYPE_MAP audit** — 70+ IDs verified against PyKotor `ResourceType` enum
- [x] **New MCP tools**: `kotor_read_lyt`, `kotor_read_bwm`, `kotor_resource_type_lookup`, `kotor_read_tpc_info`
- [x] 2,084 tests, 0 failures
- [x] **DLG write-back** (`to_gff_bytes`): full GFF V3.2 DLG serialiser
- [x] **`kotor_dlg_write` MCP tool**: AI agents can create/edit conversations end-to-end
- [x] **`kotor_read_pth` MCP tool**: parse path graphs (AI waypoints)
- [x] **K2 DLG extensions**: `camera_style`, `anim_list` per-node fields
- [x] **MDL binary writer** (`mdl_writer.py`) — complete round-trip, skinned mesh, AABB tree, dangly+emitter nodes
- [x] **Dangly constraint-weight write-back** — actual `node.constraint_weights` written per-vertex
- [x] **Emitter node header + 18 controllers** — full 208-byte KotOR emitter block
- [x] **Animation seek API** — `seek()`, `get_duration()`, `get_elapsed()` on `AnimationPlayer`
- [x] **2,552 tests, 0 failures** (as of v2.0.9)
- [x] **Walkmesh overlay fix** — `WOKParser.from_file()` + `face.walkable` (v2.0.10)

### Phase 2 — Connected Workflow 🔄

- [x] **DLG GFF write-back** (`DLGGraphData.to_gff_bytes()`) — full round-trip serialiser
- [x] **`kotor_dlg_write` MCP tool** — AI agents can create/edit DLG conversations programmatically
- [x] **K2 DLG extensions** — `camera_style`, `anim_list`, `listener`, `sound` per-node fields
- [x] **`kotor_read_pth` MCP tool** — parse path graph GFF (AI waypoints and connections)
- [x] **Migrate panels to `load_ui()`** — `InspectorPanel`, `TwoDAEditorPanel`, `DLGEditorPanel` wired; `_ui_loaded` flag; 34 tests
- [x] **`ModuleIO` service** — ERF-loading logic extracted from `ModuleState` (v2.0.5)
- [x] **GhostRigger stub** — `ghostrigger/` package: IPC server (port 7001), BlueprintRegistry, MainWindow; 29 tests
- [x] **GhostScripter stub** — `ghostscripter/` package: IPC server (port 7002), NWScriptCompiler, NWScriptHighlighter, MainWindow; 54 tests
- [x] **`viewport_renderer.py`** — `_EGLRenderer` extracted from `viewport.py` (−1,497 lines)
- [x] **Ghostworks IPC bridge** — `ghostworks_bridge.py` + 12 GhostWorks MCP tools (103 total)
- [x] **FunctionBrowserPanel** (GhostScripter) — full NWScript stdlib browser
- [x] **BlueprintFieldEditor** (GhostRigger) — UTC/UTP/UTD field editor widget
- [ ] **EventBus + constructor injection** — decouple viewport from `module_state` singleton
- [ ] **Animation timeline playhead** — connect `AnimationClipSignal.emit/connect` stubs → `viewport.step_to_frame()`
- [ ] DLG editor: integration test with real KotOR 1 `.dlg` file

### Phase 3 — Level Assembly & Viewer 🗺️

- [ ] **MDL → GPU mesh** — parse MDL/MDX geometry → OpenGL VBO → textured render
- [ ] **TPC/TGA texture loader** — DXT1/DXT3/DXT5 decompression; bind to MDL material slots
- [ ] **Multi-room rendering from LYT** — position each room at `world_x/y/z` from LYT
- [ ] **Face-click walkmesh editing** — click face in viewport → paint material
- [ ] **One-click repack to .mod** — after WOK edit → repack all resources into new .mod
- [ ] Module instance editor (creatures, doors, triggers from GIT)
- [ ] Room connections (LYT door-hook snapping; vertex-paint for room transitions)
- [ ] Trigger/waypoint placement
- [ ] Patrol path editor (place nodes → generate PTH GFF)
- [ ] **DanglymeshHeader / SkinmeshHeader** full decode (partially parsed in `mdl_parser.py`)
- [ ] Texture atlas builder for TPC/DDS → MDL material assignment
- [ ] **Minimap generation** — top-down render → `lbl_mapXXXX.tga` (KotorBlender method)

### Phase 4 — Game Installation Integration 🎮

- [ ] **KotOR install detector** — Windows registry + Steam + common paths + env vars (mirror PyKotor `game_detector.py`)
- [ ] **BIF resource browser** — `chitin.key` → open any resource by ResRef
- [ ] **Override folder integration** — show Override/ files in content browser with priority flag
- [ ] **"Load from game" button** — select ResRef → extract from BIF/MOD/Override
- [ ] **Module list from Modules/** — list all .mod/.rim files from game directory
- [ ] **TLK browser** — load `dialog.tlk`; resolve StrRefs in ARE/GIT/DLG fields
- [ ] **2DA table lookup** — browse `appearance.2da`, `baseitems.2da`, `feat.2da` with filter

### Phase 5 — Quality & Distribution 📦

- [ ] **PyInstaller single-file bundle** — `build.bat → GModular.exe` (no Python required)
- [ ] **GitHub Actions CI** — run tests on push; build EXE on tag
- [ ] **Reduce silent swallows** — fix 114 `except: pass` blocks → log + user toast
- [ ] **Test coverage audit** — add assertions to 35% pass-only tests
- [ ] **User documentation** — getting-started: open .mod, fix walkmesh, save
- [ ] **Deadly Stream release thread** — post to KotOR modding community forum

---

## Research Notes (from Deep Scan)

Cross-referenced against **Kotor.NET** and **PyKotor** during development:

### Resource Type IDs
- PyKotor `ResourceType` enum is the authoritative source for game-format type IDs
- IDs in the 2039–2066 range have subtle ordering: BTE(2039)→UTE(2040)→BTD(2041)→UTD(2042)→BTP(2043)→UTP(2044)→DFT(2045)→GIC(2046)→GUI(2047)
- `tpc` is at **3007** (high range), not 2056 as sometimes listed
- `mdx` is at **3008** (high range), separate from WLK (2055)

### SSF Format
- KotOR 1 & 2 both use **28 sounds** in a fixed table (V1.1, `SSF `)
- Kotor.NET defines a 40-slot table for NWN2 compatibility — irrelevant for KotOR

### LYT Format
- Kotor.NET `LYTReader.cs` throws `NotImplementedException` — our Python implementation is ahead
- PyKotor `LYTAsciiReader` uses `Vector3` for positions, `Vector4` for door-hook orientation quaternion
- Track/Obstacle entries have a `model` string field (not just raw strings)

### BWM/WOK Format
- PyKotor: `BWMFace` has `material`, vertices list, `transition` index per edge
- AABB tree built by `CalculateAABBs()` with recursive split-axis selection (longest axis)
- `CalculateEdges()` is still TODO in Kotor.NET

### GFF CExoLocString
- Binary layout: `total_size(4)` + `strref(4)` + `count(4)` + N × `[string_id(4) + length(4) + text(length)]`
- `string_id = language_id * 2 + gender_id`
- Windows-1252 encoding (Kotor.NET uses `Encoding.GetEncoding(1252)`) — not UTF-8
- Our reader currently returns English only (lang_id=0); multi-language support is Phase 2

### TPC Format
- Header: `compressed_size(4)` + `??(4)` + `width(2)` + `height(2)` + `encoding(1)` + `mipmap_count(1)` + `reserved(114)`
- `compressed_size != 0` → DXT; encoding 2=DXT1, 4=DXT5; encoding 1/2/4 for uncompressed Greyscale/RGB/RGBA
- Mipmap sizes: DXT1 = max(8, ceil(w/4)×ceil(h/4)×8), DXT5 = max(16, ceil(w/4)×ceil(h/4)×16)

---

## Contributing

1. Fork, create a feature branch from `main`
2. Run `pytest` — must be 0 failures before opening PR
3. Follow the architecture rules (see `ARCHITECTURE.md`):
   - `mcp/` must not import `gui/` at module level
   - `formats/` must not import Qt
   - All Qt code uses `qtpy` imports, never bare `PyQt5`
4. Add tests for any new format code or `.ui` files
5. Keep the `RES_TYPE_MAP` in `gmodular/formats/archives.py` in sync with PyKotor `ResourceType`
6. Open PR against `main` with a description of changes

---

## License

MIT — see [LICENSE](LICENSE)

---

*Built with ❤️ for the KotOR modding community. Inspired by PyKotor, Kotor.NET, HolocronToolset, and TSLPatcher.*
