"""Custom widget components for the application."""

from PyQt5.QtWidgets import QPushButton, QWidget


class MaterialButton(QPushButton):
    """Custom styled button with material design appearance."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        """Initialize the MaterialButton."""
        super().__init__(text, parent)
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #00525f;
                border: none;
                color: white;
                padding: 10px 20px;
                text-align: center;
                text-decoration: none;
                font-size: 16px;
                margin: 4px 2px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #007f76;
            }
            QPushButton:pressed {
                background-color: #96d574;
            }
        """
        )
