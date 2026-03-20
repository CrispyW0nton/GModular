"""
GModular — ResourcePort Protocol
=================================
A *structural* Protocol (PEP 544) that any resource provider must satisfy.

Depending on this interface rather than the concrete ``ResourceManager`` class
reduces coupling strength from Model to Contract:

  - Tests can inject ``FakeResourceManager`` or ``MemResourceManager``.
  - The MCP layer, viewport, and core domain all annotate against this type.
  - ``ResourceManager`` already satisfies the protocol — no changes to it needed.

Khononov: "Contract coupling is the lowest coupling strength available for
distant components."  (Ch. 7 — Integration Strength)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, runtime_checkable

try:
    from typing import Protocol
except ImportError:                        # Python < 3.8 compat shim
    from typing_extensions import Protocol  # type: ignore[assignment]

if TYPE_CHECKING:
    pass


@runtime_checkable
class ResourcePort(Protocol):
    """
    Minimal contract for any component that can supply KotOR resources.

    Implementations
    ---------------
    - ``gmodular.formats.archives.ResourceManager``  — production singleton
    - ``gmodular.formats.resource_port.MemResourceManager`` — in-memory stub
    - Any object with matching method signatures (duck-typed via Protocol)

    Usage
    -----
    Annotate parameters / fields as ``ResourcePort`` to avoid hard-wiring to
    the concrete class::

        class ViewportWidget:
            def __init__(self, rm: ResourcePort): ...

        def my_tool(rm: ResourcePort, resref: str) -> bytes | None:
            return rm.get_file(resref, "mdl")
    """

    def get(self, resref: str, res_type: int) -> Optional[bytes]:
        """Return resource bytes by ResRef + numeric type ID, or None."""
        ...

    def get_file(self, resref: str, ext: str) -> Optional[bytes]:
        """Return resource bytes by ResRef + extension string (e.g. ``'mdl'``), or None."""
        ...

    def list_resources(self, res_type: int) -> List[str]:
        """Return sorted list of all known ResRefs for the given type ID."""
        ...


class MemResourceManager:
    """
    In-memory ``ResourcePort`` implementation for tests and headless tools.

    Usage::

        rm = MemResourceManager()
        rm.add("c_bastila", "mdl", mdl_bytes)
        rm.add("dialog",    "tlk", tlk_bytes)

        assert rm.get_file("c_bastila", "mdl") == mdl_bytes
    """

    def __init__(self) -> None:
        # key: (resref.lower(), ext.lower()) → bytes
        self._store: Dict[tuple, bytes] = {}

    def add(self, resref: str, ext: str, data: bytes) -> None:
        """Register a resource."""
        self._store[(resref.lower(), ext.lower())] = data

    def add_by_type_id(self, resref: str, res_type: int, data: bytes) -> None:
        """Register a resource by numeric type ID (resolves extension via RES_TYPE_MAP)."""
        from .archives import RES_TYPE_MAP
        ext = RES_TYPE_MAP.get(res_type, "bin")
        self.add(resref, ext, data)

    # ── ResourcePort interface ──────────────────────────────────────────────

    def get(self, resref: str, res_type: int) -> Optional[bytes]:
        from .archives import RES_TYPE_MAP
        ext = RES_TYPE_MAP.get(res_type, "bin")
        return self._store.get((resref.lower(), ext.lower()))

    def get_file(self, resref: str, ext: str) -> Optional[bytes]:
        return self._store.get((resref.lower(), ext.lower().lstrip(".")))

    def list_resources(self, res_type: int) -> List[str]:
        from .archives import RES_TYPE_MAP
        ext = RES_TYPE_MAP.get(res_type, "bin")
        return sorted(r for (r, e) in self._store if e == ext)

    @property
    def is_loaded(self) -> bool:
        return True

    @property
    def game_tag(self) -> str:
        return "MEM"
