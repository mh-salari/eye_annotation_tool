"""Pupil + glint + limbus detection.

Public surface:

  - :func:`detect_pupil_and_glints` — main entry point: takes a grayscale eye
    image, returns ``{pupil_contour, pupil_center, pupil_ellipse, glints,
    limbus}``. ``glints`` is a list of ``{contour, center, ellipse}`` dicts;
    ``limbus`` is ``{center, radius}`` or ``None`` if detection failed.
  - :func:`pupil_center_of_mass` — centre-of-mass of the thresholded pupil
    area, with glint holes preserved.
  - :func:`fit_convex_hull_spline` — periodic cubic B-spline through the
    convex hull of a contour.
"""

import operator

import cv2
import numpy as np
from scipy.interpolate import splev, splprep

from .limbus import detect_limbus


def fit_convex_hull_spline(contour: np.ndarray, n_points: int = 200) -> dict:
    """Fit a smooth closed cubic B-spline to the convex hull of a contour.

    Steps:
      1. Take the convex hull of the input contour points.
      2. Fit a periodic cubic B-spline through the hull vertices.
      3. Sample `n_points` evenly along the spline.
      4. Compute the enclosed area and centroid via Green's theorem on the
         sampled curve.

    Returns:
        points: (n_points, 2) sampled spline boundary.
        center: (cx, cy) centroid of the enclosed region.
        equiv_diam: diameter of a circle with the same enclosed area.

    """
    hull_indices = cv2.convexHull(contour, returnPoints=False).squeeze()
    hull_pts = contour.squeeze()[np.sort(hull_indices)]

    pts = np.vstack([hull_pts, hull_pts[0]])
    x, y = pts[:, 0].astype(float), pts[:, 1].astype(float)

    tck, _ = splprep([x, y], s=0, per=True, k=3)
    t = np.linspace(0, 1, n_points)
    sx, sy = splev(t, tck)

    sx_c = np.append(sx, sx[0])
    sy_c = np.append(sy, sy[0])
    cross = sx_c[:-1] * sy_c[1:] - sx_c[1:] * sy_c[:-1]
    signed_area = 0.5 * np.sum(cross)
    area = abs(signed_area)
    if signed_area != 0:
        cx = np.sum((sx_c[:-1] + sx_c[1:]) * cross) / (6 * signed_area)
        cy = np.sum((sy_c[:-1] + sy_c[1:]) * cross) / (6 * signed_area)
    else:
        cx, cy = float(np.mean(sx)), float(np.mean(sy))

    equiv_diam = 2 * np.sqrt(area / np.pi)
    return {
        "points": np.column_stack([sx, sy]),
        "center": (float(cx), float(cy)),
        "equiv_diam": float(equiv_diam),
    }


