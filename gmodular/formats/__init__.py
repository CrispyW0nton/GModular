"""
GModular formats — GFF/ARE/GIT/IFO readers and writers, plus KotOR-specific
binary format parsers.

Submodules:
  gff_types      — GFF data model + KotOR GIT/ARE/IFO types
  gff_reader     — GFF V3.2 binary reader
  gff_writer     — GFF V3.2 binary writer (BFS two-phase)
  archives       — BIF/KEY/ERF/RIM archive readers + ResourceManager
  resource_port  — ResourcePort Protocol + MemResourceManager (for tests)
  mdl_parser     — KotOR binary MDL/MDX model parser
  tpc_reader     — KotOR TPC/TGA texture reader
  lyt_vis        — KotOR .lyt (room layout) and .vis (visibility) parsers
  wok_parser     — KotOR .wok (walkmesh) binary parser
  twoda_loader   — KotOR 2DA spreadsheet reader
  mod_packager   — Module packager (builds .mod ERF archives)
"""
