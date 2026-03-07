"""
GModular — 2DA Lookup Layer (P8)
Loads KotOR .2da files and provides human-readable lookups for Inspector dropdowns.

2DA format (plain text):
  Line 0: "2DA V2.0"
  Line 1: blank
  Line 2: column headers (space-separated)
  Line 3+: rows starting with row number

Example (appearance.2da):
    2DA V2.0

    LABEL  RACE  ...
    0   "Commoner"   ...
    1   "Soldier"    ...

Usage:
    loader = TwoDALoader()
    loader.load_file("appearance.2da", Path("/game/appearance.2da"))
    name = loader.get_name("appearance", 47)   # -> "Rodian"
    opts = loader.get_options("appearance")    # -> [(0,"Commoner"),(1,"Soldier"),...]
"""
from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class TwoDATable:
    """In-memory representation of a .2da file."""

    def __init__(self, name: str):
        self.name = name
        self.columns: List[str] = []
        self.rows: Dict[int, Dict[str, str]] = {}   # row_idx -> {col: value}

    def get(self, row: int, column: str, default: str = "") -> str:
        row_data = self.rows.get(row, {})
        return row_data.get(column, default)

    def get_label(self, row: int) -> str:
        """Return the LABEL column (first text column) for a row."""
        row_data = self.rows.get(row, {})
        # Try LABEL column first, then first column
        if "LABEL" in row_data:
            return row_data["LABEL"].strip('"')
        if self.columns:
            return row_data.get(self.columns[0], "").strip('"')
        return f"Row {row}"

    def options(self) -> List[Tuple[int, str]]:
        """Return [(row_index, display_name)] sorted by index."""
        result = []
        for idx in sorted(self.rows.keys()):
            label = self.get_label(idx)
            if label and label != "****":
                result.append((idx, f"{label}  (row {idx})"))
        return result

    def row_count(self) -> int:
        return len(self.rows)

    def __len__(self):
        return len(self.rows)


