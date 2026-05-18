"""Image-viewer canvas renderer.

The renderer is a pure paint orchestrator: it reads the current state
of the various stores + the per-paint :class:`CanvasGeometry` and
produces a final :class:`QPixmap` for the widget's :class:`QLabel`.
Stateless across calls — the per-image geometry is supplied by the
widget on each render.
"""

from dataclasses import dataclass
from operator import itemgetter

import numpy as np
from PyQt5.QtCore import QPointF, QSizeF, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap

from ..state import EyeDataStore, OverlayStore, TargetMaskStore, TargetRoiStore
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
    divider: QColor
    inactive_eye_dim: QColor
    fallback_roi: QColor
    fallback_mask: QColor

    @classmethod
    def default(cls) -> "AnnotationColors":
        """Return the canonical palette used by ImageViewer."""
        return cls(
            pupil=QColor(150, 213, 116, 255),
            pupil_select=QColor(249, 248, 113, 255),
            pupil_ellipse=QColor(25, 145, 50, 255),
            # Brighter shade than the ellipse outline so the centre dot
            # reads on top of both the outline and the soft-green mask
            # fill the auto pupil plugin can paint underneath.
            pupil_center=QColor(180, 240, 80, 255),
            limbus=QColor(194, 149, 188, 255),
            limbus_select=QColor(249, 178, 208, 255),
            limbus_ellipse=QColor(139, 122, 162, 255),
            eyelid=QColor(0, 155, 201, 255),
            eyelid_select=QColor(0, 189, 194, 255),
            eyelid_ellipse=QColor(0, 118, 195, 255),
            glint=QColor(255, 165, 0, 255),
            glint_select=QColor(255, 215, 0, 255),
            # Divider line + inactive-eye dim. Divider is bright white
            # so it reads as a UI separator distinct from every data
            # layer; the dim overlay is a low-alpha black wash that
            # darkens the inactive half without hiding the eye entirely.
            divider=QColor(255, 255, 255, 230),
            inactive_eye_dim=QColor(0, 0, 0, 120),
            # Fallback colours used when a plugin didn't declare its own
            # ``roi_color`` / ``mask_color`` — keeps misconfigured plugins
            # visible instead of crashing.
            fallback_roi=QColor(255, 255, 255, 200),
            fallback_mask=QColor(255, 255, 255, 64),
        )


@dataclass
class CanvasGeometry:
    """Per-paint snapshot of widget-side state the renderer needs."""

    current_eye: str
    binocular_mode: bool
    divider_x_image: float
    current_annotation: str
    auto_managed_annotations: set[str]
    selected_point: QPointF | None


_POINT_FIELD_BY_ANNOTATION: dict[str, str] = {
    "pupil": "pupil_points",
    "limbus": "limbus_points",
    "eyelid_contour": "eyelid_contour_points",
    "glint": "glint_points",
}


