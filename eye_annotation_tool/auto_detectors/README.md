# Writing a Detector Plugin

A detector plugin is a single Python class that owns its algorithm,
its Qt parameter panel, its serialization, its overlay drawing and
its colour palette. Dropping in a new plugin requires **no edits to
the core application** — discovery is automatic via one of three
channels, and the canvas paints whatever shape your `draw_overlay`
method produces.

## The four anatomical targets

Every plugin targets exactly one of:

| Target   | What it detects                  | May depend on              |
|----------|----------------------------------|----------------------------|
| `pupil`  | Pupil centre + contour ellipse   | (none — root of the graph) |
| `glint`  | IR-LED reflection point(s)       | `pupil`                    |
| `limbus` | Iris / limbus circle             | `pupil`                    |
| `eyelid` | Eyelid contour                   | (free; not currently used) |

The target set is fixed — adding a fifth requires editing the core
`Target` literal. The set of plugins per target is open.

## Minimal plugin

```python
# my_pupil.py
import numpy as np
from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QGroupBox, QSlider, QVBoxLayout, QWidget

from eye_annotation_tool.auto_detectors.plugin_interface import DetectorPlugin


CENTER_COLOR = QColor(0, 200, 255, 255)


class _Panel(QGroupBox):
    """Right-side panel widget for the plugin."""

    # Required: emitted on every widget change with the new params dict.
    params_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__("My Pupil", parent)
        self._params = {"radius": 30}
        layout = QVBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(5, 100)
        self.slider.setValue(self._params["radius"])
        self.slider.valueChanged.connect(self._on_change)
        layout.addWidget(self.slider)
        self.setLayout(layout)

    def _on_change(self, value):
        self._params["radius"] = int(value)
        self.params_changed.emit(dict(self._params))

    # Required: current widget state as a params dict.
    def current_params(self) -> dict:
        return dict(self._params)

    # Required: populate widgets from a params dict WITHOUT emitting
    # ``params_changed`` (use blockSignals around the writes).
    def set_params(self, params: dict) -> None:
        if "radius" in params:
            self.slider.blockSignals(True)
            self.slider.setValue(int(params["radius"]))
            self.slider.blockSignals(False)
            self._params["radius"] = int(params["radius"])


class MyPupil(DetectorPlugin):
    """Toy pupil detector — picks a fixed circle at the image centre."""

    name = "my_pupil"  # unique slug; appears in the Auto Detectors menu
    target = "pupil"  # one of the four anatomical targets
    requires = ()  # other targets whose results we need
    live = True  # True = re-run on slider drag; False = manual Detect button only

    @classmethod
    def default_params(cls) -> dict:
        return {"radius": 30}

    def make_panel(self, parent=None) -> QWidget:
        return _Panel(parent)

    def detect(self, image: np.ndarray, params: dict, shared_results: dict) -> dict | None:
        h, w = image.shape[:2]
        cx, cy = w / 2.0, h / 2.0
        r = float(params["radius"])
        return {
            "center": [cx, cy],
            "ellipse": {
                "center": [cx, cy],
                "size": [2 * r, 2 * r],
                "angle": 0.0,
            },
        }

    def serialize(self, result: dict) -> dict:
        return {
            "center": list(result["center"]),
            "ellipse": {
                "center": list(result["ellipse"]["center"]),
                "size": list(result["ellipse"]["size"]),
                "angle": float(result["ellipse"]["angle"]),
            },
        }

    def deserialize(self, blob: dict) -> dict:
        return self.serialize(blob)  # symmetric here

    def draw_overlay(self, painter: QPainter, result: dict, scale: float) -> None:
        cx, cy = result["center"]
        painter.setBrush(CENTER_COLOR)
        painter.setPen(QPen(CENTER_COLOR, 3, Qt.SolidLine))
        painter.drawEllipse(QPointF(cx * scale, cy * scale), 1.5, 1.5)
```

That's the complete contract. The next sections cover **how to load
it** and **optional surface** you may want.

