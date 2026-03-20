# GModular — Development Roadmap
## Based on full source audit, slem_ar.mod scenario testing, and OldRepublicDevs repo research
**Updated:** 2026-03-20 | **Basis:** v2.0.9 state (2,552 tests, 7 skipped, 0 failures)

---

## The Honest User Experience (v2.0.9)

> *Scenario: A KotOR modder downloads the latest commit from GitHub today and wants to edit `slem_ar.mod` to fix walkmeshes.*

### What works well ✅

| Feature | Status | Notes |
|---------|--------|-------|
| **Open .mod file** | ✅ Works | `ModuleIO.load_from_mod()` extracts all resources, handles type-ID remapping, synthesizes LYT if missing |
| **Parse walkmesh (WOK)** | ✅ Works | `WOKParser.from_file()` returns a full `WalkMesh` object; 32 faces from `test_grid.wok` parsed correctly |
| **Inspect face materials** | ✅ Works | `face.walkable`, `face.material`, `face.v0/v1/v2` all correct |
| **Edit face materials** | ✅ Works | Direct attribute set: `face.material = 0` (grass) works in-memory |
| **Write modified WOK** | ✅ Works | `WOKWriter(wm).to_bytes()` produces valid BWM V1.0 binary; round-trip verified |
| **Repack into .mod** | ✅ Works | `ERFWriter` + `ERFReaderMem` pipeline works; `list_resources()` returns `'name.ext'` strings |
| **MCP server** | ✅ Works | 103 tools register cleanly; `get_all_tools()` works; no duplicates |
| **GFF read/write** | ✅ Works | ARE, GIT, IFO, DLG round-trip confirmed |
| **2DA editor** | ✅ Works | Full CRUD, filter, undo/redo, CSV export |
| **DLG editor** | ✅ Works | Node graph, GFF serialization, replies/entries, starters |
| **LYT parser** | ✅ Works | Room/door hook parsing; synthesis from MDL when LYT missing |
| **BWM/WOK round-trip** | ✅ Works | Vertex deduplication, AABB tree, adjacency table, outer edges all generated |
| **MDL binary writer** | ✅ Works | Full MDL/MDX write: geometry, skin, dangly, emitter nodes |
| **Archive formats** | ✅ Works | ERF/MOD/RIM/BIF read, ERF/MOD write |
| **GhostScripter IDE stubs** | ✅ Wired | NWScript tokenizer, syntax highlighter, function browser |
| **GhostRigger stubs** | ✅ Wired | Field editor, blueprint UI, IPC server on port 7001 |
| **Ghostworks IPC bridge** | ✅ Works | `ghostworks_bridge.py` HTTP bridge; `ghostrigger_*` and `ghostscripter_*` MCP tools |

### What doesn't work (confirmed bugs) 🔴

