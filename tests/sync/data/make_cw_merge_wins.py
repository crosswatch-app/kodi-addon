"""Regenerate cw_merge_wins.json from a CrossWatch checkout.

Run with CrossWatch's root as the first argument, so its own _merge_wins answers every case:
    python3 -I tests/sync/data/make_cw_merge_wins.py /path/to/CrossWatch
"""

import itertools
import json
import sys
from pathlib import Path


def main(root: str) -> None:
    sys.path.insert(0, root)
    from providers.sync.kodi import _addon_sync as cw  # noqa: PLC0415 - from the given checkout

    cases = []
    product = itertools.product(
        [True, False], [True, False], [0, 3, 5], [0, 3, 5], [True, False], [True, False], [False, True]
    )
    for tw, mw, te, me, tx, mx, src_only in product:
        stamp = "src" if src_only else "event"
        theirs, mine = {"watched": tw, "explicit": tx, stamp: te}, {"watched": mw, "explicit": mx, stamp: me}
        cases.append(["history", theirs, mine, cw._merge_wins("history", theirs, mine)])
    for ts, ms in itertools.product([0, 2, 4], [0, 2, 4]):
        for group in ("ratings", "progress"):
            cases.append([group, {"src": ts}, {"src": ms}, cw._merge_wins(group, {"src": ts}, {"src": ms})])
    (Path(__file__).parent / "cw_merge_wins.json").write_text(json.dumps(cases))


if __name__ == "__main__":
    main(sys.argv[1])
