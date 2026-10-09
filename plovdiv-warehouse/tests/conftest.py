"""Pytest fixtures and configuration for plovdiv-warehouse test suite."""

import sys
from pathlib import Path

# Ensure repository root and src directory are on sys.path for test runs
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
