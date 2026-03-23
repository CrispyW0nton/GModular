# GModular — Development Roadmap
## Based on full source audit, slem_ar.mod scenario testing, and OldRepublicDevs repo research
**Updated:** 2026-03-23 | **Basis:** v2.1.0 state (2,748 tests, 7 skipped, 0 failures)

---

## The Honest User Experience (v2.0.10)

> *Scenario: A KotOR modder downloads the latest commit from GitHub today and runs `build.bat`, then opens `slem_ar.mod` to fix walkmeshes.*

### ⚠️ CRITICAL BUILD.BAT BUG (Fixed in v2.0.10)

Build.bat **v2.0.8/v2.0.9 never installed `qtpy`** — which is imported by every single GUI file in the
codebase.  Launching `python main.py` or `GModular.exe` would immediately crash:
```
ModuleNotFoundError: No module named 'qtpy'
```
**Fixed in v2.0.10:** `build.bat` now installs `"qtpy>=2.4.0"` alongside PyQt5 in Step 5,
and also adds `typing_extensions` to Step 6.

### Scenario Test Results (v2.0.10 — 12 backend steps, headless)

| Step | What the modder does | Backend result |
|------|----------------------|----------------|
| 1 | Open `slem_ar.mod` | ✅ 49ms, 5 resources, no errors |
| 2 | Inspect area name/tag | ✅ `name='Sleheyron Arena'`, `tag='slem_ar'` |
| 3 | Check module entry info | ✅ `mod_name='Sleheyron Arena'` (entry_area blank — synthetic mod) |
| 4 | View creatures/objects | ✅ GIT loads: 0 objects (empty module, expected) |
| 5 | See room layout | ✅ LYT: 1 room `slem_ar` at (0,0,0) |
| 6 | Load walkmesh | ✅ 32 faces, 28 walkable, 4 non-walkable (material=3 WATER) |
| 7 | Fix non-walkable faces | ✅ Changed 4 faces to SURF_GRASS → 32/32 walkable |
| 8 | Export edited WOK | ✅ 4,744 bytes BWM V1.0; round-trip verified |
| 9 | Add a creature | ✅ `c_jedi_01` at (5,5,0) added to GIT |
| 10 | Repack to .mod | ✅ `slem_ar_scenario_output.mod` (5,934 bytes, 5 resources) |
| 11 | Write ARE back to GFF | ✅ `save_are(are, path)` implemented — writes valid GFF V3.2 .ARE binary |
| 12 | Use MCP tools | ✅ 103 tools registered via `get_all_tools()` |

**Result: 12/12 PASS ✅ (ARE write-back fixed in v2.0.14)**

### What works well ✅

| Feature | Status | Notes |
|---------|--------|-------|
| **Open .mod file** | ✅ Works | `ModuleIO().load_from_mod()` (instance method) extracts all resources, handles type-ID remapping, synthesizes LYT if missing |
| **Parse walkmesh (WOK)** | ✅ Works | `WOKParser.from_file()` returns a full `WalkMesh`; `slem_ar.wok` parsed: 32 faces, 28 walkable |
| **Inspect face materials** | ✅ Works | `face.walkable`, `face.material` (int), `face.v0/v1/v2` (tuples) all correct |
| **Edit face materials** | ✅ Works | `face.material = SURF_GRASS` (use `SURF_*` constants, not `SurfaceMaterial.GRASS`) |
| **Write modified WOK** | ✅ Works | `WOKWriter(wm, bwm_type=1).to_bytes()` → valid BWM V1.0 binary; round-trip verified |
| **Repack into .mod** | ✅ Works | `ERFWriter().add_resource(resref, ext, data)` then `.to_file(path)` |
| **MCP tools** | ✅ Works | 103 tools from `get_all_tools()` (not `MCPServer()`) |
| **GFF read (ARE/GIT/IFO)** | ✅ Works | `load_are(path)`, `load_git(path)`, `load_ifo(path)` — file paths not bytes |
| **Add creature to GIT** | ✅ Works | `GITCreature()` + `git.creatures.append(c)` |
| **LYT field** | ✅ Works | `result.lyt_text` (not `.lyt`) on `ModuleLoadResult` |
| **2DA editor** | ✅ Works | Full CRUD, filter, undo/redo, CSV export |
| **DLG editor** | ✅ Works | Node graph, GFF serialization, replies/entries, starters |
| **LYT parser** | ✅ Works | Room/door hook parsing; synthesis from MDL when LYT missing |
| **BWM/WOK round-trip** | ✅ Works | Vertex deduplication, AABB tree, adjacency table, outer edges all generated |
| **MDL binary writer** | ✅ Works | Full MDL/MDX write: geometry, skin, dangly, emitter nodes |
| **Archive formats** | ✅ Works | ERF/MOD/RIM/BIF read; ERF/MOD write via `ERFWriter` |
| **GhostScripter IDE stubs** | ✅ Wired | NWScript tokenizer, syntax highlighter, function browser |
| **GhostRigger stubs** | ✅ Wired | Field editor, blueprint UI, IPC server on port 7001 |
| **Ghostworks IPC bridge** | ✅ Works | `ghostworks_bridge.py` HTTP bridge; all `ghostrigger_*` and `ghostscripter_*` functions |

