"""
GModular — Event Bus
====================
A lightweight, synchronous publish/subscribe event bus that replaces the
scattered ``get_module_state()`` singleton calls in the GUI layer.

Design goals (Khononov §4.2 — balancing coupling via Contract):
- Publishers emit *named events* (stable string constants).
- Subscribers register callbacks without importing the publisher.
- Coupling strength drops from Functional (sharing full ModuleState knowledge
  at every call site) to **Contract** (shared event-name strings only).
- Components become individually testable: inject a stub EventBus and assert
  that the expected event was published.

Usage
-----
::
    from gmodular.core.events import EventBus, MODULE_CHANGED, OBJECT_SELECTED

    bus = EventBus()

    # Publisher side (ModuleState)
    bus.publish(MODULE_CHANGED)

    # Subscriber side (ViewportWidget)
    bus.subscribe(MODULE_CHANGED, self._on_module_changed)
    bus.subscribe(OBJECT_SELECTED, lambda obj: self.select_object(obj))

Thread safety
-------------
Callbacks are invoked synchronously on the calling thread.  The GUI must
ensure publication happens on the Qt main thread, or use ``QMetaObject.
invokeMethod`` to forward cross-thread events (same pattern as the existing
drain-timer in ``ipc/bridges.py``).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

# ── Stable event-name constants (the "contract") ─────────────────────────────

MODULE_CHANGED      = "module.changed"
"""Published whenever ModuleState data changes (load, save, undo, object edit)."""

MODULE_CLOSED       = "module.closed"
"""Published when a module is unloaded."""

OBJECT_SELECTED     = "module.object_selected"
"""Published when the active selection changes.  kwargs: ``obj`` (GIT object or None)."""

OBJECT_PLACED       = "module.object_placed"
"""Published after a new GIT object is placed.  kwargs: ``obj``."""

OBJECT_DELETED      = "module.object_deleted"
"""Published after a GIT object is deleted.  kwargs: ``obj``."""

GAME_DIR_CHANGED    = "app.game_dir_changed"
"""Published when the user sets a new game directory.  kwargs: ``game_dir`` (str)."""

ROOMS_CHANGED       = "app.rooms_changed"
"""Published when the room layout changes.  kwargs: ``rooms`` (list)."""

STATUS_MESSAGE      = "app.status_message"
"""Published to update the status-bar without coupling to MainWindow.  kwargs: ``text`` (str)."""


# ── EventBus ──────────────────────────────────────────────────────────────────

class EventBus:
    """
    Simple synchronous publish/subscribe bus.

    All callbacks registered for an event name are called in registration
    order when ``publish`` is invoked.  Exceptions in callbacks are caught
    and logged so that one bad subscriber cannot break others.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[..., Any]]] = defaultdict(list)

    # ── Subscriber API ────────────────────────────────────────────────────

    def subscribe(self, event: str, callback: Callable[..., Any]) -> None:
        """
        Register *callback* for *event*.

        The callback signature should accept ``**kwargs`` as published by the
        emitter, e.g.::

            def on_selected(obj=None, **_):
                ...
        """
        if callback not in self._subscribers[event]:
            self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable[..., Any]) -> None:
        """Remove a previously registered callback (no-op if not present)."""
        try:
            self._subscribers[event].remove(callback)
        except ValueError:
            pass

    # ── Publisher API ─────────────────────────────────────────────────────

    def publish(self, event: str, **kwargs: Any) -> None:
        """
        Invoke all subscribers registered for *event*.

        Extra keyword arguments are forwarded to every callback, so subscribers
        that only care about some fields can use ``**_`` to ignore the rest.
        """
        for callback in list(self._subscribers.get(event, [])):
            try:
                callback(**kwargs)
            except Exception as exc:  # pragma: no cover
                log.exception(
                    "EventBus: unhandled exception in subscriber %r for event %r: %s",
                    callback,
                    event,
                    exc,
                )

    # ── Introspection helpers ─────────────────────────────────────────────

    def subscriber_count(self, event: str) -> int:
        """Return the number of subscribers for *event*."""
        return len(self._subscribers.get(event, []))

    def clear(self) -> None:
        """Remove all subscriptions (useful in tests between cases)."""
        self._subscribers.clear()


# ── Application-level singleton ───────────────────────────────────────────────

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-level EventBus singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
