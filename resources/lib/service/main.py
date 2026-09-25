# SPDX-License-Identifier: GPL-2.0-only
"""Composition root and tick loop.

The loop is the only pump for Kodi's Python callback queue, so it must survive anything the
tick throws: a returned main() means no further callback is ever delivered.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace

from resources.lib import log as logmod
from resources.lib import paths
from resources.lib.advanced_settings import read_thresholds
from resources.lib.config import read_settings
from resources.lib.constants import ADDON_ID, SHUTDOWN_DRAIN_SECONDS
from resources.lib.device import device_identity
from resources.lib.kodi import KodiRuntime
from resources.lib.media import MediaResolver
from resources.lib.outbox import Outbox, config_fingerprint
from resources.lib.reporter import (
    EventSink,
    HttpReporter,
    InvalidWebhookUrl,
    LogReporter,
    OutboxLane,
    ReporterQueue,
)
from resources.lib.service.controller import Controller
from resources.lib.service.playback_monitor import PlaybackMonitor
from resources.lib.service.service_monitor import ServiceMonitor
from resources.lib.storage import JsonViewerStore, PromptMemory

TICK_SECONDS = 1.0


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _build_sink(url: str | None, log, *, token: str, abort: threading.Event):
    """Fall back to logging rather than posting somewhere unexpected."""
    if not url:
        return LogReporter()
    try:
        return HttpReporter(url, token=token, abort=abort)
    except InvalidWebhookUrl as exc:
        log.error("service.webhook_url_rejected", error=str(exc))
        return LogReporter()


def _outbox_lane(sink: EventSink, path: str, url: str | None, *, token: str) -> OutboxLane | None:
    """Only a real webhook gets one: with nowhere to deliver, the file would only grow."""
    if not isinstance(sink, HttpReporter) or not url:
        return None
    store = Outbox(path, config_fingerprint(url, token))
    store.load()
    return OutboxLane(store, sink.send_once)


def main() -> None:
    player = PlaybackMonitor(None)  # controller attached below; construction registers the callback target
    kodi = KodiRuntime(player=player)
    settings = read_settings(kodi)
    logmod.configure(log_dir=paths.log_dir(kodi), debug=settings.debug_logging, sink=kodi.log)
    # Registered before anything can log: an illegal token makes http.client raise with the
    # value in the message, and that message is logged as a field on a retryable path.
    logmod.set_secret(settings.webhook_token)
    log = logmod.get_logger("service")
    log.info("service.starting", addon=ADDON_ID, version=kodi.addon_version())

    # Created here because both sides need it and neither may default it: the queue sets it
    # on stop, the reporter waits on it during a retry backoff.
    abort = threading.Event()
    sink = _build_sink(settings.webhook_url(), log, token=settings.webhook_token, abort=abort)
    # device_identity owns identity, not versioning, and is used by tests that should not
    # need a Kodi handle just to produce a version string.
    device = replace(device_identity(kodi, settings, paths.device_path(kodi)), addon_version=kodi.addon_version())
    lane = _outbox_lane(sink, paths.outbox_path(kodi), settings.webhook_url(), token=settings.webhook_token)
    queue = ReporterQueue(sink, device, abort, outbox=lane)
    controller = Controller(
        kodi=kodi,
        viewer_store=JsonViewerStore(paths.viewers_path(kodi)),
        memory=PromptMemory(paths.prompts_path(kodi)),
        media_resolver=MediaResolver(kodi),
        queue=queue,
        settings=settings,
        thresholds=read_thresholds(kodi),
        clock=_timestamp,
        monotonic=time.monotonic,
        ids=lambda: str(uuid.uuid4()),
    )
    player._controller = controller

    def reload_settings() -> None:
        fresh = read_settings(kodi)
        logmod.set_debug(fresh.debug_logging)
        controller.on_settings_changed(fresh, read_thresholds(kodi))
        log.info("service.settings_reloaded", debug=fresh.debug_logging)

    monitor = ServiceMonitor(controller, on_settings_changed=reload_settings)
    reason = "abort"
    try:
        while not monitor.abortRequested():
            try:
                controller.on_tick(shutting_down=monitor.abortRequested())
            except Exception as exc:
                # The loop is the only callback pump. Losing it silently disables the addon.
                log.error("service.tick_failed", error=str(exc))
            if monitor.waitForAbort(TICK_SECONDS):
                break
    except Exception as exc:
        reason = "error"
        log.error("service.loop_failed", error=str(exc))
    finally:
        controller.on_abort()
        undelivered = queue.stop(deadline=SHUTDOWN_DRAIN_SECONDS)
        log.info("service.stopping", reason=reason, undelivered=undelivered)
