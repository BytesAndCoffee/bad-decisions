from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pack_dir: str | None = None
    log_level: str = "INFO"
    root_path: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        level = os.getenv("CAH_LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("CAH_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return cls(os.getenv("CAH_PACK_DIR"), level, os.getenv("CAH_ROOT_PATH", ""))
