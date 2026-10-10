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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from resources.lib.log import get_logger
from resources.lib.models import Viewer
from resources.lib.routes import RouteFacts

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


@dataclass(frozen=True)
class RememberedAnswer:
    """Who watched a show, and which show it was when they said so.

    title and year identify the show for a person reading the list, and guard an answer
    keyed on Kodi's own database id, which a library clean can hand to a different show.
    """

    viewers: tuple[str, ...]
    title: str | None = None
    year: int | None = None


def _parse_answer(raw: Any) -> RememberedAnswer | None:
    # A bare list is how answers were written before the title and year were kept.
    if isinstance(raw, list):
        names = tuple(str(n) for n in raw if n)
        return RememberedAnswer(viewers=names) if names else None
    if not isinstance(raw, dict) or not isinstance(raw.get("viewers"), list):
        return None
    names = tuple(str(n) for n in raw["viewers"] if n)
    if not names:
        return None
    title = raw.get("title")
    year = raw.get("year")
    return RememberedAnswer(
        viewers=names,
        title=str(title) if isinstance(title, str) and title else None,
        year=year if isinstance(year, int) and not isinstance(year, bool) else None,
    )


class PromptMemory:
    """Answers to the who-watched prompt, one per show.

    Read from disk on every call rather than cached: the settings screen edits the same
    file from another interpreter, and a change there has to reach the next playback.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def recall(self, key: str) -> RememberedAnswer | None:
        data = _read_json(self._path, {})
        return _parse_answer(data.get(key)) if isinstance(data, dict) else None

    def entries(self) -> dict[str, RememberedAnswer]:
        data = _read_json(self._path, {})
        if not isinstance(data, dict):
            return {}
        parsed = {str(key): _parse_answer(raw) for key, raw in data.items()}
        return {key: answer for key, answer in parsed.items() if answer is not None}

    def remember(self, key: str, viewers: tuple[str, ...], title: str | None = None, year: int | None = None) -> None:
        with self._lock:
            data = _read_json(self._path, {})
            if not isinstance(data, dict):
                data = {}
            data[key] = {"viewers": list(viewers), "title": title, "year": year}
            _write_json(self._path, data)

    def forget(self, key: str) -> None:
        with self._lock:
            data = _read_json(self._path, {})
            if isinstance(data, dict) and key in data:
                del data[key]
                _write_json(self._path, data)

    def forget_many(self, keys: list[str]) -> bool:
        """One write for any number of answers: a long list is cleared in one go on slow
        storage, and a failed write leaves every answer as it was."""
        with self._lock:
            data = _read_json(self._path, {})
            if not isinstance(data, dict):
                data = {}
            for key in keys:
                data.pop(key, None)
            return _write_json(self._path, data)

    def rename_viewer(self, old: str, new: str) -> int:
        """Every answer naming old names new instead, in one write; the count changed."""
        return self._rewrite_names(lambda names: [new if n == old else n for n in names])[0]

    def drop_viewer(self, name: str) -> tuple[int, int]:
        """The name taken out of every answer, in one write. An answer left with nobody is
        forgotten, so that show is asked about again. (changed, forgotten)."""
        return self._rewrite_names(lambda names: [n for n in names if n != name])

    def _rewrite_names(self, change: Callable[[list[str]], list[str]]) -> tuple[int, int]:
        with self._lock:
            data = _read_json(self._path, {})
            if not isinstance(data, dict):
                return 0, 0
            changed = forgotten = 0
            for key in list(data):
                raw = data[key]
                # Both stored shapes: a bare list predates the title and year.
                names = raw if isinstance(raw, list) else raw.get("viewers") if isinstance(raw, dict) else None
                if not isinstance(names, list):
                    continue
                after = change([str(n) for n in names])
                if after == names:
                    continue
                changed += 1
                if not after:
                    del data[key]
                    forgotten += 1
                elif isinstance(raw, list):
                    data[key] = after
                else:
                    raw["viewers"] = after
            if changed:
                _write_json(self._path, data)
            return changed, forgotten


class RouteStore:
    """The last route facts, for the settings screen, which runs in its own interpreter.

    Tied to the pairing by a token fingerprint, so facts from an earlier pairing are never
    read as this one's.
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def save(self, fingerprint: str, facts: RouteFacts) -> bool:
        return _write_json(
            self._path,
            {
                "fingerprint": fingerprint,
                "routes": facts.count,
                "accepted": sorted(facts.accepted),
                "asked": sorted(facts.asked),
            },
        )

    def load(self, fingerprint: str) -> RouteFacts | None:
        data = _read_json(self._path, None)
        if not isinstance(data, dict) or data.get("fingerprint") != fingerprint:
            return None
        count = data.get("routes")
        names = data.get("accepted")
        asked = data.get("asked")
        if not isinstance(count, int) or not isinstance(names, list) or not isinstance(asked, list):
            return None
        return RouteFacts(
            count,
            frozenset(str(n) for n in names if isinstance(n, str)),
            frozenset(str(n) for n in asked if isinstance(n, str)),
        )
