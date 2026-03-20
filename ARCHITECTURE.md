# GModular — Architecture Review
## *Applying "Balancing Coupling in Software Design" by Vlad Khononov*

---

## 1  Coupling Framework (Khononov's Three Dimensions)

| Dimension       | Meaning                                              | Our Scale |
|-----------------|------------------------------------------------------|-----------|
| **Strength**    | How much shared knowledge two components carry       | Contract < Model < Functional < Intrusive |
| **Distance**    | How far apart the components live (same class → cross-service) | 1 (same) … 5 (external system) |
| **Volatility**  | How often the shared knowledge changes (Core domain high, Generic low) | low/med/high |

Khononov's balance equation:

```
MAINTENANCE_EFFORT = STRENGTH × DISTANCE × VOLATILITY
BALANCE = max(|STRENGTH − DISTANCE|, 10 − VOLATILITY) + 1
```

High-strength couplings are fine **only** when components are co-located and the
knowledge is stable.  High-strength across distance (or with volatile knowledge)
is the primary source of accidental complexity.

---

## 2  Current Architecture Snapshot

```
gmodular/
├── formats/       # parsers / readers — stable generic domain
│   ├── archives.py          ResourceEntry, KEYReader, ERFReader, ResourceManager
│   ├── gff_types.py         GFFFieldType, GFFStruct, GITData, AREData, IFOData, …
│   ├── gff_reader/writer    parse ↔ domain objects
│   ├── kotor_formats.py     TwoDAData (read+write), NCSData, LIPData, SSFData,
│   │                        TXIData, VISData, LYTData — all with binary R/W
│   ├── tpc_reader.py        TPCReader, write_tpc_from_rgba, write_tpc_from_tga
│   ├── lyt_vis.py           LYTReader, LYTWriter (canonical beginlayout/donelayout)
│   ├── mdl_parser.py        MeshData, MeshNode, MDLParser
│   ├── tlk_reader.py        TLKFile, TLKReader
│   └── twoda_loader.py      TwoDATable, TwoDALoader (+ from_bytes classmethod)
│
├── core/          # application domain — medium volatility
│   └── module_state.py      ModuleProject, ModuleState, Command hierarchy
│
├── engine/        # rendering helpers — medium volatility
│   ├── mdl_renderer.py
│   ├── npc_instance.py
│   └── player_controller.py
│
├── gui/           # presentation — high volatility (Qt via qtpy, Qt5+Qt6 ready)
│   ├── main_window.py       main application window
│   ├── viewport.py          OpenGL scene viewport
│   ├── dlg_editor.py        DLG node-graph visual editor (NEW)
│   │                        DLGGraphData, DLGNodeData, DLGCanvas, DLGEditorPanel
│   ├── twoda_editor.py      2DA table editor with undo/redo (NEW)
│   └── …panels (animation, asset_palette, inspector, room_assembly, …)
│
├── ipc/           # integration — medium volatility
│   ├── bridges.py           GhostScripterBridge, GhostRiggerBridge, ProjectFileWatcher
│   ├── callback_server.py
│   └── nwscript_bridge.py   NWSScriptBridge — compile/decompile NSS↔NCS (NEW)
│                            supports nwnnsscomp, DeNCS, PyKotor, disasm fallback
│
└── mcp/           # AI-tool layer — medium volatility, medium distance
    ├── state.py             KotorInstallation
    ├── _indexer.py          build_index()
    └── tools/               79 tool handlers (up from 26)
        ├── composite.py     high-level AI tools
        ├── formats.py       format library (SSF, LIP, TXI, VIS, NCS R/W, 2DA, TLK, LTR R/W)
        ├── scripts.py       NCS disassembly / compile / decompile / info
        ├── diff_tools.py    GFF diff, 2DA diff, TLK diff, GFF patch
        └── (dlg + nwscript tools — lazy-imported to honour mcp→gui boundary)
```

---

## 3  Coupling Hotspots (Ranked by Maintenance Effort)

### 3.1  ⚠️  CRITICAL — `ModuleState.load_from_mod()` (Functional coupling, Distance 2, Volatility HIGH)

**Symptoms:**
- `module_state.py` is 450 lines; 200 of them are ERF extraction logic with archive reading,
  path guessing, temp-file writing, and resref detection.
- `ModuleState` imports `ERFReader`, `EXT_TO_TYPE`, `RES_TYPE_MAP`, `GFFReader` — it knows
  archive internals (Intrusive→Functional coupling toward `formats.archives`).
- This is Core-domain logic buried inside a state container.

