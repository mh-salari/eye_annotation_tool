# Creating a New Plugin for EyE Annotation Tool

This guide explains how to create a new pupil, limbus or eyelid detector plugin for the EyE Annotation Tool application.

## Steps to Create a New Plugin

1. Create a new Python file in the appropriate directory:
   - For pupil detectors: `annotation_app/auto_detectors/plugins/pupil_detectors/`
   - For limbus detectors: `annotation_app/auto_detectors/plugins/limbus_detectors/`
   - For eyelid detectors: `annotation_app/auto_detectors/plugins/eyelid_detectors/`

2. Import the necessary modules:
   ```python
   from annotation_app.auto_detectors.plugin_interface import DetectorPlugin
   import numpy as np
   ```

3. Create a new class that inherits from `DetectorPlugin`:
   ```python
   class MyNewDetector(DetectorPlugin):
       def __init__(self):
           # Initialize your detector here
           pass

       def detect(self, image_path):
           # Implement your detection algorithm here
           # Return the ellipse parameters and points
           return ellipse, points

       @property
       def name(self):
           # Return a unique name for your detector
           return "my_new_detector"
   ```

4. Implement the `detect` method:
   - Input: `image_path` path of the eye image
   - Output: `ellipse` (dict with keys: 'center', 'axes', 'angle') and `points` (list of point coordinates)

5. Set a unique `name` for your detector in the `name` property.

6. Save your file with a descriptive name (e.g., `my_new_detector.py`).

The plugin manager will automatically discover and load your new plugin when the application starts.

## Example

See `placeholder_limbus_detector.py` for a simple example of a detector plugin.

## Notes

- Ensure your detector class inherits from `DetectorPlugin` and implements all required methods.
- The `name` property should return a unique string to identify your detector.
- Your `detect` method should handle various image sizes and conditions robustly.
