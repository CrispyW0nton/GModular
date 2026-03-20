"""
GModular — TLK (Talk Table) Integration Tests
==============================================
Tests the TLK V3.0 binary reader/writer.
Reference: PyKotor Libraries/PyKotor/src/pykotor/resource/formats/tlk/io_tlk.py

Coverage:
 - TLK header parsing (magic, version, language)
 - Entry table loading (flags, text, sound resref, sound length)
 - Text string decoding
 - Round-trip encode/decode
 - TLKFile CRUD operations
 - Multi-language support
 - Error / edge-case robustness
"""
from __future__ import annotations

import struct
import unittest
from pathlib import Path


# ── Imports ───────────────────────────────────────────────────────────────────
from gmodular.formats.tlk_reader import (
    TLKReader,
    TLKWriter,
    TLKFile,
    TLKEntry,
    read_tlk,
    write_tlk,
    TLK_MAGIC,
    TLK_VERSION,
    TLK_HEADER_SIZE,
    TLK_ENTRY_SIZE,
    TLK_INVALID_STRREF,
    TLK_FLAG_TEXT_PRESENT,
    TLK_FLAG_SOUND_PRESENT,
    TLK_FLAG_SOUND_LENGTH,
    LANG_ENGLISH,
    LANG_FRENCH,
    LANG_GERMAN,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_tlk(entries: list) -> bytes:
    """
    Build a minimal TLK V3.0 binary from a list of (text, sound_resref, sound_len).
    Returns raw bytes.
    """
    n = len(entries)
    strings_offset = TLK_HEADER_SIZE + n * TLK_ENTRY_SIZE

    # Build string data
    string_data   = bytearray()
    text_offsets  = []
    text_lengths  = []
    for text, _, _ in entries:
        if text:
            raw = text.encode("utf-8")
            text_offsets.append(len(string_data))
            text_lengths.append(len(raw))
            string_data.extend(raw)
        else:
            text_offsets.append(0)
            text_lengths.append(0)

    buf = bytearray()

    # Header
    buf += TLK_MAGIC
    buf += TLK_VERSION
    buf += struct.pack("<III", LANG_ENGLISH, n, strings_offset)

    # Entry table
    for i, (text, sound, sound_len) in enumerate(entries):
        flags = 0
        if text:   flags |= TLK_FLAG_TEXT_PRESENT
        if sound:  flags |= TLK_FLAG_SOUND_PRESENT
        if sound_len > 0: flags |= TLK_FLAG_SOUND_LENGTH
        rr = (sound or "")[:16].encode("ascii", errors="replace").ljust(16, b"\x00")
        buf += struct.pack("<I", flags)
        buf += rr
        buf += struct.pack("<IIII", 0, 0, text_offsets[i], text_lengths[i])
        buf += struct.pack("<f", sound_len)

    buf += string_data
    return bytes(buf)


# ═════════════════════════════════════════════════════════════════════════════
#  1. Constants
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKConstants(unittest.TestCase):

    def test_magic(self):
        self.assertEqual(TLK_MAGIC, b"TLK ")

    def test_version(self):
        self.assertEqual(TLK_VERSION, b"V3.0")

    def test_header_size(self):
        self.assertEqual(TLK_HEADER_SIZE, 20)

    def test_entry_size(self):
        self.assertEqual(TLK_ENTRY_SIZE, 40)

    def test_invalid_strref(self):
        self.assertEqual(TLK_INVALID_STRREF, 0xFFFFFFFF)

    def test_flag_values(self):
        self.assertEqual(TLK_FLAG_TEXT_PRESENT,  0x0001)
        self.assertEqual(TLK_FLAG_SOUND_PRESENT, 0x0002)
        self.assertEqual(TLK_FLAG_SOUND_LENGTH,  0x0004)

    def test_language_english(self):
        self.assertEqual(LANG_ENGLISH, 0)

    def test_language_french(self):
        self.assertEqual(LANG_FRENCH, 1)

    def test_language_german(self):
        self.assertEqual(LANG_GERMAN, 2)


# ═════════════════════════════════════════════════════════════════════════════
#  2. TLKEntry data class
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKEntry(unittest.TestCase):

    def test_default_flags_text_only(self):
        e = TLKEntry(text="Hello", text_present=True)
        self.assertEqual(e.flags & TLK_FLAG_TEXT_PRESENT, TLK_FLAG_TEXT_PRESENT)
        self.assertEqual(e.flags & TLK_FLAG_SOUND_PRESENT, 0)

    def test_flags_with_sound(self):
        e = TLKEntry(text="Hi", sound_resref="nar_hi", text_present=True,
                     sound_present=True)
        self.assertEqual(e.flags & TLK_FLAG_SOUND_PRESENT, TLK_FLAG_SOUND_PRESENT)

    def test_flags_with_sound_length(self):
        e = TLKEntry(text="Hi", sound_length=2.5, text_present=True,
                     soundlength_present=True)
        self.assertEqual(e.flags & TLK_FLAG_SOUND_LENGTH, TLK_FLAG_SOUND_LENGTH)

    def test_no_flags_empty_entry(self):
        e = TLKEntry()
        self.assertEqual(e.flags, 0)

    def test_repr(self):
        e = TLKEntry(text="Test")
        self.assertIn("TLKEntry", repr(e))


# ═════════════════════════════════════════════════════════════════════════════
#  3. TLKFile operations
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKFile(unittest.TestCase):

    def test_add_returns_strref(self):
        tlk = TLKFile()
        idx = tlk.add("Hello there!")
        self.assertEqual(idx, 0)

    def test_add_multiple(self):
        tlk = TLKFile()
        for i in range(5):
            tlk.add(f"String {i}")
        self.assertEqual(len(tlk), 5)

    def test_get_text_valid(self):
        tlk = TLKFile()
        tlk.add("General Kenobi")
        self.assertEqual(tlk.get_text(0), "General Kenobi")

    def test_get_text_invalid_strref(self):
        tlk = TLKFile()
        self.assertIsNone(tlk.get_text(TLK_INVALID_STRREF))

    def test_get_text_negative_strref(self):
        tlk = TLKFile()
        self.assertIsNone(tlk.get_text(-1))

    def test_get_text_out_of_range(self):
        tlk = TLKFile()
        tlk.add("Only one")
        self.assertIsNone(tlk.get_text(999))

    def test_get_sound_present(self):
        tlk = TLKFile()
        tlk.add("Hi", sound_resref="nar_hi001", sound_length=1.5)
        s = tlk.get_sound(0)
        self.assertEqual(s, "nar_hi001")

    def test_get_sound_absent(self):
        tlk = TLKFile()
        tlk.add("No sound")
        self.assertIsNone(tlk.get_sound(0))

    def test_set_new(self):
        tlk = TLKFile()
        tlk.set(0, "New text")
        self.assertEqual(tlk.get_text(0), "New text")

    def test_set_pad_gaps(self):
        tlk = TLKFile()
        tlk.set(5, "Gap text")
        self.assertEqual(len(tlk), 6)
        self.assertEqual(tlk.get_text(5), "Gap text")
        self.assertEqual(tlk.get_text(0), None)

    def test_set_overwrite(self):
        tlk = TLKFile()
        tlk.add("Original")
        tlk.set(0, "Updated")
        self.assertEqual(tlk.get_text(0), "Updated")

    def test_resize_grow(self):
        tlk = TLKFile()
        tlk.resize(10)
        self.assertEqual(len(tlk), 10)

    def test_resize_shrink(self):
        tlk = TLKFile()
        for _ in range(5):
            tlk.add("x")
        tlk.resize(2)
        self.assertEqual(len(tlk), 2)

    def test_find_text_found(self):
        tlk = TLKFile()
        tlk.add("Darth Malak")
        tlk.add("HK-47")
        self.assertEqual(tlk.find_text("HK-47"), 1)

    def test_find_text_not_found(self):
        tlk = TLKFile()
        tlk.add("Hello")
        self.assertIsNone(tlk.find_text("Not here"))

    def test_iter(self):
        tlk = TLKFile()
        tlk.add("A"); tlk.add("B"); tlk.add("C")
        texts = [e.text for _, e in tlk]
        self.assertEqual(texts, ["A", "B", "C"])

    def test_getitem(self):
        tlk = TLKFile()
        tlk.add("Bastila")
        self.assertEqual(tlk[0].text, "Bastila")

    def test_language_default_english(self):
        tlk = TLKFile()
        self.assertEqual(tlk.language_id, LANG_ENGLISH)

    def test_language_set(self):
        tlk = TLKFile(language_id=LANG_GERMAN)
        self.assertEqual(tlk.language_id, LANG_GERMAN)


# ═════════════════════════════════════════════════════════════════════════════
#  4. TLK Binary Parsing
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKParsing(unittest.TestCase):

    def _parse(self, entries):
        return TLKReader.from_bytes(_build_tlk(entries))

    def test_empty_tlk(self):
        tlk = self._parse([])
        self.assertEqual(len(tlk), 0)

    def test_single_entry(self):
        tlk = self._parse([("Hello", "", 0.0)])
        self.assertEqual(len(tlk), 1)
        self.assertEqual(tlk.get_text(0), "Hello")

    def test_multiple_entries(self):
        tlk = self._parse([
            ("First", "", 0.0),
            ("Second", "", 0.0),
            ("Third", "", 0.0),
        ])
        self.assertEqual(len(tlk), 3)
        self.assertEqual(tlk.get_text(0), "First")
        self.assertEqual(tlk.get_text(1), "Second")
        self.assertEqual(tlk.get_text(2), "Third")

    def test_sound_resref_loaded(self):
        tlk = self._parse([("Hi there", "nar_hi001", 2.5)])
        e = tlk.entries[0]
        self.assertEqual(e.sound_resref, "nar_hi001")

    def test_sound_length_loaded(self):
        tlk = self._parse([("Hi", "sound001", 3.14)])
        e = tlk.entries[0]
        self.assertAlmostEqual(e.sound_length, 3.14, places=2)

    def test_text_present_flag(self):
        tlk = self._parse([("Something", "", 0.0)])
        e = tlk.entries[0]
        self.assertTrue(e.text_present)

    def test_sound_present_flag(self):
        tlk = self._parse([("Greetings", "snd001", 1.0)])
        e = tlk.entries[0]
        self.assertTrue(e.sound_present)

    def test_empty_text_entry(self):
        tlk = self._parse([("", "", 0.0)])
        self.assertIsNone(tlk.get_text(0))

    def test_language_id_loaded(self):
        data = _build_tlk([("Test", "", 0.0)])
        # Patch language_id to French
        patched = bytearray(data)
        struct.pack_into("<I", patched, 8, LANG_FRENCH)
        tlk = TLKReader.from_bytes(bytes(patched))
        self.assertEqual(tlk.language_id, LANG_FRENCH)

    def test_bad_magic_returns_empty(self):
        tlk = TLKReader.from_bytes(b"XXXX" + b"V3.0" + b"\x00" * 100)
        self.assertEqual(len(tlk), 0)

    def test_bad_version_returns_empty(self):
        tlk = TLKReader.from_bytes(b"TLK " + b"V9.9" + b"\x00" * 100)
        self.assertEqual(len(tlk), 0)

    def test_too_small_returns_empty(self):
        tlk = TLKReader.from_bytes(b"TLK shortdata")
        self.assertEqual(len(tlk), 0)

    def test_empty_bytes_returns_empty(self):
        tlk = TLKReader.from_bytes(b"")
        self.assertEqual(len(tlk), 0)

    def test_unicode_text(self):
        """UTF-8 text with non-ASCII characters."""
        tlk = TLKFile()
        tlk.add("Über kräftig")  # German umlaut
        data = TLKWriter(tlk).to_bytes()
        rt = TLKReader.from_bytes(data)
        self.assertEqual(rt.get_text(0), "Über kräftig")

    def test_long_text(self):
        """Long text strings should be preserved."""
        long_text = "A" * 5000
        tlk = TLKFile()
        tlk.add(long_text)
        data = TLKWriter(tlk).to_bytes()
        rt = TLKReader.from_bytes(data)
        self.assertEqual(rt.get_text(0), long_text)


# ═════════════════════════════════════════════════════════════════════════════
#  5. TLK Round-Trip
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKRoundTrip(unittest.TestCase):

    def _rt(self, tlk: TLKFile) -> TLKFile:
        return TLKReader.from_bytes(TLKWriter(tlk).to_bytes())

    def test_empty_round_trip(self):
        tlk = TLKFile()
        rt = self._rt(tlk)
        self.assertEqual(len(rt), 0)

    def test_single_text_round_trip(self):
        tlk = TLKFile()
        tlk.add("I am HK-47")
        rt = self._rt(tlk)
        self.assertEqual(rt.get_text(0), "I am HK-47")

    def test_many_entries_round_trip(self):
        tlk = TLKFile()
        texts = [f"String number {i}" for i in range(50)]
        for t in texts:
            tlk.add(t)
        rt = self._rt(tlk)
        self.assertEqual(len(rt), 50)
        for i, t in enumerate(texts):
            self.assertEqual(rt.get_text(i), t)

    def test_sound_resref_round_trip(self):
        tlk = TLKFile()
        tlk.add("Greetings", sound_resref="nar_greet", sound_length=2.1)
        rt = self._rt(tlk)
        self.assertEqual(rt.entries[0].sound_resref, "nar_greet")
        self.assertAlmostEqual(rt.entries[0].sound_length, 2.1, places=2)

    def test_language_id_round_trip(self):
        tlk = TLKFile(language_id=LANG_FRENCH)
        tlk.add("Bonjour")
        rt = self._rt(tlk)
        self.assertEqual(rt.language_id, LANG_FRENCH)

    def test_mixed_entries_round_trip(self):
        """Mix of text-only, sound+text, and empty entries."""
        tlk = TLKFile()
        tlk.add("Text only")
        tlk.add("With sound", sound_resref="snd001", sound_length=1.5)
        tlk.add("")  # empty
        rt = self._rt(tlk)
        self.assertEqual(rt.get_text(0), "Text only")
        self.assertEqual(rt.get_text(1), "With sound")
        self.assertIsNone(rt.get_text(2))

    def test_magic_preserved(self):
        tlk = TLKFile()
        tlk.add("Test")
        data = TLKWriter(tlk).to_bytes()
        self.assertEqual(data[:4], b"TLK ")
        self.assertEqual(data[4:8], b"V3.0")

    def test_header_size_correct(self):
        tlk = TLKFile()
        tlk.add("Test")
        data = TLKWriter(tlk).to_bytes()
        self.assertGreaterEqual(len(data), TLK_HEADER_SIZE + TLK_ENTRY_SIZE)

    def test_write_then_read_entry_count(self):
        tlk = TLKFile()
        for i in range(10):
            tlk.add(f"Entry {i}")
        data = TLKWriter(tlk).to_bytes()
        count, = struct.unpack_from("<I", data, 12)  # string_count at offset 12
        self.assertEqual(count, 10)

    def test_convenience_functions(self):
        tlk = TLKFile()
        tlk.add("Hello")
        data = write_tlk(tlk)
        rt = read_tlk(data)
        self.assertEqual(rt.get_text(0), "Hello")


# ═════════════════════════════════════════════════════════════════════════════
#  6. TLK from file
# ═════════════════════════════════════════════════════════════════════════════

class TestTLKFromFile(unittest.TestCase):

    def setUp(self):
        self._tmp = Path(__file__).parent / "test_data" / "tmp_test.tlk"

    def tearDown(self):
        if self._tmp.exists():
            self._tmp.unlink()

    def test_write_and_read_file(self):
        tlk = TLKFile()
        tlk.add("Saved to file")
        TLKWriter(tlk).to_file(str(self._tmp))
        self.assertTrue(self._tmp.exists())

        rt = TLKReader.from_file(str(self._tmp))
        self.assertEqual(rt.get_text(0), "Saved to file")

    def test_from_file_nonexistent(self):
        rt = TLKReader.from_file("/no/such/file.tlk")
        self.assertEqual(len(rt), 0)


# ═════════════════════════════════════════════════════════════════════════════
#  7. GFF STRREF Integration
# ═════════════════════════════════════════════════════════════════════════════

class TestStrRefIntegration(unittest.TestCase):
    """
    Verify that STRREF type (18) in GFF files round-trips correctly
    and that TLK lookups work as expected.
    """

    def test_gff_strref_field_write_read(self):
        """GFF STRREF fields store a u32 StrRef index."""
        from gmodular.formats.gff_types import GFFFieldType, GFFField, GFFStruct
        f = GFFField(label="Description", type_id=GFFFieldType.STRREF, value=42)
        self.assertEqual(f.value, 42)
        self.assertEqual(f.type_id, GFFFieldType.STRREF)

    def test_tlk_lookup_for_gff_strref(self):
        """Simulates GFF STRREF → TLK lookup pipeline."""
        tlk = TLKFile()
        tlk.add("Item description for modding")

        # Simulate a GFF field with StrRef=0
        strref = 0
        text = tlk.get_text(strref)
        self.assertEqual(text, "Item description for modding")

    def test_tlk_invalid_strref_returns_none(self):
        """StrRef 0xFFFFFFFF should always return None."""
        tlk = TLKFile()
        tlk.add("Some text")
        self.assertIsNone(tlk.get_text(TLK_INVALID_STRREF))

    def test_gff_strref_round_trip_via_gff_writer(self):
        """Full GFF round-trip with a STRREF field."""
        from gmodular.formats.gff_types import GFFFieldType, GFFField, GFFStruct, GFFRoot
        from gmodular.formats.gff_writer import GFFWriter
        from gmodular.formats.gff_reader import GFFReader

        # GFFRoot IS the root struct (inherits from GFFStruct)
        root = GFFRoot(file_type="UTI ")
        root.fields["Description"] = GFFField(
            "Description", GFFFieldType.STRREF, 99
        )

        writer = GFFWriter(root)
        data = writer.to_bytes()

        reader = GFFReader(data)
        rt = reader.parse()
        desc_field = rt.fields.get("Description")
        self.assertIsNotNone(desc_field)
        self.assertEqual(desc_field.type_id, GFFFieldType.STRREF)
        self.assertEqual(desc_field.value, 99)


# ═════════════════════════════════════════════════════════════════════════════
#  8. ERF + TLK packaging
# ═════════════════════════════════════════════════════════════════════════════

class TestERFTLKPackaging(unittest.TestCase):
    """TLK files can be stored in ERF archives (rare but possible)."""

    def test_tlk_in_erf_round_trip(self):
        from gmodular.formats.archives import ERFWriter, ERFReaderMem

        tlk = TLKFile()
        tlk.add("Packed TLK")
        tlk_bytes = TLKWriter(tlk).to_bytes()

        writer = ERFWriter()
        writer.add_resource("dialog", "tlk", tlk_bytes)
        erf_bytes = writer.to_bytes()

        reader = ERFReaderMem(erf_bytes)
        result = reader.get_resource("dialog", "tlk")
        self.assertIsNotNone(result)

        rt = TLKReader.from_bytes(result)
        self.assertEqual(rt.get_text(0), "Packed TLK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
