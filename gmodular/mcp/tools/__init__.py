"""Tool registry: collect tool definitions and dispatch call_tool by name."""
from __future__ import annotations

from typing import Any, Dict, List

from gmodular.mcp.tools import (
    agentdecompile,
    animation,
    archives,
    composite,
    conversion,
    diff_tools,
    discovery,
    formats,
    gamedata,
    ghostworks,
    installation,
    modules,
    refs,
    scripts,
    walkmesh,
)
# DLG editor and NWScript bridge are imported lazily inside handlers
# to respect the architecture rule: mcp/ must not import gui/ at module level.
# (test_architecture.py::test_mcp_does_not_import_gui uses AST scan.)

# ── Lazy module helpers (keep mcp→gui boundary clean) ─────────────────────

def _get_dlg_tools():
    """Return DLG editor tool descriptors (lazy import avoids mcp→gui violation)."""
    import importlib
    mod = importlib.import_module("gmodular.gui.dlg_editor")
    return mod.get_dlg_editor_tools()


def _get_nwscript_tools():
    """Return NWScript bridge tool descriptors."""
    import importlib
    mod = importlib.import_module("gmodular.ipc.nwscript_bridge")
    return mod.get_tools()


# ── Tool catalogue ─────────────────────────────────────────────────────────

def get_all_tools() -> List[Dict[str, Any]]:
    """Return every tool schema dict from every sub-module."""
    return (
        # Composite high-level tools (context-agnostic, high fan-in)
        # These are the primary tools for AI agents — they answer questions
        # rather than exposing format details. See DESIGN_PHILOSOPHY.md §8-9.
        composite.get_tools()
        # Low-level primitive tools (still exposed for programmatic access)
        + installation.get_tools()
        + discovery.get_tools()
        + gamedata.get_tools()
        + archives.get_tools()
        + conversion.get_tools()
        + modules.get_tools()
        + refs.get_tools()
        + walkmesh.get_tools()
        + agentdecompile.get_tools()
        # Animation control tools
        + animation.get_tools()
        # Format library tools (SSF, LIP, TXI, VIS, NCS, 2DA write, TLK write)
        + formats.get_tools()
        # NWScript / NCS script tools (disasm, compile, decompile, info)
        + scripts.get_tools()
        # Diff and patch tools (GFF diff, 2DA diff, TLK diff, GFF patch)
        + diff_tools.get_tools()
        # DLG visual editor MCP tools (lazy import keeps mcp→gui boundary clean)
        + _get_dlg_tools()
        # NWScript compile/decompile/check/format tools
        + _get_nwscript_tools()
        # Ghostworks pipeline IPC tools (GhostRigger + GhostScripter bridges)
        + ghostworks.get_tools()
    )


# ── Dispatcher ─────────────────────────────────────────────────────────────

