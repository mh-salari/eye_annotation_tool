# EyE Annotation Tool

[![PyPI](https://img.shields.io/pypi/v/eye_annotation_tool)](https://pypi.org/project/eye_annotation_tool/)
[![Downloads](https://static.pepy.tech/badge/eye_annotation_tool)](https://pepy.tech/project/eye_annotation_tool)
[![License](https://img.shields.io/pypi/l/eye_annotation_tool)](https://github.com/mh-salari/eye_annotation_tool/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18723581.svg)](https://doi.org/10.5281/zenodo.18723581)

A Qt-based desktop tool for annotating pupil, limbus (iris), eyelid, and glints in eye images. Supports monocular and binocular projects, auto-detector plugins for each annotation type, and per-eye carry-across-frames workflows.

<p align="center">
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/eye_annotation_tool/resources/main_page.png" alt="EyE Annotation Tool Main Page" width="800">
</p>

## Features

- Monocular and binocular projects; per-eye overrides for ROI, carry, and defaults.
- Manual annotation of pupil ellipse, limbus ellipse, eyelid mask, and glints.
- Auto-detector plugins per annotation type; live detection re-runs on image load and plugin swap.
- Built-in auto-detectors backed by [`lavan`](https://github.com/mh-salari/lavan) (pupil, limbus, glint).
- Project sessions with persistent defaults, undo, brightness/zoom controls, and review mode for revisiting an existing project.
- Plugin system with three discovery channels (built-in, env-var directories, Python entry-points).
- CLI flags for batch use: `--images`, `--review`, `--auto-detectors`.

## Built-in auto-detectors

| Annotation | Detector | Backend |
|---|---|---|
| Pupil | `pupil_labs_2d` | `lavan.pupil_detector_2d` (Pupil Labs 2D detector) |
| Pupil | `threshold_pupil` | `lavan.detect` (threshold + ellipse fit) |
| Limbus | `daugman_limbus` | `lavan.boundary` (Daugman integro-differential / active contour) |
| Glint | `threshold_glint` | `lavan.detect` (threshold + shape-quality gates) |

See [`auto_detectors/README.md`](eye_annotation_tool/auto_detectors/README.md) for the full plugin contract.

## Installation

### Requirements
- Python ≥3.10

### From PyPI

```bash
pip install eye_annotation_tool
```

### Using [uv](https://docs.astral.sh/uv/)

```bash
uv pip install eye_annotation_tool
```

Or, to add it to an existing uv project:

```bash
uv add eye_annotation_tool
```

### From source (editable / development)

```bash
git clone https://github.com/mh-salari/eye_annotation_tool.git
cd eye_annotation_tool
uv sync
```

Or with pip:

```bash
git clone https://github.com/mh-salari/eye_annotation_tool.git
cd eye_annotation_tool
python3 -m pip install -e .
```

## Usage

```bash
eye_annotation_tool
```

Or as a module:

```bash
python -m eye_annotation_tool
```

Common CLI flags:

```bash
# Open a project and load extra images on top
eye_annotation_tool --project my_project.json --images img1.png img2.png

# Re-annotate a subset of images against an existing project (read-only)
eye_annotation_tool --project my_project.json --review img1.png img2.png

# Enable only a subset of auto-detectors this session
eye_annotation_tool --auto-detectors pupil,limbus
```

See `eye_annotation_tool --help` for the full flag list.

## Adding Custom Plugins

Every auto-detector is a self-contained plugin that owns its algorithm,
its Qt panel, its serialization, its overlay drawing and its colour
palette — adding one requires **no edits to the core application**.
Three discovery channels are scanned at startup:

- **Built-in** — `eye_annotation_tool/auto_detectors/plugins/` (for
  plugins contributed upstream).
- **Env-var directories** — `EYE_ANNOTATION_PLUGIN_PATH`
  (`os.pathsep`-separated) for drop-in `.py` files. Easiest path for a
  one-off plugin: write a single file, point the env var at its
  directory, restart the app.
- **Python entry-points** — `[project.entry-points."eye_annotation_tool.plugins"]`
  in any installed distribution. For pip-installable plugin packages.

The full plugin authoring guide — minimal example, the `DetectorPlugin`
contract, optional panel signals, mask + ROI rendering — lives in the
[Plugin Development Guide](eye_annotation_tool/auto_detectors/README.md).

## Citing

If you use this software, please cite it using the following BibTeX entry:

```bibtex
@software{salari2025eye,
  author    = {Salari, Mohammadhossein},
  title     = {{EyE Annotation Tool}},
  year      = {2026},
  url       = {https://github.com/mh-salari/eye_annotation_tool},
  doi       = {10.5281/zenodo.18723470},
  license   = {MIT}
}
```

You can also click the "Cite this repository" button on the GitHub page for more citation formats.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

This project has received funding from the European Union's Horizon Europe research and innovation funding program under grant agreement No 101072410, Eyes4ICU project.

<p align="center">
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/eye_annotation_tool/resources/Funded_by_EU_Eyes4ICU.png" alt="Funded by EU Eyes4ICU" width="500">
</p>