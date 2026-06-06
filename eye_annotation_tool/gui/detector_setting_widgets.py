"""Qt widget builder for ``SettingSpec`` descriptors.

One widget kind per ``SettingSpec.type`` tag. The card chrome in
:mod:`detector_card` consumes only :class:`SettingsBlock`.
"""

from collections.abc import Callable
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin import SettingSpec as Setting

# Integer tick resolution for float sliders. 1000 ticks keeps the
# integer<->float round-trip lossless to 3 decimals.
_FLOAT_TICKS = 1000


# ---------------------------------------------------------------------------
# Bound widgets: each owns its setting and reports changes through ``on_change``
# ---------------------------------------------------------------------------


class _BoundWidget(QWidget):
    """Base for every setting widget. Exposes ``current_value`` / ``set_value``."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__()
        self._setting = setting
        self._on_change = on_change
        if setting.help:
            self.setToolTip(setting.help)

    @property
    def setting_name(self) -> str:
        return self._setting.name

    def current_value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Concrete widget kinds — one per Setting.type tag
# ---------------------------------------------------------------------------


def _int_bounds(setting: Setting, default: int) -> tuple[int, int]:
    lo = int(setting.min) if setting.min is not None else 0
    hi = int(setting.max) if setting.max is not None else max(lo + 1, int(default) * 4 + 1)
    return lo, max(hi, lo + 1)


def _float_bounds(setting: Setting, default: float) -> tuple[float, float]:
    lo = float(setting.min) if setting.min is not None else 0.0
    hi = float(setting.max) if setting.max is not None else max(lo + 1.0, float(default) * 4.0 + 1.0)
    return lo, max(hi, lo + 1e-6)


class _IntSliderRow(_BoundWidget):
    """Slider + spinbox row for an ``int`` setting."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        lo, hi = _int_bounds(setting, value if value is not None else 0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(_label(setting.label))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(int(value) if value is not None else lo)
        self._spin = QSpinBox()
        self._spin.setRange(lo, hi)
        self._spin.setValue(self._slider.value())
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._spin.setMaximumWidth(70)
        self._slider.valueChanged.connect(self._spin.setValue)
        self._spin.valueChanged.connect(self._slider.setValue)
        self._slider.valueChanged.connect(lambda v: on_change(setting.name, int(v)))
        row.addWidget(self._slider)
        row.addWidget(self._spin)
        self.setLayout(row)

    def current_value(self) -> int:
        return int(self._slider.value())

    def set_value(self, value: Any) -> None:
        v = int(value)
        self._slider.blockSignals(True)
        self._spin.blockSignals(True)
        self._slider.setValue(v)
        self._spin.setValue(v)
        self._slider.blockSignals(False)
        self._spin.blockSignals(False)


class _FloatSliderRow(_BoundWidget):
    """Slider + double-spinbox row for a ``float`` setting."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        self._lo, self._hi = _float_bounds(setting, value if value is not None else 0.0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(_label(setting.label))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, _FLOAT_TICKS)
        self._spin = QDoubleSpinBox()
        self._spin.setRange(self._lo, self._hi)
        self._spin.setDecimals(3)
        self._spin.setSingleStep((self._hi - self._lo) / _FLOAT_TICKS)
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._spin.setMaximumWidth(80)
        self._set_from_value(float(value) if value is not None else self._lo)
        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        row.addWidget(self._slider)
        row.addWidget(self._spin)
        self.setLayout(row)

    def _to_ticks(self, v: float) -> int:
        span = self._hi - self._lo
        if span <= 0:
            return 0
        return max(0, min(_FLOAT_TICKS, round((v - self._lo) / span * _FLOAT_TICKS)))

    def _to_value(self, ticks: int) -> float:
        return self._lo + (self._hi - self._lo) * (ticks / _FLOAT_TICKS)

    def _set_from_value(self, v: float) -> None:
        self._slider.blockSignals(True)
        self._spin.blockSignals(True)
        self._slider.setValue(self._to_ticks(v))
        self._spin.setValue(v)
        self._slider.blockSignals(False)
        self._spin.blockSignals(False)

    def _on_slider(self, ticks: int) -> None:
        v = self._to_value(int(ticks))
        self._spin.blockSignals(True)
        self._spin.setValue(v)
        self._spin.blockSignals(False)
        self._on_change(self._setting.name, float(v))

    def _on_spin(self, v: float) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(self._to_ticks(float(v)))
        self._slider.blockSignals(False)
        self._on_change(self._setting.name, float(v))

    def current_value(self) -> float:
        return float(self._spin.value())

    def set_value(self, value: Any) -> None:
        self._set_from_value(float(value))


class _BoolCheckbox(_BoundWidget):
    """Single checkbox for a ``bool`` setting."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._check = QCheckBox(setting.label)
        self._check.setChecked(bool(value))
        self._check.toggled.connect(lambda checked: on_change(setting.name, bool(checked)))
        row.addWidget(self._check)
        row.addStretch(1)
        self.setLayout(row)

    def current_value(self) -> bool:
        return bool(self._check.isChecked())

    def set_value(self, value: Any) -> None:
        self._check.blockSignals(True)
        self._check.setChecked(bool(value))
        self._check.blockSignals(False)


