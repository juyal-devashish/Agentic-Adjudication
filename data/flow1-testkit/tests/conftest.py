import json
import shutil
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(KIT / "tools"))

SCEN = KIT / "inbox_scenarios"


def pytest_configure(config):
    config.addinivalue_line("markers", "ocr: needs Tesseract OCR (slower)")
    config.addinivalue_line("markers", "slow: regenerates data")


@pytest.fixture(scope="session")
def kit() -> Path:
    return KIT


@pytest.fixture(scope="session")
def expected():
    return json.loads((KIT / "expected" / "intake_expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def corpus():
    return json.loads((KIT / "ground_truth" / "corpus_index.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def identities():
    return json.loads((KIT / "identities" / "identities_SYNTHETIC.json").read_text(encoding="utf-8"))["subjects"]


@pytest.fixture
def pipe(tmp_path):
    from adjud.intake import IntakePipeline
    return IntakePipeline(tmp_path / "store", KIT / "config", KIT / "edc_mock")


@pytest.fixture
def run_sequence(pipe, expected):
    """Import the standard sequence of files and return {file: ImportResult}."""
    def _run(upto=None):
        out = {}
        for f in expected["sequence"]:
            out[f] = pipe.import_file(SCEN / f)
            if f == upto:
                break
        return out
    return _run


def ocr_ok() -> bool:
    return shutil.which("tesseract") is not None


needs_ocr = pytest.mark.skipif(not ocr_ok(), reason="Tesseract not installed")
