"""GUI components for the eye annotation application."""

from .annotation_controls import AnnotationControlPanel
from .auto_detectors_handler import AutoDetectorsHandler
from .custom_widgets import MaterialButton
from .image_viewer import ImageViewer
from .main_window import MainWindow
from .menu_handler import MenuHandler
from .shortcut_handler import ShortcutHandler

__all__ = [
    "AnnotationControlPanel",
    "AutoDetectorsHandler",
    "ImageViewer",
    "MainWindow",
    "MaterialButton",
    "MenuHandler",
    "ShortcutHandler",
]
