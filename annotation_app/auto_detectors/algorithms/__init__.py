"""Detection algorithms (pupil + glints + limbus) absorbed from pupil-glint-detector.

These are pure functions: take a grayscale image plus thresholds (and optional
ROI / method choice) and return detection results. No GUI, no app dependencies.

Public surface:

    from annotation_app.auto_detectors.algorithms import (
        detect_pupil_and_glints,
        detect_limbus,
        fit_convex_hull_spline,
        pupil_center_of_mass,
    )
"""

from .limbus import detect_limbus
from .pupil import detect_pupil_and_glints, fit_convex_hull_spline, pupil_center_of_mass

__all__ = [
    "detect_limbus",
    "detect_pupil_and_glints",
    "fit_convex_hull_spline",
    "pupil_center_of_mass",
]