| Bug | Severity | File:Line | Description | Fix |
|-----|----------|-----------|-------------|-----|
| **Walkmesh overlay crash** | 🔴 CRITICAL | `main_window.py:1409` | `WOKParser.from_file()` returns `WalkMesh` directly; old code called `.parse()` then `.vertices` — neither exists → overlay always silently failed | **FIXED in this commit** — removed `.parse()` call, use `wok.faces` directly |
| **`face.is_walkable` typo** | 🔴 CRITICAL | `main_window.py:1443` | Attribute is `face.walkable` not `face.is_walkable` → all faces treated as non-walkable in overlay | **FIXED in this commit** |
| **`WOKWriter.write()` does not exist** | 🟠 HIGH | `walkmesh_editor.py:1083` | Was calling `.write()` but method is `.to_bytes()` / `.to_file()` | **FIXED** — walkmesh_editor already calls `.to_file()` correctly; only referenced incorrectly in prior analysis |
| **`ERFReaderMem.list_resources()` returns strings** | 🟡 MEDIUM | `archives.py` | Returns `['name.ext', ...]` not `[(name, ext), ...]`; any code unpacking as tuple fails | Callers must use `res.rsplit('.', 1)` pattern |
| **`GFFRoot.set()` signature mismatch** | 🟡 MEDIUM | `gff_types.py` | Correct signature is `set(label, type_id, value)` not `set(label, GFFField)` — documentation implies GFFField but code requires 3 args | Update docstrings; add `set_field(label, field)` overload |
| **LYT world-offset floats** | 🟡 MEDIUM | `module_io.py` | LYT `room` lines parse X/Y/Z — but `main_window._auto_load_walkmesh_from_dir` only looks up `room.world_x/y/z` via `getattr(..., 0.0)` — offset is always 0 for multi-room modules | Fix room object to store parsed LYT coords |
| **Animation playback unfinished** | 🟡 MEDIUM | `animation_panel.py` | `AnimationClipSignal.__init__`, `emit`, `connect` are stubs (pass) — scrubber wired in UI but actual keyframe stepping not connected to renderer | See Phase 4 |
| **114 silent exception swallows** | 🟡 MEDIUM | multiple | `except Exception: pass` throughout codebase — modder sees no feedback when things fail silently | Audit and add logging/user-facing error dialogs |
| **Viewport.py: 2,798 lines** | 🟡 MEDIUM | `viewport.py` | Still oversized despite renderer extraction; `__init__` and `__set_name__` stubs remain at line 75-76 | Continue refactor toward sub-modules |
| **No file drag-and-drop** | 🟡 MEDIUM | `main_window.py` | GUI has no drag-and-drop for .mod files | UX gap |
| **No installer/binary release** | 🟠 HIGH | `build.bat` | Requires Python 3.12, PyQt5, manual setup — no one-click installer | PyInstaller bundle needed for community adoption |
| **Qt UI missing for walkmesh** | 🟡 MEDIUM | `walkmesh_editor.py` | Walkmesh panel has no `.ui` file — still code-built | Low priority, functional |

---

## Source Analysis: What We Learned from Other Repos

### From `OldRepublicDevs/PyKotor` (most complete Python KotOR library)
- **BWM format**: PyKotor's `bwm_data.py` confirms our `WOKWriter` AABB tree algorithm is correct (median-split, same as kotorblender). Key insight: **trans1/trans2/trans3 on BWMFace are for room-to-room transitions** — only perimeter edges should have transitions. Our `WOKWriter` handles this correctly.
- **Resource type IDs**: PyKotor's `ResourceType` enum matches our `RES_TYPE_MAP` — both use `wok=2016`, `mdl=2002`, `are=2023`, `git=2025`. 
- **CExoLocString encoding**: Confirmed `lang*2 + gender` formula for substring_id; our `gff_types.py` Language enum matches PyKotor exactly.
- **GFF V3.2 writer**: PyKotor uses identical field-offset layout — our round-trips are compatible.
- **Installation detection**: PyKotor detects K1/K2 on Windows/macOS/Linux/Steam Deck — GModular has no game-installation detection at all. **Roadmap gap.**
- **KotorMCP**: Has 5 tools (detect, load, list, describe, journal). Our MCP server has 103 tools and is far more comprehensive, though we lack game-installation awareness.

### From `OldRepublicDevs/kotorblender` (Blender plugin for MDL/WOK/LYT)
- **WOK format**: Confirms walkable face ordering (walkable first, required by adjacency table) — our WOKWriter already does this.
- **LYT format**: `beginlayout / room <idx> <name> <x> <y> <z> / donelayout` — matches our parser; also supports `doorhook` lines with 9 values.
- **PTH format**: GFF V3.2 with `Path_Points` list, each point has `X`, `Y`, `Connections` list — our `kotor_write_pth` MCP tool matches this.
- **Minimap rendering**: Blender renders top-down, saves as `lbl_mapXXXX.tga` at area origin. GModular has no minimap generation — **roadmap gap**.
- **Room connections**: Vertex-painted with `(0.0, G, 0.0)` where `G = (200 + room_index) / 255` — GModular doesn't handle this — **roadmap gap**.
- **Animation format**: `apply_keyframes()` / `unapply_keyframes()` pattern — our MDL writer supports keyframe export but the viewport scrubber is not connected.

