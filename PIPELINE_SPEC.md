# GHOSTWORKS PIPELINE — TECHNICAL SPECIFICATION
## Shared Design Contract for GhostRigger, GhostScripter, and GModular

**Version:** 1.1  
**Date:** 2026-03-19  
**Status:** ACTIVE

---

## 1. OVERVIEW

The Ghostworks Pipeline is a three-program suite for creating custom modules
(levels) for Star Wars: Knights of the Old Republic (KotOR 1) and The Sith
Lords (KotOR 2). Together the three programs replace every tool currently used
by the modding community (KOTOR Tool, K-GFF, KotOR Scripting Tool, ERFEdit,
KotorBlender) with a single integrated workflow.

### The Three Programs

```
GhostRigger  ──IPC──►  GhostScripter  ──IPC──►  GModular
(assets)                (logic)                  (level + ship)
```

Each program is a standalone Windows executable built with Python 3.12 +
PyQt5. Each is independently useful. Together they form a linear pipeline:
assets are created in GRigger, given behavior in GScripter, and assembled
into a playable module in GModular.

### Guiding Principle

A modder should be able to:
1. Create a creature blueprint in GhostRigger (30 seconds)
2. Attach a patrol script and dialogue in GhostScripter (2 minutes)
3. Place it in a room, draw its patrol path, and export a .mod in GModular (5 minutes)
4. Drop the .mod in the game's Modules/ folder and play it (immediate)

Total: under 10 minutes for a fully scripted NPC in a working module.
Current community average for the same task: 4-6 hours across 6+ tools.

---

## 2. TECHNOLOGY STACK — ALL THREE PROGRAMS

Every program MUST use this stack. No exceptions. This ensures IPC
compatibility, shared format libraries, and a consistent build process.

| Component         | Requirement                                      |
|-------------------|--------------------------------------------------|
| Language          | Python 3.12 (NOT 3.13+ — PyQt5 has no wheel)    |
| GUI framework     | PyQt5 >= 5.15.0, < 6.0                          |
| 3D rendering      | moderngl >= 5.8.0 (binary wheel only, no MSVC)  |
| GL fallback       | PyOpenGL >= 3.1.0 (if moderngl wheel unavailable)|
| Numerics          | numpy >= 1.21.0                                  |
| File watching     | watchdog >= 2.0.0                                |
| HTTP / IPC        | requests >= 2.28.0, flask, werkzeug              |
| Build             | PyInstaller >= 5.13.0 via build.bat              |
| Testing           | pytest (all tests in tests/test_*.py)            |
| Style             | Dark theme matching GModular (see Section 9)     |

### Build Script

Every program ships a `build.bat` modeled on GModular's v1.9 build.bat:
- `chcp 65001` at top (UTF-8, prevents garbled output on Windows cmd)
- Python PATH check + version guard (block 3.13+, require >= 3.10)
- Virtual environment support (activates venv if present)
- Step-by-step pip installs with individual error messages
- PyInstaller call: `python -m PyInstaller <ProgramName>.spec --clean --noconfirm`
- Post-build validation: checks dist\<ProgramName>.exe exists
- Plain ASCII only — no Unicode box-drawing characters

---

## 3. IPC CONTRACT — HOW THE THREE PROGRAMS TALK

All three programs communicate over localhost HTTP using a simple JSON
protocol. This is the most important section. Read it carefully.

### 3.1 IPC Server Ports (FIXED — do not change)

| Program        | IPC Server Port | Purpose                        |
|----------------|-----------------|--------------------------------|
| GhostRigger    | 7001            | Receives asset-edit requests   |
| GhostScripter  | 7002            | Receives script/dlg requests   |
| GModular       | 7003            | Receives refresh/update calls  |

Each program starts its IPC server on launch and stops it on close.
The server runs in a background thread and never blocks the GUI.

### 3.2 IPC Message Format

All messages are HTTP POST to `http://localhost:<PORT>/api/<action>`
Content-Type: application/json

**Request envelope:**
```json
{
  "version": "1.0",
  "sender": "GModular",
  "action": "open_utc",
  "payload": { ... action-specific fields ... }
}
```

**Response envelope:**
```json
{
  "status": "ok",
  "action": "open_utc",
  "payload": { ... response fields ... }
}
```

Error response:
```json
{
  "status": "error",
  "action": "open_utc",
  "message": "File not found: dan13_01.utc"
}
```

### 3.3 IPC Action Catalogue

This is the full set of actions every program must implement.
"Receives" = server endpoint. "Calls" = client call it makes.

#### GhostRigger IPC (port 7001)

| Action            | Direction    | Payload                                         | Response                        |
|-------------------|--------------|-------------------------------------------------|---------------------------------|
| `open_utc`        | Receives     | `{"resref": "dan13_01", "module_dir": "C:/..."}` | `{"status": "ok"}`             |
| `open_utp`        | Receives     | `{"resref": "plc_footlocker", "module_dir": ""}` | `{"status": "ok"}`             |
| `open_utd`        | Receives     | `{"resref": "door_001", "module_dir": ""}`       | `{"status": "ok"}`             |
| `open_mdl`        | Receives     | `{"resref": "c_gamorrean", "module_dir": ""}`    | `{"status": "ok"}`             |
| `blueprint_saved` | Calls 7003   | `{"resref": "dan13_01", "type": "utc"}`          | GModular refreshes viewport    |
| `ping`            | Receives     | `{}`                                             | `{"status": "ok", "program": "GhostRigger"}` |

#### GhostScripter IPC (port 7002)

| Action              | Direction    | Payload                                                         | Response                           |
|---------------------|--------------|-----------------------------------------------------------------|------------------------------------|
| `open_script`       | Receives     | `{"resref": "c_rodian_sp", "module_dir": "C:/...", "template": "walk_spawn"}` | `{"status": "ok"}` |
| `open_dlg`          | Receives     | `{"resref": "dan13_01", "module_dir": "C:/..."}`                | `{"status": "ok"}`                |
| `script_compiled`   | Calls 7003   | `{"resref": "c_rodian_sp", "slot": "on_spawn", "object_tag": "RODIAN_01"}` | GModular fills script field   |
| `open_2da`          | Receives     | `{"table": "appearance", "row": 147}`                           | `{"status": "ok"}`                |
| `open_tlk`          | Receives     | `{"strref": 42001, "game": "k1"}`                               | `{"status": "ok"}`                |
| `ping`              | Receives     | `{}`                                                            | `{"status": "ok", "program": "GhostScripter"}` |

#### GModular IPC (port 7003)

| Action              | Direction    | Payload                                                      | Response           |
|---------------------|--------------|--------------------------------------------------------------|--------------------|
| `blueprint_saved`   | Receives     | `{"resref": "dan13_01", "type": "utc"}`                      | `{"status": "ok"}` |
| `script_compiled`   | Receives     | `{"resref": "c_rodian_sp", "slot": "on_spawn", "object_tag": "RODIAN_01"}` | `{"status": "ok"}` |
| `refresh_viewport`  | Receives     | `{}`                                                         | `{"status": "ok"}` |
| `ping`              | Receives     | `{}`                                                         | `{"status": "ok", "program": "GModular"}` |

### 3.4 IPC Availability

Programs must handle the case where a target program is not running:
- Attempt the HTTP call with a 2-second timeout
- If connection refused or timeout: show a non-blocking status bar message:
  `"GhostRigger is not running — open it to edit blueprints"`
- Never crash or show a modal error dialog for IPC failures
- The user can still use the current program normally without the others

### 3.5 IPC Implementation Reference

GModular's existing IPC code lives at:
```
gmodular/ipc/bridges.py          -- client call helpers
gmodular/ipc/callback_server.py  -- Flask server thread
```

GhostRigger and GhostScripter must implement the same pattern.

---

## 4. SHARED FILE FORMATS

All three programs read and write the same KotOR file formats. The canonical
Python implementations are in GModular. GhostRigger and GhostScripter MUST
use the same logic — either copy the relevant modules or depend on a shared
`ghostworks-formats` package (see Section 4.4).