def _parse_2da(text: str, name: str) -> Optional[TwoDATable]:
    """Parse a 2DA V2.0 plain-text file."""
    lines = text.splitlines()
    if not lines:
        return None

    # Check header
    if not lines[0].strip().upper().startswith("2DA"):
        log.debug(f"2DA {name}: bad header: {lines[0]!r}")
        return None

    table = TwoDATable(name)

    # Find column header line (first non-blank line after line 0)
    col_line_idx = -1
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped:
            col_line_idx = i
            break

    if col_line_idx < 0:
        return table  # empty table

    # Parse column headers
    table.columns = lines[col_line_idx].split()

    # Parse rows
    for line in lines[col_line_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Tokenize: handle quoted strings with spaces
        tokens = _tokenize_2da_line(stripped)
        if not tokens:
            continue
        try:
            row_idx = int(tokens[0])
        except ValueError:
            continue

        row_data: Dict[str, str] = {}
        for col_i, col_name in enumerate(table.columns):
            tok_i = col_i + 1
            if tok_i < len(tokens):
                row_data[col_name] = tokens[tok_i].strip('"')
            else:
                row_data[col_name] = ""
        table.rows[row_idx] = row_data

    return table


def _tokenize_2da_line(line: str) -> List[str]:
    """Tokenize a 2DA data line, respecting quoted strings."""
    tokens = []
    current = ""
    in_quote = False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
            current += ch
        elif ch in (' ', '\t') and not in_quote:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += ch
    if current:
        tokens.append(current)
    return tokens


class TwoDALoader:
    """
    Singleton-friendly loader for KotOR 2DA files.
    Reads from game dir or Override.
    """

    def __init__(self):
        self._tables: Dict[str, TwoDATable] = {}
        self._search_dirs: List[Path] = []

    def set_search_dirs(self, dirs: List[Path]):
        """Set directories to search for .2da files (in priority order)."""
        self._search_dirs = [Path(d) for d in dirs if d]

    def load(self, table_name: str) -> Optional[TwoDATable]:
        """
        Load a 2DA table by name (e.g. "appearance").
        Returns cached result if already loaded.
        """
        name_lower = table_name.lower()
        if name_lower in self._tables:
            return self._tables[name_lower]

        for directory in self._search_dirs:
            path = directory / f"{name_lower}.2da"
            if not path.exists():
                # Case-insensitive fallback
                try:
                    for f in directory.iterdir():
                        if f.name.lower() == f"{name_lower}.2da":
                            path = f
                            break
                except Exception:
                    continue
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    table = _parse_2da(text, name_lower)
                    if table:
                        self._tables[name_lower] = table
                        log.info(f"2DA loaded: {name_lower} ({len(table)} rows) from {path}")
                        return table
                except Exception as e:
                    log.warning(f"2DA load error {path}: {e}")

        log.debug(f"2DA not found: {table_name} (search dirs: {self._search_dirs})")
        return None

    def load_from_text(self, name: str, text: str) -> Optional[TwoDATable]:
        """Load a 2DA from a text string (for tests / embedded data)."""
        table = _parse_2da(text, name.lower())
        if table:
            self._tables[name.lower()] = table
        return table

    def get_name(self, table_name: str, row: int) -> str:
        """Return the label for a given row index."""
        table = self._tables.get(table_name.lower())
        if table is None:
            table = self.load(table_name)
        if table is None:
            return f"Row {row}"
        return table.get_label(row)

    def get_options(self, table_name: str) -> List[Tuple[int, str]]:
        """Return [(row, label)] for building a dropdown."""
        table = self._tables.get(table_name.lower())
        if table is None:
            table = self.load(table_name)
        if table is None:
            return []
        return table.options()

    def is_loaded(self, table_name: str) -> bool:
        return table_name.lower() in self._tables

    def loaded_tables(self) -> List[str]:
        return list(self._tables.keys())


# ── Global singleton ──────────────────────────────────────────────────────

_loader: Optional[TwoDALoader] = None


def get_2da_loader() -> TwoDALoader:
    global _loader
    if _loader is None:
        _loader = TwoDALoader()
    return _loader


# ── Qt ComboBox helper ────────────────────────────────────────────────────

try:
    from PyQt5.QtWidgets import QComboBox
    from PyQt5.QtGui import QFont

    class TwoDAComboBox(QComboBox):
        """
        A ComboBox backed by a 2DA table.
        Shows "LabelName  (row N)" text, stores integer row index as data.
        """

        def __init__(self, table_name: str, current_row: int = 0, parent=None):
            super().__init__(parent)
            self._table_name = table_name
            self.setFont(QFont("Consolas", 8))
            self.setEditable(False)
            self._populate(current_row)

        def _populate(self, current_row: int):
            self.blockSignals(True)
            self.clear()
            loader = get_2da_loader()
            options = loader.get_options(self._table_name)
            selected_idx = 0
            for i, (row_idx, display) in enumerate(options):
                self.addItem(display, row_idx)
                if row_idx == current_row:
                    selected_idx = i
            if not options:
                self.addItem(f"Row {current_row}", current_row)
            else:
                self.setCurrentIndex(selected_idx)
            self.blockSignals(False)

        def current_row_index(self) -> int:
            """Return the currently selected 2DA row index."""
            data = self.currentData()
            if data is not None:
                return int(data)
            return 0

        def set_row(self, row_idx: int):
            """Select the entry with the given row index."""
            for i in range(self.count()):
                if self.itemData(i) == row_idx:
                    self.setCurrentIndex(i)
                    return

except ImportError:
    pass  # headless / test environment


# ── Embedded fallback data for common tables ──────────────────────────────
# If no game dir is set, these minimal tables allow the dropdowns to function.

FACTION_FALLBACK = """\
2DA V2.0

LABEL         GLOBAL
0  Hostile1      1
1  Friendly1     0
2  Neutral1      0
3  Hostile2      1
4  Friendly2     0
5  Neutral2      0
6  PC            0
7  Henchman      0
"""

GENDER_FALLBACK = """\
2DA V2.0

LABEL
0  Male
1  Female
2  Both
3  Other
4  None
"""

CLASS_FALLBACK = """\
2DA V2.0

LABEL           HITDIE  ATTACKBONUSTABLE  FEATSTABLE  SAVINGSTROWTHTYPE
0  Soldier         10   med_attack        soldier_feat     poor/poor/poor
1  Scout            6   med_attack        scout_feat       avg/poor/avg
2  Scoundrel        6   poor_attack       scoundrel_feat   poor/avg/avg
3  Jedi Guardian    10  med_attack        jgua_feat        poor/poor/avg
4  Jedi Consular     6  poor_attack       jcon_feat        poor/poor/avg
5  Jedi Sentinel     8  med_attack        jsen_feat        poor/avg/avg
"""


def load_fallback_tables():
    """Load minimal fallback 2DA tables when no game directory is available."""
    loader = get_2da_loader()
    if not loader.is_loaded("faction"):
        loader.load_from_text("faction", FACTION_FALLBACK)
    if not loader.is_loaded("gender"):
        loader.load_from_text("gender", GENDER_FALLBACK)
    if not loader.is_loaded("classes"):
        loader.load_from_text("classes", CLASS_FALLBACK)
