"""
GModular — Interactive Tutorial Dialog
=======================================
A toggleable, step-by-step walkthrough of every major feature.

Usage
-----
    from .tutorial_dialog import TutorialDialog
    dlg = TutorialDialog(parent=self)
    dlg.show()          # non-modal, stays on screen while user works
    # or
    dlg.exec_()         # modal
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

try:
    from qtpy.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QScrollArea, QWidget, QFrame, QSizePolicy, QProgressBar,
        QCheckBox, QSplitter, QTextBrowser, QListWidget, QListWidgetItem,
        QApplication, QShortcut,
    )
    from qtpy.QtCore import Qt, Signal, QSize, QTimer
    from qtpy.QtGui import (
        QColor, QPalette, QFont, QKeySequence, QPainter, QPen, QBrush,
        QPixmap, QIcon,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    # Stub out every Qt name used at class-definition time so the module
    # can be imported in headless / test environments without crashing.
    class _Stub:  # type: ignore
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __class_getitem__(cls, item): return cls
    QDialog = _Stub; QWidget = _Stub; QFrame = _Stub  # type: ignore
    QVBoxLayout = QHBoxLayout = QSplitter = QScrollArea = _Stub  # type: ignore
    QPushButton = QLabel = QCheckBox = QProgressBar = _Stub  # type: ignore
    QListWidget = QListWidgetItem = QTextBrowser = _Stub  # type: ignore
    QShortcut = QSizePolicy = QApplication = _Stub  # type: ignore
    Qt = _Stub(); QColor = QFont = QPalette = QPixmap = QIcon = _Stub  # type: ignore
    QKeySequence = QPainter = QPen = QBrush = QSize = QTimer = _Stub  # type: ignore
    Signal = _Stub  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
#  Tutorial content — one dict per step
# ─────────────────────────────────────────────────────────────────────────────

# Each step:
#   title      : shown as section heading
#   category   : sidebar category
#   icon       : emoji icon (displayed in sidebar + header)
#   body       : rich HTML body (shown in the text browser)
#   tip        : short "Pro Tip" callout (optional)
#   diagram    : Python callable(QPainter, W, H) that draws a diagram (optional)

_STEPS: List[dict] = [

    # ── 0. Welcome ────────────────────────────────────────────────────────────
    {
        "title": "Welcome to GModular",
        "category": "Getting Started",
        "icon": "🏁",
        "body": """
<h2 style='color:#4ec9b0;'>Welcome to GModular</h2>
<p>GModular is a Python/Qt toolkit for creating and editing
<b>Star Wars: KotOR 1 &amp; 2</b> module files without needing
the original Aurora Toolset.</p>

<h3 style='color:#9cdcfe;'>Two Modes</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr>
    <td style='padding:6px 10px;background:#1a3a2a;'>
      <b style='color:#4ec9b0;'>⬛ Level Builder</b><br>
      Assemble rooms on the grid, place objects, preview your map.
      Best for building new content from scratch.
    </td>
  </tr>
  <tr>
    <td style='padding:6px 10px;background:#1a2a3a;margin-top:4px;'>
      <b style='color:#9cdcfe;'>✏ Module Editor</b><br>
      Full KotOR module editing: import existing GIT files, edit all GFF fields,
      inspect walkmeshes, export cleaned modules.
    </td>
  </tr>
</table>

<h3 style='color:#9cdcfe;'>What you can do</h3>
<ul>
  <li><b>Assemble rooms</b> on the Room Grid and export a <code>.lyt</code> /
      <code>.vis</code> pair.</li>
  <li><b>Edit GIT objects</b> — placeables, creatures, doors, waypoints,
      triggers, sounds and stores — using the Asset Palette and 3-D Viewport.</li>
  <li><b>Inspect &amp; edit</b> any GFF field in the Inspector panel.</li>
  <li><b>View the walkmesh overlay</b> — see walkable (green) and blocked (red) surfaces.</li>
  <li><b>Pack a playable <code>.mod</code></b> archive with the Module Packager.</li>
  <li><b>Live-preview</b> your module in first-person walk mode (▶ Play).</li>
  <li><b>Script integration</b> via GhostScripter IPC (NWScript editing &amp; compile).</li>
</ul>

<h3 style='color:#9cdcfe;'>How to use this tutorial</h3>
<p>Click a topic in the <b>left sidebar</b> or use the
<b>← Prev / Next →</b> buttons to walk through every feature step by step.
You can keep this window open while you work — it is non-modal.</p>
<p style='color:#569cd6;'>This tutorial appears automatically on first launch.
Press <kbd>F1</kbd> at any time to re-open it.</p>
""",
        "tip": "Start with 'Level Builder vs Module Editor' to understand the two modes.",
    },

    # ── 1. Setting the game directory ─────────────────────────────────────────
    {
        "title": "Set Game Directory",
        "category": "Getting Started",
        "icon": "📁",
        "body": """
<h2 style='color:#4ec9b0;'>Setting your Game Directory</h2>
<p>Before loading assets, tell GModular where KotOR 1 or 2 is installed.</p>

<ol>
  <li>Open <b>File → Set Game Directory</b> (or press the folder icon in the
      toolbar).</li>
  <li>Navigate to your KotOR installation folder, e.g.<br>
      <code>C:\\Program Files (x86)\\Star Wars Knights of The Old Republic</code>
      <br>or the equivalent Steam path.</li>
  <li>Click <b>OK</b>. GModular reads <code>chitin.key</code> and the
      <code>Override/</code>, <code>Modules/</code> and <code>models/</code>
      sub-directories.</li>
</ol>

<h3 style='color:#9cdcfe;'>What gets loaded</h3>
<ul>
  <li><b>Room MDL names</b> populate the Room Grid palette automatically.</li>
  <li><b>Placeable / creature / door blueprints</b> appear in the Asset Palette.</li>
  <li>The <b>Module Packager</b> can now skip base-game assets when building
      your <code>.mod</code>.</li>
</ul>
""",
        "tip": "You only need to do this once — GModular remembers the path between sessions.",
    },

    # ── 2. Creating a new module ──────────────────────────────────────────────
    {
        "title": "Create a New Module",
        "category": "Getting Started",
        "icon": "✨",
        "body": """
<h2 style='color:#4ec9b0;'>Creating a New Module</h2>
<p>Every GModular project is centred on a <b>module</b> — a named collection of
rooms and objects that maps to a single <code>.mod</code> archive.</p>

<ol>
  <li>Click <b>File → New Module</b> or press <kbd>Ctrl+N</kbd>.</li>
  <li>Enter a <b>Module Name</b> (ResRef, max 16 characters, alphanumeric + underscore).</li>
  <li>Choose a <b>save directory</b> for the project files.</li>
  <li>Click <b>Create</b>.</li>
</ol>

<h3 style='color:#9cdcfe;'>What gets created</h3>
<ul>
  <li><code>&lt;name&gt;.git</code> — GIT (Game Instance Table) for all placed objects.</li>
  <li><code>&lt;name&gt;.are</code> — Area properties (lighting, skybox, …).</li>
  <li><code>&lt;name&gt;.ifo</code> — Module info (entry point, scripts, …).</li>
  <li><code>&lt;name&gt;.lyt</code> / <code>.vis</code> — Room layout &amp; visibility
      (generated from the Room Grid).</li>