**Khononov diagnosis:**  Strength = Functional (3), Distance = 2 (same subsystem but logically
distinct responsibilities), Volatility = HIGH (archive loading rules change frequently).
`EFFORT = 3 × 2 × HIGH` — the highest single hotspot.

**Fix:**  Extract `ModuleIO` service (see §4.1).

---

### 3.2  ⚠️  CRITICAL — Singleton `get_module_state()` scattered across `viewport.py` (Common coupling)

**Symptoms:**
- `viewport.py` calls `get_module_state()` 12 times, inline at call sites.
- `gui/inspector.py` does the same 3 times.
- Any future second window / test harness / headless render cannot easily isolate state.
- This is Khononov's *Common Coupling* (shared global mutable state).

**Khononov diagnosis:**  Strength = Functional (shares full ModuleState knowledge),
Distance = 2 (gui↔core), Volatility = HIGH.  `EFFORT = 3 × 2 × HIGH`.

**Fix:**  Inject `ModuleState` at construction time; add an `EventBus` for change notifications
(see §4.2).

---

### 3.3  ⚠️  HIGH — Duplicate resource-lookup in `mcp/state.py` + `mcp/_indexer.py`

**Symptoms:**
- `mcp/state.py` has its own `KotorInstallation` class with
  `list_modules()`, `list_override()`, and TLK/KEY detection.
- `mcp/_indexer.py` has `build_index()` which re-implements the same KEY/ERF walking
  already in `formats.archives.ResourceManager`.
- Two separate codepaths diverge whenever archive reading changes.

**Khononov diagnosis:**  Model coupling duplicated across a distance boundary (formats ↔ mcp).
Volatility = MEDIUM.  This is the *Coupling Godzilla* anti-pattern: two modules share the
same domain model (resource lookup) without a shared contract.

**Fix:**  `mcp/state.py` should build a `ResourceManager` and delegate to it; `_indexer.py`
becomes a thin adapter (see §4.3).

---

### 3.4  MEDIUM — `viewport.py` imports concrete types from `formats.*` and `engine.*`

**Symptoms:**
- `viewport.py` imports `MeshData`, `MeshNode`, `MDLParser` (concrete parser types),
  `GITData`, `GITPlaceable`, etc.
- Any change to the parser data model forces a viewport rewrite.

**Khononov diagnosis:**  Model coupling (strength 2), Distance 2, Volatility MEDIUM.
Acceptable in its current form but should be moved toward Contract coupling via
`ResourcePort` (see §4.4) so that the viewport requests bytes and receives them without
knowing which reader is used.

---

### 3.5  MEDIUM — `main_window.py` is a 2 408-line God Object

**Symptoms:**
- Handles menus, toolbars, statusbar, layout, IPC bridge lifecycle, game-dir management,
  module I/O, undo/redo forwarding, mod packaging, walkmesh loading, LYT importing, and
  room assembly.
- Imports 10+ gui sub-panels and 5+ non-gui modules directly.
- High connascence-of-name: renaming any signal/slot in any panel touches MainWindow.

**Fix (incremental):**  Extract `AppController` (application-use-case coordinator) from
`MainWindow` (pure presentation).  This reduces Distance for the business-logic parts
from 2 (gui) to 1 (controller next to core).

---

## 4  Recommended Changes (Prioritised)

### 4.1  Extract `ModuleIO` — Separation of Concerns (P0)

Create `gmodular/core/module_io.py`.

```python
class ModuleIO:
    """Loads module data from disk / archive. Pure I/O, no state mutations."""

    def load_from_mod(self, mod_path: str, extract_dir=None) -> "ModuleLoadResult":
        ...

    def load_from_files(self, git, are, ifo) -> "ModuleLoadResult":
        ...
```

`ModuleState.load_from_mod` becomes a one-liner:
```python
def load_from_mod(self, path, extract_dir=None):
    result = ModuleIO().load_from_mod(path, extract_dir)
    self._apply(result)
    self._emit_change()
```

**Coupling improvement:**
- ModuleState drops imports of `ERFReader`, `EXT_TO_TYPE`, `GFFReader` → Strength drops
  from Functional to Model.
- `ModuleIO` is a pure function object (testable without Qt, without state).

---

### 4.2  Inject `ModuleState`; introduce `EventBus` (P0)

```python
# core/events.py  — lightweight typed event bus
class EventBus:
    def subscribe(self, event_type: str, callback: Callable): ...
    def publish(self, event_type: str, **kwargs): ...

# Published events (contracts — stable strings)
MODULE_CHANGED   = "module.changed"
OBJECT_SELECTED  = "module.object_selected"
GAME_DIR_CHANGED = "app.game_dir_changed"
```