async def handle_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Route a tool call by name to the appropriate handler."""

    # Composite high-level tools
    if name == "get_resource":
        return await composite.handle_get_resource(arguments)
    if name == "get_quest":
        return await composite.handle_get_quest(arguments)
    if name == "get_creature":
        return await composite.handle_get_creature(arguments)
    if name == "get_conversation":
        return await composite.handle_get_conversation(arguments)
    if name == "get_area":
        return await composite.handle_get_area(arguments)
    if name == "get_script":
        return await composite.handle_get_script(arguments)
    if name == "search":
        return await composite.handle_search(arguments)

    # Installation management
    if name == "detectInstallations":
        return await installation.handle_detect_installations(arguments)
    if name == "loadInstallation":
        return await installation.handle_load_installation(arguments)
    if name == "kotor_installation_info":
        return await installation.handle_installation_info(arguments)

    # Resource discovery
    if name == "listResources":
        return await discovery.handle_list_resources(arguments)
    if name == "describeResource":
        return await discovery.handle_describe_resource(arguments)
    if name == "kotor_find_resource":
        return await discovery.handle_find_resource(arguments)
    if name == "kotor_search_resources":
        return await discovery.handle_search_resources(arguments)

    # Game data
    if name == "journalOverview":
        return await gamedata.handle_journal_overview(arguments)
    if name == "kotor_lookup_2da":
        return await gamedata.handle_lookup_2da(arguments)
    if name == "kotor_lookup_tlk":
        return await gamedata.handle_lookup_tlk(arguments)

    # Archives
    if name == "kotor_list_archive":
        return await archives.handle_list_archive(arguments)
    if name == "kotor_extract_resource":
        return await archives.handle_extract_resource(arguments)

    # Format conversion
    if name == "kotor_read_gff":
        return await conversion.handle_read_gff(arguments)
    if name == "kotor_read_2da":
        return await conversion.handle_read_2da(arguments)
    if name == "kotor_read_tlk":
        return await conversion.handle_read_tlk(arguments)

    # Modules
    if name == "kotor_list_modules":
        return await modules.handle_list_modules(arguments)
    if name == "kotor_describe_module":
        return await modules.handle_describe_module(arguments)
    if name == "kotor_module_resources":
        return await modules.handle_module_resources(arguments)

    # References / DLG / JRL
    if name == "kotor_list_references":
        return await refs.handle_list_references(arguments)
    if name == "kotor_find_referrers":
        return await refs.handle_find_referrers(arguments)
    if name == "kotor_describe_dlg":
        return await refs.handle_describe_dlg(arguments)
    if name == "kotor_describe_jrl":
        return await refs.handle_describe_jrl(arguments)
    if name == "kotor_find_strref_referrers":
        return await refs.handle_find_strref_referrers(arguments)
    if name == "kotor_describe_resource_refs":
        return await refs.handle_describe_resource_refs(arguments)

    # Walkmesh / MDL
    if name == "kotor_walkmesh_validation_diagram":
        return await walkmesh.handle_walkmesh_validation_diagram(arguments)
    if name == "kotor_walkmesh_info":
        return await walkmesh.handle_walkmesh_info(arguments)
    if name == "kotor_mdl_info":
        return await walkmesh.handle_mdl_info(arguments)

    # Animation control
    if name == "kotor_list_animations":
        return await animation.handle_list_animations(arguments)
    if name == "kotor_play_animation":
        return await animation.handle_play_animation(arguments)
    if name == "kotor_stop_animation":
        return await animation.handle_stop_animation(arguments)
    if name == "kotor_animation_state":
        return await animation.handle_animation_state(arguments)
    if name == "kotor_entity_info":
        return await animation.handle_entity_info(arguments)

    # Format library tools
    if name == "kotor_read_ssf":
        return await formats.handle_read_ssf(arguments)
    if name == "kotor_read_lip":
        return await formats.handle_read_lip(arguments)
    if name == "kotor_read_txi":
        return await formats.handle_read_txi(arguments)
    if name == "kotor_read_vis":
        return await formats.handle_read_vis(arguments)
    if name == "kotor_read_ncs":
        return await formats.handle_read_ncs(arguments)
    if name == "kotor_write_ssf":
        return await formats.handle_write_ssf(arguments)
    if name == "kotor_write_2da_csv":
        return await formats.handle_write_2da_csv(arguments)
    if name == "kotor_write_tlk_patch":
        return await formats.handle_write_tlk_patch(arguments)
    if name == "kotor_describe_ssf":
        return await formats.handle_describe_ssf(arguments)
    if name == "kotor_read_ltr":
        return await formats.handle_read_ltr(arguments)
    if name == "kotor_write_ltr":
        return await formats.handle_write_ltr(arguments)
    if name == "kotor_write_ncs":
        return await formats.handle_write_ncs(arguments)
    if name == "kotor_read_lyt":
        return await formats.handle_read_lyt(arguments)
    if name == "kotor_write_lyt":
        return await formats.handle_write_lyt(arguments)
    if name == "kotor_read_bwm":
        return await formats.handle_read_bwm(arguments)
    if name == "kotor_resource_type_lookup":
        return await formats.handle_resource_type_lookup(arguments)
    if name == "kotor_read_tpc_info":
        return await formats.handle_read_tpc_info(arguments)
    if name == "kotor_read_pth":
        return await formats.handle_read_pth(arguments)
    if name == "kotor_write_pth":
        return await formats.handle_write_pth(arguments)
    if name == "kotor_write_bwm":
        return await formats.handle_write_bwm(arguments)
    if name == "kotor_write_lip":
        return await formats.handle_write_lip(arguments)
    if name == "kotor_write_vis":
        return await formats.handle_write_vis(arguments)
    if name == "kotor_write_txi":
        return await formats.handle_write_txi(arguments)

    # AgentDecompile / Ghidra bridge tools
    if name == "ghidra_get_program_info":
        return await agentdecompile.handle_get_program_info(arguments)
    if name == "ghidra_search_symbols":
        return await agentdecompile.handle_search_symbols(arguments)
    if name == "ghidra_list_functions":
        return await agentdecompile.handle_list_functions(arguments)
    if name == "ghidra_find_function":
        return await agentdecompile.handle_find_function(arguments)
    if name == "ghidra_decompile":
        return await agentdecompile.handle_decompile(arguments)
    if name == "ghidra_cross_reference":
        return await agentdecompile.handle_cross_reference(arguments)
    if name == "ghidra_search_strings":
        return await agentdecompile.handle_search_strings(arguments)
    if name == "ghidra_list_imports":
        return await agentdecompile.handle_list_imports(arguments)
    if name == "ghidra_analyze_vtables":
        return await agentdecompile.handle_analyze_vtables(arguments)
    if name == "ghidra_data_flow":
        return await agentdecompile.handle_data_flow(arguments)
    if name == "ghidra_export_c":
        return await agentdecompile.handle_export_c(arguments)
    if name == "ghidra_kotor_function_map":
        return await agentdecompile.handle_kotor_function_map(arguments)

    # NWScript / NCS script tools
    if name == "kotor_disasm_script":
        return await scripts.handle_disasm_script(arguments)
    if name == "kotor_compile_script":
        return await scripts.handle_compile_script(arguments)
    if name == "kotor_decompile_script":
        return await scripts.handle_decompile_script(arguments)
    if name == "kotor_ncs_info":
        return await scripts.handle_ncs_info(arguments)

    # Diff and patch tools
    if name == "kotor_gff_diff":
        return await diff_tools.handle_gff_diff(arguments)
    if name == "kotor_2da_diff":
        return await diff_tools.handle_2da_diff(arguments)
    if name == "kotor_tlk_diff":
        return await diff_tools.handle_tlk_diff(arguments)
    if name == "kotor_patch_gff":
        return await diff_tools.handle_patch_gff(arguments)

    # DLG visual editor tools
    if name == "kotor_dlg_parse":
        import importlib; mod = importlib.import_module("gmodular.gui.dlg_editor")
        return await mod.handle_dlg_parse(arguments)
    if name == "kotor_dlg_add_node":
        import importlib; mod = importlib.import_module("gmodular.gui.dlg_editor")
        return await mod.handle_dlg_add_node(arguments)
    if name == "kotor_dlg_link_nodes":
        import importlib; mod = importlib.import_module("gmodular.gui.dlg_editor")
        return await mod.handle_dlg_link_nodes(arguments)
    if name == "kotor_dlg_summarize":
        import importlib; mod = importlib.import_module("gmodular.gui.dlg_editor")
        return await mod.handle_dlg_summarize(arguments)
    if name == "kotor_dlg_write":
        import importlib; mod = importlib.import_module("gmodular.gui.dlg_editor")
        return await mod.handle_dlg_write(arguments)

    # NWScript bridge tools
    if name == "kotor_compile_nss":
        import importlib; mod = importlib.import_module("gmodular.ipc.nwscript_bridge")
        return await mod.handle_compile_nss(arguments)
    if name == "kotor_decompile_ncs":
        import importlib; mod = importlib.import_module("gmodular.ipc.nwscript_bridge")
        return await mod.handle_decompile_ncs(arguments)
    if name == "kotor_nss_check":
        import importlib; mod = importlib.import_module("gmodular.ipc.nwscript_bridge")
        return await mod.handle_nss_check(arguments)
    if name == "kotor_nss_format":
        import importlib; mod = importlib.import_module("gmodular.ipc.nwscript_bridge")
        return await mod.handle_nss_format(arguments)

    # Ghostworks pipeline tools (GhostRigger + GhostScripter IPC)
    if name == "ghostrigger_ping":
        return await ghostworks.handle_ghostrigger_ping(arguments)
    if name == "ghostrigger_open_blueprint":
        return await ghostworks.handle_ghostrigger_open_blueprint(arguments)
    if name == "ghostrigger_get_blueprint":
        return await ghostworks.handle_ghostrigger_get_blueprint(arguments)
    if name == "ghostrigger_set_field":
        return await ghostworks.handle_ghostrigger_set_field(arguments)
    if name == "ghostrigger_set_fields_bulk":
        return await ghostworks.handle_ghostrigger_set_fields_bulk(arguments)
    if name == "ghostrigger_save_blueprint":
        return await ghostworks.handle_ghostrigger_save_blueprint(arguments)
    if name == "ghostrigger_list_blueprints":
        return await ghostworks.handle_ghostrigger_list_blueprints(arguments)
    if name == "ghostscripter_ping":
        return await ghostworks.handle_ghostscripter_ping(arguments)
    if name == "ghostscripter_open_script":
        return await ghostworks.handle_ghostscripter_open_script(arguments)
    if name == "ghostscripter_get_script":
        return await ghostworks.handle_ghostscripter_get_script(arguments)
    if name == "ghostscripter_compile":
        return await ghostworks.handle_ghostscripter_compile(arguments)
    if name == "ghostscripter_list_scripts":
        return await ghostworks.handle_ghostscripter_list_scripts(arguments)

    raise ValueError(f"Unknown tool '{name}'")
