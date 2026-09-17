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
        level = os.getenv("BAD_DECISIONS_LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("BAD_DECISIONS_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return cls(
            os.getenv("BAD_DECISIONS_PACK_DIR"),
            level,
            os.getenv("BAD_DECISIONS_ROOT_PATH", ""),
        )