class _ChoiceCombo(_BoundWidget):
    """Combo box for a ``Literal[...]`` setting."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(_label(setting.label))
        self._combo = QComboBox()
        for choice in setting.choices:
            self._combo.addItem(choice)
        if value is not None and str(value) in setting.choices:
            self._combo.setCurrentIndex(setting.choices.index(str(value)))
        self._combo.currentTextChanged.connect(lambda text: on_change(setting.name, text))
        row.addWidget(self._combo, 1)
        self.setLayout(row)

    def current_value(self) -> str:
        return self._combo.currentText()

    def set_value(self, value: Any) -> None:
        idx = self._combo.findText(str(value))
        if idx < 0:
            return
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)


class _OptionalInt(_BoundWidget):
    """Enable checkbox + integer slider; emits ``None`` when disabled."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        lo, hi = _int_bounds(setting, int(value) if value is not None else 0)
        enabled = value is not None
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._check = QCheckBox(setting.label)
        self._check.setChecked(enabled)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(int(value) if value is not None else lo)
        self._slider.setEnabled(enabled)
        self._spin = QSpinBox()
        self._spin.setRange(lo, hi)
        self._spin.setValue(self._slider.value())
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._spin.setMaximumWidth(70)
        self._spin.setEnabled(enabled)
        self._check.toggled.connect(self._on_toggle)
        self._slider.valueChanged.connect(self._spin.setValue)
        self._spin.valueChanged.connect(self._slider.setValue)
        self._slider.valueChanged.connect(self._on_slider_changed)
        row.addWidget(self._check)
        row.addWidget(self._slider, 1)
        row.addWidget(self._spin)
        self.setLayout(row)

    def _on_toggle(self, checked: bool) -> None:
        self._slider.setEnabled(checked)
        self._spin.setEnabled(checked)
        self._on_change(self._setting.name, int(self._slider.value()) if checked else None)

    def _on_slider_changed(self, value: int) -> None:
        if self._check.isChecked():
            self._on_change(self._setting.name, int(value))

    def current_value(self) -> int | None:
        return int(self._slider.value()) if self._check.isChecked() else None

    def set_value(self, value: Any) -> None:
        for w in (self._check, self._slider, self._spin):
            w.blockSignals(True)
        try:
            self._check.setChecked(value is not None)
            if value is not None:
                self._slider.setValue(int(value))
                self._spin.setValue(int(value))
            self._slider.setEnabled(value is not None)
            self._spin.setEnabled(value is not None)
        finally:
            for w in (self._check, self._slider, self._spin):
                w.blockSignals(False)