### What doesn't work (confirmed bugs) 🔴

| Bug | Severity | File:Line | Description | Fix |
|-----|----------|-----------|-------------|-----|
| **build.bat missing `qtpy`** | 🔴 CRITICAL | `build.bat:Step5` | `qtpy` is the Qt compatibility shim used by every GUI file; build.bat never installed it → crash on launch with `ModuleNotFoundError: No module named 'qtpy'` | **FIXED v2.0.10** — now installs `qtpy>=2.4.0` with PyQt5 |
| **Walkmesh overlay crash** | 🔴 CRITICAL | `main_window.py:1409` | Old code called `.parse()` then `.vertices` on a `WalkMesh` object — neither method exists | **FIXED v2.0.10** — use `wok.faces` directly |
| **`face.is_walkable` typo** | 🔴 CRITICAL | `main_window.py:1443` | Attribute is `face.walkable` not `face.is_walkable` | **FIXED v2.0.10** |
| **`save_are()` missing** | ✅ FIXED v2.0.14 | `gff_writer.py` | `save_are(are, path)` implemented — GFF V3.2 binary writer for .ARE files with full field layout | Done — 7 tests (pass8) + 6 tests (pass9) |
| **`ERFReaderMem.list_resources()` returns strings** | 🟡 MEDIUM | `archives.py` | Returns `['name.ext', ...]` not `[(name, ext), ...]`; code must use `.rsplit('.', 1)` | Document in docstring; consistent with `ERFReader` |
| **`GFFRoot.set()` signature mismatch** | ✅ FIXED v2.0.14 | `gff_types.py` | `set_field(label, field: GFFField)` overload added; `set()` now accepts both APIs | Done — 7 tests (pass8) + 5 tests (pass9) |
| **LYT world-offset floats** | 🟡 MEDIUM | `module_io.py` | LYT room lines parse X/Y/Z — but `main_window` accesses `room.world_x/y/z` via `getattr(..., 0.0)` — offset always 0 for multi-room modules | Fix RoomInstance to store parsed LYT coords |
| **Animation playback unfinished** | 🟡 MEDIUM | `animation_panel.py` | `AnimationClipSignal.__init__`, `emit`, `connect` are stubs (`pass`) — scrubber wired in UI but not connected to renderer | See Phase 4 |
| **114 silent exception swallows** | 🟡 MEDIUM | multiple | `except Exception: pass` throughout — modder sees no feedback when things fail silently | Audit and add logging/user-facing error dialogs |
| **Viewport.py: 2,798 lines** | 🟡 MEDIUM | `viewport.py` | Still oversized despite renderer extraction; `__init__` and `__set_name__` stubs remain | Continue refactor toward sub-modules |
| **No file drag-and-drop** | 🟡 MEDIUM | `main_window.py` | GUI has no drag-and-drop for .mod files | UX gap |
| **No installer/binary release** | 🟠 HIGH | `build.bat` | Requires Python 3.12, PyQt5, manual setup — no one-click installer | PyInstaller bundle needed for community adoption |

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
| 1.2 | ~~Fix `build.bat`: add `qtpy` + `typing_extensions`~~ | `build.bat` | ✅ **DONE v2.0.10** |
| 1.3 | ~~Add `save_are(are, path)` to `gff_writer.py`~~ | `gff_writer.py` | ✅ **DONE v2.0.14** |
| 1.4 | ~~Add `GFFRoot.set_field(label, GFFField)` overload~~ | `gff_types.py` | ✅ **DONE v2.0.14** |
| 1.5 | Fix LYT world-offset: store `x/y/z` on `RoomInstance` from parsed LYT | `module_io.py`, `main_window.py` | 🔴 HIGH |
| 1.6 | Replace all `except Exception: pass` with `log.warning(exc)` + user toast | 114 locations | 🟠 HIGH |
| 1.7 | Add `ERFReaderMem.list_resources()` docstring clarifying string format | `archives.py` | 🟡 MEDIUM |
| 1.8 | Wire animation scrubber to viewport keyframe stepping | `animation_panel.py`, `viewport.py` | 🔴 HIGH |
| 1.9 | Fix `AnimationClipSignal` stubs (`emit`, `connect`, `__init__`) | `animation_panel.py` | 🔴 HIGH |
| 1.10 | ~~**Fix specular view_dir bug**: `camera_pos` uniform added to all 4 lit shaders; `normalize(-v_world_pos)` → `normalize(camera_pos - v_world_pos)`~~ | `viewport_shaders.py`, `viewport_renderer.py` | ✅ **DONE v2.0.14** *(McKesson §9)* |
| 1.11 | ~~Write `camera_pos` uniform in `render()` before each lit-shader draw call~~ | `viewport_renderer.py` | ✅ **DONE v2.0.14** *(McKesson §9)* |

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
| 2.9 | **Möller-Trumbore ray-triangle hit for face clicking** | Screen-ray → walkmesh triangles; required for item 2.1. Algorithm: Ericson §5.3.6 | *(Ericson §5.3.6)* |
| 2.10 | **Room Assembly 2D AABB overlap detection** | When dragging room in grid, show red highlight if overlapping another room | *(Millington2 §18)* |

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
| 3.8 | **Frustum culling** | Extract 6 planes from MVP (Lengyel §8 formula); test each room AABB per frame before submitting draw call. Eliminates wasted GPU time on off-screen rooms *(Lengyel §8, Ericson §4, Eberly §3)* |
| 3.9 | **Portal culling from .vis** | Load `.vis` file; traverse portal graph from camera's current room; only render reachable rooms. Matches KotOR's actual renderer *(Eberly §7, Ericson §7.6, Lengyel2 §5)* |
| 3.10 | **Precompute normal matrix as `uniform mat3`** | Move `transpose(inverse(mat3(model)))` from GPU fragment → CPU at VAO build time. Room models are pure translations so `normal_matrix=I` always — skip computation entirely *(Lengyel §4, Varcholik §6)* |
| 3.11 | **Add `camera_pos` to Phong shaders** | Correct specular view direction: `normalize(camera_pos - v_world_pos)` instead of `normalize(-v_world_pos)` *(McKesson §9)* |
| 3.12 | **sRGB gamma correction** | Linearize TPC textures on sample (`pow(albedo, 2.2)`); gamma-encode output (`pow(result, 1/2.2)`). KotOR textures are sRGB-encoded *(McKesson §12)* |
| 3.13 | **Shadow mapping** | Two-FBO approach: render from light's POV into depth texture; sample with PCF in main pass *(Varcholik §14)* |
| 3.14 | **TBN tangent space for MDL bump maps** | Add `in_tangent` + `in_bitangent` to `_VERT_TEXTURED`; TBN matrix for `BUMP` node type *(Lengyel §7.8, Varcholik §8-9)* |

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
| 4.6 | **RK4 integration for spline evaluation** | Replace Euler integration with RK4 for Bezier-Hermite controller step — avoids keyframe snapping *(Millington §16, McKesson §10)* |
| 4.7 | **Danglymesh damped spring** | Per-vertex damped spring with displacement constraint: `f = -k*x - b*v`; Euler step per frame. Models tree/hair oscillation *(Millington §5-7)* |
| 4.8 | **Dual quaternion skinning** | Replace linear blend skinning (causes candy-wrapper artefacts at joints) with DQS *(Lengyel2 §6)* |

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