### From `OldRepublicDevs/KotorMCP` (standalone read-only MCP server)
- Focuses purely on read operations (detect installation, list resources, describe resource, journal overview). GModular's MCP is write-capable and far broader.
- Confirms PyKotor's `Installation` class for resource resolution — we should integrate this for "load resource from game installation" workflow.

### From `CrispyW0nton/GhostRigger` (GhostRigger v4.2)
- v4.2 (2026-03-19): Full visual audit, 5,426 renders, automatic game-installation detection
- **43 MCP tools** for MDL operations, cross-game porting, UV pipeline, texture loading
- **AgentDecompile bridge** for NCS bytecode decompilation — GModular should integrate this
- **GhostRigger IPC**: Port 7001, handles `open_blueprint`, `ping`, `get_blueprint_field`, `set_blueprint_field`
- GModular's `ghostrigger_*` MCP tools call into GhostRigger IPC but GhostRigger must be running separately

### From `CrispyW0nton/GhostScripter-K1-K2` (GhostScripter v3.4.1)
- **58-59 MCP commands** for NWScript: `readGFF`, `writeDLG`, `compileScript`, `getResource`, `searchAll`
- **Composite queries**: `getQuest`, `getNpc`, `getCreature` — these are the "get_*" tools now registered in GModular's MCP
- **Visual DLG editor** (node graph), quest builder, 2DA manager, TLK editor, journal editor
- GModular's `ghostscripter_*` MCP tools call GhostScripter IPC on port 7002

---

## Roadmap

### Phase 1 — Bug Fix Sprint (v2.1.0) 🚨 URGENT
*Target: Zero crash-on-use bugs. A modder should be able to open a .mod and see the walkmesh.*

| # | Task | Files | Priority |
|---|------|-------|----------|
| 1.1 | ~~Fix walkmesh overlay (`parser.parse()` / `.is_walkable`)~~ | `main_window.py` | ✅ **DONE** |
| 1.2 | Add `GFFRoot.set_field(label, GFFField)` overload | `gff_types.py` | 🔴 HIGH |
| 1.3 | Fix LYT world-offset: store `x/y/z` on `RoomInstance` from parsed LYT | `module_io.py`, `main_window.py` | 🔴 HIGH |
| 1.4 | Replace all `except Exception: pass` with `log.warning(exc)` + user toast | 114 locations | 🟠 HIGH |
| 1.5 | Add `ERFReaderMem.list_resources()` docstring clarifying string format | `archives.py` | 🟡 MEDIUM |
| 1.6 | Wire animation scrubber to viewport keyframe stepping | `animation_panel.py`, `viewport.py` | 🔴 HIGH |
| 1.7 | Fix `AnimationClipSignal` stubs (`emit`, `connect`, `__init__`) | `animation_panel.py` | 🔴 HIGH |

### Phase 2 — Walkmesh Editor Completion (v2.1.x)
*Target: A modder can visually edit walkmesh face materials, fix broken faces, and export a game-ready .wok.*

| # | Task | Notes |
|---|------|-------|
| 2.1 | **Walkmesh face select + paint in 3D viewport** | Click face → panel shows material, can change dropdown; viewport re-renders immediately |
| 2.2 | **Visual diff mode** | Side-by-side original vs. modified face coloring |
| 2.3 | **AABB tree visualization** | Debug overlay showing AABB node hierarchy |
| 2.4 | **Perimeter edge visualization** | Highlight outer edges + transition IDs |
| 2.5 | **Merge multiple WOK files** | For multi-room modules, combine room walkmeshes before edit |
| 2.6 | **Fix non-walkable island detection** | Auto-detect faces isolated from walkable regions |
| 2.7 | **One-click repack to .mod** | After WOK edit → pack back into original .mod in-place |
| 2.8 | **Material legend panel** | Show all KotOR surface materials with colors + walkable flag |

