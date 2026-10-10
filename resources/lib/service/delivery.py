# SPDX-License-Identifier: GPL-2.0-only
"""Where events go for the current connection settings, rebuilt when they change.

Pairing, a Link, a pasted URL and Unpair all end as a settings change, and this is what
turns one into a new delivery path without a Kodi restart.
"""

from __future__ import annotations

import threading

from resources.lib import status
from resources.lib.config import Settings
from resources.lib.kodi import KodiApi
from resources.lib.log import get_logger
from resources.lib.outbox import Outbox, config_fingerprint
from resources.lib.reporter import EventSink, HttpReporter, InvalidWebhookUrl, LogReporter, OutboxLane
from resources.lib.routes import RouteFacts
from resources.lib.storage import RouteStore

_log = get_logger("service")

# The URL the reporter posts to and the token in its header. None for the URL means
# unconfigured, whatever the token says.
Endpoint = tuple[str | None, str]


def _endpoint(settings: Settings) -> Endpoint:
    url = settings.webhook_url()
    return (url, settings.webhook_token if url else "")


class Delivery:
    """Builds the sink and outbox lane for an endpoint, and reports its instance name.

    One Outbox for the life of the service, rebound to each new token: two stores over one
    file would each save their own entries over the other's. An endpoint with no sink, after
    Unpair, leaves the store alone, so its watches are still there if the same token returns.

    The reporter worker notes the instance name from a ping reply; the service thread writes
    it into the status setting, because everything that talks to Kodi runs there.
    """

    def __init__(
        self, kodi: KodiApi, outbox_path: str, abort: threading.Event, routes: RouteStore | None = None
    ) -> None:
        self._kodi = kodi
        self._outbox_path = outbox_path
        self._abort = abort
        self._endpoint: Endpoint | None = None
        self._outbox: Outbox | None = None
        self._lock = threading.Lock()
        self._instance: tuple[Endpoint, str] | None = None
        self._routes = routes
        self._route_facts: tuple[Endpoint, RouteFacts] | None = None

    def changed(self, settings: Settings) -> bool:
        return _endpoint(settings) != self._endpoint

    def build(self, settings: Settings) -> tuple[EventSink, OutboxLane | None]:
        endpoint = _endpoint(settings)
        self._endpoint = endpoint
        url, token = endpoint
        if url is None:
            status.write(self._kodi, status.not_paired(self._kodi))
            return LogReporter(), None
        try:
            sink = HttpReporter(
                url,
                token=token,
                abort=self._abort,
                on_instance=lambda instance: self._note_instance(endpoint, instance),
                on_routes=lambda facts: self._note_routes(endpoint, facts),
            )
        except InvalidWebhookUrl as exc:
            # Fall back to logging rather than posting somewhere unexpected.
            _log.error("service.webhook_url_rejected", error=str(exc))
            return LogReporter(), None
        fingerprint = config_fingerprint(token)
        if self._outbox is None:
            self._outbox = Outbox(self._outbox_path, fingerprint)
            self._outbox.load()
        else:
            self._outbox.rebind(fingerprint)
        return sink, OutboxLane(self._outbox, sink.send_once)

    def _note_instance(self, endpoint: Endpoint, instance: str) -> None:
        with self._lock:
            self._instance = (endpoint, instance)

    def _note_routes(self, endpoint: Endpoint, facts: RouteFacts) -> None:
        with self._lock:
            self._route_facts = (endpoint, facts)

    def publish_status(self) -> None:
        """Write the instance name a ping reply carried, if it is for the current endpoint.

        A reply that arrives after the connection changed again belongs to the old server.
        """
        with self._lock:
            noted, self._instance = self._instance, None
            routed, self._route_facts = self._route_facts, None
        if routed is not None and self._routes is not None:
            endpoint, facts = routed
            if endpoint == self._endpoint and endpoint[0] is not None:
                self._routes.save(config_fingerprint(endpoint[1]), facts)
                _log.info("service.routes_noted", routes=facts.count, accepted=len(facts.accepted))
        if noted is None:
            return
        endpoint, instance = noted
        url = endpoint[0]
        if endpoint != self._endpoint or url is None:
            return
        status.write(self._kodi, status.paired(self._kodi, instance, url))
