"""Directory-tree view of the project's images.

Presents the loaded image set as a collapsible folder hierarchy (like a file
explorer) instead of a flat list: every directory is an expandable node and each
image is a leaf under its real folder. The tree is a *view* over the project's
image set, not a filesystem browser, so it shows exactly the images that are
loaded — nothing more.

The widget owns only presentation and the directory grouping. It emits
:attr:`image_selected` when the user picks an image leaf and
:attr:`remove_requested` with the paths a removal action targets; the main
window decides what those mean for the project state.
"""

import os
import re
from pathlib import Path

import qtawesome as qta
from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QAbstractItemView, QMenu, QTreeWidget, QTreeWidgetItem

# Per-item data: the absolute path (image path for leaves, directory path for
# folders) and whether the item is a directory node.
_PATH_ROLE = Qt.UserRole
_IS_DIR_ROLE = Qt.UserRole + 1

# Match the rest of the app's icons (trash / zoom / brightness all use this grey).
_FOLDER_COLOUR = "#e0e0e0"
_FILE_COLOUR = "#9aa0a6"

_DIGITS = re.compile(r"(\d+)")


def _natural_key(text: str) -> list:
    """Sort key that orders embedded numbers numerically (STEP_2 before STEP_11)."""
    return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in _DIGITS.split(text)]


