"""
GModular Portfolio Screenshots Generator
Creates 5 high-quality dark-theme code/architecture screenshots.
"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT_DIR = "/home/user/webapp/assets/screenshots"
os.makedirs(OUT_DIR, exist_ok=True)

# ── Theme ─────────────────────────────────────────────────────────────────────
BG       = (30, 30, 30)
PANEL    = (37, 37, 38)
ELEVATED = (45, 45, 45)
BORDER   = (62, 62, 66)
GUTTER_C = (26, 26, 26)
T_PRIM   = (212, 212, 212)
T_SEC    = (157, 157, 157)
T_DIM    = (110, 110, 110)
ACCENT   = (79, 195, 247)
BLUE_BAR = (0, 122, 204)
LINE_HL  = (42, 58, 74)
LINE_HL2 = (26, 45, 42)

# Syntax colours
KW    = (86,  156, 214)   # blue        keywords
TEAL  = (78,  201, 176)   # teal        class names, builtins
STR   = (206, 145, 120)   # orange      strings
NUM   = (181, 206, 168)   # green       numbers
CMT   = (106, 153,  85)   # green       comments
FUNC  = (220, 220, 170)   # yellow      function names
PARAM = (156, 220, 254)   # light blue  params/vars
PUNCT = (212, 212, 212)   # default     punctuation
DECO  = (197, 134, 192)   # purple      decorators
ACC   = (79,  195, 247)   # accent      headings
ORG   = (255, 180,  80)   # orange      special

W, H = 1400, 860

def lf(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def luf(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

CF   = lf(14)
CFB  = lf(14, True)
UFS  = luf(12)
UFM  = luf(13)
UFB  = luf(14, True)
UFLB = luf(16, True)

GW = 52   # gutter width
LH = 21   # line height

# ── Drawing helpers ───────────────────────────────────────────────────────────

def titlebar(d, title, sub=""):
    d.rectangle([0, 0, W, 36], fill=BLUE_BAR)
    d.text((16, 9), title, font=UFB, fill=(255, 255, 255))
    if sub:
        tw = int(d.textlength(title, font=UFB))
        d.text((16 + tw + 16, 10), sub, font=UFS, fill=(200, 224, 248))
    for i, c in enumerate([(255,95,87),(254,188,46),(40,200,64)]):
        cx = W - 58 + i * 20
        d.ellipse([cx-5, 13, cx+5, 23], fill=c)

def tab_row(d, labels, active=0, y0=36, y1=60):
    x = 0
    for i, lbl in enumerate(labels):
        w2 = int(d.textlength(lbl, font=UFS)) + 28
        bg = ELEVATED if i == active else PANEL
        d.rectangle([x, y0, x+w2, y1], fill=bg)
        tc = T_PRIM if i == active else T_DIM
        d.text((x+12, y0+5), lbl, font=UFS, fill=tc)
        if i == active:
            d.rectangle([x, y1-3, x+w2, y1], fill=ACCENT)
        x += w2 + 1

def statusbar(d, items):
    d.rectangle([0, H-26, W, H], fill=BLUE_BAR)
    x = 10
    for item in items:
        d.text((x, H-20), item, font=UFS, fill=(220, 235, 252))
        x += int(d.textlength(item, font=UFS)) + 24

def code_area(d, top, bottom=None):
    bottom = bottom or (H - 26)
    d.rectangle([0, top, W, bottom], fill=PANEL)
    d.rectangle([0, top, GW, bottom], fill=GUTTER_C)
    d.rectangle([GW, top, GW+1, bottom], fill=BORDER)

def gutter_numbers(d, start, count, top):
    for i in range(count):
        n = start + i
        ns = str(n)
        nw = int(d.textlength(ns, font=CF))
        d.text((GW - nw - 6, top + i * LH + 3), ns, font=CF, fill=T_DIM)

def hl_line(d, row, top, col=LINE_HL):
    y = top + row * LH
    d.rectangle([GW+1, y-1, W, y+LH-2], fill=col)

def code_line(d, row, top, tokens, x0=None):
    """tokens = list of (color_tuple, text) — color first, text second."""
    x = x0 if x0 is not None else (GW + 12)
    y = top + row * LH + 3
    for col, txt in tokens:
        d.text((x, y), txt, font=CF, fill=col)
        x += int(d.textlength(txt, font=CF))
    return x

def callout_box(d, x, y, w2, h2, title, lines, border_col, bg_col):
    d.rectangle([x, y, x+w2, y+h2], fill=bg_col)
    d.rectangle([x, y, x+w2, y+h2], outline=border_col, width=2)
    d.text((x+10, y+6), title, font=UFB, fill=border_col)
    for i, (txt, col) in enumerate(lines):
        d.text((x+10, y+26 + i*18), txt, font=UFS, fill=col)

def info_box(d, x, y, w2, h2, lines, border_col=(78,201,176), bg_col=(26,42,38)):
    d.rectangle([x, y, x+w2, y+h2], fill=bg_col)
    d.rectangle([x, y, x+w2, y+h2], outline=border_col, width=1)
    for i, (txt, col, fnt) in enumerate(lines):
        d.text((x+12, y+8+i*18), txt, font=fnt, fill=col)


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT 1 — GFF BFS Two-Phase Writer
# ═══════════════════════════════════════════════════════════════════════════════

def make_screenshot_1():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    titlebar(d, "GModular — gff_writer.py", "GFF V3.2 Binary Writer  ·  BFS Two-Phase Encoding Algorithm")
    tab_row(d, ["gff_writer.py", "gff_types.py", "gff_reader.py"], active=0)

    TOP = 61
    code_area(d, TOP)

    # Highlight Phase 1 block rows 7-12
    for r in range(7, 13):
        hl_line(d, r, TOP, LINE_HL)
    # Highlight Phase 2 header rows 14-15
    for r in range(14, 16):
        hl_line(d, r, TOP, LINE_HL2)

    lines = [
        # row, tokens
        (0,  [(CMT,   "    # ── Two-Phase BFS Encoding ──────────────────────────────────────────")]),
        (1,  [(KW,    "    def "), (FUNC,  "_build_all"), (PUNCT, "("), (KW, "self"),
              (PUNCT, ", "), (PARAM, "root"), (PUNCT, ": "), (STR, '"GFFStruct"'), (PUNCT, ")")]),
        (2,  [(PUNCT, '        """')]),
        (3,  [(CMT,   "        Two-phase GFF build:")]),
        (4,  [(CMT,   "        Phase 1 — BFS-collect all structs, assign stable indices.")]),
        (5,  [(CMT,   "        Phase 2 — Encode fields (can now safely reference struct indices).")]),
        (6,  [(PUNCT, '        """')]),
        (7,  [(CMT,   "        # ── Phase 1: assign struct indices via BFS ──────────────────────")]),
        (8,  [(PARAM, "        all_structs"), (PUNCT, "  = "), (KW, "self"), (PUNCT, "."),
              (FUNC, "_collect_structs_bfs"), (PUNCT, "("), (PARAM, "root"), (PUNCT, ")")]),
        (9,  [(PARAM, "        struct_idx_map"), (PUNCT, ": "), (TEAL, "Dict"), (PUNCT, " = {}"),
              (CMT,   "   # id(struct) -> stable integer index")]),
        (10, [(KW,    "        for"), (PUNCT, " i, s "), (KW, "in"), (PUNCT, " "),
              (TEAL, "enumerate"), (PUNCT, "("), (PARAM, "all_structs"), (PUNCT, "):")]),
        (11, [(PARAM, "            struct_idx_map"), (PUNCT, "["), (TEAL, "id"),
              (PUNCT, "("), (PARAM, "s"), (PUNCT, ")] = "), (PARAM, "i")]),
        (12, [(KW,    "            self"), (PUNCT, "._structs."), (FUNC, "append"),
              (PUNCT, "(("), (PARAM, "s.struct_id"), (PUNCT, ", "), (NUM, "0"),
              (PUNCT, ", "), (NUM, "0"), (PUNCT, "))"),
              (CMT, "   # placeholder entries")]),
        (13, [(PUNCT, "")]),
        (14, [(CMT,   "        # ── Phase 2: encode all fields in BFS order ─────────────────────")]),
        (15, [(KW,    "        def "), (FUNC, "encode_field"),
              (PUNCT, "("), (PARAM, "field"), (PUNCT, ": "), (TEAL, "GFFField"),
              (PUNCT, ") -> "), (TEAL, "int"), (PUNCT, ":")]),
        (16, [(PARAM, "            ft"),    (PUNCT, "    = "), (PARAM, "field.type_id")]),
        (17, [(PARAM, "            lidx"),  (PUNCT, "  = "), (KW, "self"), (PUNCT, "."),
              (FUNC, "_intern_label"), (PUNCT, "("), (PARAM, "field.label"), (PUNCT, ")")]),
        (18, [(KW,    "            if"),    (PUNCT, "   ft == "), (TEAL, "GFFFieldType"),
              (PUNCT, "."), (PARAM, "STRUCT"), (PUNCT, ":")]),
        (19, [(PARAM, "                sub_idx"), (PUNCT, " = "), (PARAM, "struct_idx_map"),
              (PUNCT, "."), (FUNC, "get"), (PUNCT, "("), (TEAL, "id"),
              (PUNCT, "("), (PARAM, "value"), (PUNCT, "), "), (NUM, "0"), (PUNCT, ")")]),
        (20, [(KW,    "                self"), (PUNCT, "._fields."), (FUNC, "append"),
              (PUNCT, "(("), (PARAM, "ft"), (PUNCT, ", "), (PARAM, "lidx"),
              (PUNCT, ", "), (PARAM, "sub_idx"), (PUNCT, "))")]),
        (21, [(KW,    "            elif"), (PUNCT, " ft == "), (TEAL, "GFFFieldType"),
              (PUNCT, "."), (PARAM, "LIST"), (PUNCT, ":")]),
        (22, [(PARAM, "                list_off"), (PUNCT, " = "), (TEAL, "len"),
              (PUNCT, "("), (KW, "self"), (PUNCT, "._list_indices)")]),
        (23, [(KW,    "                self"), (PUNCT, "._list_indices."), (FUNC, "extend"),
              (PUNCT, "("), (TEAL, "struct"), (PUNCT, "."), (FUNC, "pack"),
              (PUNCT, "("), (STR, '"<I"'), (PUNCT, ", "), (TEAL, "len"),
              (PUNCT, "("), (PARAM, "items"), (PUNCT, ")))")]),
        (24, [(KW,    "                for"), (PUNCT, " sub_s "),
              (KW, "in"), (PUNCT, " "), (PARAM, "value"), (PUNCT, " or []:")]),
        (25, [(PARAM, "                    sub_idx"), (PUNCT, " = "),
              (PARAM, "struct_idx_map"), (PUNCT, "."), (FUNC, "get"),
              (PUNCT, "("), (TEAL, "id"), (PUNCT, "("), (PARAM, "sub_s"),
              (PUNCT, "), "), (NUM, "0"), (PUNCT, ")")]),
        (26, [(KW,    "                    self"), (PUNCT, "._list_indices."), (FUNC, "extend"),
              (PUNCT, "("), (TEAL, "struct"), (PUNCT, "."), (FUNC, "pack"),
              (PUNCT, "("), (STR, '"<I"'), (PUNCT, ", "), (PARAM, "sub_idx"), (PUNCT, "))")]),
        (27, [(KW,    "                self"), (PUNCT, "._fields."), (FUNC, "append"),
              (PUNCT, "(("), (PARAM, "ft"), (PUNCT, ", "), (PARAM, "lidx"),
              (PUNCT, ", "), (PARAM, "list_off"), (PUNCT, "))")]),
        (28, [(PUNCT, "            ..."),
              (CMT,   "  # + 14 more field types: FLOAT, INT, STRING, VECTOR, ORIENTATION…")]),
        (29, [(KW,    "            return"), (PUNCT, " fi")]),
        (30, [(PUNCT, "")]),
        (31, [(CMT,   "        # Encode fields for every struct in BFS order")]),
        (32, [(KW,    "        for"), (PUNCT, " s "), (KW, "in"), (PUNCT, " "), (PARAM, "all_structs"), (PUNCT, ":")]),
        (33, [(PARAM, "            field_indices"), (PUNCT, " = ["), (FUNC, "encode_field"),
              (PUNCT, "("), (PARAM, "f"), (PUNCT, ") "), (KW, "for"), (PUNCT, " f "),
              (KW, "in"), (PUNCT, " "), (PARAM, "s.fields.values"), (PUNCT, "()]()")]),
        (34, [(PARAM, "            self._structs"), (PUNCT, "["),
              (PARAM, "struct_idx_map"), (PUNCT, "["), (TEAL, "id"), (PUNCT, "("), (PARAM, "s"),
              (PUNCT, ")]] = ("), (PARAM, "s.struct_id"), (PUNCT, ", "),
              (PARAM, "sdata"), (PUNCT, ", "), (PARAM, "field_count"), (PUNCT, ")")]),
    ]

    for row, toks in lines:
        code_line(d, row, TOP, toks)

    gutter_numbers(d, 111, 35, TOP)

    # Callout: Phase 1
    callout_box(d, 1060, TOP + 7*LH, 318, 108,
        "PHASE 1 — BFS Struct Collection",
        [("BFS walk assigns each GFFStruct a", T_PRIM),
         ("stable integer index before any", T_PRIM),
         ("field data is written.", T_PRIM),
         ("", T_DIM),
         ("Mirrors BioWare's GFF3Writer.", T_SEC)],
        ACCENT, (26, 40, 58))

    # Callout: Phase 2
    callout_box(d, 1060, TOP + 14*LH, 318, 108,
        "PHASE 2 — Field Encoding",
        [("Fields encoded in BFS order. STRUCT", T_PRIM),
         ("fields embed pre-assigned indices.", T_PRIM),
         ("LIST fields store count + index array.", T_PRIM),
         ("", T_DIM),
         ("Guarantees correct binary layout.", T_SEC)],
        TEAL, (26, 42, 38))

    # Bottom insight bar
    bx, by = GW+12, TOP + 35*LH - 4
    d.rectangle([bx, by, bx+1000, by+56], fill=(24,36,24), outline=TEAL, width=1)
    d.text((bx+12, by+7),  "KEY INSIGHT", font=UFB, fill=TEAL)
    d.text((bx+130, by+7),
           "Standard recursive GFF encoding fails — STRUCT/LIST fields must embed indices that haven't been assigned yet.",
           font=UFS, fill=T_PRIM)
    d.text((bx+12, by+28), "Solution:",
           font=UFB, fill=ACCENT)
    d.text((bx+90, by+28),
           "BFS Phase 1 collects and numbers every struct; Phase 2 encodes fields in the same BFS order with stable refs.",
           font=UFS, fill=T_SEC)
    d.text((bx+12, by+44),
           "Result: 44/44 GFF round-trip tests passing. Binary output is byte-identical to KotOR engine expectations.",
           font=UFS, fill=TEAL)

    statusbar(d, [
        "gmodular/formats/gff_writer.py",
        "GFF V3.2 Binary Format",
        "✓  44 / 44 tests passing",
        "Python 3.12  ·  GModular v1.0"
    ])
    path = f"{OUT_DIR}/01_gff_bfs_writer.png"
    img.save(path)
    print(f"  ✓  Screenshot 1 saved → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT 2 — Command Pattern / Undo-Redo (module_state.py)
# ═══════════════════════════════════════════════════════════════════════════════

def make_screenshot_2():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    titlebar(d, "GModular — module_state.py", "Command Pattern  ·  Undo / Redo Stack  ·  Observer Callbacks")
    tab_row(d, ["module_state.py", "gff_types.py", "main_window.py"], active=0)

    TOP = 61
    code_area(d, TOP)

    # Highlight Command base class
    for r in range(1, 4):
        hl_line(d, r, TOP, LINE_HL)
    # Highlight execute()
    for r in range(18, 21):
        hl_line(d, r, TOP, LINE_HL2)
    # Highlight undo()
    for r in range(27, 30):
        hl_line(d, r, TOP, LINE_HL2)

    lines = [
        (0,  [(CMT,   "# ── Command Pattern (Undo / Redo) ──────────────────────────────────────────")]),
        (1,  [(KW,    "class "), (TEAL, "Command"), (PUNCT, ":")]),
        (2,  [(PUNCT, '    """'), (CMT, "Base class for all undoable actions in GModular."), (PUNCT, '"""')]),
        (3,  [(PARAM, "    description"), (PUNCT, ": "), (TEAL, "str"), (PUNCT, ' = '), (STR, '"Action"')]),
        (4,  [(KW,    "    def "), (FUNC, "execute"), (PUNCT, "("), (KW, "self"), (PUNCT, "): "), (KW, "pass")]),
        (5,  [(KW,    "    def "), (FUNC, "undo"),    (PUNCT, "("), (KW, "self"), (PUNCT, "): "), (KW, "pass")]),
        (6,  [(PUNCT, "")]),
        (7,  [(KW,    "class "), (TEAL, "PlaceObjectCommand"), (PUNCT, "("), (TEAL, "Command"), (PUNCT, "):")]),
        (8,  [(PUNCT, '    """'), (CMT, "Generic place — works for Placeables, Creatures, Doors, Waypoints…"), (PUNCT, '"""')]),
        (9,  [(KW,    "    def "), (FUNC, "__init__"), (PUNCT, "("), (KW, "self"), (PUNCT, ", "),
              (PARAM, "git"), (PUNCT, ": "), (TEAL, "GITData"), (PUNCT, ", "),
              (PARAM, "obj"), (PUNCT, "):")]),
        (10, [(PARAM, "        self.git"), (PUNCT, " = "), (PARAM, "git")]),
        (11, [(PARAM, "        self.obj"), (PUNCT, " = "), (PARAM, "obj")]),
        (12, [(PARAM, "        self.description"), (PUNCT, " = "),
              (STR,   'f"Place {'), (FUNC, "_obj_type_label"), (PUNCT, "("), (PARAM, "obj"),
              (STR, ")} '{"), (PARAM, "obj.resref"), (STR, "}'\"")])  ,
        (13, [(KW,    "    def "), (FUNC, "execute"), (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (14, [(KW,    "        self"), (PUNCT, ".git."), (FUNC, "add_object"), (PUNCT, "("), (KW, "self"), (PUNCT, ".obj)")]),
        (15, [(KW,    "    def "), (FUNC, "undo"),    (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (16, [(KW,    "        self"), (PUNCT, ".git."), (FUNC, "remove_object"), (PUNCT, "("), (KW, "self"), (PUNCT, ".obj)")]),
        (17, [(PUNCT, "")]),
        (18, [(KW,    "class "), (TEAL, "ModuleState"), (PUNCT, ":")]),
        (19, [(PARAM, "    UNDO_LIMIT"), (PUNCT, " = "), (NUM, "100")]),
        (20, [(PARAM, "    AUTOSAVE_INTERVAL_S"), (PUNCT, " = "), (NUM, "120"),
              (CMT, "  # 2-minute rolling autosave")]),
        (21, [(PUNCT, "")]),
        (22, [(KW,    "    def "), (FUNC, "execute"), (PUNCT, "("),
              (KW, "self"), (PUNCT, ", "), (PARAM, "cmd"), (PUNCT, ": "), (TEAL, "Command"), (PUNCT, "):")]),
        (23, [(PUNCT, '        """'), (CMT, "Execute a command and push it onto the undo stack."), (PUNCT, '"""')]),
        (24, [(PARAM, "        cmd"), (PUNCT, "."), (FUNC, "execute"), (PUNCT, "()")]),
        (25, [(KW,    "        self"), (PUNCT, "._undo_stack."), (FUNC, "append"), (PUNCT, "("), (PARAM, "cmd"), (PUNCT, ")")]),
        (26, [(KW,    "        self"), (PUNCT, "._redo_stack."), (FUNC, "clear"), (PUNCT, "()")]),
        (27, [(KW,    "        if"), (PUNCT, " "), (TEAL, "len"), (PUNCT, "("),
              (KW, "self"), (PUNCT, "._undo_stack) > "), (PARAM, "self.UNDO_LIMIT"), (PUNCT, ":")]),
        (28, [(KW,    "            self"), (PUNCT, "._undo_stack."), (FUNC, "pop"),
              (PUNCT, "("), (NUM, "0"), (PUNCT, ")"),
              (CMT, "   # drop oldest beyond limit")]),
        (29, [(KW,    "        self"), (PUNCT, "._dirty = "), (KW, "True")]),
        (30, [(KW,    "        self"), (PUNCT, "."), (FUNC, "_emit_change"), (PUNCT, "()")]),
        (31, [(PUNCT, "")]),
        (32, [(KW,    "    def "), (FUNC, "undo"), (PUNCT, "("),
              (KW, "self"), (PUNCT, ") -> "), (TEAL, "Optional"), (PUNCT, "["), (TEAL, "str"), (PUNCT, "]:")]),
        (33, [(KW,    "        if not self"), (PUNCT, "._undo_stack: "), (KW, "return"), (PUNCT, " "), (KW, "None")]),
        (34, [(PARAM, "        cmd"), (PUNCT, " = "), (KW, "self"), (PUNCT, "._undo_stack."), (FUNC, "pop"), (PUNCT, "()")]),
        (35, [(PARAM, "        cmd"), (PUNCT, "."), (FUNC, "undo"), (PUNCT, "()")]),
        (36, [(KW,    "        self"), (PUNCT, "._redo_stack."), (FUNC, "append"), (PUNCT, "("), (PARAM, "cmd"), (PUNCT, ")")]),
        (37, [(KW,    "        self"), (PUNCT, "._dirty = "), (KW, "True")]),
        (38, [(KW,    "        self"), (PUNCT, "."), (FUNC, "_emit_change"), (PUNCT, "()")]),
    ]

    for row, toks in lines:
        code_line(d, row, TOP, toks)

    gutter_numbers(d, 30, len(lines), TOP)

    # Right panel: Command hierarchy diagram
    px = 1060
    py = TOP + 4

    d.rectangle([px, py, px+318, py+310], fill=(30,30,34), outline=BORDER, width=1)
    d.rectangle([px, py, px+318, py+24], fill=ELEVATED)
    d.text((px+10, py+5), "Command Hierarchy", font=UFB, fill=ACCENT)

    cmd_items = [
        ("Command", TEAL, 0),
        ("  ├─  PlaceObjectCommand", PARAM, 1),
        ("  ├─  DeleteObjectCommand", PARAM, 1),
        ("  ├─  MoveObjectCommand", PARAM, 1),
        ("  ├─  RotateObjectCommand", PARAM, 1),
        ("  └─  ModifyPropertyCommand", PARAM, 1),
    ]
    for i, (txt, col, ind) in enumerate(cmd_items):
        d.text((px+12+ind*8, py+32+i*22), txt, font=CF, fill=col)

    d.rectangle([px+6, py+166, px+312, py+167], fill=BORDER)
    d.text((px+10, py+174), "Undo Stack  (LIFO, limit 100)", font=UFB, fill=FUNC)

    stack_items = [
        "PlaceObjectCommand  'chair001'",
        "ModifyPropertyCommand  'bearing'",
        "DeleteObjectCommand  'DOOR_1'",
        "MoveObjectCommand  'jedi001'",
        "← top (most recent)",
    ]
    for i, txt in enumerate(stack_items):
        col = ACCENT if i == len(stack_items)-1 else T_SEC
        d.text((px+12, py+196+i*19), txt, font=UFS, fill=col)

    d.rectangle([px+6, py+296, px+312, py+310], fill=(40,60,40), outline=TEAL, width=1)
    d.text((px+10, py+299), "Observer: _emit_change() → viewport._on_module_changed()", font=UFS, fill=TEAL)

    # Autosave annotation
    callout_box(d, 1060, TOP + 17*LH, 318, 90,
        "Autosave  (2-minute interval)",
        [("threading.Timer fires every 120s.", T_PRIM),
         ("Only writes if module is dirty.", T_PRIM),
         ("Saves to .gmodular/autosave/", T_PRIM),
         ("Restarts timer after each tick.", T_SEC)],
        ORG, (40, 30, 20))

    statusbar(d, [
        "gmodular/core/module_state.py",
        "Command Pattern  ·  Undo/Redo  ·  Observer",
        "5 Command classes",
        "Python 3.12  ·  GModular v1.0"
    ])
    path = f"{OUT_DIR}/02_command_pattern.png"
    img.save(path)
    print(f"  ✓  Screenshot 2 saved → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT 3 — 3D Viewport: GLSL Shaders + OrbitCamera
# ═══════════════════════════════════════════════════════════════════════════════

def make_screenshot_3():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    titlebar(d, "GModular — viewport.py", "ModernGL 3D Viewport  ·  GLSL Shaders  ·  Orbit Camera  ·  Ray-Cast Picking")
    tab_row(d, ["viewport.py", "main_window.py", "player_controller.py"], active=0)

    TOP = 61
    code_area(d, TOP)

    # Highlight GLSL blocks
    for r in range(2, 9):
        hl_line(d, r, TOP, (40, 30, 22))
    for r in range(12, 20):
        hl_line(d, r, TOP, (30, 22, 40))
    for r in range(22, 29):
        hl_line(d, r, TOP, (22, 38, 28))

    lines = [
        (0,  [(CMT,   "# ── GLSL Shaders (embedded — no .glsl files needed) ─────────────────────")]),
        (1,  [(PARAM, "_VERT_SHADER"), (PUNCT, " = "), (STR, '"""')]),
        (2,  [(STR,   "#version 330 core")]),
        (3,  [(STR,   "in vec3 in_position;")]),
        (4,  [(STR,   "in vec3 in_color;")]),
        (5,  [(STR,   "out vec3 v_color;")]),
        (6,  [(STR,   "uniform mat4 mvp;")]),
        (7,  [(STR,   "void main() {")]),
        (8,  [(STR,   "    gl_Position = mvp * vec4(in_position, 1.0);  v_color = in_color;")]),
        (9,  [(STR,   '} """')]),
        (10, [(PUNCT, "")]),
        (11, [(PARAM, "_VERT_MESH_SHADER"), (PUNCT, " = "), (STR, '"""')]),
        (12, [(STR,   "#version 330 core")]),
        (13, [(STR,   "in vec3 in_position;  in vec3 in_normal;")]),
        (14, [(STR,   "out vec3 v_normal;  out vec3 v_world_pos;")]),
        (15, [(STR,   "uniform mat4 mvp;  uniform mat4 model;")]),
        (16, [(STR,   "void main() {")]),
        (17, [(STR,   "    vec4 world = model * vec4(in_position, 1.0);")]),
        (18, [(STR,   "    v_normal = normalize(mat3(model) * in_normal);")]),
        (19, [(STR,   '    gl_Position = mvp * vec4(in_position, 1.0); } """')]),
        (20, [(PUNCT, "")]),
        (21, [(PARAM, "_FRAG_MESH_SHADER"), (PUNCT, " = "), (STR, '"""')]),
        (22, [(STR,   "#version 330 core")]),
        (23, [(STR,   "in vec3 v_normal;  in vec3 v_world_pos;")]),
        (24, [(STR,   "uniform vec3 diffuse_color;  uniform vec3 light_dir;  uniform float ambient;")]),
        (25, [(STR,   "out vec4 fragColor;")]),
        (26, [(STR,   "void main() {")]),
        (27, [(STR,   "    float diff = max(dot(normalize(v_normal), normalize(light_dir)), 0.0);")]),
        (28, [(STR,   '    fragColor = vec4(diffuse_color * (ambient + diff * (1.0 - ambient)), 1.0); } """')]),
        (29, [(PUNCT, "")]),
        (30, [(CMT,   "# ── OrbitCamera — Maya-style, Z-up right-handed (matches KotOR) ─────────")]),
        (31, [(KW,    "class "), (TEAL, "OrbitCamera"), (PUNCT, ":")]),
        (32, [(KW,    "    def "), (FUNC, "eye"), (PUNCT, "("), (KW, "self"), (PUNCT, ") -> "), (TEAL, "np.ndarray"), (PUNCT, ":")]),
        (33, [(PARAM, "        az"), (PUNCT, " = "), (TEAL, "math"), (PUNCT, "."), (FUNC, "radians"),
              (PUNCT, "("), (KW, "self"), (PUNCT, ".azimuth);  "),
              (PARAM, "el"), (PUNCT, " = "), (TEAL, "math"), (PUNCT, "."), (FUNC, "radians"),
              (PUNCT, "("), (KW, "self"), (PUNCT, ".elevation)")]),
        (34, [(KW,    "        return"), (PUNCT, " self.target + np.array([")]),
        (35, [(PARAM, "            self.distance"), (PUNCT, " * math.cos(el) * math.cos(az),")]),
        (36, [(PARAM, "            self.distance"), (PUNCT, " * math.cos(el) * math.sin(az),")]),
        (37, [(PARAM, "            self.distance"), (PUNCT, " * math.sin(el)  ], dtype="),
              (STR, "'f4'"), (PUNCT, ")")]),
        (38, [(PUNCT, "")]),
        (39, [(KW,    "    def "), (FUNC, "ray_from_screen"),
              (PUNCT, "("), (KW, "self"), (PUNCT, ", "),
              (PARAM, "sx"), (PUNCT, ": "), (TEAL, "int"), (PUNCT, ", "),
              (PARAM, "sy"), (PUNCT, ": "), (TEAL, "int"), (PUNCT, ", "),
              (PARAM, "W"), (PUNCT, ", "), (PARAM, "H"),
              (PUNCT, ") -> "), (TEAL, "Tuple"), (PUNCT, "[...]:"),
              (CMT, "   # NDC → view-space → world-space")]),
        (40, [(PARAM, "        nx"), (PUNCT, " = ("), (NUM, "2.0"), (PUNCT, " * "), (PARAM, "sx"), (PUNCT, " / W) - "),
              (NUM, "1.0"), (PUNCT, ";  "),
              (PARAM, "ny"), (PUNCT, " = "), (NUM, "1.0"), (PUNCT, " - ("), (NUM, "2.0"),
              (PUNCT, " * "), (PARAM, "sy"), (PUNCT, " / H)")]),
    ]

    for row, toks in lines:
        code_line(d, row, TOP, toks)

    gutter_numbers(d, 233, len(lines), TOP)

    # Right panel: shader + color legend
    px = 1055
    py = TOP + 4

    # Vertex / Fragment split legend
    d.rectangle([px, py, px+323, py+80], fill=(40,30,20), outline=ORG, width=1)
    d.text((px+10, py+6),   "Vertex Shader",   font=UFB,  fill=ORG)
    d.text((px+10, py+24),  "→ Transforms positions: MVP × vec4(pos, 1.0)", font=UFS, fill=T_PRIM)
    d.text((px+10, py+40),  "→ Passes world-space normal to fragment shader", font=UFS, fill=T_PRIM)
    d.text((px+10, py+56),  "→ Two variants: flat-color  |  lit mesh (Phong)", font=UFS, fill=T_SEC)

    d.rectangle([px, py+88, px+323, py+168], fill=(30,20,40), outline=DECO, width=1)
    d.text((px+10, py+94),  "Fragment Shader", font=UFB,  fill=DECO)
    d.text((px+10, py+112), "→ Diffuse lighting: Lambert dot-product term", font=UFS, fill=T_PRIM)
    d.text((px+10, py+128), "→ Ambient + diffuse blended by uniform float", font=UFS, fill=T_PRIM)
    d.text((px+10, py+144), "→ Per-object color passed as uniform vec3", font=UFS, fill=T_SEC)

    # Object color swatch legend
    swatches = [
        ("Placeable",  (51,  153, 255)),
        ("Creature",   (255, 102,  51)),
        ("Door",       (204, 178,  26)),
        ("Waypoint",   (204,  51, 204)),
        ("Trigger",    ( 51, 255, 128)),
        ("Sound",      ( 51, 230, 230)),
        ("Store",      ( 51, 230,  77)),
        ("Selected",   (255, 255,   0)),
    ]
    d.rectangle([px, py+176, px+323, py+176+len(swatches)*22+18], fill=ELEVATED, outline=BORDER, width=1)
    d.text((px+10, py+182), "Viewport Object Color Coding", font=UFB, fill=ACCENT)
    for i, (lbl, col) in enumerate(swatches):
        y2 = py + 202 + i*22
        d.rectangle([px+12, y2+3, px+30, y2+17], fill=col)
        d.text((px+38, y2+2), lbl, font=UFS, fill=T_PRIM)

    # Camera controls legend
    d.rectangle([px, py+380, px+323, py+490], fill=(26,30,36), outline=ACCENT, width=1)
    d.text((px+10, py+386), "Orbit Camera Controls", font=UFB, fill=ACCENT)
    ctrl = [
        ("RMB drag",     "Orbit (azimuth / elevation)"),
        ("MMB drag",     "Pan (screen-space, Z-safe)"),
        ("Scroll wheel", "Zoom (distance × 0.9^delta)"),
        ("W A S D",      "WASD fly in editor mode"),
        ("F key",        "Frame all GIT objects"),
        ("LMB + gizmo",  "Translate / rotate selected"),
    ]
    for i, (k, v) in enumerate(ctrl):
        d.text((px+12, py+406+i*14), k, font=UFS, fill=FUNC)
        d.text((px+112, py+406+i*14), v, font=UFS, fill=T_SEC)

    statusbar(d, [
        "gmodular/gui/viewport.py",
        "ModernGL  ·  GLSL 330 Core  ·  PyQt5",
        "Orbit + Play mode",
        "Python 3.12  ·  GModular v1.0"
    ])
    path = f"{OUT_DIR}/03_viewport_shaders.png"
    img.save(path)
    print(f"  ✓  Screenshot 3 saved → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT 4 — Test Suite (test_gff.py)
# ═══════════════════════════════════════════════════════════════════════════════

def make_screenshot_4():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    titlebar(d, "GModular — test_gff.py", "GFF V3.2 Round-Trip Test Suite  ·  pytest  ·  44 / 44 Passing")
    tab_row(d, ["test_gff.py", "gff_writer.py", "gff_reader.py"], active=0)

    TOP = 61
    code_area(d, TOP)

    for r in [7, 8, 9, 10]:
        hl_line(d, r, TOP, LINE_HL)
    for r in [19, 20, 21, 22]:
        hl_line(d, r, TOP, LINE_HL2)

    lines = [
        (0,  [(CMT,   "# ── GFF V3.2 Round-Trip Tests — GModular test suite (44 tests / 100% pass) ──")]),
        (1,  [(KW,    "def "), (FUNC, "_write_read"), (PUNCT, "("), (PARAM, "root"),
              (PUNCT, ": "), (TEAL, "GFFRoot"), (PUNCT, ") -> "), (TEAL, "GFFRoot"), (PUNCT, ":")]),
        (2,  [(PUNCT, '    """'), (CMT, "Round-trip: GFFRoot → writer → bytes → reader → GFFRoot"), (PUNCT, '"""')]),
        (3,  [(PARAM, "    data"), (PUNCT, "   = "), (TEAL, "GFFWriter"), (PUNCT, "("), (PARAM, "root"),
              (PUNCT, ")."), (FUNC, "to_bytes"), (PUNCT, "()")]),
        (4,  [(PARAM, "    reader"), (PUNCT, " = "), (TEAL, "GFFReader"), (PUNCT, "."),
              (FUNC, "from_bytes"), (PUNCT, "("), (PARAM, "data"), (PUNCT, ")")]),
        (5,  [(KW,    "    return"), (PUNCT, " "), (PARAM, "reader"), (PUNCT, "."), (FUNC, "parse"), (PUNCT, "()")]),
        (6,  [(PUNCT, "")]),
        (7,  [(KW,    "class "), (TEAL, "TestScalarFields"), (PUNCT, ":")]),
        (8,  [(KW,    "    def "), (FUNC, "test_byte"), (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (9,  [(PARAM, "        root"), (PUNCT, " = "), (FUNC, "_make_simple_root"),
              (PUNCT, "(Val=("), (TEAL, "GFFFieldType"), (PUNCT, "."), (PARAM, "BYTE"),
              (PUNCT, ", "), (NUM, "200"), (PUNCT, "))")]),
        (10, [(PARAM, "        r2"), (PUNCT, "   = "), (FUNC, "_write_read"), (PUNCT, "("), (PARAM, "root"), (PUNCT, ")")]),
        (11, [(KW,    "        assert"), (PARAM, " r2.fields["), (STR, '"Val"'),
              (PARAM, "].value"), (PUNCT, " == "), (NUM, "200")]),
        (12, [(PUNCT, "")]),
        (13, [(KW,    "    def "), (FUNC, "test_dword"), (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (14, [(PARAM, "        root"), (PUNCT, " = "), (FUNC, "_make_simple_root"),
              (PUNCT, "(Val=("), (TEAL, "GFFFieldType"), (PUNCT, "."), (PARAM, "DWORD"),
              (PUNCT, ", "), (NUM, "0xDEADBEEF"), (PUNCT, "))")]),
        (15, [(PARAM, "        r2"), (PUNCT, "   = "), (FUNC, "_write_read"), (PUNCT, "("), (PARAM, "root"), (PUNCT, ")")]),
        (16, [(KW,    "        assert"), (PARAM, " r2.fields["), (STR, '"Val"'),
              (PARAM, "].value"), (PUNCT, " == "), (NUM, "0xDEADBEEF")]),
        (17, [(PUNCT, "")]),
        (18, [(KW,    "class "), (TEAL, "TestGITRoundTrip"), (PUNCT, ":")]),
        (19, [(KW,    "    def "), (FUNC, "test_creature_fields"), (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (20, [(PARAM, "        git"), (PUNCT,  " = "), (FUNC, "_make_full_git"), (PUNCT, "()")]),
        (21, [(FUNC,  "        save_git"), (PUNCT, "("), (PARAM, "git"),
              (PUNCT, ", "), (KW, "self"), (PUNCT, ".tmp)")]),
        (22, [(PARAM, "        git2"), (PUNCT, " = "), (FUNC, "load_git"),
              (PUNCT, "("), (KW, "self"), (PUNCT, ".tmp)")]),
        (23, [(PARAM, "        c"), (PUNCT, " = "), (PARAM, "git2.creatures"), (PUNCT, "["), (NUM, "0"), (PUNCT, "]")]),
        (24, [(KW,    "        assert"), (PARAM, " c.tag"),     (PUNCT, " == "), (STR, '"JEDI_1"')]),
        (25, [(KW,    "        assert"), (PUNCT, " abs("), (PARAM, "c.bearing"), (PUNCT, " - "),
              (NUM, "1.5707963"), (PUNCT, ") < "), (NUM, "1e-5")]),
        (26, [(KW,    "        assert"), (PARAM, " c.on_spawn"), (PUNCT, " == "), (STR, '"on_spawn_sc"')]),
        (27, [(PUNCT, "")]),
        (28, [(KW,    "    def "), (FUNC, "test_trigger_geometry"), (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (29, [(PARAM, "        t"), (PUNCT, "    = "), (PARAM, "git2.triggers"), (PUNCT, "["), (NUM, "0"), (PUNCT, "]")]),
        (30, [(KW,    "        assert"), (PARAM, " t.tag"), (PUNCT, " == "), (STR, '"TRIG_1"')]),
        (31, [(KW,    "        assert"), (PUNCT, " "), (TEAL, "len"), (PUNCT, "("),
              (PARAM, "t.geometry"), (PUNCT, ") == "), (NUM, "4"),
              (CMT, "   # 4 vertices round-tripped correctly")]),
        (32, [(KW,    "        assert"), (PUNCT, " abs("), (PARAM, "t.geometry[1].x"),
              (PUNCT, " - "), (NUM, "1.0"), (PUNCT, ") < "), (NUM, "1e-5")]),
        (33, [(KW,    "        assert"), (PARAM, " t.on_enter"), (PUNCT, " == "), (STR, '"on_enter_sc"')]),
        (34, [(PUNCT, "")]),
        (35, [(KW,    "    def "), (FUNC, "test_idempotent_multiple_calls"), (PUNCT, "("), (KW, "self"), (PUNCT, "):")]),
        (36, [(PUNCT, '        """'), (CMT, "to_bytes() called twice must return identical output."), (PUNCT, '"""')]),
        (37, [(PARAM, "        b1"), (PUNCT, " = "), (PARAM, "writer"), (PUNCT, "."),
              (FUNC, "to_bytes"), (PUNCT, "();  "),
              (PARAM, "b2"), (PUNCT, " = "), (PARAM, "writer"), (PUNCT, "."),
              (FUNC, "to_bytes"), (PUNCT, "()")]),
        (38, [(KW,    "        assert"), (PUNCT, " b1 == b2")]),
    ]

    for row, toks in lines:
        code_line(d, row, TOP, toks)

    gutter_numbers(d, 1, len(lines), TOP)

    # Right panel: test results terminal
    px = 1060
    py = TOP + 4
    d.rectangle([px, py, px+318, py+400], fill=(20, 22, 20), outline=(60,80,60), width=1)
    d.rectangle([px, py, px+318, py+24], fill=(30, 40, 30))
    d.text((px+10, py+5), "$ python -m pytest tests/ -v", font=CF, fill=T_PRIM)

    results = [
        ("PASSED", "test_header_size", (60,200,90)),
        ("PASSED", "test_file_type_preserved", (60,200,90)),
        ("PASSED", "test_version_is_v32", (60,200,90)),
        ("PASSED", "test_empty_root_struct", (60,200,90)),
        ("PASSED", "test_byte", (60,200,90)),
        ("PASSED", "test_word", (60,200,90)),
        ("PASSED", "test_dword", (60,200,90)),
        ("PASSED", "test_float", (60,200,90)),
        ("PASSED", "test_double", (60,200,90)),
        ("PASSED", "test_dword64", (60,200,90)),
        ("PASSED", "test_strref", (60,200,90)),
        ("PASSED", "test_cexostring", (60,200,90)),
        ("PASSED", "test_resref_max_length", (60,200,90)),
        ("PASSED", "test_cexolocstring", (60,200,90)),
        ("PASSED", "test_vector3", (60,200,90)),
        ("PASSED", "test_orientation_quaternion", (60,200,90)),
        ("PASSED", "test_void_data", (60,200,90)),
        ("PASSED", "test_nested_struct", (60,200,90)),
        ("PASSED", "test_list_field_multiple", (60,200,90)),
        ("PASSED", "test_multiple_lists", (60,200,90)),
    ]
    for i, (status, name, col) in enumerate(results):
        y2 = py + 30 + i*17
        d.text((px+10, y2), status, font=UFS, fill=col)
        d.text((px+78, y2), name, font=UFS, fill=T_SEC)
    d.text((px+10, py+30+20*17), "... 24 more tests ...", font=UFS, fill=T_DIM)

    # Summary box
    d.rectangle([px+4, py+378, px+314, py+398], fill=(30,50,30), outline=(60,200,90), width=2)
    d.text((px+10, py+382), "44 passed in 0.14s", font=CFB, fill=(60,200,90))

    # Coverage table
    d.rectangle([px, py+406, px+318, py+560], fill=ELEVATED, outline=BORDER, width=1)
    d.text((px+10, py+412), "Test Coverage Areas", font=UFB, fill=ACCENT)
    areas = [
        ("GFF Header layout",     "✓ 4 tests"),
        ("All 18 scalar types",   "✓ 9 tests"),
        ("String fields",         "✓ 6 tests"),
        ("Composite / nested",    "✓ 7 tests"),
        ("GIT round-trips",       "✓ 9 tests"),
        ("GFFWriter API",         "✓ 3 tests"),
        ("GFFReader API",         "✓ 5 tests"),
        ("Ambient audio fields",  "✓ 1 test"),
    ]
    for i, (area, cnt) in enumerate(areas):
        d.text((px+12, py+432+i*16), area, font=UFS, fill=T_PRIM)
        d.text((px+220, py+432+i*16), cnt, font=UFS, fill=TEAL)

    statusbar(d, [
        "tests/test_gff.py",
        "pytest  ·  GFF V3.2 Format",
        "✓  44 / 44 tests  ·  0.14s",
        "Python 3.12  ·  GModular v1.0"
    ])
    path = f"{OUT_DIR}/04_test_suite.png"
    img.save(path)
    print(f"  ✓  Screenshot 4 saved → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT 5 — Ghostworks Pipeline Architecture Diagram
# ═══════════════════════════════════════════════════════════════════════════════

def make_screenshot_5():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    titlebar(d, "GModular — GHOSTWORKS_BLUEPRINT.md",
             "Three-Program Suite Architecture  ·  IPC Contract  ·  Shared Format Layer")

    TOP = 36

    # ── Header ────────────────────────────────────────────────────────────────
    d.rectangle([0, TOP, W, TOP+48], fill=ELEVATED)
    d.rectangle([0, TOP+46, W, TOP+48], fill=BORDER)
    d.text((20, TOP+10), "Ghostworks Pipeline", font=UFLB, fill=ACCENT)
    d.text((260, TOP+12), "— Replacing 6+ legacy KotOR modding tools with one integrated suite", font=UFM, fill=T_SEC)
    d.text((20, TOP+30), "Goal: 4–6 hour NPC workflow  →  under 10 minutes across 3 connected tools", font=UFS, fill=T_SEC)

    # ── Three program boxes ────────────────────────────────────────────────────
    BOX_TOP = TOP + 56
    BOX_H   = 210
    bw = 370

    boxes = [
        {
            "x": 30, "title": "GhostRigger", "subtitle": "KotOR Asset Editor",
            "port": "IPC  :  Port 7001",
            "col": (86,156,214),
            "items": [
                "Blueprint editors  (UTC / UTP / UTD)",
                "3D MDL viewer  (ModernGL)",
                "Animation timeline  (keyframe scrubber)",
                "UV editor  +  lightmap baker",
                "Archive browser  (.mod / .rim / .erf)",
                "GFF reader / writer  (shared layer)",
            ]
        },
        {
            "x": 430, "title": "GhostScripter", "subtitle": "Script + Logic IDE",
            "port": "IPC  :  Port 7002",
            "col": (197,134,192),
            "items": [
                "NWScript IDE  (syntax highlight, autocomplete)",
                "Dialog tree editor  (QGraphicsView nodes)",
                "NWScript compiler  (.nss → .ncs)",
                "2DA spreadsheet editor",
                "TLK string table browser",
                "GFF reader / writer  (shared layer)",
            ]
        },
        {
            "x": 830, "title": "GModular", "subtitle": "KotOR Level Designer",
            "port": "IPC  :  Port 7003",
            "col": (78,201,176),
            "items": [
                "3D Viewport  (ModernGL, orbit + play mode)",
                "Scene Outline + Inspector Panel",
                "Asset Palette  +  Room Assembly Grid",
                "Walkmesh editor  (WOK parser)",
                "Module Packager  (ERF / MOD export)",
                "GFF reader / writer  (canonical impl)",
            ]
        },
    ]

    for b in boxes:
        x, col = b["x"], b["col"]
        d.rectangle([x, BOX_TOP, x+bw, BOX_TOP+BOX_H], fill=(28,30,32), outline=col, width=2)
        d.rectangle([x, BOX_TOP, x+bw, BOX_TOP+34], fill=tuple(max(0,c-60) for c in col))
        d.text((x+12, BOX_TOP+6), b["title"], font=UFB, fill=col)
        tw = int(d.textlength(b["title"], font=UFB))
        d.text((x+12+tw+10, BOX_TOP+9), b["subtitle"], font=UFS, fill=T_SEC)
        d.text((x+bw-130, BOX_TOP+9), b["port"], font=UFS, fill=T_DIM)
        for i, item in enumerate(b["items"]):
            d.text((x+14, BOX_TOP+40+i*27), "▸", font=UFS, fill=col)
            d.text((x+28, BOX_TOP+40+i*27), item, font=UFS, fill=T_PRIM)

    # ── IPC arrows ────────────────────────────────────────────────────────────
    AY = BOX_TOP + BOX_H//2
    # GhostRigger → GhostScripter
    ax1, ax2 = 30+bw, 430
    d.line([ax1, AY, ax2, AY], fill=(86,156,214), width=3)
    d.polygon([(ax2,AY-6),(ax2,AY+6),(ax2+12,AY)], fill=(86,156,214))
    d.text(((ax1+ax2)//2-14, AY-16), "IPC", font=UFS, fill=T_DIM)
    # GhostScripter → GModular
    ax3, ax4 = 430+bw, 830
    d.line([ax3, AY, ax4, AY], fill=(197,134,192), width=3)
    d.polygon([(ax4,AY-6),(ax4,AY+6),(ax4+12,AY)], fill=(197,134,192))
    d.text(((ax3+ax4)//2-14, AY-16), "IPC", font=UFS, fill=T_DIM)

    # ── Shared Format Layer ────────────────────────────────────────────────────
    FMT_TOP = BOX_TOP + BOX_H + 20
    d.rectangle([30, FMT_TOP, W-30, FMT_TOP+72], fill=(26,30,26), outline=TEAL, width=2)
    d.text((50, FMT_TOP+6),  "SHARED FORMAT LAYER  —  gmodular/formats/", font=UFB, fill=TEAL)
    fmt_items = [
        ("gff_types.py",   "GFF data model + KotOR GIT/ARE/IFO types"),
        ("gff_reader.py",  "Binary GFF V3.2 parser — 18 field types"),
        ("gff_writer.py",  "BFS two-phase binary writer — byte-identical output"),
        ("archives.py",    "BIF / ERF / KEY / RIM archive reader"),
        ("mdl_parser.py",  "ASCII MDL room geometry parser"),
        ("mod_packager.py","ERF/MOD packer with dependency walker"),
    ]
    for i, (name, desc) in enumerate(fmt_items):
        col_x = 50 + (i % 3) * 440
        row_y = FMT_TOP + 26 + (i // 3) * 20
        d.text((col_x, row_y), name, font=CF, fill=FUNC)
        d.text((col_x+148, row_y), desc, font=UFS, fill=T_SEC)

    # Arrows from boxes down to shared layer
    for b in boxes:
        bx_mid = b["x"] + bw//2
        d.line([bx_mid, BOX_TOP+BOX_H, bx_mid, FMT_TOP], fill=T_DIM, width=1)
        d.polygon([(bx_mid-5,FMT_TOP),(bx_mid+5,FMT_TOP),(bx_mid,FMT_TOP+8)], fill=T_DIM)

    # ── IPC Protocol detail ────────────────────────────────────────────────────
    IPC_TOP = FMT_TOP + 80
    d.rectangle([30, IPC_TOP, W-30, IPC_TOP+130], fill=(26,28,36), outline=ACCENT, width=1)
    d.text((50, IPC_TOP+8), "IPC Contract  —  localhost HTTP · JSON envelope · 2-second timeout · graceful degradation", font=UFB, fill=ACCENT)

    ipc_rows = [
        ("7001  GhostRigger", "open_utc / open_utp / open_utd / open_mdl / blueprint_saved / ping", (86,156,214)),
        ("7002  GhostScripter","open_script / open_dlg / script_compiled / open_2da / open_tlk / ping",(197,134,192)),
        ("7003  GModular",     "blueprint_saved / script_compiled / refresh_viewport / ping",        (78,201,176)),
    ]
    for i, (port, actions, col) in enumerate(ipc_rows):
        d.text((50, IPC_TOP+32+i*28),   port, font=UFB, fill=col)
        d.text((240, IPC_TOP+32+i*28),  "→", font=CF, fill=T_DIM)
        d.text((262, IPC_TOP+32+i*28),  actions, font=UFS, fill=T_PRIM)

    d.text((50, IPC_TOP+116),
           "If a target program is not running: quiet status-bar message — no crash, no modal dialog, full standalone use.",
           font=UFS, fill=T_SEC)

    # ── Tech stack bar ─────────────────────────────────────────────────────────
    TECH_TOP = IPC_TOP + 138
    d.rectangle([30, TECH_TOP, W-30, TECH_TOP+46], fill=ELEVATED, outline=BORDER, width=1)
    d.text((50, TECH_TOP+6), "Technology Stack  (all three programs):", font=UFB, fill=T_PRIM)
    tech = [
        "Python 3.12", "PyQt5 5.15", "ModernGL 5.8", "NumPy",
        "Flask (IPC)", "watchdog", "PyInstaller", "pytest"
    ]
    x2 = 50
    for t in tech:
        tw2 = int(d.textlength(t, font=UFS)) + 20
        d.rectangle([x2, TECH_TOP+24, x2+tw2, TECH_TOP+42], fill=(38,42,50), outline=BORDER, width=1)
        d.text((x2+10, TECH_TOP+26), t, font=UFS, fill=PARAM)
        x2 += tw2 + 8

    # ── Completion bar ─────────────────────────────────────────────────────────
    COMP_TOP = TECH_TOP + 54
    d.rectangle([30, COMP_TOP, W-30, COMP_TOP+52], fill=(22,24,22), outline=BORDER, width=1)
    d.text((50, COMP_TOP+6), "GModular Completion  —  Phase 1: ✓ 100%   Phase 2: ✓ ~85%   Phase 3: ⟳ ~10%   Phase 4: ○ 0%", font=UFM, fill=T_PRIM)
    # Progress bar
    bar_x, bar_y, bar_w, bar_h = 50, COMP_TOP+28, W-100, 16
    d.rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], fill=(38,38,38))
    # Fill to 63%
    fill_w = int(bar_w * 0.63)
    d.rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h], fill=TEAL)
    d.text((bar_x+fill_w+4, bar_y), "63%  overall", font=UFS, fill=T_SEC)

    statusbar(d, [
        "GHOSTWORKS_BLUEPRINT.md",
        "Three-program KotOR modding suite",
        "GModular  ·  GhostScripter  ·  GhostRigger",
        "Python 3.12  ·  v1.0"
    ])
    path = f"{OUT_DIR}/05_pipeline_architecture.png"
    img.save(path)
    print(f"  ✓  Screenshot 5 saved → {path}")


# ── Run all ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating GModular portfolio screenshots…")
    make_screenshot_1()
    make_screenshot_2()
    make_screenshot_3()
    make_screenshot_4()
    make_screenshot_5()
    print(f"\nAll 5 screenshots saved to: {OUT_DIR}")
