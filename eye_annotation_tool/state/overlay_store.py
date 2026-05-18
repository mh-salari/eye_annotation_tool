"""Per-target, per-eye-slot Auto Detect overlays.

Each plugin produces a result dict on success; the renderer hands
that dict back to ``plugin.draw_overlay`` later. The store keeps the
two-level ``{target: {slot: result}}`` shape behind named methods so
callers don't have to manage the nested dict directly.
"""

from collections.abc import Iterator


class OverlayStore:
    """Owns ``{target: {slot: result_dict}}`` for the Auto Detect overlays."""

    def __init__(self) -> None:
        """Start with no stored overlays."""
        self.overlays: dict[str, dict[str, dict]] = {}

    def set(self, target: str, slot: str, result: dict) -> None:
        """Store ``result`` for ``(target, slot)``."""
        self.overlays.setdefault(target, {})[slot] = result

    def get(self, target: str, slot: str) -> dict | None:
        """Return the result stored for ``(target, slot)``, or ``None``."""
        return self.overlays.get(target, {}).get(slot)

    def clear(self, target: str, slot: str | None = None) -> bool:
        """Drop the result for ``(target, slot)`` (or every slot when ``slot`` is None).

        Returns ``True`` when anything was removed, so the caller knows
        whether a repaint is needed.
        """
        if slot is None:
            return self.overlays.pop(target, None) is not None
        by_slot = self.overlays.get(target)
        if not by_slot or slot not in by_slot:
            return False
        del by_slot[slot]
        if not by_slot:
            del self.overlays[target]
        return True

    def clear_all(self) -> bool:
        """Drop every stored overlay. Returns whether the store was non-empty before the clear."""
        had_any = bool(self.overlays)
        self.overlays.clear()
        return had_any

    def items_for_paint(self) -> Iterator[tuple[str, dict]]:
        """Yield ``(target, result)`` for every non-``None`` stored result.

        Slots are flattened — the renderer paints both eyes' stored
        results in a single pass and the per-slot key is irrelevant
        once the result has been written to the right viewer half.
        """
        for target, by_slot in self.overlays.items():
            for result in by_slot.values():
                if result is not None:
                    yield target, result
