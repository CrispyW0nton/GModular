# GModular — Modular KotOR 1 & 2 Toolkit

> **A professional, modular editor for Star Wars: Knights of the Old Republic 1 & 2 modules.**

GModular is a standalone Python/Qt desktop application that provides a full authoring environment for KotOR module files (`.git`, `.are`, `.ifo`). It is designed to work alongside [GhostScripter](https://github.com/CrispyW0nton/GhostScripter) (NWScript IDE) and [GhostRigger](https://github.com/CrispyW0nton/GhostRigger) (model rigging tool) via a lightweight IPC bridge.

---

## Table of Contents

1. [Features](#features)
2. [Screenshots](#screenshots)
3. [Architecture](#architecture)
4. [Project Structure](#project-structure)
5. [Installation](#installation)
6. [Usage](#usage)
7. [GFF Format Support](#gff-format-support)
8. [IPC Integration](#ipc-integration)
9. [Phase Roadmap](#phase-roadmap)
10. [Running Tests](#running-tests)
11. [Contributing](#contributing)
12. [License](#license)

---

## Features

### ✅ Implemented (MVP)

| Feature | Status |
|---|---|
| GFF V3.2 binary reader (GIT/ARE/IFO) | ✅ Complete |
| GFF V3.2 binary writer — BFS two-phase encoding | ✅ Complete |
| All GFF field types (BYTE → ORIENTATION) | ✅ Complete |
| GIT round-trip (Placeables, Creatures, Doors, Waypoints, Triggers) | ✅ Complete |
| Module state (undo/redo, dirty flag, autosave) | ✅ Complete |
| Undoable commands (Place, Delete, Move, Rotate, ModifyProperty) | ✅ Complete |
| Asset Palette panel (search, tabs, custom ResRef) | ✅ Complete |
| Scene Outline / Hierarchy panel | ✅ Complete |
| Inspector Panel (editable object properties) | ✅ Complete |
| 3D Viewport (ModernGL orbit camera, picking, placement) | ✅ Complete |
| Walkmesh Editor panel | ✅ Complete |
| IPC Bridges — GhostScripter (port 5002) | ✅ Complete |
| IPC Bridges — GhostRigger (port 5001) | ✅ Complete |
| IPC Callback Server on port 5003 | ✅ Complete |
| File watcher (watchdog) for .ncs / .mdl changes | ✅ Complete |
| Resource Manager (Override + Modules directory scan) | ✅ Complete |
| Dark theme (VS Code-inspired) | ✅ Complete |
| Settings persistence (`~/.gmodular/settings.json`) | ✅ Complete |
| Full test suite (44 tests, 100% pass) | ✅ Complete |

### 🔜 Planned (see Roadmap)

- BIF/KEY archive parsing (Phase 3)
- Full PyKotor integration (Phase 3)
- 3D model rendering via MDL parser (Phase 4)
- NWScript compiler integration via GhostScripter (Phase 4)
- Full walkmesh bake & export (Phase 4)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         GModular GUI                            │
│  ┌──────────────┐  ┌──────────────────────────┐  ┌───────────┐ │
│  │ Asset Palette│  │     3D Viewport           │  │ Inspector │ │
│  │  (search +   │  │  (ModernGL orbit camera,  │  │  Panel    │ │
│  │   tabs)      │  │   object picking, grid)   │  │           │ │
│  └──────────────┘  └──────────────────────────┘  └───────────┘ │
│  ┌──────────────┐  ┌──────────────────────────┐                 │
│  │ Scene        │  │ Bottom tabs:             │                 │
│  │ Outline      │  │  Log | Walkmesh | Area   │                 │
│  └──────────────┘  └──────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────┘
          │                                    │
          ▼                                    ▼
┌─────────────────┐                ┌──────────────────────┐
│   ModuleState   │                │   IPC Bridges        │
│  (GITData,      │                │  GhostScripter :5002 │
│   AREData,      │                │  GhostRigger   :5001 │
│   IFOData,      │                │  Callback Srv  :5003 │
│   undo/redo)    │                └──────────────────────┘
└─────────────────┘
          │
          ▼
┌─────────────────┐
│  GFF Formats    │
│  gff_reader.py  │
│  gff_writer.py  │
│  gff_types.py   │
└─────────────────┘
```

### GFF BFS Two-Phase Writer

KotOR's GFF format requires all struct indices to be stable before any field data can reference them. GModular's writer uses a **breadth-first two-phase algorithm**:

1. **Phase 1 — BFS Collection**: Walk the tree breadth-first, assign a stable integer index to every `GFFStruct`.
2. **Phase 2 — Field Encoding**: Encode all fields in BFS order; LIST and STRUCT fields can now safely embed the pre-assigned indices.

This ensures the binary layout is always correct and identical to what KotOR's engine expects.

---

## Project Structure

```
GModular/
├── gmodular/
│   ├── __init__.py           # Package root (version 1.0.0-MVP)
│   ├── core/
│   │   ├── __init__.py
│   │   └── module_state.py   # ModuleProject, ModuleState, undo commands
│   ├── formats/
│   │   ├── __init__.py
│   │   ├── gff_types.py      # GFF data model + KotOR GIT/ARE/IFO types
│   │   ├── gff_reader.py     # GFF V3.2 binary reader
│   │   ├── gff_writer.py     # GFF V3.2 binary writer (BFS two-phase)
│   │   └── archives.py       # BIF/ERF/RIM archive reader (stub/Phase 3)
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── main_window.py    # MainWindow — full application shell
│   │   ├── viewport.py       # ViewportWidget (ModernGL 3D viewport)
│   │   ├── inspector.py      # InspectorPanel — property editor
│   │   ├── asset_palette.py  # AssetPalette — browse & place assets
│   │   ├── scene_outline.py  # SceneOutlinePanel — object hierarchy tree
│   │   └── walkmesh_editor.py# WalkmeshPanel — walkmesh editing tools
│   ├── ipc/
│   │   ├── __init__.py
│   │   ├── bridges.py        # GhostScripterBridge, GhostRiggerBridge
│   │   └── callback_server.py# Flask callback server on port 5003
│   └── utils/
│       ├── __init__.py
│       └── resource_manager.py # KotOR resource discovery & access
├── tests/
│   ├── __init__.py
│   └── test_gff.py           # 44 pytest tests (all passing)
├── assets/                   # Icons and static assets
├── resources/                # Qt resource files
├── main.py                   # Application entry point
├── requirements.txt          # Python dependencies
├── setup.py                  # Package configuration
└── README.md                 # This file
```

---

## Installation

### Prerequisites

- Python 3.10+
- A KotOR 1 or KotOR 2 installation (for live game data)

### Steps

```bash
# Clone the repository
git clone https://github.com/CrispyW0nton/GModular.git
cd GModular

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate.bat    # Windows

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

### Dependencies

| Package | Purpose |
|---|---|
| `PyQt5` | Desktop GUI framework |
| `moderngl` | OpenGL 3.3+ context for 3D viewport |
| `PyGLM` | Math library (vectors, matrices, quaternions) |
| `watchdog` | File system watcher for script / model hot-reload |
| `flask` | Lightweight IPC callback server (port 5003) |
| `pytest` | Test runner |

---

## Usage

### First Launch

1. **Set Game Directory** → `Tools → Set Game Directory` (or toolbar button)  
   Select the folder containing `chitin.key`.
2. **Load Assets** → `Tools → Load Game Assets`  
   Populates the Asset Palette from the game's Override directory.
3. **New Module** → `File → New Module`  
   Enter a ResRef, name and description.

### Editing a Module

- **Place Objects**: Click an asset in the palette → click `Place Selected`, or double-click.  
  Then click in the 3D viewport to choose a location.
- **Select Objects**: Left-click an object in the viewport.  
  Properties appear in the Inspector panel on the right.
- **Scene Hierarchy**: The Scene Outline panel (top-left) lists all objects.  
  Right-click for context menu (select / delete).
- **Undo / Redo**: `Ctrl+Z` / `Ctrl+Shift+Z` (or Edit menu).
- **Save**: `Ctrl+S` saves the `.git` file.  
  `Ctrl+Shift+S` saves to a new path.

### Viewport Controls

| Action | Input |
|---|---|
| Orbit | Right-mouse drag |
| Pan | Middle-mouse drag |
| Zoom | Scroll wheel |
| Fly | `W` / `A` / `S` / `D` |
| Frame All | `F` key or toolbar button |
| Select | Left-click on object |
| Delete selected | `Delete` key |

---

## GFF Format Support

GModular implements the **GFF V3.2** binary format used by KotOR 1 & 2 (and other Aurora-engine games).

### Supported Field Types

| Type | Encoding | Notes |
|---|---|---|
| `BYTE` | inline uint8 | |
| `CHAR` | inline int8 | |
| `WORD` | inline uint16 | |
| `SHORT` | inline int16 | |
| `DWORD` | inline uint32 | |
| `INT` | inline int32 | |
| `DWORD64` | field data uint64 | |
| `INT64` | field data int64 | |
| `FLOAT` | inline float32 (as uint bits) | |
| `DOUBLE` | field data float64 | |
| `CEXOSTRING` | field data length-prefixed UTF-8 | |
| `RESREF` | field data length-prefixed ASCII ≤16 | |
| `CEXOLOCSTRING` | field data multi-language | English extracted |
| `VOID` | field data length-prefixed blob | |
| `STRUCT` | inline struct index | BFS-ordered |
| `LIST` | list-indices byte offset | BFS-ordered |
| `ORIENTATION` | field data 4×float32 (quaternion) | |
| `VECTOR` | field data 3×float32 | |
| `STRREF` | inline uint32 | TLK reference |

### Supported File Types

| File | Type tag | Description |
|---|---|---|
| `.git` | `GIT ` | Game Instance Table — object placements |
| `.are` | `ARE ` | Area metadata (fog, music, tileset, …) |
| `.ifo` | `IFO ` | Module metadata & entry point |
| `.dlg` | `DLG ` | Dialog tree (read-only for now) |

---

## IPC Integration

GModular connects to its sibling tools via HTTP:

| Service | Port | Direction | Purpose |
|---|---|---|---|
| GhostScripter | 5002 | GModular → GS | Open / compile NWScript files |
| GhostRigger   | 5001 | GModular → GR | Request model rigs |
| GModular CB   | 5003 | GS / GR → GM | Receive compile results & model updates |

Both bridges poll their targets every 8 seconds and emit Qt signals (`connected`, `disconnected`, `scripts_updated`, etc.) that drive UI state.

The callback server (`ipc/callback_server.py`) runs a minimal Flask app on port 5003 and posts events back to the GUI via a thread-safe Qt signal queue.

---

## Phase Roadmap

| Phase | Focus | Status |
|---|---|---|
| **1 — Foundation** | Project scaffolding, GFF types, reader/writer, module state, GUI shell | ✅ ~100% |
| **2 — Editor MVP** | Viewport, Inspector, Asset Palette, Scene Outline, Walkmesh Editor, IPC | ✅ ~85% |
| **3 — Game Integration** | BIF/KEY archive parsing, full resource manager, live 2DA/template loading | 🔜 ~10% |
| **4 — Advanced Tools** | MDL 3D rendering, walkmesh bake, NWScript compilation pipeline, DLG editor | 🔜 0% |
| **5 — Polish & Release** | Packaging (PyInstaller), documentation, KotOR 2 TSL-specific features | 🔜 0% |

### Overall Completion: ~63%

---

## Running Tests

```bash
# From the project root
python -m pytest tests/ -v

# Expected output:
# 44 passed in 0.14s
```

The test suite covers:

- GFF header layout and binary correctness
- All 18 scalar / string / composite field types (round-trip)
- Nested STRUCTs and multi-item LIST fields
- Full GIT round-trips for Placeables, Creatures, Doors, Waypoints, Triggers
- Ambient audio fields
- GFFWriter API (idempotency, file write)
- GFFReader API (from_bytes, from_file, caching)

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes, add tests for any new format behaviour
4. Run `python -m pytest tests/` — all tests must pass
5. Open a pull request against `main`

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*GModular is a community project and is not affiliated with or endorsed by LucasArts, BioWare, Obsidian Entertainment, or Disney.*