class ImageTree(QTreeWidget):
    """A folder tree of the project's images, grouped by their real directories."""

    # A leaf image was clicked / activated (absolute path).
    image_selected = pyqtSignal(str)
    # Paths the user asked to drop from the project (context menu / Delete key).
    remove_requested = pyqtSignal(list)

    def __init__(self, parent: QTreeWidget | None = None) -> None:
        """Set up an empty, header-less, multi-select tree with a context menu."""
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemClicked.connect(self._on_item_interacted)
        self.itemActivated.connect(self._on_item_interacted)
        self._path_to_item: dict[str, QTreeWidgetItem] = {}
        self._folder_icon = qta.icon("mdi6.folder", color=_FOLDER_COLOUR)
        self._file_icon = qta.icon("mdi6.image-outline", color=_FILE_COLOUR)

    # ---------------------------------------------------------------------------
    # Building the tree
    # ---------------------------------------------------------------------------

    def set_images(self, paths: list[str]) -> None:
        """Rebuild the tree from ``paths``, nesting each image under its folders.

        Directories common to every path collapse into a single root node, so
        the tree shows the loaded subtree from its shared ancestor downward.
        Siblings are ordered folders-first, then files, case-insensitively.
        """
        self.clear()
        self._path_to_item = {}
        norm = [str(Path(p)) for p in paths]
        if not norm:
            return
        root_dir = self._common_root(norm)
        root_item = self._make_dir_item(root_dir)
        self.addTopLevelItem(root_item)
        dir_items: dict[str, QTreeWidgetItem] = {root_dir: root_item}
        for path in norm:
            parent_item = self._ensure_dir(str(Path(path).parent), dir_items)
            leaf = self._make_file_item(path)
            parent_item.addChild(leaf)
            self._path_to_item[path] = leaf
        self._sort_children(root_item)
        root_item.setExpanded(True)

    @staticmethod
    def _common_root(paths: list[str]) -> str:
        """Return the deepest directory shared by every path in ``paths``."""
        common = Path(os.path.commonpath(paths))
        # commonpath of a single file returns that file; back up to its folder.
        return str(common.parent if common.is_file() else common)

    def _ensure_dir(self, dir_path: str, dir_items: dict[str, QTreeWidgetItem]) -> QTreeWidgetItem:
        """Return the node for ``dir_path``, creating its ancestor chain as needed."""
        existing = dir_items.get(dir_path)
        if existing is not None:
            return existing
        parent = str(Path(dir_path).parent)
        # Every path sits under the common root, so the walk up always reaches a
        # cached ancestor before passing the filesystem root.
        parent_item = self._ensure_dir(parent, dir_items)
        item = self._make_dir_item(dir_path)
        parent_item.addChild(item)
        dir_items[dir_path] = item
        return item

    def _make_dir_item(self, dir_path: str) -> QTreeWidgetItem:
        """Build a folder node labelled by its directory name."""
        item = QTreeWidgetItem([Path(dir_path).name or dir_path])
        item.setData(0, _PATH_ROLE, dir_path)
        item.setData(0, _IS_DIR_ROLE, True)
        item.setIcon(0, self._folder_icon)
        return item

    def _make_file_item(self, path: str) -> QTreeWidgetItem:
        """Build an image leaf labelled by its file name."""
        item = QTreeWidgetItem([Path(path).name])
        item.setData(0, _PATH_ROLE, path)
        item.setData(0, _IS_DIR_ROLE, False)
        item.setIcon(0, self._file_icon)
        item.setToolTip(0, path)
        return item

    def _sort_children(self, item: QTreeWidgetItem) -> None:
        """Order an item's children folders-first then files, recursing into folders."""
        children = [item.takeChild(0) for _ in range(item.childCount())]
        children.sort(key=lambda c: (not bool(c.data(0, _IS_DIR_ROLE)), _natural_key(c.text(0))))
        for child in children:
            item.addChild(child)
            if child.data(0, _IS_DIR_ROLE):
                self._sort_children(child)

    # ---------------------------------------------------------------------------
    # Navigation helpers (image order + selection)
    # ---------------------------------------------------------------------------

    def ordered_paths(self) -> list[str]:
        """Return every image path in top-to-bottom tree order (depth-first)."""
        result: list[str] = []
        for i in range(self.topLevelItemCount()):
            self._collect_leaves(self.topLevelItem(i), result)
        return result

    def _collect_leaves(self, item: QTreeWidgetItem, out: list[str]) -> None:
        """Append ``item``'s descendant leaf paths to ``out`` in display order."""
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, _IS_DIR_ROLE):
                self._collect_leaves(child, out)
            else:
                out.append(child.data(0, _PATH_ROLE))

    def select_path(self, path: str) -> None:
        """Select the leaf for ``path``, expanding its folders and scrolling to it."""
        item = self._path_to_item.get(str(Path(path)))
        if item is None:
            return
        self.blockSignals(True)
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.setCurrentItem(item)
        self.scrollToItem(item)
        self.blockSignals(False)

    def selected_image_paths(self) -> list[str]:
        """Return the paths of the currently-selected image leaves (folders ignored)."""
        return [it.data(0, _PATH_ROLE) for it in self.selectedItems() if not it.data(0, _IS_DIR_ROLE)]

    def selected_removal_paths(self) -> list[str]:
        """Paths to remove for the selection: selected images plus every image under selected folders."""
        paths: list[str] = []
        for item in self.selectedItems():
            if item.data(0, _IS_DIR_ROLE):
                paths.extend(self._descendant_leaf_paths(item, recursive=True))
            else:
                paths.append(item.data(0, _PATH_ROLE))
        seen: set[str] = set()
        out: list[str] = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    # ---------------------------------------------------------------------------
    # Selection + removal interaction
    # ---------------------------------------------------------------------------

    def _on_item_interacted(self, item: QTreeWidgetItem) -> None:
        """Emit :attr:`image_selected` when an image leaf is clicked or activated."""
        if item is not None and not item.data(0, _IS_DIR_ROLE):
            self.image_selected.emit(item.data(0, _PATH_ROLE))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Map Delete / Backspace to a removal request for the selected images."""
        if event.key() in {Qt.Key_Delete, Qt.Key_Backspace}:
            paths = self.selected_removal_paths()
            if paths:
                self.remove_requested.emit(paths)
                return
        super().keyPressEvent(event)

    def _descendant_leaf_paths(self, item: QTreeWidgetItem, *, recursive: bool) -> list[str]:
        """Image paths directly under ``item`` (``recursive=False``) or anywhere below it."""
        paths: list[str] = []
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, _IS_DIR_ROLE):
                if recursive:
                    paths.extend(self._descendant_leaf_paths(child, recursive=True))
            else:
                paths.append(child.data(0, _PATH_ROLE))
        return paths

    def _show_context_menu(self, pos: QPoint) -> None:
        """Show removal actions for the item under the cursor (folder or image)."""
        item = self.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        if item.data(0, _IS_DIR_ROLE):
            self._add_folder_actions(menu, item)
        else:
            self._add_image_actions(menu, item)
        menu.exec_(self.viewport().mapToGlobal(pos))

    def _add_folder_actions(self, menu: QMenu, item: QTreeWidgetItem) -> None:
        """Add 'remove this folder only' and 'remove folder + subfolders' actions."""
        name = item.text(0)
        direct = self._descendant_leaf_paths(item, recursive=False)
        deep = self._descendant_leaf_paths(item, recursive=True)
        action_direct = menu.addAction(f'Remove images in "{name}" only ({len(direct)})')
        action_direct.setEnabled(bool(direct))
        action_direct.triggered.connect(lambda: self.remove_requested.emit(direct))
        action_deep = menu.addAction(f'Remove "{name}" and all subfolders ({len(deep)})')
        action_deep.setEnabled(bool(deep))
        action_deep.triggered.connect(lambda: self.remove_requested.emit(deep))

    def _add_image_actions(self, menu: QMenu, item: QTreeWidgetItem) -> None:
        """Add single- or multi-image removal for an image leaf."""
        selected = self.selected_image_paths()
        path = item.data(0, _PATH_ROLE)
        if len(selected) > 1 and path in selected:
            action = menu.addAction(f"Remove selected ({len(selected)}) images from project")
            action.triggered.connect(lambda: self.remove_requested.emit(selected))
        else:
            action = menu.addAction("Remove image from project")
            action.triggered.connect(lambda: self.remove_requested.emit([path]))
