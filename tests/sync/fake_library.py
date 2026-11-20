"""A Kodi video library behind JSON-RPC, answering the way Kodi does.

Paging follows VideoLibrary.GetMovies and friends: `limits.start`/`end` slice the list and
`limits.total` is the full count. `properties: []` returns ids only.

Writes follow VideoLibrary.cpp SetMovieDetails/SetEpisodeDetails: fields are set as given,
and an episode write first copies the play count and last-played date of another watched
copy of the same show, season and episode (VideoDatabase.cpp SetDetailsForEpisode), silently,
before a play count or date in the request is applied over it. A play-count or date change
is announced as OnUpdate, with "playcount" only when the count changed and "transaction"
during a scan (VideoDatabase.cpp SetPlayCount); rating- and resume-only writes announce
nothing. Announcements queue in `announcements` for the test to deliver when it chooses, as
Kodi delivers them asynchronously.
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
        self.announcements: list[dict[str, Any]] = []
        self.scanning = False
        self.writes: list[tuple[str, dict[str, Any]]] = []

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
        out["VideoLibrary.GetMovieDetails"] = lambda p: self._details("movies", "movieid", "moviedetails", p)
        out["VideoLibrary.GetEpisodeDetails"] = lambda p: self._details("episodes", "episodeid", "episodedetails", p)
        out["VideoLibrary.GetTVShowDetails"] = lambda p: self._details("tvshows", "tvshowid", "tvshowdetails", p)
        out["VideoLibrary.SetMovieDetails"] = lambda p: self._set("movies", "movieid", "movie", p)
        out["VideoLibrary.SetEpisodeDetails"] = lambda p: self._set("episodes", "episodeid", "episode", p)
        return out

    def row(self, kind: str, kodi_id: int) -> dict[str, Any]:
        id_field = {"movies": "movieid", "episodes": "episodeid", "tvshows": "tvshowid"}[kind]
        return next(r for r in getattr(self, kind) if r[id_field] == kodi_id)

    def _details(self, kind: str, id_field: str, key: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            row = self.row(kind, int(params[id_field]))
        except StopIteration:
            raise LookupError(f"Invalid params: no {id_field} {params[id_field]}") from None
        wanted = params.get("properties") or []
        return {
            key: {id_field: row[id_field], "label": row.get("label", ""), **{p: row[p] for p in wanted if p in row}}
        }

    def _set(self, kind: str, id_field: str, kodi_type: str, params: dict[str, Any]) -> dict[str, Any]:
        row = self.row(kind, int(params[id_field]))
        self.writes.append((kodi_type, dict(params)))
        before = (row["playcount"], row["lastplayed"])
        if kind == "episodes":
            copy = next(
                (e for e in self.episodes if e is not row and e["tvshowid"] == row["tvshowid"] and e["season"] == row["season"]
                 and e["episode"] == row["episode"] and e["playcount"] > 0),
                None,
            )  # fmt: skip
            if copy is not None:
                row["playcount"], row["lastplayed"] = copy["playcount"], copy["lastplayed"]
        for name in ("userrating", "resume"):
            if name in params:
                row[name] = params[name]
        wanted = (params.get("playcount", before[0]), params.get("lastplayed", before[1]))
        if kind == "episodes":
            # Kodi keeps play count, last played and the bookmark per file, so every episode
            # in a multi-episode file shares them.
            for other in self.episodes:
                if other is not row and other["file"] == row["file"]:
                    other["resume"] = row["resume"]
        if wanted != before:
            # Applied after the copy, and announced, as JSON-RPC's SetPlayCount call does.
            row["playcount"], row["lastplayed"] = wanted
            if kind == "episodes":
                for other in self.episodes:
                    if other is not row and other["file"] == row["file"]:
                        other["playcount"], other["lastplayed"] = wanted
            data: dict[str, Any] = {"item": {"id": row[id_field], "type": kodi_type}}
            if wanted[0] != before[0]:
                data["playcount"] = wanted[0]
            if self.scanning:
                data["transaction"] = True
            self.announcements.append(data)
        return {"result": "OK"}

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
