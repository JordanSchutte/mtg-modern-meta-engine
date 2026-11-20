"""Local data refresh: the pure pieces and the script's --dry-run and preflight paths."""

import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta import local_sync as ls
from mtgmeta.config import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_local.py"


def _write_forecast(root: Path, weeks: list[str]) -> None:
    path = root / ls.FORECAST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(
        [{"archetype": f"A{i}", "share": 0.1, "target_week": w} for i, w in enumerate(weeks)]),
        path)


def test_settings_default_and_env_remote(monkeypatch):
    monkeypatch.delenv("RCLONE_REMOTE", raising=False)
    monkeypatch.delenv("R2_BUCKET", raising=False)
    s = Settings.from_env()
    assert (s.rclone_remote, s.r2_bucket) == ("r2", "mtgmeta-bronze")
    monkeypatch.setenv("RCLONE_REMOTE", "cf")
    monkeypatch.setenv("R2_BUCKET", "other")
    s = Settings.from_env()
    assert (s.rclone_remote, s.r2_bucket) == ("cf", "other")


def test_commands_are_copy_not_sync():
    cmds = ls.commands("r2", "mtgmeta-bronze", Path("data"))
    assert [c[:4] for c in cmds] == [
        ["rclone", "copy", "r2:mtgmeta-bronze/silver", os.path.join("data", "silver")],
        ["rclone", "copy", "r2:mtgmeta-bronze/gold", os.path.join("data", "gold")],
    ]
    assert all("sync" not in c and "--delete" not in " ".join(c) for c in cmds)
    assert [c[2] for c in ls.commands("r2", "b", Path("d"), ("gold",))] == ["r2:b/gold"]


def test_snapshot_diff_and_report(tmp_path):
    (tmp_path / "silver" / "melee").mkdir(parents=True)
    (tmp_path / "silver" / "melee" / "events.parquet").write_bytes(b"a" * 10)
    (tmp_path / "silver" / "cards.parquet").write_bytes(b"b" * 5)
    before = ls.snapshot(tmp_path / "silver")
    assert before.count == 2 and before.total_bytes == 15
    assert ls.snapshot(tmp_path / "gold").count == 0   # missing dir is just empty

    (tmp_path / "silver" / "melee" / "events.parquet").write_bytes(b"a" * 12)   # updated
    (tmp_path / "silver" / "melee" / "matches.parquet").write_bytes(b"c")       # new
    after = ls.snapshot(tmp_path / "silver")
    d = ls.diff(before, after)
    assert d.added == ["melee/matches.parquet"]
    assert d.updated == ["melee/events.parquet"]
    assert d.changed == 2

    text = ls.report({"silver": before}, {"silver": after}, None, "2026-11-23")
    assert "silver: 3 files" in text and "1 new, 1 updated" in text
    assert "new  silver/melee/matches.parquet" in text
    assert "upd  silver/melee/events.parquet" in text
    assert "target week 2026-11-23 (was none)" in text
    assert "(unchanged)" in ls.report({"silver": after}, {"silver": after}, "x", "x")
    assert "none locally" in ls.report({"silver": after}, {"silver": after}, None, None)


def test_forecast_target_week(tmp_path):
    assert ls.forecast_target_week(tmp_path) is None
    _write_forecast(tmp_path, ["2026-11-16", "2026-11-23", "2026-11-16"])
    assert ls.forecast_target_week(tmp_path) == "2026-11-23"
    (tmp_path / ls.FORECAST_FILE).write_bytes(b"not parquet")
    assert ls.forecast_target_week(tmp_path) is None


def test_remote_configured_from_env_without_rclone():
    assert ls.remote_configured("r2", env={"RCLONE_CONFIG_R2_TYPE": "s3"}, rclone=None)
    assert not ls.remote_configured("r2", env={}, rclone=None)


def _run(args: list[str], root: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RCLONE_CONFIG_")}
    env.update({"DATA_ROOT": str(root), "PATH": str(root / "nobin"), **env_extra})
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, check=False, timeout=120)


def test_dry_run_prints_commands_and_state_without_rclone(tmp_path):
    _write_forecast(tmp_path, ["2026-11-23"])
    (tmp_path / "silver").mkdir()
    (tmp_path / "silver" / "cards.parquet").write_bytes(b"x")
    r = _run(["--dry-run"], tmp_path, {})
    assert r.returncode == 0, r.stderr
    assert "$ rclone copy r2:mtgmeta-bronze/silver" in r.stdout
    assert "$ rclone copy r2:mtgmeta-bronze/gold" in r.stdout
    assert "silver: 1 files" in r.stdout and "gold: 1 files" in r.stdout
    assert "target week 2026-11-23 (unchanged)" in r.stdout
    assert "warning: rclone is not on PATH" in r.stdout


FAKE_RCLONE = f"""#!{sys.executable}
# Stand-in for rclone: knows one remote and "copies" by writing fixed files locally.
import os, shutil, sys
from pathlib import Path
cmd = sys.argv[1]
if cmd == "listremotes":
    print("r2:")
elif cmd == "copy":
    src, dest = sys.argv[2], Path(sys.argv[3])
    if src.endswith("silver"):
        (dest / "melee").mkdir(parents=True, exist_ok=True)
        (dest / "melee" / "events.parquet").write_bytes(b"events")
    else:
        (dest / "melee" / "forecast").mkdir(parents=True, exist_ok=True)
        shutil.copy(os.environ["FAKE_FORECAST"], dest / "melee" / "forecast")
    print("Transferred: 1 / 1")
else:
    sys.exit(9)
"""


def test_run_path_reports_what_landed(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "rclone").write_text(FAKE_RCLONE)
    (bindir / "rclone").chmod(0o755)
    src = tmp_path / "src"
    _write_forecast(src, ["2026-11-30"])
    root = tmp_path / "data"
    (root / "silver" / "melee").mkdir(parents=True)
    (root / "silver" / "melee" / "events.parquet").write_bytes(b"old")   # will be updated
    r = _run([], root, {"PATH": str(bindir),
                        "FAKE_FORECAST": str(src / ls.FORECAST_FILE)})
    assert r.returncode == 0, r.stderr
    assert "$ rclone copy r2:mtgmeta-bronze/silver" in r.stdout
    assert "silver: 1 files" in r.stdout and "0 new, 1 updated" in r.stdout
    assert "gold: 1 files" in r.stdout and "1 new, 0 updated" in r.stdout
    assert "target week 2026-11-30 (was none)" in r.stdout
    assert ls.forecast_target_week(root) == "2026-11-30"


def test_missing_rclone_exits_2_with_hint(tmp_path):
    r = _run([], tmp_path, {"RCLONE_REMOTE": "cf"})
    assert r.returncode == 2
    assert "rclone is not on PATH" in r.stderr
    assert "no remote named 'cf:'" in r.stderr and "RCLONE_CONFIG_CF_TYPE" in r.stderr
    assert "nothing was copied" in r.stderr
    assert not (tmp_path / "silver").exists()