</ul>

<p>The 3-D viewport switches to the editor and the How-To-Build guide is printed
to the Output Log.</p>
""",
        "tip": "Module names must be ≤ 16 characters and must not start with a digit.",
    },

    # ── 3. Room Grid ──────────────────────────────────────────────────────────
    {
        "title": "Room Grid — Assembling Rooms",
        "category": "Room Grid",
        "icon": "🏗",
        "body": """
<h2 style='color:#4ec9b0;'>Room Grid — Assembling Rooms</h2>
<p>The <b>Room Grid</b> tab (bottom panel) lets you visually lay out the rooms
that make up your module.  Each cell on the grid corresponds to one room tile
in the <code>.lyt</code> file.</p>

<h3 style='color:#9cdcfe;'>Adding rooms</h3>
<ul>
  <li><b>Drag</b> a room name from the left palette onto a grid cell.</li>
  <li>Or <b>right-click</b> a grid cell and choose <em>Place room here</em>.</li>
  <li>Or <b>double-click</b> a room name in the palette to place it at the
      first empty cell.</li>
</ul>

<h3 style='color:#9cdcfe;'>What the 3-D Viewport shows</h3>
<p>As soon as you place a room, GModular rebuilds the viewport geometry:</p>
<ul>
  <li>If the <code>.mdl</code> file is found in the game directory, the actual
      polygon mesh is rendered with Phong shading via <b>ModernGL</b>.</li>
  <li>If no <code>.mdl</code> is available, a coloured <b>placeholder box</b>
      (10 × 10 × 4 world-units) is shown in its place so you can still see the
      layout.</li>
</ul>

<h3 style='color:#9cdcfe;'>Room colours (placeholder mode)</h3>
<table style='border-collapse:collapse;'>
  <tr><td style='padding:2px 8px;background:#405060;'>Blue</td><td>First room</td></tr>
  <tr><td style='padding:2px 8px;background:#405040;'>Green</td><td>Second room</td></tr>
  <tr><td style='padding:2px 8px;background:#604030;'>Orange</td><td>Third room</td></tr>
  <tr><td style='padding:2px 8px;background:#504060;'>Purple</td><td>Fourth + rooms</td></tr>
</table>

<h3 style='color:#9cdcfe;'>Zoom</h3>
<p>Hold <kbd>Ctrl</kbd> and scroll the mouse wheel to zoom the grid in or out
(range: 32 – 120 pixels per cell).</p>
""",
        "tip": "Press F in the 3-D viewport after placing rooms to frame the camera on the scene.",
        "diagram": "_draw_room_grid_diagram",
    },

    # ── 4. Room connections ───────────────────────────────────────────────────
    {
        "title": "Room Connections & Portals",
        "category": "Room Grid",
        "icon": "🔗",
        "body": """
<h2 style='color:#4ec9b0;'>Room Connections &amp; Portals</h2>
<p>Adjacent rooms on the grid are <b>automatically connected</b> in the
<code>.vis</code> file — each room can see its four orthogonal neighbours.</p>

<h3 style='color:#9cdcfe;'>Adding explicit portal connections</h3>
<ol>
  <li>Right-click a room on the grid → <em>Connect to…</em></li>
  <li>Click the second room you want to connect.</li>
  <li>The connection is drawn as a line on the grid and recorded in the
      <code>.vis</code> file.</li>
</ol>

<h3 style='color:#9cdcfe;'>Door-hook snapping</h3>
<p>If a <code>.mdl</code> file has been registered for the room, GModular reads
<b>door-hook nodes</b> (nodes whose name starts with <code>DW_</code> or
<code>doorway_</code>).  When you connect two rooms, their door-hooks snap to
the nearest matching pair automatically.</p>

<h3 style='color:#9cdcfe;'>Removing a room</h3>
<ul>
  <li>Right-click the room on the grid → <em>Remove room</em>.</li>
  <li>All portal connections involving that room are removed too.</li>
</ul>
""",
        "tip": "The .vis file is regenerated every time you change the grid. Click 'Copy VIS' to grab it.",
    },

    # ── 5. 3-D Viewport controls ──────────────────────────────────────────────
    {
        "title": "3-D Viewport — Camera Controls",
        "category": "3D Viewport",
        "icon": "🎥",
        "body": """
<h2 style='color:#4ec9b0;'>3-D Viewport — Camera Controls</h2>
<p>The main 3-D viewport uses an <b>orbit camera</b> (like Maya/Blender)
with Z-up, right-handed coordinates matching KotOR's Odyssey engine.</p>

<h3 style='color:#9cdcfe;'>Mouse controls</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'>
    <th style='padding:4px 10px;text-align:left;'>Action</th>
    <th style='padding:4px 10px;text-align:left;'>Input</th>
  </tr>
  <tr>
    <td style='padding:3px 10px;'>Orbit (rotate around target)</td>
    <td style='padding:3px 10px;'><b>Right Mouse Button</b> drag</td>
  </tr>
  <tr style='background:#2a2a3a;'>
    <td style='padding:3px 10px;'>Pan (move target)</td>
    <td style='padding:3px 10px;'><b>Middle Mouse Button</b> drag</td>
  </tr>
  <tr>
    <td style='padding:3px 10px;'>Zoom</td>
    <td style='padding:3px 10px;'><b>Scroll Wheel</b></td>
  </tr>
  <tr style='background:#2a2a3a;'>
    <td style='padding:3px 10px;'>Select object</td>
    <td style='padding:3px 10px;'><b>Left Click</b> on object</td>
  </tr>
  <tr>
    <td style='padding:3px 10px;'>Place object (in placement mode)</td>
    <td style='padding:3px 10px;'><b>Left Click</b> on ground</td>
  </tr>
</table>

<h3 style='color:#9cdcfe;'>Keyboard shortcuts</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'>
    <th style='padding:4px 10px;text-align:left;'>Key</th>
    <th style='padding:4px 10px;text-align:left;'>Action</th>
  </tr>
  <tr><td style='padding:3px 10px;'><kbd>W A S D</kbd></td><td style='padding:3px 10px;'>Pan camera target</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Q / E</kbd></td><td style='padding:3px 10px;'>Move target down / up</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>F</kbd></td><td style='padding:3px 10px;'>Frame all objects</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Delete</kbd></td><td style='padding:3px 10px;'>Delete selected object</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Escape</kbd></td><td style='padding:3px 10px;'>Cancel placement / cancel gizmo drag</td></tr>
</table>
""",
        "tip": "Double-click any object in the Scene Outline to frame the camera on it.",
    },

    # ── 6. Transform Gizmo ────────────────────────────────────────────────────
    {
        "title": "Transform Gizmo",
        "category": "3D Viewport",
        "icon": "↔",
        "body": """
<h2 style='color:#4ec9b0;'>Transform Gizmo</h2>
<p>When an object is selected in the viewport, a <b>3-axis translate gizmo</b>
and a <b>rotation ring</b> appear over it.</p>

