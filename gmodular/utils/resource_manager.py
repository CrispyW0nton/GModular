"""
GModular — Resource Manager (re-export shim)

The canonical ResourceManager implementation lives in
``gmodular.formats.archives``.  This module re-exports it so that any
code that imports from ``gmodular.utils.resource_manager`` continues to
work without modification.
"""
from ..formats.archives import (  # noqa: F401
    ResourceManager,
    ResourceEntry,
    KEYReader,
    ERFReader,
    RES_TYPE_MAP,
    EXT_TO_TYPE,
    get_resource_manager,
)

__all__ = [
    "ResourceManager",
    "ResourceEntry",
    "KEYReader",
    "ERFReader",
    "RES_TYPE_MAP",
    "EXT_TO_TYPE",
    "get_resource_manager",
]
