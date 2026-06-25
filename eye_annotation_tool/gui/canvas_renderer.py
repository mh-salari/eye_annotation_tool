"""Image-viewer canvas renderer.

The renderer is a pure paint orchestrator: it reads the current state
of the various stores + the per-paint :class:`CanvasGeometry` and
produces a final :class:`QPixmap` for the widget's :class:`QLabel`.
Stateless across calls — the per-image geometry is supplied by the
widget on each render.

Detection overlays are drawn from each kind's :class:`DetectorCard`
overlay state (visibility / colour / alpha / thickness per overlay
key), not from per-plugin draw methods.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt5.QtCore import QPointF, QSizeF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

from ..state import EyeDataStore, OverlayStore, TargetRoiStore
from ..utils.project_settings import DETECTOR_MANUAL, DETECTOR_OFF
from .brightness_controller import BrightnessController
from .zoom_controller import ZoomController


@dataclass
class AnnotationColors:
    """RGBA colours for every manual / overlay layer the renderer paints."""

    pupil: QColor
    pupil_select: QColor
    pupil_ellipse: QColor
    pupil_center: QColor
    limbus: QColor
    limbus_select: QColor
    limbus_ellipse: QColor
    eyelid: QColor
    eyelid_select: QColor
    eyelid_ellipse: QColor
    glint: QColor
    glint_select: QColor
    purkinje_iv: QColor
    purkinje_iv_select: QColor
    divider: QColor
    inactive_eye_dim: QColor
    fallback_roi: QColor

    @classmethod
    def default(cls) -> "AnnotationColors":
        """Return the canonical palette used by ImageViewer."""
        return cls(
            pupil=QColor(150, 213, 116, 255),
            pupil_select=QColor(249, 248, 113, 255),
            pupil_ellipse=QColor(25, 145, 50, 255),
            pupil_center=QColor(180, 240, 80, 255),
            limbus=QColor(194, 149, 188, 255),
            limbus_select=QColor(249, 178, 208, 255),
            limbus_ellipse=QColor(139, 122, 162, 255),
            eyelid=QColor(0, 155, 201, 255),
            eyelid_select=QColor(0, 189, 194, 255),
            eyelid_ellipse=QColor(0, 118, 195, 255),
            glint=QColor(255, 165, 0, 255),
            glint_select=QColor(255, 215, 0, 255),
            purkinje_iv=QColor(0, 220, 220, 255),
            purkinje_iv_select=QColor(120, 255, 255, 255),
            divider=QColor(255, 255, 255, 230),
            inactive_eye_dim=QColor(0, 0, 0, 120),
            fallback_roi=QColor(255, 255, 255, 200),
        )


@dataclass
class CanvasGeometry:
    """Per-paint snapshot of widget-side state the renderer needs."""

    current_eye: str
    binocular_mode: bool
    divider_x_image: float
    current_annotation: str
    selected_point: QPointF | None


_POINT_FIELD_BY_ANNOTATION: dict[str, str] = {
    "pupil": "pupil_points",
    "limbus": "limbus_points",
    "eyelid_contour": "eyelid_contour_points",
    "glint": "glint_points",
    "purkinje_iv": "purkinje_iv_points",
}

# Manual annotation slug to the detector kind the card lives under.
# The renderer reads each kind's manual overlay state via the lookup
# callable to colour + size points / ellipses / centres.
_KIND_BY_ANNOTATION: dict[str, str] = {
    "pupil": "pupil",
    "limbus": "limbus",
    "eyelid_contour": "eyelid",
    "glint": "glint",
    "purkinje_iv": "purkinje_iv",
}


# Maps ``(kind, detector_id)`` to that detector's overlay state
# (``{overlay_key: {show, color, alpha, thickness, type}}``), or ``None``.
OverlayStateLookup = Callable[[str, str], dict[str, dict] | None]
# Maps ``(eye slot, kind)`` to the detector id that eye uses for the kind.
SelectionLookup = Callable[[str, str], str]


class CanvasRenderer:
    """Composite paint orchestrator for the image-viewer canvas."""

    def __init__(
        self,
        colors: AnnotationColors,
        eye_data_store: EyeDataStore,
        overlay_store: OverlayStore,
        roi_store: TargetRoiStore,
        overlay_state_lookup: OverlayStateLookup,
        selection_lookup: SelectionLookup,
        brightness: BrightnessController,
        zoom: ZoomController,
    ) -> None:
        """Wire dependencies; the widget keeps these references in sync."""
        self.colors = colors
        self.eye_data_store = eye_data_store
        self.overlay_store = overlay_store
        self.roi_store = roi_store
        self.overlay_state_lookup = overlay_state_lookup
        self.selection_lookup = selection_lookup
        self.brightness = brightness
        self.zoom = zoom
        # Cache the zoom-scaled source pixmap so a slider drag (which
        # triggers many repaints with unchanged zoom + brightness) skips
        # the per-frame QPixmap.scaled allocation.
        self._scaled_source: QPixmap | None = None
        self._scaled_signature: tuple | None = None

    # ---------------------------------------------------------------------------
    # Single entry point
    # ---------------------------------------------------------------------------

    def render(self, original_pixmap: QPixmap | None, geometry: CanvasGeometry) -> QPixmap | None:
        """Build and return the painted canvas pixmap, or ``None`` when no image is loaded."""
        if original_pixmap is None or original_pixmap.isNull():
            return None
        source_pixmap = self.brightness.display_pixmap(original_pixmap)
        scaled_pixmap = self._scaled_source_for(source_pixmap)
        # Before the viewport is laid out the zoom factor can collapse to ~0,
        # making the scaled pixmap null; painting on it would spam QPainter /
        # QFont warnings. Skip until there is a real size to draw into.
        if scaled_pixmap.isNull():
            return None
        canvas = QPixmap(scaled_pixmap.size())
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, scaled_pixmap)

        self._draw_eye_annotations(painter, "left", geometry)
        if geometry.binocular_mode:
            self._draw_eye_annotations(painter, "right", geometry)

        self._draw_detection_overlays(painter)
        self._draw_target_rois(painter, geometry)
        if geometry.binocular_mode:
            self._draw_inactive_half_dim(painter, canvas, geometry)
            self._draw_divider(painter, canvas, geometry)

        painter.end()
        return canvas

    def _scaled_source_for(self, source_pixmap: QPixmap) -> QPixmap:
        """Return ``source_pixmap`` scaled to the current zoom, reusing the cached copy when valid."""
        signature = (source_pixmap.cacheKey(), self.zoom.factor)
        if self._scaled_signature == signature and self._scaled_source is not None:
            return self._scaled_source
        scaled = source_pixmap.scaled(
            source_pixmap.size() * self.zoom.factor,
            Qt.KeepAspectRatio,
            Qt.FastTransformation,
        )
        self._scaled_source = scaled
        self._scaled_signature = signature
        return scaled

    # ---------------------------------------------------------------------------
    # Manual annotation layers
    # ---------------------------------------------------------------------------

    def _draw_eye_annotations(self, painter: QPainter, eye: str, geometry: CanvasGeometry) -> None:
        """Paint the manual point + ellipse layers for ``eye``."""
        eye_data = self.eye_data_store.eye_data[eye]
        self._draw_points_for_eye(painter, eye_data, eye, geometry)
        self._draw_ellipses_for_eye(painter, eye_data, eye, geometry)

    def _draw_points_for_eye(
        self,
        painter: QPainter,
        eye_data: dict,
        eye: str,
        geometry: CanvasGeometry,
    ) -> None:
        """Render every manual point list for one eye, with selection highlight on the active eye."""
        is_active = eye == geometry.current_eye
        slot = self._slot_for_eye(eye, geometry)
        eye_label = "L" if eye == "left" else "R"
        defaults = (
            (eye_data["pupil_points"], self.colors.pupil, self.colors.pupil_select, "pupil"),
            (eye_data["limbus_points"], self.colors.limbus, self.colors.limbus_select, "limbus"),
            (eye_data["eyelid_contour_points"], self.colors.eyelid, self.colors.eyelid_select, "eyelid_contour"),
            (eye_data["glint_points"], self.colors.glint, self.colors.glint_select, "glint"),
            (
                eye_data["purkinje_iv_points"],
                self.colors.purkinje_iv,
                self.colors.purkinje_iv_select,
                "purkinje_iv",
            ),
        )
        for points, default_color, select_color, annotation_type in defaults:
            if not self._is_manual(slot, _KIND_BY_ANNOTATION[annotation_type]):
                continue
            color, size, alpha_color = self._manual_point_style(annotation_type, default_color)
            if color is None:  # "points" overlay hidden in the card
                continue
            for point in points:
                scaled_point = QPointF(point.x() * self.zoom.factor, point.y() * self.zoom.factor)
                highlighted = (
                    is_active and point == geometry.selected_point and geometry.current_annotation == annotation_type
                )
                pen_color = select_color if highlighted else alpha_color
                painter.setPen(QPen(pen_color, max(1, int(size * 2)), Qt.SolidLine))
                painter.drawEllipse(scaled_point, float(size), float(size))
                if geometry.binocular_mode:
                    font = painter.font()
                    font.setPointSize(8)
                    painter.setFont(font)
                    painter.setPen(QPen(color, 1, Qt.SolidLine))
                    text_pos = QPointF(scaled_point.x() + 6, scaled_point.y() - 4)
                    painter.drawText(text_pos, eye_label)

    def _draw_ellipses_for_eye(self, painter: QPainter, eye_data: dict, eye: str, geometry: CanvasGeometry) -> None:
        """Render the fitted pupil + limbus geometry (smooth curve or ellipse) for one eye."""
        slot = self._slot_for_eye(eye, geometry)
        for annotation_type, ellipse_field, curve_field, default_outline, default_center in (
            ("pupil", "pupil_ellipse", "pupil_fit_curve", self.colors.pupil_ellipse, self.colors.pupil_center),
            ("limbus", "limbus_ellipse", "limbus_fit_curve", self.colors.limbus_ellipse, self.colors.limbus_ellipse),
        ):
            if not self._is_manual(slot, annotation_type):
                continue
            ellipse = eye_data.get(ellipse_field)
            curve = eye_data.get(curve_field)
            if not ellipse and not curve:
                continue
            outline_color, outline_thickness, outline_style = self._manual_line_style(
                annotation_type, "ellipse", default_outline
            )
            if outline_color is not None:
                painter.setPen(self._make_pen(outline_color, outline_thickness, outline_style))
                painter.setBrush(Qt.NoBrush)
                # Smooth-curve mode draws the actual boundary through the points;
                # ellipse mode draws the fitted conic.
                if curve:
                    self._draw_closed_curve(painter, curve)
                elif ellipse:
                    self._draw_single_ellipse(painter, ellipse)
            center_color, center_size = self._manual_center_style(annotation_type, default_center)
            if center_color is not None and ellipse:
                self._draw_ellipse_center(painter, ellipse, center_color, center_size)

    def _draw_closed_curve(self, painter: QPainter, curve: list) -> None:
        """Render a closed boundary polyline at the current zoom factor."""
        if len(curve) < 2:
            return
        poly = QPolygonF([QPointF(p.x() * self.zoom.factor, p.y() * self.zoom.factor) for p in curve])
        painter.drawPolygon(poly)

    def _draw_single_ellipse(self, painter: QPainter, ellipse: tuple | None) -> None:
        """Render one rotated ellipse outline at the current zoom factor."""
        if ellipse is None:
            return
        center, size, angle = ellipse
        scaled_center = QPointF(center.x() * self.zoom.factor, center.y() * self.zoom.factor)
        scaled_size = QSizeF(size.width() * self.zoom.factor, size.height() * self.zoom.factor)
        painter.save()
        painter.translate(scaled_center)
        painter.rotate(angle)
        painter.drawEllipse(QPointF(0, 0), scaled_size.width() / 2, scaled_size.height() / 2)
        painter.restore()

    def _draw_ellipse_center(
        self,
        painter: QPainter,
        ellipse: tuple,
        color: QColor,
        size: float = 2.0,
    ) -> None:
        """Render a small filled dot at the centre of a manually fitted ellipse."""
        if ellipse is None:
            return
        center, _size, _angle = ellipse
        scaled = QPointF(center.x() * self.zoom.factor, center.y() * self.zoom.factor)
        painter.save()
        painter.setBrush(color)
        painter.setPen(QPen(color, 1, Qt.SolidLine))
        painter.drawEllipse(scaled, float(size), float(size))
        painter.restore()

    # ---------------------------------------------------------------------------
    # Manual overlay style lookup: per (kind, overlay-key) from the card
    # ---------------------------------------------------------------------------

    def _manual_point_style(
        self,
        annotation_type: str,
        default_color: QColor,
    ) -> tuple[QColor | None, float, QColor]:
        """Return ``(base_color, point_radius, alpha_blended_color)`` for a points overlay.

        ``None`` for ``base_color`` means the card has the "points"
        overlay hidden and the caller should skip drawing.
        """
        entry = self._manual_entry(annotation_type, "points")
        if entry is None:
            return default_color, 1.5, default_color
        if not entry.get("show", True):
            return None, 0.0, default_color
        color = QColor(entry.get("color", default_color))
        alpha = float(entry.get("alpha", 1.0))
        blended = self._with_alpha(color, alpha)
        size = float(entry.get("thickness", 2)) / 2.0 + 0.5
        return color, max(size, 0.5), blended

    def _manual_line_style(
        self,
        annotation_type: str,
        key: str,
        default_color: QColor,
    ) -> tuple[QColor | None, int, str]:
        """Return ``(color, thickness, style)`` for a line-type overlay (e.g. fitted ellipse)."""
        entry = self._manual_entry(annotation_type, key)
        if entry is None:
            return default_color, 1, "solid"
        if not entry.get("show", True):
            return None, 1, "solid"
        color = self._with_alpha(QColor(entry.get("color", default_color)), float(entry.get("alpha", 1.0)))
        return color, max(1, int(entry.get("thickness", 1))), entry.get("style", "solid")

    @staticmethod
    def _make_pen(color: QColor, thickness: float, style: str) -> QPen:
        """Build a line-overlay pen; ``dash`` uses a wide-gap custom dash pattern.

        Qt's stock ``DashLine`` packs the dashes too tightly to read on a thin
        boundary, so the dashed style sets an explicit dash:gap pattern (in
        pen-width units) with the gap twice the dash length.
        """
        pen = QPen(color, thickness, Qt.SolidLine)
        if style == "dash":
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([3.0, 6.0])
        return pen

    def _manual_center_style(
        self,
        annotation_type: str,
        default_color: QColor,
    ) -> tuple[QColor | None, float]:
        """Return ``(color, dot_radius)`` for the ellipse-centre marker."""
        entry = self._manual_entry(annotation_type, "center")
        if entry is None:
            return default_color, 2.0
        if not entry.get("show", True):
            return None, 0.0
        color = self._with_alpha(QColor(entry.get("color", default_color)), float(entry.get("alpha", 1.0)))
        size = float(entry.get("thickness", 2))
        return color, max(size, 1.0)

    def _manual_entry(self, annotation_type: str, key: str) -> dict | None:
        """Look up a manual overlay entry by (annotation type, overlay key)."""
        kind = _KIND_BY_ANNOTATION.get(annotation_type)
        if kind is None:
            return None
        overlay_state = self.overlay_state_lookup(kind, DETECTOR_MANUAL)
        if not overlay_state:
            return None
        return overlay_state.get(key)

    # ---------------------------------------------------------------------------
    # Auto Detect overlays — generic walk over result + overlay-state
    # ---------------------------------------------------------------------------

    def _draw_detection_overlays(self, painter: QPainter) -> None:
        """Render each eye's Auto Detect overlay styled by that eye's own detector."""
        for kind, slot, result in self.overlay_store.items_for_paint():
            detector_id = self.selection_lookup(slot, kind)
            if detector_id in {DETECTOR_OFF, DETECTOR_MANUAL}:
                continue
            overlay_state = self.overlay_state_lookup(kind, detector_id)
            if overlay_state is None:
                continue
            self._draw_one_result(painter, kind, result, overlay_state)

    def _draw_one_result(
        self,
        painter: QPainter,
        kind: str,
        result: dict,
        overlay_state: dict[str, dict],
    ) -> None:
        """Walk one detection result and paint each overlay key the user has visible."""
        if "glints" in result and isinstance(result["glints"], list):
            self._draw_glint_list(painter, result["glints"], overlay_state)
            return

        for key, entry in overlay_state.items():
            if not entry.get("show", True):
                continue
            elem_type = entry.get("type", "line")
            color = self._with_alpha(entry["color"], float(entry.get("alpha", 1.0)))
            thickness = int(entry.get("thickness", 1) or 1)
            style = entry.get("style", "solid")
            if elem_type == "fill":
                # "mask" overlays paint the result's contour filled (or
                # the limbus polygon filled), not the binary mask ndarray.
                if key == "mask" and result.get("contour") is not None:
                    self._draw_fill_polygon(painter, result["contour"], color)
                elif key == "mask" and "R_theta" in result and result.get("center") is not None:
                    polygon = self._limbus_polygon_points(result)
                    if polygon is not None:
                        self._draw_fill_polygon(painter, polygon, color)
                continue
            value = result.get(key)
            if value is None:
                if key == "curve" and result.get("center") is not None:
                    self._draw_limbus_curve(painter, result, color, thickness, style)
                continue
            if key == "contour":
                self._draw_contour(painter, value, color, thickness, style)
            elif key == "ellipse":
                self._draw_ellipse_outline(painter, value, color, thickness, style)
            elif key == "center":
                self._draw_point(painter, value, color, thickness)
            elif key == "curve":
                self._draw_limbus_curve(painter, result, color, thickness, style)
            _ = kind  # parametrise future per-kind branches without losing the value

    def _draw_glint_list(
        self,
        painter: QPainter,
        glints: list[dict],
        overlay_state: dict[str, dict],
    ) -> None:
        """Render the per-glint list emitted by the threshold glint detector."""
        for key, entry in overlay_state.items():
            if not entry.get("show", True):
                continue
            elem_type = entry.get("type", "line")
            color = self._with_alpha(entry["color"], float(entry.get("alpha", 1.0)))
            thickness = int(entry.get("thickness", 1) or 1)
            for g in glints:
                if elem_type == "fill":
                    # Glint "mask" overlay = each glint's contour filled.
                    contour = g.get("contour")
                    if contour is not None:
                        self._draw_fill_polygon(painter, contour, color)
                    continue
                value = g.get(key)
                if value is None:
                    continue
                if key == "contour":
                    self._draw_contour(painter, value, color, thickness)
                elif key == "center":
                    self._draw_point(painter, value, color, thickness)

    # ----- low-level primitives -----

    @staticmethod
    def _with_alpha(color: QColor, alpha01: float) -> QColor:
        out = QColor(color)
        out.setAlpha(int(max(0.0, min(1.0, alpha01)) * 255))
        return out

    def _draw_point(self, painter: QPainter, center: tuple | list, color: QColor, size: int) -> None:
        cx, cy = float(center[0]), float(center[1])
        painter.save()
        painter.setBrush(color)
        painter.setPen(QPen(color, 1, Qt.SolidLine))
        painter.drawEllipse(QPointF(cx * self.zoom.factor, cy * self.zoom.factor), float(size), float(size))
        painter.restore()

    def _draw_contour(
        self, painter: QPainter, contour: object, color: QColor, thickness: int, style: str = "solid"
    ) -> None:
        pts = self._contour_to_points(contour)
        if pts is None or len(pts) < 2:
            return
        polygon = self._polygon_from_points(pts)
        painter.save()
        painter.setPen(self._make_pen(color, thickness, style))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(polygon)
        painter.restore()

    def _draw_ellipse_outline(
        self, painter: QPainter, ellipse: object, color: QColor, thickness: int, style: str = "solid"
    ) -> None:
        center, size, angle = self._normalise_ellipse(ellipse)
        if center is None:
            return
        painter.save()
        painter.setPen(self._make_pen(color, thickness, style))
        painter.setBrush(Qt.NoBrush)
        painter.translate(QPointF(center[0] * self.zoom.factor, center[1] * self.zoom.factor))
        painter.rotate(float(angle))
        painter.drawEllipse(
            QPointF(0, 0),
            float(size[0]) / 2 * self.zoom.factor,
            float(size[1]) / 2 * self.zoom.factor,
        )
        painter.restore()

    def _draw_limbus_curve(
        self, painter: QPainter, result: dict, color: QColor, thickness: int, style: str = "solid"
    ) -> None:
        cx, cy = float(result["center"][0]), float(result["center"][1])
        if "R_theta" in result and "thetas" in result:
            thetas = np.asarray(result["thetas"])
            radii = np.asarray(result["R_theta"])
            xs = cx + radii * np.cos(thetas)
            ys = cy + radii * np.sin(thetas)
            pts = np.stack([xs, ys], axis=-1)
            self._draw_contour(painter, pts, color, thickness, style)
        elif "radius" in result:
            radius = float(result["radius"])
            painter.save()
            painter.setPen(self._make_pen(color, thickness, style))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(
                QPointF(cx * self.zoom.factor, cy * self.zoom.factor),
                radius * self.zoom.factor,
                radius * self.zoom.factor,
            )
            painter.restore()

    def _draw_fill_polygon(self, painter: QPainter, contour: object, color: QColor) -> None:
        pts = self._contour_to_points(contour)
        if pts is None or len(pts) < 3:
            return
        polygon = self._polygon_from_points(pts)
        painter.save()
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(polygon)
        painter.restore()

    def _polygon_from_points(self, pts: np.ndarray) -> QPolygonF:
        """Build a scaled :class:`QPolygonF` from an Nx2 point array in one pass."""
        scale = self.zoom.factor
        return QPolygonF([QPointF(float(p[0]) * scale, float(p[1]) * scale) for p in pts])

    @staticmethod
    def _limbus_polygon_points(result: dict) -> np.ndarray | None:
        """Build the limbus boundary polygon from an ``R_theta`` / ``thetas`` result."""
        center = result.get("center")
        thetas = result.get("thetas")
        radii = result.get("R_theta")
        if center is None or thetas is None or radii is None:
            return None
        cx, cy = float(center[0]), float(center[1])
        thetas = np.asarray(thetas)
        radii = np.asarray(radii)
        xs = cx + radii * np.cos(thetas)
        ys = cy + radii * np.sin(thetas)
        return np.stack([xs, ys], axis=-1)

    @staticmethod
    def _contour_to_points(contour: object) -> np.ndarray | None:
        if contour is None:
            return None
        if not isinstance(contour, np.ndarray):
            if not (isinstance(contour, (list, tuple)) and contour and isinstance(contour[0], (list, tuple))):
                return None
            contour = np.asarray(contour)
        # Accept both OpenCV's (N, 1, 2) and a plain (N, 2) — whether the
        # contour came straight from a detector or was reloaded from JSON.
        if contour.ndim == 3 and contour.shape[1] == 1 and contour.shape[2] == 2:
            return contour.reshape(-1, 2)
        if contour.ndim == 2 and contour.shape[1] == 2:
            return contour
        return None

    @staticmethod
    def _normalise_ellipse(ellipse: object) -> tuple:
        if isinstance(ellipse, dict):
            center = ellipse.get("center")
            size = ellipse.get("size")
            angle = ellipse.get("angle", 0.0)
            if center is None or size is None:
                return (None, None, 0.0)
            return (tuple(center), tuple(size), float(angle))
        if isinstance(ellipse, (list, tuple)) and len(ellipse) == 3:
            return (tuple(ellipse[0]), tuple(ellipse[1]), float(ellipse[2]))
        return (None, None, 0.0)

    # ---------------------------------------------------------------------------
    # Per-kind ROI rectangles
    # ---------------------------------------------------------------------------

    def _draw_target_rois(self, painter: QPainter, geometry: CanvasGeometry) -> None:
        """Render every stored per-eye ROI rectangle.

        Only the active eye's rectangle for the active drag-edit kind
        gets corner handles — the inactive eye's rectangle paints plain
        so the user sees their saved ROI without it tempting edit
        attempts.
        """
        active_slot = self._active_eye_slot(geometry)
        active_target = self.roi_store.active_target
        for kind, slot, roi in self.roi_store.items_for_paint():
            color = self._roi_color(kind, slot)
            is_active = kind == active_target and slot == active_slot
            self._draw_target_roi_box(painter, roi, color, active=is_active)

    def _roi_color(self, kind: str, slot: str) -> QColor:
        """Colour ``slot``'s ROI rectangle from its detector's first line/point overlay, or the fallback."""
        overlay_state = self.overlay_state_lookup(kind, self.selection_lookup(slot, kind))
        if not overlay_state:
            return self.colors.fallback_roi
        for entry in overlay_state.values():
            if entry.get("type") in {"line", "point"}:
                return QColor(entry["color"])
        return self.colors.fallback_roi

    def _draw_target_roi_box(
        self,
        painter: QPainter,
        roi: tuple,
        color: QColor,
        *,
        active: bool,
    ) -> None:
        """Render one per-kind ROI rectangle, with corner handles when ``active``."""
        x, y, w, h = roi
        sx, sy, sw, sh = (
            x * self.zoom.factor,
            y * self.zoom.factor,
            w * self.zoom.factor,
            h * self.zoom.factor,
        )
        painter.setPen(QPen(color, 2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(int(sx), int(sy), int(sw), int(sh))
        if not active:
            return
        handle_size = 8
        painter.setPen(QPen(color, 2, Qt.SolidLine))
        painter.setBrush(color)
        for cx, cy in (
            (sx, sy),
            (sx + sw, sy),
            (sx, sy + sh),
            (sx + sw, sy + sh),
        ):
            painter.drawRect(
                int(cx - handle_size / 2),
                int(cy - handle_size / 2),
                handle_size,
                handle_size,
            )

    # ---------------------------------------------------------------------------
    # Binocular decorations: divider + inactive-half dim wash
    # ---------------------------------------------------------------------------

    def _draw_divider(self, painter: QPainter, canvas: QPixmap, geometry: CanvasGeometry) -> None:
        """Draw the vertical binocular divider as a dashed line."""
        x = geometry.divider_x_image * self.zoom.factor
        painter.save()
        painter.setPen(QPen(self.colors.divider, 2, Qt.DashLine))
        painter.drawLine(int(x), 0, int(x), canvas.height())
        painter.restore()

    def _draw_inactive_half_dim(self, painter: QPainter, canvas: QPixmap, geometry: CanvasGeometry) -> None:
        """Wash the half of the canvas not owned by the active eye with a low-alpha fill."""
        divider_canvas_x = int(geometry.divider_x_image * self.zoom.factor)
        canvas_width, canvas_height = canvas.width(), canvas.height()
        if geometry.current_eye == "left":
            rect = (divider_canvas_x, 0, canvas_width - divider_canvas_x, canvas_height)
        else:
            rect = (0, 0, divider_canvas_x, canvas_height)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.colors.inactive_eye_dim)
        painter.drawRect(*rect)
        painter.restore()

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    @staticmethod
    def _active_eye_slot(geometry: CanvasGeometry) -> str:
        """Map the binocular flag + current eye to the slot key used by the stores."""
        return geometry.current_eye if geometry.binocular_mode else "single"

    @staticmethod
    def _slot_for_eye(eye: str, geometry: CanvasGeometry) -> str:
        """Map an eye to its store slot key (``"single"`` in monocular)."""
        return eye if geometry.binocular_mode else "single"

    def _is_manual(self, slot: str, kind: str) -> bool:
        """Whether ``slot``'s detector for ``kind`` is the manual annotator."""
        return self.selection_lookup(slot, kind) == DETECTOR_MANUAL