### Phase 5b — Renderer Performance (v2.3.x)
*Target: Viewport stays fast for large multi-room modules (20+ rooms, 100+ GIT objects).*
*All items from textbook study — see TEXTBOOK_STUDY_REPORT.md.*

| # | Task | Notes |
|---|------|-------|
| 5b.1 | **Uniform Buffer Objects (UBOs)** | Move per-frame matrices + light data into a `std140` UBO block. Reduces N×5 uniform API calls to 1 bind per frame *(Lengyel2 §1)* |
| 5b.2 | **Instance batching for GIT objects** | When the same creature/placeable MDL appears N times, use `DrawInstanced` with per-instance transform buffer *(Varcholik §15)* |
| 5b.3 | **LOD for distant rooms** | Rooms >50 units from camera use simplified index buffer (every other triangle). Eberly LOD node pattern *(Eberly §4)* |
| 5b.4 | **FXAA post-process pass** | Screen-space edge detection → anti-aliased output. One additional full-screen quad render *(Lengyel2 §7)* |
| 5b.5 | **Reverse-Z depth buffer** | For large outdoor modules (Dantooine, Kashyyyk): `near → 1.0, far → 0.0`, `glDepthFunc = GREATER`. Eliminates Z-fighting at distance *(McKesson §5)* |
| 5b.6 | **Cull-before-draw pass split in `render()`** | Separate `_cull_rooms()` → `_draw_rooms()` passes. Cull pass populates `_visible_rooms` list; draw pass iterates that list *(Eberly §3)* |
| 5b.7 | **Spatial coherence cache** | Cache `_last_camera_room` for walkmesh queries; only re-query AABB tree when camera moves >0.5 units *(Ericson §6.7)* |

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
| `viewport.py` (< 3,000 lines ✅ v2.1.0) | Still monolithic despite `viewport_renderer.py` extraction | Hard to maintain | Extract `WalkmeshOverlay`, `SelectionManager`, `GizmoController` sub-classes |
| `main_window.py` (2,458 lines) | All UI logic in one file | Hard to maintain | Extract `ModuleLoader`, `WokWorkflow`, `DlgWorkflow` facades |
| `module_io.py` (silent swallows) | `_remap_resources_by_signature` has 10+ silent `except: pass` | Breaks on corrupt .mod | Replace with `log.debug(f"remap failed {resref}: {e}")` |
| GFF API (`set_field()` ✅ added v2.0.14) | Old 3-arg `set()` still present for back-compat | Could confuse new contributors | Document clearly; deprecate 3-arg form in v2.2.x |
| Animation signals (stubs remain) | `AnimationClipSignal.emit/connect` are `pass` stubs | Scrubber UI fires nothing | Implement with `qtpy.QtCore.Signal` or callback list |
| Test quality (some no-assert tests) | Some tests pass trivially without verifying behavior | False confidence | Audit and add assertions to worst offenders |
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
| TPC/TGA texture | ✅ API fixed | ✅ | ✅ | ✅ | ✗ |
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
| **v2.0.13** | Projection matrix fix, depth FBO, LYT parser, render tests, 8-textbook study | 2,388 ✅ |
| **v2.0.14** | Specular camera_pos fix, CPU normal matrix, Möller-Trumbore ray-hit, frustum culling, portal/VIS culling, save_are(), set_field() overload — 58 new tests | 2,446 ✅ |
| **v2.0.15** | Phase 2/3 API validation: ray-tri edge cases, walkmesh click logic, LYT world-coord, VIS data, save_are/ifo binary, GFFField round-trip — 46 new tests | 2,492 ✅ |
| **v2.1.0** | **CURRENT** — Walkmesh face-selection wiring, MDL→GPU bridge fix, TPC texture API fix, slem_ar.mod fixture MDL/MDX, test_roadmap_pass10+11 — 81 new tests | **2,748** ✅ |
| **v2.1.x** | Walkmesh editor completion (visual paint, face click using M-T, merge, one-click repack) | ~2,900 |
| **v2.2.x** | MDL viewer + animation playback (baked keyframes, Hermite/Bezier) | ~3,100 |
| **v2.3.x** | Game installation integration + renderer performance (UBOs, instancing, FXAA) | ~3,300 |
| **v2.4.x** | Module authoring pipeline | ~3,500 |
| **v2.5.x** | GhostWorks end-to-end pipeline | ~3,700 |
| **v3.0.0** | Binary release + community launch | ~3,900 |

