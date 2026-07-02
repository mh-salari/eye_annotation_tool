"""Editable folder tree of compare pairs, grouped by the folder each pair sits in.

The pairs are the project's ``pairs`` list. Adding emits ``add_requested`` (the
main window reads the current image-list selection); double-clicking a pair emits
``open_requested`` to load it in Compare; any edit emits ``pairs_changed`` so the
project persists. Pairs are grouped under their real directories, mirroring the
image tree.
"""

import os
from itertools import starmap
from pathlib import Path

import qtawesome as qta
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .custom_widgets import MaterialButton
from .theme import theme

# Per-item data: a leaf's ``[path_a, path_b]`` pair (folder nodes carry ``None``).
_PAIR_ROLE = Qt.UserRole


class PairsPanel(QWidget):
    """Folder tree of image pairs for Compare mode, backed by the project ``pairs``."""

    add_requested = pyqtSignal()
    open_requested = pyqtSignal(str, str)
    pairs_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the header (add / remove) and the pairs tree."""
        super().__init__(parent)
        self._pairs: list[list[str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("Compare pairs:"))
        header.addStretch()
        self.add_button = MaterialButton("Add pair")
        self.add_button.setToolTip("Pair the two images selected in the list above")
        self.add_button.clicked.connect(self.add_requested.emit)
        header.addWidget(self.add_button)
        self.remove_button = MaterialButton("Remove")
        self.remove_button.clicked.connect(self._remove_selected)
        header.addWidget(self.remove_button)
        layout.addLayout(header)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.tree)
        self._pair_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._folder_icon = qta.icon("mdi6.folder", color=theme.color("icon"))
        self._pair_icon = qta.icon("mdi6.compare-horizontal", color=theme.color("icon_muted"))
        theme.changed.connect(self._on_theme_changed)

    def set_pairs(self, pairs: list) -> None:
        """Replace the pairs (e.g. on project load) without emitting ``pairs_changed``."""
        self._pairs = [[str(a), str(b)] for a, b in pairs]
        self._refresh()

    def pairs(self) -> list[list[str]]:
        """Return the current pairs as ``[[path_a, path_b], ...]``."""
        return [list(pair) for pair in self._pairs]

    def add_pair(self, path_a: str, path_b: str) -> None:
        """Append a pair and emit ``pairs_changed``."""
        self._pairs.append([path_a, path_b])
        self._refresh()
        self.pairs_changed.emit()

    def _remove_selected(self) -> None:
        item = self.tree.currentItem()
        pair = item.data(0, _PAIR_ROLE) if item is not None else None
        if pair is not None:
            self._pairs.remove(list(pair))
            self._refresh()
            self.pairs_changed.emit()

    def _open_item(self, item: QTreeWidgetItem) -> None:
        pair = item.data(0, _PAIR_ROLE)
        if pair is not None:
            self.open_requested.emit(pair[0], pair[1])

    def select_pair(self, path_a: str, path_b: str) -> None:
        """Highlight the leaf for the open pair, expanding its folders and scrolling to it."""
        item = self._pair_items.get((path_a, path_b))
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    # ---------------------------------------------------------------------------
    # Tree building
    # ---------------------------------------------------------------------------

    def _refresh(self) -> None:
        self.tree.clear()
        self._pair_items = {}
        if not self._pairs:
            return
        dirs = list(starmap(self._pair_dir, self._pairs))
        root = os.path.commonpath(dirs)
        dir_items = {root: self._make_dir_item(root)}
        self.tree.addTopLevelItem(dir_items[root])
        for (path_a, path_b), directory in zip(self._pairs, dirs, strict=True):
            self._ensure_dir(directory, root, dir_items).addChild(self._make_pair_item(path_a, path_b))
        self.tree.expandAll()

    @staticmethod
    def _pair_dir(path_a: str, path_b: str) -> str:
        """The smallest directory holding both images of the pair."""
        return os.path.commonpath([str(Path(path_a).parent), str(Path(path_b).parent)])

    def _ensure_dir(self, dir_path: str, root: str, dir_items: dict[str, QTreeWidgetItem]) -> QTreeWidgetItem:
        """Return the node for ``dir_path``, creating its chain down from ``root``."""
        existing = dir_items.get(dir_path)
        if existing is not None:
            return existing
        parent = self._ensure_dir(str(Path(dir_path).parent), root, dir_items)
        item = self._make_dir_item(dir_path)
        parent.addChild(item)
        dir_items[dir_path] = item
        return item

    def _make_dir_item(self, dir_path: str) -> QTreeWidgetItem:
        """Build a folder node labelled by its directory name."""
        item = QTreeWidgetItem([Path(dir_path).name or dir_path])
        item.setIcon(0, self._folder_icon)
        item.setToolTip(0, dir_path)
        return item

    def _make_pair_item(self, path_a: str, path_b: str) -> QTreeWidgetItem:
        """Build a pair leaf labelled ``name_a ↔ name_b``."""
        item = QTreeWidgetItem([f"{Path(path_a).name}  ↔  {Path(path_b).name}"])
        item.setData(0, _PAIR_ROLE, [path_a, path_b])
        item.setIcon(0, self._pair_icon)
        item.setToolTip(0, f"{path_a}\n{path_b}")
        self._pair_items[path_a, path_b] = item
        return item

    def _on_theme_changed(self) -> None:
        self._folder_icon = qta.icon("mdi6.folder", color=theme.color("icon"))
        self._pair_icon = qta.icon("mdi6.compare-horizontal", color=theme.color("icon_muted"))
        self._refresh()
