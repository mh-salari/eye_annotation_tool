# EyE Annotation Tool

[![PyPI](https://img.shields.io/pypi/v/eye_annotation_tool)](https://pypi.org/project/eye_annotation_tool/)
[![Downloads](https://static.pepy.tech/badge/eye_annotation_tool)](https://pepy.tech/project/eye_annotation_tool)
[![License](https://img.shields.io/pypi/l/eye_annotation_tool)](https://github.com/mh-salari/eye_annotation_tool/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18723581.svg)](https://doi.org/10.5281/zenodo.18723581)

EyE Annotation Tool is a tool for annotating pupil, iris and eyelid in eye images. It provides a user-friendly interface for manual annotation and supports AI-assisted detection.

<p align="center">
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/annotation_app/resources/main_page.png" alt="EyE Annotation Tool Main Page" width="800">
</p>

## Features

- Load and navigate through multiple eye images
- Manual annotation of pupil, iris, eyelid, and glints
- AI-assisted detection of pupil, iris, eyelid, and glints
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

> **Apple Silicon (M1/M2/M3) Note:** The `pupil-detectors` dependency only provides pre-built wheels for x86_64. On Apple Silicon Macs, you need to use an x86_64 Python via Rosetta 2:
>
> ```bash
> uv python install cpython-3.11-macos-x86_64
> uv python pin cpython-3.11-macos-x86_64
> uv sync
> ```

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

EyE Annotation Tool supports custom plugins for pupil, iris and eyelid detection. To add a new plugin:

1. Create a new Python file in the appropriate plugin directory.
2. Define your detector class in this file.
3. Ensure your detector follows the required interface.

For a detailed guide on creating plugins, see the [Plugin Development Guide](ai/README.md) in the `ai` directory.

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
<img src="https://raw.githubusercontent.com/mh-salari/eye_annotation_tool/main/annotation_app/resources/Funded_by_EU_Eyes4ICU.png" alt="Funded by EU Eyes4ICU" width="500">
</p>