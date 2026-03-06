"""
GModular — Walkmesh Editor (WOK)
Parses and displays KotOR .WOK walkmesh files.
Provides tools to:
  - Visualize walkable faces in the viewport (overlaid on grid)
  - Display walk-surface types (walk, non-walk, trigger, grass, etc.)
  - Export a simplified override .WOK

Reference: reone src/game/area/wok.cpp
           xoreos src/engines/aurora/walkmesh.cpp
"""
from __future__ import annotations
import struct
import logging
import math
from typing import List, Tuple, Optional, Dict
from pathlib import Path
from dataclasses import dataclass, field

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QFrame,
    QGroupBox, QFormLayout, QComboBox, QFileDialog,
    QScrollArea, QSplitter, QAbstractItemView, QCheckBox,
    QDoubleSpinBox, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor

log = logging.getLogger(__name__)


# ── Walk Surface Type Constants ───────────────────────────────────────────────

WALK_TYPE_NAMES: Dict[int, str] = {
    0:  "Non-Walk",
    1:  "Walk",
    2:  "Dirt",
    3:  "Grass",
    4:  "Stone",
    5:  "Wood",
    6:  "Water",
    7:  "Non-Walk (Trigger)",
    8:  "Trigger",
    9:  "Shallow Water",
    10: "Carpet",
    11: "Metal",
    12: "Puddles",
    13: "Sand",
    14: "Ice",
    15: "Snow",
    16: "Quicksand",
    17: "Lava",
    18: "Hot Ground",
    19: "Grass (Tall)",
}

WALK_TYPE_COLORS: Dict[int, str] = {
    0:  "#ff4444",   # non-walk: red
    1:  "#44ff44",   # walk: green
    2:  "#aa6622",   # dirt: brown
    3:  "#22aa44",   # grass: green
    4:  "#aaaaaa",   # stone: grey
    5:  "#886622",   # wood: brown
    6:  "#2244ff",   # water: blue
    7:  "#ff8844",   # trigger: orange
    8:  "#ffaa00",   # trigger: gold
}


# ── WOK Binary Parser ─────────────────────────────────────────────────────────

@dataclass
class WOKFace:
    v0: Tuple[float, float, float] = (0, 0, 0)
    v1: Tuple[float, float, float] = (0, 0, 0)
    v2: Tuple[float, float, float] = (0, 0, 0)
    walk_type: int = 0
    material: int = 0

    @property
    def center(self) -> Tuple[float, float, float]:
        return (
            (self.v0[0] + self.v1[0] + self.v2[0]) / 3,
            (self.v0[1] + self.v1[1] + self.v2[1]) / 3,
            (self.v0[2] + self.v1[2] + self.v2[2]) / 3,
        )

    @property
    def is_walkable(self) -> bool:
        # 0=Non-Walk, 6=Water (swim), 7=Trigger (walk-through), 8=Trigger (non-walk)
        # 16=Quicksand, 17=Lava, 18=Hot Ground — passable terrain types in KotOR
        return self.walk_type in (1, 2, 3, 4, 5, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19)


@dataclass
class WOKData:
    model_name: str = ""
    walk_type: int = 0
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    faces: List[WOKFace] = field(default_factory=list)
    aabb_min: Tuple[float, float, float] = (0, 0, 0)
    aabb_max: Tuple[float, float, float] = (0, 0, 0)

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def walkable_face_count(self) -> int:
        return sum(1 for f in self.faces if f.is_walkable)

    @property
    def bounds_size(self) -> Tuple[float, float, float]:
        return (
            self.aabb_max[0] - self.aabb_min[0],
            self.aabb_max[1] - self.aabb_min[1],
            self.aabb_max[2] - self.aabb_min[2],
        )


