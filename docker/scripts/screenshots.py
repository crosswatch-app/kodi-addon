#!/usr/bin/env python3
"""Capture every CrossWatch window into docs/screenshots/<screen>/, cropped to the window.

Runs against the Flatpak Kodi on an X display (the test rig): installs this checkout and a
development-only helper add-on (screenshot_helper/), restarts Kodi so new strings and the
helper load, then opens each scene in screenshot_helper/scenes.json with demo data and
captures it. Kodi's debug overlay is switched off for the run and restored afterwards.

    screenshots.py                  every scene
    screenshots.py NAME [NAME ...]  only these scenes
    screenshots.py --no-restart     Kodi already runs this checkout and the helper
    screenshots.py --show TITLE     use this library show, --movie TITLE this film (default:
                                    a random one from --show-folder / --movie-folder)

A new window needs a scene (tests/test_screenshot_scenes.py fails without one), so a run
of this script recaptures every window, new or changed. Needs ImageMagick (import, convert)
and Estuary as the active skin, so the committed images share one look.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "docker" / "scripts"
HELPER = SCRIPTS / "screenshot_helper"
HELPER_ID = "script.crosswatch.screenshots"
WINDOWS = ROOT / "resources" / "skins" / "Default" / "1080i"
OUT = ROOT / "docs" / "screenshots"
KODI_DATA = Path.home() / ".var" / "app" / "tv.kodi.Kodi" / "data"
# Space kept around the window's panel, so its rounded corners and border are not clipped.
MARGIN = 24
SHOW_LOG_INFO = "debug.showloginfo"


def rpc(url: str, method: str, params: dict | None = None) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        reply = json.load(response)
    if "error" in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply.get("result")


def alive(url: str) -> bool:
    try:
        return rpc(url, "JSONRPC.Ping") == "pong"
    except OSError:
        return False


def wait_for(condition, what: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.5)
    sys.exit(f"timed out waiting for {what}")


def install(url: str, display: str) -> None:
    subprocess.run([str(SCRIPTS / "setup.sh"), "--native"], check=True, stdout=subprocess.DEVNULL)
    target = KODI_DATA / "addons" / HELPER_ID
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(HELPER, target, ignore=shutil.ignore_patterns("__pycache__"))
    if alive(url):
        rpc(url, "Application.Quit")
        wait_for(lambda: not alive(url), "Kodi to quit")
        time.sleep(3)
    subprocess.Popen(
        ["flatpak", "run", "tv.kodi.Kodi"],
        env={**os.environ, "DISPLAY": display},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    wait_for(lambda: alive(url), "Kodi to start", timeout=120)
    # The home screen is still building when the web server answers.
    time.sleep(8)
    rpc(url, "Addons.SetAddonEnabled", {"addonid": HELPER_ID, "enabled": True})


def panel(window: str) -> tuple[int, int, int, int]:
    """The first group under <controls> is the window's panel; its box is the crop."""
    group = ElementTree.parse(WINDOWS / window).getroot().find("controls/control[@type='group']")
    if group is None:
        sys.exit(f"{window}: no panel group to crop to")
    left, top, width, height = (int(group.findtext(key) or 0) for key in ("left", "top", "width", "height"))
    return left - MARGIN, top - MARGIN, width + 2 * MARGIN, height + 2 * MARGIN


def active(url: str, window: str) -> bool:
    condition = f"Window.IsActive({window})"
    result = rpc(url, "XBMC.GetInfoBooleans", {"booleans": [condition]})
    return isinstance(result, dict) and bool(result.get(condition))


# What a scene's "library" value names: the JSON-RPC call, its result key, and the item key.
LIBRARY = {
    "tvshow": ("VideoLibrary.GetTVShows", "tvshows"),
    "movie": ("VideoLibrary.GetMovies", "movies"),
}


def pick(url: str, kind: str, title: str | None, folder: str) -> dict:
    """A library item with poster art; its title, poster and year fill the scenes that ask.

    Random unless a title is given: the example changes as the library does, which is fine
    for screenshots that show the window rather than the show.
    """
    method, key = LIBRARY[kind]
    result = rpc(url, method, {"properties": ["title", "year", "art", "file"]})
    items = (result.get(key) or []) if isinstance(result, dict) else []
    if title is None:
        # Titles that open with quotes or punctuation make odd examples.
        items = [i for i in items if folder in str(i.get("file") or "") and str(i.get("title") or "")[:1].isalnum()]
    usable = [i for i in items if (i.get("art") or {}).get("poster") and (title is None or i.get("title") == title)]
    if not usable:
        sys.exit(f"no library {kind} with a poster {f'titled {title!r}' if title else f'under {folder}'}")
    item = random.choice(usable)
    return {"title": item["title"], "year": item.get("year") or None, "poster": item["art"]["poster"]}


