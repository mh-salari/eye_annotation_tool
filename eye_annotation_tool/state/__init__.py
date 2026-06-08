"""State stores extracted from the GUI widgets.

Each store owns a single piece of domain state previously held as a
mutable dict on ``MainWindow`` or ``ImageViewer``. The widgets become
thin coordinators that read and write through these stores instead of
managing the state themselves.
"""

from .eye_data_store import EyeDataStore
from .overlay_store import OverlayStore
from .per_eye_state import PerEyeStateStore
from .project_store import ProjectStore
from .session_state import SessionState
from .target_roi_store import TargetRoiStore
from .undo_coordinator import UndoCoordinator
from .undo_stack import UndoStack

__all__ = [
    "EyeDataStore",
    "OverlayStore",
    "PerEyeStateStore",
    "ProjectStore",
    "SessionState",
    "TargetRoiStore",
    "UndoCoordinator",
    "UndoStack",
]
