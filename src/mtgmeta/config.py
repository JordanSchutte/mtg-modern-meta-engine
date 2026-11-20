"""Runtime settings, read from the environment (and .env when present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MIN_MELEE_INTERVAL = 2.0


@dataclass(frozen=True)
class Settings:
    melee_contact_email: str
    melee_request_interval: float
    data_root: Path
    # Where the published tables live: rclone remote name and R2 bucket, the same
    # values the workflows use (remote "r2", bucket "mtgmeta-bronze").
    rclone_remote: str = "r2"
    r2_bucket: str = "mtgmeta-bronze"

    @classmethod
    def from_env(cls) -> Settings:
        interval = float(os.getenv("MELEE_REQUEST_INTERVAL", "2.5"))
        return cls(
            melee_contact_email=os.getenv("MELEE_CONTACT_EMAIL", "").strip(),
            # Never go faster than the floor, whatever the environment says.
            melee_request_interval=max(interval, MIN_MELEE_INTERVAL),
            data_root=Path(os.getenv("DATA_ROOT", "data")),
            rclone_remote=os.getenv("RCLONE_REMOTE", "r2").strip() or "r2",
            r2_bucket=os.getenv("R2_BUCKET", "mtgmeta-bronze").strip() or "mtgmeta-bronze",
        )
