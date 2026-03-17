# GHOSTWORKS PIPELINE — TECHNICAL SPECIFICATION
## Shared Design Contract for GhostRigger, GhostScripter, and GModular

**Version:** 1.0  
**Date:** 2026-03-07  
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

### 7.1 Already Implemented (as of v2.0 — all P1-P10 complete)

```
gmodular/
    core/
        module_state.py      -- GIT load/save, undo/redo command stack
    formats/
        gff_types.py         -- GIT object data classes (all 7 GIT types)
        gff_reader.py        -- binary GFF V3.2 parser (all 18 field types)
        gff_writer.py        -- binary GFF V3.2 writer (BFS two-phase)
        archives.py          -- chitin.key, BIF, ERF/MOD/RIM reader + ERFWriter
        mdl_parser.py        -- binary MDL/MDX parser (K1+K2, controller data)
        tpc_reader.py        -- TPC texture reader (DXT1/5, mips, cubemap)
        wok_parser.py        -- walkmesh parser + AABB tree + ray-cast queries
        twoda_loader.py      -- 2DA table loader, TwoDAComboBox widget
        lyt_vis.py           -- LYT/VIS room layout parser & writer
        mod_packager.py      -- dependency walker, validation, ERF/MOD export
    gui/
        main_window.py       -- main window (2408 lines), all menus and panels
        viewport.py          -- 3D viewport (2145 lines): ModernGL VAO,
                                Phong lighting, frustum culling, gizmo,
                                play mode, walkmesh overlay, MDL rendering
        inspector.py         -- GFF field editor, all 7 object types,
                                2DA dropdowns, script pencil IPC buttons,
                                'Edit in GhostRigger' button, patrol section
        asset_palette.py     -- left panel: game resource tree
        content_browser.py   -- tile/list asset browser (1057 lines):
                                category tree, search, drag-to-place
        scene_outline.py     -- object hierarchy, search, context menu
        walkmesh_editor.py   -- WOK visualizer (1153 lines): face paint,
                                AABB tree, GWOK export
        room_assembly.py     -- 2D room grid (1240 lines): drag-drop,
                                LYT/VIS generation, door-hook detection
        patrol_editor.py     -- visual waypoint editor, auto-naming
        mod_import_dialog.py -- archive import with resource browser
        mod_packager_dialog.py -- packager UI: checklist, warnings
        script_library.py    -- NWScript template library (474 lines)
        tutorial_dialog.py   -- step-by-step onboarding
    engine/
        mdl_renderer.py      -- ModernGL VAO upload + render, LRU cache
        player_controller.py -- FPS camera + walkmesh collision (play mode)
        npc_instance.py      -- NPC patrol/idle behavior (play mode)
    ipc/
        bridges.py           -- GhostScripterBridge, GhostRiggerBridge,
                                ProjectFileWatcher (493 lines)
        callback_server.py   -- GModularIPCServer Flask thread (port 7003)
    utils/
        resource_manager.py  -- ResourceManager singleton shim
```

### 7.2 GModular Completed Features

All P1-P10 pipeline features are implemented. Remaining gaps are noted in
Section 7.3.

**P1 — Room Assembly Grid** — COMPLETE
- `room_assembly.py` (1240 lines): drag-and-drop 2D top-down grid
- Auto-generates `.lyt` from placed rooms; auto-generates `.vis` from adjacency
- Door-hook scanning via MDL node names; room connection indicators drawn
- Zoom controls, right-click context menu, room rename/delete

**P2 — Binary MDL Renderer** — COMPLETE
- `mdl_parser.py` (1244 lines): full binary MDL/MDX parser (K1 + K2)
- `mdl_renderer.py` (766 lines): ModernGL VAO pipeline, Phong lighting
- Renders actual room geometry and MDL models at their GIT positions
- Frustum culling, LRU model cache (64 models), wireframe/normal debug overlays
- Falls back to coloured placeholder box when no MDL file is found

**P3 — Full WOK Parser and Visualizer** — COMPLETE
- `wok_parser.py` (501 lines): binary .wok parser, AABB tree, per-face materials
- `walkmesh_editor.py` (1153 lines): walkable (green) / non-walkable (red) faces
- Face-paint tool, AABB tree visualiser, ray-cast height queries
- `height_at`, `face_at`, `clamp_to_walkmesh`, `bounds`, `material_counts`
- GWOK export (GModular interchange format; GhostRigger rebuilds native .wok)

**P4 — Visual Patrol Waypoint Linker** — COMPLETE
- `patrol_editor.py` (245 lines): click-to-place waypoints in the viewport
- Auto-names WP_[NPC_TAG]_01, WP_[NPC_TAG]_02... (case-insensitive)
- Dashed path preview line in viewport; waypoints persisted in GIT
- NWScript hint: shows which template to add to OnSpawn

**P5 — Visual Asset Browser** — COMPLETE
- `content_browser.py` (1057 lines): tile/list view toggle, category tree
- Live search; drag-to-place into viewport; asset type icons
- Right-click context menu; populated from game archives via ResourceManager

**P6 — Module Packager (MOD Export)** — COMPLETE
- `mod_packager.py` (750 lines): dependency walker from .git to all ResRefs
- Collects .are/.ifo/.git/.lyt/.vis, all UTx blueprints, .ncs scripts, textures
- `ERFWriter` packs to ERF/MOD/RIM with correct header and resource table
- `mod_packager_dialog.py` (415 lines): checklist UI, size estimate, warnings

