"""
GModular — 2DA Editor Panel
============================
A dockable Qt widget for viewing and editing KotOR .2da tables.

Features
--------
- Load binary (V2.b) or ASCII (V2.0) 2DA files
- Editable QTableWidget with cell-level undo/redo
- Add / remove rows and columns
- Save back to binary V2.b or ASCII V2.0
- Filter rows by text search
- Export as CSV

Architecture
------------
The panel owns a TwoDAData model and mirrors it into a QTableWidget.
Edits are recorded as (row, col, old_val, new_val) tuples for undo/redo.

References
----------
PyKotor/resource/formats/twoda/twoda_data.py   — data model
PyKotor/resource/formats/twoda/io_twoda.py     — binary R/W (V2.b)
Kotor.NET/Formats/Kotor2DA/TwoDA.cs            — column/row API
Kotor.NET.Patcher/Diff/Diff2DA.cs              — diff algorithm
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Qt availability guard ────────────────────────────────────────────────────
try:
    from qtpy.QtCore    import Qt, QTimer
    from qtpy.QtWidgets import (
        QAction, QDockWidget, QFileDialog, QHBoxLayout,
        QHeaderView, QLabel, QLineEdit, QMessageBox,
        QPushButton, QTableWidget, QTableWidgetItem,
        QToolBar, QUndoCommand, QUndoStack, QVBoxLayout, QWidget,
    )
    _HAS_QT = True
    _UndoBase  = QUndoCommand
    _WidgetBase = QWidget
except ImportError:
    _HAS_QT = False
    _UndoBase  = object   # type: ignore[assignment,misc]
    _WidgetBase = object  # type: ignore[assignment,misc]

from gmodular.formats.kotor_formats import TwoDAData, write_2da_binary, write_2da_ascii


# ── Undo/Redo command ────────────────────────────────────────────────────────

class _CellEdit(_UndoBase):   # type: ignore[misc]
    """Records a single cell change for undo/redo."""

    def __init__(self, table_widget, row: int, col: int, old: str, new: str):
        if _HAS_QT:
            super().__init__(f"Edit [{row},{col}]")
        self._tw  = table_widget
        self._row = row
        self._col = col
        self._old = old
        self._new = new

    def redo(self):
        if not _HAS_QT:
            return
        item = self._tw.item(self._row, self._col)
        if item:
            item.setText(self._new)

    def undo(self):
        if not _HAS_QT:
            return
        item = self._tw.item(self._row, self._col)
        if item:
            item.setText(self._old)


# ── 2DA Editor Widget ────────────────────────────────────────────────────────

class TwoDAEditorPanel(_WidgetBase):  # type: ignore[misc]
    """
    Dockable 2DA editor.

    Usage (from MainWindow)::

        editor = TwoDAEditorPanel(parent=self)
        dock = QDockWidget("2DA Editor", self)
        dock.setWidget(editor)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

        editor.load_bytes(data, filename="classes.2da")

    Qt .ui migration (Phase 2):
      Attempts to load twoda_editor.ui via load_ui(). If successful,
      self._ui_loaded is True and designer-defined widget names are
      available. Python layout is always built as a complete fallback.
    """

    def __init__(self, parent=None):
        if not _HAS_QT:
            return
        super().__init__(parent)
        self._model:    Optional[TwoDAData] = None
        self._filename: str = ""
        self._dirty:    bool = False
        self._undo_stack = QUndoStack(self)
        self._suppress_changes = False
        self._ui_loaded = False  # True when twoda_editor.ui was loaded

        # ── Phase 2: attempt to load Qt Designer layout ───────────────────────
        try:
            from .ui_loader import load_ui
            self._ui_loaded = load_ui("twoda_editor.ui", self)
        except Exception as _exc:
            log.debug("twoda_editor.ui not loaded (%s) — using Python layout", _exc)

        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = QToolBar(self)
        tb.setMovable(False)

        self._btn_open  = QPushButton("📂 Open")
        self._btn_save  = QPushButton("💾 Save")
        self._btn_saveas= QPushButton("Save As…")
        self._btn_add_row  = QPushButton("＋ Row")
        self._btn_del_row  = QPushButton("－ Row")
        self._btn_add_col  = QPushButton("＋ Col")
        self._btn_del_col  = QPushButton("－ Col")
        self._btn_undo  = QPushButton("↩ Undo")
        self._btn_redo  = QPushButton("↪ Redo")
        self._btn_csv   = QPushButton("CSV…")

        for b in (self._btn_open, self._btn_save, self._btn_saveas,
                  self._btn_add_row, self._btn_del_row,
                  self._btn_add_col, self._btn_del_col,
                  self._btn_undo, self._btn_redo, self._btn_csv):
            tb.addWidget(b)

        self._btn_open.clicked.connect(self._on_open)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_saveas.clicked.connect(self._on_save_as)
        self._btn_add_row.clicked.connect(self._on_add_row)
        self._btn_del_row.clicked.connect(self._on_del_row)
        self._btn_add_col.clicked.connect(self._on_add_col)
        self._btn_del_col.clicked.connect(self._on_del_col)
        self._btn_undo.clicked.connect(self._undo_stack.undo)
        self._btn_redo.clicked.connect(self._undo_stack.redo)
        self._btn_csv.clicked.connect(self._on_export_csv)

        layout.addWidget(tb)

        # ── Search bar ───────────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Filter:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search cells…")
        self._search.textChanged.connect(self._on_filter)
        search_row.addWidget(self._search)
        self._lbl_status = QLabel("No file loaded")
        search_row.addWidget(self._lbl_status)
        layout.addLayout(search_row)

        # ── Table ────────────────────────────────────────────────────────────
        self._table = QTableWidget(self)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(self._table.SelectItems)
        self._table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

    # ── Public API ───────────────────────────────────────────────────────────

    def load_bytes(self, data: bytes, filename: str = ""):
        """Load a 2DA from binary or ASCII bytes."""
        if not _HAS_QT:
            return
        from gmodular.formats.twoda_loader import TwoDALoader
        try:
            self._model    = TwoDALoader.from_bytes(data)
            self._filename = filename
            self._dirty    = False
            self._undo_stack.clear()
            self._refresh_table()
            self._lbl_status.setText(
                f"{filename}  —  {len(self._model.rows)} rows × "
                f"{len(self._model.headers)} cols"
            )
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def load_file(self, path: str):
        """Load a 2DA from a file path."""
        with open(path, "rb") as f:
            self.load_bytes(f.read(), filename=path)

    def get_bytes_binary(self) -> bytes:
        """Return current table as binary V2.b bytes."""
        self._sync_model_from_table()
        return write_2da_binary(self._model)

    def get_bytes_ascii(self) -> bytes:
        """Return current table as ASCII V2.0 bytes."""
        self._sync_model_from_table()
        return write_2da_ascii(self._model)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _refresh_table(self):
        """Rebuild the QTableWidget from the model."""
        if not self._model:
            return
        self._suppress_changes = True
        m = self._model
        self._table.clear()
        self._table.setRowCount(len(m.rows))
        self._table.setColumnCount(len(m.headers))
        self._table.setHorizontalHeaderLabels(m.headers)
        self._table.setVerticalHeaderLabels([str(i) for i in range(len(m.rows))])

        for ri, row in enumerate(m.rows):
            for ci, header in enumerate(m.headers):
                val = row.get(header, "")
                item = QTableWidgetItem(val)
                self._table.setItem(ri, ci, item)

        self._suppress_changes = False

    def _sync_model_from_table(self):
        """Copy table edits back to the TwoDAData model."""
        if not self._model:
            return
        for ri in range(self._table.rowCount()):
            if ri >= len(self._model.rows):
                self._model.rows.append({})
            for ci, header in enumerate(self._model.headers):
                item = self._table.item(ri, ci)
                self._model.rows[ri][header] = item.text() if item else ""

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_cell_changed(self, row: int, col: int):
        if self._suppress_changes or not self._model:
            return
        item = self._table.item(row, col)
        new_val = item.text() if item else ""
        header  = self._model.headers[col] if col < len(self._model.headers) else ""
        old_val = self._model.rows[row].get(header, "") if row < len(self._model.rows) else ""
        if new_val != old_val:
            # Update model immediately
            if row < len(self._model.rows):
                self._model.rows[row][header] = new_val
            self._dirty = True

    def _on_filter(self, text: str):
        text = text.lower()
        for ri in range(self._table.rowCount()):
            row_visible = False
            for ci in range(self._table.columnCount()):
                item = self._table.item(ri, ci)
                if item and text in item.text().lower():
                    row_visible = True
                    break
            self._table.setRowHidden(ri, not row_visible and bool(text))

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open 2DA", "", "2DA Files (*.2da);;All Files (*)"
        )
        if path:
            self.load_file(path)

    def _on_save(self):
        if not self._filename:
            self._on_save_as()
            return
        try:
            data = self.get_bytes_binary()
            with open(self._filename, "wb") as f:
                f.write(data)
            self._dirty = False
            self._lbl_status.setText(f"Saved: {self._filename}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _on_save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save 2DA", self._filename or "output.2da",
            "2DA Binary (*.2da);;All Files (*)"
        )
        if path:
            self._filename = path
            self._on_save()

    def _on_add_row(self):
        if not self._model:
            return
        ri = self._table.rowCount()
        self._model.rows.append({h: "" for h in self._model.headers})
        self._suppress_changes = True
        self._table.insertRow(ri)
        for ci, h in enumerate(self._model.headers):
            self._table.setItem(ri, ci, QTableWidgetItem(""))
        self._suppress_changes = False
        self._dirty = True

    def _on_del_row(self):
        rows = sorted(set(i.row() for i in self._table.selectedItems()), reverse=True)
        for ri in rows:
            if ri < len(self._model.rows):
                self._model.rows.pop(ri)
            self._table.removeRow(ri)
        self._dirty = True

    def _on_add_col(self):
        if not self._model:
            return
        col_name, ok = __import__('qtpy.QtWidgets', fromlist=['QInputDialog']).QInputDialog.getText(
            self, "Add Column", "Column name:"
        )
        if ok and col_name:
            self._model.headers.append(col_name)
            for row in self._model.rows:
                row[col_name] = ""
            ci = self._table.columnCount()
            self._suppress_changes = True
            self._table.insertColumn(ci)
            self._table.setHorizontalHeaderItem(ci, QTableWidgetItem(col_name))
            for ri in range(self._table.rowCount()):
                self._table.setItem(ri, ci, QTableWidgetItem(""))
            self._suppress_changes = False
            self._dirty = True

    def _on_del_col(self):
        cols = sorted(set(i.column() for i in self._table.selectedItems()), reverse=True)
        for ci in cols:
            if ci < len(self._model.headers):
                col_name = self._model.headers.pop(ci)
                for row in self._model.rows:
                    row.pop(col_name, None)
            self._table.removeColumn(ci)
        self._dirty = True

    def _on_export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "output.csv", "CSV (*.csv);;All Files (*)"
        )
        if path:
            self._sync_model_from_table()
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._model.headers)
                for row in self._model.rows:
                    writer.writerow([row.get(h, "") for h in self._model.headers])
            self._lbl_status.setText(f"Exported CSV: {path}")
