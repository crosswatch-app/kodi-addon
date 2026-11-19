"""A Kodi video library behind JSON-RPC, answering the way Kodi does.

Paging follows VideoLibrary.GetMovies and friends: `limits.start`/`end` slice the list and
`limits.total` is the full count. `properties: []` returns ids only. Later sync tasks add
writes and announcements here.
"""

from collections.abc import Callable
from typing import Any

_LISTS = {
    "VideoLibrary.GetMovies": ("movies", "movieid"),
    "VideoLibrary.GetEpisodes": ("episodes", "episodeid"),
    "VideoLibrary.GetTVShows": ("tvshows", "tvshowid"),
}


class FakeLibrary:
    def __init__(self) -> None:
        self.movies: list[dict[str, Any]] = []
        self.episodes: list[dict[str, Any]] = []
        self.tvshows: list[dict[str, Any]] = []
        self.sources: list[dict[str, str]] = []
        # Called before a page is answered, with the method and the page's start, so a test
        # can change the library between pages as another box would.
        self.before_page: Callable[[str, int], None] | None = None
        # Ids in a locked source: dropped from a page after the limit is applied but still
        # counted in the total, as Kodi does for a profile without the master lock code.
        self.locked: set[int] = set()

    def add_movie(self, movieid: int, **fields: Any) -> dict[str, Any]:
        row = {"movieid": movieid, "label": fields.get("title", ""), **_defaults(), **fields}
        self.movies.append(row)
        return row

    def add_show(self, tvshowid: int, **fields: Any) -> dict[str, Any]:
        row = {"tvshowid": tvshowid, "label": fields.get("title", ""), "uniqueid": {}, "title": "", "year": 0, **fields}
        self.tvshows.append(row)
        return row

    def add_episode(self, episodeid: int, tvshowid: int, season: int, episode: int, **fields: Any) -> dict[str, Any]:
        show = next((s for s in self.tvshows if s["tvshowid"] == tvshowid), {})
        row = {
            "episodeid": episodeid,
            "label": fields.get("title", ""),
            **_defaults(),
            "tvshowid": tvshowid,
            "showtitle": show.get("title", ""),
            "season": season,
            "episode": episode,
            **fields,
        }
        self.episodes.append(row)
        return row

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        out: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            method: (lambda params, method=method: self._list(method, params)) for method in _LISTS
        }
        out["Files.GetSources"] = lambda params: {"sources": [dict(s) for s in self.sources]}
        return out

    def _list(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        field, id_field = _LISTS[method]
        limits = params.get("limits") or {}
        start = int(limits.get("start", 0))
        if self.before_page is not None:
            self.before_page(method, start)
        rows: list[dict[str, Any]] = getattr(self, field)
        total = len(rows)
        end = int(limits.get("end", total)) if limits else total
        properties = params.get("properties") or []
        page = [
            {id_field: r[id_field], "label": r.get("label", ""), **{p: r[p] for p in properties if p in r}}
            for r in rows[start:end]
            if r[id_field] not in self.locked
        ]
        return {field: page, "limits": {"start": start, "end": min(end, total), "total": total}}


def _defaults() -> dict[str, Any]:
    return {
        "uniqueid": {},
        "title": "",
        "file": "",
        "playcount": 0,
        "lastplayed": "",
        "userrating": 0,
        "resume": {"position": 0.0, "total": 0.0},
        "dateadded": "2026-01-01 10:00:00",
    }
