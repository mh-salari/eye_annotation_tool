# Writing a Detector Plugin

A plugin is a single `DetectorPlugin` instance: a function plus enough
metadata to render its parameter panel and to wire upstream results
into its call. No Qt code, no subclassing — just one dataclass.

## Discovery

eye-annotation-tool loads plugins from three places at startup:

1. **cheshm bridge (built-in)** — every detector exposed by
   [cheshm](https://github.com/mh-salari/cheshm) is wrapped
   automatically. Nothing to do.
2. **User plugin dir** — `~/.config/eye_annotation_tool/plugins/`.
   Drop a `.py` file here and it loads on the next launch.
3. **`EYE_ANNOTATION_PLUGINS` env var** — `os.pathsep`-separated list
   of extra directories scanned the same way:

   ```bash
   export EYE_ANNOTATION_PLUGINS=$HOME/my_plugins:/path/to/another
   eye_annotation_tool
   ```

A `.py` file in any user dir is loaded if it declares a module-level
`PLUGINS = [DetectorPlugin(...), ...]` list. Files whose name starts
with `_` are skipped. A plugin that shares `(kind, name)` with a
built-in shadows the built-in — useful for tuning a detector without
forking cheshm.

## Plugin contract

```python
from eye_annotation_tool.auto_detectors.plugin import DetectorPlugin, SettingSpec

DetectorPlugin(
    name: str,                                  # unique slug, shown in the menu
    kind: "pupil" | "glint" | "limbus" | "eyelid",
    function: Callable,                         # (image, *wired_args, **settings) -> dict | None
    settings: list[SettingSpec] = [],           # tunables -> Qt panel
    wired_inputs: list[str] = [],               # ["pupil_center", "pupil_radius", ...]
    overlays: list[tuple[str, str]] = [],       # [("contour", "line"), ("center", "point")]
    description: str = "",
    family: str = "",
)
```

`function` receives the grayscale image first, then any wired upstream
results (positionally, in the order `wired_inputs` lists them), then
the settings as keyword arguments. It returns the result dict — shape
depends on `kind`; see the cheshm built-ins for examples — or `None`
when detection fails.

### SettingSpec

```python
SettingSpec(
    name: str,
    default: Any,
    type: str = "float",       # "int" | "float" | "bool" | "choice"
                               # | "optional_int" | "optional_float" | "roi" | "any"
    min: float | int | None = None,
    max: float | int | None = None,
    choices: list[str] = [],   # required for type="choice"
    label: str = "",           # falls back to name title-cased
    help: str = "",
    hidden: bool = False,      # skip in the UI (orchestrator fills it)
)
```

## Minimal example

```python
# ~/.config/eye_annotation_tool/plugins/my_pupil.py
import cv2
from eye_annotation_tool.auto_detectors.plugin import DetectorPlugin, SettingSpec


def detect_my_pupil(image, threshold=50):
    _, binary = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    m = cv2.moments(largest)
    if m["m00"] == 0:
        return None
    cx = m["m10"] / m["m00"]
    cy = m["m01"] / m["m00"]
    (ex, ey), (w, h), angle = cv2.fitEllipse(largest) if len(largest) >= 5 else ((cx, cy), (1.0, 1.0), 0.0)
    return {
        "center": (cx, cy),
        "ellipse": ((ex, ey), (w, h), angle),
        "contour": largest,
    }


PLUGINS = [
    DetectorPlugin(
        name="MyPupil",
        kind="pupil",
        function=detect_my_pupil,
        settings=[
            SettingSpec(name="threshold", default=50, type="int", min=0, max=255, label="Pupil threshold"),
        ],
        overlays=[("contour", "line"), ("center", "point")],
    ),
]
```

Restart `eye_annotation_tool` — "MyPupil" appears in the pupil card's
detector dropdown with a threshold slider.
