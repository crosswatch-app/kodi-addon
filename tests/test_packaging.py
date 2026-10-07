"""What ships: the staging script decides what a test install and a release zip contain."""

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "docker" / "scripts" / "stage.py"


def _run(*args: str) -> str:
    done = subprocess.run([sys.executable, str(STAGE), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _shipped(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def test_a_staged_folder_holds_only_what_kodi_loads(tmp_path):
    _run("folder", str(tmp_path / "out"))
    shipped = _shipped(tmp_path / "out")
    assert {"addon.xml", "service.py", "configure.py", "LICENSE", "changelog.txt"} <= shipped
    assert "resources/settings.xml" in shipped
    assert "resources/lib/service/main.py" in shipped
    for path in shipped:
        top = path.split("/")[0]
        assert top in {"addon.xml", "service.py", "configure.py", "LICENSE", "changelog.txt", "resources", "icon.png", "fanart.jpg"}, path
        assert "__pycache__" not in path and not path.endswith(".pyc"), path


def test_restaging_removes_what_is_no_longer_shipped(tmp_path):
    out = tmp_path / "out"
    _run("folder", str(out))
    (out / "stale.txt").write_text("left over")
    _run("folder", str(out))
    assert not (out / "stale.txt").exists()


def test_a_folder_that_is_not_this_addon_is_never_replaced(tmp_path):
    out = tmp_path / "someone-elses"
    out.mkdir()
    (out / "keep.txt").write_text("not ours")
    done = subprocess.run([sys.executable, str(STAGE), "folder", str(out)], capture_output=True, text=True)
    assert done.returncode != 0
    assert (out / "keep.txt").read_text() == "not ours"


def test_the_release_zip_holds_one_folder_named_after_the_addon(tmp_path):
    path = Path(_run("zip", str(tmp_path)))
    # Tilde in Kodi's files, hyphen in the file name, as for the tag.
    assert path.name.startswith("service.crosswatch-v") and "~" not in path.name
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert all(name.startswith("service.crosswatch/") for name in names)
    assert "service.crosswatch/addon.xml" in names
