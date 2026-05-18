"""Per-target threshold masks + per-target Show-mask visibility flags.

Each plugin can return a binary mask alongside its geometry overlay.
Masks are per-eye-slot (only the active eye's mask updates per run),
but the Show-mask toggle is per-target — flipping it reveals both
eyes' stored masks at once.
"""

from collections.abc import Iterator

import numpy as np


class TargetMaskStore:
    """Holds ``{target: {slot: mask}}`` plus a per-target visibility flag."""

    def __init__(self) -> None:
        """Start with no stored masks and every target hidden."""
        self.masks: dict[str, dict[str, np.ndarray]] = {}
        self.show: dict[str, bool] = {}

    # ---------------------------------------------------------------------------
    # Per-(target, slot) mask data
    # ---------------------------------------------------------------------------

    def set(self, target: str, slot: str, mask: np.ndarray | None) -> bool:
        """Store ``mask`` for ``(target, slot)``; ``None`` drops the entry.

        Returns whether the target is currently visible so the caller
        knows whether the change needs a repaint.
        """
        if mask is None:
            return self._drop(target, slot) and self.is_visible(target)
        self.masks.setdefault(target, {})[slot] = mask
        return self.is_visible(target)

    def get(self, target: str, slot: str) -> np.ndarray | None:
        """Return the mask stored for ``(target, slot)`` (or ``None``)."""
        return self.masks.get(target, {}).get(slot)

    def clear(self, target: str, slot: str | None = None) -> bool:
        """Drop the mask(s) for ``target``; returns whether a repaint is needed.

        ``slot=None`` drops every slot for ``target``. A repaint is only
        needed when the target is currently visible.
        """
        was_visible = self.is_visible(target) and bool(self.masks.get(target))
        if slot is None:
            self.masks.pop(target, None)
            return was_visible
        return self._drop(target, slot) and was_visible

    def clear_all(self) -> bool:
        """Drop every stored mask; returns whether anything visible was cleared."""
        had_visible = any(self.is_visible(target) for target in self.masks)
        self.masks.clear()
        return had_visible

    def _drop(self, target: str, slot: str) -> bool:
        """Remove ``slot`` from ``masks[target]``; prune empty target dicts."""
        by_slot = self.masks.get(target)
        if not by_slot or slot not in by_slot:
            return False
        del by_slot[slot]
        if not by_slot:
            del self.masks[target]
        return True

    # ---------------------------------------------------------------------------
    # Per-target visibility flag
    # ---------------------------------------------------------------------------

    def set_show(self, target: str, on: bool) -> None:
        """Toggle the Show-mask flag for ``target`` (affects both eyes' masks)."""
        self.show[target] = bool(on)

    def is_visible(self, target: str) -> bool:
        """True when ``target``'s Show-mask flag is on."""
        return bool(self.show.get(target))

    # ---------------------------------------------------------------------------
    # Iteration for the renderer
    # ---------------------------------------------------------------------------

    def visible_items(self) -> Iterator[tuple[str, np.ndarray]]:
        """Yield ``(target, mask)`` for every target whose Show-mask flag is on.

        Masks from both eye slots are yielded so the renderer paints
        each visible half in a single pass.
        """
        for target, by_slot in self.masks.items():
            if not self.is_visible(target):
                continue
            for mask in by_slot.values():
                if mask is not None:
                    yield target, mask
