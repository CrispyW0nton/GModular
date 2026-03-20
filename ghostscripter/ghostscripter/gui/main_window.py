"""
GhostScripter — Main Window
============================
NWScript IDE main window — syntax-highlighted editor, compile output panel,
function browser sidebar, blueprint IPC status, and script registry.

Design matches the Ghostworks dark theme contract (PIPELINE_SPEC.md §6).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from qtpy.QtWidgets import (
        QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QTextEdit, QStatusBar,
        QTreeWidget, QTreeWidgetItem, QSplitter,
        QPlainTextEdit, QFrame, QLineEdit,
    )
    from qtpy.QtCore import Qt, QTimer
    from qtpy.QtGui import QFont
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QMainWindow = object          # type: ignore

# Re-export highlighter classes from nwscript_tokens for backward compat
try:
    from ghostscripter.gui.nwscript_tokens import (
        NWScriptHighlighter, FunctionBrowserPanel,
        NWScriptTokenizer, NWSCRIPT_STDLIB,
    )
except ImportError:
    NWScriptHighlighter = None      # type: ignore
    FunctionBrowserPanel = None     # type: ignore
    NWScriptTokenizer = None        # type: ignore
    NWSCRIPT_STDLIB = []            # type: ignore

class MainWindow(QMainWindow if _HAS_QT else object):
    """GhostScripter main window — NWScript IDE with IPC integration."""

    TITLE = "GhostScripter — KotOR Script + Logic IDE  v1.0"
    STYLE = """
        QMainWindow, QWidget { background: #1e1e1e; color: #d4d4d4; }
        QLabel { color: #d4d4d4; }
        QPushButton {
            background: #2d2d2d; color: #d4d4d4;
            border: 1px solid #3e3e42; border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover { background: #264f78; }
        QPushButton:pressed { background: #1f3f5c; }
        QTreeWidget {
            background: #252526; color: #d4d4d4;
            border: 1px solid #3e3e42;
        }
        QTreeWidget::item:selected { background: #264f78; }
        QPlainTextEdit, QTextEdit {
            background: #1e1e1e; color: #d4d4d4;
            border: 1px solid #3e3e42;
            font-family: Consolas, "Courier New", monospace;
            font-size: 10pt;
        }
        QTabWidget::pane { border: 1px solid #3e3e42; }
        QTabBar::tab {
            background: #252526; color: #9d9d9d;
            padding: 4px 12px; border: 1px solid #3e3e42;
        }
        QTabBar::tab:selected { color: #4fc3f7; border-bottom: 2px solid #4fc3f7; }
        QStatusBar { background: #252526; color: #9d9d9d; }
        QLineEdit {
            background: #2d2d2d; color: #d4d4d4;
            border: 1px solid #3e3e42; border-radius: 4px; padding: 2px 6px;
        }
        QFrame[frameShape="4"] { color: #3e3e42; }
    """

    def __init__(self):
        if _HAS_QT:
            super().__init__()
            self.setWindowTitle(self.TITLE)
            self.setStyleSheet(self.STYLE)
            self.resize(1280, 800)
            self._highlighter = None
            self._build_ui()
            self._status_timer = QTimer(self)
            self._status_timer.timeout.connect(self._poll_ipc_status)
            self._status_timer.start(5000)
        else:
            log.warning("Qt not available — MainWindow is a no-op stub")

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        if not _HAS_QT:
            return
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_body(), 1)
        self.setStatusBar(self._build_statusbar())

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background:#252526; border-bottom:1px solid #3e3e42;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 2, 8, 2)

        for label, tip, slot in [
            ("New Script",  "Create a new NWScript",            self._on_new_script),
            ("Open...",     "Open an existing .nss file",        self._on_open_script),
            ("Save",        "Save current script",               self._on_save_script),
            ("Compile",     "Compile current script to .ncs",    self._on_compile),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedWidth(90)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedHeight(20)
        lay.addWidget(sep)

        for label, tip, slot in [
            ("▶ Run Check", "Validate script logic",   self._on_validate),
            ("Clear",       "Clear diagnostics panel", self._on_clear_diag),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedWidth(90)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        lay.addStretch()

        self._ipc_lbl = QLabel("IPC: port 7002")
        self._ipc_lbl.setStyleSheet("color:#4ec9b0; font-size:11px;")
        lay.addWidget(self._ipc_lbl)

        return bar

    def _build_body(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)

        # ── Left panel: script list ───────────────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(200)
        left.setMaximumWidth(260)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(4, 4, 4, 4)

        lbl = QLabel("Scripts")
        lbl.setStyleSheet("color:#4fc3f7; font-weight:bold; padding:2px 0;")
        left_lay.addWidget(lbl)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter scripts…")
        left_lay.addWidget(self._search)

        self._script_tree = QTreeWidget()
        self._script_tree.setHeaderHidden(True)
        self._script_tree.itemClicked.connect(self._on_script_selected)
        left_lay.addWidget(self._script_tree, 1)

        self._update_script_tree()
        splitter.addWidget(left)

        # ── Centre: editor + diagnostics ─────────────────────────────────────
        centre = QSplitter(Qt.Vertical)

        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText(
            "// NWScript source editor\n"
            "// Open or create a script to begin editing.\n"
        )
        self._editor.textChanged.connect(self._on_text_changed)
        self._highlighter = NWScriptHighlighter(self._editor.document())
        centre.addWidget(self._editor)

        self._diag = QTextEdit()
        self._diag.setReadOnly(True)
        self._diag.setMaximumHeight(180)
        self._diag.setPlaceholderText("Compile diagnostics will appear here…")
        centre.addWidget(self._diag)
        centre.setSizes([580, 180])

        splitter.addWidget(centre)

        # ── Right panel: function browser ─────────────────────────────────────
        if _HAS_QT and FunctionBrowserPanel is not None:
            self._func_browser = FunctionBrowserPanel()
            self._func_browser.setMinimumWidth(220)
            self._func_browser.setMaximumWidth(300)
            self._func_browser.function_selected.connect(self._on_function_selected)
            splitter.addWidget(self._func_browser)
        else:
            right = QWidget()
            right.setMinimumWidth(220)
            splitter.addWidget(right)

        splitter.setSizes([220, 820, 260])

        return splitter

    def _on_function_selected(self, name: str, sig: str) -> None:
        """Insert function call skeleton at cursor position."""
        if not _HAS_QT:
            return
        cursor = self._editor.textCursor()
        cursor.insertText(name + "(")
        self._editor.setTextCursor(cursor)
        self._status_lbl.showMessage(f"Inserted: {name}()")

    def _build_statusbar(self) -> QStatusBar:
        sb = QStatusBar()
        sb.showMessage("GhostScripter ready — IPC server on port 7002")
        self._status_lbl = sb
        return sb

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_new_script(self):
        try:
            from ghostscripter.core.script_state import get_registry, Script
            resref = f"new_script_{len(get_registry()):03d}"
            s = Script(resref=resref, source="void main() {\n    // TODO\n}\n")
            get_registry().add(s)
            self._editor.setPlainText(s.source)
            self._update_script_tree()
            self._status_lbl.showMessage(f"New script: {resref}")
        except Exception as exc:
            log.debug("_on_new_script error: %s", exc)

    def _on_open_script(self):
        """Stub — will open file dialog in Phase 2."""
        self._status_lbl.showMessage("Open .nss file — not yet implemented (Phase 2)")

    def _on_save_script(self):
        """Stub — will save to disk in Phase 2."""
        self._status_lbl.showMessage("Save script — not yet implemented (Phase 2)")

    def _on_compile(self):
        try:
            from ghostscripter.core.script_state import NWScriptCompiler
            source = self._editor.toPlainText()
            if not source.strip():
                self._diag.setPlainText("No source to compile.")
                return
            compiler = NWScriptCompiler()
            result = compiler.compile(source)
            lines = []
            if result["success"]:
                lines.append("✓ Compile OK")
            else:
                lines.append("✗ Compile FAILED")
            for e in result["errors"]:
                lines.append(f"  ERROR: {e}")
            for w in result["warnings"]:
                lines.append(f"  WARN:  {w}")
            self._diag.setPlainText("\n".join(lines))
            self._status_lbl.showMessage(
                "Compiled OK" if result["success"] else f"Compile failed ({len(result['errors'])} errors)"
            )
        except Exception as exc:
            log.debug("_on_compile error: %s", exc)
            self._diag.setPlainText(f"Compile error: {exc}")

    def _on_validate(self):
        self._on_compile()

    def _on_clear_diag(self):
        self._diag.clear()

    def _on_text_changed(self):
        pass  # Future: mark script dirty, live diagnostics

    def _on_script_selected(self, item, _col=0):
        try:
            from ghostscripter.core.script_state import get_registry
            resref = item.text(0)
            s = get_registry().get(resref)
            if s:
                self._editor.setPlainText(s.source)
                self._status_lbl.showMessage(f"Loaded: {resref}")
        except Exception as exc:
            log.debug("_on_script_selected error: %s", exc)

    def _update_script_tree(self):
        if not _HAS_QT:
            return
        try:
            from ghostscripter.core.script_state import get_registry
            self._script_tree.clear()
            for s in get_registry().list_all():
                QTreeWidgetItem(self._script_tree, [s.resref])
        except Exception as exc:
            log.debug("_update_script_tree error: %s", exc)

    def _poll_ipc_status(self):
        try:
            from ghostscripter.ipc.server import is_running
            running = is_running()
            color = "#4ec9b0" if running else "#f44747"
            status = "running" if running else "stopped"
            self._ipc_lbl.setText(f"IPC: port 7002 ({status})")
            self._ipc_lbl.setStyleSheet(f"color:{color}; font-size:11px;")
        except Exception:
            pass
