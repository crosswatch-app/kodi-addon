# SPDX-License-Identifier: GPL-2.0-only
"""Addon-wide constants. No behaviour lives here."""

ADDON_ID = "service.crosswatch"
LOG_NAME = "crosswatch"

# Progress cadence is wall clock, not percentage: the contract asks for an event every
# 60 seconds and after a seek, and CrossWatch does its own throttling on top.
DEFAULT_PROGRESS_INTERVAL_SECONDS = 60

# The heartbeat that tells CrossWatch to stop polling this Kodi. It falls back to polling
# after 15 minutes of silence, so 5 minutes leaves room for two lost pings.
PING_INTERVAL_SECONDS = 300

# Older servers coerce a missing percent to zero, which destroys the viewer's resume point
# in the downstream sink. Warn rather than refuse: the user may not control the server.
MIN_CROSSWATCH_VERSION = "0.13.0"

DEFAULT_INDEX_TTL_SECONDS = 3600
# The contract's request timeout, and the budget a failed delivery may consume. The budget
# is far larger than Kodi's 5000ms shutdown window, which is why the backoff waits on an
# abort event rather than sleeping blind.
HTTP_TIMEOUT_SECONDS = 10.0
RETRY_BUDGET_SECONDS = 120.0
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0)

# Kodi allows a service script 5000ms after abort before injecting SystemExit
# (xbmc/interfaces/python/PythonInvoker.cpp:62), so the shutdown path must finish well inside it.
SHUTDOWN_HTTP_TIMEOUT_SECONDS = 1.0
SHUTDOWN_DRAIN_SECONDS = 2.5

DEFAULT_QUEUE_SIZE = 100
PROMPT_AUTOCLOSE_SECONDS = 120

# A permanently unexpandable playlist must not pin the addon into a continuous rebuild loop.
FAILURE_BACKOFF_SECONDS = 60
MAX_FAILURE_BACKOFF_SECONDS = 1800

LOG_MAX_BYTES = 500 * 1024
LOG_BACKUP_COUNT = 3