<h3 style='color:#9cdcfe;'>Axes</h3>
<ul>
  <li><span style='color:#e63c3c;'>■ Red arrow</span> — X axis (East/West)</li>
  <li><span style='color:#3cc83c;'>■ Green arrow</span> — Y axis (North/South)</li>
  <li><span style='color:#3c78e6;'>■ Blue arrow</span> — Z axis (Up/Down)</li>
  <li><span style='color:#ddc832;'>○ Yellow ring</span> — Rotate around Z</li>
</ul>

<h3 style='color:#9cdcfe;'>Dragging</h3>
<ol>
  <li>Hover over an arrow — it highlights in bright yellow.</li>
  <li>Click and <b>drag</b> to translate the object along that axis.</li>
  <li>Drag the rotation ring to change the object's bearing.</li>
</ol>

<h3 style='color:#9cdcfe;'>Snapping</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'>
    <th style='padding:4px 10px;text-align:left;'>Modifier</th>
    <th style='padding:4px 10px;text-align:left;'>Snap interval</th>
  </tr>
  <tr><td style='padding:3px 10px;'><kbd>Ctrl</kbd></td><td style='padding:3px 10px;'>1.0 world unit (coarse)</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Shift</kbd></td><td style='padding:3px 10px;'>0.25 units (fine)</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Ctrl + Shift</kbd></td><td style='padding:3px 10px;'>0.5 units (medium)</td></tr>
</table>
<p>A <b>SNAP</b> indicator appears in the bottom-right of the viewport when
snapping is active.</p>
""",
        "tip": "Snapping uses Unreal-style modifier keys — Ctrl for coarse, Shift for fine.",
        "diagram": "_draw_gizmo_diagram",
    },

    # ── 7. Asset Palette ──────────────────────────────────────────────────────
    {
        "title": "Asset Palette",
        "category": "Editing Objects",
        "icon": "🗂",
        "body": """
<h2 style='color:#4ec9b0;'>Asset Palette</h2>
<p>The <b>Asset Palette</b> (left panel) lists every blueprint available from
your game directory, organised into tabs:</p>
<ul>
  <li><b>Placeables</b> — furniture, crates, terminals, …</li>
  <li><b>Creatures</b> — NPCs and enemies</li>
  <li><b>Doors</b> — interior and exterior door prefabs</li>
  <li><b>Sounds</b>, <b>Stores</b>, <b>Triggers</b>, <b>Waypoints</b></li>
</ul>

<h3 style='color:#9cdcfe;'>Placing an asset</h3>
<ol>
  <li>Click an asset in the palette to select it.</li>
  <li>The cursor changes to a <b>cross (+)</b> and a status bar message
      confirms placement mode.</li>
  <li><b>Left-click</b> anywhere on the viewport ground plane to place the
      object at that position.</li>
  <li>The object appears immediately in both the 3-D viewport and the
      Scene Outline.</li>
</ol>

<h3 style='color:#9cdcfe;'>Object appearance in the viewport</h3>
<table style='border-collapse:collapse;'>
  <tr><td style='padding:2px 8px;background:#305080;'>Blue cube</td><td>Placeable</td></tr>
  <tr><td style='padding:2px 8px;background:#803020;'>Orange box</td><td>Creature</td></tr>
  <tr><td style='padding:2px 8px;background:#806010;'>Yellow slab</td><td>Door</td></tr>
  <tr><td style='padding:2px 8px;background:#205040;'>Green disc</td><td>Trigger / Waypoint</td></tr>
  <tr><td style='padding:2px 8px;background:#206040;'>Cyan</td><td>Sound emitter</td></tr>
</table>
""",
        "tip": "Press Escape to exit placement mode without placing an object.",
    },

    # ── 8. Inspector panel ────────────────────────────────────────────────────
    {
        "title": "Inspector Panel",
        "category": "Editing Objects",
        "icon": "🔍",
        "body": """
<h2 style='color:#4ec9b0;'>Inspector Panel</h2>
<p>Selecting any object in the viewport or Scene Outline opens its properties
in the <b>Inspector</b> panel (right side).  Every GFF field is exposed as an
editable row:</p>

<ul>
  <li><b>ResRef fields</b> — 16-char resource reference (blueprint filename)</li>
  <li><b>Tag / Name / Description</b> — free-text labels</li>
  <li><b>Position (X/Y/Z)</b> — world-space coordinates</li>
  <li><b>Bearing</b> — rotation around Z in degrees</li>
  <li><b>Scripts</b> (on_click, on_death, …) — NWScript ResRefs with
      <em>Open in GhostScripter</em> button</li>
  <li><b>Faction, appearance, sound set</b> — integer indices</li>
</ul>

<h3 style='color:#9cdcfe;'>Editing a field</h3>
<ol>
  <li>Click the value cell to the right of the field label.</li>
  <li>Type the new value and press <kbd>Enter</kbd> or click elsewhere.</li>
  <li>The module is marked <em>modified</em> (asterisk in title bar).</li>
</ol>

<h3 style='color:#9cdcfe;'>Script buttons</h3>
<p>Script fields show an extra <b>[→ GhostScripter]</b> button that opens the
script in the external NWScript editor when GhostScripter is running on
port 5002.</p>
""",
        "tip": "Changes in the Inspector are immediately reflected in the 3-D viewport.",
    },

    # ── 9. Scene Outline ──────────────────────────────────────────────────────
    {
        "title": "Scene Outline",
        "category": "Editing Objects",
        "icon": "📋",
        "body": """
<h2 style='color:#4ec9b0;'>Scene Outline</h2>
<p>The <b>Scene Outline</b> panel (right side, below Inspector) lists every GIT
object in the current module, grouped by type.</p>

<h3 style='color:#9cdcfe;'>Interactions</h3>
<ul>
  <li><b>Single-click</b> selects the object and highlights it in the viewport
      (shown in bright yellow).</li>
  <li><b>Double-click</b> selects <em>and</em> frames the camera on that object.</li>
  <li>The outline automatically refreshes when objects are added or removed.</li>
</ul>

<h3 style='color:#9cdcfe;'>Deleting objects</h3>
<ol>
  <li>Select the object in the outline or viewport.</li>
  <li>Press <kbd>Delete</kbd> (viewport focused) or use
      <b>Edit → Delete Selected</b>.</li>
</ol>
""",
        "tip": "Ctrl+Z / Ctrl+Y undo and redo any add or delete operation.",
    },

    # ── 10. Module Packager ───────────────────────────────────────────────────
    {
        "title": "Module Packager",
        "category": "Packaging",
        "icon": "📦",
        "body": """
<h2 style='color:#4ec9b0;'>Module Packager</h2>
<p>The <b>Module Packager</b> bundles your GFF files and optional textures into
a single <code>.mod</code> ERF archive that KotOR can load directly.</p>

<h3 style='color:#9cdcfe;'>Opening the packager</h3>
<p>Go to <b>Module → Package Module…</b> or use the toolbar button.</p>

<h3 style='color:#9cdcfe;'>Build steps</h3>
<ol>
  <li>The packager <b>validates</b> the module first (tag uniqueness, ResRef
      length limits, script existence, door link consistency, …).</li>
  <li>It collects <b>core files</b>: <code>.are</code>, <code>.git</code>,
      <code>.ifo</code>, <code>.lyt</code>, <code>.vis</code>.</li>
  <li>It <b>walks MDL dependencies</b> to find referenced TPC/TGA textures
      and packs them in too (base-game assets are skipped to keep the archive
      small).</li>
  <li>It writes the binary <b>ERF/MOD</b> archive with proper header, key list
      and resource list.</li>
