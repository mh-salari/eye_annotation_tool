"""Per-eye manual-annotation store: point lists + fitted ellipses.

Each eye carries six fields:

* ``pupil_points`` / ``limbus_points`` / ``eyelid_contour_points`` /
  ``glint_points`` — lists of :class:`QPointF` placed by the user.
* ``pupil_ellipse`` / ``limbus_ellipse`` — fitted ellipses (or ``None``).

The store owns both eyes' data and the active-eye selector. Working
references for the active eye are returned live (no copies) so
callers can mutate the lists in place and the change is persisted
without an explicit save step.
"""

POINT_FIELDS: tuple[str, ...] = (
    "pupil_points",
    "limbus_points",
    "eyelid_contour_points",
    "glint_points",
    "purkinje_iv_points",
)
ELLIPSE_FIELDS: tuple[str, ...] = ("pupil_ellipse", "limbus_ellipse")
# Smooth-curve boundary points for the manual "smooth" fit mode. Transient:
# recomputed from points + smoothness on each fit, not persisted (the
# annotation save owns the canonical result).
CURVE_FIELDS: tuple[str, ...] = ("pupil_fit_curve", "limbus_fit_curve")
ALL_FIELDS: tuple[str, ...] = POINT_FIELDS + ELLIPSE_FIELDS
EYES: tuple[str, ...] = ("left", "right")

# Map each annotation slug (used by ``current_annotation`` and the UI
# layer) to its ``(points_field, ellipse_field | None)`` pair in
# :class:`EyeDataStore`. Pupil + limbus carry both a point list and a
# fitted ellipse; eyelid + glint are points-only.
FIELDS_BY_ANNOTATION: dict[str, tuple[str, str | None]] = {
    "pupil": ("pupil_points", "pupil_ellipse"),
    "limbus": ("limbus_points", "limbus_ellipse"),
    "eyelid_contour": ("eyelid_contour_points", None),
    "glint": ("glint_points", None),
    "purkinje_iv": ("purkinje_iv_points", None),
}

# Per-eye field values: point lists hold :class:`QPointF`, ellipse
# slots hold the 3-tuple returned by ``cv2.fitEllipse`` or ``None``.
EyeField = list | tuple | None


def _empty_eye() -> dict[str, EyeField]:
    """Return the canonical empty annotation dict for one eye."""
    return {
        **{field: [] for field in POINT_FIELDS},
        **dict.fromkeys(ELLIPSE_FIELDS),
        **{field: [] for field in CURVE_FIELDS},
    }


class EyeDataStore:
    """Holds ``{left, right}`` annotation dicts and the active-eye selector."""

    def __init__(self) -> None:
        """Start with two empty eye dicts and ``current_eye = "left"``."""
        self.current_eye: str = "left"
        self.eye_data: dict[str, dict[str, EyeField]] = {eye: _empty_eye() for eye in EYES}

    # ---------------------------------------------------------------------------
    # Active-eye accessors (live refs into ``eye_data[current_eye]``)
    # ---------------------------------------------------------------------------

    def get_field(self, field: str) -> EyeField:
        """Return the live value of ``field`` for the active eye."""
        return self.eye_data[self.current_eye][field]

    def set_field(self, field: str, value: EyeField) -> None:
        """Assign ``value`` to ``field`` for the active eye."""
        self.eye_data[self.current_eye][field] = value

    def switch_eye(self, eye: str) -> None:
        """Make ``eye`` the active eye. ``eye`` must be ``"left"`` or ``"right"``."""
        self.current_eye = eye

    # ---------------------------------------------------------------------------
    # Per-field / per-target clears
    # ---------------------------------------------------------------------------

    def clear_field(self, eye: str, field: str) -> None:
        """Reset ``field`` on ``eye`` to its empty form (``[]`` or ``None``)."""
        self.eye_data[eye][field] = [] if field in POINT_FIELDS else None

    def clear_target_across_eyes(self, points_field: str, ellipse_field: str | None) -> bool:
        """Clear ``points_field`` (and optionally ``ellipse_field``) on both eyes.

        Returns ``True`` when at least one eye had data to clear, so the
        caller knows whether to push an undo state and repaint.
        """
        had_data = False
        for eye in EYES:
            if self.eye_data[eye][points_field]:
                had_data = True
                self.eye_data[eye][points_field] = []
            if ellipse_field is not None and self.eye_data[eye][ellipse_field] is not None:
                had_data = True
                self.eye_data[eye][ellipse_field] = None
        return had_data

    # ---------------------------------------------------------------------------
    # Full save / load round-trip
    # ---------------------------------------------------------------------------

    def as_dict(self) -> dict[str, dict[str, EyeField]]:
        """Return a shallow copy of the full ``{eye: {field: value}}`` tree."""
        return {eye: dict(data) for eye, data in self.eye_data.items()}

    def from_dict(self, data: dict[str, dict[str, EyeField]]) -> None:
        """Replace the full store from a serialised payload (e.g. annotation JSON).

        Each eye starts from the empty template so transient fields (the smooth
        curve) always exist even though the payload never carries them.
        """
        self.eye_data = {}
        for eye in EYES:
            block = _empty_eye()
            loaded = data.get(eye)
            if isinstance(loaded, dict):
                block.update(loaded)
            self.eye_data[eye] = block
