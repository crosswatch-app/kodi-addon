# SPDX-License-Identifier: GPL-2.0-only
"""Addon-wide constants. No behaviour lives here."""

ADDON_ID = "service.crosswatch"
LOG_NAME = "crosswatch"

DEFAULT_PROGRESS_STEP = 25
DEFAULT_INDEX_TTL_SECONDS = 3600
DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0

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
