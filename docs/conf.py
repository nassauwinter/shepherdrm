"""Configure the Sphinx documentation build for Shepherd RM."""

from shepherd_rm import __version__

project = "Shepherd RM"
author = "Shepherd RM contributors"
release = __version__

extensions = ["myst_parser"]
source_suffix = {
    ".md": "markdown",
}
exclude_patterns = ["_build"]

html_theme = "sphinx_rtd_theme"
