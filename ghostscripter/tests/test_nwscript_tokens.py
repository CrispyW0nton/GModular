"""
GhostScripter — NWScript Tokenizer + Function Browser Tests
============================================================
Tests for:
  1. NWScriptTokenizer — all token types
  2. NWSCRIPT_STDLIB table — completeness / structure
  3. Category helpers — get_categories, get_functions_by_category, search_functions
  4. NWScriptHighlighter — import and interface (headless)
  5. FunctionBrowserPanel — import and interface (headless)
  6. main_window re-exports NWScriptHighlighter / FunctionBrowserPanel
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ──────────────────────────────────────────────────────────────────────────────
# 1. Tokenizer
# ──────────────────────────────────────────────────────────────────────────────

class TestNWScriptTokenizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ghostscripter.gui.nwscript_tokens import NWScriptTokenizer
        cls.tok = NWScriptTokenizer()

    def _token_types(self, source: str):
        return [t for t, *_ in self.tok.tokenize(source)]

    def _tokens_of(self, source: str, kind: str):
        return [text for t, s, e, text in self.tok.tokenize(source) if t == kind]

    def test_keyword_if(self):
        types = self._token_types("if (x) {}")
        self.assertIn("keyword", types)

    def test_keyword_while(self):
        self.assertIn("keyword", self._token_types("while (1) {}"))

    def test_type_int(self):
        self.assertIn("type", self._token_types("int x = 0;"))

    def test_type_void(self):
        self.assertIn("type", self._token_types("void main()"))

    def test_type_float(self):
        self.assertIn("type", self._token_types("float f = 1.0f;"))

    def test_type_string(self):
        self.assertIn("type", self._token_types('string s = "";'))

    def test_type_object(self):
        self.assertIn("type", self._token_types("object oPC = GetFirstPC();"))

    def test_entrypoint_main(self):
        toks = self._tokens_of("void main() {}", "entrypoint")
        self.assertIn("main", toks)

    def test_entrypoint_starting_conditional(self):
        toks = self._tokens_of("int StartingConditional() {}", "entrypoint")
        self.assertIn("StartingConditional", toks)

    def test_stdlib_get_first_pc(self):
        toks = self._tokens_of("GetFirstPC()", "stdlib")
        self.assertIn("GetFirstPC", toks)

    def test_stdlib_assign_command(self):
        toks = self._tokens_of("AssignCommand(oPC, ActionMoveToObject(oTarget));", "stdlib")
        self.assertIn("AssignCommand", toks)
        self.assertIn("ActionMoveToObject", toks)

    def test_stdlib_apply_effect(self):
        toks = self._tokens_of("ApplyEffectToObject(DURATION_TYPE_INSTANT, EffectDamage(10), oTarget);", "stdlib")
        self.assertIn("ApplyEffectToObject", toks)
        self.assertIn("EffectDamage", toks)

    def test_constant_object_self(self):
        toks = self._tokens_of("object o = OBJECT_SELF;", "constant")
        self.assertIn("OBJECT_SELF", toks)

    def test_constant_true_false(self):
        toks = self._tokens_of("int b = TRUE; int c = FALSE;", "constant")
        self.assertIn("TRUE", toks)
        self.assertIn("FALSE", toks)

    def test_number_int(self):
        toks = self._tokens_of("int x = 42;", "number")
        self.assertIn("42", toks)

    def test_number_float(self):
        toks = self._tokens_of("float f = 3.14;", "number")
        self.assertIn("3.14", toks)

    def test_string_literal(self):
        toks = self._tokens_of('string s = "hello world";', "string")
        self.assertTrue(any("hello" in t for t in toks))

    def test_line_comment(self):
        toks = self._tokens_of("// this is a comment\n", "comment")
        self.assertTrue(len(toks) > 0)

    def test_preproc_include(self):
        toks = self._tokens_of('#include "k_inc_utility"\n', "preproc")
        self.assertTrue(len(toks) > 0)

    def test_identifier_custom_func(self):
        toks = self._tokens_of("MyCustomFunc()", "identifier")
        self.assertIn("MyCustomFunc", toks)

    def test_empty_source(self):
        toks = self.tok.tokenize("")
        self.assertEqual(toks, [])

    def test_full_script_tokens_count(self):
        source = (
            "// KotOR script\n"
            "void main() {\n"
            "    object oPC = GetFirstPC();\n"
            "    if (GetIsPC(oPC)) {\n"
            "        SpeakString(\"Hello!\");\n"
            "    }\n"
            "}\n"
        )
        toks = self.tok.tokenize(source)
        self.assertGreater(len(toks), 10)


# ──────────────────────────────────────────────────────────────────────────────
# 2. NWSCRIPT_STDLIB table
# ──────────────────────────────────────────────────────────────────────────────

class TestNWScriptStdlib(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ghostscripter.gui.nwscript_tokens import NWSCRIPT_STDLIB
        cls.stdlib = NWSCRIPT_STDLIB

    def test_stdlib_not_empty(self):
        self.assertGreater(len(self.stdlib), 50)

    def test_stdlib_row_is_3_tuple(self):
        for row in self.stdlib:
            self.assertEqual(len(row), 3, f"Bad row: {row}")

    def test_stdlib_has_action_movetoobject(self):
        names = [r[0] for r in self.stdlib]
        self.assertIn("ActionMoveToObject", names)

    def test_stdlib_has_effect_damage(self):
        names = [r[0] for r in self.stdlib]
        self.assertIn("EffectDamage", names)

    def test_stdlib_has_speak_string(self):
        names = [r[0] for r in self.stdlib]
        self.assertIn("SpeakString", names)

    def test_stdlib_has_math_functions(self):
        names = [r[0] for r in self.stdlib]
        for fn in ("abs", "sqrt", "cos", "sin", "Random"):
            self.assertIn(fn, names, f"Missing math function: {fn}")

    def test_stdlib_has_string_functions(self):
        names = [r[0] for r in self.stdlib]
        for fn in ("GetStringLength", "IntToString", "StringToInt"):
            self.assertIn(fn, names)

    def test_stdlib_has_dice_functions(self):
        names = [r[0] for r in self.stdlib]
        for fn in ("d6", "d20", "d100"):
            self.assertIn(fn, names)

    def test_stdlib_has_global_functions(self):
        names = [r[0] for r in self.stdlib]
        for fn in ("GetGlobalBoolean", "SetGlobalNumber", "GetLocalNumber"):
            self.assertIn(fn, names)

    def test_stdlib_signatures_are_strings(self):
        for fn, sig, cat in self.stdlib:
            self.assertIsInstance(sig, str)

    def test_stdlib_categories_are_strings(self):
        for fn, sig, cat in self.stdlib:
            self.assertIsInstance(cat, str)
            self.assertGreater(len(cat), 0)

    def test_stdlib_no_duplicate_names(self):
        names = [r[0] for r in self.stdlib]
        self.assertEqual(len(names), len(set(names)))


# ──────────────────────────────────────────────────────────────────────────────
# 3. Category helpers
# ──────────────────────────────────────────────────────────────────────────────

class TestCategoryHelpers(unittest.TestCase):
    def test_get_categories_not_empty(self):
        from ghostscripter.gui.nwscript_tokens import get_categories
        cats = get_categories()
        self.assertGreater(len(cats), 3)

    def test_get_categories_sorted(self):
        from ghostscripter.gui.nwscript_tokens import get_categories
        cats = get_categories()
        self.assertEqual(cats, sorted(cats))

    def test_get_categories_has_action(self):
        from ghostscripter.gui.nwscript_tokens import get_categories
        self.assertIn("Action/AI", get_categories())

    def test_get_categories_has_effect(self):
        from ghostscripter.gui.nwscript_tokens import get_categories
        self.assertIn("Effect", get_categories())

    def test_get_functions_by_category_action(self):
        from ghostscripter.gui.nwscript_tokens import get_functions_by_category
        fns = get_functions_by_category("Action/AI")
        self.assertGreater(len(fns), 5)

    def test_get_functions_by_category_returns_tuples(self):
        from ghostscripter.gui.nwscript_tokens import get_functions_by_category
        for name, sig in get_functions_by_category("Math"):
            self.assertIsInstance(name, str)
            self.assertIsInstance(sig, str)

    def test_get_functions_by_category_empty_for_unknown(self):
        from ghostscripter.gui.nwscript_tokens import get_functions_by_category
        self.assertEqual(get_functions_by_category("NonExistentCategory"), [])

    def test_search_functions_returns_hits(self):
        from ghostscripter.gui.nwscript_tokens import search_functions
        results = search_functions("Effect")
        self.assertGreater(len(results), 3)

    def test_search_functions_case_insensitive(self):
        from ghostscripter.gui.nwscript_tokens import search_functions
        r1 = search_functions("effect")
        r2 = search_functions("Effect")
        self.assertEqual(len(r1), len(r2))

    def test_search_functions_empty_returns_all(self):
        from ghostscripter.gui.nwscript_tokens import search_functions, NWSCRIPT_STDLIB
        results = search_functions("")
        self.assertEqual(len(results), len(NWSCRIPT_STDLIB))

    def test_search_functions_specific(self):
        from ghostscripter.gui.nwscript_tokens import search_functions
        results = search_functions("GetFirstPC")
        names = [r[0] for r in results]
        self.assertIn("GetFirstPC", names)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Highlighter interface
# ──────────────────────────────────────────────────────────────────────────────

class TestNWScriptHighlighter(unittest.TestCase):
    def test_import(self):
        from ghostscripter.gui.nwscript_tokens import NWScriptHighlighter
        self.assertTrue(True)

    def test_has_qt_attribute(self):
        import ghostscripter.gui.nwscript_tokens as mod
        self.assertIn("_HAS_QT", dir(mod))

    def test_highlight_block_headless(self):
        import ghostscripter.gui.nwscript_tokens as mod
        if mod._HAS_QT:
            self.skipTest("Qt available")
        # Can't instantiate without QTextDocument, but we can check the class
        self.assertTrue(hasattr(mod.NWScriptHighlighter, "highlightBlock"))

    def test_tokenizer_is_separate(self):
        from ghostscripter.gui.nwscript_tokens import NWScriptTokenizer
        tok = NWScriptTokenizer()
        toks = tok.tokenize("int x = 1;")
        self.assertTrue(len(toks) > 0)


# ──────────────────────────────────────────────────────────────────────────────
# 5. FunctionBrowserPanel interface
# ──────────────────────────────────────────────────────────────────────────────

class TestFunctionBrowserPanel(unittest.TestCase):
    def test_import(self):
        from ghostscripter.gui.nwscript_tokens import FunctionBrowserPanel
        self.assertTrue(True)

    def test_has_get_selected_function(self):
        from ghostscripter.gui.nwscript_tokens import FunctionBrowserPanel
        self.assertTrue(hasattr(FunctionBrowserPanel, "get_selected_function"))

    def test_headless_get_selected_function(self):
        import ghostscripter.gui.nwscript_tokens as mod
        if mod._HAS_QT:
            self.skipTest("Qt available")
        panel = mod.FunctionBrowserPanel()
        result = panel.get_selected_function()
        self.assertIsNone(result)


# ──────────────────────────────────────────────────────────────────────────────
# 6. main_window re-exports
# ──────────────────────────────────────────────────────────────────────────────

class TestMainWindowReExports(unittest.TestCase):
    def test_main_window_imports_highlighter(self):
        import ghostscripter.gui.main_window as mw
        self.assertTrue(hasattr(mw, "NWScriptHighlighter"))

    def test_main_window_imports_function_browser(self):
        import ghostscripter.gui.main_window as mw
        self.assertTrue(hasattr(mw, "FunctionBrowserPanel"))

    def test_main_window_imports_tokenizer(self):
        import ghostscripter.gui.main_window as mw
        self.assertTrue(hasattr(mw, "NWScriptTokenizer"))

    def test_main_window_imports_stdlib(self):
        import ghostscripter.gui.main_window as mw
        self.assertTrue(hasattr(mw, "NWSCRIPT_STDLIB"))

    def test_stdlib_same_as_tokens_module(self):
        import ghostscripter.gui.main_window as mw
        from ghostscripter.gui.nwscript_tokens import NWSCRIPT_STDLIB
        self.assertEqual(mw.NWSCRIPT_STDLIB, NWSCRIPT_STDLIB)


if __name__ == "__main__":
    unittest.main()
