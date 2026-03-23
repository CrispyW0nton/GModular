"""
GModular — Animation Timeline Panel
=====================================
A dockable QWidget providing a per-entity animation scrubber,
play/pause, speed control, and animation list selector.

Architecture
------------
- AnimationTimelinePanel  — QWidget (dock target)
  - entity selector     (QComboBox populated from EntityRegistry)
  - animation selector  (QComboBox showing available animation names)
  - ◀ |◀ ▶ ▶| transport buttons
  - speed knob          (QDoubleSpinBox 0.1× – 4×)
  - time ruler          (AnimationRuler custom widget)
  - scrub slider        (QSlider mapped to 0–duration)

The panel connects to the ViewportWidget via the ``viewport`` attribute.
When a viewport is set it polls the AnimationPlayer each redraw via a
QTimer (50 ms / 20 fps) to keep the ruler cursor in sync.

Signals
-------
animation_changed(entity_id: int, anim_name: str)
    Emitted when the user changes the active animation.
time_scrubbed(entity_id: int, t: float)
    Emitted when the user drags the scrub slider.

Usage (in MainWindow)::

    panel = AnimationTimelinePanel()
    dock  = QDockWidget("Animation", self)
    dock.setWidget(panel)
    self.addDockWidget(Qt.BottomDockWidgetArea, dock)
    panel.set_viewport(self._viewport)
"""

from __future__ import annotations

import logging
from typing import Optional, List, Any

log = logging.getLogger(__name__)

# ── Qt optional import (mirrors the rest of the codebase) ────────────────────
_HAS_QT = False
try:
    from qtpy.QtWidgets import (
        QWidget, QHBoxLayout, QVBoxLayout, QLabel,
        QPushButton, QComboBox, QSlider, QDoubleSpinBox,
        QSizePolicy, QToolButton, QFrame, QStyle,
    )
    from qtpy.QtCore import Qt, QTimer, Signal, QSize
    from qtpy.QtGui import QPainter, QColor, QFontMetrics, QPen, QBrush
    _HAS_QT = True
except ImportError:
    # Headless / test environment — provide minimal stubs.
    # The Signal stub is functional: emit() calls all connected callbacks.
    # This lets headless tests verify animation_changed / time_scrubbed fire.
    class _Stub:
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __getattr__(self, n): return self
    QWidget = QHBoxLayout = QVBoxLayout = QLabel = QPushButton = \
        QComboBox = QSlider = QDoubleSpinBox = QSizePolicy = \
        QToolButton = QFrame = _Stub
    Qt = _Stub()
    QTimer = _Stub
    class Signal:
        """
        Functional headless Signal stub.

        Mirrors the Qt Signal interface:
          - connect(callable)  — register a callback
          - disconnect(callable) — remove a callback (no error if absent)
          - emit(*args)        — call all connected callbacks with *args

        Used in the headless test environment so that animation_changed and
        time_scrubbed signals work correctly without a Qt event loop.
        """
        def __init__(self, *type_args):
            self._callbacks: list = []

        def connect(self, f):
            if callable(f) and f not in self._callbacks:
                self._callbacks.append(f)

        def disconnect(self, f=None):
            if f is None:
                self._callbacks.clear()
            else:
                try:
                    self._callbacks.remove(f)
                except ValueError:
                    pass  # already disconnected — Qt silently ignores this

        def emit(self, *args):
            for cb in list(self._callbacks):
                try:
                    cb(*args)
                except Exception:
                    pass  # match Qt behaviour: exceptions in slots don't propagate

    QSize = _Stub
    QPainter = QColor = QFontMetrics = QPen = QBrush = _Stub


# ─────────────────────────────────────────────────────────────────────────────
#  AnimationRuler — custom timeline ruler
# ─────────────────────────────────────────────────────────────────────────────

