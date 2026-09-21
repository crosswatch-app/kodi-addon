#!/usr/bin/env python3
"""Print one piece of addon metadata. Paths arrive as argv, never spliced into source."""

import re
import sys
from pathlib import Path
from xml.etree import ElementTree

if __name__ == "__main__":
    what, root = sys.argv[1], Path(sys.argv[2])
    if what == "id":
        value = ElementTree.parse(root / "addon.xml").getroot().get("id") or ""
    elif what == "log_name":
        text = (root / "resources" / "lib" / "constants.py").read_text(encoding="utf-8")
        match = re.search(r'LOG_NAME\s*=\s*"([^"]+)"', text)
        value = match.group(1) if match else ""
    else:
        raise SystemExit(f"unknown field: {what}")
    if not value:
        raise SystemExit(f"could not determine {what}")
    print(value)
