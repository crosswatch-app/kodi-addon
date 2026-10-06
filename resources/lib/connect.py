# SPDX-License-Identifier: GPL-2.0-only
"""The settings scripts that connect this Kodi to CrossWatch: Pair, Link and Unpair.

Each ends by writing settings. The running service picks the change up through
onSettingsChanged and switches over without a restart; with the add-on's settings screen
open, Kodi holds the writes on that screen and saves them, and tells the service, when it
closes.
"""

from __future__ import annotations

from collections.abc import Callable

from resources.lib import log as logmod
from resources.lib import paths, status
from resources.lib.config import KEY_BASE_URL, KEY_TOKEN, read_settings, split_token
from resources.lib.constants import (
    LINK_AUTOCLOSE_SECONDS,
    LINK_CONFIRM,
    NOTIFY_HEADING,
    PAIR_ADDRESS,
    PAIR_BAD_ADDRESS,
    PAIR_CODE,
    PAIR_DONE,
    PAIR_FAILED,
    PAIR_INVALID_CODE,
    PAIR_RATE_LIMITED,
    PAIR_UNREACHABLE,
    UNPAIR_CONFIRM,
)
from resources.lib.kodi import KodiApi, KodiRuntime
from resources.lib.pairing import (
    WEBHOOK_PATH,
    PairResult,
    address_hint,
    exchange_code,
    normalise_address,
    normalise_code,
)
from resources.lib.reporter import InvalidWebhookUrl, validate_webhook_url

_log = logmod.get_logger("config")

Exchange = Callable[[str, str], PairResult]


def _store(kodi: KodiApi, url: str, token: str, line: str) -> None:
    # Token first, so the service never reads a new URL paired with the old token.
    kodi.set_setting(KEY_TOKEN, token)
    kodi.set_setting(KEY_BASE_URL, url)
    status.write(kodi, line)


def _failure(kodi: KodiApi, result: PairResult, address: str) -> str:
    if result.outcome == "invalid_code":
        return kodi.localised(PAIR_INVALID_CODE)
    if result.outcome == "rate_limited":
        return kodi.localised(PAIR_RATE_LIMITED)
    if result.outcome == "unreachable":
        return status.fill(kodi.localised(PAIR_UNREACHABLE), address)
    return status.fill(kodi.localised(PAIR_FAILED), str(result.status))


def pair(kodi: KodiApi, exchange: Exchange = exchange_code) -> bool:
    """Ask for the address, then the code. A cancel or a failure changes no setting."""
    heading = kodi.localised(NOTIFY_HEADING)
    typed = kodi.text_input(kodi.localised(PAIR_ADDRESS), address_hint(read_settings(kodi).webhook_base_url))
    if not typed.strip():
        return False
    address = normalise_address(typed)
    if address is None:
        kodi.notify(heading, status.fill(kodi.localised(PAIR_BAD_ADDRESS), typed.strip()))
        return False
    code = normalise_code(kodi.text_input(kodi.localised(PAIR_CODE)))
    if not code:
        return False
    result = exchange(address, code)
    if result.outcome != "paired":
        kodi.notify(heading, _failure(kodi, result, address))
        return False
    logmod.set_secret(result.token)
    # The typed address, not the url in the reply: it is the one proven to reach CrossWatch
    # from this Kodi, where CrossWatch's own idea of its address may sit behind Docker or a
    # proxy.
    url = f"{address}{WEBHOOK_PATH}"
    _store(kodi, url, result.token, status.paired(kodi, result.instance, url))
    kodi.notify(heading, status.fill(kodi.localised(PAIR_DONE), result.instance or status.address_of(url)))
    return True


def parse_arguments(argv: list[str]) -> dict[str, str]:
    """key=value arguments, as Addons.ExecuteAddon passes an array of them."""
    return dict(arg.split("=", 1) for arg in argv if "=" in arg)


def link(kodi: KodiApi, arguments: dict[str, str]) -> bool:
    """CrossWatch's shortcut: it hands over URL and token, the household says yes on the TV.

    Malformed arguments never reach a dialog: they did not come from a person.
    """
    url, pasted = split_token(arguments.get("url", "").strip())
    token = arguments.get("token", "").strip() or pasted
    if not url or not token:
        _log.warning("config.link_rejected", reason="missing")
        return False
    try:
        validate_webhook_url(url)
    except InvalidWebhookUrl:
        _log.warning("config.link_rejected", reason="url")
        return False
    logmod.set_secret(token)
    question = status.fill(kodi.localised(LINK_CONFIRM), status.address_of(url))
    if not kodi.confirm(kodi.localised(NOTIFY_HEADING), question, autoclose=LINK_AUTOCLOSE_SECONDS):
        _log.info("config.link_declined")
        return False
    _store(kodi, url.rstrip("/"), token, status.linked(kodi, url))
    _log.info("config.linked")
    return True


def unpair(kodi: KodiApi) -> bool:
    """Stored watches stay on disk: pairing again with the same token still delivers them."""
    if not kodi.confirm(kodi.localised(NOTIFY_HEADING), kodi.localised(UNPAIR_CONFIRM)):
        return False
    _store(kodi, "", "", status.not_paired(kodi))
    _log.info("config.unpaired")
    return True


def _runtime() -> KodiApi:
    kodi = KodiRuntime()
    settings = read_settings(kodi)
    # A separate interpreter with its own module state, as for the other settings screens.
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    logmod.set_secret(settings.webhook_token)
    return kodi


def pair_main() -> None:
    pair(_runtime())


def link_main(arguments: dict[str, str]) -> None:
    link(_runtime(), arguments)


def unpair_main() -> None:
    unpair(_runtime())
