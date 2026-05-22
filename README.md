# EyE Annotation Tool

[![PyPI](https://img.shields.io/pypi/v/eye_annotation_tool)](https://pypi.org/project/eye_annotation_tool/)
[![Downloads](https://static.pepy.tech/badge/eye_annotation_tool)](https://pepy.tech/project/eye_annotation_tool)
[![License](https://img.shields.io/pypi/l/eye_annotation_tool)](https://github.com/mh-salari/eye_annotation_tool/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18723470.svg)](https://doi.org/10.5281/zenodo.18723470)

A Qt-based desktop tool for annotating pupil, limbus (iris), eyelid, and glints in eye images. Supports monocular and binocular projects, auto-detectors for each annotation type, and per-eye carry-across-frames workflows. Auto-detectors come from [cheshm](https://github.com/mh-salari/cheshm).

<p align="center">
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/eye_annotation_tool/resources/main_page.png" alt="EyE Annotation Tool Main Page" width="800">
</p>

## Features

- Monocular and binocular projects; per-eye overrides for ROI, carry, and defaults.
- Manual annotation of pupil ellipse, limbus ellipse, eyelid mask, and glints.
- Live auto-detection that re-runs on image load and detector swap.
- Auto-detectors are every pupil / glint / limbus detector cheshm exposes (`Simple`, `ElSe`, `ExCuSe`, `PuRe`, `PuReST`, `PupilLabs2D`, `Starburst`, `Swirski2D` for pupil; `Simple` for glint; `active_contour`, `integro_differential`, `pupil_guided` for limbus). See [cheshm's detector list](https://github.com/mh-salari/cheshm#detectors) for licences.
- Project sessions with persistent defaults, undo, brightness/zoom controls, and review mode.
- CLI flags for batch use: `--images`, `--review`, `--auto-detectors`.

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

## Adding your own detectors

To add a custom detector without modifying this project, drop a `.py` file in `~/.config/eye_annotation_tool/plugins/` (or any directory listed in the `EYE_ANNOTATION_PLUGINS` env var) that declares a module-level `PLUGINS = [DetectorPlugin(...)]` list. See [`eye_annotation_tool/auto_detectors/README.md`](eye_annotation_tool/auto_detectors/README.md) for the full plugin contract and a minimal example.

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

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

This project has received funding from the European Union's Horizon Europe research and innovation funding program under grant agreement No 101072410, Eyes4ICU project.

<p align="center">
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/eye_annotation_tool/resources/Funded_by_EU_Eyes4ICU.png" alt="Funded by EU Eyes4ICU" width="500">
</p>