class CanvasRenderer:
    """Composite paint orchestrator for the image-viewer canvas."""

    def __init__(
        self,
        colors: AnnotationColors,
        eye_data_store: EyeDataStore,
        overlay_store: OverlayStore,
        roi_store: TargetRoiStore,
        mask_store: TargetMaskStore,
        active_plugins: dict[str, object],
        brightness: BrightnessController,
        zoom: ZoomController,
    ) -> None:
        """Wire dependencies; the widget keeps these references in sync."""
        self.colors = colors
        self.eye_data_store = eye_data_store
        self.overlay_store = overlay_store
        self.roi_store = roi_store
        self.mask_store = mask_store
        self.active_plugins = active_plugins
        self.brightness = brightness
        self.zoom = zoom

    # ---------------------------------------------------------------------------
    # Single entry point
    # ---------------------------------------------------------------------------

    def render(self, original_pixmap: QPixmap | None, geometry: CanvasGeometry) -> QPixmap | None:
        """Build and return the painted canvas pixmap, or ``None`` when no image is loaded."""
        if original_pixmap is None or original_pixmap.isNull():
            return None
        source_pixmap = self.brightness.display_pixmap(original_pixmap)
        scaled_pixmap = source_pixmap.scaled(
            source_pixmap.size() * self.zoom.factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
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

    # ---------------------------------------------------------------------------
    # Manual annotation layers
    # ---------------------------------------------------------------------------

    def _draw_eye_annotations(self, painter: QPainter, eye: str, geometry: CanvasGeometry) -> None:
        """Paint the manual point + ellipse layers for ``eye``."""
        eye_data = self.eye_data_store.eye_data[eye]
        self._draw_points_for_eye(painter, eye_data, eye, geometry)
        self._draw_ellipses_for_eye(painter, eye_data, geometry)

    def _draw_points_for_eye(
        self,
        painter: QPainter,
        eye_data: dict,
        eye: str,
        geometry: CanvasGeometry,
    ) -> None:
        """Render every manual point list for one eye, with selection highlight on the active eye."""
        is_active = eye == geometry.current_eye
        eye_label = "L" if eye == "left" else "R"
        for points, color, select_color, annotation_type in (
            (eye_data["pupil_points"], self.colors.pupil, self.colors.pupil_select, "pupil"),
            (eye_data["limbus_points"], self.colors.limbus, self.colors.limbus_select, "limbus"),
            (eye_data["eyelid_contour_points"], self.colors.eyelid, self.colors.eyelid_select, "eyelid_contour"),
            (eye_data["glint_points"], self.colors.glint, self.colors.glint_select, "glint"),
        ):
            if annotation_type in geometry.auto_managed_annotations:
                continue
            for point in points:
                scaled_point = QPointF(point.x() * self.zoom.factor, point.y() * self.zoom.factor)
                highlighted = (
                    is_active
                    and point == geometry.selected_point
                    and geometry.current_annotation == annotation_type
                )
                pen_color = select_color if highlighted else color
                painter.setPen(QPen(pen_color, 3, Qt.SolidLine))
                painter.drawEllipse(scaled_point, 1.5, 1.5)
                # In monocular mode the L/R distinction is meaningless,
                # so the per-point eye label is suppressed entirely.
                if geometry.binocular_mode:
                    font = painter.font()
                    font.setPointSize(8)
                    painter.setFont(font)
                    painter.setPen(QPen(color, 1, Qt.SolidLine))
                    text_pos = QPointF(scaled_point.x() + 6, scaled_point.y() - 4)
                    painter.drawText(text_pos, eye_label)

    def _draw_ellipses_for_eye(self, painter: QPainter, eye_data: dict, geometry: CanvasGeometry) -> None:
        """Render the fitted pupil + limbus ellipses (with centre markers) for one eye."""
        if eye_data["pupil_ellipse"] and "pupil" not in geometry.auto_managed_annotations:
            painter.setPen(QPen(self.colors.pupil_ellipse, 1, Qt.SolidLine))
            self._draw_single_ellipse(painter, eye_data["pupil_ellipse"])
            self._draw_ellipse_center(painter, eye_data["pupil_ellipse"], self.colors.pupil_center)
        if eye_data["limbus_ellipse"] and "limbus" not in geometry.auto_managed_annotations:
            painter.setPen(QPen(self.colors.limbus_ellipse, 1, Qt.SolidLine))
            self._draw_single_ellipse(painter, eye_data["limbus_ellipse"])
            self._draw_ellipse_center(painter, eye_data["limbus_ellipse"], self.colors.limbus_ellipse)

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

    def _draw_ellipse_center(self, painter: QPainter, ellipse: tuple, color: QColor) -> None:
        """Render a small filled dot at the centre of a manually fitted ellipse."""
        if ellipse is None:
            return
        center, _size, _angle = ellipse
        scaled = QPointF(center.x() * self.zoom.factor, center.y() * self.zoom.factor)
        painter.save()
        painter.setBrush(color)
        painter.setPen(QPen(color, 1, Qt.SolidLine))
        painter.drawEllipse(scaled, 2.0, 2.0)
        painter.restore()

    # ---------------------------------------------------------------------------
    # Auto Detect overlays + masks
    # ---------------------------------------------------------------------------

    def _draw_detection_overlays(self, painter: QPainter) -> None:
        """Render every per-eye, per-target Auto Detect overlay via the plugins.

        Threshold-mask fills go under the markers so the centres and
        ellipses remain legible on top. Each plugin declares an integer
        ``overlay_z_order`` so overlays sort consistently across plugins
        (e.g. limbus iris ring goes under the pupil + glint markers).
        """
        self._draw_target_masks(painter)
        pairs: list[tuple[int, object, dict]] = []
        for target, result in self.overlay_store.items_for_paint():
            plugin = self.active_plugins.get(target)
            if plugin is None:
                continue
            z = int(getattr(plugin, "overlay_z_order", 0))
            pairs.append((z, plugin, result))
        pairs.sort(key=itemgetter(0))
        for _z, plugin, result in pairs:
            plugin.draw_overlay(painter, result, self.zoom.factor)

    def _draw_target_masks(self, painter: QPainter) -> None:
        """Render every visible per-eye threshold mask as a semi-transparent fill."""
        for target, mask in self.mask_store.visible_items():
            plugin = self.active_plugins.get(target)
            color = getattr(plugin, "mask_color", None) or self.colors.fallback_mask
            self._draw_mask(painter, mask, color)

    def _draw_mask(self, painter: QPainter, mask: np.ndarray, color: QColor) -> None:
        """Blit a uint8 0/255 mask onto the canvas as a single coloured RGBA fill."""
        if mask.size == 0:
            return
        h, w = mask.shape[:2]
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        bool_mask = mask > 0
        rgba[bool_mask, 0] = color.red()
        rgba[bool_mask, 1] = color.green()
        rgba[bool_mask, 2] = color.blue()
        rgba[bool_mask, 3] = color.alpha()
        rgba = np.ascontiguousarray(rgba)
        qimg = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
        scaled = QPixmap.fromImage(qimg).scaled(
            int(w * self.zoom.factor),
            int(h * self.zoom.factor),
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        painter.drawPixmap(0, 0, scaled)

    # ---------------------------------------------------------------------------
    # Per-target ROI rectangles
    # ---------------------------------------------------------------------------

    def _draw_target_rois(self, painter: QPainter, geometry: CanvasGeometry) -> None:
        """Render every stored per-eye ROI rectangle.

        Only the active eye's rectangle for the active drag-edit target
        gets corner handles — the inactive eye's rectangle paints
        plain so the user sees their saved ROI without it tempting
        edit attempts.
        """
        active_slot = self._active_eye_slot(geometry)
        active_target = self.roi_store.active_target
        for target, slot, roi in self.roi_store.items_for_paint():
            plugin = self.active_plugins.get(target)
            color = getattr(plugin, "roi_color", None) or self.colors.fallback_roi
            is_active = target == active_target and slot == active_slot
            self._draw_target_roi_box(painter, roi, color, active=is_active)

    def _draw_target_roi_box(
        self,
        painter: QPainter,
        roi: tuple,
        color: QColor,
        *,
        active: bool,
    ) -> None:
        """Render one per-target ROI rectangle, with corner handles when ``active``."""
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
