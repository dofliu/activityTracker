from __future__ import annotations

from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def samples() -> Path:
    assert SAMPLES.is_dir(), "樣本目錄不見了——下面的測試會全部變成空轉"
    return SAMPLES