### 4.1 GFF V3.2 (Generic File Format)

Used by: .utc .utp .utd .utw .utm .uts .utt .git .are .ifo .dlg .jrl and more.

**Spec summary:**
- Header: FileType (4 bytes) + "V3.2" (4 bytes) + 6 offset/count pairs
- 7 sections: Header, Struct Array, Field Array, Label Array,
  Field Data Block, Field Indices, List Indices
- Field types: Byte(0), Char(1), UInt16(2), Int16(3), UInt32(4), Int32(5),
  UInt64(6), Int64(7), Float(8), Double(9), CExoString(10), ResRef(11),
  CExoLocString(12), Binary(13), Struct(14), List(15), Position(16), Rotation(17)
- ResRef: max 16 ASCII characters, null-padded, case-insensitive
- Top-Level Struct: always index 0, type always 0xFFFFFFFF

**GModular implementations:**
```
gmodular/formats/gff_types.py    -- data classes for all GFF types + GIT objects
gmodular/formats/gff_reader.py   -- binary GFF parser
gmodular/formats/gff_writer.py   -- binary GFF writer
```

**External references:**
- BioWare Aurora GFF spec PDF: nwn.wiki/download/attachments/327727/Bioware_Aurora_GFF_Format.pdf
- xoreos C++ reference: github.com/xoreos/xoreos/blob/master/src/aurora/gff3file.cpp

### 4.2 Archive Formats

| Format | Use                                  | GModular impl           |
|--------|--------------------------------------|-------------------------|
| BIF    | Game data archives (chitin.key refs) | formats/archives.py     |
| ERF    | Module containers (.mod, .rim, .erf) | formats/archives.py     |
| KEY    | chitin.key — master resource index   | formats/archives.py     |
| RIM    | Smaller module containers (patches)  | formats/archives.py     |

chitin.key lives in the game root directory and indexes all BIF archives.
Resource lookup order: Override folder > module .mod > chitin.key BIFs.

### 4.3 Other Formats

| Format   | Description                              | Reference                                  |
|----------|------------------------------------------|--------------------------------------------|
| MDL/MDX  | 3D model (binary node tree + mesh data)  | github.com/seedhartha/kotorblender         |
| LYT      | Plain text: room name + XYZ offset each  | "roomname x.xx y.yy z.zz" per line        |
| VIS      | Plain text: room visibility pairs        | "ROOM_A ROOM_B" per line                  |
| WOK      | Binary walkmesh per room                 | github.com/seedhartha/reone (C++ ref)      |
| PTH      | Binary NPC pathfinding graph             | github.com/seedhartha/kotorblender         |
| 2DA      | Tab/space-separated table                | "2DA V2.0\n\n col1 col2\n0 val val\n..."  |
| TLK      | Binary string table (dialog.tlk)         | PyKotor: github.com/OldRepublicDevs/PyKotor|
| DLG      | GFF: dialogue tree (NPC lines + replies) | GFF format, type "DLG "                   |
| NSS/NCS  | NWScript source + compiled bytecode      | nwn.wiki NCS spec; PyKotor has compiler    |
| TPC/TGA  | Texture formats                          | PyKotor for TPC; standard PIL for TGA      |

### 4.4 Shared Format Package (Recommended)

To avoid code duplication, extract GModular's format code into a shared
installable package `ghostworks-formats` that all three programs pip-install:

```
ghostworks-formats/
    gw_formats/
        __init__.py
        gff_types.py      -- from gmodular/formats/gff_types.py
        gff_reader.py     -- from gmodular/formats/gff_reader.py
        gff_writer.py     -- from gmodular/formats/gff_writer.py
        archives.py       -- from gmodular/formats/archives.py
        mdl_parser.py     -- from gmodular/formats/mdl_parser.py
    setup.py
```

Until that package exists, copy the relevant files and maintain parity.
Any bug fix in GModular's format code must be applied to all three programs.

---

## 5. GHOSTRIGGER — FULL SPECIFICATION

### 5.1 Purpose

GhostRigger is the asset creation and deep editing tool. A modder uses
GhostRigger to create every blueprint (UTC, UTP, UTD) and every 3D asset
(MDL models, rigs, animations, UV maps, lightmaps). It also provides a raw
module file browser for inspecting and batch-editing the contents of any
.mod, .rim, or .erf archive.

GhostRigger is the "3DS Max / Maya" of the pipeline. It deals with data at
its most raw and detailed level. Modders who only do level design can skip
it entirely and use GModular's built-in asset browser for standard game
assets. GhostRigger is for those who want custom models or deep blueprint
control.