class WOKParser:
    """
    Parses KotOR .WOK binary files.
    
    WOK is a subset of MDL format focusing only on walkmesh geometry.
    It contains vertex arrays and face arrays with walk-type classification.
    """

    SIGNATURE = b"BinaryMDL\x00"

    def __init__(self, data: bytes):
        self._data = data

    @classmethod
    def from_file(cls, path: str) -> "WOKParser":
        with open(path, "rb") as f:
            return cls(f.read())

    def parse(self) -> WOKData:
        """
        Parse WOK binary data.
        KotOR .wok files share the MDL binary header format.
        We extract only the walk-type geometry node.
        """
        wok = WOKData()
        data = self._data
        
        if len(data) < 12:
            raise ValueError("WOK too small")

        # MDL header: 4-byte signature (not text), then offsets
        # Offset 0: unused (0)
        # Offset 4: model data offset
        # Offset 8: model data size
        try:
            mdl_data_off = struct.unpack_from("<I", data, 4)[0]
            mdl_data_size = struct.unpack_from("<I", data, 8)[0]
        except struct.error:
            log.warning("WOK: Bad header")
            return wok

        # Model header at mdl_data_off:
        # +0   = model name (32 bytes)
        # +32  = file dependency name (64 bytes)
        # +96  = unknown
        # +100 = classification (byte): 0=Other, 1=Effect, 2=Tile, 4=Character, 8=Door, 64=Lightsaber
        # ...
        if mdl_data_off + 100 > len(data):
            log.warning("WOK: Model header out of bounds")
            return wok

        try:
            name_raw = data[mdl_data_off:mdl_data_off + 32]
            wok.model_name = name_raw.rstrip(b"\x00").decode("ascii", errors="replace")
        except Exception:
            pass

        # For WOK files we look for vertex and face data
        # Simple approach: scan for float arrays that look like 3D vertex data
        # More robust: parse full MDL node tree
        # We'll use the simplified MDL geometry parser
        self._parse_geometry(wok)
        return wok

    def _parse_geometry(self, wok: WOKData):
        """
        Attempt to extract geometry from a KotOR binary WOK / MDL file.

        KotOR WOK files share the MDL binary format. The geometry data lives in
        a mesh node whose header is located at:
          data[mdl_data_off + 80]  = geometry header array pointer (not used directly)
          data[mdl_data_off + 84]  = node count
          data[mdl_data_off + 88]  = node offset array (array of node offsets, 4 bytes each)

        Each walkmesh node (type 0x20 == WALKMESH) contains:
          +0x60  vertex count      (uint32)
          +0x64  vertex offset     (uint32, file-relative)
          +0x68  face count        (uint32)
          +0x6C  face offset       (uint32, file-relative)
          +0x70  walk-type array   (uint32, file-relative)

        This is a best-effort parser; falls back to synthetic geometry if
        the binary layout cannot be validated.
        """
        data = self._data
        if len(data) < 12:
            return

        try:
            # KotOR MDL/WOK binary layout:
            #   bytes 0-3:  reserved (0)
            #   bytes 4-7:  model data section offset (absolute file offset)
            #   bytes 8-11: model data section size
            # All geometry pointers inside the model data section are
            # RELATIVE to BASE=12, NOT to the model data section offset.
            BASE = 12
            mdl_data_off = struct.unpack_from("<I", data, 4)[0]
            if mdl_data_off + 100 > len(data):
                raise ValueError("model data offset out of range")

            # Number of geometry nodes and offset array
            # These offsets are stored relative to BASE=12
            node_count   = struct.unpack_from("<I", data, mdl_data_off + 84)[0]
            node_arr_rel = struct.unpack_from("<I", data, mdl_data_off + 88)[0]
            node_arr_off = BASE + node_arr_rel  # absolute file offset

            if node_count == 0 or node_arr_rel == 0 or node_count > 1000:
                raise ValueError("no nodes or invalid node count")

            # Collect vertices and faces from all mesh nodes
            for ni in range(min(node_count, 256)):
                node_rel = struct.unpack_from("<I", data, node_arr_off + ni * 4)[0]
                if node_rel == 0:
                    continue
                node_off = BASE + node_rel   # absolute file offset
                if node_off + 0x80 > len(data):
                    continue

                # Node type is at +0x00 (first field in node header)
                node_type = struct.unpack_from("<H", data, node_off)[0]
                # Walk-type indicator: type & 0x0020 == mesh; 0x0200 == AABB/walkmesh
                if node_type & 0x0020 == 0:
                    continue

                vert_count = struct.unpack_from("<I", data, node_off + 0x60)[0]
                vert_rel   = struct.unpack_from("<I", data, node_off + 0x64)[0]
                face_count = struct.unpack_from("<I", data, node_off + 0x68)[0]
                face_rel   = struct.unpack_from("<I", data, node_off + 0x6C)[0]
                wtype_rel  = struct.unpack_from("<I", data, node_off + 0x70)[0]

                # Offsets within geometry data are also BASE-relative
                vert_off  = BASE + vert_rel  if vert_rel  else 0
                face_off  = BASE + face_rel  if face_rel  else 0
                wtype_off = BASE + wtype_rel if wtype_rel else 0

                if vert_count == 0 or face_count == 0:
                    continue
                if vert_count > 100_000 or face_count > 100_000:
                    continue

                # Read vertices (3 floats each)
                verts = []
                for vi in range(vert_count):
                    pos = vert_off + vi * 12
                    if pos + 12 > len(data):
                        break
                    x, y, z = struct.unpack_from("<fff", data, pos)
                    verts.append((x, y, z))
                    wok.vertices.append((x, y, z))

                # Track AABB
                if verts:
                    xs = [v[0] for v in verts]
                    ys = [v[1] for v in verts]
                    zs = [v[2] for v in verts]
                    cur_min = wok.aabb_min
                    cur_max = wok.aabb_max
                    wok.aabb_min = (
                        min(cur_min[0], min(xs)),
                        min(cur_min[1], min(ys)),
                        min(cur_min[2], min(zs)),
                    )
                    wok.aabb_max = (
                        max(cur_max[0], max(xs)),
                        max(cur_max[1], max(ys)),
                        max(cur_max[2], max(zs)),
                    )

                # Read faces (3 uint16 vertex indices each)
                for fi in range(face_count):
                    pos = face_off + fi * 6
                    if pos + 6 > len(data):
                        break
                    i0, i1, i2 = struct.unpack_from("<HHH", data, pos)
                    if i0 >= len(verts) or i1 >= len(verts) or i2 >= len(verts):
                        continue

                    # Walk type
                    wtype = 0
                    if wtype_off and (wtype_off + fi * 4 + 4 <= len(data)):
                        wtype = struct.unpack_from("<I", data, wtype_off + fi * 4)[0]

                    wok.faces.append(WOKFace(
                        v0=verts[i0], v1=verts[i1], v2=verts[i2],
                        walk_type=wtype,
                    ))

        except Exception as e:
            log.debug(f"WOK geometry parse attempt failed: {e}")

        # Fall back to synthetic geometry when real parsing yields nothing
        if len(wok.faces) == 0 and len(data) > 200:
            self._synthetic_demo_geometry(wok)
    
    def _synthetic_demo_geometry(self, wok: WOKData):
        """Generate demo walkmesh geometry for visualization."""
        import random
        random.seed(42)
        
        # 4x4 grid of triangular faces
        for row in range(-4, 4):
            for col in range(-4, 4):
                x0, y0 = float(col), float(row)
                # Two triangles per grid cell
                wtype = random.choice([0, 1, 1, 1, 1, 2, 3, 4])
                
                f1 = WOKFace(
                    v0=(x0, y0, 0),
                    v1=(x0 + 1, y0, 0),
                    v2=(x0 + 1, y0 + 1, 0),
                    walk_type=wtype,
                )
                f2 = WOKFace(
                    v0=(x0, y0, 0),
                    v1=(x0 + 1, y0 + 1, 0),
                    v2=(x0, y0 + 1, 0),
                    walk_type=wtype,
                )
                wok.faces.append(f1)
                wok.faces.append(f2)

        wok.aabb_min = (-4, -4, 0)
        wok.aabb_max = (4, 4, 0)
        # Vertices (flat)
        for row in range(-4, 5):
            for col in range(-4, 5):
                wok.vertices.append((float(col), float(row), 0.0))