## Discovery channels

The plugin manager scans three places at startup:

### 1. Built-in (shipped with the app)

`eye_annotation_tool/auto_detectors/plugins/<target>_detectors/*.py`.
Only used by plugins you contribute upstream.

### 2. Env-var directories — `EYE_ANNOTATION_PLUGIN_PATH`

`os.pathsep`-separated list of directories. Each directory is walked
recursively for `*.py` files and every concrete `DetectorPlugin`
subclass found is registered. Simplest way to add a one-off plugin
without packaging:

```bash
export EYE_ANNOTATION_PLUGIN_PATH=$HOME/my_plugins
eye_annotation_tool
```

### 3. Python entry-points — `eye_annotation_tool.plugins`

For pip-installable plugin packages. In your plugin package's
`pyproject.toml`:

```toml
[project.entry-points."eye_annotation_tool.plugins"]
my_pupil = "my_pkg.detector:MyPupil"
```

Install the package (`pip install -e .` during development) and the
plugin shows up the next time `eye_annotation_tool` starts.

## Optional surface

The minimum contract above gives you a working plugin. Additional
class attributes and panel signals unlock more behaviour:

### Class attributes

```python
class MyPupil(DetectorPlugin):
    # ... required attrs above ...

    # Lower z-order draws first (behind). Built-in limbus uses -10 so
    # the iris ring sits under pupil + glint markers. Default 0.
    overlay_z_order: int = 0

    # Colour for the per-target ROI rectangle. Set only on plugins
    # whose panel emits ``roi_edit_requested``.
    roi_color: QColor | None = QColor(0, 200, 255, 200)

    # Colour for the threshold-mask overlay. Set only on plugins whose
    # ``detect`` returns a ``"mask"`` key.
    mask_color: QColor | None = QColor(0, 200, 220, 64)
```

### Panel signals (all optional)

A panel widget can additionally expose these `pyqtSignal`s, which the
core wires up automatically if present:

| Signal                 | Payload | What it does                                                      |
|------------------------|---------|-------------------------------------------------------------------|
| `roi_edit_requested`   | `bool`  | Enters / leaves canvas drag-edit mode for this target's ROI rect  |
| `clear_roi_requested`  | none    | Drops the ROI rectangle and re-runs detection without it          |
| `show_mask_toggled`    | `bool`  | Toggles the threshold-mask overlay visibility                     |
| `detect_requested`     | none    | Triggers a single detection run (used by `live=False` plugins)    |

If the panel surfaces an ROI button, it should also expose a
`set_<target>_roi(roi)` method (e.g. `set_pupil_roi`) — the canvas
calls it after the user finishes drag-editing the rectangle so the
new value lands in the panel's params dict.

### Returning a mask

If your `detect` returns a `"mask"` key (a `uint8` numpy array of the
same shape as the input image, non-zero where the mask is "on"), the
viewer can render it as a semi-transparent fill when the user toggles
**Show mask**. The mask must be stripped on `serialize` — it's
transient view-only state, never persisted to JSON.

### Consuming upstream results — `requires`

Listing a target in `requires` guarantees `shared_results[target]` is
non-None when `detect` runs (the orchestrator topologically sorts the
chain). The shape matches the upstream plugin's `deserialize` output;
the built-in `pupil` shape is documented above.

Pupil can come from either an auto pupil plugin or from a manually
fitted ellipse — your plugin doesn't need to care which.

## Strict discovery

Discovery is intentionally strict — at startup any of these raise
`RuntimeError` with the offending plugin's source:

- empty `name`
- duplicate `name` across plugins
- plugin's `target` mismatches what project settings expect
- a directory in `EYE_ANNOTATION_PLUGIN_PATH` that doesn't exist
- an entry-point whose value is not a `DetectorPlugin` subclass

If a plugin is silently missing from the menu, list the registered
ones with:

```bash
python -c "from eye_annotation_tool.auto_detectors import PluginManager; \
           print(sorted(PluginManager().all()))"
```
