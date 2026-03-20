"""
tests/test_roadmap_pass7.py  —  Deep-Scan Pass 7
=================================================
Validates the three key improvements from Pass 7:

  1.  GFF CExoLocString — full multi-language round-trip
      • reader returns LocalizedString objects (not plain str)
      • correct cp1252 encoding for English/Western-European substrings
      • correct cp1250 encoding for Polish/Eastern-European substrings
      • correct cp1251 encoding for Russian/Cyrillic substrings
      • backward-compatibility: plain-str write still round-trips cleanly

  2.  Animation pipeline wiring
      • ViewportWidget exposes ``frame_advanced`` signal
      • ViewportWidget has a ``set_animation_panel`` convenience method
      • AnimationTimelinePanel has ``set_viewport`` / ``refresh_entities``
        / ``set_selected_entity`` / ``play_animation_on_entity`` methods
      • AnimationRuler API: ``set_duration``, ``set_current``, ``set_loop``

  3.  MDL base-header helper + walkmesh-editor de-duplication
      • ``read_mdl_base_header`` public function is importable from
        gmodular.formats.mdl_parser
      • Returns correct keys: ``name``, ``bb_min``, ``bb_max``,
        ``root_node_off``, ``model_data_off``
      • WOKParser.parse() uses the shared helper (smoke test)

Run with:  pytest tests/test_roadmap_pass7.py -v
"""
from __future__ import annotations

import struct
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_read(root):
    """Round-trip a GFFRoot through the binary writer then reader."""
    from gmodular.formats.gff_writer import GFFWriter
    from gmodular.formats.gff_reader import GFFReader
    data = GFFWriter(root).to_bytes()
    return GFFReader(data).parse()


def _make_root(**fields):
    """Build a minimal GFFRoot with the given {label: (type_id, value)} fields."""
    from gmodular.formats.gff_types import GFFFieldType, GFFField, GFFRoot
    root = GFFRoot(file_type="TEST")
    root.struct_id = 0xFFFFFFFF
    for label, (ft, val) in fields.items():
        root.fields[label] = GFFField(label, ft, val)
    return root


