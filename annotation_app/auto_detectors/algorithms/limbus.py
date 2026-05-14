"""Daugman integro-differential operator limbus circle detection."""

import numpy as np
from daugman_derived_boundary_detectors import IntegroDifferentialOperator


def detect_limbus(
    img: np.ndarray,
    pupil_center: tuple[float, float],
    pupil_radius: float,
    r_min_factor: float = 1.5,
    r_max_factor: float = 5.0,
) -> tuple[tuple[float, float], float]:
    """Daugman integro-differential operator limbus circle: ``(center_xy, radius)``.

    The centre search is seeded at the pupil centre and swept over a ±15 px
    window. The iris radius is searched between ``r_min_factor`` and
    ``r_max_factor`` times the pupil radius.
    """
    pcx, pcy = pupil_center
    r_min = max(round(pupil_radius * r_min_factor), 1)
    r_max = max(round(pupil_radius * r_max_factor), r_min + 1)
    op = IntegroDifferentialOperator(img, r_min=r_min, r_max=r_max)
    results = op.search(cen_x=round(pcy), cen_y=round(pcx), range_=15, step=1)
    if len(results) == 0:
        raise ValueError("integro-differential operator: no iris candidates")
    ly, lx, _score, lr = results[-1]
    return (float(lx), float(ly)), float(lr)
