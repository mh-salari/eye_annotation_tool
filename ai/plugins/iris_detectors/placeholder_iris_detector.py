import numpy as np

from ai.plugin_interface import DetectorPlugin


class PlaceholderirisDetector(DetectorPlugin):
    def __init__(self):
        pass

    def detect(self, image_path):
        height, width = 192, 192
        center = (width // 2, height // 2)
        axes = (width // 4, height // 4)  # 50% larger than the image size
        angle = 0

        # Generate 5 points on the ellipse
        t = np.linspace(0, 2 * np.pi, 5, endpoint=False)
        points = np.column_stack([axes[0] * np.cos(t), axes[1] * np.sin(t)])
        points += center

        ellipse = {
            "center": center,
            "axes": (axes[0] * 2, axes[1] * 2),  # cv2.ellipse expects full axes length
            "angle": angle,
        }

        return ellipse, points.tolist()

    @property
    def name(self):
        return "test"
