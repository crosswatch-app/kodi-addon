#!/usr/bin/env python3
"""Copy exactly what Kodi loads: into a folder for a test install, or into a release zip.

An allow-list, not an exclude list. Everything else in the checkout (the venv, caches,
tests, tooling, local notes) is left behind without having to be named, so a new dev file
cannot reach a user by being forgotten.

    stage.py folder DEST   replace DEST with the add-on's files
    stage.py zip OUTDIR    write OUTDIR/<id>-v<version>.zip and print its path
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ("addon.xml", "service.py", "configure.py", "LICENSE", "changelog.txt", "resources")
# Shipped once they exist; the addon.xml <assets> entry is what makes Kodi use them.
OPTIONAL = ("icon.png", "fanart.jpg")


def _addon() -> tuple[str, str]:
    root = ElementTree.parse(ROOT / "addon.xml").getroot()
    return root.get("id") or "", root.get("version") or ""


def _copy(dest: Path) -> None:
    for name in SHIPPED + OPTIONAL:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif source.is_file():
            shutil.copy2(source, dest / name)
        elif name in SHIPPED:
            raise SystemExit(f"missing from the checkout: {name}")


def stage_folder(dest: Path) -> None:
    addon_id, _ = _addon()
    if dest.exists() and any(dest.iterdir()):
        # Emptied before copying, so only ever a previous install of this add-on.
        marker = dest / "addon.xml"
        if not marker.is_file() or ElementTree.parse(marker).getroot().get("id") != addon_id:
            raise SystemExit(f"refusing to replace {dest}: not an install of {addon_id}")
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _copy(dest)


def _build_time() -> tuple[int, int, int, int, int, int]:
    """The commit's time, so the same commit always gives the same zip, byte for byte.

    SOURCE_DATE_EPOCH wins when set, as reproducible-build tooling expects; outside a git
    checkout the zip format's earliest date is used.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        try:
            epoch = subprocess.run(
                ["git", "-C", str(ROOT), "log", "-1", "--format=%ct"], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            epoch = ""
    if not epoch:
        return (1980, 1, 1, 0, 0, 0)
    return time.gmtime(int(epoch))[:6]


def stage_zip(outdir: Path) -> Path:
    addon_id, version = _addon()
    # Kodi's files use the tilde form; file names, like tags, use a hyphen.
    target = outdir / f"{addon_id}-v{version.replace('~', '-')}.zip"
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / addon_id
        folder.mkdir()
        _copy(folder)
        stamp = _build_time()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                name = path.relative_to(tmp).as_posix()
                # Fixed time and plain permissions rather than whatever the checkout had: the
                # builder's umask and clock are not part of the release.
                if path.is_dir():
                    info = zipfile.ZipInfo(f"{name}/", date_time=stamp)
                    info.external_attr = (0o40755 << 16) | 0x10
                    archive.writestr(info, b"")
                else:
                    info = zipfile.ZipInfo(name, date_time=stamp)
                    info.external_attr = 0o100644 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
    return target


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("folder", "zip"):
        raise SystemExit("usage: stage.py folder DEST | stage.py zip OUTDIR")
    if sys.argv[1] == "folder":
        stage_folder(Path(sys.argv[2]))
    else:
        print(stage_zip(Path(sys.argv[2])))
