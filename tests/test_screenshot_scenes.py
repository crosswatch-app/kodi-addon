"""Every window has a screenshot scene, and every scene has a committed screenshot.

The capture itself needs a running Kodi (docker/scripts/screenshots.py); these checks keep
a new window or a new scene from landing without one.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "resources" / "skins" / "Default" / "1080i"
HELPER = ROOT / "docker" / "scripts" / "screenshot_helper"
SCENES = json.loads((HELPER / "scenes.json").read_text(encoding="utf-8"))


def test_every_window_has_a_scene():
    windows = {path.name for path in WINDOWS.glob("*.xml")}
    assert windows
    assert windows <= {scene["window"] for scene in SCENES.values()}


def test_every_scene_names_a_shipped_window():
    for name, scene in SCENES.items():
        assert (WINDOWS / scene["window"]).is_file(), name


def test_every_scene_has_a_committed_screenshot():
    for name in SCENES:
        assert (ROOT / "screenshots" / f"{name}.png").is_file(), f"run docker/scripts/screenshots.py for {name}"


def test_scene_names_are_file_and_argument_safe():
    """A scene name becomes a file name and a RunScript argument, which Kodi splits on commas."""
    for name in SCENES:
        assert name.replace("-", "").isalnum() and name == name.lower(), name


def test_a_scene_takes_a_library_item_or_gives_its_own_text():
    """A "library" scene gets title and poster from Kodi's library at capture time."""
    for name, scene in SCENES.items():
        if "library" in scene:
            assert scene["library"] in {"tvshow", "movie"}, name
            assert "title" not in scene and "poster" not in scene, name
        else:
            assert {"title", "subtitle", "poster"} <= set(scene), name
