"""Telling a mouse wheel from a trackpad.

On macOS most mice report pixel deltas too (Magic Mouse, Logitech and anything with smooth
scrolling turned on), so `pixelDelta()` alone can't say whether the user turned a wheel or
swiped two fingers. A trackpad is recognised by its device type, or by the begin / update /
end scroll phases that only gesture surfaces send; a mouse wheel has no scroll phase.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QInputDevice


def is_trackpad_scroll(e) -> bool:
    """True for two-finger scrolling on a trackpad, False for a mouse wheel."""
    dev = e.pointingDevice() if hasattr(e, "pointingDevice") else None
    kind = dev.type() if dev is not None else None
    if kind == QInputDevice.DeviceType.TouchPad:
        return True
    return e.phase() != Qt.ScrollPhase.NoScrollPhase and not e.pixelDelta().isNull()


def zoom_key(e) -> bool:
    """Cmd or Ctrl held: scrolling should zoom, whatever the device."""
    return bool(e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))


def wheel_steps(e) -> float:
    """Vertical wheel movement in eighths of a degree (120 per notch, + is away from the user)."""
    dy = e.angleDelta().y()
    if not dy:
        # a few mice only report pixels; treat ~one line of scrolling as a notch
        dy = e.pixelDelta().y() * 4
    return float(dy)
