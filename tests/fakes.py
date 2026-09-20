"""Test doubles. Each is bound to its protocol by an annotated assignment in the tests, so
a protocol that grows a method fails type checking until the double catches up."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from resources.lib.kodi import WINDOW_INVALID


class FakeKodi:
    def __init__(
        self,
        rpc_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
        settings: dict[str, str] | None = None,
        files: dict[str, str] | None = None,
        multiselect_answer: list[int] | None = None,
        select_answer: int = -1,
        input_answer: str = "",
        confirm_answer: bool = False,
        dialog_id: int = WINDOW_INVALID,
        playing: bool = True,
        position_ms: int | None = None,
        duration_ms: int | None = None,
        position_raises: bool = False,
        duration_raises: bool = False,
        info_labels: dict[str, str] | None = None,
        root: str = "/kodi",
    ) -> None:
        self.rpc_handlers = dict(rpc_handlers or {})
        self.settings = dict(settings or {})
        self.files = dict(files or {})
        self.multiselect_answer = multiselect_answer
        self.select_answer = select_answer
        self.input_answer = input_answer
        self.confirm_answer = confirm_answer
        self.dialog_id = dialog_id
        self.playing = playing
        self.position_ms = position_ms
        self.duration_ms = duration_ms
        self.position_raises = position_raises
        self.duration_raises = duration_raises
        self.info_labels = dict(info_labels or {})
        self.root = root
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.multiselect_calls: list[tuple[str, list[str], list[int] | None, int]] = []
        self.notifications: list[tuple[str, str]] = []
        self.logged: list[tuple[str, int]] = []

    def jsonrpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, dict(params or {})))
        handler = self.rpc_handlers.get(method)
        assert handler is not None, f"unstubbed JSON-RPC method: {method}"
        return handler(dict(params or {}))

    def setting(self, key: str) -> str:
        return self.settings.get(key, "")

    def setting_bool(self, key: str) -> bool:
        return self.settings.get(key, "").lower() == "true"

    def setting_int(self, key: str) -> int:
        raw = self.settings.get(key, "")
        return int(raw) if raw else 0

    def addon_version(self) -> str:
        return "0.1.0"

    def info_label(self, key: str) -> str:
        return self.info_labels.get(key, "")

    def translate(self, path: str) -> str:
        return path.replace("special://", f"{self.root}/")

    def read_text(self, path: str, max_bytes: int = 256 * 1024) -> str | None:
        text = self.files.get(path)
        return text[:max_bytes] if text is not None else None

    def is_playing(self) -> bool:
        return self.playing

    def player_times(self) -> tuple[int | None, int | None]:
        position = None if self.position_raises else self.position_ms
        duration = None if self.duration_raises else self.duration_ms
        # Same rule as KodiRuntime: Kodi reports 0.0 for an unknown duration, and the whole
        # optional-percent design rests on that becoming None rather than zero.
        return position, (duration if duration else None)

    def topmost_dialog_id(self) -> int:
        return self.dialog_id

    def multiselect(
        self, heading: str, options: list[str], preselect: list[int] | None = None, autoclose: int = 0
    ) -> list[int] | None:
        self.multiselect_calls.append((heading, list(options), preselect, autoclose))
        return self.multiselect_answer

    def select(self, heading: str, options: list[str]) -> int:
        return self.select_answer

    def text_input(self, heading: str, default: str = "") -> str:
        return self.input_answer

    def confirm(self, heading: str, message: str) -> bool:
        return self.confirm_answer

    def notify(self, heading: str, message: str) -> None:
        self.notifications.append((heading, message))

    def localised(self, string_id: int) -> str:
        return f"#{string_id}"

    def log(self, message: str, level: int) -> None:
        self.logged.append((message, level))
