"""Application menu bar setup.

Three top-level menus: File (load/save/exit + project-defaults action),
Auto Detectors (per-target plugin choice), and Help.

The Auto Detectors menu has one submenu per anatomical target. Each
submenu is a mutually-exclusive radio group listing every plugin
``plugin_manager.for_target(target)`` returns plus a final "Disabled"
entry. Selecting an entry routes through
:meth:`MainWindow.select_plugin_for_target` which updates project
settings and rebuilds the Auto Detect panel stack.
"""

from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QAction, QActionGroup, QMenu

from ..utils.project_settings import DETECTOR_TARGETS

if TYPE_CHECKING:
    from .main_window import MainWindow


def _humanize_plugin_name(slug: str) -> str:
    """Convert a plugin slug (``threshold_pupil``) to a menu label (``Threshold Pupil``)."""
    return slug.replace("_", " ").title()


def _target_label(target: str) -> str:
    """Return the submenu title for ``target`` (e.g. ``"Pupil Detector"``)."""
    return f"{target.capitalize()} Detector"


class MenuHandler:
    """Build the application's menu bar and keep the Auto Detectors menu in sync."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialise with a back-reference to the main window."""
        self.main_window = main_window
        # target → (QActionGroup, {plugin_slug_or_"disabled": QAction}). Cached
        # so :meth:`update_auto_detectors_menu` can re-tick the right entry
        # when the project's detector choice changes from outside the menu.
        self._target_groups: dict[str, tuple[QActionGroup, dict[str, QAction]]] = {}

    def setup_menu(self) -> None:
        """Construct the File, Auto Detectors, and Help menus on the menu bar."""
        menubar = self.main_window.menuBar()
        self._add_file_menu(menubar.addMenu("File"))
        self._add_auto_detectors_menu(menubar.addMenu("Auto Detectors"))
        self._add_help_menu(menubar.addMenu("Help"))

    def _add_file_menu(self, file_menu: QMenu) -> None:
        new_action = QAction("New Project", self.main_window)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.main_window.on_new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project…", self.main_window)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.main_window.on_open_project)
        file_menu.addAction(open_action)

        save_action = QAction("Save Project", self.main_window)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.main_window.save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save Project As…", self.main_window)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.main_window.save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        save_defaults_action = QAction("Save Current Settings as Project Defaults", self.main_window)
        save_defaults_action.triggered.connect(
            lambda: self.main_window.detection_controller.save_current_settings_as_project_defaults(self.main_window),
        )
        file_menu.addAction(save_defaults_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self.main_window)
        exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(exit_action)

    def _add_auto_detectors_menu(self, menu: QMenu) -> None:
        for target in DETECTOR_TARGETS:
            submenu = menu.addMenu(_target_label(target))
            self._populate_target_submenu(submenu, target)

    def _populate_target_submenu(self, submenu: QMenu, target: str) -> None:
        group = QActionGroup(self.main_window)
        group.setExclusive(True)
        actions: dict[str, QAction] = {}
        for plugin in self.main_window.plugin_manager.for_target(target):
            action = QAction(_humanize_plugin_name(plugin.name), self.main_window)
            action.setCheckable(True)
            action.setActionGroup(group)
            action.triggered.connect(
                lambda _checked=False, t=target, name=plugin.name: (
                    self.main_window.detection_controller.select_plugin_for_target(t, name)
                ),
            )
            submenu.addAction(action)
            actions[plugin.name] = action
        disabled_action = QAction("Disabled", self.main_window)
        disabled_action.setCheckable(True)
        disabled_action.setActionGroup(group)
        disabled_action.triggered.connect(
            lambda _checked=False, t=target: self.main_window.detection_controller.select_plugin_for_target(
                t, "disabled"
            ),
        )
        submenu.addAction(disabled_action)
        actions["disabled"] = disabled_action
        self._target_groups[target] = (group, actions)

    def update_auto_detectors_menu(self) -> None:
        """Re-tick each submenu's checked action to match current project settings.

        Called by MainWindow after every project-settings change (project
        load, plugin selection, "Save Current Settings as Project Defaults").
        """
        for target, (_group, actions) in self._target_groups.items():
            current = self.main_window.detection_controller.current_plugin_for_target(target)
            for slug, action in actions.items():
                action.setChecked(slug == current)

    def _add_help_menu(self, help_menu: QMenu) -> None:
        about_action = QAction("About", self.main_window)
        about_action.triggered.connect(self.main_window.show_about_dialog)
        help_menu.addAction(about_action)
