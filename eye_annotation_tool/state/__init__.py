"""State stores extracted from the GUI widgets.

Each store owns a single piece of domain state previously held as a
mutable dict on ``MainWindow`` or ``ImageViewer``. The widgets become
thin coordinators that read and write through these stores instead of
managing the state themselves.
"""

from .carry_roi_state import CarryRoiStore
from .per_eye_state import PerEyeStateStore
from .project_store import ProjectStore

__all__ = ["CarryRoiStore", "PerEyeStateStore", "ProjectStore"]