---

## Completed This Session (v2.1.0, 2026-03-23)

### v2.1.0 (test_roadmap_pass10.py + test_roadmap_pass11.py — 81 tests)

| # | Feature | Reference | Tests Added | Status |
|---|---------|-----------|-------------|--------|
| 1 | **Walkmesh face selection wired** — `mousePressEvent` calls `_pick_walkmesh_face(sx,sy)` when `_walkmesh_edit_mode` is active; emits `walkmesh_face_selected(face_idx, t)` | Ericson §5.3.6 Möller-Trumbore | 8 tests | ✅ DONE |
| 2 | **`MeshData.renderable_nodes()` alias** — canonical alias for `visible_mesh_nodes()` added to `mdl_parser.py`; `load_mdl_mesh` updated to call it | mdl_parser.py MeshNode API | 4 tests | ✅ DONE |
| 3 | **`load_mdl_mesh` fix** — now correctly expands face-indexed vertices, calls `_upload_textured_mesh` with positional args matching the renderer signature | viewport_renderer.py line 628 | 6 tests | ✅ DONE |
| 4 | **`_load_tpc_texture` API fix** — replaced broken `TPCReader(bytes).to_rgba()` with correct `TPCReader.from_bytes(data)` → `TPCImage.rgba_bytes`; dimensions now read from `tpc.width/height` | tpc_reader.py TPCImage API | 5 tests | ✅ DONE |
| 5 | **`slem_ar.mod` fixture rebuilt** — MDL+MDX files added to test fixture using `ERFWriter`; fixture now has 8 resources including `slem_ar.mdl` and `slem_ar.mdx` | ERFWriter.to_bytes() | 3 tests | ✅ DONE |
| 6 | **VIS portal graph loaded in `main_window`** — `VisibilityData.from_string()` called after mod load; `set_vis_rooms()` receives visible-room set from parsed .vis data | Eberly §7, Ericson §7.6 | 4 tests | ✅ DONE |
| 7 | **Exception audit — viewport files** — silent `except Exception: pass` in `viewport.py` replaced with `log.debug(e)` (GL cleanup paths in renderer deliberately kept silent — context may be gone) | Phase 1.6 | 2 tests | ✅ DONE |
| 8 | **`viewport.py` line count trimmed to < 3,000** — `load_mdl_mesh` docstring condensed, `_expand_mdl_node` helper tightened, `load_walkmesh_from_rooms` docstring condensed | Maintainability | 3 tests | ✅ DONE |
| 9 | **`ViewportWidget.__new__` usage fixed** — test suite corrected to use `ViewportWidget.__new__(ViewportWidget)` not `object.__new__(ViewportWidget)` | Python MRO | 6 tests | ✅ DONE |
| 10 | **`MeshNode` `node_type` → `flags` fix** — tests updated to use `node.flags`, `node.is_mesh`, `node.is_aabb` instead of non-existent `node_type` attribute | mdl_parser.py MeshNode.flags | 5 tests | ✅ DONE |
| 11 | **`VisibilityData` VIS format fix** — confirmed multi-token per-line format works; `are_visible(b,c)` returns True when `b_room → c_room` exists in one direction | lyt_vis.py VisibilityData | 5 tests | ✅ DONE |

