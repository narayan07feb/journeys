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


@pytest.fixture
def tappable_pack_dir(pack_dir) -> Path:
    """The example pack with tap-all and explore allowed (only ever used with the fake driver)."""
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("allowed: false", "allowed: true"))
    return pack_dir