class AnimationRuler(QWidget):
    """
    Horizontal ruler showing elapsed time and playhead cursor.

    Draws:
    - Dark background
    - Tick marks every 0.1 s (minor) and every 0.5 s (major, with label)
    - Red playhead line at current time
    - Click → scrub to that time (emits time_clicked)
    """

    time_clicked = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._duration: float = 1.0
        self._current:  float = 0.0
        self._loop:     bool  = False
        if _HAS_QT:
            self.setMinimumHeight(32)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setCursor(Qt.SizeHorCursor)

    def set_duration(self, d: float) -> None:
        self._duration = max(0.01, d)
        if _HAS_QT:
            self.update()

    def set_current(self, t: float) -> None:
        self._current = max(0.0, min(t, self._duration))
        if _HAS_QT:
            self.update()

    def set_loop(self, loop: bool) -> None:
        self._loop = loop
        if _HAS_QT:
            self.update()

    if _HAS_QT:
        def mousePressEvent(self, ev):
            if ev.button() == Qt.LeftButton:
                t = self._x_to_time(ev.x())
                self.time_clicked.emit(t)

        def mouseMoveEvent(self, ev):
            if ev.buttons() & Qt.LeftButton:
                t = self._x_to_time(ev.x())
                self.time_clicked.emit(t)

        def _x_to_time(self, px: int) -> float:
            w = max(1, self.width() - 2)
            frac = max(0.0, min(1.0, px / w))
            return frac * self._duration

        def paintEvent(self, ev):
            p = QPainter(self)
            try:
                self._draw(p)
            finally:
                p.end()

        def _draw(self, p: QPainter) -> None:
            w, h = self.width(), self.height()

            # Background
            p.fillRect(0, 0, w, h, QColor(28, 28, 36))

            if self._duration <= 0:
                return

            # Ticks
            major_pen = QPen(QColor(160, 160, 180), 1)
            minor_pen = QPen(QColor(80, 80, 100), 1)

            step = 0.1
            t = 0.0
            while t <= self._duration + 1e-6:
                x = int(t / self._duration * (w - 2)) + 1
                is_major = abs(round(t * 2) - t * 2) < 0.02  # 0.5 s multiples
                if is_major:
                    p.setPen(major_pen)
                    p.drawLine(x, 0, x, h)
                    # Label
                    label = f"{t:.1f}s" if t < 60 else f"{int(t // 60)}:{t % 60:04.1f}"
                    p.setPen(QPen(QColor(180, 180, 200), 1))
                    p.drawText(x + 2, h - 4, label)
                else:
                    p.setPen(minor_pen)
                    p.drawLine(x, h // 2, x, h)
                t = round(t + step, 6)

            # Loop indicator (faint end mark)
            if self._loop:
                p.setPen(QPen(QColor(60, 180, 80, 140), 2))
                p.drawLine(w - 2, 0, w - 2, h)

            # Playhead
            px_pos = int(self._current / self._duration * (w - 2)) + 1
            p.setPen(QPen(QColor(255, 60, 60), 2))
            p.drawLine(px_pos, 0, px_pos, h)

            # Time label on playhead
            lbl = f"{self._current:.2f}s"
            fm  = QFontMetrics(p.font())
            lw  = fm.horizontalAdvance(lbl) if hasattr(fm, 'horizontalAdvance') \
                  else fm.width(lbl)
            off = px_pos + 4 if px_pos + lw + 6 < w else px_pos - lw - 4
            p.setPen(QPen(QColor(255, 80, 80), 1))
            p.drawText(off, 12, lbl)


# ─────────────────────────────────────────────────────────────────────────────
#  AnimationTimelinePanel
# ─────────────────────────────────────────────────────────────────────────────

class AnimationTimelinePanel(QWidget):
    """
    Dockable animation timeline.

    Populated from the viewport's EntityRegistry.  When an entity is
    selected the panel switches to its AnimationPlayer.
    """

    animation_changed = Signal(int, str)  # entity_id, anim_name
    time_scrubbed     = Signal(int, float) # entity_id, time

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._viewport:     Optional[Any] = None
        self._entity_id:    int = 0
        self._entity_map:   dict = {}  # combo index → entity_id
        self._player:       Optional[Any] = None  # current AnimationPlayer
        self._duration:     float = 1.0
        self._poll_timer:   Optional[Any] = None

        if _HAS_QT:
            self._build_ui()
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(50)
            self._poll_timer.timeout.connect(self._poll_player)
            self._poll_timer.start()
            self.setMinimumHeight(110)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(3)

        # ── Row 1: entity selector + animation selector ──────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        lbl_entity = QLabel("Entity:")
        lbl_entity.setFixedWidth(42)
        self._entity_combo = QComboBox()
        self._entity_combo.setToolTip("Select entity to animate")
        self._entity_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._entity_combo.currentIndexChanged.connect(self._on_entity_changed)

        lbl_anim = QLabel("Anim:")
        lbl_anim.setFixedWidth(36)
        self._anim_combo = QComboBox()
        self._anim_combo.setToolTip("Select animation clip")
        self._anim_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._anim_combo.currentTextChanged.connect(self._on_anim_changed)

        row1.addWidget(lbl_entity)
        row1.addWidget(self._entity_combo)
        row1.addWidget(lbl_anim)
        row1.addWidget(self._anim_combo)
        root.addLayout(row1)

        # ── Row 2: transport buttons + speed + loop ──────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        self._btn_rewind  = QPushButton("⏮")
        self._btn_rewind.setFixedSize(28, 24)
        self._btn_rewind.setToolTip("Jump to start")
        self._btn_rewind.clicked.connect(self._on_rewind)

        self._btn_playpause = QPushButton("▶")
        self._btn_playpause.setFixedSize(36, 24)
        self._btn_playpause.setToolTip("Play / Pause")
        self._btn_playpause.setCheckable(True)
        self._btn_playpause.clicked.connect(self._on_play_pause)

        self._btn_end = QPushButton("⏭")
        self._btn_end.setFixedSize(28, 24)
        self._btn_end.setToolTip("Jump to end")
        self._btn_end.clicked.connect(self._on_jump_end)

        self._btn_loop = QPushButton("🔁")
        self._btn_loop.setFixedSize(28, 24)
        self._btn_loop.setToolTip("Toggle loop")
        self._btn_loop.setCheckable(True)
        self._btn_loop.setChecked(True)
        self._btn_loop.clicked.connect(self._on_loop_toggle)

        lbl_speed = QLabel("Speed:")
        lbl_speed.setFixedWidth(40)
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 4.0)
        self._speed_spin.setSingleStep(0.1)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setDecimals(1)
        self._speed_spin.setFixedWidth(56)
        self._speed_spin.setToolTip("Playback speed (×)")
        self._speed_spin.valueChanged.connect(self._on_speed_changed)

        self._lbl_time = QLabel("0.00 / 0.00 s")
        self._lbl_time.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._lbl_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        row2.addWidget(self._btn_rewind)
        row2.addWidget(self._btn_playpause)
        row2.addWidget(self._btn_end)
        row2.addWidget(self._btn_loop)
        row2.addWidget(lbl_speed)
        row2.addWidget(self._speed_spin)
        row2.addWidget(self._lbl_time)
        root.addLayout(row2)

        # ── Row 3: timeline ruler ────────────────────────────────────────────
        self._ruler = AnimationRuler()
        self._ruler.time_clicked.connect(self._on_ruler_click)
        root.addWidget(self._ruler)

        # ── Separator ────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

    # ── Viewport binding ──────────────────────────────────────────────────────

    def set_viewport(self, vp: Any) -> None:
        """Bind this panel to a ViewportWidget.

        Also connects the viewport's ``frame_advanced`` signal so the ruler
        cursor updates every rendered frame instead of relying on the fallback
        50 ms blind poll timer.
        """
        # Disconnect from previous viewport if any
        if self._viewport is not None and _HAS_QT:
            try:
                self._viewport.frame_advanced.disconnect(self._poll_player)
            except Exception:
                pass

        self._viewport = vp

        if vp is not None and _HAS_QT:
            try:
                vp.frame_advanced.connect(self._poll_player)
            except Exception:
                pass  # viewport doesn't have the signal yet (old build)

        self.refresh_entities()

    def refresh_entities(self) -> None:
        """Repopulate the entity combo from the viewport's entity registry."""
        if not _HAS_QT:
            return
        self._entity_combo.blockSignals(True)
        self._entity_combo.clear()
        self._entity_map.clear()

        reg = self._get_registry()
        if reg is None:
            self._entity_combo.addItem("— no entities —")
            self._entity_combo.blockSignals(False)
            return

        for ent in reg.entities:
            label = ent.tag or ent.resref or f"entity_{ent.entity_id}"
            type_tag = {1: "🧍", 3: "🚪", 4: "📦"}.get(ent.entity_type, "◉")
            self._entity_combo.addItem(f"{type_tag} {label}", ent.entity_id)
            self._entity_map[self._entity_combo.count() - 1] = ent.entity_id

        self._entity_combo.blockSignals(False)
        if self._entity_combo.count() > 0:
            self._on_entity_changed(0)

    # ── Entity/animation change handlers ─────────────────────────────────────

    def _on_entity_changed(self, index: int) -> None:
        reg = self._get_registry()
        if reg is None:
            return
        eid = self._entity_combo.itemData(index) if _HAS_QT else 0
        if eid is None:
            return
        self._entity_id = eid
        ent = reg.get(eid)
        self._player = getattr(ent, '_animation_player', None) if ent else None

        # Populate animation list
        if _HAS_QT:
            self._anim_combo.blockSignals(True)
            self._anim_combo.clear()
            if self._player:
                for name in sorted(self._player.animation_names):
                    self._anim_combo.addItem(name)
            self._anim_combo.blockSignals(False)

        self._sync_duration()

    def _on_anim_changed(self, name: str) -> None:
        if not name or not self._player:
            return
        loop = self._btn_loop.isChecked() if _HAS_QT else True
        self._player.play(name, loop=loop)
        self._sync_duration()
        self.animation_changed.emit(self._entity_id, name)
        log.debug(f"AnimationPanel: playing '{name}' on entity {self._entity_id}")

    # ── Transport handlers ────────────────────────────────────────────────────

    def _on_rewind(self) -> None:
        if self._player:
            self._player.stop()
            # reset elapsed
            try:
                self._player._current_state.elapsed = 0.0
            except Exception:
                pass
            self._player._paused = True
        if _HAS_QT:
            self._btn_playpause.setChecked(False)
            self._btn_playpause.setText("▶")
        self._ruler.set_current(0.0)

    def _on_play_pause(self, checked: bool) -> None:
        if not self._player:
            if _HAS_QT and hasattr(self, '_btn_playpause'):
                self._btn_playpause.setChecked(False)
            return
        if checked:
            anim_name = (self._anim_combo.currentText()
                         if _HAS_QT and hasattr(self, '_anim_combo') else "")
            if anim_name:
                loop = (self._btn_loop.isChecked()
                        if _HAS_QT and hasattr(self, '_btn_loop') else True)
                self._player.play(anim_name, loop=loop)
            self._player._paused = False
            if _HAS_QT and hasattr(self, '_btn_playpause'):
                self._btn_playpause.setText("⏸")
        else:
            self._player._paused = True
            if _HAS_QT and hasattr(self, '_btn_playpause'):
                self._btn_playpause.setText("▶")

    def _on_jump_end(self) -> None:
        if self._player:
            try:
                dur = getattr(self._player._current, 'length', self._duration) or 0
                self._player._current_state.elapsed = float(dur)
                self._player._paused = True
            except Exception:
                pass
        if _HAS_QT and hasattr(self, '_btn_playpause'):
            self._btn_playpause.setChecked(False)
            self._btn_playpause.setText("▶")

    def _on_loop_toggle(self, checked: bool) -> None:
        if self._player and self._player._current_state:
            self._player._current_state.loop = checked

    def _on_speed_changed(self, v: float) -> None:
        if self._player:
            self._player._speed = float(v)

    def _on_ruler_click(self, t: float) -> None:
        if not self._player:
            return
        # Use the proper seek() API if available; fall back to direct state access
        if hasattr(self._player, 'seek'):
            self._player.seek(t, pause=True)
        else:
            try:
                self._player._current_state.elapsed = t
                self._player._paused = True
            except Exception:
                pass
        if _HAS_QT:
            self._btn_playpause.setChecked(False)
            self._btn_playpause.setText("▶")
        self._ruler.set_current(t)
        self.time_scrubbed.emit(self._entity_id, t)

    # ── Polling update ────────────────────────────────────────────────────────

    def _poll_player(self, _dt: float = 0.0) -> None:
        """Sync ruler cursor with animation playback.

        Called either by the viewport's ``frame_advanced`` signal or by the
        fallback 50 ms ``QTimer`` when the viewport signal is unavailable.
        The ``_dt`` argument is ignored — we always read the player's own
        elapsed counter so scrubbing is always accurate.
        """
        if not _HAS_QT or not self._player:
            return
        try:
            # Use public API if available (seek/get_elapsed/get_duration), else fallback
            if hasattr(self._player, 'get_elapsed'):
                elapsed = self._player.get_elapsed()
                dur = self._player.get_duration() or self._duration
            else:
                state   = self._player._current_state
                elapsed = float(state.elapsed)
                dur     = float(getattr(self._player._current, 'length',
                                        self._duration) or self._duration)
            self._ruler.set_current(elapsed % dur if dur > 0 else 0)
            self._lbl_time.setText(f"{elapsed % dur:.2f} / {dur:.2f} s")
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sync_duration(self) -> None:
        if self._player and self._player._current:
            d = getattr(self._player._current, 'length', 1.0) or 1.0
            self._duration = float(d)
        else:
            self._duration = 1.0
        if _HAS_QT:
            self._ruler.set_duration(self._duration)

    def _get_registry(self) -> Optional[Any]:
        if self._viewport is None:
            return None
        return getattr(self._viewport, '_entity_registry', None)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_selected_entity(self, entity_id: int) -> None:
        """Called from viewport when user clicks an entity — jumps combo."""
        if not _HAS_QT:
            return
        for i, eid in self._entity_map.items():
            if eid == entity_id:
                self._entity_combo.setCurrentIndex(i)
                return

    def play_animation_on_entity(self, entity_id: int,
                                  anim_name: str, loop: bool = True) -> bool:
        """Programmatically play an animation — used from Inspector / IPC."""
        reg = self._get_registry()
        if reg is None:
            return False
        ent = reg.get(entity_id)
        if ent is None:
            return False
        if ent._animation_player is None:
            ent.setup_animation_player()
        if ent._animation_player is None:
            return False
        ok = ent.play_animation(anim_name, loop=loop)
        if ok:
            self._player = ent._animation_player
            self._entity_id = entity_id
            self._sync_duration()
            if _HAS_QT and hasattr(self, '_btn_playpause'):
                self._btn_playpause.setChecked(True)
                self._btn_playpause.setText("⏸")
        return ok