**KotOR Surface Material Reference** (from PyKotor `SurfaceMaterial`, kotorblender, BWM spec):
```
 0  GRASS         walkable  (green)
 1  STONE         walkable
 2  WOOD          walkable
 3  WATER         walkable (shallow)
 4  NONWALK       non-walkable
 5  TRANSPARENT   non-walkable
 6  CARPET        walkable
 7  METAL         walkable
 8  PUDDLES       walkable
 9  SWAMP         walkable
10  MUD           walkable
11  LEAVES        walkable
12  LAVA          non-walkable (damage)
13  BOTTOMLESS    non-walkable (death)
14  DEEP_WATER    non-walkable
15  DOOR          walkable (trigger)
16  NONWALK_GRASS non-walkable
```

### Phase 3 — MDL Viewer & Room Assembly (v2.2.x)
*Target: Viewport shows actual game-accurate 3D models for an opened module.*

| # | Task | Notes |
|---|------|-------|
| 3.1 | **MDL binary parser → GPU mesh** | Parse MDL/MDX geometry; upload to OpenGL VBO; render with textured shader |
| 3.2 | **TPC/TGA texture loader** | Load `.tpc` (KotOR's DXT-compressed format) and `.tga` textures into GPU |
| 3.3 | **Multi-room rendering from LYT** | Position each room model at LYT world_x/y/z offset |
| 3.4 | **Door model placement** | Place door MDLs at door-hook positions from LYT |
| 3.5 | **Walkmesh overlay on 3D geometry** | Render WOK triangles above MDL floor with material colors |
| 3.6 | **Room connection vertex painting** | Show/edit vertex color (0, G, 0) for room transitions |
| 3.7 | **Minimap generation** | Top-down orthographic render → `lbl_mapXXXX.tga` (port from KotorBlender) |

**Technical references:**
- MDL format: `gmodular/formats/mdl_parser.py` (1,819 lines, fully implemented)
- MDX: vertex/normal buffer paired with MDL, already parsed
- TPC loader: needs DXT1/DXT3/DXT5 decompression (use `compressonator` or manual)
- KotorBlender `io_scene_kotor/format/mdl/reader.py` for ASCII/binary disambiguation

### Phase 4 — Animation Playback (v2.2.x)
*Target: Viewport plays character/room animations from MDL anim nodes.*

| # | Task | Notes |
|---|------|-------|
| 4.1 | **Connect scrubber to keyframe stepping** | `animation_panel.py` emit/connect stubs → call `viewport.step_to_frame(n)` |
| 4.2 | **Baked keyframe array** | Parse MDL anim controller data → flat array of [time, value] pairs per node |
| 4.3 | **Hermite/Bezier interpolation** | KotOR uses bezier-hermite splines for position/orientation controllers |
| 4.4 | **Skinned mesh deformation** | `SKIN` node type: bone weights per vertex → GPU skinning shader |
| 4.5 | **Animation list panel** | Dropdown showing all named animations in MDL (idle, walk, attack, etc.) |

### Phase 5 — Game Installation Integration (v2.3.x)
*Target: Modder can load resources directly from their KotOR installation.*

| # | Task | Notes |
|---|------|-------|
| 5.1 | **KotOR installation detector** | Detect K1/K2 on Windows registry, Steam, common paths, env vars — mirror PyKotor `game_detector.py` |
| 5.2 | **BIF resource browser** | Browse chitin.key → open any BIF resource by ResRef |
| 5.3 | **Override folder integration** | Show override/ files in content browser with highest-priority flag |
| 5.4 | **"Load from game" button** | Select a ResRef → extract from game's BIF/MOD/Override |
| 5.5 | **Module list** | List all .mod/.rim files from game's Modules/ folder |
| 5.6 | **TLK browser** | Load dialog.tlk; resolve StrRefs in ARE/GIT/DLG fields |
| 5.7 | **2DA table lookup** | Browse appearance.2da, baseitems.2da, feat.2da etc. with filtering |

**References for implementation:**
- PyKotor `Installation` class: handles all resource resolution, BIF decompression, override priority
- KotorMCP `listResources` / `describeResource`: shows expected API surface
- GhostRigger `game_detector.py` (v4.2, 2026-03-19): cross-platform installation detection

### Phase 6 — Module Authoring Pipeline (v2.4.x)
*Target: Create a new module from scratch inside GModular.*

| # | Task | Notes |
|---|------|-------|
| 6.1 | **New module wizard** | Dialog: name, type (interior/exterior), rooms, entry point |
| 6.2 | **Room placement UI** | Drag-and-drop rooms onto 2D floor plan; auto-generate LYT |
| 6.3 | **Patrol path editor** | Place path nodes; generate PTH GFF (matches KotorBlender PTH export) |
| 6.4 | **Placeable/creature placement** | Drag from asset palette → places GIT struct with XYZ/Orientation |
| 6.5 | **Trigger volume editor** | Draw trigger polygon → GIT trigger struct |
| 6.6 | **Door placement** | Place door MDL + generate walkmesh gap + door hook |
| 6.7 | **Script assignment UI** | Assign OnEnter/OnExit/OnHeartbeat etc. scripts to area/creatures |
| 6.8 | **"Build & test" action** | Pack .mod → copy to game Modules/ → launch game (or K1R) |

### Phase 7 — GhostWorks Pipeline Integration (v2.5.x)
*Target: The 10-minute NPC workflow from PIPELINE_SPEC.md actually works end-to-end.*

| # | Task | Notes |
|---|------|-------|
| 7.1 | **GhostRigger live IPC** | GModular opens GhostRigger; user rigs model; sends blueprint back via IPC |
| 7.2 | **GhostScripter live IPC** | GModular opens GhostScripter; user writes patrol script; compiled NCS sent back |
| 7.3 | **Blueprint field editor** | Edit creature template fields (HP, AC, perception, etc.) in GhostRigger field editor |
| 7.4 | **Compile-on-save** | NWScript in GhostScripter auto-compiles when saved; GModular uses .ncs |
| 7.5 | **AgentDecompile bridge** | Decompile existing .ncs → editable .nss in GhostScripter via AgentDecompile |
| 7.6 | **Pipeline status bar** | Shows connection status for GhostRigger (port 7001) and GhostScripter (port 7002) |

### Phase 8 — Quality & Distribution (v3.0.0)
*Target: One-click installer that non-programmers can use.*

| # | Task | Notes |
|---|------|-------|
| 8.1 | **PyInstaller single-file bundle** | `build.bat` → `GModular.exe` (Windows); no Python required |
| 8.2 | **GitHub Actions CI** | Run test suite on push; build Windows EXE on tag |
| 8.3 | **User documentation** | Getting-started tutorial: open a .mod, fix a walkmesh, save |
| 8.4 | **Video tutorial** | 10-minute demo of the full Ghostworks pipeline (GRigger → GScripter → GModular) |
| 8.5 | **Deadly Stream release thread** | Post to the main KotOR modding community forum |
| 8.6 | **Reduce silent swallows** | Fix all 114 `except: pass` blocks → log + user toast |
| 8.7 | **Test coverage audit** | 35% of tests lack assertions — add meaningful assertions |
| 8.8 | **API stability** | Pin `GFFRoot.set()` signature; add `GFFRoot.set_field()` wrapper |

---

## Architecture Debt

| Area | Issue | Impact | Fix |
|------|-------|--------|-----|
| `viewport.py` (2,798 lines) | Still monolithic despite `viewport_renderer.py` extraction | Hard to maintain | Extract `WalkmeshOverlay`, `SelectionManager`, `GizmoController` sub-classes |
| `main_window.py` (2,458 lines) | All UI logic in one file | Hard to maintain | Extract `ModuleLoader`, `WokWorkflow`, `DlgWorkflow` facades |
| `module_io.py` (silent swallows) | `_remap_resources_by_signature` catches all exceptions silently | Breaks on corrupt .mod | Add specific exception handling with user warnings |
| GFF API (3-arg `set()`) | Inconsistent with dataclass pattern; confuses contributors | Bugs on every new feature | Add `set_field(label, GFFField)` or use keyword args |
| Test quality (35% no-assert) | Many tests pass trivially without verifying behavior | False confidence | Audit and add assertions to all pass-only tests |
| No type stubs for Qt | PyQt5 used without stubs → mypy/pyright can't check Qt code | IDE quality | Add `PyQt5-stubs` to dev dependencies |

---

## Cross-Repo Compatibility Matrix

| Feature | GModular | PyKotor | KotorBlender | GhostRigger | GhostScripter |
|---------|----------|---------|--------------|-------------|---------------|
| GFF V3.2 read/write | ✅ | ✅ | ✅ | partial | ✅ |
| BWM/WOK read/write | ✅ | ✅ | ✅ | ✅ | ✗ |
| MDL binary read | ✅ | ✅ | ✅ | ✅ | ✗ |
| MDL binary write | ✅ | partial | ✅ | ✅ | ✗ |
| ERF/MOD/RIM read | ✅ | ✅ | partial | ✅ | ✅ |
| ERF/MOD write | ✅ | ✅ | ✗ | ✅ | ✅ |
| TPC/TGA texture | planned | ✅ | ✅ | ✅ | ✗ |
| NWScript compile | ✗ | ✗ | ✗ | ✗ | ✅ |
| DLG node graph | ✅ | partial | ✗ | ✗ | ✅ |
| 2DA editor | ✅ | ✅ | ✗ | ✗ | ✅ |
| TLK browser | planned | ✅ | ✗ | ✗ | ✅ |
| Game install detect | ✗ | ✅ | ✅ | ✅ | ✅ |
| MCP server | ✅ 103 tools | ✗ | ✗ | ✅ 43 tools | ✅ 58 tools |
| IPC (inter-tool) | ✅ bridge | ✗ | ✗ | ✅ port 7001 | ✅ port 7002 |
| Minimap render | ✗ | ✗ | ✅ | ✗ | ✗ |
| Room vertex paint | ✗ | ✗ | ✅ | ✗ | ✗ |

---

## Version Targets

| Version | Description | Test Target |
|---------|-------------|-------------|
| **v2.0.9** | Current (IPC bridge, 103 tools, 2,552 tests) | 2,552 ✅ |
| **v2.1.0** | Bug fix sprint (walkmesh overlay fix, GFF API, animation stubs) | ~2,600 |
| **v2.1.x** | Walkmesh editor completion (visual paint, merge, one-click repack) | ~2,700 |
| **v2.2.x** | MDL viewer + animation playback | ~2,900 |
| **v2.3.x** | Game installation integration | ~3,100 |
| **v2.4.x** | Module authoring pipeline | ~3,300 |
| **v2.5.x** | GhostWorks end-to-end pipeline | ~3,500 |
| **v3.0.0** | Binary release + community launch | ~3,600 |

---

## Priority Order for Next Session

1. 🔴 **v2.1.0 bugs**: GFF `set_field()` overload, LYT room offset, animation stubs → fix and test
2. 🔴 **MDL → GPU mesh**: The #1 thing missing from "working modding tool" is seeing the actual model
3. 🔴 **Game install detection**: Modder needs to load real game files, not just their own .mod
4. 🟠 **TPC texture loader**: Can't render models without textures
5. 🟠 **Face-click walkmesh editing**: The walkmesh editor needs to be interactive in the viewport
6. 🟡 **One-click .exe build**: Binary release is required for community adoption

---

*Generated from: full source audit of GModular v2.0.9, slem_ar.mod scenario testing,*
*OldRepublicDevs/PyKotor BWM/GFF/LYT source review, OldRepublicDevs/kotorblender MDL/WOK reference,*
*OldRepublicDevs/KotorMCP API patterns, CrispyW0nton/GhostRigger v4.2 game_detector review,*
*CrispyW0nton/GhostScripter-K1-K2 v3.4.1 tool inventory.*