def detect_glints(img: np.ndarray, mask: np.ndarray, glint_threshold: int = 200) -> list[tuple[float, float]]:
    """Return ``[(x, y), ...]`` glint centroids inside ``mask`` (left-to-right by ``x``).

    Thresholds bright spots above ``glint_threshold`` inside ``mask``, splits
    merged blobs when only 3 are found, returns centroids in image coordinates.
    """
    masked = img.copy()
    masked[mask == 0] = 0

    _, glint_mask = cv2.threshold(masked, glint_threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(glint_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    blobs = []
    for c in contours:
        if c.squeeze().ndim < 2:
            continue
        m = cv2.moments(c)
        if m["m00"] > 0:
            blobs.append({
                "contour": c,
                "cx": m["m10"] / m["m00"],
                "cy": m["m01"] / m["m00"],
                "w": cv2.boundingRect(c)[2],
            })
    blobs.sort(key=operator.itemgetter("cx"))

    # if 3 blobs, split the widest one at its horizontal midpoint
    if len(blobs) == 3:
        widths = [b["w"] for b in blobs]
        median_w = sorted(widths)[1]
        widest_idx = np.argmax(widths)
        if widths[widest_idx] > 1.3 * median_w:
            wide = blobs.pop(widest_idx)
            bx, by, bw, bh = cv2.boundingRect(wide["contour"])
            mid_x = bx + bw // 2
            for x_range in [(bx, mid_x), (mid_x, bx + bw)]:
                half_mask = np.zeros_like(glint_mask)
                half_mask[by : by + bh, x_range[0] : x_range[1]] = glint_mask[by : by + bh, x_range[0] : x_range[1]]
                half_contours, _ = cv2.findContours(half_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for hc in half_contours:
                    hm = cv2.moments(hc)
                    if hm["m00"] > 0:
                        blobs.append({
                            "contour": hc,
                            "cx": hm["m10"] / hm["m00"],
                            "cy": hm["m01"] / hm["m00"],
                            "w": cv2.boundingRect(hc)[2],
                        })
            blobs.sort(key=operator.itemgetter("cx"))

    return [(b["cx"], b["cy"]) for b in blobs]


def _touches_border(contour: np.ndarray, shape: tuple[int, ...]) -> bool:
    h, w = shape[:2]
    x, y, cw, ch = cv2.boundingRect(contour)
    return x == 0 or y == 0 or x + cw == w or y + ch == h


def pupil_center_of_mass(
    pupil_mask: np.ndarray,
    pupil_contour: np.ndarray,
) -> tuple[float, float] | None:
    """Centre-of-mass of the thresholded pupil area, with glint cutouts preserved.

    The glint creates a hole in the dark pupil region; that hole biases the
    centroid away from the glint side. A convex-hull-based centroid fills the
    hole in and therefore lands somewhere different.

    Returns ``None`` if the pupil mass is zero (degenerate input).
    """
    contour_mask = np.zeros_like(pupil_mask)
    cv2.drawContours(contour_mask, [pupil_contour], -1, 255, thickness=cv2.FILLED)
    pupil_only = cv2.bitwise_and(pupil_mask, contour_mask)
    m = cv2.moments(pupil_only, binaryImage=True)
    if m["m00"] == 0:
        return None
    return (m["m10"] / m["m00"], m["m01"] / m["m00"])


def _roi_mask(shape: tuple[int, ...], roi: tuple[int, int, int, int]) -> np.ndarray:
    """Build a uint8 mask with the ``(x, y, w, h)`` rectangle set to 255."""
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    rx, ry, rw, rh = (int(v) for v in roi)
    rx, ry = max(rx, 0), max(ry, 0)
    rw, rh = max(min(rw, w - rx), 0), max(min(rh, h - ry), 0)
    if rw and rh:
        mask[ry : ry + rh, rx : rx + rw] = 255
    return mask


def detect_pupil_and_glints(
    img: np.ndarray,
    pupil_threshold: int = 30,
    glint_threshold: int = 240,
    glint_margin_ratio: float = 0.1,
    glints_target: int = 1,
    glint_max_area_ratio: float = 0.1,
    pupil_center_method: str = "convex_hull_centroid",
    pupil_roi: tuple[int, int, int, int] | None = None,
    glint_roi: tuple[int, int, int, int] | None = None,
) -> dict:
    """Detect the pupil contour, the limbus circle, and glint contours in a grayscale eye image.

    Returns ``{pupil_contour, pupil_center, pupil_ellipse, glints, limbus}``.
    ``pupil_ellipse`` is ``((cx, cy), (w, h), angle)``; the centre is chosen
    by ``pupil_center_method`` and ``(w, h, angle)`` come from
    ``cv2.fitEllipse`` on the convex hull of the pupil contour. Border-
    touching dark contours are rejected so the pupil is always an interior
    region. ``limbus`` is ``{"center": [lx, ly], "radius": r}`` or ``None``
    if Daugman IDO did not converge.

    ``pupil_center_method``:
      - ``"convex_hull_centroid"`` (default): centroid of the cubic B-spline
        fitted through the convex hull of the pupil contour.
      - ``"center_of_mass"``: moment-based centre of the thresholded pupil
        area, with glint holes preserved (matches EyeLink Centroid mode).
      - ``"ellipse_fit_center"``: centre from ``cv2.fitEllipse`` on the hull.
        Requires the hull to have at least 5 points.

    ``glint_margin_ratio`` is signed: positive expands the glint search
    region outward into the iris, negative shrinks it inward.
      - 0.0  -> search region = pupil boundary
      - +X   -> dilate by ``X * (limbus_radius - pupil_radius)`` pixels,
                so +1.0 reaches the limbus. Falls back to scaling by
                ``pupil_radius`` when limbus detection fails.
      - -X   -> erode by ``X * pupil_radius`` pixels, so -1.0 collapses
                to the pupil centre.

    ``glints_target`` is the number of physical IR LEDs in the rig. When 1
    (default), every bright blob inside the search region is unioned into a
    single centroid so a saturated reflection split across contours still
    yields one glint.

    ``glint_max_area_ratio`` rejects bright contours whose area exceeds this
    fraction of the pupil area — a guard against skin / eyelid bleed-through
    above ``glint_threshold``.

    ``pupil_roi`` and ``glint_roi`` are optional ``(x, y, w, h)`` rectangles.
    When set, the corresponding search is confined to that rectangle;
    ``glint_roi`` also overrides the pupil-mask + margin constraint.
    """
    _, pupil_mask = cv2.threshold(img, pupil_threshold, 255, cv2.THRESH_BINARY_INV)
    if pupil_roi is not None:
        pupil_mask = cv2.bitwise_and(pupil_mask, _roi_mask(img.shape, pupil_roi))
    contours, _ = cv2.findContours(pupil_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    interior = [c for c in contours if not _touches_border(c, img.shape)]
    if not interior:
        raise ValueError(
            "no interior pupil contour at this threshold — raise pupil_threshold or "
            "check the frame for a dark border covering the whole image",
        )
    pupil_contour = max(interior, key=cv2.contourArea)
    hull = cv2.convexHull(pupil_contour)
    # The spline is computed even when an alternative centre is chosen so the
    # area-equivalent diameter is available as a fallback when the hull has
    # too few points for cv2.fitEllipse.
    spline = fit_convex_hull_spline(pupil_contour)
    # cv2.fitEllipse is computed once and reused for both the (w, h, angle)
    # tuple and the ``ellipse_fit_center`` method.
    ellipse_fit = cv2.fitEllipse(hull) if len(hull) >= 5 else None

    if pupil_center_method == "center_of_mass":
        com = pupil_center_of_mass(pupil_mask, pupil_contour)
        if com is None:
            raise ValueError("center-of-mass: zero pupil mass")
        cx, cy = com
    elif pupil_center_method == "convex_hull_centroid":
        cx, cy = spline["center"]
    elif pupil_center_method == "ellipse_fit_center":
        if ellipse_fit is None:
            raise ValueError("ellipse_fit_center: hull has fewer than 5 points, cv2.fitEllipse unavailable")
        (cx, cy), _, _ = ellipse_fit
    else:
        raise ValueError(
            f"unknown pupil_center_method {pupil_center_method!r}; "
            f"expected 'convex_hull_centroid', 'center_of_mass', or 'ellipse_fit_center'",
        )
    pupil_center = (round(cx), round(cy))
    if ellipse_fit is not None:
        _, (w, h), angle = ellipse_fit
    else:
        w = h = spline["equiv_diam"]
        angle = 0.0
    pupil_ellipse = ((cx, cy), (w, h), angle)
    pupil_area = float(cv2.contourArea(pupil_contour))
    glint_max_area = pupil_area * glint_max_area_ratio

    # Glints: bright regions inside (or near the edge of) the pupil
    _, glint_mask = cv2.threshold(img, glint_threshold, 255, cv2.THRESH_BINARY)

    # Glint search region: explicit ROI overrides the pupil-mask + dilation default.
    # Dilation is expressed as a fraction of the detected pupil radius so the
    # tuning value transfers across image resolutions.
    # Limbus detection: needed for the positive glint_margin_ratio direction
    # (scaling by iris ring width). Best-effort — failure leaves limbus=None
    # and falls back to a pupil-radius scale for any positive margin.
    pupil_radius = max(w, h) / 2
    limbus = None
    try:
        (lcx, lcy), lr = detect_limbus(img, (cx, cy), pupil_radius)
        limbus = {"center": [float(lcx), float(lcy)], "radius": float(lr)}
    except Exception:
        pass

    if glint_roi is not None:
        glint_search_mask = _roi_mask(img.shape, glint_roi)
    else:
        glint_search_mask = np.zeros_like(glint_mask)
        cv2.drawContours(glint_search_mask, [pupil_contour], -1, 255, thickness=cv2.FILLED)
        if glint_margin_ratio > 0:
            # Expand outward in iris-ring units so +100% reaches the limbus.
            # If limbus detection failed, fall back to pupil_radius as the scale.
            ring_px = (
                max(limbus["radius"] - pupil_radius, 0.0)
                if limbus is not None
                else pupil_radius
            )
            glint_margin_px = int(round(glint_margin_ratio * ring_px))
            if glint_margin_px > 0:
                k = 2 * glint_margin_px + 1
                glint_search_mask = cv2.dilate(
                    glint_search_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
                )
        elif glint_margin_ratio < 0:
            # Shrink inward in pupil-radius units so -100% collapses to the centre.
            erosion_px = int(round(-glint_margin_ratio * pupil_radius))
            if erosion_px > 0:
                k = 2 * erosion_px + 1
                glint_search_mask = cv2.erode(
                    glint_search_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
                )

    if glints_target == 1:
        # Single-LED rig: union every bright blob inside the search region into
        # one centroid (a saturated, irregular reflection can split into multiple
        # contours that still belong to the same physical LED).
        inside_mask = cv2.bitwise_and(glint_mask, glint_search_mask)
        inside_contours, _ = cv2.findContours(inside_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inside_contours = [c for c in inside_contours if cv2.contourArea(c) <= glint_max_area]
        if inside_contours:
            union = np.zeros_like(glint_mask)
            cv2.drawContours(union, inside_contours, -1, 255, thickness=cv2.FILLED)
            u_contours, _ = cv2.findContours(union, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            filtered = [max(u_contours, key=cv2.contourArea)] if u_contours else []
        else:
            filtered = []
    else:
        # Multi-LED: keep every bright blob whose centroid lands inside the
        # search region and whose area is below the max.
        glint_contours, _ = cv2.findContours(glint_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filtered = []
        for c in glint_contours:
            if cv2.contourArea(c) > glint_max_area:
                continue
            gm = cv2.moments(c)
            if gm["m00"] > 0:
                cx = int(gm["m10"] / gm["m00"])
                cy = int(gm["m01"] / gm["m00"])
                if (
                    0 <= cy < glint_search_mask.shape[0]
                    and 0 <= cx < glint_search_mask.shape[1]
                    and glint_search_mask[cy, cx] == 255
                ):
                    filtered.append(c)

    if glints_target == 4 and len(filtered) == 3:
        # 4-LED rig fallback: if one blob spans two LEDs horizontally, split it.
        widths = [cv2.boundingRect(c)[2] for c in filtered]
        median_w = sorted(widths)[1]
        widest_idx = np.argmax(widths)
        if widths[widest_idx] > 1.5 * median_w:
            wide_c = filtered.pop(widest_idx)
            x, y, w, h = cv2.boundingRect(wide_c)
            roi = glint_mask[y : y + h, x : x + w].copy()
            left_roi = roi.copy()
            left_roi[:, w // 2 :] = 0
            right_roi = roi.copy()
            right_roi[:, : w // 2] = 0
            for half_roi, offset_x in [(left_roi, x), (right_roi, x)]:
                half_contours, _ = cv2.findContours(half_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for hc in half_contours:
                    hc[:, :, 0] += offset_x
                    hc[:, :, 1] += y
                    filtered.append(hc)

    glints = []
    for c in filtered:
        gm = cv2.moments(c)
        if gm["m00"] > 0:
            cx = int(gm["m10"] / gm["m00"])
            cy = int(gm["m01"] / gm["m00"])
            ellipse = cv2.fitEllipse(c) if len(c) >= 5 else None
            glints.append({"contour": c, "center": (cx, cy), "ellipse": ellipse})

    return {
        "pupil_contour": pupil_contour,
        "pupil_center": pupil_center,
        "pupil_ellipse": pupil_ellipse,
        "glints": glints,
        "limbus": limbus,
    }