# ─────────────────────────────────────────────────────────────────────────────
#  1.  GFF CExoLocString — multi-language round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestCExoLocStringRoundTrip:
    """Comprehensive LocalizedString read/write tests."""

    def test_plain_str_legacy_path(self):
        """A plain str passed to a CEXOLOCSTRING field must still round-trip."""
        from gmodular.formats.gff_types import GFFFieldType, LocalizedString
        r2 = _write_read(_make_root(Name=(GFFFieldType.CEXOLOCSTRING, "Hello World")))
        v = r2.fields["Name"].value
        # Reader now returns LocalizedString; get_english() must return the text
        assert isinstance(v, LocalizedString)
        assert v.get_english() == "Hello World"

    def test_localized_string_object_english(self):
        """LocalizedString with an English substring round-trips correctly."""
        from gmodular.formats.gff_types import GFFFieldType, LocalizedString
        ls = LocalizedString.from_english("Player Start")
        r2 = _write_read(_make_root(Name=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["Name"].value
        assert isinstance(v, LocalizedString)
        assert v.get_english() == "Player Start"

    def test_english_cp1252_non_ascii(self):
        """cp1252 non-ASCII characters (e.g. smart quotes) round-trip correctly.

        The byte 0x93 in cp1252 is the Unicode left double quotation mark U+201C.
        If the writer incorrectly used utf-8 this would corrupt to two bytes.
        """
        from gmodular.formats.gff_types import GFFFieldType, LocalizedString, Language, Gender
        # U+2018 = left single quote, U+2019 = right single quote — both cp1252
        text = "\u2018A\u2019"
        ls = LocalizedString.from_english(text)
        r2 = _write_read(_make_root(N=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["N"].value
        assert isinstance(v, LocalizedString)
        assert v.get_english() == text

    def test_polish_cp1250_encoding(self):
        """Polish characters (cp1250) are encoded and decoded correctly."""
        from gmodular.formats.gff_types import (
            GFFFieldType, LocalizedString, Language, Gender,
        )
        polish_text = "Zażółć gęślą jaźń"   # All cp1250
        ls = LocalizedString()
        ls.set(Language.POLISH, Gender.MALE, polish_text)
        r2 = _write_read(_make_root(PL=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["PL"].value
        assert isinstance(v, LocalizedString)
        result = v.get(Language.POLISH, Gender.MALE)
        assert result == polish_text

    def test_russian_cp1251_encoding(self):
        """Russian characters (cp1251) are encoded and decoded correctly."""
        from gmodular.formats.gff_types import (
            GFFFieldType, LocalizedString, Language, Gender,
        )
        russian_text = "Привет мир"   # All cp1251
        ls = LocalizedString()
        ls.set(Language.RUSSIAN, Gender.MALE, russian_text)
        r2 = _write_read(_make_root(RU=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["RU"].value
        assert isinstance(v, LocalizedString)
        result = v.get(Language.RUSSIAN, Gender.MALE)
        assert result == russian_text

    def test_multi_language_single_field(self):
        """A single CEXOLOCSTRING field carrying English + German + French."""
        from gmodular.formats.gff_types import (
            GFFFieldType, LocalizedString, Language, Gender,
        )
        ls = LocalizedString()
        ls.set(Language.ENGLISH, Gender.MALE, "Start")
        ls.set(Language.GERMAN,  Gender.MALE, "Anfang")
        ls.set(Language.FRENCH,  Gender.MALE, "Début")
        r2 = _write_read(_make_root(T=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["T"].value
        assert v.get(Language.ENGLISH, Gender.MALE) == "Start"
        assert v.get(Language.GERMAN,  Gender.MALE) == "Anfang"
        assert v.get(Language.FRENCH,  Gender.MALE) == "Début"

    def test_male_female_substrings(self):
        """Male and female substrings for the same language are kept distinct."""
        from gmodular.formats.gff_types import (
            GFFFieldType, LocalizedString, Language, Gender,
        )
        ls = LocalizedString()
        ls.set(Language.ENGLISH, Gender.MALE,   "Hero")
        ls.set(Language.ENGLISH, Gender.FEMALE, "Heroine")
        r2 = _write_read(_make_root(G=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["G"].value
        assert v.get(Language.ENGLISH, Gender.MALE)   == "Hero"
        assert v.get(Language.ENGLISH, Gender.FEMALE) == "Heroine"

    def test_stringref_preserved(self):
        """TLK stringref is preserved across round-trip."""
        from gmodular.formats.gff_types import GFFFieldType, LocalizedString
        ls = LocalizedString.from_stringref(42)
        ls.set_english = lambda t: None  # just check the ref
        r2 = _write_read(_make_root(S=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["S"].value
        assert v.stringref == 42

    def test_empty_locstring(self):
        """An empty LocalizedString (no substrings) round-trips to an empty locstring."""
        from gmodular.formats.gff_types import GFFFieldType, LocalizedString
        ls = LocalizedString()
        r2 = _write_read(_make_root(E=(GFFFieldType.CEXOLOCSTRING, ls)))
        v = r2.fields["E"].value
        assert isinstance(v, LocalizedString)
        assert v.get_english() == ""

    def test_reader_returns_localized_string_type(self):
        """After a round-trip the field value is always a LocalizedString, not str."""
        from gmodular.formats.gff_types import GFFFieldType, LocalizedString
        r2 = _write_read(_make_root(X=(GFFFieldType.CEXOLOCSTRING, "anything")))
        assert isinstance(r2.fields["X"].value, LocalizedString)

    def test_load_ifo_mod_name_string(self):
        """load_ifo returns mod_name as a plain str (extracted from LocalizedString)."""
        import tempfile, os
        from gmodular.formats.gff_types import (
            GFFFieldType, GFFField, GFFRoot, LocalizedString,
        )
        from gmodular.formats.gff_writer import GFFWriter
        from gmodular.formats.gff_reader import load_ifo

        root = GFFRoot(file_type="IFO ")
        root.struct_id = 0xFFFFFFFF
        ls = LocalizedString.from_english("Test Module")
        root.fields["Mod_Name"] = GFFField("Mod_Name", GFFFieldType.CEXOLOCSTRING, ls)
        root.fields["Mod_Description"] = GFFField("Mod_Description",
                                                   GFFFieldType.CEXOLOCSTRING,
                                                   LocalizedString.from_english("Desc"))

        data = GFFWriter(root).to_bytes()
        with tempfile.NamedTemporaryFile(suffix=".ifo", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            ifo = load_ifo(tmp)
            assert isinstance(ifo.mod_name, str)
            assert ifo.mod_name == "Test Module"
        finally:
            os.unlink(tmp)

    def test_locstring_field_helper_accepts_localized_string(self):
        """_locstring_field in gff_writer wraps a LocalizedString without error."""
        from gmodular.formats.gff_writer import _locstring_field
        from gmodular.formats.gff_types import LocalizedString, GFFFieldType
        ls = LocalizedString.from_english("X")
        fld = _locstring_field("Mod_Name", ls)
        assert fld.type_id == GFFFieldType.CEXOLOCSTRING
        assert isinstance(fld.value, LocalizedString)

    def test_locstring_field_helper_accepts_plain_str(self):
        """_locstring_field in gff_writer wraps a plain str into LocalizedString."""
        from gmodular.formats.gff_writer import _locstring_field
        from gmodular.formats.gff_types import LocalizedString, GFFFieldType
        fld = _locstring_field("Mod_Name", "plain string")
        assert fld.type_id == GFFFieldType.CEXOLOCSTRING
        assert isinstance(fld.value, LocalizedString)
        assert fld.value.get_english() == "plain string"


# ─────────────────────────────────────────────────────────────────────────────
#  2.  LocalizedString unit tests (not GFF dependent)
# ─────────────────────────────────────────────────────────────────────────────

class TestLocalizedStringAPI:
    """Tests for the LocalizedString dataclass in gff_types."""

    def test_from_english_factory(self):
        from gmodular.formats.gff_types import LocalizedString
        ls = LocalizedString.from_english("hello")
        assert ls.get_english() == "hello"

    def test_from_stringref_factory(self):
        from gmodular.formats.gff_types import LocalizedString
        ls = LocalizedString.from_stringref(99)
        assert ls.stringref == 99
        assert ls.get_english() == ""

    def test_get_fallback(self):
        """get_english falls back to first available substring when English missing."""
        from gmodular.formats.gff_types import LocalizedString, Language, Gender
        ls = LocalizedString()
        ls.set(Language.GERMAN, Gender.MALE, "Welt")
        assert ls.get_english() == "Welt"

    def test_set_and_get(self):
        from gmodular.formats.gff_types import LocalizedString, Language, Gender
        ls = LocalizedString()
        ls.set(Language.FRENCH, Gender.FEMALE, "Bonjour")
        assert ls.get(Language.FRENCH, Gender.FEMALE) == "Bonjour"
        assert ls.get(Language.FRENCH, Gender.MALE) is None

    def test_items_iteration(self):
        from gmodular.formats.gff_types import LocalizedString, Language, Gender
        ls = LocalizedString.from_english("A")
        ls.set(Language.FRENCH, Gender.MALE, "B")
        items = list(ls.items())
        assert len(items) == 2

    def test_language_get_encoding(self):
        """Language.get_encoding() returns the correct codepage per spec."""
        from gmodular.formats.gff_types import Language
        assert Language.ENGLISH.get_encoding()  == "cp1252"
        assert Language.FRENCH.get_encoding()   == "cp1252"
        assert Language.GERMAN.get_encoding()   == "cp1252"
        assert Language.POLISH.get_encoding()   == "cp1250"
        assert Language.RUSSIAN.get_encoding()  == "cp1251"
        assert Language.GREEK.get_encoding()    == "cp1253"
        assert Language.TURKISH.get_encoding()  == "cp1254"
        assert Language.HEBREW.get_encoding()   == "cp1255"
        assert Language.ARABIC.get_encoding()   == "cp1256"
        assert Language.KOREAN.get_encoding()   == "cp949"
        assert Language.JAPANESE.get_encoding() == "cp932"

    def test_locstring_substring_id_formula(self):
        """substring_id = language * 2 + gender."""
        from gmodular.formats.gff_types import locstring_substring_id, Language, Gender
        assert locstring_substring_id(Language.ENGLISH, Gender.MALE)   == 0
        assert locstring_substring_id(Language.ENGLISH, Gender.FEMALE) == 1
        assert locstring_substring_id(Language.FRENCH,  Gender.MALE)   == 2
        assert locstring_substring_id(Language.FRENCH,  Gender.FEMALE) == 3

    def test_locstring_pair_decode(self):
        """locstring_pair reverses the substring_id formula."""
        from gmodular.formats.gff_types import locstring_pair, Language, Gender
        lang, gender = locstring_pair(0)
        assert lang   == Language.ENGLISH
        assert gender == Gender.MALE
        lang, gender = locstring_pair(3)
        assert lang   == Language.FRENCH
        assert gender == Gender.FEMALE


# ─────────────────────────────────────────────────────────────────────────────
#  3.  Animation pipeline wiring
# ─────────────────────────────────────────────────────────────────────────────

class TestAnimationPipelineWiring:
    """Verify the animation pipeline API surface without requiring Qt."""

    def test_animation_panel_importable(self):
        """AnimationTimelinePanel is importable (headless)."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert AnimationTimelinePanel is not None

    def test_animation_ruler_importable(self):
        """AnimationRuler is importable."""
        from gmodular.gui.animation_panel import AnimationRuler
        assert AnimationRuler is not None

    def test_animation_panel_has_set_viewport_method(self):
        """AnimationTimelinePanel class has set_viewport method defined."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert callable(getattr(AnimationTimelinePanel, "set_viewport", None))

    def test_animation_panel_has_refresh_entities_method(self):
        """AnimationTimelinePanel class has refresh_entities method defined."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert callable(getattr(AnimationTimelinePanel, "refresh_entities", None))

    def test_animation_panel_has_set_selected_entity_method(self):
        """AnimationTimelinePanel class has set_selected_entity method defined."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert callable(getattr(AnimationTimelinePanel, "set_selected_entity", None))

    def test_animation_panel_has_play_animation_on_entity_method(self):
        """AnimationTimelinePanel class has play_animation_on_entity method."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert callable(getattr(AnimationTimelinePanel, "play_animation_on_entity", None))

    def test_animation_ruler_set_duration(self):
        """AnimationRuler.set_duration accepts float without error (no Qt needed)."""
        from gmodular.gui.animation_panel import AnimationRuler, _HAS_QT
        if _HAS_QT:
            pytest.skip("AnimationRuler requires QApplication when Qt is available")
        ruler = AnimationRuler()
        ruler.set_duration(2.5)
        assert ruler._duration == 2.5

    def test_animation_ruler_set_current(self):
        """AnimationRuler.set_current clamps to [0, duration] (no Qt)."""
        from gmodular.gui.animation_panel import AnimationRuler, _HAS_QT
        if _HAS_QT:
            pytest.skip("AnimationRuler requires QApplication when Qt is available")
        ruler = AnimationRuler()
        ruler.set_duration(3.0)
        ruler.set_current(1.5)
        assert ruler._current == 1.5
        ruler.set_current(-1.0)
        assert ruler._current == 0.0
        ruler.set_current(999.0)
        assert ruler._current == 3.0

    def test_animation_ruler_set_loop(self):
        """AnimationRuler.set_loop stores the value (no Qt)."""
        from gmodular.gui.animation_panel import AnimationRuler, _HAS_QT
        if _HAS_QT:
            pytest.skip("AnimationRuler requires QApplication when Qt is available")
        ruler = AnimationRuler()
        ruler.set_loop(True)
        assert ruler._loop is True
        ruler.set_loop(False)
        assert ruler._loop is False

    def test_animation_ruler_has_set_duration(self):
        """AnimationRuler class defines set_duration."""
        from gmodular.gui.animation_panel import AnimationRuler
        assert callable(getattr(AnimationRuler, "set_duration", None))

    def test_animation_ruler_has_set_current(self):
        """AnimationRuler class defines set_current."""
        from gmodular.gui.animation_panel import AnimationRuler
        assert callable(getattr(AnimationRuler, "set_current", None))

    def test_animation_ruler_has_set_loop(self):
        """AnimationRuler class defines set_loop."""
        from gmodular.gui.animation_panel import AnimationRuler
        assert callable(getattr(AnimationRuler, "set_loop", None))

    def test_viewport_has_set_animation_panel(self):
        """ViewportWidget exposes set_animation_panel method."""
        from gmodular.gui.viewport import ViewportWidget
        assert hasattr(ViewportWidget, "set_animation_panel")
        assert callable(ViewportWidget.set_animation_panel)

    def test_viewport_has_frame_advanced_signal(self):
        """ViewportWidget declares a frame_advanced signal at class level."""
        from gmodular.gui.viewport import ViewportWidget
        # The signal is a class attribute (defined with Signal or _Stub)
        assert hasattr(ViewportWidget, "frame_advanced")

    def test_animation_panel_poll_player_method(self):
        """AnimationTimelinePanel defines _poll_player for frame-signal connection."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert callable(getattr(AnimationTimelinePanel, "_poll_player", None))

    def test_animation_panel_signals_declared(self):
        """animation_changed and time_scrubbed signals are declared as class attrs."""
        from gmodular.gui.animation_panel import AnimationTimelinePanel
        assert hasattr(AnimationTimelinePanel, "animation_changed")
        assert hasattr(AnimationTimelinePanel, "time_scrubbed")


# ─────────────────────────────────────────────────────────────────────────────
#  4.  MDL base-header helper + walkmesh-editor de-duplication
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLBaseHeaderHelper:
    """Tests for the public read_mdl_base_header() function."""

    def _make_mdl_stub(self, name: str = "m_room01",
                       bb_min=(0.0, 0.0, 0.0),
                       bb_max=(10.0, 10.0, 1.0),
                       base: int = 12) -> bytes:
        """Build a minimal synthetic MDL binary with correct header layout.

        The stub only fills the fields that read_mdl_base_header() reads:
          base+8..+40   ModelName[32]
          base+40       RootNodeOffset (4 bytes)
          base+80+24    BoundingBoxMin (3×float)
          base+80+36    BoundingBoxMax (3×float)
        Total: base + 80 + 48 = base + 128 bytes minimum.
        """
        # Total size: base + 200 bytes to be safe
        buf = bytearray(base + 200)

        # File offset 4: model_data_off pointer
        struct.pack_into("<I", buf, 4, base)

        # FunctionPtr1/2 at base+0/4 (leave as 0)
        # ModelName at base+8 (32 bytes)
        name_bytes = name.encode("ascii")[:32].ljust(32, b"\x00")
        buf[base + 8: base + 40] = name_bytes

        # RootNodeOffset at base+40
        struct.pack_into("<I", buf, base + 40, 0)  # no root node

        # BoundingBox at base+80+24 (min) and base+80+36 (max)
        M = base + 80
        struct.pack_into("<3f", buf, M + 24, *bb_min)
        struct.pack_into("<3f", buf, M + 36, *bb_max)

        return bytes(buf)

    def test_importable(self):
        from gmodular.formats.mdl_parser import read_mdl_base_header
        assert callable(read_mdl_base_header)

    def test_returns_dict_with_expected_keys(self):
        from gmodular.formats.mdl_parser import read_mdl_base_header
        data = self._make_mdl_stub()
        hdr = read_mdl_base_header(data, base=12)
        assert "name"           in hdr
        assert "bb_min"         in hdr
        assert "bb_max"         in hdr
        assert "root_node_off"  in hdr
        assert "model_data_off" in hdr

    def test_name_extraction(self):
        from gmodular.formats.mdl_parser import read_mdl_base_header
        data = self._make_mdl_stub(name="dan13_01a")
        hdr = read_mdl_base_header(data, base=12)
        assert hdr["name"] == "dan13_01a"

    def test_bounding_box_extraction(self):
        from gmodular.formats.mdl_parser import read_mdl_base_header
        bb_min = (-5.0, -5.0, 0.0)
        bb_max = (5.0, 5.0, 3.0)
        data = self._make_mdl_stub(bb_min=bb_min, bb_max=bb_max)
        hdr = read_mdl_base_header(data, base=12)
        assert abs(hdr["bb_min"][0] - (-5.0)) < 1e-5
        assert abs(hdr["bb_min"][1] - (-5.0)) < 1e-5
        assert abs(hdr["bb_max"][0] - 5.0)   < 1e-5
        assert abs(hdr["bb_max"][2] - 3.0)   < 1e-5

    def test_model_data_off_echoed(self):
        from gmodular.formats.mdl_parser import read_mdl_base_header
        data = self._make_mdl_stub(base=12)
        hdr = read_mdl_base_header(data, base=12)
        assert hdr["model_data_off"] == 12

    def test_raises_on_too_small_data(self):
        from gmodular.formats.mdl_parser import read_mdl_base_header
        with pytest.raises(ValueError, match="too small"):
            read_mdl_base_header(b"\x00" * 20, base=12)

    def test_walkmesh_editor_uses_helper(self):
        """WOKParser.parse() imports read_mdl_base_header without error."""
        from gmodular.gui.walkmesh_editor import _HAS_MDL_HELPER
        # The import should succeed in this environment
        assert _HAS_MDL_HELPER is True

    def test_wok_parser_parse_smoke(self):
        """WOKParser.parse() on a minimal MDL-format stub returns a WOKData."""
        from gmodular.gui.walkmesh_editor import WOKParser, WOKData
        data = self._make_mdl_stub(name="room01")
        wok = WOKParser(data).parse()
        assert isinstance(wok, WOKData)

    def test_wok_parser_name_from_helper(self):
        """WOKParser populates model_name via the shared header helper."""
        from gmodular.gui.walkmesh_editor import WOKParser
        data = self._make_mdl_stub(name="tar_m05aa")
        wok = WOKParser(data).parse()
        assert wok.model_name == "tar_m05aa"


# ─────────────────────────────────────────────────────────────────────────────
#  5.  Regression guards — existing tests must still pass
# ─────────────────────────────────────────────────────────────────────────────

class TestPass7Regressions:
    """Regression checks: existing behaviour preserved after Pass 7 changes."""

    def test_gff_cexostring_unaffected(self):
        """CEXOSTRING (not CExoLocString) still round-trips as plain str."""
        from gmodular.formats.gff_types import GFFFieldType
        r2 = _write_read(_make_root(T=(GFFFieldType.CEXOSTRING, "simple")))
        assert r2.fields["T"].value == "simple"

    def test_gff_resref_unaffected(self):
        """RESREF fields still round-trip as plain str after Pass 7."""
        from gmodular.formats.gff_types import GFFFieldType
        r2 = _write_read(_make_root(R=(GFFFieldType.RESREF, "chair001")))
        assert r2.fields["R"].value == "chair001"

    def test_gff_vector_unaffected(self):
        """VECTOR fields still round-trip correctly after Pass 7."""
        from gmodular.formats.gff_types import GFFFieldType, Vector3
        v = Vector3(1.5, 2.5, -3.5)
        r2 = _write_read(_make_root(P=(GFFFieldType.VECTOR, v)))
        v2 = r2.fields["P"].value
        assert isinstance(v2, Vector3)
        assert abs(v2.x - 1.5) < 1e-5

    def test_load_are_name_is_str(self):
        """load_are returns are.name as a plain str."""
        import tempfile, os
        from gmodular.formats.gff_types import (
            GFFFieldType, GFFField, GFFRoot, LocalizedString,
        )
        from gmodular.formats.gff_writer import GFFWriter
        from gmodular.formats.gff_reader import load_are

        root = GFFRoot(file_type="ARE ")
        root.struct_id = 0xFFFFFFFF
        ls = LocalizedString.from_english("Dantooine Plains")
        root.fields["Name"] = GFFField("Name", GFFFieldType.CEXOLOCSTRING, ls)
        root.fields["Tag"]  = GFFField("Tag",  GFFFieldType.CEXOSTRING, "dan_plains")

        data = GFFWriter(root).to_bytes()
        with tempfile.NamedTemporaryFile(suffix=".are", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            are = load_are(tmp)
            assert isinstance(are.name, str)
            assert are.name == "Dantooine Plains"
            assert are.tag  == "dan_plains"
        finally:
            os.unlink(tmp)

    def test_mdl_base_header_name_with_null_padding(self):
        """Name field with null-padding is stripped correctly."""
        from gmodular.formats.mdl_parser import read_mdl_base_header
        # Build 32-byte name with internal nulls (should stop at first null)
        buf = bytearray(200)
        struct.pack_into("<I", buf, 4, 12)
        name_padded = b"m_room\x00\x00\x00\x00" + b"\x00" * 22
        buf[12 + 8: 12 + 40] = name_padded
        M = 12 + 80
        struct.pack_into("<3f", buf, M + 24, 0.0, 0.0, 0.0)
        struct.pack_into("<3f", buf, M + 36, 1.0, 1.0, 1.0)
        hdr = read_mdl_base_header(bytes(buf), base=12)
        assert hdr["name"] == "m_room"