</ol>

<h3 style='color:#9cdcfe;'>Validation issues</h3>
<p>Issues are colour-coded:
<span style='color:#e64646;'>🔴 Error</span> (must fix),
<span style='color:#e6c846;'>🟡 Warning</span> (should review),
<span style='color:#46c846;'>🟢 Info</span> (informational).
</p>
""",
        "tip": "You can run validation alone via Module → Validation Report without building.",
    },

    # ── 11. Walkmesh Editor ───────────────────────────────────────────────────
    {
        "title": "Walkmesh Editor",
        "category": "Advanced",
        "icon": "🗺",
        "body": """
<h2 style='color:#4ec9b0;'>Walkmesh Editor</h2>
<p>The <b>Walkmesh Editor</b> tab lets you load and visualise the
<code>.wok</code> (WalkmeshObject) file that defines which surfaces
are walkable in a room.</p>

<h3 style='color:#9cdcfe;'>Surface types &amp; colours</h3>
<table style='border-collapse:collapse;font-size:0.9em;'>
  <tr style='background:#2a2a3a;'><th style='padding:3px 8px;'>Colour</th><th style='padding:3px 8px;'>Surface</th></tr>
  <tr><td style='padding:2px 8px;background:#ff4444;'>Red</td><td>Non-Walk (blocked)</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:2px 8px;background:#44ff44;color:#000;'>Green</td><td>Walk (normal floor)</td></tr>
  <tr><td style='padding:2px 8px;background:#4444ff;'>Blue</td><td>Trigger region</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:2px 8px;background:#ffaa44;'>Orange</td><td>Dirt</td></tr>
  <tr><td style='padding:2px 8px;background:#44aaff;'>Sky Blue</td><td>Shallow Water</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:2px 8px;background:#ff2200;'>Lava Red</td><td>Lava (deadly)</td></tr>
</table>

<h3 style='color:#9cdcfe;'>AABB ray-cast API</h3>
<p>When a <code>.wok</code> is loaded, an <b>AABB binary tree</b> is built
automatically, enabling O(log N) queries:</p>
<ul>
  <li><code>raycast(origin, dir)</code> — nearest walkable hit</li>
  <li><code>raycast_vertical(x, y)</code> — Z height at (x, y)</li>
  <li><code>query_sphere(center, radius)</code> — all faces within sphere</li>
  <li><code>face_at(x, y)</code> — face type at world (x, y)</li>
</ul>
""",
        "tip": "The AABB tree is also used by Play Mode to keep the player on the walkable surface.",
    },

    # ── 12. Play / Preview mode ───────────────────────────────────────────────
    {
        "title": "Play / Preview Mode",
        "category": "Advanced",
        "icon": "▶",
        "body": """
<h2 style='color:#4ec9b0;'>Play / Preview Mode</h2>
<p>The <b>Play</b> button (toolbar) enters a first-person walk preview of your
module — useful for checking room scale and object placement before packaging.</p>

<h3 style='color:#9cdcfe;'>Controls in play mode</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'><th style='padding:4px 10px;text-align:left;'>Key</th><th style='padding:4px 10px;text-align:left;'>Action</th></tr>
  <tr><td style='padding:3px 10px;'><kbd>W / S</kbd></td><td style='padding:3px 10px;'>Move forward / back</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>A / D</kbd></td><td style='padding:3px 10px;'>Turn left / right</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Q / E</kbd></td><td style='padding:3px 10px;'>Strafe left / right</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Shift</kbd> (held)</td><td style='padding:3px 10px;'>Run (2× speed)</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Mouse move</kbd></td><td style='padding:3px 10px;'>Look around</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Escape</kbd></td><td style='padding:3px 10px;'>Exit play mode</td></tr>
</table>

<h3 style='color:#9cdcfe;'>What is rendered</h3>
<p>In play mode the <b>MDL room geometry</b> is shown with Phong lighting
(if the <code>.mdl</code> files were found in the game directory).  NPC
instances from the GIT are visible as orange boxes.</p>

<p>The player is constrained to the <b>walkable</b> faces of the walkmesh.
Collision is resolved using the AABB tree.</p>
""",
        "tip": "Press F1 inside play mode (after pressing Escape) to re-read this tutorial.",
    },

    # ── 13. IPC / GhostScripter ───────────────────────────────────────────────
    {
        "title": "IPC — GhostScripter & GhostRigger",
        "category": "Advanced",
        "icon": "🔌",
        "body": """
<h2 style='color:#4ec9b0;'>IPC Integration</h2>
<p>GModular exposes a simple JSON-over-TCP IPC layer so that external tools
can communicate with it while it is running.</p>

<h3 style='color:#9cdcfe;'>Ports</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'><th style='padding:4px 10px;'>Port</th><th style='padding:4px 10px;'>Tool</th><th style='padding:4px 10px;'>Purpose</th></tr>
  <tr><td style='padding:3px 10px;'>5001</td><td style='padding:3px 10px;'>GhostRigger</td><td style='padding:3px 10px;'>Model rigging &amp; animation</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'>5002</td><td style='padding:3px 10px;'>GhostScripter</td><td style='padding:3px 10px;'>NWScript editing &amp; compile</td></tr>
  <tr><td style='padding:3px 10px;'>5003</td><td style='padding:3px 10px;'>Callback server</td><td style='padding:3px 10px;'>Inbound events to GModular</td></tr>
</table>

<h3 style='color:#9cdcfe;'>Script workflow</h3>
<ol>
  <li>Launch <b>GhostScripter</b> (separate application, port 5002).</li>
  <li>In the Inspector, click the <b>[→ GhostScripter]</b> button next to any
      script field.</li>
  <li>Edit the script in GhostScripter.</li>
  <li>Click <b>Compile</b> — the compiled <code>.ncs</code> is saved to your
      module directory automatically.</li>
</ol>
""",
        "tip": "GModular starts the callback server on port 5003 automatically at launch.",
    },

    # ── 14. 2DA Loader ────────────────────────────────────────────────────────
    {
        "title": "2DA Loader",
        "category": "Advanced",
        "icon": "📊",
        "body": """
<h2 style='color:#4ec9b0;'>2DA Loader</h2>
<p>KotOR stores many game tables in <b>2DA</b> (two-dimensional array) text
files — faction names, appearance IDs, class names, etc.  GModular ships a
built-in <code>TwoDALoader</code> that you can query from Python scripts or
the IPC interface.</p>

<h3 style='color:#9cdcfe;'>Python API</h3>
<pre style='background:#1a1a2e;padding:8px;border-radius:4px;font-size:0.85em;'>
from gmodular.formats.twoda_loader import get_2da_loader

loader = get_2da_loader()
loader.set_search_dirs(["path/to/game/Override"])

# Load table by name
table = loader.load("appearance")

# Look up a value
name = table.get(row=5, column="LABEL")

# Find a row by value
row_idx = table.find_row("LABEL", "N_DarthRevan")

# Iterate all values in a column
for idx, val in table.column_values("LABEL"):
    print(idx, val)
</pre>