`ViewportWidget.__init__(self, state: ModuleState, bus: EventBus)` — no more
`get_module_state()` calls inside methods.  Same for `InspectorPanel`.

**Coupling improvement:**
- Common coupling → Contract coupling (pub/sub string contracts).
- Viewport becomes testable with a stub `ModuleState`.

---

### 4.3  `mcp/state.py` delegates to `ResourceManager` (P1)

```python
# mcp/state.py
class KotorInstallation:
    def __init__(self, game: str, path: str):
        self.game = game
        self.path = path
        self._rm: Optional[ResourceManager] = None

    def resource_manager(self) -> ResourceManager:
        if self._rm is None:
            self._rm = ResourceManager()
            self._rm.set_game(self.path, self.game)
        return self._rm
```

`_indexer.py`'s `build_index()` is replaced by `KotorInstallation.resource_manager()`.
MCP tools call `inst.resource_manager().get(resref, res_type)` directly.

**Coupling improvement:**
- Eliminates 100+ lines of duplicated archive-walking.
- Both the desktop app and MCP tools share the same resolution priority order.

---

### 4.4  Introduce `ResourcePort` Protocol (P1)

```python
# formats/resource_port.py
from typing import Protocol, Optional, List

class ResourcePort(Protocol):
    """Contract for any resource provider.  Depend on this, not ResourceManager."""
    def get(self, resref: str, res_type: int) -> Optional[bytes]: ...
    def get_file(self, resref: str, ext: str) -> Optional[bytes]: ...
    def list_resources(self, res_type: int) -> List[str]: ...
```

`ViewportWidget`, `MCP tools`, and `ModuleState` type-annotate against `ResourcePort`
instead of the concrete `ResourceManager`.  Tests inject a `FakeResourceManager`.

---

### 4.5  Split `main_window.py` → `AppController` + `MainWindow` (P2)

```
gui/
├── app_controller.py   # use-case coordinator (no Qt widgets)
│   ├── open_module(path)
│   ├── save_module()
│   ├── set_game_dir(path)
│   └── validate_module()
└── main_window.py      # pure presentation; calls AppController
```

---

## 5  Khononov Metrics — Before vs After

| Coupling Hotspot                      | Before: S×D×V | After: S×D×V | Change |
|---------------------------------------|---------------|--------------|--------|
| ModuleState ↔ archives (load_from_mod)| 3×2×H = 6H   | 1×1×H = H    | −5H    |
| viewport ↔ module_state (singleton)   | 3×2×H = 6H   | 1×2×M = 2M   | −4H    |
| mcp._indexer ↔ formats.archives       | 3×3×M = 9M   | 1×3×M = 3M   | −6M    |
| viewport ↔ formats.* (model types)    | 2×2×M = 4M   | 1×2×L = 2L   | −2M    |

H=High, M=Medium, L=Low volatility (ordinal 3/2/1).

---

## 6  Implementation Plan

| Priority | Item                                    | Files Changed                                  | Lines Est. |
|----------|-----------------------------------------|------------------------------------------------|------------|
| P0       | Extract `ModuleIO`                      | `core/module_io.py` (new), `core/module_state.py` | +120 / −180 |
| P0       | EventBus + constructor injection        | `core/events.py` (new), `gui/viewport.py`, `gui/inspector.py`, `gui/main_window.py` | +60 / −30 |
| P1       | `ResourcePort` protocol                 | `formats/resource_port.py` (new), minor type annotations | +30 |
| P1       | MCP delegates to `ResourceManager`      | `mcp/state.py`, `mcp/_indexer.py`, `mcp/tools/*.py` | −120 |
| P2       | `AppController` split                   | `gui/app_controller.py` (new), `gui/main_window.py` | +200 / −400 |

---

## 7  Fractal Application (Khononov Ch. 12)

Khononov's fractal principle: apply the same coupling model at **every abstraction level**.

- **Method level:**  `ModuleState._autosave_tick` should not check `self.git is None` —
  that is a hidden control-flow dependency on field order.  Extract `_is_saveable()`.
- **Class level:**  `GITData.duplicate_object()` has 30 lines of per-type logic; a
  `Duplicatable` protocol would eliminate the type-dispatch.
- **Package level:**  `gmodular.mcp` must not import `gmodular.gui` (currently it does not —
  good); enforce this with an import-boundary test.
- **System level:**  MCP ↔ GModular desktop share `formats.*` as a stable contract layer —
  this is correct Contract coupling at the right distance.

---

## 8  What NOT to Change

