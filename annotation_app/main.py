import os
import sys

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from .gui import MainWindow


def run_app():
    app = QApplication(sys.argv)

    # Set the application icon
    icon_path = os.path.join(os.path.dirname(__file__), "resources", "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