**v2.1.0 test summary: 81 new tests added → 2,748 passing (was 2,492 at v2.0.15, +256 total from infrastructure fixes)**

---

## Previously Completed (v2.0.14 + v2.0.15, 2026-03-21)

### v2.0.14 (test_roadmap_pass8.py — 58 tests)


| # | Feature | Reference | Tests Added | Status |
|---|---------|-----------|-------------|--------|
| 1 | Specular `camera_pos` fix — `view_dir = normalize(camera_pos - v_world_pos)` in all 4 lit shaders | McKesson Ch.9, Lengyel §7 | 4 shader tests | ✅ DONE |
| 2 | CPU normal matrix — `cpu_normal_mat` uniform precomputed via `_cpu_normal_matrix()` | Lengyel §4 | 5 normal matrix tests | ✅ DONE |
| 3 | Möller-Trumbore ray-triangle intersection — `_ray_tri_intersect()` + `hit_test_walkmesh()` | Ericson §5.3.6 | 7 ray-cast tests | ✅ DONE |
| 4 | Frustum culling — `_extract_frustum_planes()` + `_aabb_inside_frustum()` + room AABB cache | Lengyel §8, Ericson §4 | 8 frustum tests | ✅ DONE |
| 5 | Portal/VIS culling — `set_vis_rooms()`, `_vis_rooms` gate in render loop | Eberly §7, Ericson §7.6 | 4 portal tests | ✅ DONE |
| 6 | `save_are()` — complete GFF V3.2 serialiser for .ARE files | xoreos, KotOR spec | 7 ARE tests | ✅ DONE |
| 7 | `GFFStruct.set_field()` overload — accepts `GFFField` objects | GFF V3.2 spec | 7 API tests | ✅ DONE |
| 8 | Room AABB cache — populated in `rebuild_room_vaos`, used for frustum test | Ericson §6.4.2 | (part of frustum) | ✅ DONE |

