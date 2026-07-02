"""Per-target, per-eye-slot ROI rectangles + active-drag-target selector.

Each Auto Detect target can carry its own search-region rectangle per
eye. The store owns the nested ``{target: {slot: roi}}`` dict and the
active-drag-target selector that the input handler uses to know which
ROI a click is supposed to edit. The mouse state machine (drawing /
moving / resizing) stays on the widget.
"""

from collections.abc import Iterator

Roi = tuple[int, int, int, int]


class TargetRoiStore:
    """Holds per-(target, slot) rectangles and the active-drag-target selector."""

    def __init__(self) -> None:
        """Start with no stored ROIs and no active drag target."""
        self.rois: dict[str, dict[str, Roi]] = {}
        self.active_target: str | None = None

    # ---------------------------------------------------------------------------
    # Per-(target, slot) accessors
    # ---------------------------------------------------------------------------

    def set(self, target: str, slot: str, roi: Roi | None) -> None:
        """Store ``roi`` for ``(target, slot)``. ``None`` drops the entry."""
        if roi is None:
            self._drop(target, slot)
            return
        self.rois.setdefault(target, {})[slot] = tuple(roi)  # type: ignore[assignment]

    def get(self, target: str, slot: str) -> Roi | None:
        """Return the ROI stored for ``(target, slot)`` (or ``None``)."""
        return self.rois.get(target, {}).get(slot)

    def clear(self, target: str, slot: str | None = None) -> bool:
        """Drop the ROI for ``(target, slot)`` (or every slot when ``slot`` is None).

        Returns whether anything was removed.
        """
        if slot is None:
            return self.rois.pop(target, None) is not None
        return self._drop(target, slot)

    def clear_all(self) -> bool:
        """Drop every stored ROI. Returns whether the store had any data."""
        had_any = bool(self.rois)
        self.rois.clear()
        return had_any

    def _drop(self, target: str, slot: str) -> bool:
        """Remove ``slot`` from ``rois[target]``; prune empty target dicts."""
        by_slot = self.rois.get(target)
        if not by_slot or slot not in by_slot:
            return False
        del by_slot[slot]
        if not by_slot:
            del self.rois[target]
        return True

    # ---------------------------------------------------------------------------
    # Active drag target
    # ---------------------------------------------------------------------------

    def set_active_target(self, target: str | None) -> None:
        """Update the active drag-edit target (``None`` leaves drag mode)."""
        self.active_target = target

    def active_drag_roi(self, slot: str) -> Roi | None:
        """Return the ROI for ``(active_target, slot)`` (or ``None`` when no active target)."""
        if self.active_target is None:
            return None
        return self.get(self.active_target, slot)

    # ---------------------------------------------------------------------------
    # Iteration for the renderer
    # ---------------------------------------------------------------------------

    def items_for_paint(self) -> Iterator[tuple[str, str, Roi]]:
        """Yield ``(target, slot, roi)`` for every stored rectangle."""
        for target, by_slot in self.rois.items():
            for slot, roi in by_slot.items():
                yield target, slot, roi
