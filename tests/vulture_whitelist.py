# SPDX-License-Identifier: GPL-2.0-only
"""Names that are used, but not by this codebase.

Kodi calls player callbacks by position into signatures it defines, so a parameter the addon
has no use for still cannot be renamed or dropped. Renaming one to `_seekOffset` would not
change what Kodi passes, but it would make the signature stop matching the documented API
that a maintainer checks it against.

This file exists so those names can be declared dead-by-design in one reviewable place rather
than by loosening `min_confidence` or globally ignoring a name that might be genuinely unused
somewhere else. It is listed in `[tool.vulture].paths` and is never packaged: the addon zip
excludes `tests/` wholesale.

Add a name here only when an external caller owns the signature. Anything else is dead code
and should be deleted instead.
"""


class _PlayerCallbacks:
    """Mirrors xbmc.Player's seek callbacks; the arguments describe the requested seek."""

    def onPlayBackSeek(self, time: int, seekOffset: int) -> int:
        return seekOffset

    def onPlayBackSeekChapter(self, chapter: int) -> int:
        return chapter
