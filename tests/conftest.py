import shutil
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "settings-pack"


@pytest.fixture
def pack_dir(tmp_path) -> Path:
    """A writable copy of the example pack."""
    dst = tmp_path / "pack"
    shutil.copytree(EXAMPLE, dst)
    return dst