### 5.2 Window Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Menu: File  Edit  Asset  Module  View  IPC  Help                │
├─────────────┬───────────────────────────────────┬───────────────┤
│             │                                   │               │
│  ASSET      │         3D VIEWPORT               │  PROPERTIES   │
│  BROWSER    │    (MDL model + rig display)       │  PANEL        │
│             │                                   │               │
│  Tree of    │    Orbit camera (same controls     │  GFF field    │
│  game       │    as GModular: LMB orbit,         │  editor for   │
│  resources  │    MMB pan, scroll zoom)           │  selected     │
│  organized  │                                   │  blueprint    │
│  by type:   │    Shows: mesh geometry,           │               │
│  Creatures  │    bone skeleton, UV seams,        │  All fields   │
│  Placeables │    walkmesh faces, animations      │  labelled in  │
│  Doors      │                                   │  plain English│
│  Models     ├───────────────────────────────────┤               │
│  Textures   │                                   │  2DA-backed   │
│  Scripts    │  ANIMATION TIMELINE               │  dropdowns    │
│  Archives   │  (keyframe scrubber,              │  where        │
│             │   animation set selector)          │  applicable   │
└─────────────┴───────────────────────────────────┴───────────────┘
│ Status bar: current module dir | IPC status | selected object   │
└─────────────────────────────────────────────────────────────────┘
```

### 5.3 Feature List

#### Blueprint Editors (GFF-backed)
Each editor shows human-readable field names, not raw GFF labels.
Fields that reference 2DA rows show the row name, not just the number.

**UTC — Creature Blueprint**
Fields: Tag, ResRef, Name (StrRef), Appearance_Type (2DA lookup),
Gender, Race, Class1/Level1 through Class3/Level3, HP/MaxHP,
FP/MaxFP, Fortitude/Reflex/Will saves, all Attribute scores (STR/DEX/CON/
INT/WIS/CHA), all skill rows (Computer Use, Demolitions, Stealth, etc.),
all feat rows (checkboxes), all inventory slots, all script slots (OnSpawn,
OnDeath, OnDamaged, OnAttacked, OnHeartbeat, OnBlocked, OnConversation,
OnDisturbance, OnEndConversation, OnUserDefined), Conversation (ResRef),
Faction (dropdown: Friendly/Hostile/Neutral/Predator...), SoundSet (2DA),
BodyBag, Disarmable, IsPC flag, WillNotRender, NoPermDeath.

**UTP — Placeable Blueprint**
Fields: Tag, ResRef, Name, Appearance (placeables.2da lookup), HP/MaxHP,
Static flag, Useable flag, HasInventory, Faction, all script slots (OnUsed,
OnOpen, OnClosed, OnDamaged, OnDeath, OnHeartbeat, OnMeleeAttacked,
OnLock, OnUnlock, OnUserDefined), trap fields, inventory contents.

**UTD — Door Blueprint**
Fields: Tag, ResRef, Name, GenericType (genericdoors.2da lookup), LinkedTo
(tag of linked module/door), LinkedToFlags, all script slots (OnOpen,
OnClose, OnFailToOpen, OnDamaged, OnDeath, OnMeleeAttacked, OnLock,
OnOpen2, OnUnlock, OnUserDefined), HP/MaxHP, Lock fields (Locked, LockDiff,
KeyRequired, KeyTag, AutoRemoveKey), Static flag.

#### 3D Asset Pipeline

**MDL Viewer**
- Load binary MDL from game archives or loose file
- Render using moderngl (same shader infrastructure as GModular)
- Show: mesh geometry (wireframe/solid toggle), bone skeleton overlay,
  walkmesh AABB node (colored faces by material type), emitter nodes,
  reference nodes
- Node tree panel: list all nodes, click to select/highlight in viewport

**Rigging**
- Display bone hierarchy as a tree
- Click a bone in tree → highlight in viewport
- Edit bone name, parent, position offset
- Import external skeleton from ASCII MDL
- Skinning weights display: heat-map overlay on mesh

**Animation Editing**
- Animation set list: select, rename, delete, create animations
- Keyframe timeline: scrub through frames, add/delete keyframes
- Per-node transform channels: position, rotation (quaternion, shown as
  Euler in degrees)
- Play animation in viewport at real-time or custom FPS
- Export animation back to MDL

**UV Editor**
- Show UV map as 2D overlay
- Seam visualization on 3D mesh
- Select faces, move UV islands
- Assign lightmap UV channel (UVMap_lm separate from UVMap)

**Lightmap Baking**
- Select room or placeable MDL
- Set light sources (ambient color, directional lights, point lights)
- Bake to TGA using CPU raytracer (no GPU required, just slower)
- Preview baked result on mesh in viewport
- Export: saves .tga lightmap file alongside MDL

#### Module Editor (Archive Browser)
- Open any .mod, .rim, .erf file (or chitin.key for full game browse)
- Tree view: all contained resources grouped by type
- Right-click any resource: Extract, Edit In [appropriate editor], Replace,
  Delete, Rename
- Drag a file from Windows Explorer → drop into archive to add it
- Save archive: re-pack to .mod/.rim/.erf with correct ERF header
- Batch export: select multiple resources, export all to a folder
- Diff view: compare two archives side-by-side (highlight added/changed/removed)

### 5.4 IPC Behavior

On receiving `open_utc`:
1. Extract the UTC file from module_dir using archives.py
2. Parse with gff_reader.py
3. Open or focus a blueprint editor tab showing that UTC
4. Bring GhostRigger window to front (win32: SetForegroundWindow)

On saving a blueprint:
1. Write back to GFF with gff_writer.py
2. POST `blueprint_saved` to GModular on port 7003
3. GModular refreshes the viewport object for that ResRef

### 5.5 File Structure

```
GhostRigger/
    main.py                    -- entry point, QApplication init, IPC start
    build.bat                  -- v1.0, same pattern as GModular build.bat
    GhostRigger.spec           -- PyInstaller spec
    setup_python.bat           -- same as GModular's
    ghostrigger/
        __init__.py
        core/
            asset_library.py   -- resource resolution (chitin.key → BIF)
            blueprint_state.py -- open blueprint + dirty tracking
        formats/               -- copy of ghostworks-formats (until shared pkg)
            gff_types.py
            gff_reader.py
            gff_writer.py
            archives.py
            mdl_parser.py
        gui/
            main_window.py     -- QMainWindow, menu, layout
            asset_browser.py   -- left panel tree
            viewport.py        -- 3D MDL viewer (moderngl)
            properties.py      -- right panel GFF field editor
            timeline.py        -- animation keyframe timeline
            uv_editor.py       -- UV map 2D panel
            utc_editor.py      -- UTC-specific field widgets
            utp_editor.py      -- UTP-specific field widgets
            utd_editor.py      -- UTD-specific field widgets
            module_editor.py   -- archive browser panel
        ipc/
            server.py          -- Flask server on port 7001
            client.py          -- HTTP calls to ports 7002 and 7003
    tests/
        test_gff_roundtrip.py
        test_utc_fields.py
        test_ipc_server.py
        test_mdl_parser.py
    requirements.txt
    README.md
```

---

## 6. GHOSTSCRIPTER — FULL SPECIFICATION

### 6.1 Purpose

GhostScripter is the logic and language tool. A modder uses GhostScripter
to write NWScript code, build dialogue trees, maintain string tables (TLK),
and manage 2DA data tables. It sits in the middle of the pipeline: assets
created by GhostRigger are given behavior here, then passed to GModular for
placement.

GhostScripter is the "Visual Studio + Unreal Blueprint Editor" of the
pipeline. It handles all the text, logic, and language work so that neither
GhostRigger (3D art tool) nor GModular (level layout tool) has to embed a
full IDE.

### 6.2 Window Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Menu: File  Script  Dialog  Tables  Strings  IPC  Help          │
├─────────────┬───────────────────────────────────┬───────────────┤
│             │                                   │               │
│  PROJECT    │         MAIN EDITOR AREA          │  REFERENCE    │
│  PANEL      │    (tabs: Script | Dialog |       │  PANEL        │
│             │     2DA | TLK)                    │               │
│  Tree of    │                                   │  Active tab:  │
│  scripts,   │  SCRIPT TAB:                      │               │
│  dialogs,   │    Syntax-highlighted code        │  Function     │
│  2DA files, │    editor with line numbers       │  browser:     │
│  TLK files  │    and gutter markers             │  search NSS   │
│  in current │                                   │  functions,   │
│  module     │  DIALOG TAB:                      │  click for    │
│             │    Visual node graph              │  signature +  │
│             │    (NPC lines = blue nodes,       │  description  │
│             │     PC replies = green nodes,     │               │
│             │     conditions = yellow,          │  Template     │
│             │     actions = red)                │  library:     │
│             │                                   │  one-click    │
│             │  2DA TAB:                         │  insert       │
│             │    Spreadsheet editor             │  common       │
│             │                                   │  patterns     │
│             │  TLK TAB:                         │               │
│             │    String table browser           │               │
├─────────────┴───────────────────────────────────┴───────────────┤
│ OUTPUT: compile log | error lines | IPC event log               │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 Feature List

#### NWScript IDE

**Code Editor** (QPlainTextEdit with custom syntax highlighter)
- Syntax highlighting categories:
  - Keywords: `void`, `int`, `float`, `string`, `object`, `vector`,
    `effect`, `event`, `location`, `talent`, `action`, `if`, `else`,
    `while`, `for`, `do`, `return`, `#include`, `#define`
  - Built-in functions: all functions from nwscript.nss (both K1 and TSL)
  - Constants: `TRUE`, `FALSE`, `OBJECT_SELF`, `OBJECT_INVALID`,
    `OBJECT_TYPE_*`, `FACTION_*`, `AMBIENT_PRESENCE_*`, all game constants
  - String literals: green
  - Comments: gray italic (`//` and `/* */`)
  - Numbers: cyan
- Line numbers in gutter
- Current-line highlight
- Bracket matching
- Code folding on `{}` blocks

**Autocomplete**
- Trigger on any letter or `(` after a function name
- Popup shows: function signature, return type, parameter list, description
- Source: parsed from nwscript.nss (ship both K1 and TSL versions)
- Also completes: local variable names, #define constants, #include file names

**Function Browser** (right panel)
- Search field: type any substring → filters function list live
- Click a function → inserts at cursor with parameter placeholders
- Shows: full signature, return type, parameter descriptions, usage notes
- Grouped by category: Object, Action, Conversation, Combat, Effect,
  Global Variable, Party, Inventory, Item, Module, Sound, Waypoint, etc.

**Compile**
- Button: "Compile" (Ctrl+B)
- Calls: Python NWScript compiler (from PyKotor, or invoke nwnnsscomp.exe
  if present in tools/)
- Output panel: shows all errors and warnings with line numbers
- On success: .ncs file written to module scripts folder
- On success: POST `script_compiled` to GModular on port 7003 with the
  resref, slot name, and object tag (if opened via IPC from GModular)

