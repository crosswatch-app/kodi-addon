import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.stubs import install_kodi_stubs  # noqa: E402

install_kodi_stubs()


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset log.py's module-global state around every test.

    Resolved through sys.modules rather than imported: the logging module does not exist
    until the task that creates it, and a test that never touches it needs no reset.

    A reset written as a statement in a test body is skipped when an earlier assertion in
    that test fails, leaking the debug flag, an open file handle and a stale log directory
    into every test that runs afterwards.
    """
    logmod: Any = sys.modules.get("resources.lib.log")
    if logmod is not None:
        logmod.reset()
    yield
    logmod = sys.modules.get("resources.lib.log")
    if logmod is not None:
        logmod.reset()
