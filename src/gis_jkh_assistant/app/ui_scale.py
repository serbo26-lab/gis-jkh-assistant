"""User interface size in addition to Qt's native display DPI scaling."""
from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QGridLayout, QLayout

SCALE_SETTING = "ui.scale_percent"
SCALE_OPTIONS = (100, 125, 150)


def scale_percent(value: object) -> int:
    """A malformed or obsolete preference must not make the UI unusable."""
    try:
        percent = int(str(value))
    except (TypeError, ValueError):
        return 100
    return percent if percent in SCALE_OPTIONS else 100


def scale_factor(value: object) -> float:
    return scale_percent(value) / 100


def scaled(value: int, factor: float) -> int:
    return max(0, round(value * factor))


def apply_layout_scale(root, factor: float) -> None:
    """Scale authored spacing from its base once, without accumulating rounding."""
    for layout in root.findChildren(QLayout):
        margins = layout.property("uiBaseMargins")
        if margins is None:
            current = layout.contentsMargins()
            margins = [current.left(), current.top(), current.right(), current.bottom()]
            layout.setProperty("uiBaseMargins", margins)
        layout.setContentsMargins(*(scaled(value, factor) for value in margins))
        if isinstance(layout, (QGridLayout, QFormLayout)):
            for direction in ("Horizontal", "Vertical"):
                key = f"uiBase{direction}Spacing"
                value = layout.property(key)
                if value is None:
                    value = getattr(layout, f"{direction.lower()}Spacing")()
                    layout.setProperty(key, value)
                if value >= 0:
                    getattr(layout, f"set{direction}Spacing")(scaled(value, factor))
        else:
            spacing = layout.property("uiBaseSpacing")
            if spacing is None:
                spacing = layout.spacing()
                layout.setProperty("uiBaseSpacing", spacing)
            if spacing >= 0:
                layout.setSpacing(scaled(spacing, factor))
