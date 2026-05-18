"""Per-target, per-eye carry-over ROI store.

Carry-over lets a user lock an ROI rectangle on one image and have it
propagate to subsequent image loads for the same eye. Each target has
its own enable flag and stored rectangle, and the flag is independent
per eye so toggling Carry on the left side does not drag the right.

The store owns the two tables plus the small bits of logic the UI
needs: project-file round-trip via :meth:`load_from_project_block` /
:meth:`to_project_block`, panel-checkbox display via
:meth:`checkbox_state`, and slot selection on image load via
:meth:`pending_slots_for_apply`.
"""

from collections.abc import Iterable

from .per_eye_state import CarryRoiSlot, Target

Roi = tuple[int, int, int, int]


class CarryRoiStore:
    """Per-target, per-eye carry-over ROI state.

    Two parallel tables: ``enabled[target][slot]`` is the per-eye Carry
    checkbox flag, and ``values[target][slot]`` is the rectangle the
    user has decided to carry forward (or ``None`` if nothing is
    stored yet). Slots are ``"left"``, ``"right"``, ``"single"``.
    """

    SLOTS: tuple[CarryRoiSlot, ...] = ("left", "right", "single")

    def __init__(self, targets: Iterable[Target]) -> None:
        """Initialise both tables with one entry per (target, slot)."""
        self._targets: tuple[Target, ...] = tuple(targets)
        self.enabled: dict[Target, dict[CarryRoiSlot, bool]] = {
            target: dict.fromkeys(self.SLOTS, False) for target in self._targets
        }
        self.values: dict[Target, dict[CarryRoiSlot, Roi | None]] = {
            target: dict.fromkeys(self.SLOTS) for target in self._targets
        }

    # ---------------------------------------------------------------------------
    # Direct accessors
    # ---------------------------------------------------------------------------

    def is_enabled(self, target: Target, slot: CarryRoiSlot) -> bool:
        """Return whether Carry is enabled for ``(target, slot)``."""
        return bool(self.enabled[target][slot])

    def set_enabled(self, target: Target, slot: CarryRoiSlot, value: bool) -> None:
        """Set the Carry flag for ``(target, slot)``."""
        self.enabled[target][slot] = bool(value)

    def get_value(self, target: Target, slot: CarryRoiSlot) -> Roi | None:
        """Return the stored carry rectangle for ``(target, slot)`` (``None`` if absent)."""
        return self.values[target][slot]

    def set_value(self, target: Target, slot: CarryRoiSlot, roi: Roi | None) -> None:
        """Replace the stored carry rectangle for ``(target, slot)``.

        ``roi`` may be ``None`` (clears the slot) or a 4-element iterable
        of numeric corners; the rectangle is normalised to ``(int, int, int, int)``.
        """
        if roi is None:
            self.values[target][slot] = None
            return
        self.values[target][slot] = tuple(int(c) for c in roi)  # type: ignore[assignment]

    # ---------------------------------------------------------------------------
    # Project file round-trip
    # ---------------------------------------------------------------------------

    def load_from_project_block(self, target: Target, carry_block: dict) -> None:
        """Replace ``target``'s rows from a project-file ``carry_roi`` block.

        Missing or malformed entries fall back to disabled / no stored
        value so a project file that doesn't carry the new schema still
        loads.
        """
        enabled_in = carry_block.get("enabled") if isinstance(carry_block, dict) else None
        if not isinstance(enabled_in, dict):
            enabled_in = {}
        for slot in self.SLOTS:
            self.enabled[target][slot] = bool(enabled_in.get(slot, False))
        values_in = carry_block.get("values") if isinstance(carry_block, dict) else None
        if not isinstance(values_in, dict):
            values_in = {}
        for slot in self.SLOTS:
            value = values_in.get(slot)
            if isinstance(value, (list, tuple)) and len(value) == 4:
                self.values[target][slot] = tuple(int(c) for c in value)  # type: ignore[assignment]
            else:
                self.values[target][slot] = None

    def to_project_block(self, target: Target) -> dict:
        """Emit ``target``'s rows as a project-file ``carry_roi`` block."""
        return {
            "enabled": {slot: bool(self.enabled[target][slot]) for slot in self.SLOTS},
            "values": {
                slot: list(value) if value is not None else None for slot, value in self.values[target].items()
            },
        }

    # ---------------------------------------------------------------------------
    # Derived queries used by the UI
    # ---------------------------------------------------------------------------

    def checkbox_state(self, target: Target, slot: CarryRoiSlot, viewer_roi: Roi | None) -> bool:
        """Return whether the Carry checkbox should display as checked for ``(target, slot)``.

        Checked only when Carry is enabled for the slot AND the slot has
        a stored value AND that value matches the supplied ``viewer_roi``
        bit-for-bit. Loading an image whose saved ROI differs from the
        carry-over leaves the checkbox unchecked — a visual cue that
        "this image isn't the one we're propagating".
        """
        if not self.is_enabled(target, slot):
            return False
        carry_value = self.get_value(target, slot)
        if carry_value is None or viewer_roi is None:
            return False
        return tuple(int(c) for c in viewer_roi) == tuple(int(c) for c in carry_value)

    def pending_slots_for_apply(
        self,
        target: Target,
        candidate_slots: Iterable[CarryRoiSlot],
        already_filled_slots: Iterable[CarryRoiSlot],
    ) -> list[tuple[CarryRoiSlot, Roi]]:
        """Return ``[(slot, carry_value), ...]`` for slots that should receive a carry-fill.

        A slot qualifies when:
          * it is in ``candidate_slots`` (typically the active mode's
            slots: ``("left", "right")`` or ``("single",)``);
          * Carry is enabled for ``(target, slot)``;
          * the slot is NOT in ``already_filled_slots`` (the caller
            determines what counts as filled — usually a saved ROI on
            the freshly loaded image);
          * a carry value is stored for the slot.
        """
        filled = set(already_filled_slots)
        pending: list[tuple[CarryRoiSlot, Roi]] = []
        for slot in candidate_slots:
            if slot in filled:
                continue
            if not self.is_enabled(target, slot):
                continue
            value = self.get_value(target, slot)
            if value is None:
                continue
            pending.append((slot, value))
        return pending
