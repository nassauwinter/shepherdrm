"""Enforce repository-wide module-purpose documentation for Python files."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXCLUDED_PARTS = {".venv", "__pycache__", "_build"}


def test_every_python_file_has_a_module_docstring() -> None:
    """Every maintained Python file begins with a non-empty module docstring."""
    undocumented = [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*.py")
        if not EXCLUDED_PARTS.intersection(path.parts)
        and ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) is None
    ]

    assert undocumented == []
