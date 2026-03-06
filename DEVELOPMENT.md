# GModular — Development Notes

## Iteration Log

### Iteration 3 (2026-03-06) — GFF Writer Fix & Test Suite

**Branch**: `genspark_ai_developer`

#### Critical Fix
- `GFFWriter.to_bytes()` was calling `_add_struct()` shim (no-op) instead of
  `_build_all()` BFS encoder → fixed; all GFF round-trips now work correctly.

#### Added
- `tests/test_gff.py` — 44 pytest tests, 100% pass rate
- `gmodular/utils/resource_manager.py` — KotOR resource discovery
- `README.md` — comprehensive project documentation

#### Test Results
```
python -m pytest tests/test_gff.py -v
44 passed in 0.14s
```

## Architecture Notes

### GFF BFS Two-Phase Writer
The GFF binary format requires struct indices to be stable before field data
can reference them. GModular uses a breadth-first two-phase approach:

**Phase 1 — BFS Collect**: Walk tree breadth-first, assign stable index to every GFFStruct.  
**Phase 2 — Encode Fields**: Encode fields in BFS order; LIST/STRUCT fields embed pre-assigned indices.

This mirrors how Bioware's own GFF3Writer works internally.

## Known Issues / Next Steps

1. BIF/KEY archive parsing not yet implemented (ResourceManager uses loose files only)
2. 3D model rendering (MDL → OpenGL) not yet implemented (placeholder boxes shown)  
3. Walkmesh bake/export not yet implemented (UI stub only)
4. GhostScripter/GhostRigger IPC only live when those tools are running
