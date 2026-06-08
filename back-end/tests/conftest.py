"""Shared pytest helpers for the ARB Bot backend test suite.

Conventions:
  - Integration tests are marked ``@pytest.mark.integration`` and must skip
    cleanly when the required env vars or sibling feature branches are absent.
  - Tests are located at ``back-end/tests/`` and ``pytest`` is run from the
    ``back-end/`` directory (pytest.ini lives there).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Make ``import file_processing...``, ``import search...``, ``import app``
# resolvable when pytest is invoked from anywhere.
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def require_env(*names: str) -> None:
    """Skip the calling test if any of the named env vars are missing."""
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        pytest.skip(f"Required env var(s) not set: {', '.join(missing)}")


@pytest.fixture(scope="session")
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture(scope="session")
def sample_asd_path(backend_root: Path) -> Path:
    """Path to ``sample_asd.docx``; build it on demand if missing."""
    p = backend_root / "file_processing" / "data" / "sample_asd.docx"
    if not p.exists():
        try:
            from file_processing.build_sample_asd import build  # type: ignore
        except Exception as e:
            pytest.skip(f"sample_asd.docx missing and builder not importable: {e}")
        build()
    return p


@pytest.fixture
def flask_client():
    """Flask test client. Skips if ``app`` cannot be imported (e.g. missing deps)."""
    try:
        from app import app  # type: ignore
    except Exception as e:
        pytest.skip(f"Cannot import Flask app: {e}")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
