# SPDX-License-Identifier: GPL-2.0-only
"""Viewer configuration entry point.

At the addon root, not under resources/, so that the directory CPythonInvoker puts on
sys.path is the package root and `from resources.lib...` resolves.
"""

if __name__ == "__main__":
    from resources.lib.viewer_config import main

    main()
