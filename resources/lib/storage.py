# SPDX-License-Identifier: GPL-2.0-only
"""Local JSON persistence, behind a seam. No policy lives here.

ViewerStore is an interface because a later version pulls viewer names from CrossWatch so
they cannot drift from the route username whitelist.

Neither reads nor writes raise. A write sits on the stop path, so a full disk must cost an
attribution rather than a watch.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Protocol

from resources.lib.log import get_logger
from resources.lib.models import Viewer

_log = get_logger("storage")


class ViewerStore(Protocol):
    def viewers(self) -> list[Viewer]: ...
    def save(self, viewers: list[Viewer]) -> None: ...


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        _log.warning("storage.unreadable", name=os.path.basename(path))
        return default


def _write_json(path: str, value: Any) -> bool:
    directory = os.path.dirname(path)
    tmp = ""
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        _log.warning("storage.write_failed", name=os.path.basename(path), error=str(exc))
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


class JsonViewerStore:
    def __init__(self, path: str) -> None:
        self._path = path

    def viewers(self) -> list[Viewer]:
        rows = _read_json(self._path, [])
        if not isinstance(rows, list):
            return []
        out: list[Viewer] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            out.append(
                Viewer(
                    name=name,
                    playlists=tuple(str(p) for p in row.get("playlists") or []),
                    profiles=tuple(str(p) for p in row.get("profiles") or []),
                )
            )
        return out

    def save(self, viewers: list[Viewer]) -> None:
        _write_json(
            self._path,
            [{"name": v.name, "playlists": list(v.playlists), "profiles": list(v.profiles)} for v in viewers],
        )


class PromptMemory:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def recall(self, key: str) -> tuple[str, ...] | None:
        data = _read_json(self._path, {})
        names = data.get(key) if isinstance(data, dict) else None
        if not isinstance(names, list) or not names:
            return None
        return tuple(str(n) for n in names)

    def remember(self, key: str, names: tuple[str, ...]) -> None:
        with self._lock:
            data = _read_json(self._path, {})
            if not isinstance(data, dict):
                data = {}
            data[key] = list(names)
            _write_json(self._path, data)

    def forget_all(self) -> None:
        with self._lock:
            _write_json(self._path, {})