Per Khononov, **do not reduce coupling where distance is already 1**:

- `formats/gff_reader.py` directly importing `gff_types.py` — same package, stable types.
  Introducing an interface here would be over-engineering.
- `engine/mdl_renderer.py` importing `formats/mdl_parser.py` — co-located concern,
  low volatility.  Keep as-is.
- `ipc/bridges.py` hardcoded ports — this is *External coupling* but the ports are
  part of a stable PIPELINE_SPEC contract.  Document them, don't abstract them.

---

## 9  Qt Compatibility Layer (qtpy)

**Change (2026-03):** All raw `PyQt5` imports replaced with `qtpy` equivalents across 21 files.

| Before | After | Rationale |
|--------|-------|-----------|
| `from PyQt5.QtCore import pyqtSignal` | `from qtpy.QtCore import Signal` | qtpy normalises PyQt5/PySide2/Qt6 signal API |
| `from PyQt5.QtWidgets import …` | `from qtpy.QtWidgets import …` | Single source of truth; Qt6 upgrade is one `pip install` |
| `pyqtSlot` | `Slot` | qtpy alias |

`qtpy` is a thin shim that forwards to whichever Qt binding is installed (PyQt5,
PyQt6, PySide2, PySide6) using the same import path.  Adding `QT_API=pyqt6` to
the environment will give us Qt6 improvements (HiDPI, Wayland, performance)
with zero code changes.

---

## 10  New Subsystems Added (2026-03)

### 10.1  DLG Visual Node-Graph Editor (`gui/dlg_editor.py`)

Implements the full KotOR conversation-tree editor described in PIPELINE_SPEC.md §3.4.

Architecture:
```
DLGGraphData    ← pure-Python data model (no Qt dep), serialisable to/from JSON + GFF
DLGNodeData     ← individual node (Entry = NPC line, Reply = player option)
DLGCanvas       ← QGraphicsScene subclass — nodes as DLGNodeItem, edges as DLGEdgeItem  
DLGEditorPanel  ← QWidget container: canvas + properties + toolbar
```

MCP tools (lazy-imported to honour mcp→gui boundary):
- `kotor_dlg_parse`     — GFF bytes → graph JSON
- `kotor_dlg_add_node`  — add Entry/Reply to graph
- `kotor_dlg_link_nodes`— connect two nodes
- `kotor_dlg_summarize` — human-readable conversation walkthrough

### 10.2  NWScript IPC Bridge (`ipc/nwscript_bridge.py`)

```
NWSScriptBridge
  compile(nss_source)     → CompileResult(ncs_bytes, errors)
  decompile(ncs_bytes)    → CompileResult(nss_source, errors)
  check_syntax(source)    → {valid, errors, warnings}
  format_nss(source)      → formatted string
```

Priority chain (compile): external nwnnsscomp → PyKotor PLY parser → error  
Priority chain (decompile): external DeNCS → PyKotor decompiler → disasm fallback  

MCP tools: `kotor_compile_nss`, `kotor_decompile_ncs`, `kotor_nss_check`, `kotor_nss_format`

### 10.3  Format Library Additions

| Format | Read | Write | Notes |
|--------|------|-------|-------|
| 2DA binary | ✅ (was text-only) | ✅ | `TwoDAData.from_bytes`, `write_2da_binary` |
| TPC | ✅ | ✅ NEW | `write_tpc_from_rgba`, `write_tpc_from_tga` |
| LYT | ✅ | ✅ improved | canonical beginlayout/donelayout |
| NCS | ✅ fixed | — | RETN, SAVEBP, NOP, RESTOREBP zero-operand fix |
| GFF diff | — | — | `kotor_gff_diff` MCP tool |
| 2DA diff | — | — | `kotor_2da_diff` (mirrors Kotor.NET Diff2DA.cs) |
| TLK diff | — | — | `kotor_tlk_diff` |
| GFF patch | — | — | `kotor_patch_gff` |

---

## 11  Format Library — Complete Status (2026-03-18)

