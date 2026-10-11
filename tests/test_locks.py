"""Every exact pin in a requirements file is the version its hash lock installs.

CI installs from the locks, not from the requirements files. Dependabot bumps a requirements
file but cannot regenerate a hash lock, so a bump without a fresh lock would change nothing
CI runs; this fails it until the lock is regenerated with the command in the file's header.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#\\]+)", re.MULTILINE)


def _pins(path: Path) -> dict[str, str]:
    return {name.lower().replace("_", "-"): version for name, version in PIN.findall(path.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("name", ["dev", "docs"])
def test_every_direct_pin_is_what_the_lock_installs(name):
    wanted = _pins(ROOT / f"requirements-{name}.txt")
    locked = _pins(ROOT / f"requirements-{name}.lock")
    assert wanted
    for package, version in wanted.items():
        assert locked.get(package) == version, f"{package}=={version}: regenerate requirements-{name}.lock"