<h3 style='color:#9cdcfe;'>Fallback tables</h3>
<p>Even without a game directory, the loader ships built-in fallback data for
<code>faction</code>, <code>gender</code>, and <code>classes</code>.</p>
""",
        "tip": "call load_fallback_tables() to pre-populate the loader with built-in data.",
    },

    # ── 15. Saving & loading ──────────────────────────────────────────────────
    {
        "title": "Saving & Loading",
        "category": "Getting Started",
        "icon": "💾",
        "body": """
<h2 style='color:#4ec9b0;'>Saving &amp; Loading</h2>

<h3 style='color:#9cdcfe;'>Save</h3>
<ul>
  <li><b>File → Save</b> (<kbd>Ctrl+S</kbd>) — writes all GFF files to the
      current module directory.</li>
  <li><b>File → Save As…</b> (<kbd>Ctrl+Shift+S</kbd>) — choose a new directory
      and name.</li>
  <li>The title bar shows an asterisk (<b>*</b>) when there are unsaved changes.</li>
</ul>

<h3 style='color:#9cdcfe;'>Load</h3>
<ul>
  <li><b>File → Open Module…</b> (<kbd>Ctrl+O</kbd>) — open a folder containing
      <code>.are</code> / <code>.git</code> / <code>.ifo</code> files.</li>
  <li><b>File → Recent Modules</b> — quickly re-open recent projects.</li>
</ul>

<h3 style='color:#9cdcfe;'>Auto-generated files</h3>
<p>The <code>.lyt</code> and <code>.vis</code> files are regenerated from the
Room Grid every time you save.  You do not edit them manually.</p>

<h3 style='color:#9cdcfe;'>LYT / VIS round-trip</h3>
<p>If your module folder already contains a <code>.lyt</code> file from a
previous session, GModular can parse it back in and repopulate the Room Grid:</p>
<pre style='background:#1a1a2e;padding:8px;border-radius:4px;font-size:0.85em;'>
from gmodular.gui.room_assembly import LYTData
lyt = LYTData.from_file("path/to/module.lyt")
room_panel.set_rooms(lyt.rooms)
</pre>
""",
        "tip": "Ctrl+Z / Ctrl+Y undo/redo any object placement or deletion.",
    },

    # ── 16. Keyboard shortcut reference ───────────────────────────────────────
    {
        "title": "Keyboard Shortcut Reference",
        "category": "Reference",
        "icon": "⌨",
        "body": """
<h2 style='color:#4ec9b0;'>Keyboard Shortcut Reference</h2>

<h3 style='color:#9cdcfe;'>Global</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'><th style='padding:4px 10px;text-align:left;'>Shortcut</th><th style='padding:4px 10px;text-align:left;'>Action</th></tr>
  <tr><td style='padding:3px 10px;'><kbd>Ctrl+N</kbd></td><td style='padding:3px 10px;'>New Module</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Ctrl+O</kbd></td><td style='padding:3px 10px;'>Open Module</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Ctrl+S</kbd></td><td style='padding:3px 10px;'>Save</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Ctrl+Z</kbd></td><td style='padding:3px 10px;'>Undo</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Ctrl+Y</kbd></td><td style='padding:3px 10px;'>Redo</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>F1</kbd></td><td style='padding:3px 10px;'>Open Tutorial</td></tr>
</table>

<h3 style='color:#9cdcfe;'>Viewport (editor mode)</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'><th style='padding:4px 10px;text-align:left;'>Shortcut</th><th style='padding:4px 10px;text-align:left;'>Action</th></tr>
  <tr><td style='padding:3px 10px;'><kbd>F</kbd></td><td style='padding:3px 10px;'>Frame all / frame selected</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Delete</kbd></td><td style='padding:3px 10px;'>Delete selected object</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>W/A/S/D/Q/E</kbd></td><td style='padding:3px 10px;'>Pan camera target</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Ctrl</kbd> + drag</td><td style='padding:3px 10px;'>Snap 1.0 u</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Shift</kbd> + drag</td><td style='padding:3px 10px;'>Snap 0.25 u</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Ctrl+Shift</kbd> + drag</td><td style='padding:3px 10px;'>Snap 0.5 u</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Escape</kbd></td><td style='padding:3px 10px;'>Cancel placement / gizmo drag</td></tr>
</table>

<h3 style='color:#9cdcfe;'>Room Grid</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'><th style='padding:4px 10px;text-align:left;'>Shortcut</th><th style='padding:4px 10px;text-align:left;'>Action</th></tr>
  <tr><td style='padding:3px 10px;'><kbd>Ctrl + Scroll</kbd></td><td style='padding:3px 10px;'>Zoom grid in/out</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'>Right-click</td><td style='padding:3px 10px;'>Context menu: place, remove, connect</td></tr>
  <tr><td style='padding:3px 10px;'>Drag from palette</td><td style='padding:3px 10px;'>Place room at dropped cell</td></tr>
</table>
""",
        "tip": "All shortcuts work when the respective widget has keyboard focus.",
    },

    # ── New: Two Operating Modes ──────────────────────────────────────────────
    {
        "title": "Level Builder vs Module Editor",
        "category": "Getting Started",
        "icon": "⚙",
        "body": """
<h2 style='color:#4ec9b0;'>Two Operating Modes</h2>
<p>GModular has two distinct modes accessible from the <b>mode switcher</b>
in the top bar:</p>

<h3 style='color:#80d4b0;'>⬛  Level Builder</h3>
<p>Designed for <b>building new maps from scratch</b>.</p>
<ul>
  <li>Assemble rooms on the <b>Room Grid</b> (bottom panel).</li>
  <li>Rooms snap together edge-to-edge so walkmeshes connect seamlessly.</li>
  <li>Place placeables, creatures, doors, waypoints in the 3-D viewport.</li>
  <li>Preview your map in <b>first-person walk mode</b> (▶ Play).</li>
  <li>Export <code>.lyt</code>, <code>.vis</code>, and <code>.git</code>.</li>
  <li>Pack to <code>.mod</code> with the Module Packager.</li>
</ul>

<h3 style='color:#9cdcfe;'>✏  Module Editor</h3>
<p>Designed for <b>editing existing KotOR modules</b> — full Blender-like power.</p>
<ul>
  <li>Import <code>.git</code> files from existing <code>.mod</code>/<code>.rim</code>
      archives.</li>
  <li>Move, rotate, and resize placed objects with the <b>transform gizmo</b>.</li>
  <li>Edit every GFF field in the <b>Inspector</b> panel.</li>
  <li>View and edit the <b>walkmesh overlay</b> in the 3-D viewport.</li>
  <li>Use the <b>Walkmesh Editor</b> (bottom tab) to load and inspect
      <code>.wok</code> files.</li>
  <li>Export cleaned modules for use with KotOR / TSL override directories.</li>
</ul>

<h3 style='color:#9cdcfe;'>How to switch</h3>
<p>Click the <b>mode dropdown</b> in the top viewport bar (reads
<em>⬛ Level Builder</em> or <em>✏ Module Editor</em>).
The viewport badge and toolbar update immediately.</p>
""",
        "tip": "You can switch modes at any time — your work is preserved.",
    },

    # ── New: Walkmesh Tools ───────────────────────────────────────────────────
    {
        "title": "Walkmesh — Overlay & Editor",
        "category": "Advanced",
        "icon": "🗺",
        "body": """
