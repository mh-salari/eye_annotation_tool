"""Session policy for the ``--monocular`` and ``--auto-detectors`` CLI flags.

A loaded project file may disagree with the CLI flags. The policy
captures the decision once per session: either the CLI flags win and
the project file is rewritten to match, or the project file wins and
the CLI flags are ignored. Until :meth:`set_active` is called the
policy is "undecided" and the per-flag *_locked queries report False.

The dialog that asks the user lives on ``MainWindow``; this class
provides the conflict list and the override calculations so the GUI
side only owns the Qt-specific bits.
"""

from ..utils.project_settings import DETECTOR_TARGETS


class CliOverridePolicy:
    """CLI-vs-project-settings reconciliation for a single session.

    Constructed once at startup from the parsed CLI flags. After the
    project file is loaded ``MainWindow`` calls :meth:`conflicts` to
    decide whether a user prompt is needed, runs the dialog, then
    calls :meth:`set_active` with the user's answer. After that the
    ``session_*`` / ``*_locked`` queries return the resolved policy.
    """

    def __init__(self, monocular: bool, auto_detectors: set[str] | None) -> None:
        """Capture the raw CLI flag values.

        ``monocular`` is the ``--monocular`` boolean; ``auto_detectors``
        is the set passed via ``--auto-detectors`` (``None`` when the
        flag was not given).
        """
        self.monocular: bool = bool(monocular)
        self.auto_detectors: set[str] | None = set(auto_detectors) if auto_detectors is not None else None
        self._active: bool | None = None

    # ---------------------------------------------------------------------------
    # Resolution lifecycle
    # ---------------------------------------------------------------------------

    def has_any_override(self) -> bool:
        """Return True iff at least one CLI override flag was given."""
        return self.monocular or self.auto_detectors is not None

    def set_active(self, active: bool) -> None:
        """Record the session's verdict on whether CLI flags win.

        ``True`` => CLI flags override the project file (the project
        file should also be rewritten by the caller). ``False`` =>
        project file wins, CLI flags are ignored for the rest of the
        session.
        """
        self._active = bool(active)

    def is_active(self) -> bool:
        """Return True once :meth:`set_active` has been called with True."""
        return self._active is True

    # ---------------------------------------------------------------------------
    # Per-flag locked queries
    # ---------------------------------------------------------------------------

    def monocular_locked(self) -> bool:
        """``--monocular`` is in force for this session."""
        return self.monocular and self.is_active()

    def auto_detectors_locked(self) -> bool:
        """``--auto-detectors`` is in force for this session."""
        return self.auto_detectors is not None and self.is_active()

    # ---------------------------------------------------------------------------
    # Apply the policy to raw project values
    # ---------------------------------------------------------------------------

    def session_binocular(self, source_value: bool) -> bool:
        """Apply the session policy to a raw binocular flag.

        Returns ``False`` when ``--monocular`` is locked for the session,
        otherwise returns ``source_value`` unchanged. Used by both the
        project-settings load path and the per-image meta load path so
        the policy gate lives in exactly one place.
        """
        return False if self.monocular_locked() else bool(source_value)

    def session_detectors(self, source_value: dict) -> dict:
        """Apply the session policy to a raw detectors dict.

        When ``--auto-detectors`` is locked, returns a dict with only
        the CLI-listed targets enabled (the others forced to
        ``"disabled"``); otherwise returns ``source_value`` unchanged.
        """
        if self.auto_detectors_locked():
            return self.override_detectors_from_cli(source_value)
        return source_value

    # ---------------------------------------------------------------------------
    # Conflict detection + detector override
    # ---------------------------------------------------------------------------

    def conflicts(self, project_settings: dict) -> list[str]:
        """Return human-readable descriptions of fields where CLI disagrees with project.

        Computed BEFORE the session verdict so the dialog has something
        to show; consequently this method ignores ``_active`` and looks
        only at the raw CLI flags.
        """
        out: list[str] = []
        if self.monocular and bool(project_settings.get("binocular_mode", True)):
            out.append("Binocular mode: project file = binocular, CLI = monocular.")
        if self.auto_detectors is not None:
            current = {
                target
                for target, block in project_settings.get("detectors", {}).items()
                if block.get("plugin", "disabled") != "disabled"
            }
            if current != self.auto_detectors:
                out.append(
                    "Auto detectors enabled: project file = "
                    f"{sorted(current) or 'none'}, CLI = {sorted(self.auto_detectors)}.",
                )
        return out

    def override_detectors_from_cli(self, project_detectors: dict) -> dict:
        """Return a detectors dict honouring ``--auto-detectors`` for this session.

        Targets in :attr:`auto_detectors` keep the project file's plugin
        choice (and any tuned params) untouched; every other target is
        forced to ``"disabled"``. The original ``project_detectors`` is
        not mutated.

        Raises ``SystemExit`` when a CLI-enabled target is set to
        ``"disabled"`` in the project file — that combination is a
        user-side conflict and silently substituting a default plugin
        would surprise the user.
        """
        if self.auto_detectors is None:
            return project_detectors
        overridden: dict = {}
        for target in DETECTOR_TARGETS:
            existing = project_detectors.get(target, {})
            if target in self.auto_detectors:
                plugin_slug = existing.get("plugin", "disabled")
                if plugin_slug == "disabled":
                    raise SystemExit(
                        f"--auto-detectors includes {target!r} but the project "
                        f"settings file has {target!r} set to 'disabled'. Enable "
                        f"a plugin for {target!r} via the Auto Detectors menu "
                        f"(or hand-edit the settings file) and re-run, or drop "
                        f"{target!r} from --auto-detectors.",
                    )
                overridden[target] = {"plugin": plugin_slug, "params": dict(existing.get("params", {}))}
            else:
                overridden[target] = {"plugin": "disabled", "params": {}}
        return overridden
