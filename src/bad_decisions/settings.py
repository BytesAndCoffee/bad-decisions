from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


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
    peer_pressure_max_rooms: int = 200
    peer_pressure_max_players: int = 12
    peer_pressure_min_free_mb: int = 256
    rate_limit_per_minute: int = 30
    cors_origins: tuple[str, ...] = ()
    management_db: str | None = None
    management_public_origin: str | None = None
    management_attest_url: str | None = None
    management_tags: tuple[str, ...] = ()
    management_nodes: tuple[str, ...] = ()
    management_session_ttl_seconds: int = 180
    tailscale_socket: str = "/var/run/tailscale/tailscaled.sock"

    @property
    def management_enabled(self) -> bool:
        return self.management_db is not None

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

        cors_origins = tuple(item.strip().rstrip("/") for item in os.getenv("BAD_DECISIONS_CORS_ORIGINS", "").split(",") if item.strip())
        for origin in cors_origins:
            parsed = urlsplit(origin)
            try:
                parsed.port
            except ValueError:
                invalid_port = True
            else:
                invalid_port = False
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
                or invalid_port
                or "*" in origin
            ):
                raise ValueError("BAD_DECISIONS_CORS_ORIGINS must list exact http(s)://host[:port] origins, comma-separated")
        management = _management_settings(positive)
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
            peer_pressure_max_rooms=positive("BAD_DECISIONS_PEER_PRESSURE_MAX_ROOMS", 200),
            peer_pressure_max_players=positive("BAD_DECISIONS_PEER_PRESSURE_MAX_PLAYERS", 12),
            peer_pressure_min_free_mb=positive("BAD_DECISIONS_PEER_PRESSURE_MIN_FREE_MB", 256),
            rate_limit_per_minute=positive("BAD_DECISIONS_RATE_LIMIT_PER_MINUTE", 30),
            cors_origins=cors_origins,
            **management,
        )


def _exact_origin(value: str, *, https_only: bool) -> bool:
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in ({"https"} if https_only else {"http", "https"})
        and bool(parsed.hostname)
        and not parsed.path and not parsed.query and not parsed.fragment
        and parsed.username is None and parsed.password is None
        and "*" not in value
    )


def _management_settings(positive) -> dict:
    """The management page is all-or-nothing: a partial configuration is a startup error."""
    db, origin_name, url_name, tags_name = (
        "BAD_DECISIONS_MANAGEMENT_DB", "BAD_DECISIONS_MANAGEMENT_PUBLIC_ORIGIN",
        "BAD_DECISIONS_MANAGEMENT_ATTEST_URL", "BAD_DECISIONS_MANAGEMENT_TAGS",
    )
    values = {name: os.getenv(name, "").strip() for name in (db, origin_name, url_name, tags_name)}
    nodes_raw = os.getenv("BAD_DECISIONS_MANAGEMENT_NODES", "").strip()
    socket = os.getenv("BAD_DECISIONS_TAILSCALE_SOCKET", "/var/run/tailscale/tailscaled.sock")
    ttl = positive("BAD_DECISIONS_MANAGEMENT_SESSION_TTL_SECONDS", 180)
    if not any(values.values()) and not nodes_raw:
        return {"management_session_ttl_seconds": ttl, "tailscale_socket": socket}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"the management page needs all of its settings; missing {', '.join(missing)}")
    if not Path(values[db]).is_absolute():
        raise ValueError("BAD_DECISIONS_MANAGEMENT_DB must be an absolute path")
    origin = values[origin_name].rstrip("/")
    if not _exact_origin(origin, https_only=True):
        raise ValueError("BAD_DECISIONS_MANAGEMENT_PUBLIC_ORIGIN must be an exact https://host[:port] origin")
    attest_url = values[url_name].rstrip("/")
    if not _exact_origin(attest_url, https_only=True):
        raise ValueError("BAD_DECISIONS_MANAGEMENT_ATTEST_URL must be an exact https://host[:port] URL with no path")
    if urlsplit(attest_url).hostname == urlsplit(origin).hostname:
        raise ValueError("BAD_DECISIONS_MANAGEMENT_ATTEST_URL must be the tailnet host, not the public site")
    tags = tuple(sorted({item.strip() for item in values[tags_name].split(",") if item.strip()}))
    if not tags or not all(re.fullmatch(r"tag:[A-Za-z0-9][A-Za-z0-9-]*", tag) for tag in tags):
        raise ValueError("BAD_DECISIONS_MANAGEMENT_TAGS must list Tailscale tags such as tag:mgmt, comma-separated")
    nodes = tuple(sorted({item.strip() for item in nodes_raw.split(",") if item.strip()}))
    if not all(re.fullmatch(r"[A-Za-z0-9]+", node) for node in nodes):
        raise ValueError("BAD_DECISIONS_MANAGEMENT_NODES must list stable node IDs, comma-separated")
    if not Path(socket).is_absolute():
        raise ValueError("BAD_DECISIONS_TAILSCALE_SOCKET must be an absolute path")
    if ttl < 90:
        raise ValueError("BAD_DECISIONS_MANAGEMENT_SESSION_TTL_SECONDS must be at least 90 so a 60-second renewal can land")
    return {
        "management_db": values[db],
        "management_public_origin": origin,
        "management_attest_url": attest_url,
        "management_tags": tags,
        "management_nodes": nodes,
        "management_session_ttl_seconds": ttl,
        "tailscale_socket": socket,
    }