**v2.0.14 test summary: 58 new tests added → 2,446 passing (was 2,388)**

### v2.0.15 (test_roadmap_pass9.py — 46 tests)

| # | Feature | Tests Added | Status |
|---|---------|-------------|--------|
| 1 | `_ray_tri_intersect()` edge-case validation (parallel, behind, near-edge, tuple verts, miss outside) | 6 tests | ✅ DONE |
| 2 | `hit_test_walkmesh()` multi-triangle selection (closest-win, single miss, side-miss, empty, degenerate) | 6 tests | ✅ DONE |
| 3 | `set_vis_rooms()` portal state management (enable, disable, empty set, None, membership) | 5 tests | ✅ DONE |
| 4 | `_cpu_normal_matrix()` correctness (identity, translation, uniform scale, dtype, shape) | 5 tests | ✅ DONE |
| 5 | Frustum helpers: 6-plane extraction, 4-component planes, empty bypass, inside/behind culling | 5 tests | ✅ DONE |
| 6 | `LayoutData.from_string()` world-coord preservation (single, two rooms, negative coords, `.position` tuple) | 4 tests | ✅ DONE |
| 7 | `VisibilityData` API (are_visible true/false, visible_from list, case-insensitive) | 4 tests | ✅ DONE |
| 8 | `save_are()` / `save_ifo()` binary validation (non-empty, ARE/IFO magic bytes, tag-divergence) | 6 tests | ✅ DONE |
| 9 | `GFFStruct.set_field()` round-trip (GFFField accept, byte value, string, overwrite, `in` operator) | 5 tests | ✅ DONE |

**v2.0.15 test summary: 46 new tests added → 2,492 passing (was 2,446)**

---

## Priority Order for Next Session

### Tier 1 — Critical Path (Unblocks visible rendering)

1. 🔴 **LYT world-offset integration** (Phase 1.5): `RoomPlacement.x/y/z` is correctly parsed and tested. Audit that `main_window.py` passes real `RoomPlacement` objects (not bare `RoomInstance` stubs with `.world_x/.world_y/.world_z`) to `rebuild_room_vaos()`. The renderer's `_room_coord_opt()` helper uses `getattr(obj, 'x', 0.0)` — confirm `RoomPlacement` has `.x/.y/.z` not `.world_x`. Then multi-room modules will render rooms at their correct world positions. (Files: `gmodular/gui/main_window.py`, `gmodular/gui/viewport_renderer.py`)

2. 🔴 **TPC textures in `rebuild_room_vaos`** (Phase 3.2): `_load_tpc_texture` API is now correct (`TPCReader.from_bytes`). Wire it into the `rebuild_room_vaos` path so rooms load real DXT textures from the game directory. Currently `rebuild_room_vaos` resolves `.tpc` paths but calls the old broken API. Update the call site. (File: `gmodular/gui/viewport_renderer.py` lines 1300–1390)