**P7 — Script Field IPC Integration** — COMPLETE
- Every script ResRef field in the Inspector has a pencil icon button
- Click pencil: POST `open_script` to GhostScripter (port 7002)
- On `script_compiled` received: auto-fills the ResRef field + marks dirty

**P8 — 2DA Lookup Layer** — COMPLETE
- `twoda_loader.py` (559 lines): full 2DA V2.0 parser, typed getters, search
- `TwoDAComboBox` widget: dropdown backed by any loaded 2DA table
- Inspector shows "Gamorrean Guard (row 47)" not "47" for all 2DA-backed fields
- Built-in fallback tables for headless/test environments

**P9 — Blueprint IPC Integration** — COMPLETE
- Inspector "Edit in GhostRigger" button for any selected GIT object
- POST `open_utc` / `open_utp` / `open_utd` to GhostRigger (port 7001)
- On `blueprint_saved` received: reloads object data + refreshes viewport

**P10 — Module Validation Report** — COMPLETE
- Standalone panel (Module > Validate) plus inline in packager dialog
- Tag uniqueness (case-insensitive across all 7 GIT object types)
- ResRef length <= 16 characters for all fields
- Script ResRef presence check (.ncs in module or Override)
- Door LinkedTo validity check
- Patrol waypoint naming validation (WP_[TAG]_01 must exist in GIT)
- Severity-sorted report (error / warning / info)

### 7.3 Known Remaining Gaps

**Animation playback in viewport**
MDL controller keyframes (position, orientation, scale, alpha) are fully parsed
and stored in `MeshNode`. Wiring a timeline scrubber to step through frames in
the viewport render loop is the remaining work.

**Native KotOR .wok export**
GWOK interchange export works and lets GhostRigger rebuild the geometry.
Producing a byte-for-byte valid KotOR binary `.wok` including the AABB tree
and correct face offset tables is a GhostRigger responsibility.

**DLG dialogue tree editor**
`.dlg` GFF files are fully readable and writable via `gff_reader`/`gff_writer`.
A visual QGraphicsView node-graph editor for building and editing dialogue trees
has not yet been implemented.

**NWScript compiler**
The GhostScripter IPC bridge (`bridges.py`) is complete. The compiler itself
lives in GhostScripter, which must be running on port 7002.

---

## 8. DEVELOPMENT PRIORITIES AND ORDER OF WORK

### Phase 1 — Core Programs Running

Each program should start, show its layout, and have a working IPC server.
Test: launch all three, ping each from the others.

- **GModular:** Complete. All P1-P10 features implemented. See Section 7.2.
- **GhostRigger:** Build main window, blueprint editors (UTC/UTP/UTD),
  and IPC server on port 7001. Start with UTC editor (most used).
- **GhostScripter:** Build main window, script code editor with syntax
  highlighting and function browser, IPC server on port 7002. Start with
  the compile pipeline.

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
- Animation playback timeline (controller data parsed, viewport scrubber not built)
- Native .wok binary export (GWOK export done; KotOR-binary round-trip via GhostRigger)
- DLG dialogue tree visual editor (GFF read/write complete; node-graph UI not built)

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

| Ext    | GFF? | Contents                                 | Used by     |
|--------|------|------------------------------------------|-------------|
| .utc   | yes  | Creature blueprint                       | GRigger, GModular |
| .utp   | yes  | Placeable blueprint                      | GRigger, GModular |
| .utd   | yes  | Door blueprint                           | GRigger, GModular |
| .utw   | yes  | Waypoint blueprint                       | GModular    |
| .utm   | yes  | Merchant/store blueprint                 | GRigger, GModular |
| .uts   | yes  | Sound blueprint                          | GModular    |
| .utt   | yes  | Trigger template                         | GModular    |
| .git   | yes  | Game Instance Table (all placed objects) | GModular    |
| .are   | yes  | Area properties (fog, ambient, rest)     | GModular    |
| .ifo   | yes  | Module info (entry point, start script)  | GModular    |
| .dlg   | yes  | Dialogue tree                            | GScripter   |
| .jrl   | yes  | Journal / quest log                      | GScripter   |
| .lyt   | no   | Room layout: name + XYZ per room         | GModular    |
| .vis   | no   | Visibility: room pairs that see each other| GModular   |
| .wok   | no   | Walkmesh per room (binary face list)     | GModular    |
| .pth   | no   | NPC pathfinding graph                    | GModular    |
| .mdl   | no   | 3D model (binary node tree)              | GRigger, GModular |
| .mdx   | no   | Mesh vertex/normal data (paired w/ mdl)  | GRigger, GModular |
| .2da   | no   | 2D data table (appearance, feats, etc.)  | GScripter, GModular |
| .tlk   | no   | String table (dialog.tlk)                | GScripter   |
| .nss   | no   | NWScript source code                     | GScripter   |
| .ncs   | no   | Compiled NWScript bytecode               | GScripter   |
| .tga   | no   | Texture (TGA format)                     | GRigger     |
| .tpc   | no   | Texture (KotOR proprietary, TGA+mips)    | GRigger     |
| .mod   | no   | ERF archive: complete module package     | GModular    |
| .rim   | no   | ERF archive: module patch/DLC            | GModular    |
| .erf   | no   | ERF archive: generic resource container  | All three   |
| .bif   | no   | BIF archive: game data (indexed by KEY)  | All three (read-only) |

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
