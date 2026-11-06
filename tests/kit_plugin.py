"""Pytest plugin: run the Flow 1 test kit's tests against OUR adjud package.

The kit's conftest.py puts data/flow1-testkit/ first on sys.path, which would make
`import adjud` load the kit's reference implementation. Importing our package here,
before any conftest runs, pins `adjud` (and therefore every `adjud.*` submodule, which
is resolved through `adjud.__path__`) to this repository.

    uv run python -m pytest -p tests.kit_plugin data/flow1-testkit/tests
"""

from __future__ import annotations

from pathlib import Path

import adjud

OUR_PACKAGE = Path(__file__).resolve().parents[1] / "adjud"


def _check_package() -> None:
    loaded = Path(adjud.__file__).resolve().parent
    if loaded != OUR_PACKAGE:
        raise RuntimeError(f"kit tests would run against {loaded}, not {OUR_PACKAGE}")


def pytest_configure(config) -> None:
    _check_package()


def pytest_report_header(config) -> str:
    return f"adjud under test: {OUR_PACKAGE}"
