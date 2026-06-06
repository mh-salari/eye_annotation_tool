"""Sizing helpers for ``QComboBox`` widgets."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QStyle


def fit_combo_to_items(combo: QComboBox) -> None:
    """Size the combo and its popup to the widest item. Call after populating."""
    if combo.count() == 0:
        return
    fm = combo.fontMetrics()
    text_px = max(fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
    style = combo.style()
    # The popup is sized independently of the button and needs room for its
    # scrollbar and the selected-item check indicator.
    chrome_px = style.pixelMetric(QStyle.PM_ScrollBarExtent) + style.pixelMetric(QStyle.PM_IndicatorWidth)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    view = combo.view()
    view.setTextElideMode(Qt.ElideNone)
    view.setMinimumWidth(text_px + chrome_px)