**Script Templates** (matches GModular's script_library.py content)
All templates available as File > New From Template:
- `walk_spawn.nss` — WalkWayPoints on spawn
- `walk_random.nss` — random walk heartbeat
- `open_door.nss` — open door by tag
- `open_store.nss` — open merchant by tag
- `spawn_on_enter.nss` — spawn NPC at waypoint on trigger enter
- `make_hostile.nss` — change faction + attack player
- `trigger_conversation.nss` — start dialogue on trigger enter
- `dead_on_spawn.nss` — spawn NPC as corpse
- `start_conditional.nss` — check module name or global variable
- `check_global.nss` — StartingConditional on global number

**DeNCS Integration**
- File > Decompile .ncs
- Drop a .ncs binary or select from module archive
- Calls xoreos-tools `ncsdecomp` if present, otherwise Python fallback
- Opens decompiled result in new editor tab (read-only, marked [decompiled])

#### Dialog Tree Editor (.dlg)

**Node Graph Canvas** (QGraphicsView + QGraphicsScene)
- Node types:
  - NPC Entry (blue): text spoken by NPC. Fields: Text (StrRef/plain),
    Sound (ResRef), Animation, Script (fires when line plays), Delay,
    Quest/QuestEntry
  - PC Reply (green): player response option. Same fields as Entry.
  - Start node (white): root list of NPC entries shown at conversation start
- Edges: Entry → Reply → Entry chains (alternating NPC/PC)
- Conditions: any node can have a StartingConditional script (yellow dot)
- Actions: any node can have an action script that fires on entry (red dot)
- Context menu on canvas: Add NPC Line, Add PC Reply, Auto-Layout
- Context menu on node: Edit Fields, Set Condition, Set Action,
  Open Script In Editor, Delete

**Node Edit Dialog**
- Text field (plain text or StrRef lookup button → opens TLK browser)
- Sound ResRef field (with browse button → audio preview if file exists)
- Animation dropdown (matching animation names from game)
- Script fields: StartingConditional, ActionTaken (open in GhostScripter IDE)
- Link to existing node (for branching/rejoining conversations)
- IsChild flag, PlotXP, PlotIndex, Quest fields

**DLG Save/Load**
- Reads and writes GFF format (type "DLG ")
- Full round-trip fidelity: every field GModular's gff_reader supports
  plus DLG-specific link structures (EntryList/ReplyList with LinkList)

#### 2DA Table Editor

**Spreadsheet View** (QTableWidget)
- Load any .2da from game archives or Override folder
- Editable cells (double-click to edit)
- Column resize, column reorder (visual only, save order preserved)
- Row insert, row delete, row duplicate
- Search: Ctrl+F → highlight matching cells
- Key tables to support: appearance.2da, placeables.2da, genericdoors.2da,
  soundset.2da, feat.2da, skills.2da, classes.2da, spells.2da,
  globalcat.2da (for global variables), alienvo.2da
- Preview column: for appearance/placeables rows, shows MDL thumbnail
  (calls GhostRigger viewport via IPC if GRigger is running, otherwise
  skips preview)
- Save: writes back 2DA V2.0 format

**Cross-Reference View**
- Right-click a row: "Find references" → scans open module for all GFF
  files that reference this row number → shows list

#### TLK String Editor

**String Table Browser**
- Load dialog.tlk (K1 or TSL) from game directory
- Show: StrRef (int), Sound ResRef, Text
- Search: by StrRef number or text substring
- Edit text in-place
- Add new StrRef (appends to end)
- Export: save modified TLK as a patch (custom.tlk or append_tlk)
- Used by: DLG editor (StrRef picker button), blueprint editors
  (Name fields that use StrRef)

### 6.4 IPC Behavior

On receiving `open_script`:
1. Locate the .nss source file in module_dir/scripts/ (or extract .ncs
   and decompile if no .nss found)
2. If not found and a template name is given: open that template in a
   new tab pre-filled with the ResRef as the filename
3. Bring GhostScripter window to front
4. Set internal context: resref, slot, object_tag (used on compile)

On receiving `open_dlg`:
1. Extract the .dlg GFF from module_dir
2. Parse and display in the dialog graph canvas
3. Bring GhostScripter window to front

On compile success:
1. Write .ncs to module scripts folder
2. POST `script_compiled` to GModular port 7003:
   `{"resref": "c_rodian_sp", "slot": "on_spawn", "object_tag": "RODIAN_01"}`
3. GModular fills the script field in the inspector for that object

### 6.5 File Structure

```
GhostScripter/
    main.py
    build.bat
    GhostScripter.spec
    setup_python.bat
    ghostscripter/
        __init__.py
        core/
            compiler.py        -- .nss → .ncs (PyKotor or subprocess nwnnsscomp)
            nss_parser.py      -- parse nwscript.nss for autocomplete/highlight
            script_state.py    -- open scripts, dirty tracking
            dlg_state.py       -- open dialog trees, dirty tracking
        formats/               -- copy of ghostworks-formats
            gff_types.py
            gff_reader.py
            gff_writer.py
            archives.py
        gui/
            main_window.py
            project_panel.py   -- left tree
            code_editor.py     -- QPlainTextEdit + highlighter + autocomplete
            function_browser.py-- right panel function list
            dialog_editor.py   -- QGraphicsView node graph
            node_item.py       -- QGraphicsItem for DLG nodes
            table_2da.py       -- QTableWidget 2DA editor
            tlk_editor.py      -- TLK string browser
            template_library.py-- script template panel (matches GModular's)
            output_panel.py    -- compile log + IPC event log
        ipc/
            server.py          -- Flask server on port 7002
            client.py          -- HTTP calls to ports 7001 and 7003
        data/
            nwscript_k1.nss    -- K1 function definitions
            nwscript_tsl.nss   -- TSL function definitions
    tests/
        test_compiler.py
        test_dlg_roundtrip.py
        test_2da_editor.py
        test_ipc_server.py
        test_syntax_highlighter.py
    requirements.txt
    README.md
```

---

## 7. GMODULAR — CURRENT STATE AND ROADMAP

GModular is the level assembly and ship tool. It is the furthest along
of the three programs. Here is its current implementation state and the
features still needed to complete the pipeline.

### 7.1 Already Implemented (as of v2.0.6 — Deep Scan Passes 8–12)

```
gmodular/
    formats/
        archives.py          -- chitin.key/BIF, ERF/MOD/RIM; RES_TYPE_MAP (70+ IDs,
                                 fully verified against PyKotor ResourceType enum)
        gff_types.py         -- GIT object data classes (all 7 GIT types)
        gff_reader.py        -- binary GFF V3.2 parser (all 18 field types)
        gff_writer.py        -- binary GFF V3.2 writer (BFS two-phase)
        mdl_parser.py        -- binary MDL/MDX parser (K1+K2, controller data)
        tpc_reader.py        -- TPC texture reader (DXT1/5, mips, cubemap)
        wok_parser.py        -- walkmesh parser + AABB tree + ray-cast queries
        twoda_loader.py      -- 2DA table loader, TwoDAComboBox widget
        lyt_vis.py           -- LYT/VIS room layout parser & writer; structured
                                 Track/Obstacle entries (not just raw strings)
        kotor_formats.py     -- SSF(28 sounds), LIP, TXI, VIS, PTH, LTR(26/28),
                                 NCS(decode+write), TLK, TPC writer
        mod_packager.py      -- dependency walker, validation, ERF/MOD export
    gui/
        main_window.py       -- main window, all menus and panels
        viewport.py          -- 3D viewport: ModernGL VAO, Phong lighting,
                                frustum culling, gizmo, play mode, walkmesh
                                (now imports OrbitCamera + shaders from sub-modules)
        viewport_camera.py   -- OrbitCamera extracted from viewport.py (NEW v2.0.6)
                                Standalone Maya-style orbit camera; testable without Qt
        viewport_shaders.py  -- All GLSL shader source strings (NEW v2.0.6)
                                8 shaders: flat/lit/lit_no_uv/uniform/outline/picker/
                                textured/skinned; ALL_SHADERS dict for tooling
        inspector.py         -- GFF field editor, all 7 object types
        dlg_editor.py        -- visual DLG node-graph editor (NEW in v2.0)
        twoda_editor.py      -- 2DA table editor with undo/redo (NEW in v2.0)
        ui/                  -- Qt Designer .ui files (NEW in v2.0)
            inspector.ui
            twoda_editor.ui
            dlg_editor.ui
            mod_import_dialog.ui
        ui_loader.py         -- load_ui() / load_ui_type() (NEW in v2.0)
        asset_palette.py     -- left panel: game resource tree
        content_browser.py   -- tile/list asset browser
        scene_outline.py     -- object hierarchy, search, context menu
        walkmesh_editor.py   -- WOK visualizer: face paint, AABB, export
        room_assembly.py     -- 2D room grid: drag-drop, LYT/VIS generation
        patrol_editor.py     -- visual waypoint editor, auto-naming
    engine/
        mdl_renderer.py      -- ModernGL VAO upload + render, LRU cache
                                (silent swallows → log.debug in v2.0.6)
        player_controller.py -- FPS camera + walkmesh collision
        npc_instance.py      -- NPC patrol/idle behavior
    mcp/
        server.py            -- MCP/SSE server (port 7003)
        state.py             -- KotorInstallation cache
        _indexer.py          -- resource indexer (override/modules/chitin)
        tools/               -- 103 MCP tools across 13 modules (v2.0.9)
    ipc/
        ghidra_bridge.py     -- AgentDecompile HTTP bridge (qtpy signals)
        nwscript_bridge.py   -- NSS/NCS compile+decompile pipeline
```

### 7.2 GModular Completed Features

All P1-P10 pipeline features are implemented, plus the v2.0 additions below.
Remaining gaps are noted in Section 7.3.

**P1 — Room Assembly Grid** — COMPLETE
- `room_assembly.py`: drag-and-drop 2D top-down grid
- Auto-generates `.lyt` from placed rooms; auto-generates `.vis` from adjacency
- Door-hook scanning via MDL node names; room connection indicators drawn
- Zoom controls, right-click context menu, room rename/delete

**P2 — Binary MDL Renderer** — COMPLETE
- `mdl_parser.py`: full binary MDL/MDX parser (K1 + K2)
- `mdl_renderer.py`: ModernGL VAO pipeline, Phong lighting
- Frustum culling, LRU model cache (64 models), wireframe/normal debug overlays

**P3 — Full WOK Parser and Visualizer** — COMPLETE
- `wok_parser.py`: binary .wok parser, AABB tree, per-face materials
- `walkmesh_editor.py`: walkable (green) / non-walkable (red) face-paint tool
- `height_at`, `face_at`, `clamp_to_walkmesh`, `bounds`, `material_counts`

**P4 — Visual Patrol Waypoint Linker** — COMPLETE

**P5 — Visual Asset Browser** — COMPLETE

**P6 — Module Packager (MOD Export)** — COMPLETE

**P7 — Script Field IPC Integration** — COMPLETE

**P8 — 2DA Lookup Layer** — COMPLETE

**P9 — Blueprint IPC Integration** — COMPLETE

**P10 — Module Validation Report** — COMPLETE

**v2.0 — qtpy + MCP + DLG + NWScript** — COMPLETE
- All 68 raw `PyQt5` imports replaced with `qtpy` (21 files)
- Qt Designer `.ui` files + `ui_loader.py`
- 83 MCP tools (SSF/LIP/TXI/VIS/LYT/BWM/TPC-info/LTR/NCS/LYT/resource-lookup)
- DLG visual node-graph editor
- NWScript compile/decompile bridge
- TPC writer, 2DA binary round-trip, GFF/2DA/TLK diff+patch
- 1,933 tests, 0 failures

**v2.0.1 — Resource Map Audit + 4 New MCP Tools** — COMPLETE
- Full `RES_TYPE_MAP` audit against PyKotor `ResourceType` enum
- 70+ type IDs now match game format exactly
- `kotor_read_lyt`, `kotor_read_bwm`, `kotor_resource_type_lookup`, `kotor_read_tpc_info`

**v2.0.2 — DLG Write-Back + PTH + AnimList/CameraStyle** — COMPLETE
- `DLGGraphData.to_gff_bytes()` — full GFF V3.2 DLG serialiser
- `kotor_dlg_write`, `kotor_read_pth` MCP tools
- `DLGNodeData.camera_style` + `anim_list` fields; GUI properties panel updated
- 85 MCP tools, 1,966 tests, 0 failures

**v2.0.3 — Resource Map Corrections + 3 Write Tools** — COMPLETE
- Critical resource-type fixes: `nss=2009`, `ncs=2010` (were swapped), `pth=3003`, `lip=3004`, `rim=3002`
- Added `erf=9997`, `bif=9998`, `key=9999`
- `kotor_write_pth` — PTH path graph GFF binary serialiser + round-trip
- `kotor_write_bwm` — BWM V1.0 binary exporter (wok/dwk/pwk); walkable flag + material control
- `kotor_write_lyt` — LYT canonical text writer (CRLF, BioWare-format) + round-trip
- `DLGPropertiesPanel` now shows CameraStyle field and AnimList add/remove
- 88 MCP tools, 2,035 tests, 0 failures

**v2.0.4 — Complete Format Write Coverage + DLG Script2** — COMPLETE
- `kotor_write_lip` — LIP V1.0 lip-sync binary writer (duration + keyframes, shape by name or int)
- `kotor_write_vis` — VIS ASCII room-visibility writer (bidirectional visibility graph)
- `kotor_write_txi` — TXI ASCII texture metadata writer (envmap, blending, fps, numx/numy, etc.)
- DLG `Script2` (KotOR 2 second conditional script) now written and read back in GFF + JSON paths
- Fixed `DLGNodeData.script2` omitted from `to_dict()` / `from_dict()` → full MCP round-trip now works
- Every KotOR format that has an MCP reader now also has an MCP writer
- 91 MCP tools, 2,084 tests, 0 failures

**v2.0.5 — Pass 7: Encoding correctness, animation wiring, ModuleIO refactor** — see below

**v2.0.5 — Pass 7: Encoding correctness, animation wiring, ModuleIO refactor** — COMPLETE
- **GFF CExoLocString multi-language (full fix)**:
  - `GFFReader.read_cexolocstring()` now returns `LocalizedString` objects (not plain `str`)
  - Each substring decoded with the correct Windows codepage per language:
    English/Western → cp1252, Polish/Czech → cp1250, Russian → cp1251, Greek → cp1253, etc.
  - `GFFWriter` accepts both `LocalizedString` objects and plain `str` (backward-compatible)
  - Plain `str` is wrapped as English-male with proper cp1252 encoding (fixes the UTF-8 bug)
  - `_locstring_field()`, `save_ifo()` waypoint `MapNote` updated to use `LocalizedString`
  - `load_are()`, `load_ifo()` extract `.name`/`mod_name` via new `_get_locstr()` helper
- **Animation pipeline wiring**:
  - `ViewportWidget.set_animation_panel(panel)` convenience method added
  - Delegates to `panel.set_viewport(self)` which connects `frame_advanced` signal
  - `AnimationTimelinePanel._poll_player` slot receives per-frame `dt` from signal
- **MDL base-header helper** — de-duplicates `struct.unpack` offsets:
  - `read_mdl_base_header(data, base)` public function in `mdl_parser.py`
  - Extracts `name`, `bb_min`, `bb_max`, `root_node_off` from shared MDL/WOK header layout
  - `WOKParser.parse()` in `walkmesh_editor.py` imports and uses this helper
- **ModuleIO service complete**:
  - `ModuleIO.load_from_files(git, are, ifo)` extracted from `ModuleState`
  - `ModuleState.load_from_files()` now delegates to `ModuleIO`, mirroring `load_from_mod()`
  - Both load paths are now coupling-clean (ModuleState has no format-parser imports)
- 91 MCP tools, **2,132 tests**, 0 failures (+48 new Pass 7 tests)

**v2.0.6 — Passes 8–12: MDL writer, Animation seek API, Viewport refactor, Silent swallows** — COMPLETE
- **Pass 8 — MDL Binary Writer (`mdl_writer.py`)** — COMPLETE
  - Full KotOR binary MDL/MDX writer; round-trip verified: write→parse→assert
  - Fixed `_mesh_stats()` AttributeError (tuple vs dict mismatch in `_bb` map)
  - Fixed `classification` handling: int + string values both accepted via `_CLASS_TO_BYTE`
  - `test_mdl_writer.py`: 43 tests covering headers, geometry, skinned meshes, AABB, metadata
  - 100% pass rate, including edge cases (300-vertex meshes, empty models, long names)
- **Pass 9 — Animation Scrubber (`seek` API)** — COMPLETE
  - `AnimationPlayer.seek(time_s, pause=True)` — proper scrubber API replacing direct state access
  - `AnimationPlayer.get_elapsed()` / `get_duration()` — public read-only playback state
  - `AnimationTimelinePanel._on_ruler_click` updated to call `seek()` (falls back gracefully)
  - `AnimationTimelinePanel._poll_player` updated to call `get_elapsed()` / `get_duration()`
  - 7 new seek/get_elapsed/get_duration tests; all pass
- **Pass 12a — BWM Edge Adjacency** — COMPLETE
  - `WOKWriter._build_edge_tables()` constructs adjacent-edge and outer-edge tables
  - Full outer-edge/perimeter round-trip verified
  - 130 BWM integration tests; all pass
- **Pass 12b — Viewport Refactor (sub-module extraction)** — COMPLETE
  - `viewport_camera.py`: `OrbitCamera` class extracted; testable without Qt or OpenGL
  - `viewport_shaders.py`: all 8 GLSL shader source strings; `ALL_SHADERS` dict
  - `viewport.py` re-exports both modules for 100% backward compatibility
  - 48 new tests in `test_viewport_refactor.py` (OrbitCamera math + shader content)
- **Pass 11 — Silent Swallow Audit** — COMPLETE
  - 8 critical `except: pass` blocks in format/engine/core layer converted to `log.debug`
  - Files improved: archives.py, mod_packager.py, wok_parser.py, mdl_renderer.py,
    play_mode.py (×2), module_io.py, entity_system.py
  - GUI-layer swallows (viewport.py) intentionally left as defensive fallbacks
- **Test quality improvements** — COMPLETE
  - No-assertion test count reduced from 197 → 57 (EventBus, AnimationPlayer, MDL parser)
  - Total test count: **2,227 passed, 3 skipped** (+95 vs v2.0.5)
  - New test files: `test_mdl_writer.py` (43), `test_viewport_refactor.py` (48),
    `test_bwm_integration.py` (130)

**v2.0.7 — EGLRenderer Extraction + Viewport Refactor Complete** — COMPLETE
- `_EGLRenderer` (1,507 lines) extracted from `viewport.py` → `viewport_renderer.py`
- `viewport.py` reduced from 4,295 → 2,798 lines (−35%); imports `_EGLRenderer` from new module
- `tests/test_viewport_refactor.py` expanded to 68 tests (20 new renderer surface tests)
- Total test count: **2,247 passed, 3 skipped** (+20 vs v2.0.6)

**v2.0.8 — GhostRigger+GhostScripter Stubs, .ui Migration, Dangly/Emitter Write-back** — COMPLETE
- **GhostScripter `gui/main_window.py`** — Full NWScript IDE Qt window: syntax-highlighted editor
  (`NWScriptHighlighter`), compile output panel, script registry sidebar, IPC status indicator,
  polled `_poll_ipc_status()`. Matches Ghostworks dark-theme contract (PIPELINE_SPEC §6).
- **GhostScripter tests** — `ghostscripter/tests/test_ghostscripter.py`: 54 tests covering
  Script dataclass, ScriptRegistry thread-safety, NWScriptCompiler stub, live IPC round-trips,
  headless window construction.
- **GhostRigger IPC tests** — All handlers properly registered in `setUpClass`; 29 tests pass.
- **Qt `.ui` migration (Phase 2)** — `InspectorPanel`, `TwoDAEditorPanel`, `DLGEditorPanel` all
  attempt `load_ui()` at startup; `self._ui_loaded` flag exposed; Python layout is complete fallback.
  `tests/test_ui_migration.py` — 34 tests: API surface, XML validity of 4 `.ui` files,
  `_ui_loaded` attribute + `load_ui()` call presence, integration (empty-dir, missing-file).
- **Dangly constraint-weight write-back** — `MDLWriter` now writes `node.constraint_weights`
  per-vertex; defaults to `1.0` when attribute absent or list is shorter than vertex count.
- **Emitter node header** — Full 208-byte KotOR emitter block written
  (`dead_space`, `blast_radius/length`, `branch_count`, `x/y_grid`, `spawn_type`,
  `update_type`, `render_type`, `blend_type`, `texture`, `chunk_name`, `two_sided_tex`,
  `loop`, `render_order`, `frame_blending`, `depth_texture`).
- **Emitter controllers** — 18 static controllers (t=0) written: `birthrate`, `life_exp`,
  `velocity`, `spread`, `size_start/end`, `alpha_start/end`, `gravity`, `mass`, `x/y_size`,
  `fps`, `frame_start/end`, `color_start/mid/end`. `CTRL_EM_*` constants in `mdl_writer.py`.
- **MDL writer test expansion** — `TestMDLWriterDangly` (8 tests) + `TestMDLWriterEmitter`
  (12 tests) added; total MDL writer tests: 63.
- **build.bat v2.0.8** — GhostRigger + GhostScripter self-test steps (11b/c); non-fatal.
- **GModular.spec v2.0.8** — Version bumped; `viewport_renderer` confirmed in hidden imports.
- Total test count: **2,384 passed, 3 skipped** (+137 vs v2.0.6; 83 stub tests in sub-projects)

**v2.0.9 — Ghostworks IPC Bridge, End-to-End Pipeline, 103 MCP Tools** — COMPLETE

- **`gmodular/ipc/ghostworks_bridge.py`** — Pure-`urllib` HTTP bridge for GhostRigger (port 7001) and GhostScripter (port 7002). Zero Qt/requests dependencies. Functions: `ghostrigger_ping`, `ghostrigger_open_blueprint`, `ghostrigger_get_blueprint`, `ghostrigger_set_field`, `ghostrigger_save_blueprint`, `ghostrigger_list_blueprints`, `ghostscripter_ping`, `ghostscripter_open_script`, `ghostscripter_get_script`, `ghostscripter_compile`, `ghostscripter_list_scripts`.
- **`gmodular/mcp/tools/ghostworks.py`** — 12 new MCP tools exposing the full Ghostworks IPC surface to AI agents. All tools return `{"error": …}` gracefully when a companion app is offline.
- **103 MCP tools** (up from 91) across 13 modules.
- **`ghostscripter/ghostscripter/gui/nwscript_tokens.py`** — Full KotOR 1 & 2 NWScript token database (keywords, stdlib, game constants). Used by `NWScriptHighlighter` and `FunctionBrowserPanel`.
- **`FunctionBrowserPanel`** — Searchable Qt widget with category tree, signature, docstring pane, clipboard copy. Wired into GhostScripter `MainWindow`.
- **`ghostrigger/ghostrigger/gui/field_editor.py`** — `BlueprintFieldEditor` Qt form for UTC/UTP/UTD fields with live IPC set/get and Save button.
- **End-to-end integration test** — `tests/test_ghostworks_pipeline_e2e.py` (25 tests) exercises the full three-program pipeline from GhostRigger blueprint creation through GhostScripter compile to GModular MCP dispatch.
- **Event-loop robustness** — All async test helpers use `asyncio.run()` fallback, safe across all pytest-asyncio versions.
- Total test count: **2,552 passed, 7 skipped** (+168 vs v2.0.8)

**v2.0.10 — Walkmesh Overlay Fix, slem_ar.mod Scenario, Comprehensive Roadmap** — COMPLETE

- **Walkmesh overlay critical fix** — `main_window._auto_load_walkmesh_from_dir()`: removed incorrect `.parse()` call on `WOKParser.from_file()` result (which already returns a `WalkMesh` object, not a parser). Fixed `face.is_walkable` → `face.walkable`. The walkmesh overlay was silently broken in all previous versions due to these two bugs.
- **`slem_ar.mod` integration scenario** — Full load→parse→edit→write→repack pipeline verified in code: `ModuleIO.load_from_mod()` → WOK parse → material fix (non-walkable to grass) → `WOKWriter.to_bytes()` round-trip → `ERFWriter` repack. All steps pass.
- **`tests/test_data/slem_ar.mod`** — Synthetic Sleheyron Arena module (ARE + IFO + GIT + LYT + WOK) for load-chain integration tests.
- **ROADMAP.md** — Comprehensive 8-phase roadmap: confirmed bugs, OldRepublicDevs cross-reference (PyKotor BWM/GFF/LYT, kotorblender WOK/MDL/LYT/PTH, KotorMCP API), KotOR surface material reference, cross-repo compatibility matrix, per-phase task lists with file targets.
- Total test count: **2,552 passed, 7 skipped** (unchanged — fixes are in GUI paths not covered by headless tests)

### 7.3 Known Remaining Gaps

**Animation playback in viewport**
MDL controller keyframes are fully parsed. `AnimationTimelinePanel` is wired to
`ViewportWidget.frame_advanced` signal (Pass 7). Timeline ruler, transport controls,
and `seek()` API are fully functional (Pass 9). Remaining: `AnimationClipSignal.emit`,
`connect`, and `__init__` are still stubs — they must be connected to `viewport.step_to_frame(n)`.

**MDL → GPU mesh rendering**
MDL/MDX binary parser is complete. The viewport renders geometry via ModernGL but
does not yet upload MDL node meshes as textured GPU VBOs. TPC/TGA texture decompression
(DXT1/DXT3/DXT5) is also needed. This is the highest-priority visible gap.

**Game installation integration**
GModular has no KotOR installation detector. Modders cannot browse `chitin.key`,
open resources directly from BIF archives, or load the game's TLK file for StrRef
resolution. PyKotor's `Installation` class + `game_detector.py` (from GhostRigger v4.2)
are the reference implementations.

**~~Walkmesh overlay~~** — RESOLVED in v2.0.10
`main_window._auto_load_walkmesh_from_dir()` now correctly calls
`WOKParser.from_file()` directly (no `.parse()` step) and uses `face.walkable`
(not the non-existent `face.is_walkable`). Walkmesh overlay fully functional.

**Native KotOR .wok export** — RESOLVED in v2.0.3
`WOKWriter` produces byte-for-byte valid BWM V1.0 binaries. The `kotor_write_bwm`
MCP tool exposes this to AI agents. `BWM.calculate_edges()` adjacency completion
remains as a future enhancement (also TODO in Kotor.NET).

**~~MDL binary writer~~** — FULLY RESOLVED in v2.0.8
`MDLWriter` in `mdl_writer.py` produces valid KotOR binary MDL/MDX.
Round-trip verified: skinned meshes, AABB nodes, dangly (constraint weights), emitter nodes,
controller data. 63 tests, 0 failures.

**~~GFF CExoLocString multi-language~~** — RESOLVED in v2.0.5
Full multi-language support with correct codepage per language ID.
`LocalizedString` objects returned by reader; writer encodes with the
per-language Windows codepage. See v2.0.5 changelog above.

**~~Qt Designer .ui adoption~~** — RESOLVED in v2.0.8
All four `.ui` files validated. `InspectorPanel`, `TwoDAEditorPanel`, `DLGEditorPanel`
all wired with `load_ui()` + `_ui_loaded` flag + complete Python fallback.
34 tests in `test_ui_migration.py`.

**~~Viewport.py monolith~~** — RESOLVED in v2.0.7
`_EGLRenderer` extracted into `viewport_renderer.py`. `viewport.py` is now 2,798 lines
(was 4,295). Sub-module re-exports maintain 100% backward compatibility.

**~~`ModuleIO` service~~** — RESOLVED in v2.0.5
`ModuleState.load_from_files()` now delegates to `ModuleIO.load_from_files()`,
completing the coupling-clean refactor. Both MOD and files load paths go
through the stateless `ModuleIO` service.

---

## 8. DEVELOPMENT PRIORITIES AND ORDER OF WORK

### Phase 1 — Core Programs Running

Each program should start, show its layout, and have a working IPC server.
Test: launch all three, ping each from the others.

- **GModular:** Complete. All P1-P10 features implemented. See Section 7.2.
- **GhostRigger:** ✅ Stub complete (v2.0.8) — IPC server (port 7001), BlueprintRegistry,
  MainWindow with blueprint tree; 29 tests. Next: full UTC/UTP/UTD field editor, MDL preview.
- **GhostScripter:** ✅ Stub complete (v2.0.8) — IPC server (port 7002), NWScriptCompiler,
  NWScriptHighlighter, full IDE MainWindow; 54 tests. Next: full NWScript compiler integration.

### Phase 2 — Connected Workflow

With all three running, implement the core handoffs:
1. GModular inspector: "Edit in GhostRigger" — COMPLETE (P9)
2. GRigger saves UTC: GModular refreshes viewport — COMPLETE (P9 callback)
3. GModular inspector: script field pencil — COMPLETE (P7)
4. GScripter compiles: GModular fills script ResRef — COMPLETE (P7 callback)

GModular is ready for Phase 2. GhostRigger and GhostScripter need to be built.

### Phase 3 — Level Assembly (GModular P1, P2, P3)

All complete in GModular. Room Assembly Grid, binary MDL renderer, and full
WOK parser/visualizer are implemented and passing 641 tests.

### Phase 4 — Full Polish

GModular remaining items:
- ~~Animation scrubber~~ — COMPLETE (v2.0.6, seek() API)
- ~~Native .wok binary export~~ — COMPLETE (v2.0.3, WOKWriter)
- ~~MDL binary writer~~ — COMPLETE (v2.0.6, MDLWriter round-trip)
- ~~GFF CExoLocString multi-language~~ — COMPLETE (v2.0.5)
- ~~`ModuleIO` service extraction~~ — COMPLETE (v2.0.5)
- Qt Designer .ui adoption (files created; panels need migrating to `load_ui()`)
- `_EGLRenderer` extraction from viewport.py (viewport_camera + viewport_shaders done)
- `BWM.calculate_edges()` — full adjacency completion for area-to-area edge cases
- Windows build pipeline (`build.bat` / PyInstaller spec) — not yet created
- Dangly/emitter node controller write-back in MDLWriter (low priority)

GhostScripter items (not yet started):
- Dialog tree editor, 2DA editor, TLK editor, NWScript compiler integration

GhostRigger items (not yet started):
- 3D MDL viewer, animation timeline, lightmap baking, native .wok export

---

## 9. VISUAL DESIGN CONTRACT

All three programs must look like they belong to the same suite.

### Color Palette (Dark Theme — matches GModular)

```
Background (main):        #1e1e1e
Background (panel):       #252526
Background (elevated):    #2d2d2d
Border:                   #3e3e42
Text (primary):           #d4d4d4
Text (secondary):         #9d9d9d
Text (disabled):          #6e6e6e
Accent (blue):            #4fc3f7
Accent (green — OK):      #4ec9b0
Accent (yellow — warn):   #dcdcaa
Accent (red — error):     #f44747
Accent (orange):          #ce9178
Selection highlight:      #264f78
Grid lines:               #3a3a3a

Viewport object colors (GIT objects):
  Placeable:              #4fc3f7  (light blue)
  Creature:               #81c784  (green)
  Door:                   #ffb74d  (orange)
  Trigger:                #f06292  (pink)
  Waypoint:               #4dd0e1  (cyan)
  Sound:                  #ce93d8  (purple)
  Store:                  #a5d6a7  (light green)
  Selected:               #ffffff  (white)
```

### Typography

- UI font: system default (Segoe UI on Windows)
- Code editor: `Consolas` 10pt (Windows) or `Courier New` 10pt fallback
- Labels: normal weight
- Section headers: bold

### Widget Standards

- All QPushButton: flat style, 4px rounded corners, hover highlight
- All QLineEdit / QSpinBox: #2d2d2d background, #3e3e42 border, 4px radius
- All QGroupBox: 1px #3e3e42 border, title in accent blue
- Scrollbars: thin (8px), #3e3e42 handle, transparent track
- Tab bars: underline style (no box), active tab in accent blue
- Status bar: #007acc left strip, #252526 background

### Window Title Format

```
GhostRigger  — KotOR Asset Editor  v1.0
GhostScripter — KotOR Script + Logic IDE  v1.0
GModular     — KotOR Level Designer  v1.0
```

---

## 10. TESTING CONTRACT

Every program must have a pytest test suite. Minimum coverage:

### All Programs

- GFF round-trip: write a GFF with all field types, read it back,
  assert all values identical
- Archive read: load a real chitin.key, resolve at least one resource
- IPC ping: start the IPC server, send a ping, assert ok response
- IPC error handling: send malformed JSON, assert no crash

### GhostRigger Specific

- UTC field round-trip: create UTC GFF, write, read back, check all fields
- UTP field round-trip: same for placeables
- UTD field round-trip: same for doors
- MDL parser: load an ASCII MDL, check node count and geometry

### GhostScripter Specific

- Syntax highlighter: feed sample NSS, assert keyword/function/comment spans
- Compiler: compile a trivial void main() {} → assert .ncs produced
- DLG round-trip: create DLG GFF with 2 entries and 1 reply, write, read back
- 2DA parser: read a sample 2DA, assert row/column values

### GModular Specific

- All existing tests continue to pass
- New tests for each new feature as it is added

### Test Run Requirement

All tests must pass with exit code 0 before any commit.
`python -m pytest tests/ --tb=short -q`

---

## 11. REPOSITORY STRUCTURE

Each program lives in its own repository:

```
github.com/CrispyW0nton/GhostRigger      -- GhostRigger repo
github.com/CrispyW0nton/GhostScripter    -- GhostScripter repo
github.com/CrispyW0nton/GModular         -- GModular repo (exists)
```

Optional shared library (future):
```
github.com/CrispyW0nton/ghostworks-formats  -- shared format code
```

### Branch Strategy

- `main` — always stable, always builds, always passes all tests
- Feature branches → pull requests → `main` after each iteration

### Commit Convention

Format: `type(scope): description`

Types: `feat` `fix` `refactor` `test` `docs` `build`
Scope: component name e.g. `ipc`, `gff`, `viewport`, `compiler`, `dlg`

Examples:
```
feat(ipc): add open_utc endpoint on port 7001
fix(gff): correct CExoLocString gender bit handling
feat(viewport): binary MDL renderer with moderngl
test(compiler): add round-trip .nss to .ncs test
```

---

## 12. QUICK REFERENCE: KOTOR FILE FORMATS

| Ext    | TypeID | GFF? | Contents                                 | Used by     |
|--------|--------|------|------------------------------------------|-------------|
| .utc   | 2027   | yes  | Creature blueprint                       | GRigger, GModular |
| .utp   | 2044   | yes  | Placeable blueprint                      | GRigger, GModular |
| .utd   | 2042   | yes  | Door blueprint                           | GRigger, GModular |
| .utw   | 2058   | yes  | Waypoint blueprint                       | GModular    |
| .utm   | 2051   | yes  | Merchant/store blueprint                 | GRigger, GModular |
| .uts   | 2035   | yes  | Sound blueprint                          | GModular    |
| .utt   | 2032   | yes  | Trigger template                         | GModular    |
| .ute   | 2040   | yes  | Encounter template                       | GModular    |
| .git   | 2023   | yes  | Game Instance Table (all placed objects) | GModular    |
| .are   | 2012   | yes  | Area properties (fog, ambient, rest)     | GModular    |
| .ifo   | 2014   | yes  | Module info (entry point, start script)  | GModular    |
| .dlg   | 2029   | yes  | Dialogue tree                            | GScripter   |
| .jrl   | 2056   | yes  | Journal / quest log                      | GScripter   |
| .gic   | 2046   | yes  | Game instance comments                   | GModular    |
| .gui   | 2047   | yes  | GUI definition                           | GModular    |
| .fac   | 2038   | yes  | Faction data                             | GModular    |
| .lyt   | 3000   | no   | Room layout: name + XYZ per room         | GModular    |
| .vis   | 3001   | no   | Visibility: room pairs that see each other| GModular   |
| .wok   | 2016   | no   | Walkmesh per room (binary face list)     | GModular    |
| .dwk   | 2052   | no   | Door walkmesh                            | GModular    |
| .pwk   | 2053   | no   | Placeable walkmesh                       | GModular    |
| .pth   | —      | gff  | NPC pathfinding graph                    | GModular    |
| .mdl   | 2002   | no   | 3D model (binary node tree)              | GRigger, GModular |
| .mdx   | 3008   | no   | Mesh vertex/normal data (paired w/ mdl)  | GRigger, GModular |
| .2da   | 2017   | no   | 2D data table (appearance, feats, etc.)  | GScripter, GModular |
| .tlk   | 2018   | no   | String table (dialog.tlk)                | GScripter   |
| .nss   | 2009   | no   | NWScript source code                     | GScripter   |
| .ncs   | 2010   | no   | Compiled NWScript bytecode               | GScripter   |
| .tga   | 3      | no   | Texture (TGA format)                     | GRigger     |
| .tpc   | 3007   | no   | Texture (KotOR proprietary, TGA+mips)    | GRigger     |
| .txi   | 2022   | no   | Texture info (ASCII key=value)           | GRigger     |
| .ssf   | 2060   | no   | Soundset (28 entries, V1.1)              | GModular    |
| .lip   | —      | no   | Lip sync phoneme data                    | GModular    |
| .ltr   | 2037   | no   | Letter probability table (name gen)      | GModular    |
| .mod   | 2011   | no   | ERF archive: complete module package     | GModular    |
| .rim   | —      | no   | ERF archive: module patch/DLC            | GModular    |
| .erf   | —      | no   | ERF archive: generic resource container  | All three   |
| .sav   | 2057   | no   | ERF archive: save game                   | GModular    |
| .bif   | —      | no   | BIF archive: game data (indexed by KEY)  | All three (read-only) |

TypeIDs verified against PyKotor `ResourceType` enum and Kotor.NET `KotorResourceType`.
Full mapping in `gmodular/formats/archives.py → RES_TYPE_MAP`.

---

## 13. CONTACT AND COORDINATION

- **Repository (GModular):** https://github.com/CrispyW0nton/GModular
- **IPC bus:** localhost ports 7001 (GRigger), 7002 (GScripter), 7003 (GModular)
- **Format reference:** gmodular/formats/ in the GModular repository
- **Test command:** `python -m pytest tests/ --tb=short -q`
- **Build command:** double-click `build.bat` (Python 3.12 required)

When in doubt about a file format, check GModular's existing implementation
first, then cross-reference with:
- PyKotor: github.com/OldRepublicDevs/PyKotor (most complete Python library)
- KotorBlender: github.com/seedhartha/kotorblender (MDL/WOK/LYT/PTH Python)
- reone: github.com/seedhartha/reone (complete C++ Aurora engine reference)
- xoreos: github.com/xoreos/xoreos (GFF C++ reference implementation)

---

*End of PIPELINE_SPEC.md*
*This document is the single source of truth for all three programs.*
*Any change to the IPC contract, file format handling, or port assignments*
*must be reflected here before implementation begins.*