<h2 style='color:#4ec9b0;'>Walkmesh Overlay &amp; Editor</h2>

<h3 style='color:#80d4b0;'>What is a walkmesh?</h3>
<p>In KotOR every area has a <b>walkmesh</b> (<code>.wok</code> file) that defines
which triangles characters can walk on.  GModular shows this as a
<b>semi-transparent colour overlay</b> on top of room geometry:</p>
<ul>
  <li><span style='color:#44ff44;'>■ Green</span> — walkable surface</li>
  <li><span style='color:#ff4444;'>■ Red</span> — blocked / non-walkable</li>
</ul>

<h3 style='color:#9cdcfe;'>Toggling the overlay</h3>
<ul>
  <li>Press <kbd>W</kbd> in the 3-D viewport (while not flying) to toggle.</li>
  <li>Or click <b>⊞ Walkmesh</b> in the viewport toolbar.</li>
  <li>The <em>WALKMESH ON/OFF</em> indicator appears at the bottom-right.</li>
</ul>

<h3 style='color:#9cdcfe;'>Walkmesh Editor tab</h3>
<p>Open the <b>Walkmesh Editor</b> in the bottom panel to:</p>
<ul>
  <li>Load a <code>.wok</code> file from disk.</li>
  <li>Inspect all face types (Walk, Dirt, Grass, Water, etc.).</li>
  <li>Change surface type for individual triangles.</li>
  <li>Export a modified <code>.wok</code> for use in-game.</li>
</ul>

<h3 style='color:#9cdcfe;'>Room-snapping &amp; walkmesh connectivity</h3>
<p>When rooms are placed on the Room Grid, GModular automatically aligns them
so that shared edges are within <b>0.5 world units</b> of each other.
This ensures the walkmeshes of adjacent rooms connect without gaps that
would trap characters.</p>
<ul>
  <li>Rooms snap at 10-unit boundaries in the Level Builder.</li>
  <li>Shared walkmesh edges are marked in the overlay so you can verify
      connectivity at a glance.</li>
</ul>
""",
        "tip": "If characters get stuck at room borders, check the walkmesh overlay for red gaps.",
    },

    # ── New: Viewport Navigation Deep-Dive ───────────────────────────────────
    {
        "title": "Viewport Navigation & Flying",
        "category": "3D Viewport",
        "icon": "✈",
        "body": """
<h2 style='color:#4ec9b0;'>Viewport Navigation &amp; Flying</h2>

<h3 style='color:#9cdcfe;'>Orbit / Pan / Zoom (all modes)</h3>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'>
    <th style='padding:4px 10px;text-align:left;'>Mouse</th>
    <th style='padding:4px 10px;text-align:left;'>Action</th>
  </tr>
  <tr><td style='padding:3px 10px;'>Right-drag</td><td>Orbit (rotate around target)</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'>Middle-drag</td><td>Pan (slide target)</td></tr>
  <tr><td style='padding:3px 10px;'>Scroll wheel</td><td>Zoom in/out</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'>Left-click</td><td>Select object (or place in PLACE mode)</td></tr>
</table>

<h3 style='color:#9cdcfe;'>Keyboard fly-through (WASD)</h3>
<p>While the viewport has <b>keyboard focus</b>:</p>
<table style='border-collapse:collapse;width:100%;'>
  <tr style='background:#2a2a3a;'>
    <th style='padding:4px 10px;text-align:left;'>Key</th>
    <th style='padding:4px 10px;text-align:left;'>Action</th>
  </tr>
  <tr><td style='padding:3px 10px;'><kbd>W / S</kbd></td><td>Fly forward / back</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>A / D</kbd></td><td>Strafe left / right</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Q / E</kbd></td><td>Fly down / up</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Shift</kbd> + any</td><td>2.5× speed boost</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>F</kbd></td><td>Frame all objects</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>W</kbd> alone</td><td>Toggle walkmesh overlay</td></tr>
  <tr><td style='padding:3px 10px;'><kbd>Delete</kbd></td><td>Delete selected</td></tr>
  <tr style='background:#2a2a3a;'><td style='padding:3px 10px;'><kbd>Escape</kbd></td><td>Cancel action</td></tr>
</table>

<h3 style='color:#9cdcfe;'>Play Preview mode</h3>
<p>Click <b>▶ Play</b> to enter first-person walk mode:</p>
<ul>
  <li>Mouse controls look direction.</li>
  <li><kbd>W/A/S/D</kbd> to walk, <kbd>Shift</kbd> to run.</li>
  <li>Press <kbd>Esc</kbd> to return to the editor.</li>
