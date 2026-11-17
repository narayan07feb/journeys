"""`journeys setup`: fetch an upstream Speedrun release into third_party/, checked against its SHA-256."""
from __future__ import annotations

import hashlib
import json
import platform
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = "hubastard/speedrun"


def _asset_name(version: str) -> str:
    system, machine = platform.system(), platform.machine()
    if system == "Darwin" and machine == "arm64":
        return f"speedrun-{version}-macos-arm64.tar.gz"
    if system == "Linux" and machine in ("x86_64", "AMD64"):
        return f"speedrun-{version}-linux-x86_64.tar.gz"
    raise RuntimeError(f"Speedrun has no release for {system} {machine}")


def latest_version() -> str:
    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=30) as r:
        return json.load(r)["tag_name"]


def install(dest_root: Path, version: str | None = None) -> Path:
    version = version or latest_version()
    dest = dest_root / version
    if (dest / "speedrun").exists():
        return dest / "speedrun"
    name = _asset_name(version)
    base = f"https://github.com/{REPO}/releases/download/{version}/{name}"
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / name
        urllib.request.urlretrieve(base, archive)
        with urllib.request.urlopen(base + ".sha256", timeout=30) as r:
            want = r.read().decode().split()[0].lower()
        got = hashlib.sha256(archive.read_bytes()).hexdigest()
        if got != want:
            raise RuntimeError(f"{name}: SHA-256 {got} does not match the published {want}")
        unpack = Path(tmp) / "unpack"
        with tarfile.open(archive) as tf:
            tf.extractall(unpack, filter="data")  # refuses absolute paths, .. and device files
        found = [p for p in unpack.rglob("speedrun") if p.is_file()]
        if not found:
            raise RuntimeError(f"{name} has no speedrun binary")
        dest.mkdir(parents=True, exist_ok=True)
        for item in found[0].parent.iterdir():
            if item.name.startswith("._"):  # macOS metadata side files in the archive
                continue
            item.rename(dest / item.name)
    (dest / "speedrun").chmod(0o755)
    return dest / "speedrun"
