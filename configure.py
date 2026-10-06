# SPDX-License-Identifier: GPL-2.0-only
"""Script entry point for the add-on's settings screens.

At the addon root, not under resources/, so that the directory CPythonInvoker puts on
sys.path is the package root and `from resources.lib...` resolves.

Kodi passes RunScript arguments in sys.argv after the script path. With none, the viewer
dialog opens, which is what the existing settings action relies on.
"""

import sys

REMEMBERED = "remembered"


def dispatch(argv: list[str]) -> None:
    if REMEMBERED in argv[1:]:
        from resources.lib.remembered import main as remembered_main

        remembered_main()
        return
    from resources.lib.viewer_config import main as viewers_main

    viewers_main()


if __name__ == "__main__":
    dispatch(sys.argv)
