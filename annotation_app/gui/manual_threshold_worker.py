"""Background detection worker for the Manual Threshold mode.

Lives in a ``QThread`` so the GUI stays responsive while
``detect_pupil_and_glints`` runs. Each call to :meth:`detect` runs once on the
worker's event loop; if a new call comes in before the previous one finishes,
the previous result is still emitted (the receiver should compare against the
current params if it cares about staleness).
"""

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from annotation_app.auto_detectors.algorithms import detect_limbus, detect_pupil_and_glints


class DetectionWorker(QObject):
    """Run ``detect_pupil_and_glints`` off the GUI thread."""

    detection_ready = pyqtSignal(dict)
    detection_failed = pyqtSignal(str)

    # ``object`` (not ``np.ndarray``) because numpy is not a Qt-registered
    # meta type and queued connections across threads must marshal arguments
    # through Qt's known types.
    @pyqtSlot(object, object)
    def detect(self, image: np.ndarray, params: dict) -> None:
        """Run a single detection. ``params`` carries every detector knob."""
        try:
            result = detect_pupil_and_glints(
                image,
                pupil_threshold=int(params["pupil_threshold"]),
                glint_threshold=int(params["glint_threshold"]),
                glint_margin_ratio=float(params["glint_margin_ratio"]),
                glints_target=int(params["glints_target"]),
                glint_max_area_ratio=float(params["glint_max_area_ratio"]),
                pupil_center_method=params["pupil_center_method"],
                pupil_roi=params.get("pupil_roi"),
                glint_roi=params.get("glint_roi"),
            )
        except Exception as exc:  # detection_failed surfaces in the GUI status bar
            self.detection_failed.emit(f"{type(exc).__name__}: {exc}")
            return

        # Limbus is best-effort: it depends on a successful pupil fit and
        # the Daugman IDO can fail at extreme thresholds. Don't poison the
        # whole detection when only the limbus part is unhappy.
        limbus = None
        try:
            (pcx, pcy), (pw, ph), _ = result["pupil_ellipse"]
            pupil_radius = max(pw, ph) / 2
            (lcx, lcy), lr = detect_limbus(image, (pcx, pcy), pupil_radius)
            limbus = {"center": [float(lcx), float(lcy)], "radius": float(lr)}
        except Exception:
            pass

        # ``pupil_contour`` deliberately omitted — it's an ndarray (not JSON
        # serialisable for the tuning save path) and the viewer doesn't draw
        # it anyway.
        payload = {
            "pupil_center": list(result["pupil_center"]),
            "pupil_ellipse": _ellipse_to_json(result["pupil_ellipse"]),
            "glints": [list(g["center"]) for g in result["glints"]],
            "limbus": limbus,
        }
        self.detection_ready.emit(payload)


def _ellipse_to_json(ellipse: tuple) -> list:
    """Convert ((cx, cy), (w, h), angle) into JSON-friendly nested lists."""
    (cx, cy), (w, h), angle = ellipse
    return [[float(cx), float(cy)], [float(w), float(h)], float(angle)]
