"""Image processing utilities for ellipse fitting and point selection."""

import cv2
import numpy as np
from cheshm.shape import smoothing_spline
from PyQt5.QtCore import QPointF
from scipy import optimize
from scipy.spatial import cKDTree


def adaptive_control_points(
    contour: np.ndarray, min_pts: int = 20, max_pts: int = 48
) -> np.ndarray:
    """Simplify a closed contour to an adaptive set of editable control points.

    Ramer-Douglas-Peucker (``cv2.approxPolyDP``) keeps more points where the rim
    bends and fewer on smooth arcs. The tolerance is binary-searched so the point
    count lands in ``[min_pts, max_pts]`` - no hardcoded count - giving the fewest
    points that still trace the shape. Used to seed manual boundary editing from
    an auto-detected pupil contour. Returns an ``(M, 2)`` float array of ordered
    boundary points.
    """
    pts = np.asarray(contour, dtype=np.float32).reshape(-1, 1, 2)
    if len(pts) <= min_pts:
        return pts.reshape(-1, 2).astype(float)
    perimeter = cv2.arcLength(pts, True)
    lo, hi = 1e-4 * perimeter, 0.08 * perimeter
    approx = cv2.approxPolyDP(pts, hi, True)
    for _ in range(40):
        eps = 0.5 * (lo + hi)
        approx = cv2.approxPolyDP(pts, eps, True)
        n = len(approx)
        if n > max_pts:
            lo = eps  # coarser -> fewer points
        elif n < min_pts:
            hi = eps  # finer -> more points
        else:
            break
    return approx.reshape(-1, 2).astype(float)


def best_smoothness(
    control_pts: np.ndarray, reference: np.ndarray, hi: float = 0.004, steps: int = 81
) -> float:
    """Pick the smoothness whose smooth curve best matches the detected contour.

    ``control_pts`` are the seed control points; ``reference`` is the original
    detected contour. At smoothness 0 the spline interpolates the sparse control
    points and *overshoots* between them, so it deviates from the detection; a
    tiny smoothness removes that overshoot and tracks the detection better, then
    larger values over-round and drift away. So the deviation has a clear minimum
    at a small smoothness, which this finds by scanning a fine grid over the small
    useful range ``[0, hi]`` (smoothness is scale-invariant). Points are
    angle-ordered to match the fit.
    """
    arr = np.asarray(control_pts, dtype=float).reshape(-1, 2)
    if len(arr) < 4:
        return 0.0
    c0 = arr.mean(axis=0)
    ordered = np.ascontiguousarray(arr[np.argsort(np.arctan2(arr[:, 1] - c0[1], arr[:, 0] - c0[0]))])
    ref = np.asarray(reference, dtype=float).reshape(-1, 2)
    best_s, best_dev = 0.0, np.inf
    for s in np.linspace(0.0, hi, steps):
        curve = smoothing_spline(ordered, float(s))
        if curve is None or len(curve) < 5:
            continue
        dev = float(cKDTree(curve).query(ref)[0].mean())
        if dev < best_dev:
            best_dev, best_s = dev, float(s)
    return best_s


def fit_ellipse(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fit an ellipse to the given set of points.

    Args:
        x (np.array): x-coordinates of the points
        y (np.array): y-coordinates of the points

    Returns:
        np.array: Parameters of the fitted ellipse (xc, yc, a, b, theta)

    """

    def f(c: np.ndarray) -> np.ndarray:
        xc, yc, a, b, theta = c
        distance = (
            ((x - xc) * np.cos(theta) + (y - yc) * np.sin(theta)) ** 2 / a**2
            + ((x - xc) * np.sin(theta) - (y - yc) * np.cos(theta)) ** 2 / b**2
            - 1
        )
        return distance

    x_m, y_m = np.mean(x), np.mean(y)
    center_estimate = x_m, y_m
    a_estimate = np.max(np.abs(x - x_m))
    b_estimate = np.max(np.abs(y - y_m))
    theta_estimate = 0

    estimate = [*center_estimate, a_estimate, b_estimate, theta_estimate]
    result = optimize.minimize(
        lambda c: np.sum(f(c) ** 2),
        estimate,
        method="SLSQP",
        constraints={"type": "ineq", "fun": lambda c: c[2] - c[3]},
    )

    return result.x


def find_closest_point(points: list[QPointF], pos: QPointF, factor: float) -> QPointF | None:
    """Find the closest point to the given position.

    Args:
        points (list): List of QPointF objects representing the points.
        pos (QPointF): The position to check.
        factor (float): The zoom factor.

    Returns:
        QPointF or None: The closest point if within the threshold, otherwise None.

    """
    min_dist = float("inf")
    closest_point = None

    for point in points:
        dist = (point.x() - pos.x()) ** 2 + (point.y() - pos.y()) ** 2
        if dist < min_dist:
            min_dist = dist
            closest_point = point

    # Only select the point if it's within a certain radius
    if min_dist < (10 / factor) ** 2:
        return closest_point
    return None
