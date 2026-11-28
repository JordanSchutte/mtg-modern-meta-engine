#!/usr/bin/env python3
"""Write the weekly meta brief from the gold tables.

    python scripts/build_brief.py              # writes gold/melee/brief/{brief-<week>.md,brief.md}
                                               #   and {report-<week>.html,report.html} beside them
    python scripts/build_brief.py --no-write   # print only
    python scripts/build_brief.py --today 2026-11-16

Run after build_forecast.py. The brief is dated by the forecast's target week
and overwritten within that week; movers compare against the previous week's
brief, so keep gold/melee/brief/ between runs (the watcher pulls it from R2).
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from mtgmeta.config import Settings
from mtgmeta.gold import brief, report_html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="pretend it is this date (default: today)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = Settings.from_env().data_root
    text, inputs = brief.run(root, today=args.today)
    print(text)
    if not args.no_write:
        html = report_html.render(inputs, inputs.position, inputs.field)
        written = brief.write(text, inputs.target_week, root, html=html)
        print(f"wrote {', '.join(str(p) for p in written.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
