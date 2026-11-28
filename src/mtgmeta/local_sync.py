"""Refresh a laptop's silver and gold tables from R2 so the MCP server has current data.

The pipeline runs only in GitHub Actions; this module never writes to R2. It wraps
the two `rclone copy` commands Jordan used to type by hand, checks that rclone and
the remote are configured before starting, and reports what changed locally.

rclone finds the remote either in its config file (`rclone config`) or in
`RCLONE_CONFIG_<REMOTE>_*` environment variables, the way the workflows set it
up. Settings loads `.env`, so those variables can live there for local use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq

LAYERS = ("silver", "gold")
FORECAST_FILE = Path("gold/melee/forecast/field_forecast.parquet")
REPORT_FILE = Path("gold/melee/brief/report.html")     # the HTML brief, opened from disk


# -- preflight ---------------------------------------------------------------

def rclone_path() -> str | None:
    return shutil.which("rclone")


def remote_configured(remote: str, env: dict[str, str] | None = None,
                      rclone: str | None = None) -> bool:
    """True when rclone knows the remote, from the environment or its config file."""
    env = os.environ if env is None else env
    if env.get(f"RCLONE_CONFIG_{remote.upper()}_TYPE"):
        return True
    rclone = rclone or rclone_path()
    if not rclone:
        return False
    try:
        out = subprocess.run([rclone, "listremotes"], capture_output=True, text=True,
                             check=False, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return f"{remote}:" in {line.strip() for line in out.splitlines()}


def preflight(remote: str) -> list[str]:
    """Problems that stop a sync before it starts, each with how to fix it. Empty = go."""
    problems = []
    rclone = rclone_path()
    if not rclone:
        problems.append("rclone is not on PATH. Install it from https://rclone.org/install/ "
                        "(Ubuntu's packaged build is too old for R2).")
    if not remote_configured(remote, rclone=rclone):
        problems.append(
            f"rclone has no remote named '{remote}:'. Either run `rclone config` and create an "
            f"S3 remote for Cloudflare R2, or put RCLONE_CONFIG_{remote.upper()}_TYPE=s3, "
            f"..._PROVIDER=Cloudflare, ..._ACCESS_KEY_ID, ..._SECRET_ACCESS_KEY and "
            f"..._ENDPOINT in .env (see .env.example).")
    return problems


# -- what to run --------------------------------------------------------------

def commands(remote: str, bucket: str, data_root: Path,
             layers: tuple[str, ...] = LAYERS) -> list[list[str]]:
    """One `rclone copy` per layer. copy only adds and updates; it never deletes locally."""
    return [["rclone", "copy", f"{remote}:{bucket}/{layer}", str(data_root / layer),
             "--fast-list", "--stats-one-line", "--stats", "30s"] for layer in layers]


def run_commands(cmds: list[list[str]]) -> int:
    """Run each command, streaming its output; return the first non-zero exit code."""
    for cmd in cmds:
        print("$ " + " ".join(cmd), flush=True)
        rc = subprocess.run(cmd, check=False).returncode
        if rc != 0:
            return rc
    return 0


# -- what changed -------------------------------------------------------------

@dataclass
class Snapshot:
    files: dict[str, tuple[int, int]] = field(default_factory=dict)  # rel path -> (size, mtime_ns)

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(size for size, _ in self.files.values())


def snapshot(root: Path) -> Snapshot:
    """Every regular file under root (recursively), keyed by its path relative to root."""
    snap = Snapshot()
    if not root.is_dir():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap.files[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return snap


@dataclass
class Diff:
    added: list[str]
    updated: list[str]

    @property
    def changed(self) -> int:
        return len(self.added) + len(self.updated)


def diff(before: Snapshot, after: Snapshot) -> Diff:
    added = sorted(k for k in after.files if k not in before.files)
    updated = sorted(k for k in after.files if k in before.files
                     and after.files[k] != before.files[k])
    return Diff(added, updated)


def forecast_target_week(data_root: Path) -> str | None:
    """The newest `target_week` in field_forecast.parquet, or None when it isn't there."""
    path = data_root / FORECAST_FILE
    if not path.exists():
        return None
    try:
        col = pq.read_table(path, columns=["target_week"]).column("target_week").to_pylist()
    except Exception:  # noqa: BLE001 - a half-copied or foreign file is just "unknown"
        return None
    weeks = [str(w) for w in col if w is not None]
    return max(weeks) if weeks else None


def _mb(n: int) -> str:
    return f"{n / 1_000_000:,.1f} MB"


def report(before: dict[str, Snapshot], after: dict[str, Snapshot],
           week_before: str | None, week_after: str | None, show: int = 12,
           report_path: Path | None = None) -> str:
    """Per-layer counts, the files that changed (truncated), the forecast week and, when
    ``report_path`` is given, where the HTML brief landed (so Jordan knows what to open)."""
    lines = []
    for layer in before:
        d = diff(before[layer], after[layer])
        lines.append(f"{layer}: {after[layer].count} files, {_mb(after[layer].total_bytes)}; "
                     f"{len(d.added)} new, {len(d.updated)} updated")
        for name in (d.added + d.updated)[:show]:
            tag = "new" if name in d.added else "upd"
            lines.append(f"  {tag}  {layer}/{name}")
        if d.changed > show:
            lines.append(f"  ... and {d.changed - show} more")
    if week_after is None:
        lines.append("forecast: none locally (gold/melee/forecast/field_forecast.parquet missing)")
    elif week_before != week_after:
        lines.append(f"forecast: target week {week_after} (was {week_before or 'none'})")
    else:
        lines.append(f"forecast: target week {week_after} (unchanged)")
    if report_path is not None:
        if report_path.exists():
            lines.append(f"report: open {report_path.resolve()} in a browser")
        else:
            lines.append(f"report: none locally ({REPORT_FILE.as_posix()} missing)")
    return "\n".join(lines)