</ul>
""",
        "tip": "Speed automatically scales with zoom distance — zoom in for precise placement, zoom out for fast navigation.",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  Diagram painters
# ─────────────────────────────────────────────────────────────────────────────

def _draw_room_grid_diagram(p: "QPainter", W: int, H: int):
    """Draw a simple 3×2 room grid schematic."""
    p.fillRect(0, 0, W, H, QColor(18, 18, 28))
    cell = min(W // 4, H // 3) - 6
    ox   = (W - cell * 3) // 2
    oy   = (H - cell * 2) // 2
    colors = [
        QColor(80, 120, 160), QColor(80, 130, 90),
        QColor(150, 100, 60), QColor(100, 80, 150),
        QColor(80, 130, 130), QColor(130, 120, 80),
    ]
    for row in range(2):
        for col in range(3):
            idx = row * 3 + col
            x = ox + col * cell
            y = oy + row * cell
            p.fillRect(x + 2, y + 2, cell - 4, cell - 4, colors[idx])
            p.setPen(QPen(colors[idx].lighter(160), 2))
            p.drawRect(x + 2, y + 2, cell - 4, cell - 4)
    # Connection lines
    p.setPen(QPen(QColor(255, 220, 60), 2))
    for row in range(2):
        for col in range(2):
            x1 = ox + col * cell + cell - 2
            y1 = oy + row * cell + cell // 2
            x2 = ox + (col + 1) * cell + 2
            y2 = y1
            p.drawLine(x1, y1, x2, y2)
    # Label
    p.setPen(QPen(QColor(180, 180, 180)))
    p.setFont(QFont("Consolas", 8))
    p.drawText(0, H - 6, W, 20, Qt.AlignCenter, "Room Grid — 3×2 layout example")


def _draw_gizmo_diagram(p: "QPainter", W: int, H: int):
    """Draw a simple gizmo diagram."""
    p.fillRect(0, 0, W, H, QColor(18, 18, 28))
    cx, cy = W // 2, H // 2
    arm = min(W, H) // 3

    # Object dot
    p.setBrush(QBrush(QColor(200, 200, 200)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(cx - 6, cy - 6, 12, 12)

    # X axis (red)
    p.setPen(QPen(QColor(220, 60, 60), 3))
    p.drawLine(cx, cy, cx + arm, cy)
    p.drawText(cx + arm + 4, cy + 5, "X")

    # Y axis (green)  — Y is up in this 2-D diagram
    p.setPen(QPen(QColor(60, 200, 60), 3))
    p.drawLine(cx, cy, cx, cy - arm)
    p.drawText(cx + 4, cy - arm - 4, "Y")

    # Z axis (blue)   — diagonal
    p.setPen(QPen(QColor(60, 120, 220), 3))
    p.drawLine(cx, cy, cx - arm // 2, cy - arm // 2)
    p.drawText(cx - arm // 2 - 16, cy - arm // 2 - 4, "Z")

    # Rotation ring
    p.setPen(QPen(QColor(200, 200, 50), 2, Qt.DashLine))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(cx - arm // 2, cy - arm // 2, arm, arm)

    p.setPen(QPen(QColor(160, 160, 160)))
    p.setFont(QFont("Consolas", 8))
    p.drawText(0, H - 6, W, 20, Qt.AlignCenter, "Transform Gizmo — XYZ arrows + rotation ring")


_DIAGRAM_PAINTERS = {
    "_draw_room_grid_diagram": _draw_room_grid_diagram,
    "_draw_gizmo_diagram":     _draw_gizmo_diagram,
}


# ─────────────────────────────────────────────────────────────────────────────
#  DiagramWidget
# ─────────────────────────────────────────────────────────────────────────────

class DiagramWidget(QWidget):
    """Widget that delegates its paintEvent to a callable."""

    def __init__(self, painter_fn, parent=None):
        super().__init__(parent)
        self._fn = painter_fn
        self.setMinimumHeight(140)
        self.setMaximumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._fn(p, self.width(), self.height())
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  TutorialDialog
# ─────────────────────────────────────────────────────────────────────────────

class TutorialDialog(QDialog):
    """
    Toggleable step-by-step tutorial for GModular.

    Use ``show()`` for a non-modal window the user keeps open while working,
    or ``exec_()`` for modal use.
    """

    #: Emitted when the user closes / hides the dialog
    closed = Signal()

    def __init__(self, parent=None, start_step: int = 0):
        super().__init__(parent)
        self._step = max(0, min(start_step, len(_STEPS) - 1))
        self._build_ui()
        self._populate_sidebar()
        self._show_step(self._step)
        # F1 / Escape shortcuts
        QShortcut(QKeySequence("F1"),     self, self.close)
        QShortcut(QKeySequence("Escape"), self, self.close)

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("GModular Tutorial")
        self.setMinimumSize(820, 620)
        self.resize(920, 680)
        self.setModal(False)

        # Dark palette
        pal = QPalette()
        pal.setColor(QPalette.Window,      QColor(24, 24, 36))
        pal.setColor(QPalette.WindowText,  QColor(210, 210, 210))
        pal.setColor(QPalette.Base,        QColor(18, 18, 28))
        pal.setColor(QPalette.Text,        QColor(210, 210, 210))
        pal.setColor(QPalette.Button,      QColor(40, 40, 60))
        pal.setColor(QPalette.ButtonText,  QColor(210, 210, 210))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(52)
        header.setStyleSheet(
            "background:#1a1a2e; border-bottom:1px solid #3c3c5a;")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 0, 16, 0)

        self._header_icon = QLabel("🏁")
        self._header_icon.setFont(QFont("Segoe UI Emoji", 18))
        hlay.addWidget(self._header_icon)

        self._header_title = QLabel("Welcome to GModular")
        self._header_title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self._header_title.setStyleSheet("color:#4ec9b0; margin-left:8px;")
        hlay.addWidget(self._header_title)

        hlay.addStretch()

        # Progress
        self._progress = QProgressBar()
        self._progress.setFixedWidth(160)
        self._progress.setFixedHeight(10)
        self._progress.setRange(0, len(_STEPS) - 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar{background:#2a2a42;border-radius:5px;}"
            "QProgressBar::chunk{background:#4ec9b0;border-radius:5px;}")
        hlay.addWidget(self._progress)

        self._step_label = QLabel("1 / 17")
        self._step_label.setStyleSheet("color:#888;margin-left:6px;font-size:9pt;")
        hlay.addWidget(self._step_label)

        outer.addWidget(header)

        # ── Body splitter ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle{background:#2a2a42; width:3px;}")
        outer.addWidget(splitter, 1)

        # Left sidebar
        sidebar_w = QWidget()
        sidebar_w.setFixedWidth(200)
        sidebar_w.setStyleSheet("background:#1a1a2e;")
        sb_lay = QVBoxLayout(sidebar_w)
        sb_lay.setContentsMargins(0, 8, 0, 0)
        sb_lay.setSpacing(0)

        sidebar_title = QLabel("  Topics")
        sidebar_title.setFont(QFont("Segoe UI", 9))
        sidebar_title.setStyleSheet("color:#666; padding:4px 0 8px 8px;")
        sb_lay.addWidget(sidebar_title)

        self._sidebar = QListWidget()
        self._sidebar.setStyleSheet(
            "QListWidget{background:#1a1a2e;border:none;outline:none;}"
            "QListWidget::item{color:#aaa;padding:6px 10px;border:none;"
            "border-left:3px solid transparent;}"
            "QListWidget::item:selected{background:#252540;color:#4ec9b0;"
            "border-left:3px solid #4ec9b0;}"
            "QListWidget::item:hover:!selected{background:#20203a;}")
        self._sidebar.currentRowChanged.connect(self._show_step)
        sb_lay.addWidget(self._sidebar, 1)
        splitter.addWidget(sidebar_w)

        # Right content area
        content_w = QWidget()
        content_w.setStyleSheet("background:#18182a;")
        c_lay = QVBoxLayout(content_w)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#18182a;}"
            "QScrollBar:vertical{background:#1a1a2e;width:10px;}"
            "QScrollBar::handle:vertical{background:#3a3a5a;border-radius:4px;}"
        )
        self._content_inner = QWidget()
        self._content_inner.setStyleSheet("background:#18182a;")
        self._inner_lay = QVBoxLayout(self._content_inner)
        self._inner_lay.setContentsMargins(20, 20, 20, 20)
        self._inner_lay.setSpacing(12)

        self._diagram_widget: Optional[DiagramWidget] = None
        self._body_browser = QTextBrowser()
        self._body_browser.setStyleSheet(
            "QTextBrowser{background:#18182a;border:none;color:#d4d4d4;"
            "font-size:10pt;line-height:1.5;}"
            "a{color:#4ec9b0;}")
        self._body_browser.setOpenExternalLinks(True)
        self._inner_lay.addWidget(self._body_browser)

        self._tip_frame = QFrame()
        self._tip_frame.setStyleSheet(
            "QFrame{background:#1e2a3a;border-left:3px solid #4ec9b0;"
            "padding:6px 10px;border-radius:2px;}")
        tip_lay = QHBoxLayout(self._tip_frame)
        tip_lay.setContentsMargins(8, 6, 8, 6)
        tip_icon = QLabel("💡")
        tip_icon.setFont(QFont("Segoe UI Emoji", 11))
        tip_lay.addWidget(tip_icon)
        self._tip_label = QLabel()
        self._tip_label.setWordWrap(True)
        self._tip_label.setStyleSheet("color:#b0d0e8;font-style:italic;font-size:9pt;")
        tip_lay.addWidget(self._tip_label, 1)
        self._inner_lay.addWidget(self._tip_frame)

        self._inner_lay.addStretch()
        scroll.setWidget(self._content_inner)
        c_lay.addWidget(scroll, 1)

        # Navigation bar
        nav = QFrame()
        nav.setFixedHeight(52)
        nav.setStyleSheet(
            "QFrame{background:#1a1a2e;border-top:1px solid #2a2a42;}")
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(16, 0, 16, 0)

        # "Don't show again" checkbox
        self._no_show = QCheckBox("Don't show at startup")
        self._no_show.setStyleSheet("color:#666;font-size:9pt;")
        nav_lay.addWidget(self._no_show)
        nav_lay.addStretch()

        btn_style = (
            "QPushButton{background:#2a2a4a;color:#d4d4d4;border:1px solid #3a3a5a;"
            "border-radius:4px;padding:6px 18px;font-size:10pt;}"
            "QPushButton:hover{background:#3a3a6a;border-color:#4ec9b0;}"
            "QPushButton:pressed{background:#1a1a3a;}"
            "QPushButton:disabled{color:#444;}"
        )
        self._btn_prev = QPushButton("← Prev")
        self._btn_prev.setStyleSheet(btn_style)
        self._btn_prev.clicked.connect(self._prev)
        nav_lay.addWidget(self._btn_prev)

        self._btn_next = QPushButton("Next →")
        self._btn_next.setStyleSheet(
            btn_style.replace("2a2a4a", "2a4a3a").replace("3a3a6a", "3a6a5a"))
        self._btn_next.clicked.connect(self._next)
        nav_lay.addWidget(self._btn_next)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(btn_style)
        btn_close.clicked.connect(self.close)
        nav_lay.addWidget(btn_close)

        c_lay.addWidget(nav)
        splitter.addWidget(content_w)
        splitter.setSizes([200, 720])

    def _populate_sidebar(self):
        """Fill the sidebar list with all step titles grouped by category."""
        current_cat = None
        for idx, step in enumerate(_STEPS):
            cat = step.get("category", "")
            if cat != current_cat:
                # Category header (non-selectable)
                header_item = QListWidgetItem(f"  {cat}")
                header_item.setFlags(Qt.NoItemFlags)
                header_item.setFont(QFont("Segoe UI", 8, QFont.Bold))
                header_item.setForeground(QColor("#5566aa"))
                self._sidebar.addItem(header_item)
                current_cat = cat
            icon = step.get("icon", "•")
            item = QListWidgetItem(f"  {icon}  {step['title']}")
            item.setData(Qt.UserRole, idx)
            item.setFont(QFont("Segoe UI", 9))
            self._sidebar.addItem(item)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _prev(self):
        if self._step > 0:
            self.goto_step(self._step - 1)

    def _next(self):
        if self._step < len(_STEPS) - 1:
            self.goto_step(self._step + 1)

    def goto_step(self, idx: int):
        """Navigate to a specific step (0-based)."""
        idx = max(0, min(idx, len(_STEPS) - 1))
        self._step = idx
        # Sync sidebar selection (skip non-selectable category headers)
        for row in range(self._sidebar.count()):
            item = self._sidebar.item(row)
            if item and item.data(Qt.UserRole) == idx:
                self._sidebar.blockSignals(True)
                self._sidebar.setCurrentRow(row)
                self._sidebar.blockSignals(False)
                break
        self._show_step(idx)

    def _show_step(self, sidebar_row_or_step_idx: int):
        """Called when the sidebar row changes or goto_step is called."""
        # If called from sidebar currentRowChanged, map row → step idx
        item = self._sidebar.item(sidebar_row_or_step_idx)
        if item is None:
            return
        idx = item.data(Qt.UserRole)
        if idx is None:
            # Category header — skip
            return
        self._step = idx
        step = _STEPS[idx]

        # Header
        self._header_icon.setText(step.get("icon", "•"))
        self._header_title.setText(step["title"])
        self._progress.setValue(idx)
        self._step_label.setText(f"{idx + 1} / {len(_STEPS)}")

        # Remove old diagram if any
        if self._diagram_widget is not None:
            self._inner_lay.removeWidget(self._diagram_widget)
            self._diagram_widget.deleteLater()
            self._diagram_widget = None

        # Diagram (if any) — insert before body
        diagram_key = step.get("diagram")
        if diagram_key and diagram_key in _DIAGRAM_PAINTERS:
            self._diagram_widget = DiagramWidget(_DIAGRAM_PAINTERS[diagram_key])
            self._inner_lay.insertWidget(0, self._diagram_widget)

        # Body
        body_html = step.get("body", "")
        # Wrap in dark-themed base CSS
        full_html = f"""
