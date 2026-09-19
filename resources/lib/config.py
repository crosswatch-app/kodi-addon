# SPDX-License-Identifier: GPL-2.0-only
"""Every setting, read in one place, as one immutable value.

The token is stored separately from the base URL and joined only here and in the reporter,
so the credential never exists inside a string that a logging path could reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from resources.lib.constants import DEFAULT_INDEX_TTL_SECONDS, DEFAULT_PROGRESS_STEP
from resources.lib.kodi import KodiApi

KEY_BASE_URL = "webhook_base_url"
KEY_TOKEN = "webhook_token"
KEY_DEVICE_ID = "device_id"
KEY_PROGRESS_STEP = "progress_step"
KEY_MOVIE_PROMPTS = "movie_prompts"
KEY_INDEX_TTL = "index_ttl_minutes"
KEY_DEBUG = "debug_logging"


@dataclass(frozen=True)
class Settings:
    webhook_base_url: str = ""
    webhook_token: str = ""
    device_id: str = ""
    device_name: str = ""
    progress_step: int = DEFAULT_PROGRESS_STEP
    movie_prompts: bool = True
    index_ttl_seconds: int = DEFAULT_INDEX_TTL_SECONDS
    debug_logging: bool = False

    def webhook_url(self) -> str | None:
        if not self.webhook_base_url or not self.webhook_token:
            return None
        # Encoded, not interpolated: a token containing #, & or a space would otherwise
        # truncate the credential or corrupt the request line.
        query = urlencode({"profile": self.webhook_token})
        return f"{self.webhook_base_url.rstrip('/')}?{query}"


def _int(kodi: KodiApi, key: str, fallback: int) -> int:
    try:
        return kodi.setting_int(key) or fallback
    except Exception:
        return fallback


def _bool(kodi: KodiApi, key: str, fallback: bool) -> bool:
    try:
        raw = kodi.setting(key)
        return fallback if raw == "" else kodi.setting_bool(key)
    except Exception:
        return fallback


def read_settings(kodi: KodiApi) -> Settings:
    ttl_minutes = _int(kodi, KEY_INDEX_TTL, DEFAULT_INDEX_TTL_SECONDS // 60)
    return Settings(
        webhook_base_url=kodi.setting(KEY_BASE_URL).strip(),
        webhook_token=kodi.setting(KEY_TOKEN).strip(),
        device_id=kodi.setting(KEY_DEVICE_ID).strip(),
        device_name=kodi.info_label("System.FriendlyName"),
        progress_step=_int(kodi, KEY_PROGRESS_STEP, DEFAULT_PROGRESS_STEP),
        movie_prompts=_bool(kodi, KEY_MOVIE_PROMPTS, True),
        index_ttl_seconds=ttl_minutes * 60,
        debug_logging=_bool(kodi, KEY_DEBUG, False),
    )
