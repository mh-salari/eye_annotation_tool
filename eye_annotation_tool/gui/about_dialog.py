"""Help > About dialog.

Builds the rich-text body, parses the version literal out of
``setup.py``, and shows the result inside a :class:`QMessageBox`. Kept
separate from ``main_window.py`` so the AST parse of ``setup.py``
doesn't need to live next to the GUI shell.
"""

import ast
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from .theme import theme


def get_version_from_setup() -> str:
    """Read the application version literal from ``setup.py``."""
    setup_path = Path(__file__).parent / ".." / ".." / "setup.py"
    tree = ast.parse(setup_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.func.id == "setup":
            for keyword in node.keywords:
                if keyword.arg == "version":
                    return ast.literal_eval(keyword.value)
    return "Unknown"


def show_about_dialog(parent: QWidget) -> None:
    """Show the About dialog parented to ``parent``."""
    about_text = (
        "<h3>EyE Annotation Tool</h3>"
        "<p>A tool to annotate eye images for pupil, limbus and eyelid detection.</p>"
        "<p>Developed by "
        "<a href='https://mh-salari.ir/'"
        f"style='color: {theme.color('link')};'>Mohammadhossein Salari</a></p>"
        f"<p>Current version: {get_version_from_setup()}</p>"
        "<p>To get the latest version of Eye Annotation Tool, visit<br>"
        "<a href='https://github.com/mh-salari/eye_annotation_tool' "
        f"style='color: {theme.color('link')};' target='_blank' rel='noopener noreferrer'>"
        "github.com/mh-salari/eye_annotation_tool</a></p>"
        "<p>This project has received funding from the European Union's Horizon "
        "Europe research and innovation funding program under grant "
        "agreement No 101072410, Eyes4ICU project.</p>"
    )
    about_widget = QWidget()
    layout = QVBoxLayout()
    text_label = QLabel(about_text)
    text_label.setTextFormat(Qt.RichText)
    text_label.setOpenExternalLinks(True)
    text_label.setWordWrap(True)
    layout.addWidget(text_label)
    image_label = QLabel()
    image_path = str(Path(__file__).parent / ".." / "resources" / "Funded_by_EU_Eyes4ICU.png")
    pixmap = QPixmap(image_path)
    image_label.setPixmap(pixmap.scaled(400, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    image_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(image_label)
    about_widget.setLayout(layout)
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle("About EyE Annotation Tool")
    msg_box.setIcon(QMessageBox.NoIcon)
    msg_box.layout().addWidget(about_widget, 0, 0, 1, msg_box.layout().columnCount())
    msg_box.exec_()
