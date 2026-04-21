"""Pytest config for the experiments/ package.

Adds this directory to sys.path so tests can import `shared.*`, `agent.*`, etc.
When issue #22 (experiments/ as a uv project) lands, this file can be replaced
by a proper `[tool.pytest.ini_options] pythonpath` entry in pyproject.toml.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
