"""
GModular — Qt Designer .ui file loader utility.

Usage
-----
    from gmodular.gui.ui_loader import load_ui, load_ui_type

    # Option 1 – load into an existing widget instance:
    class MyWidget(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            load_ui("inspector.ui", self)
            # self.titleLabel, self.scrollArea etc. are now attributes

    # Option 2 – generate a base class (like uic.loadUiType):
    Form, Base = load_ui_type("twoda_editor.ui")
    class TwoDAEditor(Base, Form):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setupUi(self)

Design notes
------------
* Works with PyQt5, PyQt6, PySide2, and PySide6 via the qtpy shim.
* .ui files live in  gmodular/gui/ui/  next to this module.
* Falls back gracefully when a .ui file is missing (logs a warning and
  continues so the Python fallback layout still runs).
* In a packaged (PyInstaller) build the ui/ directory must be included
  in the --add-data list; GModular.spec already does this.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple, Type

log = logging.getLogger(__name__)

# Directory that contains all .ui files
_UI_DIR = Path(__file__).parent / "ui"


def _ui_path(filename: str) -> Path:
    """Return the absolute path to a .ui file by name."""
    return _UI_DIR / filename


def load_ui(filename: str, widget, ui_dir: Optional[Path] = None) -> bool:
    """
    Load a Qt Designer .ui file into *widget* in-place.

    Parameters
    ----------
    filename : str
        Name of the .ui file (e.g. ``"inspector.ui"``).
    widget   : QWidget
        The target widget; its children will be populated from the .ui.
    ui_dir   : Path, optional
        Override the default ui/ directory.

    Returns
    -------
    bool
        ``True`` if the .ui was loaded successfully, ``False`` otherwise.
        On failure the widget is left unchanged (Python fallback layout
        should handle construction instead).
    """
    path = (ui_dir or _UI_DIR) / filename
    if not path.exists():
        log.warning("ui_loader: .ui file not found: %s", path)
        return False

    try:
        from qtpy import uic  # type: ignore[attr-defined]
        uic.loadUi(str(path), widget)
        log.debug("ui_loader: loaded %s", filename)
        return True
    except Exception as exc:
        log.warning("ui_loader: failed to load %s — %s", filename, exc)
        return False


def load_ui_type(
    filename: str,
    ui_dir: Optional[Path] = None,
) -> Tuple[Type, Type]:
    """
    Generate a (Form, Base) class pair from a .ui file.

    Mirrors the ``PyQt5.uic.loadUiType`` / ``PyQt6.uic.loadUiType`` API.

    Parameters
    ----------
    filename : str
        Name of the .ui file.
    ui_dir   : Path, optional
        Override the default ui/ directory.

    Returns
    -------
    (FormClass, BaseClass)
        Use as ``class MyWidget(BaseClass, FormClass)`` then call
        ``self.setupUi(self)`` in ``__init__``.

    Raises
    ------
    FileNotFoundError
        If the .ui file does not exist.
    ImportError
        If qtpy / uic is not available.
    """
    path = (ui_dir or _UI_DIR) / filename
    if not path.exists():
        raise FileNotFoundError(f"UI file not found: {path}")

    from qtpy import uic  # type: ignore[attr-defined]
    return uic.loadUiType(str(path))


def ui_file(filename: str, ui_dir: Optional[Path] = None) -> str:
    """
    Return the absolute string path to a .ui file.

    Useful when you want to pass the path directly to a framework that
    accepts a filename rather than handling loading itself.
    """
    path = (ui_dir or _UI_DIR) / filename
    if not path.exists():
        log.warning("ui_loader.ui_file: %s not found (returning path anyway)", path)
    return str(path)


def list_ui_files(ui_dir: Optional[Path] = None) -> list:
    """Return a sorted list of all .ui filenames in the ui/ directory."""
    d = ui_dir or _UI_DIR
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.ui"))