<html><head><style>
  body  {{ font-family: 'Segoe UI', Arial, sans-serif; font-size:10pt;
           color:#d4d4d4; background:#18182a; line-height:1.6; }}
  h2    {{ color:#4ec9b0; margin-top:0; }}
  h3    {{ color:#9cdcfe; margin-top:12px; }}
  code  {{ background:#252540; color:#ce9178; padding:1px 4px;
           border-radius:3px; font-family:Consolas,monospace; }}
  pre   {{ background:#1a1a2e; color:#ce9178; padding:10px;
           border-radius:6px; font-family:Consolas,monospace; font-size:9pt; }}
  kbd   {{ background:#2a2a42; color:#ddd; padding:1px 5px;
           border:1px solid #5a5a7a; border-radius:3px;
           font-family:Consolas,monospace; font-size:9pt; }}
  table {{ border-collapse:collapse; width:100%; }}
  th    {{ background:#252540; padding:4px 10px; text-align:left;
           border-bottom:1px solid #3a3a5a; }}
  td    {{ padding:3px 10px; border-bottom:1px solid #2a2a3a; }}
  ul,ol {{ margin-left:18px; }}
  li    {{ margin-bottom:4px; }}
</style></head><body>{body_html}</body></html>"""
        self._body_browser.setHtml(full_html)

        # Tip
        tip = step.get("tip", "")
        if tip:
            self._tip_label.setText(tip)
            self._tip_frame.show()
        else:
            self._tip_frame.hide()

        # Button states
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < len(_STEPS) - 1)
        if idx == len(_STEPS) - 1:
            self._btn_next.setText("✓ Done")
        else:
            self._btn_next.setText("Next →")

    # ── Closing ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def should_show_at_startup(self) -> bool:
        """Returns False if user checked 'Don't show at startup'."""
        return not self._no_show.isChecked()


# ─────────────────────────────────────────────────────────────────────────────
#  Module-level singleton helper
# ─────────────────────────────────────────────────────────────────────────────

_tutorial_instance: Optional["TutorialDialog"] = None


def show_tutorial(parent=None, step: int = 0) -> Optional["TutorialDialog"]:
    """
    Show (or raise) the tutorial dialog.  Uses a module-level singleton so
    only one window is ever open.  Safe to call from anywhere.
    """
    global _tutorial_instance
    if not _HAS_QT:
        return None
    try:
        if _tutorial_instance is None or not _tutorial_instance.isVisible():
            _tutorial_instance = TutorialDialog(parent=parent, start_step=step)
        _tutorial_instance.goto_step(step)
        _tutorial_instance.show()
        _tutorial_instance.raise_()
        _tutorial_instance.activateWindow()
        return _tutorial_instance
    except Exception as e:
        log.debug(f"show_tutorial error: {e}")
        return None