3. 🔴 **Animation signal stubs → real signals** (Phase 1.8/4.1): `AnimationClipSignal.__init__`, `emit`, and `connect` are still `pass` stubs in `animation_panel.py`. The scrubber UI is wired but fires nothing. Replace with a proper Qt signal (pyqtSignal / Signal from qtpy) or a lightweight callback list. The `_on_ruler_click` → `seek(elapsed)` path must actually advance the renderer's frame. (File: `gmodular/gui/animation_panel.py`)

4. 🔴 **`rebuild_room_vaos` texture integration test** (Phase 3.3): Write `test_roadmap_pass12.py` covering: LYT world-offset round-trip (room rendered at correct x/y/z), TPC load in `rebuild_room_vaos`, animation signal `connect/emit`, and VIS portal culling in a two-room scenario.

### Tier 2 — High Value (Enables real modding workflow)

5. 🟠 **Game installation detection** (Phase 5.1): Mirror PyKotor's `game_detector.py` strategy — check Windows registry `HKLM\SOFTWARE\BioWare\SW\KOTOR`, Steam `libraryfolders.vdf`, common paths `C:/Program Files (x86)/Star Wars Knights of the Old Republic`, env var `KOTOR_PATH`. Return a `GameInstall(path, game=1|2)` dataclass. (File: new `gmodular/core/game_detector.py`)

6. 🟠 **Module thumbnail generator** (Phase 3.4): `generate_module_thumbnail()` is implemented in `viewport.py`. Wire it to `main_window.py` so the content browser shows a rendered thumbnail after mod load. Cache to `{mod_stem}_thumb.png`. (Files: `gmodular/gui/main_window.py`, `gmodular/gui/viewport.py`)

7. 🟠 **`module_io.py` exception audit** (Phase 1.6): `_remap_resources_by_signature` has 10+ silent `except Exception: pass` blocks. Replace with `log.debug(f"resource remap failed for {resref}: {e}")`. This gives modders feedback when .mod files are corrupt. (File: `gmodular/core/module_io.py`)

### Tier 3 — Polish & Distribution

8. 🟡 **Drag-and-drop .mod support** (Phase 6.1): `QDropEvent` in `MainWindow` accepting `.mod`/`.erf`/`.rim` files — calls existing `_load_module()`. Three lines of code with big UX impact.

9. 🟡 **One-click `.exe` build** (Phase 8.1): `GModular.spec` exists. Verify `pyinstaller GModular.spec` produces a working EXE on Windows. Add `--hidden-import` entries for `moderngl`, `qtpy`, `PyQt5`. Required for community adoption on Deadly Stream.

10. 🟡 **Test coverage audit** (Phase 8.7): Grep for tests that `assert True` or have no assertions. Add meaningful assertions to the worst 20 offenders. Focus on `test_module_state.py` and `test_mcp.py` where pass-only tests are most common.

---

*Updated 2026-03-23 (v2.1.0). Incorporates findings from eight foundational 3D engine textbooks:*
*Eberly (3D Game Engine Design 2e), Varcholik (RT3D Rendering DirectX+HLSL), Ericson (RT Collision Detection),*
*McKesson (Learning Modern 3D Graphics), Lengyel (Math for 3D Game Programming 3e),*
*Lengyel (Foundations of GED Vol.2 Rendering), Millington (Game Physics Engine Development 2e).*
*Full report: TEXTBOOK_STUDY_REPORT.md*

*Generated from: full source audit of GModular v2.1.0, slem_ar.mod scenario testing (12/12 via EGL render, slem_ar.mod rebuilt with MDL/MDX),*
*OldRepublicDevs/PyKotor BWM/GFF/LYT source review, OldRepublicDevs/kotorblender MDL/WOK reference,*
*OldRepublicDevs/KotorMCP API patterns, CrispyW0nton/GhostRigger v4.2 game_detector review,*
*CrispyW0nton/GhostScripter-K1-K2 v3.4.1 tool inventory.*
