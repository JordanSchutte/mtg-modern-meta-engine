#!/usr/bin/env python3
"""Pull the current silver and gold tables from R2 into DATA_ROOT for the MCP server.

    python scripts/sync_local.py              # rclone copy r2:<bucket>/{silver,gold} -> data/
    python scripts/sync_local.py --dry-run    # print the commands and local state, run nothing
    python scripts/sync_local.py --only gold  # one layer

Read-only against R2 (copy, never sync/delete). Remote name and bucket come from
Settings (RCLONE_REMOTE, R2_BUCKET; defaults r2 / mtgmeta-bronze). Exits 2 when
rclone or the remote's config is missing, 1 when rclone itself fails.
"""

from __future__ import annotations

import argparse
import sys

from mtgmeta import local_sync
from mtgmeta.config import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would run and the current local state; do not call rclone")
    ap.add_argument("--only", choices=local_sync.LAYERS, action="append",
                    help="limit to one layer (repeatable)")
    args = ap.parse_args()

    settings = Settings.from_env()
    root, remote, bucket = settings.data_root, settings.rclone_remote, settings.r2_bucket
    layers = tuple(args.only) if args.only else local_sync.LAYERS
    cmds = local_sync.commands(remote, bucket, root, layers)

    before = {layer: local_sync.snapshot(root / layer) for layer in layers}
    week_before = local_sync.forecast_target_week(root)

    if args.dry_run:
        print(f"dry run: would refresh {', '.join(layers)} under {root} from {remote}:{bucket}")
        for cmd in cmds:
            print("$ " + " ".join(cmd))
        problems = local_sync.preflight(remote)
        for p in problems:
            print(f"warning: {p}")
        print()
        print(local_sync.report(before, before, week_before, week_before,
                                report_path=root / local_sync.REPORT_FILE))
        return 0

    problems = local_sync.preflight(remote)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        print("nothing was copied.", file=sys.stderr)
        return 2

    rc = local_sync.run_commands(cmds)
    after = {layer: local_sync.snapshot(root / layer) for layer in layers}
    print()
    print(local_sync.report(before, after, week_before, local_sync.forecast_target_week(root),
                            report_path=root / local_sync.REPORT_FILE))
    if rc != 0:
        print(f"\nerror: rclone exited {rc}; the counts above are what landed before it stopped.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
