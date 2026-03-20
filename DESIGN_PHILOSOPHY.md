# Ghostworks Pipeline — Design Philosophy
## Structured Design Analysis (Yourdon & Constantine) Applied to KotOR Modding Tools

> *"The goal of structured design is to minimize the total cost of developing and maintaining a system."*
> — Yourdon & Constantine, *Structured Design* (1978)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [The Three-Tool Architecture](#2-the-three-tool-architecture)
3. [What Structured Design Teaches Us](#3-what-structured-design-teaches-us)
4. [Current State Analysis](#4-current-state-analysis)
5. [What Is Missing](#5-what-is-missing)
6. [What Works Well](#6-what-works-well)
7. [IPC Architecture — The Correct Model](#7-ipc-architecture--the-correct-model)
8. [MCP Tool Philosophy — Context-Agnostic Design](#8-mcp-tool-philosophy--context-agnostic-design)
9. [The New Tool Layer](#9-the-new-tool-layer)
10. [Context Mapping: Who Asks What](#10-context-mapping-who-asks-what)
11. [Implementation Roadmap](#11-implementation-roadmap)

---

## 1. System Overview

The Ghostworks Pipeline is a three-tool modding IDE that aims to be the "Unreal Engine" of KotOR modding:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        GHOSTWORKS PIPELINE                                  │
│                                                                             │
│  GhostScripter       GModular           GhostRigger                        │
│  ─────────────       ────────           ───────────                        │
│  NWScript IDE        Module Editor      Model/Rig Tool                     │
│  DLG editor          Area layout        MDL/MDX parser                     │
│  Quest builder       GIT/ARE/IFO        Animation                          │
│  2DA manager         .mod export        Texture tools                      │
│  TLK editor          3D viewport        K1↔K2 porter                       │
│  :6400 MCP (27)      :6480 MCP (38)     :7001 MCP (32)                     │
│                                                                             │
│  All three expose MCP servers + IPC bridges + AgentDecompile               │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Combined MCP surface**: 97 tools across three servers (plus 53 Ghidra tools via AgentDecompile).

The question is not "do we have enough tools?" — we have plenty. The question is:
*"Are the tools designed to serve the model's reasoning, or the developer's mental model of the implementation?"*

---

## 2. The Three-Tool Architecture

### Current IPC topology

```
GhostScripter  ←──────→  GModular  ←──────→  GhostRigger
   :7002 (client)          :7003 (callback)     :7001 (client)
   :6400 (MCP)             :6480 (MCP)           :7001 (MCP)

All three also connect to:
   AgentDecompile  170.9.241.140:8080/mcp/
   Ghidra Server   170.9.241.140:13100
```

### Ghostworks pipeline ports (PIPELINE_SPEC.md)

| Service           | Port  | Role           | Protocol  |
|-------------------|-------|----------------|-----------|
| GhostRigger       | 7001  | GUI IPC server | HTTP REST |
| GhostScripter     | 7002  | GUI IPC server | HTTP REST |
| GModular Callback | 7003  | Inbound calls  | HTTP REST |
| GhostRigger MCP   | 7001  | AI tools       | MCP       |
| GhostScripter MCP | 6400  | AI tools       | MCP       |
| GModular MCP      | 6480  | AI tools       | MCP       |
| AgentDecompile    | 8080  | Ghidra backend | MCP/HTTP  |

---

## 3. What Structured Design Teaches Us

### 3.1 Coupling — the dominant cost driver

Constantine & Yourdon rank coupling from best to worst:

| Level | Name | Mechanism | KotOR Example |
|-------|------|-----------|---------------|
| 1 | **Data coupling** | Pass only needed params | `get_resource(game, resref, type)` |
| 2 | **Stamp coupling** | Pass a struct but use all of it | `describe_resource(entry)` |
| 3 | **Control coupling** | Pass a flag that changes behavior | `read_gff(game, resref, mode="full")` |
| 4 | **Common coupling** | Share global state | `_INSTALLATIONS` dict |
| 5 | **Content coupling** | One module modifies another's internals | Direct DB writes from MCP |

**Diagnosis**: Most current tools are at level 1–2, which is correct. The `loadInstallation` / `_INSTALLATIONS` global cache is level 4 — acceptable but should be documented as a deliberate trade-off for session state.

### 3.2 Cohesion — the measure of "belongs together"

From highest (best) to lowest (worst):

| Level | Name | KotOR Tool Pattern |
|-------|------|--------------------|
| 7 | **Functional** | `get_quest` — does exactly one thing: return quest data |
| 6 | **Sequential** | `compile_script` — validates then compiles |
| 5 | **Communicational** | `kotor_describe_module` — reads GIT + ARE + IFO all for one module |
| 4 | **Procedural** | `detectInstallations + loadInstallation` together |
| 3 | **Temporal** | Initialization tools |
| 2 | **Logical** | A single `query_kotor(type, ...)` switch statement |
| 1 | **Coincidental** | A "utilities" module |

**Diagnosis**: Current tools are mostly at level 5–7. The `describeResource` tool dips to level 3 (temporal) because it mixes metadata with analysis. This is acceptable given its utility, but high-level tools like `get_quest` should be strictly functional.

### 3.3 Transform Analysis — the "mosque shape"

A well-designed system follows a **transform-centered hierarchy**:

```
          [Top: control & coordination]
               /         \
      [Middle: transforms]  [Middle: transforms]
        /    \                /    \
  [Primitives] [Primitives]  [Prim] [Prim]
```

The "mosque shape" says:
- **Top of hierarchy** = broad, coordinator modules (low cohesion but high control)
- **Middle** = transform modules, good fan-out
- **Bottom** = atomic, reusable primitives with HIGH FAN-IN

**Applied**: `get_quest` (top) → calls `find_resource_bytes`, `parse_gff`, `resolve_strref` (bottom primitives with high fan-in).

### 3.4 Information Hiding — hide what changes, expose what's stable

What changes in KotOR modding:
- File locations (override vs module vs chitin)
- Resource formats (GFF v3.2, 2DA v2.0)
- Game-specific constants (K1 vs K2 TLK IDs)
- Script decompiler availability (DeNCS, xoreos-tools, pykotor)

What's stable:
- The concept of a "quest" (tag + JRL + scripts + DLG)
- The concept of a "creature" (UTC + appearance.2da + scripts)
- The concept of a "script" (NSS source + NCS binary + signature)

**Design principle**: MCP tools should expose the *stable concepts*, not the *volatile implementation details*. A tool named `get_quest` is better than `read_jrl_by_tag` because the former hides the JRL format detail.

### 3.5 Fan-in — the measure of reuse

High fan-in = shared primitive used by many callers. This is the purpose of modularity.

Current high-fan-in primitives (good):
- `find_resource_bytes(inst, resref, ext)` — called by all 38 tools
- `_extract_refs(struct)` — called by multiple ref tools
- `json_content(data)` — called everywhere

**New primitives that would have high fan-in**:
- `resolve_tlk_text(inst, strref_or_exolocstr)` — quest names, DLG text, item names
- `parse_gff_as_dict(inst, resref, ext)` — full GFF as flat dict for any tool
- `collect_scripts_from_gff(root)` — all script fields from any GFF
- `describe_creature_brief(inst, resref)` — appearance + scripts in one pass

---

## 4. Current State Analysis

### 4.1 GModular (38 MCP tools)

**Strengths:**
- Clean module-per-tool-category architecture
- Khononov coupling properly applied (EventBus, ResourcePort, ModuleIO)
- Single canonical `find_resource_bytes` (high fan-in primitive)
- Full format coverage: GFF, MDL, WOK, TPC, 2DA, TLK, ERF, LYT
- 1,380 tests, 100% pass rate
- AgentDecompile bridge for binary analysis

**Weaknesses / Missing:**
- No composite "get X" tools — only raw `read_gff`, `lookup_2da` etc.
- No NWScript decompiler integration (NCS → NSS)
- No script compilation
- `kotor_describe_module` is good but not `get_area` (lacks creature list, trigger list)
- No `search` tool (full-text across all resource types)
- No `get_quest` (JRL + scripts + DLG + TLK in one call)
- `kotor_list_references` is useful but requires knowing restype

### 4.2 GhostScripter (27 MCP tools)

**Strengths:**
- NWScript-first: `compileSummary`, `nwscriptSignature`, `searchNWScript`
- `writeDLG` / `writeGFF` — only tool in the suite with write capability
- TSLPatcher `twoDAChangesINI` — unique
- Deep DLG model (`DialogueFile`, `DialogueNode`, `DialogueBranch`)

**Weaknesses / Missing:**
- No `get_quest` (has `journalOverview` and `readJournal` but not composite)
- Binary analysis via `binaryDecompile` overlaps with GModular's `ghidra_decompile`
- `agdecStatus`, `binaryAnalyze` are redundant with GModular's Ghidra tools
- IPC on port 5001/5002 conflicts with PIPELINE_SPEC (7001/7002/7003)

### 4.3 GhostRigger (32 MCP tools)

**Strengths:**
- Only tool that renders MDL models to PNG
- K1↔K2 cross-porter
- Cloth/PBD physics simulation
- Deep model analysis: UV audit, normal check, AABB
- `ghostrigger_audit` is unique and very useful

**Weaknesses / Missing:**
- `ghostrigger_render_model` is not available cross-tool (GModular can't call it)
- No GIT/module placement tools
- Heavy overlap with GModular on GFF/2DA/TLK reading (4 tools duplicated)

### 4.4 Cross-tool redundancy map

| Functionality | GModular | GhostScripter | GhostRigger |
|---------------|----------|---------------|-------------|
| `detectInstallations` | ✅ | ✅ | ✅ |
| `loadInstallation` | ✅ | ✅ | ✅ |
| `listResources` | ✅ | ✅ | ✅ |
| `read_gff` | ✅ | ✅ | ✅ |
| `read_2da` | ✅ | ✅ | ✅ |
| `read_tlk` | ✅ | ✅ | ✅ |
| `journalOverview` | ✅ | ✅ | ✅ |
| `ghidra/binary tools` | ✅ (12) | ✅ (7) | ✅ (11) |
| NWScript compile | ❌ | ✅ | ❌ |
| NWScript decompile | ❌ | ✅ | ❌ |
| DLG write | ❌ | ✅ | ❌ |
| MDL render | ❌ | ❌ | ✅ |
| MDL port K1↔K2 | ❌ | ❌ | ✅ |

**The core problem**: 12 tools are duplicated across all three servers. A model using all three servers sees `detectInstallations` three times. This is **stamp coupling at the protocol level** — each server reimplements the same interface rather than delegating.

---

## 5. What Is Missing

### 5.1 Composite "Get X" tools (highest priority)

These are the tools a **model actually wants to call**. Not "read this binary format" — "tell me about this quest/creature/area."

| Missing Tool | What it aggregates | Use cases |
|---|---|---|
| `get_quest(game, tag)` | JRL entries + state descriptions + TLK text + scripts responsible + DLG files referencing it | Discord bot answering "how do I complete X quest", Cursor writing quest scripts |
| `get_resource(game, resref, type)` | Raw bytes + human-readable text representation | Any context needing the actual resource content |
| `get_creature(game, resref)` | UTC fields + appearance.2da row + scripts + faction | NPC research, companion building |
| `get_conversation(game, resref)` | DLG full tree with TLK-resolved text | Dialogue writing, branching analysis |
| `get_area(game, resref)` | ARE + GIT summary + creature/door/placeable counts + LYT rooms | Module design, area research |
| `get_script(game, resref)` | NCS raw + NSS decompiled source (if available) + which resources call it | Script debugging, reverse engineering |
| `search(game, query)` | Full-text search across 2DA, TLK, resref names, GFF tags | "Find everything related to Bastila" |

### 5.2 NCS decompiler integration

GhostScripter has DeNCS/xoreos-tools integration in the GUI but not in MCP tools. A `get_script` MCP tool should:
1. Get `resref.ncs` bytes
2. Attempt decompilation via: DeNCS CLI → xoreos-tools → pykotor → "raw bytes (no decompiler)"
3. Return NSS source + binary hash + which GFF resources reference this script

### 5.3 Cross-tool IPC — GModular calling GhostRigger

GModular cannot currently call `ghostrigger_render_model`. The IPC bridge exists for GUI↔GUI but not for MCP↔MCP cross-tool calls. This means an agent using GModular's MCP cannot get model renders.

**Solution**: GModular's MCP should proxy `render_model` via the GhostRigger IPC bridge (port 7001), returning the PNG path. This creates a true **transaction center** where GModular acts as the dispatcher.

### 5.4 Missing: TSLPatcher patch generation in GModular

GhostScripter has `twoDAChangesINI` but GModular doesn't. For a full modding workflow, GModular should be able to diff 2DA tables and generate patches.

### 5.5 Missing: Script writing / compilation in GModular MCP

GhostScripter is the compile tool, but GModular's MCP has no way to trigger compilation. An agent building a module from GModular's MCP should be able to call GhostScripter via IPC to compile a script.

---

## 6. What Works Well

### 6.1 The ResourcePort / find_resource_bytes pattern

The single `find_resource_bytes(inst, resref, ext)` primitive is a textbook example of **functional cohesion + high fan-in**. Every tool calls it. If the resource location strategy changes (e.g., adding a remote resource cache), only one function needs updating.

### 6.2 The EventBus pattern in GModular

Contract coupling (publishing named events) rather than direct object references is exactly what Yourdon & Constantine recommend. It means GUI components can be tested in isolation.

### 6.3 The AgentDecompile chain

The proxy architecture (`GModular MCP → agdec-proxy → 170.9.241.140:8080 → Ghidra:13100 → swkotor.exe`) is a well-designed afferent/efferent chain with clear transform layers. No circular dependencies.

### 6.4 KotorInstallation session state

Using a per-game singleton `_INSTALLATIONS[game]` as session state is a pragmatic choice. It's **common coupling** (level 4), but it's bounded: only one module (state.py) owns it, and all others access it via `load_installation()`. This is the correct way to handle common coupling when you can't avoid shared state.

---

## 7. IPC Architecture — The Correct Model

### Current (partially implemented)

```
GhostScripter ←─── HTTP :7002 ───→ GModular ←─── HTTP :7001 ───→ GhostRigger
                  (open/compile)   :7003 cb     (open/render)
```

### What it should be (Transaction Center pattern)

Apply **transaction analysis**: GModular is the transaction center — it dispatches work to the right tool based on the task type.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TRANSACTION CENTER: GModular                             │
│                                                                             │
│  Task type → dispatcher → appropriate handler:                             │
│                                                                             │
│  "compile script foo.nss"  → IPC :7002 → GhostScripter                    │
│  "render model n_bastila"  → IPC :7001 → GhostRigger                      │
│  "decompile function X"    → HTTP :8080 → AgentDecompile                   │
│  "read global.jrl"         → local → GModular ResourceManager              │
│  "edit dialogue"           → IPC :7002 → GhostScripter                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

This is the **mosque shape**: GModular at the top dispatches, specialized tools at the bottom execute.

### IPC call contracts (should be implemented)

```python
# GModular → GhostScripter
POST http://localhost:7002/api/compile    {"script": "foo.nss", "source": "..."}
POST http://localhost:7002/api/open_dlg   {"resref": "c_bastila"}
POST http://localhost:7002/api/open_script {"resref": "k_hench_bastila"}

# GModular → GhostRigger
POST http://localhost:7001/api/render     {"resref": "n_bastila", "azimuth": 45}
POST http://localhost:7001/api/open_model {"resref": "n_bastila"}
POST http://localhost:7001/api/port       {"resref": "n_bastila", "target": "k2"}

# GhostScripter → GModular callback
POST http://localhost:7003/api/compile_result {"success": true, "ncs_path": "..."}

# GhostRigger → GModular callback
POST http://localhost:7003/api/model_ready    {"resref": "n_bastila", "png_path": "..."}
```

---

## 8. MCP Tool Philosophy — Context-Agnostic Design

### The core principle

**Tools should answer questions, not expose formats.**

A tool named `kotor_read_gff` answers: *"Give me the binary GFF file contents as JSON."*
A tool named `get_creature` answers: *"Tell me about this creature."*

The first requires the model to know that creatures are stored in UTC files, that UTC files are GFF format, and how to interpret the GFF fields. The second hides all of that.

### Context-agnostic means:

A tool's description should never imply a specific use case. Compare:

❌ Bad: `"Useful for Cursor/VS Code when writing NWScript functions that reference appearances"`
✅ Good: `"Return a creature template's stats, appearance, and all script references"`

The bad version hardcodes the context (VS Code + NWScript writing). The good version describes what the tool returns, and any context — Discord bot, Cursor, Claude Desktop — can use it.

### The naming convention

Tools should be named after **what they return**, not **how they work**:

| ❌ Old style (how) | ✅ New style (what) |
|---|---|
| `kotor_read_gff` | `get_resource` |
| `kotor_describe_dlg` | `get_conversation` |
| `kotor_describe_jrl` | `get_quest` (when tag given) |
| `kotor_lookup_2da` | `get_resource` (2da type) |
| `kotor_find_referrers` | `find_uses` |
| `kotor_walkmesh_info` | `get_resource` (wok type) |

The new tools *delegate* to the old ones — they don't replace them. The old primitive tools remain for programmatic access. The new tools are high-level and have high fan-in at the primitive level.

### The Structured Design check

For each tool, ask:
1. **Functional cohesion test**: Can you describe the tool in one sentence starting with "Returns..."?
2. **Data coupling test**: Does the caller need to know anything about the implementation to use the tool?
3. **Fan-in test**: Does this tool call existing primitives, or does it duplicate logic?
4. **Information hiding test**: Does the tool expose stable concepts or volatile format details?

---

## 9. The New Tool Layer

### 9.1 `get_resource` — the universal fetch

```
get_resource(game, resref, type)
→ Returns the resource content in the most useful human-readable form

type = "utc"  → UTC fields as structured dict (name, tag, appearance, scripts, …)
type = "dlg"  → Dialogue tree with TLK-resolved text
type = "jrl"  → Journal categories and quest states
type = "2da"  → Table as rows/columns
type = "nss"  → Script source text
type = "ncs"  → Decompiled source (if decompiler available) or base64
type = "are"  → Area properties
type = "git"  → Module object placement summary
type = "mdl"  → Model info (nodes, meshes, animations)
type = "wok"  → Walkmesh info (face count, walkable area)
type = "lyt"  → Room layout
type = "tpc"  → Texture info (format, size, mip levels)
type = "tlk"  → not applicable (use strref lookup)
```

**Why this works**: A Discord bot asking "what does n_bastila.utc contain?" and Cursor writing NWScript that references creature fields both call the same `get_resource(k1, n_bastila, utc)`. The tool doesn't know or care which context it's in.

### 9.2 `get_quest` — the composite quest view

```
get_quest(game, tag)
→ Returns:
  - JRL category and all states with resolved TLK text
  - All scripts referenced by those states
  - All DLG files that reference this quest tag (via kotor_find_referrers)
  - The globalcat.2da row for this quest (if applicable)
  - Global variables associated with the quest (K_SWG_ naming convention)
```

**Markdown output example** (for Discord bot):
```markdown
## Quest: Finding Bastila (tag: `k_swg_bastila`)

**States:**
- 0: Not Started
- 1: Rescue Bastila from Taris (Active)
- 2: Bastila Rescued (Complete)

**Scripts involved:** `k_swg_bas_start`, `k_swg_bas_comp`
**Dialogues:** `tar_vult_01`, `tar_escape_01`
**Global vars:** `K_SWG_BASTILA` (bool), `K_SWG_BASTILA_STATE` (number)
```

### 9.3 `get_conversation` — the full DLG tree

```
get_conversation(game, resref)
→ Returns dialogue tree with:
  - All entries (NPC lines) with TLK-resolved text
  - All replies (player lines) with TLK-resolved text
  - Branch connections (entry → replies, reply → entries)
  - All script references (OnEntry, Script1, Script2, Active conditions)
  - VO sound ResRefs
  - Camera angle settings (TSL)
```

**Why**: An agent writing dialogue branches needs to see the full context, not just entry counts.

### 9.4 `get_creature` — the full NPC profile

```
get_creature(game, resref)
→ Returns:
  - UTC fields: name (TLK resolved), tag, resref, race, gender, class, level, HP
  - Appearance from appearance.2da: model/texture references
  - All script slots: OnHeartbeat, OnDeath, OnDialog, etc.
  - Faction
  - Equipment (item resrefs)
  - Portrait from portraits.2da
```

### 9.5 `get_area` — the full area profile

```
get_area(game, resref)
→ Returns:
  - ARE properties: name, ambient music, fog, sky
  - IFO module info (if loading as a module)
  - GIT summary: creature count, door count, placeable count, trigger count, waypoint count
  - LYT room list
  - Script references from ARE
```

### 9.6 `get_script` — the script profile

```
get_script(game, resref)
→ Returns:
  - NSS source text (if .nss file exists in override or modules)
  - Decompiled source from .ncs (if decompiler available)
  - Function signature (first function in file)
  - All resources that call this script (from kotor_find_referrers)
```

### 9.7 `search` — cross-type full text search

```
search(game, query, types=None, limit=50)
→ Searches across:
  - TLK strings (text match)
  - 2DA values (all columns)
  - Resource names (resref match)
  - GFF Tag fields
  - Script function names (nss files)
  
Returns ranked list of matches with type, resref, field, matched text
```

---

## 10. Context Mapping: Who Asks What

The tools are **context-agnostic** — but understanding the contexts helps verify that the tool design serves all of them without hardcoding any.

### Context 1: Discord Bot (KotorMCP Discord integration)

Users ask natural-language questions about KotOR game data. The bot has no game install — it uses a running MCP server pointed at a game directory.

Example questions:
- "What are the quest states for Finding Bastila?"
- "What scripts does Zaalbar run when you talk to him?"
- "What does appearance row 57 look like?"

**Tools needed**: `get_quest`, `get_creature`, `get_resource`, `search`

**Key requirement**: Output must be readable as Markdown or plain text, not JSON. Tools should return well-formatted text, not raw data structures.

### Context 2: VS Code / Cursor AI assistant

Developer is writing NWScript or editing 2DA files. They ask questions while coding.

Example questions:
- "What parameters does `GetGlobalNumber` take?"
- "What's the resref for the Taris Upper City module?"
- "Show me the DLG tree for c_bastila.dlg"

**Tools needed**: `get_resource`, `get_script`, `get_conversation`, NWScript tools (GhostScripter)

**Key requirement**: Tool output must be precise, code-friendly. Structured JSON is fine here.

### Context 3: Claude Desktop agentic modding session

User asks Claude to help build a new mod. Claude builds things autonomously.

Example requests:
- "Create a quest for a Padawan NPC on Dantooine"
- "Port n_bastila to KotOR 2 format"
- "Add a new 2DA row to appearance.2da for my custom NPC"

**Tools needed**: All tools, especially write tools (`writeDLG`, `writeGFF`, `twoDAChangesINI`), GhostRigger render tools, GhostScripter compile tools.

**Key requirement**: Tools must compose well — output of one tool is often input to another.

### Context 4: CLI research session

Developer is doing binary research, trying to understand how the engine works.

Example:
```bash
agentdecompile-cli --server-url http://170.9.241.140:8080/mcp tool decompile-function \
  --programPath /K1/k1_win_gog_swkotor.exe --function "CExoFile::Read"
```

**Tools needed**: AgentDecompile tools, `get_resource` for cross-referencing

### Context 5: Automated modder workflow (scripts, CI)

A Python script that builds a mod automatically — extracts templates, modifies them, packages them.

**Tools needed**: `get_resource`, `get_quest`, `get_creature`, write tools, `ghostrigger_port`, `compile_script`

### Key insight: the tools are the same across all contexts

`get_quest(k1, "k_swg_bastila")` works in all 5 contexts. The Discord bot formats the output as Markdown. The Cursor assistant shows it in a code comment. Claude uses it to plan quest scripts. The CLI prints JSON. The CI script parses the JSON.

**This is the mark of good design**: context-agnostic, high fan-in, functional cohesion.

---

## 11. Implementation Roadmap

### Phase 1: New composite tools in GModular MCP (immediate)

Add `gmodular/mcp/tools/composite.py` with:
- `get_resource` — universal fetch with human-readable output per type
- `get_quest` — JRL + TLK + scripts + DLG refs
- `get_creature` — UTC + appearance.2da + scripts + faction
- `get_conversation` — DLG full tree with resolved text
- `get_area` — ARE + GIT + LYT
- `get_script` — NSS/NCS + decompile attempt + referrers
- `search` — cross-type full-text search

All tools call existing primitives (`find_resource_bytes`, `_extract_refs`, etc.) — **no new duplicated logic**.

### Phase 2: NCS decompiler integration

Add decompiler chain to `get_script`:
1. Check for `tools/NCSDecompCLI.jar` + Java
2. Check for `ncsdecomp` on PATH (xoreos-tools)
3. Check for `pykotor` module
4. Fallback: return raw bytes as base64 + "no decompiler available" note

### Phase 3: Cross-tool proxy tools in GModular MCP

Add to GModular MCP:
- `render_model(resref, azimuth, elevation)` → calls GhostRigger :7001 IPC, returns PNG path
- `compile_script(resref, source)` → calls GhostScripter :7002 IPC, returns NCS path
- `open_in_ghostscripter(resref, type)` → IPC open request to GhostScripter

### Phase 4: Port unification — reduce overlap

Create a shared `ghostworks_common` package (or just copy the pattern) so:
- All three tools use the same `detectInstallations`, `loadInstallation`, `find_resource_bytes`
- Game-installation logic lives in one place with `fan-in = 3`

### Phase 5: Formatting layer

Add a `format` parameter to composite tools:
- `format="markdown"` → returns Discord/README-friendly text
- `format="json"` → returns structured dict (default)
- `format="brief"` → one-line summary

This is the final expression of context-agnostic design: the **same tool, same data**, different presentation depending on who's asking.

---

## Summary: The Ghostworks Design Rules

1. **Tools answer questions, not expose formats.** `get_quest` > `read_jrl`.
2. **Functional cohesion**: one tool, one purpose. Describe it in one sentence starting with "Returns...".
3. **Data coupling**: pass only what the tool needs. No global state except the session installation cache.
4. **Maximum fan-in**: all new tools call existing primitives. No duplicated format-reading logic.
5. **Information hiding**: hide volatile details (file format, binary encoding) behind stable concept names.
6. **Context-agnostic naming**: no tool name implies a use case. `get_resource` works in a Discord bot, VS Code, and CLI equally.
7. **Transform center**: GModular dispatches to GhostScripter (scripts) and GhostRigger (models). It does not reimplement their functionality.
8. **Mosque shape**: coordinator tools (get_quest) at top, atomic primitives (find_resource_bytes) at bottom with high fan-in.
