from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from resources.lib.sync.clock import kodi_to_utc, parse_utc, utc_to_kodi


@pytest.fixture(autouse=True)
def amsterdam(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "Europe/Amsterdam")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_round_trip_amsterdam_summer_and_winter() -> None:
    assert kodi_to_utc("2026-07-01 20:10:00") == "2026-07-01T18:10:00Z"
    assert kodi_to_utc("2026-01-15 20:10:00") == "2026-01-15T19:10:00Z"
    assert utc_to_kodi("2026-07-01T18:10:00Z") == "2026-07-01 20:10:00"
    assert utc_to_kodi("2026-01-15T19:10:00Z") == "2026-01-15 20:10:00"


def test_ambiguous_fall_back_hour_round_trips_the_sent_string() -> None:
    # 02:30 happens twice on 2026-10-25 in Amsterdam.
    local = "2026-10-25 02:30:00"
    assert utc_to_kodi(kodi_to_utc(local)) == local


def test_empty_and_bad_dates() -> None:
    assert kodi_to_utc("") == ""
    assert kodi_to_utc("0000-00-00 00:00:00") == ""
    assert kodi_to_utc("yesterday") == ""
    assert utc_to_kodi("") == ""
    assert utc_to_kodi("nonsense") == ""


def test_parse_utc_compares_as_time() -> None:
    a, b = parse_utc("2026-07-01T18:10:00Z"), parse_utc("2026-07-01T20:09:00+02:00")
    assert a is not None and b is not None and b < a
    assert parse_utc("2026-07-01T18:10:00") == a
    assert parse_utc("") is None
    assert parse_utc("x") is None
