from PyQt5.QtWidgets import QMessageBox

class NavigationController:
    def __init__(self, main_window):
        self.main_window = main_window

    def next_image(self):
        if self.main_window.current_image_index < len(self.main_window.image_paths) - 1:
            if self.main_window.annotation_modified:
                reply = self.show_save_dialog()
                if reply == QMessageBox.Cancel:
                    return
                elif reply == QMessageBox.Yes:
                    self.main_window.save_current_annotations()

            self.main_window.current_image_index += 1
            self.main_window.load_current_image()
            self.main_window.image_list_widget.setCurrentRow(self.main_window.current_image_index)

    def prev_image(self):
        if self.main_window.current_image_index > 0:
            if self.main_window.annotation_modified:
                reply = self.show_save_dialog()
                if reply == QMessageBox.Cancel:
                    return
                elif reply == QMessageBox.Yes:
                    self.main_window.save_current_annotations()

            self.main_window.current_image_index -= 1
            self.main_window.load_current_image()
            self.main_window.image_list_widget.setCurrentRow(self.main_window.current_image_index)

    def on_image_selected(self, item):
        selected_index = self.main_window.image_list_widget.row(item)
        if selected_index != self.main_window.current_image_index:
            if self.main_window.annotation_modified:
                reply = self.show_save_dialog()
                if reply == QMessageBox.Cancel:
                    self.main_window.image_list_widget.setCurrentRow(self.main_window.current_image_index)
                    return
                elif reply == QMessageBox.Yes:
                    self.main_window.save_current_annotations()

            self.main_window.current_image_index = selected_index
            self.main_window.load_current_image()

    def show_save_dialog(self):
        return QMessageBox.question(
            self.main_window,
            "Save Changes",
            "Do you want to save the changes to the current image?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes
        )