def pick_many(url: str, kind: str, folder: str, count: int) -> list[dict]:
    method, key = LIBRARY[kind]
    result = rpc(url, method, {"properties": ["title", "year", "art", "file"]})
    items = (result.get(key) or []) if isinstance(result, dict) else []
    usable = [
        i for i in items
        if folder in str(i.get("file") or "") and str(i.get("title") or "")[:1].isalnum() and (i.get("art") or {}).get("poster")
    ]
    chosen = sorted(random.sample(usable, min(count, len(usable))), key=lambda i: str(i["title"]).casefold())
    return [{"title": i["title"], "year": i.get("year") or None, "poster": i["art"]["poster"]} for i in chosen]


def toast(url: str) -> bool:
    condition = "Window.IsVisible(notification)"
    result = rpc(url, "XBMC.GetInfoBooleans", {"booleans": [condition]})
    return isinstance(result, dict) and bool(result.get(condition))


def capture(url: str, display: str, name: str, scene: dict) -> Path:
    window = scene["window"]
    rpc(url, "Addons.ExecuteAddon", {"addonid": HELPER_ID, "params": [name]})
    wait_for(lambda: active(url, window), f"{name} to open", timeout=20)
    time.sleep(1)  # the open animation
    # Toasts (the add-on's own after a restart, or another add-on's) draw over the window.
    wait_for(lambda: not toast(url), "notifications to clear", timeout=30)
    for key in scene["keys"]:
        rpc(url, f"Input.{key}")
        time.sleep(0.4)
    # Focus and tick changes animate; wait them out so reruns give identical files.
    time.sleep(1.5)
    left, top, width, height = panel(window)
    folder = OUT / scene["group"]
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / f".{name}.raw.png"
    subprocess.run(["import", "-display", display, "-window", "root", str(raw)], check=True)
    out = folder / f"{name}.png"
    # -strip drops the timestamps ImageMagick writes, so an unchanged window gives an
    # unchanged file and git shows only real differences.
    subprocess.run(
        ["convert", str(raw), "-crop", f"{width}x{height}+{left}+{top}", "+repage", "-strip", str(out)], check=True
    )
    raw.unlink()
    rpc(url, "Input.Back")
    wait_for(lambda: not active(url, window), f"{name} to close", timeout=10)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenes", nargs="*", help="scene names (default: all)")
    parser.add_argument("--display", default=":20")
    parser.add_argument("--rpc", default="http://127.0.0.1:8081/jsonrpc")
    parser.add_argument("--no-restart", action="store_true", help="skip install and restart")
    parser.add_argument("--show", help="library show title for the scenes that show one")
    parser.add_argument("--movie", help="library film title for the scenes that show one")
    parser.add_argument("--show-folder", default="/TVSeries/EN/", help="pick shows whose path holds this")
    parser.add_argument("--movie-folder", default="/Movies/", help="pick films whose path holds this")
    args = parser.parse_args()

    scenes = json.loads((HELPER / "scenes.json").read_text(encoding="utf-8"))
    unknown = set(args.scenes) - set(scenes)
    if unknown:
        sys.exit(f"unknown scenes: {', '.join(sorted(unknown))}")
    chosen = {name: scenes[name] for name in (args.scenes or scenes)}

    if not args.no_restart:
        install(args.rpc, args.display)
    skin = rpc(args.rpc, "Settings.GetSettingValue", {"setting": "lookandfeel.skin"})
    if not isinstance(skin, dict) or skin.get("value") != "skin.estuary":
        sys.exit("switch Kodi to Estuary first: the committed screenshots are all taken in it")

    library = {
        "tvshow": pick(args.rpc, "tvshow", args.show, args.show_folder),
        "movie": pick(args.rpc, "movie", args.movie, args.movie_folder),
    }
    # A list's rows: a handful of shows, sorted as the screens sort them.
    library["shows"] = pick_many(args.rpc, "tvshow", args.show_folder, 8)
    (KODI_DATA / "addons" / HELPER_ID / "library.json").write_text(json.dumps(library), encoding="utf-8")
    print(f"show: {library['tvshow']['title']}, film: {library['movie']['title']}")
    OUT.mkdir(exist_ok=True)
    overlay = rpc(args.rpc, "Settings.GetSettingValue", {"setting": SHOW_LOG_INFO})
    was_on = isinstance(overlay, dict) and overlay.get("value") is True
    rpc(args.rpc, "Settings.SetSettingValue", {"setting": SHOW_LOG_INFO, "value": False})
    try:
        for name, scene in chosen.items():
            print(capture(args.rpc, args.display, name, scene).relative_to(ROOT))
    finally:
        if was_on:
            rpc(args.rpc, "Settings.SetSettingValue", {"setting": SHOW_LOG_INFO, "value": True})


if __name__ == "__main__":
    main()
