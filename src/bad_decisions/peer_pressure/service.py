from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from ..packs import Registry

ROOM_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
PLAYER_ID = re.compile(r"^player_[0-9a-f]{32}$")
REQUEST_ID = re.compile(r"^req_[0-9a-f]{32}$")
STATES = {"WAITING", "PLAYING", "JUDGING", "ROUND_RESULT", "ENDED"}
ACTIVE_STATES = {"PLAYING", "JUDGING", "ROUND_RESULT"}
SCHEMA_VERSION = 3
# Half-built rooms live under this name until they are linked into place.
BUILDING_SUFFIX = ".building"


class PeerPressureError(Exception):
    def __init__(self, reason: str, message: str, *, status: int = 400, revision: int | None = None, resync: bool = False):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status
        self.revision = revision
        self.resync = resync

    def details(self) -> dict[str, Any]:
        """Extra fields for the API's error envelope."""
        value: dict[str, Any] = {"resync": self.resync}
        if self.revision is not None:
            value["revision"] = self.revision
        return value


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE room(
  id INTEGER PRIMARY KEY CHECK(id=1), room_code TEXT NOT NULL, state TEXT NOT NULL,
  revision INTEGER NOT NULL, round_number INTEGER NOT NULL, hand_size INTEGER NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE players(
  id TEXT PRIMARY KEY, display_name TEXT NOT NULL, session_token_hash TEXT NOT NULL UNIQUE,
  score INTEGER NOT NULL DEFAULT 0, seat_order INTEGER NOT NULL UNIQUE,
  connected INTEGER NOT NULL DEFAULT 1, last_seen INTEGER NOT NULL, joined_at INTEGER NOT NULL
);
CREATE TABLE drawn_cards(
  kind TEXT NOT NULL CHECK(kind IN ('prompt','answer')), card_key TEXT NOT NULL,
  PRIMARY KEY(kind, card_key)
);
CREATE TABLE rounds(
  id TEXT PRIMARY KEY, round_number INTEGER NOT NULL UNIQUE, prompt_key TEXT NOT NULL,
  prompt_id TEXT NOT NULL, prompt_pack TEXT NOT NULL, prompt_text TEXT NOT NULL,
  template TEXT NOT NULL, slots INTEGER NOT NULL, responsible_adult_player_id TEXT NOT NULL REFERENCES players(id),
  state TEXT NOT NULL, winning_submission_id TEXT, started_at INTEGER NOT NULL, judged_at INTEGER
);
CREATE TABLE hands(
  player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE, card_instance_id TEXT PRIMARY KEY,
  answer_key TEXT NOT NULL, answer_id TEXT NOT NULL, answer_pack TEXT NOT NULL,
  answer_text TEXT NOT NULL, dealt_at INTEGER NOT NULL
);
CREATE TABLE submissions(
  id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  player_id TEXT NOT NULL REFERENCES players(id), presentation_order INTEGER, submitted_at INTEGER NOT NULL,
  UNIQUE(round_id, player_id)
);
CREATE TABLE submission_cards(
  submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE, position INTEGER NOT NULL,
  card_instance_id TEXT NOT NULL, answer_id TEXT NOT NULL, answer_pack TEXT NOT NULL,
  answer_text TEXT NOT NULL, PRIMARY KEY(submission_id, position)
);
CREATE TABLE processed_requests(
  request_id TEXT PRIMARY KEY, player_id TEXT NOT NULL, request_type TEXT NOT NULL,
  result_revision INTEGER NOT NULL, result_payload TEXT NOT NULL, created_at INTEGER NOT NULL
);
PRAGMA user_version=3;
"""

class PeerPressureService:
    """Server-authoritative multiplayer using one disposable SQLite DB per room.

    Cards are streamed: a room stores only the keys it has drawn, and each draw
    picks from the in-memory registry (immutable at runtime), so opening a room
    costs one small insert however many packs are loaded.
    """

    def __init__(
        self,
        root: str | Path,
        registry: Registry,
        *,
        room_ttl_seconds: int = 6 * 60 * 60,
        hand_size: int = 10,
        minimum_players: int = 3,
        disconnect_timeout_seconds: int = 30,
        max_rooms: int = 200,
        max_players: int = 12,
        min_free_mb: int = 256,
        now: Callable[[], float] = time.time,
        rng: random.Random | random.SystemRandom | None = None,
    ):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.registry = registry
        self.room_ttl_seconds = room_ttl_seconds
        self.hand_size = hand_size
        self.minimum_players = minimum_players
        self.disconnect_timeout_seconds = disconnect_timeout_seconds
        self.max_rooms = max_rooms
        self.max_players = max_players
        self.min_free_mb = min_free_mb
        self.now = now
        self.rng = rng or random.SystemRandom()
        self._pools: dict[str, tuple[tuple[str, Any], ...]] = {
            "prompt": tuple((f"{card.pack}:{card.id}", card) for pack in registry.packs.values() for card in pack.prompts),
            "answer": tuple((f"{card.pack}:{card.id}", card) for pack in registry.packs.values() for card in pack.answers),
        }

    @staticmethod
    def validate_room(room: str) -> str:
        room = room.strip().lower()
        if not ROOM_CODE.fullmatch(room):
            raise PeerPressureError("invalid_room", "Room IDs must be 3-32 lowercase letters, digits, or hyphens")
        return room

    @staticmethod
    def validate_name(name: str) -> str:
        value = " ".join(name.strip().split())
        if not 1 <= len(value) <= 32 or not value.isprintable():
            raise PeerPressureError("invalid_display_name", "Display names must be 1-32 printable characters")
        return value

    def _path(self, room: str) -> Path:
        return self.root / f"{self.validate_room(room)}.sqlite3"

    def _connect(self, room: str, *, existing: bool = True) -> sqlite3.Connection:
        path = self._path(room)
        if existing and not path.is_file():
            raise PeerPressureError("room_not_found", "That room does not exist or has expired", status=404)
        connection = self._open(path)
        try:
            self._upgrade(connection)
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _upgrade(connection: sqlite3.Connection) -> None:
        """Rooms are disposable, so older room schemas are refused rather than migrated."""
        if connection.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            raise PeerPressureError("room_expired", "That room was created by an older server version; start a new room", status=410)

    def cleanup_expired(self) -> int:
        removed = 0
        now = int(self.now())
        for path in self.root.glob("*.sqlite3"):
            try:
                with closing(sqlite3.connect(path)) as connection:
                    row = connection.execute("SELECT expires_at,state FROM room WHERE id=1").fetchone()
                if row is None or row[0] <= now or row[1] == "ENDED":
                    path.unlink(missing_ok=True)
                    removed += 1
            except (OSError, sqlite3.Error):
                continue
        # Builders that crashed before linking their room into place.
        for path in self.root.glob(f".*{BUILDING_SUFFIX}"):
            try:
                if path.stat().st_mtime < now - 3600:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        return removed

    def create_room(self, room: str) -> dict[str, Any]:
        """Build the room privately, then link it into place so nobody sees a half-made room."""
        room = self.validate_room(room)
        self.cleanup_expired()
        path = self._path(room)
        if path.exists():
            raise PeerPressureError("room_exists", "That room already exists", status=409)
        if sum(1 for _ in self.root.glob("*.sqlite3")) >= self.max_rooms:
            raise PeerPressureError("too_many_rooms", "The server is hosting as many rooms as it can; try again later", status=503)
        if shutil.disk_usage(self.root).free < self.min_free_mb * 1024 * 1024:
            raise PeerPressureError("server_busy", "The server is low on space for new rooms; try again later", status=503)
        building = self.root / f".{room}.{secrets.token_hex(8)}{BUILDING_SUFFIX}"
        try:
            descriptor = os.open(building, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            with closing(self._open(building)) as connection:
                connection.executescript(SCHEMA)
                now = int(self.now())
                connection.execute(
                    "INSERT INTO room VALUES(1,?,?,?,?,?,?,?,?)",
                    (room, "WAITING", 0, 0, self.hand_size, now, now, now + self.room_ttl_seconds),
                )
            try:
                os.link(building, path)
            except FileExistsError as exc:
                raise PeerPressureError("room_exists", "That room already exists", status=409) from exc
            return {"room": room, "state": "WAITING", "revision": 0}
        finally:
            building.unlink(missing_ok=True)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def join(self, room: str, display_name: str, *, player_id: str | None = None, session_token: str | None = None, create: bool = False) -> dict[str, Any]:
        room = self.validate_room(room)
        display_name = self.validate_name(display_name)
        if create and not self._path(room).exists():
            try:
                self.create_room(room)
            except PeerPressureError as exc:
                if exc.reason != "room_exists":
                    raise
        connection = self._connect(room)
        try:
            connection.execute("BEGIN IMMEDIATE")
            metadata = self._room(connection)
            self._ensure_live(room, metadata)
            now = int(self.now())
            if player_id or session_token:
                player_id = self._authenticate(connection, player_id, session_token or "")["id"]
                self._touch(connection, player_id)
            else:
                self._touch(connection, None)
                if metadata["state"] != "WAITING":
                    raise PeerPressureError("game_in_progress", "New players cannot join after the game begins", status=409)
                if connection.execute("SELECT COUNT(*) FROM players").fetchone()[0] >= self.max_players:
                    raise PeerPressureError("room_full", f"This room already has {self.max_players} players", status=409)
                duplicate = connection.execute("SELECT 1 FROM players WHERE display_name=? COLLATE NOCASE", (display_name,)).fetchone()
                if duplicate:
                    raise PeerPressureError("display_name_taken", "That display name is already in this room", status=409)
                player_id = f"player_{uuid.uuid4().hex}"
                session_token = secrets.token_urlsafe(32)
                seat = connection.execute("SELECT COALESCE(MAX(seat_order),-1)+1 FROM players").fetchone()[0]
                connection.execute(
                    "INSERT INTO players(id,display_name,session_token_hash,seat_order,last_seen,joined_at) VALUES(?,?,?,?,?,?)",
                    (player_id, display_name, self._hash_token(session_token), seat, now, now),
                )
                self._bump(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
        connection.close()
        projection = self.project(room, player_id, session_token)
        return {"room": room, "player_id": player_id, "session_token": session_token, "revision": projection["room"]["revision"], "state": projection}

    def sync(self, room: str, player_id: str | None, session_token: str) -> dict[str, Any]:
        state = self.project(room, player_id, session_token, touch=True)
        return {"room": room, "revision": state["room"]["revision"], "state": state}

    def heartbeat(self, room: str, player_id: str | None, session_token: str, revision: int) -> dict[str, Any]:
        with closing(self._connect(room)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                player_id = self._authenticate(connection, player_id, session_token)["id"]
                self._touch(connection, player_id)
                current = self._room(connection)["revision"]
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"revision": current, "resync": revision != current}

    def leave(self, room: str, player_id: str | None, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "leave", self._leave)

    def start(self, room: str, player_id: str | None, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "start", self._start)

    def submit(self, room: str, player_id: str | None, session_token: str, request_id: str, revision: int, cards: list[str]) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "submit", lambda c, p: self._submit(c, p, cards))

    def judge(self, room: str, player_id: str | None, session_token: str, request_id: str, revision: int, submission_id: str) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "judge", lambda c, p: self._judge(c, p, submission_id))

    def advance(self, room: str, player_id: str | None, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "advance", self._advance)

    def end(self, room: str, player_id: str | None, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        result = self._mutation(room, player_id, session_token, request_id, revision, "end", self._end)
        self._path(room).unlink(missing_ok=True)
        return result

    def _mutation(self, room: str, player_id: str, token: str, request_id: str, revision: int, action: str, operation: Callable[[sqlite3.Connection, sqlite3.Row], None]) -> dict[str, Any]:
        if not REQUEST_ID.fullmatch(request_id):
            raise PeerPressureError("invalid_request_id", "request_id must be req_ followed by 32 hexadecimal characters")
        connection = self._connect(room)
        try:
            connection.execute("BEGIN IMMEDIATE")
            player = self._authenticate(connection, player_id, token)
            player_id = player["id"]
            previous = connection.execute("SELECT result_payload FROM processed_requests WHERE request_id=? AND player_id=?", (request_id, player_id)).fetchone()
            if previous:
                connection.rollback()
                return json.loads(previous[0])
            current = self._room(connection)["revision"]
            if revision != current:
                raise PeerPressureError("stale_revision", "Room state changed; synchronize and try again", revision=current, resync=True, status=409)
            self._touch(connection, player_id)
            operation(connection, player)
            result_revision = self._bump(connection)
            result = {"request_id": request_id, "revision": result_revision}
            connection.execute(
                "INSERT INTO processed_requests VALUES(?,?,?,?,?,?)",
                (request_id, player_id, action, result_revision, json.dumps(result, separators=(",", ":")), int(self.now())),
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _leave(self, connection: sqlite3.Connection, player: sqlite3.Row) -> None:
        connection.execute("UPDATE players SET connected=0,last_seen=? WHERE id=?", (int(self.now()), player["id"]))
        self._repair_table(connection)

    @staticmethod
    def _host(connection: sqlite3.Connection) -> str | None:
        """The first connected player in seat order hosts (starts and ends) the room;
        when everyone is away, the player who opened it."""
        row = connection.execute("SELECT id FROM players ORDER BY connected DESC, seat_order LIMIT 1").fetchone()
        return row[0] if row else None

    def _repair_table(self, connection: sqlite3.Connection) -> bool:
        """Keep the game moving after someone leaves or goes away.

        A departed Responsible Adult hands the role to the next connected player
        in seat order. That player's own decision this round (if any) is
        withdrawn and returned to their hand, so they never judge their own
        answers; with no decisions left, the round goes back to collecting them.
        """
        room = self._room(connection)
        if room["state"] not in ACTIVE_STATES:
            return False
        changed = False
        round_ = self._current_round(connection)
        adult = connection.execute("SELECT seat_order,connected FROM players WHERE id=?", (round_["responsible_adult_player_id"],)).fetchone()
        if not adult["connected"]:
            successor = connection.execute(
                "SELECT id FROM players WHERE connected=1 ORDER BY seat_order>? DESC, seat_order LIMIT 1",
                (adult["seat_order"],),
            ).fetchone()
            if successor is None:
                return False
            changed = True
            if room["state"] != "ROUND_RESULT":
                self._withdraw_submission(connection, round_["id"], successor[0])
            connection.execute("UPDATE rounds SET responsible_adult_player_id=? WHERE id=?", (successor[0], round_["id"]))
            round_ = self._current_round(connection)
            if room["state"] == "JUDGING" and not connection.execute("SELECT 1 FROM submissions WHERE round_id=?", (round_["id"],)).fetchone():
                connection.execute("UPDATE room SET state='PLAYING' WHERE id=1")
                connection.execute("UPDATE rounds SET state='PLAYING' WHERE id=?", (round_["id"],))
        if self._room(connection)["state"] == "PLAYING":
            changed = self._maybe_begin_judging(connection, round_) or changed
        return changed

    @staticmethod
    def _withdraw_submission(connection: sqlite3.Connection, round_id: str, player_id: str) -> None:
        submission = connection.execute("SELECT id FROM submissions WHERE round_id=? AND player_id=?", (round_id, player_id)).fetchone()
        if submission is None:
            return
        for card in connection.execute("SELECT * FROM submission_cards WHERE submission_id=?", (submission[0],)).fetchall():
            connection.execute(
                "INSERT INTO hands VALUES(?,?,?,?,?,?,?)",
                (player_id, card["card_instance_id"], f"{card['answer_pack']}:{card['answer_id']}", card["answer_id"], card["answer_pack"], card["answer_text"], 0),
            )
        connection.execute("DELETE FROM submissions WHERE id=?", (submission[0],))

    def _start(self, connection: sqlite3.Connection, player: sqlite3.Row) -> None:
        room = self._room(connection)
        if room["state"] != "WAITING":
            raise PeerPressureError("illegal_transition", "The game has already started", status=409)
        if self._host(connection) != player["id"]:
            raise PeerPressureError("not_responsible_adult", "Only the room's host may start the game", status=403)
        count = connection.execute("SELECT COUNT(*) FROM players WHERE connected=1").fetchone()[0]
        if count < self.minimum_players:
            raise PeerPressureError("not_enough_players", f"Peer Pressure requires at least {self.minimum_players} players", status=409)
        for row in connection.execute("SELECT id FROM players WHERE connected=1 ORDER BY seat_order").fetchall():
            self._fill_hand(connection, row[0])
        self._new_round(connection, player["id"], 1)

    def _submit(self, connection: sqlite3.Connection, player: sqlite3.Row, cards: list[str]) -> None:
        room = self._room(connection)
        if room["state"] != "PLAYING":
            raise PeerPressureError("illegal_transition", "Answers are not being accepted right now", status=409)
        round_ = self._current_round(connection)
        if player["id"] == round_["responsible_adult_player_id"]:
            raise PeerPressureError("responsible_adult_cannot_submit", "The Responsible Adult cannot submit a decision", status=403)
        if connection.execute("SELECT 1 FROM submissions WHERE round_id=? AND player_id=?", (round_["id"], player["id"])).fetchone():
            raise PeerPressureError("already_submitted", "You already submitted a decision this round", status=409)
        if len(cards) != round_["slots"]:
            raise PeerPressureError("wrong_card_count", f"This prompt needs {round_['slots']} answers")
        if len(set(cards)) != len(cards):
            raise PeerPressureError("duplicate_card", "Each answer card may only be used once")
        owned = connection.execute(
            f"SELECT * FROM hands WHERE player_id=? AND card_instance_id IN ({','.join('?' for _ in cards)})",
            (player["id"], *cards),
        ).fetchall() if cards else []
        by_id = {row["card_instance_id"]: row for row in owned}
        if len(by_id) != len(cards):
            raise PeerPressureError("card_not_in_hand", "Every submitted answer must be in your hand", status=403)
        submission = f"sub_{uuid.uuid4().hex}"
        connection.execute("INSERT INTO submissions(id,round_id,player_id,submitted_at) VALUES(?,?,?,?)", (submission, round_["id"], player["id"], int(self.now())))
        for index, card_id in enumerate(cards):
            row = by_id[card_id]
            connection.execute(
                "INSERT INTO submission_cards VALUES(?,?,?,?,?,?)",
                (submission, index, card_id, row["answer_id"], row["answer_pack"], row["answer_text"]),
            )
            connection.execute("DELETE FROM hands WHERE card_instance_id=?", (card_id,))
        self._maybe_begin_judging(connection, round_)

    def _maybe_begin_judging(self, connection: sqlite3.Connection, round_: sqlite3.Row) -> bool:
        eligible = connection.execute("SELECT COUNT(*) FROM players WHERE connected=1 AND id<>?", (round_["responsible_adult_player_id"],)).fetchone()[0]
        submitted = connection.execute(
            "SELECT COUNT(*) FROM submissions s JOIN players p ON p.id=s.player_id WHERE s.round_id=? AND p.connected=1",
            (round_["id"],),
        ).fetchone()[0]
        if submitted >= eligible and submitted > 0:
            ids = [row[0] for row in connection.execute("SELECT id FROM submissions WHERE round_id=? ORDER BY id", (round_["id"],))]
            self.rng.shuffle(ids)
            for order, submission_id in enumerate(ids):
                connection.execute("UPDATE submissions SET presentation_order=? WHERE id=?", (order, submission_id))
            connection.execute("UPDATE room SET state='JUDGING' WHERE id=1")
            connection.execute("UPDATE rounds SET state='JUDGING' WHERE id=?", (round_["id"],))
            return True
        return False

    def _judge(self, connection: sqlite3.Connection, player: sqlite3.Row, submission_id: str) -> None:
        room = self._room(connection)
        round_ = self._current_round(connection)
        if room["state"] != "JUDGING":
            raise PeerPressureError("illegal_transition", "There are no decisions to judge right now", status=409)
        if player["id"] != round_["responsible_adult_player_id"]:
            raise PeerPressureError("not_responsible_adult", "Only the Responsible Adult may choose the consequence", status=403)
        submission = connection.execute("SELECT * FROM submissions WHERE id=? AND round_id=?", (submission_id, round_["id"])).fetchone()
        if submission is None:
            raise PeerPressureError("invalid_submission", "That anonymous decision is not part of this round")
        connection.execute("UPDATE players SET score=score+1 WHERE id=?", (submission["player_id"],))
        connection.execute("UPDATE rounds SET state='ROUND_RESULT',winning_submission_id=?,judged_at=? WHERE id=?", (submission_id, int(self.now()), round_["id"]))
        connection.execute("UPDATE room SET state='ROUND_RESULT' WHERE id=1")

    def _advance(self, connection: sqlite3.Connection, player: sqlite3.Row) -> None:
        room = self._room(connection)
        round_ = self._current_round(connection)
        if room["state"] != "ROUND_RESULT":
            raise PeerPressureError("illegal_transition", "The table is not ready for another round", status=409)
        if player["id"] != round_["responsible_adult_player_id"]:
            raise PeerPressureError("not_responsible_adult", "Only the Responsible Adult may advance the table", status=403)
        for row in connection.execute("SELECT id FROM players WHERE connected=1").fetchall():
            self._fill_hand(connection, row[0])
        next_adult = connection.execute(
            "SELECT id FROM players WHERE connected=1 AND seat_order>? ORDER BY seat_order LIMIT 1",
            (player["seat_order"],),
        ).fetchone()
        if next_adult is None:
            next_adult = connection.execute("SELECT id FROM players WHERE connected=1 ORDER BY seat_order LIMIT 1").fetchone()
        if next_adult is None:
            raise PeerPressureError("not_enough_players", "No players remain at the table", status=409)
        self._new_round(connection, next_adult[0], room["round_number"] + 1)

    def _end(self, connection: sqlite3.Connection, player: sqlite3.Row) -> None:
        if self._host(connection) != player["id"]:
            raise PeerPressureError("not_room_owner", "Only the room's host may end it", status=403)
        connection.execute("UPDATE room SET state='ENDED' WHERE id=1")

    def _draw(self, connection: sqlite3.Connection, kind: str, count: int) -> list[tuple[str, Any]]:
        """Draw ``count`` cards this room has not drawn before, straight from the registry."""
        if count <= 0:
            return []
        pool = self._pools[kind]
        drawn = {row[0] for row in connection.execute("SELECT card_key FROM drawn_cards WHERE kind=?", (kind,))}
        if len(drawn) * 2 < len(pool):
            # Mostly undrawn: sample and skip repeats, without scanning the whole pool.
            picks: dict[str, Any] = {}
            while len(picks) < count:
                key, card = self.rng.choice(pool)
                if key not in drawn and key not in picks:
                    picks[key] = card
            chosen = list(picks.items())
        else:
            remaining = [entry for entry in pool if entry[0] not in drawn]
            if len(remaining) < count:
                raise PeerPressureError(f"{kind}_deck_exhausted", f"There are not enough unused {kind}s left", status=409)
            chosen = self.rng.sample(remaining, count)
        connection.executemany("INSERT INTO drawn_cards VALUES(?,?)", [(kind, key) for key, _card in chosen])
        return chosen

    def _fill_hand(self, connection: sqlite3.Connection, player_id: str) -> None:
        room = self._room(connection)
        count = connection.execute("SELECT COUNT(*) FROM hands WHERE player_id=?", (player_id,)).fetchone()[0]
        now = int(self.now())
        for key, card in self._draw(connection, "answer", room["hand_size"] - count):
            connection.execute(
                "INSERT INTO hands VALUES(?,?,?,?,?,?,?)",
                (player_id, f"card_{uuid.uuid4().hex}", key, card.id, card.pack, card.text, now),
            )

    def _new_round(self, connection: sqlite3.Connection, responsible_adult: str, number: int) -> None:
        [(key, prompt)] = self._draw(connection, "prompt", 1)
        round_id = f"round_{uuid.uuid4().hex}"
        connection.execute(
            "INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (round_id, number, key, prompt.id, prompt.pack, prompt.text, prompt.template, prompt.slots, responsible_adult, "PLAYING", None, int(self.now()), None),
        )
        connection.execute("UPDATE room SET state='PLAYING',round_number=? WHERE id=1", (number,))

    def project(self, room: str, player_id: str | None, token: str, *, touch: bool = False) -> dict[str, Any]:
        """Build one player's view from a single consistent snapshot."""
        with closing(self._connect(room)) as connection:
            connection.execute("BEGIN IMMEDIATE" if touch else "BEGIN")
            try:
                player_id = self._authenticate(connection, player_id, token)["id"]
                if touch:
                    self._touch(connection, player_id)
                value = self._project(connection, room, player_id)
                connection.commit()
                return value
            except Exception:
                connection.rollback()
                raise

    def _project(self, connection: sqlite3.Connection, room: str, player_id: str) -> dict[str, Any]:
        metadata = self._room(connection)
        self._ensure_live(room, metadata)
        players = [dict(row) for row in connection.execute("SELECT id,display_name,score,connected,seat_order FROM players ORDER BY seat_order")]
        value: dict[str, Any] = {
            "room": {"code": room, "state": metadata["state"], "revision": metadata["revision"], "round": metadata["round_number"]},
            "players": [{"id": row["id"], "name": row["display_name"], "score": row["score"], "connected": bool(row["connected"])} for row in players],
            "responsible_adult": None,
            "prompt": None,
            "you": {"id": player_id, "hand": [], "submitted": False, "room_owner": self._host(connection) == player_id},
            "judging": None,
            "result": None,
        }
        round_ = connection.execute("SELECT * FROM rounds ORDER BY round_number DESC LIMIT 1").fetchone()
        if round_ is None:
            return value
        adult = next(row for row in players if row["id"] == round_["responsible_adult_player_id"])
        value["responsible_adult"] = {"id": adult["id"], "name": adult["display_name"]}
        value["prompt"] = {"id": round_["prompt_id"], "pack": round_["prompt_pack"], "text": round_["prompt_text"], "slots": round_["slots"]}
        value["you"]["hand"] = [
            {"card_instance_id": row["card_instance_id"], "id": row["answer_id"], "pack": row["answer_pack"], "text": row["answer_text"]}
            for row in connection.execute("SELECT * FROM hands WHERE player_id=? ORDER BY dealt_at,card_instance_id", (player_id,))
        ]
        value["you"]["submitted"] = connection.execute("SELECT 1 FROM submissions WHERE round_id=? AND player_id=?", (round_["id"], player_id)).fetchone() is not None
        if metadata["state"] == "JUDGING" and player_id == round_["responsible_adult_player_id"]:
            value["judging"] = {"decisions": [
                {"submission_id": submission["id"], "answers": [card[0] for card in connection.execute("SELECT answer_text FROM submission_cards WHERE submission_id=? ORDER BY position", (submission["id"],))]}
                for submission in connection.execute("SELECT id FROM submissions WHERE round_id=? ORDER BY presentation_order", (round_["id"],))
            ]}
        if metadata["state"] == "ROUND_RESULT":
            winner = connection.execute("SELECT s.player_id,p.display_name FROM submissions s JOIN players p ON p.id=s.player_id WHERE s.id=?", (round_["winning_submission_id"],)).fetchone()
            cards = [row[0] for row in connection.execute("SELECT answer_text FROM submission_cards WHERE submission_id=? ORDER BY position", (round_["winning_submission_id"],))]
            value["result"] = {"winning_player": {"id": winner[0], "name": winner[1]}, "answers": cards, "rendered": round_["template"].format(*cards)}
        return value

    @staticmethod
    def _room(connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM room WHERE id=1").fetchone()
        if row is None or row["state"] not in STATES:
            raise PeerPressureError("room_corrupt", "Room state is unavailable", status=500)
        return row

    def _ensure_live(self, room: str, metadata: sqlite3.Row) -> None:
        if metadata["expires_at"] <= int(self.now()) or metadata["state"] == "ENDED":
            self._path(room).unlink(missing_ok=True)
            raise PeerPressureError("room_expired", "That room has expired", status=410)

    @staticmethod
    def _authenticate(connection: sqlite3.Connection, player_id: str | None, token: str) -> sqlite3.Row:
        """The session token identifies the player; a player_id, when given, must match it."""
        if not token or (player_id is not None and not PLAYER_ID.fullmatch(player_id)):
            raise PeerPressureError("invalid_session", "Valid room credentials are required", status=401)
        token_hash = PeerPressureService._hash_token(token)
        player = connection.execute("SELECT * FROM players WHERE session_token_hash=?", (token_hash,)).fetchone()
        if player is None or not hmac.compare_digest(player["session_token_hash"], token_hash) or player_id not in (None, player["id"]):
            raise PeerPressureError("invalid_session", "Valid room credentials are required", status=401)
        return player

    def _current_round(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM rounds ORDER BY round_number DESC LIMIT 1").fetchone()
        if row is None:
            raise PeerPressureError("round_unavailable", "There is no active round", status=409)
        return row

    def _bump(self, connection: sqlite3.Connection) -> int:
        now = int(self.now())
        connection.execute("UPDATE room SET revision=revision+1,updated_at=?,expires_at=? WHERE id=1", (now, now + self.room_ttl_seconds))
        return connection.execute("SELECT revision FROM room WHERE id=1").fetchone()[0]

    def _touch(self, connection: sqlite3.Connection, player_id: str | None) -> None:
        """Record activity, mark silent players away, and keep the table playable.

        Bumps the revision only when something other players can see changed.
        """
        now = int(self.now())
        changed = False
        if player_id:
            row = connection.execute("SELECT connected FROM players WHERE id=?", (player_id,)).fetchone()
            connection.execute("UPDATE players SET connected=1,last_seen=? WHERE id=?", (now, player_id))
            changed = row is not None and not row[0]
        cursor = connection.execute(
            "UPDATE players SET connected=0 WHERE connected=1 AND last_seen<? AND id<>?",
            (now - self.disconnect_timeout_seconds, player_id or ""),
        )
        changed = cursor.rowcount > 0 or changed
        changed = self._repair_table(connection) or changed
        if changed:
            self._bump(connection)
