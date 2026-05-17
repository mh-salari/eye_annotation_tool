# EyE Annotation Tool

[![PyPI](https://img.shields.io/pypi/v/eye_annotation_tool)](https://pypi.org/project/eye_annotation_tool/)
[![Downloads](https://static.pepy.tech/badge/eye_annotation_tool)](https://pepy.tech/project/eye_annotation_tool)
[![License](https://img.shields.io/pypi/l/eye_annotation_tool)](https://github.com/mh-salari/eye_annotation_tool/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18723581.svg)](https://doi.org/10.5281/zenodo.18723581)

EyE Annotation Tool is a tool for annotating pupil, iris and eyelid in eye images. It provides a user-friendly interface for manual annotation and supports auto-detector plugins.

<p align="center">
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/eye_annotation_tool/resources/main_page.png" alt="EyE Annotation Tool Main Page" width="800">
</p>

## Features

- Load and navigate through multiple eye images
- Manual annotation of pupil, iris, eyelid, and glints
- Auto-detector plugins for pupil, iris, eyelid, and glints
- Undo functionality for annotations
- Save and load annotations
- Extensible plugin system for custom detectors

## Installation

```bash
pip install eye_annotation_tool
```

For the latest development version:

```bash
pip install git+https://github.com/mh-salari/eye_annotation_tool.git
```

### Using uv

If you prefer [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/mh-salari/eye_annotation_tool.git
cd eye_annotation_tool
uv sync
```

## Usage

```bash
eye_annotation_tool
```

Or with uv:

```bash
uv run eye_annotation_tool
```

Or run it as a module:

```bash
python -m eye_annotation_tool
```

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