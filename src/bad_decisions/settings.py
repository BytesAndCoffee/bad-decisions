from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    pack_dir: str | None = None
    log_level: str = "INFO"
    root_path: str = ""
    consequences_db: str | None = None
    consequences_dynamodb_table: str | None = None
    consequences_recording: bool = True
    consequences_feedback: bool = True
    consequences_public_stats: bool = False
    consequences_busy_timeout_ms: int = 250
    consequences_feedback_ttl_seconds: int = 7 * 24 * 60 * 60
    consequences_retention_days: int = 90
    management_token: str | None = None
    peer_pressure_dir: str | None = None
    peer_pressure_room_ttl_seconds: int = 6 * 60 * 60
    peer_pressure_hand_size: int = 10
    peer_pressure_minimum_players: int = 3
    peer_pressure_disconnect_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        level = os.getenv("BAD_DECISIONS_LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("BAD_DECISIONS_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        consequences_db = os.getenv("BAD_DECISIONS_CONSEQUENCES_DB")
        if consequences_db and not Path(consequences_db).is_absolute():
            raise ValueError("BAD_DECISIONS_CONSEQUENCES_DB must be an absolute path")
        peer_pressure_dir = os.getenv(
            "BAD_DECISIONS_PEER_PRESSURE_DIR",
            str(Path(tempfile.gettempdir()) / "bad-decisions-peer-pressure"),
        )
        if not Path(peer_pressure_dir).is_absolute():
            raise ValueError("BAD_DECISIONS_PEER_PRESSURE_DIR must be an absolute path")

        def boolean(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            if value.lower() in {"1", "true", "yes", "on"}:
                return True
            if value.lower() in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be a boolean")

        def positive(name: str, default: int) -> int:
            value = int(os.getenv(name, str(default)))
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
            return value

        retention_days = positive("BAD_DECISIONS_CONSEQUENCES_RETENTION_DAYS", 90)
        feedback_ttl = positive("BAD_DECISIONS_CONSEQUENCES_FEEDBACK_TTL_SECONDS", 7 * 24 * 60 * 60)
        if feedback_ttl > retention_days * 24 * 60 * 60:
            raise ValueError("BAD_DECISIONS_CONSEQUENCES_FEEDBACK_TTL_SECONDS may not exceed retention")
        return cls(
            pack_dir=os.getenv("BAD_DECISIONS_PACK_DIR"),
            log_level=level,
            root_path=os.getenv("BAD_DECISIONS_ROOT_PATH", ""),
            consequences_db=consequences_db,
            consequences_dynamodb_table=os.getenv("BAD_DECISIONS_CONSEQUENCES_DYNAMODB_TABLE"),
            consequences_recording=boolean("BAD_DECISIONS_CONSEQUENCES_RECORDING", True),
            consequences_feedback=boolean("BAD_DECISIONS_CONSEQUENCES_FEEDBACK", True),
            consequences_public_stats=boolean("BAD_DECISIONS_CONSEQUENCES_PUBLIC_STATS", False),
            consequences_busy_timeout_ms=positive("BAD_DECISIONS_CONSEQUENCES_BUSY_TIMEOUT_MS", 250),
            consequences_feedback_ttl_seconds=feedback_ttl,
            consequences_retention_days=retention_days,
            management_token=os.getenv("BAD_DECISIONS_MANAGEMENT_TOKEN"),
            peer_pressure_dir=peer_pressure_dir,
            peer_pressure_room_ttl_seconds=positive("BAD_DECISIONS_PEER_PRESSURE_ROOM_TTL_SECONDS", 6 * 60 * 60),
            peer_pressure_hand_size=positive("BAD_DECISIONS_PEER_PRESSURE_HAND_SIZE", 10),
            peer_pressure_minimum_players=positive("BAD_DECISIONS_PEER_PRESSURE_MINIMUM_PLAYERS", 3),
            peer_pressure_disconnect_timeout_seconds=positive("BAD_DECISIONS_PEER_PRESSURE_DISCONNECT_TIMEOUT_SECONDS", 30),
        )
