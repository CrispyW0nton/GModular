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
        """Get a cell value by (row_index, column_name)."""
        row_data = self.rows.get(row, {})
        return row_data.get(column, default)

    def get_int(self, row: int, column: str, default: int = 0) -> int:
        """Get a cell value as int. Returns default on error."""
        val = self.get(row, column, "")
        if val in ("", "****"):
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, row: int, column: str, default: float = 0.0) -> float:
        """Get a cell value as float. Returns default on error."""
        val = self.get(row, column, "")
        if val in ("", "****"):
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_label(self, row: int) -> str:
        """Return the LABEL column (first text column) for a row."""
        row_data = self.rows.get(row, {})
        # Try LABEL column first, then first column
        if "LABEL" in row_data:
            return row_data["LABEL"].strip('"')
        if self.columns:
            return row_data.get(self.columns[0], "").strip('"')
        return f"Row {row}"

    def get_column(self, column: str) -> List[Tuple[int, str]]:
        """
        Return all (row_index, value) pairs for the given column name.
        Rows with empty or '****' values are included.
        """
        result = []
        for idx in sorted(self.rows.keys()):
            val = self.rows[idx].get(column, "")
            result.append((idx, val))
        return result

    def find_row(self, column: str, value: str,
                 case_sensitive: bool = False) -> Optional[int]:
        """
        Find the first row where column == value.
        Returns the row index, or None if not found.
        """
        needle = value if case_sensitive else value.lower()
        for idx in sorted(self.rows.keys()):
            cell = self.rows[idx].get(column, "")
            cell_cmp = cell if case_sensitive else cell.lower()
            if cell_cmp == needle:
                return idx
        return None

    def find_rows(self, column: str, value: str,
                  case_sensitive: bool = False) -> List[int]:
        """Find all row indices where column == value."""
        needle = value if case_sensitive else value.lower()
        result = []
        for idx in sorted(self.rows.keys()):
            cell = self.rows[idx].get(column, "")
            cell_cmp = cell if case_sensitive else cell.lower()
            if cell_cmp == needle:
                result.append(idx)
        return result

    def column_values(self, column: str, skip_empty: bool = True) -> List[str]:
        """Return all distinct values in a column, in row order."""
        vals = []
        for idx in sorted(self.rows.keys()):
            v = self.rows[idx].get(column, "")
            if skip_empty and v in ("", "****"):
                continue
            if v not in vals:
                vals.append(v)
        return vals

    def options(self) -> List[Tuple[int, str]]:
        """Return [(row_index, display_name)] sorted by index."""
        result = []
        for idx in sorted(self.rows.keys()):
            label = self.get_label(idx)
            if label and label != "****":
                result.append((idx, f"{label}  (row {idx})"))
        return result

    def to_text(self) -> str:
        """Serialize this 2DA table back to plain-text .2da format."""
        lines = ["2DA V2.0", ""]
        # Column header line
        lines.append("  ".join(self.columns))
        # Data rows
        for idx in sorted(self.rows.keys()):
            row_data = self.rows[idx]
            cells = [str(idx)]
            for col in self.columns:
                v = row_data.get(col, "****")
                if not v:
                    v = "****"
                # Quote if contains spaces
                if " " in v and not v.startswith('"'):
                    v = f'"{v}"'
                cells.append(v)
            lines.append("  ".join(cells))
        return "\n".join(lines) + "\n"

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def __len__(self):
        return len(self.rows)

    def __contains__(self, row_idx: int) -> bool:
        return row_idx in self.rows

    def __iter__(self):
        """Iterate over (row_index, row_dict) in row-index order."""
        for idx in sorted(self.rows.keys()):
            yield idx, self.rows[idx]


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

    def load_from_bytes(self, name: str, data: bytes,
                        encoding: str = "utf-8") -> Optional[TwoDATable]:
        """
        Load a 2DA from raw bytes (e.g. fetched from a game archive).
        Tries the given encoding first, then falls back to latin-1.
        """
        for enc in (encoding, "latin-1"):
            try:
                text = data.decode(enc, errors="replace")
                return self.load_from_text(name, text)
            except Exception:
                continue
        return None

    def get_table(self, table_name: str) -> Optional[TwoDATable]:
        """Return a loaded table (auto-load if not cached)."""
        name_lower = table_name.lower()
        if name_lower not in self._tables:
            self.load(table_name)
        return self._tables.get(name_lower)

    def get_cell(self, table_name: str, row: int,
                 column: str, default: str = "") -> str:
        """Convenience: get a single cell value directly."""
        table = self.get_table(table_name)
        if table is None:
            return default
        return table.get(row, column, default)

    def find_row(self, table_name: str, column: str, value: str,
                 case_sensitive: bool = False) -> Optional[int]:
        """Find the first row in a table where column == value."""
        table = self.get_table(table_name)
        if table is None:
            return None
        return table.find_row(column, value, case_sensitive)

    def reload(self, table_name: str) -> Optional[TwoDATable]:
        """Force-reload a 2DA from disk, discarding the cached version."""
        name_lower = table_name.lower()
        self._tables.pop(name_lower, None)
        return self.load(table_name)

    def clear_cache(self):
        """Discard all cached 2DA tables (e.g. after game directory change)."""
        self._tables.clear()
        log.debug("2DA cache cleared")

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

    @classmethod
    def from_bytes(cls, data: bytes) -> "TwoDAData_like":  # type: ignore[return]
        """Convenience classmethod: parse *data* and return a TwoDAData.

        Delegates to :meth:`TwoDAData.from_bytes
        <gmodular.formats.kotor_formats.TwoDAData.from_bytes>`.
        Importable as ``TwoDALoader.from_bytes(data)`` for backwards compat.
        """
        from gmodular.formats.kotor_formats import TwoDAData  # local import avoids circular
        return TwoDAData.from_bytes(data)