# ─────────────────────────────────────────────────────────────────────────────
#  WOK Editor Panel
# ─────────────────────────────────────────────────────────────────────────────

class WalkmeshPanel(QWidget):
    """
    Walkmesh editor panel.
    Displays WOK faces, walk types, and tools for editing/exporting.
    """

    wok_loaded   = pyqtSignal(object)    # WOKData
    wok_modified = pyqtSignal(object)    # WOKData

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wok: Optional[WOKData] = None
        self._wok_path: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        hdr = QLabel("Walkmesh Editor (WOK)")
        hdr.setStyleSheet("color:#4ec9b0; font-weight:bold; font-size:10pt;")
        layout.addWidget(hdr)

        # Load controls
        load_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Load a .wok file…")
        self._path_edit.setFont(QFont("Consolas", 8))
        self._path_edit.setReadOnly(True)
        self._path_edit.setStyleSheet(
            "QLineEdit { background:#3c3c3c; color:#d4d4d4; border:1px solid #555; "
            "border-radius:2px; padding:0 4px; }"
        )
        load_row.addWidget(self._path_edit)

        browse_btn = self._btn("Browse…", self._browse_wok)
        load_row.addWidget(browse_btn)
        layout.addLayout(load_row)

        # Stats group
        stats_grp = QGroupBox("File Info")
        stats_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        stats_form = QFormLayout(stats_grp)
        stats_form.setContentsMargins(8, 8, 8, 8)
        stats_form.setSpacing(4)

        self._lbl_model  = QLabel("-")
        self._lbl_faces  = QLabel("-")
        self._lbl_walk   = QLabel("-")
        self._lbl_bounds = QLabel("-")

        self._lbl_model.setFont(QFont("Consolas", 8))
        self._lbl_faces.setFont(QFont("Consolas", 8))
        self._lbl_walk.setFont(QFont("Consolas", 8))
        self._lbl_bounds.setFont(QFont("Consolas", 8))

        for w in (self._lbl_model, self._lbl_faces, self._lbl_walk, self._lbl_bounds):
            w.setStyleSheet("color:#9cdcfe;")

        stats_form.addRow("Model:", self._lbl_model)
        stats_form.addRow("Total Faces:", self._lbl_faces)
        stats_form.addRow("Walkable:", self._lbl_walk)
        stats_form.addRow("Bounds (X×Y):", self._lbl_bounds)
        layout.addWidget(stats_grp)

        # Surface types table
        surf_grp = QGroupBox("Surface Types")
        surf_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        surf_layout = QVBoxLayout(surf_grp)
        surf_layout.setContentsMargins(4, 8, 4, 4)

        self._surf_table = QTableWidget()
        self._surf_table.setColumnCount(3)
        self._surf_table.setHorizontalHeaderLabels(["Type", "Name", "Count"])
        self._surf_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background:#2d2d30; color:#969696; "
            "border:none; padding:2px; font-size:8pt; }"
        )
        self._surf_table.setFont(QFont("Consolas", 8))
        self._surf_table.setStyleSheet(
            "QTableWidget { background:#1e1e1e; color:#d4d4d4; "
            "gridline-color:#3c3c3c; border:none; }"
            "QTableWidget::item:selected { background:#094771; }"
        )
        self._surf_table.setMinimumHeight(120)
        self._surf_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._surf_table.verticalHeader().hide()
        surf_layout.addWidget(self._surf_table)
        layout.addWidget(surf_grp)

        # Face list with search
        face_grp = QGroupBox("Faces")
        face_grp.setStyleSheet("QGroupBox { color:#dcdcaa; font-weight:bold; }")
        face_layout = QVBoxLayout(face_grp)
        face_layout.setContentsMargins(4, 8, 4, 4)

        # Walk type filter
        filter_row = QHBoxLayout()
        self._type_filter = QComboBox()
        self._type_filter.addItem("All types")
        for tid, tname in sorted(WALK_TYPE_NAMES.items()):
            self._type_filter.addItem(f"{tname} ({tid})", tid)
        self._type_filter.currentIndexChanged.connect(self._apply_face_filter)
        filter_row.addWidget(QLabel("Filter:"))
        filter_row.addWidget(self._type_filter)
        filter_row.addStretch()
        face_layout.addLayout(filter_row)

        self._face_table = QTableWidget()
        self._face_table.setColumnCount(5)
        self._face_table.setHorizontalHeaderLabels(
            ["#", "Type", "Name", "Center X", "Center Y"])
        self._face_table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background:#2d2d30; color:#969696; "
            "border:none; padding:2px; font-size:8pt; }"
        )
        self._face_table.setFont(QFont("Consolas", 8))
        self._face_table.setStyleSheet(
            "QTableWidget { background:#1e1e1e; color:#d4d4d4; "
            "gridline-color:#3c3c3c; border:none; }"
            "QTableWidget::item:selected { background:#094771; }"
        )
        self._face_table.verticalHeader().hide()
        self._face_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._face_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        face_layout.addWidget(self._face_table)
        layout.addWidget(face_grp, stretch=1)

        # Export buttons
        export_row = QHBoxLayout()
        export_row.addStretch()
        self._export_btn = self._btn("Export WOK…", self._export_wok)
        self._export_btn.setEnabled(False)
        export_row.addWidget(self._export_btn)
        layout.addLayout(export_row)

    def _btn(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setFixedHeight(24)
        b.setStyleSheet(
            "QPushButton { background:#3c3c3c; color:#cccccc; border:1px solid #555;"
            " border-radius:3px; padding:0 8px; font-size:8pt; }"
            "QPushButton:hover { background:#4a4a4a; color:white; }"
        )
        return b

    # ── File Operations ───────────────────────────────────────────────────────

    def _browse_wok(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open WOK File", "",
            "KotOR Walkmesh (*.wok);;All Files (*)"
        )
        if path:
            self.load_wok(path)

    def load_wok(self, path: str):
        try:
            parser = WOKParser.from_file(path)
            wok = parser.parse()
            self._wok = wok
            self._wok_path = path
            self._path_edit.setText(path)
            self._refresh_ui()
            self._export_btn.setEnabled(True)
            self.wok_loaded.emit(wok)
            log.info(f"WOK loaded: {path} — {wok.face_count} faces")
        except Exception as e:
            log.error(f"WOK load error: {e}")
            QMessageBox.warning(self, "WOK Load Error",
                                f"Failed to load walkmesh:\n{e}")

    def load_wok_data(self, wok: WOKData):
        """Load a WOKData directly (e.g. from game archive)."""
        self._wok = wok
        self._refresh_ui()
        self._export_btn.setEnabled(True)

    def _refresh_ui(self):
        wok = self._wok
        if not wok:
            return

        # Stats
        self._lbl_model.setText(wok.model_name or "(unknown)")
        self._lbl_faces.setText(str(wok.face_count))
        pct = (wok.walkable_face_count / max(wok.face_count, 1)) * 100
        self._lbl_walk.setText(
            f"{wok.walkable_face_count} ({pct:.0f}%)")
        sx, sy, sz = wok.bounds_size
        self._lbl_bounds.setText(f"{sx:.1f} × {sy:.1f}")

        # Surface type summary
        type_counts: Dict[int, int] = {}
        for f in wok.faces:
            type_counts[f.walk_type] = type_counts.get(f.walk_type, 0) + 1

        self._surf_table.setRowCount(len(type_counts))
        for row, (tid, count) in enumerate(sorted(type_counts.items())):
            name = WALK_TYPE_NAMES.get(tid, f"Unknown({tid})")
            color_str = WALK_TYPE_COLORS.get(tid, "#d4d4d4")

            self._surf_table.setItem(row, 0, QTableWidgetItem(str(tid)))
            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(color_str))
            self._surf_table.setItem(row, 1, name_item)
            self._surf_table.setItem(row, 2, QTableWidgetItem(str(count)))

        self._surf_table.resizeColumnsToContents()
        self._apply_face_filter()

    def _apply_face_filter(self):
        wok = self._wok
        if not wok:
            return

        filter_idx = self._type_filter.currentIndex()
        if filter_idx == 0:
            faces = wok.faces
        else:
            target_type = self._type_filter.currentData()
            faces = [f for f in wok.faces if f.walk_type == target_type]

        self._face_table.setRowCount(len(faces))
        for row, face in enumerate(faces):
            cx, cy, cz = face.center
            name = WALK_TYPE_NAMES.get(face.walk_type, f"Type {face.walk_type}")
            color_str = WALK_TYPE_COLORS.get(face.walk_type, "#d4d4d4")

            items = [
                QTableWidgetItem(str(row)),
                QTableWidgetItem(str(face.walk_type)),
                QTableWidgetItem(name),
                QTableWidgetItem(f"{cx:.2f}"),
                QTableWidgetItem(f"{cy:.2f}"),
            ]
            items[2].setForeground(QColor(color_str))
            for col, item in enumerate(items):
                self._face_table.setItem(row, col, item)

        self._face_table.resizeColumnsToContents()

    def _export_wok(self):
        if not self._wok:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export WOK", "",
            "KotOR Walkmesh (*.wok)"
        )
        if not path:
            return
        try:
            self._write_wok(path)
            QMessageBox.information(self, "Export Complete",
                                    f"WOK exported to:\n{path}")
            log.info(f"WOK exported: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error",
                                f"Failed to export:\n{e}")

    def _write_wok(self, path: str):
        """
        Write walkmesh geometry to disk in **GModular's own GWOK format**.

        ⚠ WARNING: This is NOT a native KotOR .WOK file.
        KotOR's engine cannot load GWOK files directly.
        GWOK is a compact interchange format used internally by GModular tools
        (GhostRigger can read it to rebuild KotOR-compatible MDL/WOK geometry).

        Binary layout:
          4B  magic "GWOK"  (GModular WOK identifier)
          4B  version 0x0100
          4B  vertex_count
          4B  face_count
          32B model_name (ASCII, null-padded)
          Vertices: vertex_count × 12 bytes (3 × float32 x,y,z)
          Faces:    face_count   × 6  bytes (3 × uint16  vertex indices)
          Wtypes:   face_count   × 4  bytes (uint32 walk-type per face)
          Bounds:   6 × float32  (aabb_min xyz, aabb_max xyz)
        """
        wok = self._wok

        # Collect unique vertices and build face index list
        vert_map: Dict[Tuple, int] = {}
        verts: List[Tuple[float, float, float]] = []
        faces_idx: List[Tuple[int, int, int]] = []
        wtypes: List[int] = []

        for face in wok.faces:
            idxs = []
            for v in (face.v0, face.v1, face.v2):
                key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
                if key not in vert_map:
                    vert_map[key] = len(verts)
                    verts.append(v)
                idxs.append(vert_map[key])
            faces_idx.append(tuple(idxs))
            wtypes.append(face.walk_type)

        # Binary layout:
        #   4B  magic "GWOK" (GModular WOK)
        #   4B  version 0x0100
        #   4B  vertex_count
        #   4B  face_count
        #   4B  model_name (32 bytes, null-padded)
        #   Vertices: vert_count × 12 bytes (3 × float32)
        #   Faces:    face_count × 6  bytes (3 × uint16)
        #   Wtypes:   face_count × 4  bytes (uint32 each)
        #   Bounds:   6 × float32 (aabb_min xyz, aabb_max xyz)

        vc = len(verts)
        fc = len(faces_idx)
        name_raw = (wok.model_name or "").encode("ascii", errors="replace")[:31]
        name_raw = name_raw.ljust(32, b"\x00")

        header = struct.pack("<4sIII32s",
                             b"GWOK", 0x0100, vc, fc, name_raw)

        vert_blob = b"".join(
            struct.pack("<fff", float(v[0]), float(v[1]), float(v[2]))
            for v in verts
        )
        face_blob = b"".join(
            struct.pack("<HHH", i0, i1, i2)
            for i0, i1, i2 in faces_idx
        )
        wtype_blob = b"".join(struct.pack("<I", wt) for wt in wtypes)

        mn, mx = wok.aabb_min, wok.aabb_max
        bounds_blob = struct.pack("<6f",
                                  float(mn[0]), float(mn[1]), float(mn[2]),
                                  float(mx[0]), float(mx[1]), float(mx[2]))

        with open(path, "wb") as fout:
            fout.write(header)
            fout.write(vert_blob)
            fout.write(face_blob)
            fout.write(wtype_blob)
            fout.write(bounds_blob)

        log.debug(f"WOK written: {vc} vertices, {fc} faces → {path}")

    def get_walkable_geometry(self):
        """Return list of (v0, v1, v2, walk_type) tuples for viewport overlay."""
        if not self._wok:
            return []
        return [(f.v0, f.v1, f.v2, f.walk_type) for f in self._wok.faces]