class _OptionalFloat(_BoundWidget):
    """Enable checkbox + float slider; emits ``None`` when disabled."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        self._lo, self._hi = _float_bounds(setting, float(value) if value is not None else 0.0)
        enabled = value is not None
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._check = QCheckBox(setting.label)
        self._check.setChecked(enabled)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, _FLOAT_TICKS)
        self._spin = QDoubleSpinBox()
        self._spin.setRange(self._lo, self._hi)
        self._spin.setDecimals(3)
        self._spin.setSingleStep((self._hi - self._lo) / _FLOAT_TICKS)
        self._spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._spin.setMaximumWidth(80)
        initial = float(value) if value is not None else self._lo
        self._set_from_value(initial)
        self._slider.setEnabled(enabled)
        self._spin.setEnabled(enabled)
        self._check.toggled.connect(self._on_toggle)
        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        row.addWidget(self._check)
        row.addWidget(self._slider, 1)
        row.addWidget(self._spin)
        self.setLayout(row)

    def _to_ticks(self, v: float) -> int:
        span = self._hi - self._lo
        if span <= 0:
            return 0
        return max(0, min(_FLOAT_TICKS, round((v - self._lo) / span * _FLOAT_TICKS)))

    def _to_value(self, ticks: int) -> float:
        return self._lo + (self._hi - self._lo) * (ticks / _FLOAT_TICKS)

    def _set_from_value(self, v: float) -> None:
        self._slider.blockSignals(True)
        self._spin.blockSignals(True)
        self._slider.setValue(self._to_ticks(v))
        self._spin.setValue(v)
        self._slider.blockSignals(False)
        self._spin.blockSignals(False)

    def _on_toggle(self, checked: bool) -> None:
        self._slider.setEnabled(checked)
        self._spin.setEnabled(checked)
        self._on_change(self._setting.name, float(self._spin.value()) if checked else None)

    def _on_slider(self, ticks: int) -> None:
        v = self._to_value(int(ticks))
        self._spin.blockSignals(True)
        self._spin.setValue(v)
        self._spin.blockSignals(False)
        if self._check.isChecked():
            self._on_change(self._setting.name, float(v))

    def _on_spin(self, v: float) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(self._to_ticks(float(v)))
        self._slider.blockSignals(False)
        if self._check.isChecked():
            self._on_change(self._setting.name, float(v))

    def current_value(self) -> float | None:
        return float(self._spin.value()) if self._check.isChecked() else None

    def set_value(self, value: Any) -> None:
        for w in (self._check, self._slider, self._spin):
            w.blockSignals(True)
        try:
            self._check.setChecked(value is not None)
            if value is not None:
                self._set_from_value(float(value))
            self._slider.setEnabled(value is not None)
            self._spin.setEnabled(value is not None)
        finally:
            for w in (self._check, self._slider, self._spin):
                w.blockSignals(False)


class _RoiSpinboxRow(_BoundWidget):
    """Four-spinbox row for an ``(x, y, w, h)`` ROI tuple. ``None`` means whole image."""

    def __init__(self, setting: Setting, value: Any, on_change: Callable[[str, Any], None]) -> None:
        super().__init__(setting, value, on_change)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_label(f"{setting.label} (x, y, w, h)"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._spins: list[QSpinBox] = []
        initial = tuple(value) if isinstance(value, (list, tuple)) and len(value) == 4 else (0, 0, 0, 0)
        for i in range(4):
            spin = QSpinBox()
            spin.setRange(0, 100000)
            spin.setValue(int(initial[i]))
            spin.setMaximumWidth(70)
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.valueChanged.connect(lambda _v, _i=i: self._on_field_changed())
            self._spins.append(spin)
            row.addWidget(spin)
        outer.addLayout(row)
        self.setLayout(outer)

    def _on_field_changed(self) -> None:
        values = tuple(int(s.value()) for s in self._spins)
        self._on_change(self._setting.name, values if any(values) else None)

    def current_value(self) -> tuple | None:
        values = tuple(int(s.value()) for s in self._spins)
        return values if any(values) else None

    def set_value(self, value: Any) -> None:
        coords = tuple(value) if isinstance(value, (list, tuple)) and len(value) == 4 else (0, 0, 0, 0)
        for spin, v in zip(self._spins, coords, strict=True):
            spin.blockSignals(True)
            spin.setValue(int(v))
            spin.blockSignals(False)


# ---------------------------------------------------------------------------
# Builder + container
# ---------------------------------------------------------------------------


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setMinimumWidth(110)
    return lbl


_BUILDERS: dict[str, Callable[[Setting, Any, Callable[[str, Any], None]], _BoundWidget]] = {
    "int": _IntSliderRow,
    "float": _FloatSliderRow,
    "bool": _BoolCheckbox,
    "choice": _ChoiceCombo,
    "optional_int": _OptionalInt,
    "optional_float": _OptionalFloat,
    "roi": _RoiSpinboxRow,
}


class SettingsBlock(QGroupBox):
    """Container that builds one widget per visible :class:`Setting` of a detector."""

    def __init__(
        self,
        settings: list[Setting],
        values: dict[str, Any],
        on_change: Callable[[str, Any], None],
        *,
        title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self._on_change = on_change
        self._widgets: dict[str, _BoundWidget] = {}
        # Hidden settings (e.g. the canvas-driven ROI) have no widget but still carry a value.
        self._hidden: dict[str, Any] = {}
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        for setting in settings:
            if setting.hidden:
                self._hidden[setting.name] = values.get(setting.name, setting.default)
                continue
            builder = _BUILDERS.get(setting.type)
            if builder is None:
                layout.addWidget(QLabel(f"{setting.label}  (unsupported type {setting.type!r})"))
                continue
            widget = builder(setting, values.get(setting.name, setting.default), on_change)
            layout.addWidget(widget)
            self._widgets[setting.name] = widget
        self.setLayout(layout)

    def current_values(self) -> dict[str, Any]:
        return {**self._hidden, **{name: widget.current_value() for name, widget in self._widgets.items()}}

    def set_values(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            if name in self._hidden:
                self._hidden[name] = value
            widget = self._widgets.get(name)
            if widget is not None:
                widget.set_value(value)