# ── Global singleton ──────────────────────────────────────────────────────

_loader: Optional[TwoDALoader] = None


def get_2da_loader() -> TwoDALoader:
    global _loader
    if _loader is None:
        _loader = TwoDALoader()
    return _loader


# ── Qt ComboBox helper ────────────────────────────────────────────────────

try:
    from qtpy.QtWidgets import QComboBox
    from qtpy.QtGui import QFont

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


SURFACEMAT_FALLBACK = """\
2DA V2.0

LABEL               Walk   WalkCam  Grapple  Sound  Dirt  Grass  Puddles  Overlay  Color
0   Dirt            1      1        1        0      1     0      0        ****     ****
1   Obscuring       1      1        1        0      0     0      0        ****     ****
2   Grass           1      1        1        0      0     1      0        ****     ****
3   Stone           1      1        1        0      0     0      0        ****     ****
4   Wood            1      1        1        0      0     0      0        ****     ****
5   Water           1      1        0        0      0     0      0        ****     ****
6   NonWalk         0      0        0        0      0     0      0        ****     ****
7   Transparent     0      0        0        0      0     0      0        ****     ****
8   Carpet          1      1        1        0      0     0      0        ****     ****
9   Metal           1      1        1        0      0     0      0        ****     ****
10  Puddles         1      1        1        0      0     0      1        ****     ****
11  Swamp           1      1        1        0      0     0      0        ****     ****
12  Mud             1      1        1        0      0     0      0        ****     ****
13  Leaves          1      1        1        0      0     0      0        ****     ****
14  Lava            0      0        0        0      0     0      0        ****     ****
15  BottomlessPit   0      0        0        0      0     0      0        ****     ****
16  DeepWater       0      0        0        0      0     0      0        ****     ****
17  Door            1      1        0        0      0     0      0        ****     ****
18  Snow            1      1        1        0      0     0      0        ****     ****
19  Sand            1      1        1        0      0     0      0        ****     ****
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
    if not loader.is_loaded("surfacemat"):
        loader.load_from_text("surfacemat", SURFACEMAT_FALLBACK)


def get_surfacemat_name(material_id: int) -> str:
    """
    Return the label for a surface material ID from surfacemat.2da.

    Falls back to a hardcoded list if the 2DA table is not loaded.
    Used by WOK parser and MDL AABB mesh inspector.
    """
    loader = get_2da_loader()
    if not loader.is_loaded("surfacemat"):
        load_fallback_tables()
    table = loader.get_table("surfacemat")
    if table:
        return table.get_label(material_id)
    # Hardcoded fallback
    _NAMES = [
        "Dirt", "Obscuring", "Grass", "Stone", "Wood", "Water",
        "NonWalk", "Transparent", "Carpet", "Metal", "Puddles",
        "Swamp", "Mud", "Leaves", "Lava", "BottomlessPit",
        "DeepWater", "Door", "Snow", "Sand",
    ]
    if 0 <= material_id < len(_NAMES):
        return _NAMES[material_id]
    return f"Material_{material_id}"


def is_walkable_from_2da(material_id: int) -> bool:
    """
    Return True if the surface material is walkable, using surfacemat.2da.
    Falls back to the hardcoded table if 2DA is unavailable.
    """
    loader = get_2da_loader()
    if not loader.is_loaded("surfacemat"):
        load_fallback_tables()
    table = loader.get_table("surfacemat")
    if table:
        return table.get_int(material_id, "Walk", default=0) != 0
    # Hardcoded fallback (same as wok_parser._SURF_WALKABLE)
    _WALKABLE = [
        True, True, True, True, True, True, False, False,
        True, True, True, True, True, True, False, False,
        False, True, True, True,
    ]
    if 0 <= material_id < len(_WALKABLE):
        return _WALKABLE[material_id]
    return False
