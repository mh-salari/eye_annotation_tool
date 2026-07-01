"""Compose two grayscale eye images into an aligned overlay / difference.

Image B is mapped onto image A by a 2x3 affine (translation, or translation +
rotation). The output spans the *union* of A's frame and the transformed B, so a
shifted or rotated B is never cropped. ``compose`` returns a 2-D uint8 grayscale
array, except a heatmap-coloured difference, which is an ``(H, W, 3)`` BGR array.
"""

import cv2
import numpy as np

OVERLAY = "Overlay"
DIFF = "Diff"
VIEWS = (OVERLAY, DIFF)

# A solid mask warps to <255 only on the resampled edge; this keeps a pixel as
# "present" unless it is essentially background.
_PRESENT = 8

# Percentile of the difference mapped to white when scaling (cheshm viz::diff_hot).
_DIFF_VMAX_PERCENTILE = 99.0


def _corners(height: int, width: int, transform: np.ndarray | None) -> np.ndarray:
    """The four image corners, optionally mapped through a 2x3 affine."""
    pts = np.array([[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]])
    if transform is None:
        return pts
    return pts @ transform[:, :2].T + transform[:, 2]


def _place(image: np.ndarray, transform: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Warp ``image`` into a ``size`` canvas by ``transform``; return (canvas, present-mask)."""
    canvas = cv2.warpAffine(image, transform, size)
    mask = cv2.warpAffine(np.full_like(image, 255), transform, size) > _PRESENT
    return canvas, mask


def compare_layout(
    shape_a: tuple[int, ...],
    shape_b: tuple[int, ...],
    transform: np.ndarray,
) -> tuple[tuple[int, int], np.ndarray, np.ndarray]:
    """Union-canvas size and the 2x3 transforms placing A and B without cropping.

    ``transform`` is the 2x3 affine mapping B onto A. The returned ``a_transform``
    and ``b_transform`` map each source image into the shared union canvas, so a
    caller can place each image's annotations at the same offsets ``compose`` uses.
    """
    height_a, width_a = shape_a[:2]
    height_b, width_b = shape_b[:2]
    union = np.vstack([_corners(height_a, width_a, None), _corners(height_b, width_b, transform)])
    min_xy = np.floor(union.min(axis=0)).astype(int)
    max_xy = np.ceil(union.max(axis=0)).astype(int)
    size = (int(max_xy[0] - min_xy[0]), int(max_xy[1] - min_xy[1]))
    offset = -min_xy.astype(float)
    a_transform = np.array([[1.0, 0.0, offset[0]], [0.0, 1.0, offset[1]]])
    b_transform = transform.copy()
    b_transform[:, 2] += offset
    return size, a_transform, b_transform


def compose(
    image_a: np.ndarray,
    image_b: np.ndarray,
    transform: np.ndarray,
    view: str,
    alpha: float,
    diff_colormap: bool = True,
) -> np.ndarray:
    """Composite A and the affine-aligned B over their union frame.

    ``transform`` is the 2x3 affine mapping B onto A. ``alpha`` weights B in the
    overlay. ``diff_colormap`` renders the difference as a warm heatmap (BGR)
    rather than grey.
    """
    size, a_transform, b_transform = compare_layout(image_a.shape, image_b.shape, transform)
    a_canvas, a_mask = _place(image_a, a_transform, size)
    b_canvas, b_mask = _place(image_b, b_transform, size)

    if view == OVERLAY:
        blended = ((1.0 - alpha) * a_canvas + alpha * b_canvas).astype(np.uint8)
        # Where only one image covers a pixel, show that image rather than a
        # half-strength blend against black.
        blended[a_mask & ~b_mask] = a_canvas[a_mask & ~b_mask]
        blended[b_mask & ~a_mask] = b_canvas[b_mask & ~a_mask]
        return blended

    both = a_mask & b_mask
    diff = cv2.absdiff(a_canvas, b_canvas).astype(np.float32)
    diff[~both] = 0.0
    overlap = diff[both]
    # A high percentile rather than the max keeps a few bright outlier pixels (a
    # moved glint) from crushing the subtle differences to black.
    vmax = max(float(np.percentile(overlap, _DIFF_VMAX_PERCENTILE)), 1.0) if overlap.size else 1.0
    # Round to match cheshm's saturate_cast; a plain uint8 cast truncates.
    scaled = np.clip(np.round(diff * (255.0 / vmax)), 0, 255).astype(np.uint8)
    if diff_colormap:
        return cv2.applyColorMap(scaled, cv2.COLORMAP_HOT)
    return scaled
