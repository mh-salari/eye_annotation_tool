"""Help > About dialog."""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from .._version import version as _app_version
from .theme import theme


def show_about_dialog(parent: QWidget) -> None:
    """Show the About dialog parented to ``parent``."""
    about_text = (
        "<h3>EyE Annotation Tool</h3>"
        "<p>A tool to annotate eye images for pupil, limbus and eyelid detection.</p>"
        "<p>Developed by "
        "<a href='https://mh-salari.ir/'"
        f"style='color: {theme.color('link')};'>Mohammadhossein Salari</a></p>"
        f"<p>Current version: {_app_version}</p>"
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