| Format | Ext  | Read | Write | Location |
|--------|------|------|-------|----------|
| GFF V3.2 | .gff/.are/.git/.ifo/… | ✅ | ✅ | formats/gff_reader.py + gff_writer.py |
| ERF/MOD/SAV | .erf/.mod | ✅ | ✅ | formats/archives.py |
| RIM | .rim | ✅ | — | formats/archives.py |
| BIF/KEY | .bif/.key | ✅ | — | formats/archives.py |
| MDL/MDX | .mdl | ✅ | — | formats/mdl_parser.py |
| WOK/BWM | .wok | ✅ | ✅ | formats/wok_parser.py |
| TPC | .tpc | ✅ | ✅ | formats/tpc_reader.py |
| 2DA binary | .2da | ✅ | ✅ | formats/kotor_formats.py |
| 2DA ASCII | .2da | ✅ | ✅ | formats/twoda_loader.py |
| TLK | .tlk | ✅ | ✅ | formats/kotor_formats.py |
| LYT | .lyt | ✅ | ✅ | formats/lyt_vis.py |
| VIS | .vis | ✅ | ✅ | formats/kotor_formats.py |
| SSF | .ssf | ✅ | ✅ | formats/kotor_formats.py |
| LIP | .lip | ✅ | ✅ | formats/kotor_formats.py |
| TXI | .txi | ✅ | ✅ | formats/kotor_formats.py |
| PTH | .pth | ✅ | ✅ | formats/kotor_formats.py (via GFF) |
| NCS | .ncs | ✅ | ✅ NEW | formats/kotor_formats.py |
| LTR | .ltr | ✅ NEW | ✅ NEW | formats/kotor_formats.py |
| DLG | .dlg | ✅ (via GFF) | ✅ (via GFF) | gui/dlg_editor.py data model |

---

## 12  Roadmap (Prioritised, 2026-03-18)

### DONE ✅
- [x] Full GFF V3.2 reader/writer (all 18 field types)
- [x] MDL/MDX parser (model/mesh rendering)
- [x] WOK/BWM parser + writer + AABB tree
- [x] TPC reader + writer
- [x] ERF/RIM/MOD archive support + module import dialog
- [x] 2DA binary read/write (Kotor.NET compatible)
- [x] TLK / SSF / LIP / TXI / VIS / PTH / LYT formats
- [x] **LTR read/write** (name-generator Markov chain)
- [x] **NCS write** (re-assembler, patch-level)
- [x] ModernGL 3D viewport with Phong lighting, frustum culling, walkmesh overlay
- [x] Room assembly grid (drag-and-drop, LYT/VIS generation)
- [x] Inspector with TLK + 2DA lookups, script pencil-button IPC
- [x] DLG visual node-graph editor (`gui/dlg_editor.py`)
- [x] NWScript IPC bridge (`ipc/nwscript_bridge.py`)
- [x] **qtpy compatibility layer** (Qt5+Qt6 ready — 21 files migrated, `main.py` fixed)
- [x] **Qt Designer `.ui` files** — `inspector.ui`, `twoda_editor.ui`, `dlg_editor.ui`, `mod_import_dialog.ui`; `ui_loader.py` wrapper; GModular.spec bundles `ui/`; 33 new XML validity + spec tests
- [x] 79 MCP tools (composite, installation, discovery, game data, archives, formats, scripts, diff/patch, DLG, NWScript)
- [x] Architecture boundary test (`test_mcp_does_not_import_gui`)
- [x] 1,933 tests, 100% pass rate

### NEXT 🔜 (Near-term, high-value)
- [ ] **Migrate panels to `load_ui()`** — wire `inspector.py`, `twoda_editor.py`, `dlg_editor.py` to their `.ui` files (replaces manual layout boilerplate)
- [ ] **Add `.ui` for `tutorial_dialog.py`** (1,310 lines), `viewport.py` controls, `walkmesh_editor.py`
- [ ] **`ModuleIO` service** — extract 200-line ERF loading block out of `ModuleState` into `core/module_io.py` (see §3.1 hotspot)
- [ ] **EventBus + constructor injection** — eliminate 12 `get_module_state()` call-sites in `viewport.py` (see §3.2 hotspot)
- [ ] **DLG GFF round-trip test** — verify full GFF → DLGGraphData → GFF pipeline with a real `.dlg` from KotOR 1
- [ ] **MDL writer** — export `.mdl`/`.mdx` for custom room models (currently read-only)

### FUTURE 🗓️ (Medium-term)
- [ ] **FXAA/SSAO** post-processing passes in ModernGL viewport
- [ ] **Animation playback** — MDL skeletal/animation state machine in `engine/`
- [ ] **PyKotor NWScript compiler integration** — wire PLY-based compiler into `nwscript_bridge.py` without requiring external `nwnnsscomp`
- [ ] **Qt6 full migration** — set `QT_API=pyqt6` and audit for breaking API differences (e.g., `exec_()` → `exec()`, `QRegExp` removal)
- [ ] **GFF XML/JSON import-export** — round-trip via GFF ↔ JSON for AI agent editing
- [ ] **Steam Workshop packager** — wrap module packager output in Workshop manifest format

