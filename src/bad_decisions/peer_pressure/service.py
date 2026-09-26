from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..packs import Registry

ROOM_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
PLAYER_ID = re.compile(r"^player_[0-9a-f]{32}$")
REQUEST_ID = re.compile(r"^req_[0-9a-f]{32}$")
STATES = {"WAITING", "PLAYING", "JUDGING", "ROUND_RESULT", "ENDED"}


class PeerPressureError(Exception):
    def __init__(self, reason: str, message: str, *, status: int = 400, revision: int | None = None, resync: bool = False):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status
        self.revision = revision
        self.resync = resync

    def nack(self, request_id: str | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {"type": "NACK", "reason": self.reason, "message": self.message, "resync": self.resync}
        if request_id:
            value["request_id"] = request_id
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
CREATE TABLE question_deck(
  position INTEGER PRIMARY KEY, card_key TEXT NOT NULL, card_id TEXT NOT NULL, pack_id TEXT NOT NULL,
  representation TEXT NOT NULL, template TEXT NOT NULL, slots INTEGER NOT NULL, drawn INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE response_deck(
  position INTEGER PRIMARY KEY, card_key TEXT NOT NULL, response_id TEXT NOT NULL, pack_id TEXT NOT NULL,
  response_text TEXT NOT NULL, drawn INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE rounds(
  id TEXT PRIMARY KEY, round_number INTEGER NOT NULL UNIQUE, question_key TEXT NOT NULL,
  question_id TEXT NOT NULL, question_pack TEXT NOT NULL, representation TEXT NOT NULL,
  template TEXT NOT NULL, slots INTEGER NOT NULL, responsible_adult_player_id TEXT NOT NULL REFERENCES players(id),
  state TEXT NOT NULL, winning_submission_id TEXT, started_at INTEGER NOT NULL, judged_at INTEGER
);
CREATE TABLE hands(
  player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE, card_instance_id TEXT PRIMARY KEY,
  response_key TEXT NOT NULL, response_id TEXT NOT NULL, response_pack TEXT NOT NULL,
  response_text TEXT NOT NULL, dealt_at INTEGER NOT NULL
);
CREATE TABLE submissions(
  id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  player_id TEXT NOT NULL REFERENCES players(id), presentation_order INTEGER, submitted_at INTEGER NOT NULL,
  UNIQUE(round_id, player_id)
);
CREATE TABLE submission_cards(
  submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE, position INTEGER NOT NULL,
  card_instance_id TEXT NOT NULL, response_id TEXT NOT NULL, response_pack TEXT NOT NULL,
  response_text TEXT NOT NULL, PRIMARY KEY(submission_id, position)
);
CREATE TABLE processed_requests(
  request_id TEXT PRIMARY KEY, player_id TEXT NOT NULL, request_type TEXT NOT NULL,
  result_revision INTEGER NOT NULL, result_payload TEXT NOT NULL, created_at INTEGER NOT NULL
);
"""


class PeerPressureService:
    """Server-authoritative multiplayer using one disposable SQLite DB per room."""

    def __init__(
        self,
        root: str | Path,
        registry: Registry,
        *,
        room_ttl_seconds: int = 6 * 60 * 60,
        hand_size: int = 10,
        minimum_players: int = 3,
        disconnect_timeout_seconds: int = 30,
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
        self.now = now
        self.rng = rng or random.SystemRandom()

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
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def cleanup_expired(self) -> int:
        removed = 0
        now = int(self.now())
        for path in self.root.glob("*.sqlite3"):
            try:
                with sqlite3.connect(path) as connection:
                    row = connection.execute("SELECT expires_at,state FROM room WHERE id=1").fetchone()
                if row is None or row[0] <= now or row[1] == "ENDED":
                    path.unlink(missing_ok=True)
                    removed += 1
            except (OSError, sqlite3.Error):
                continue
        return removed

    def create_room(self, room: str) -> dict[str, Any]:
        room = self.validate_room(room)
        self.cleanup_expired()
        path = self._path(room)
        try:
            fd = path.open("xb")
            fd.close()
            path.chmod(0o600)
        except FileExistsError as exc:
            raise PeerPressureError("room_exists", "That room already exists", status=409) from exc
        try:
            with self._connect(room) as connection:
                connection.executescript(SCHEMA)
                now = int(self.now())
                connection.execute(
                    "INSERT INTO room VALUES(1,?,?,?,?,?,?,?,?)",
                    (room, "WAITING", 0, 0, self.hand_size, now, now, now + self.room_ttl_seconds),
                )
                questions = [card for pack in self.registry.packs.values() for card in pack.black]
                responses = [card for pack in self.registry.packs.values() for card in pack.white]
                self.rng.shuffle(questions)
                self.rng.shuffle(responses)
                connection.executemany(
                    "INSERT INTO question_deck(position,card_key,card_id,pack_id,representation,template,slots) VALUES(?,?,?,?,?,?,?)",
                    [(index, f"{card.pack}:{card.id}", card.id, card.pack, card.repr, card.template, card.slots) for index, card in enumerate(questions)],
                )
                connection.executemany(
                    "INSERT INTO response_deck(position,card_key,response_id,pack_id,response_text) VALUES(?,?,?,?,?)",
                    [(index, f"{card.pack}:{card.id}", card.id, card.pack, card.text) for index, card in enumerate(responses)],
                )
            return {"room": room, "state": "WAITING", "revision": 0}
        except Exception:
            path.unlink(missing_ok=True)
            raise

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
            self._mark_stale(connection, exclude=player_id)
            if player_id or session_token:
                if not player_id or not session_token or not PLAYER_ID.fullmatch(player_id):
                    raise PeerPressureError("invalid_session", "A complete room session is required", status=401)
                player = connection.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
                if player is None or not hmac.compare_digest(player["session_token_hash"], self._hash_token(session_token)):
                    raise PeerPressureError("invalid_session", "That room session is not valid", status=401)
                connection.execute("UPDATE players SET connected=1,last_seen=? WHERE id=?", (now, player_id))
                if not player["connected"]:
                    self._bump(connection)
            else:
                if metadata["state"] != "WAITING":
                    raise PeerPressureError("game_in_progress", "New players cannot join after the game begins", status=409)
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
        return {"type": "SYNACK", "room": room, "player_id": player_id, "session_token": session_token, "revision": projection["room"]["revision"], "state": projection}

    def sync(self, room: str, player_id: str, session_token: str) -> dict[str, Any]:
        state = self.project(room, player_id, session_token, touch=True)
        return {"type": "SYNACK", "room": room, "revision": state["room"]["revision"], "state": state}

    def heartbeat(self, room: str, player_id: str, session_token: str, revision: int) -> dict[str, Any]:
        with self._connect(room) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authenticate(connection, player_id, session_token)
            self._mark_stale(connection, exclude=player_id)
            current = self._room(connection)["revision"]
            connection.execute("UPDATE players SET connected=1,last_seen=? WHERE id=?", (int(self.now()), player_id))
            connection.commit()
        if revision != current:
            return {"type": "NACK", "reason": "stale_revision", "revision": current, "resync": True}
        return {"type": "ACK", "revision": current}

    def leave(self, room: str, player_id: str, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "leave", self._leave)

    def start(self, room: str, player_id: str, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "start", self._start)

    def submit(self, room: str, player_id: str, session_token: str, request_id: str, revision: int, cards: list[str]) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "submit", lambda c, p: self._submit(c, p, cards))

    def judge(self, room: str, player_id: str, session_token: str, request_id: str, revision: int, submission_id: str) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "judge", lambda c, p: self._judge(c, p, submission_id))

    def advance(self, room: str, player_id: str, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
        return self._mutation(room, player_id, session_token, request_id, revision, "advance", self._advance)

    def end(self, room: str, player_id: str, session_token: str, request_id: str, revision: int) -> dict[str, Any]:
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
            self._mark_stale(connection, exclude=player_id)
            previous = connection.execute("SELECT result_payload FROM processed_requests WHERE request_id=? AND player_id=?", (request_id, player_id)).fetchone()
            if previous:
                connection.rollback()
                return json.loads(previous[0])
            current = self._room(connection)["revision"]
            if revision != current:
                raise PeerPressureError("stale_revision", "Room state changed; synchronize and try again", revision=current, resync=True, status=409)
            operation(connection, player)
            result_revision = self._bump(connection)
            result = {"type": "ACK", "request_id": request_id, "revision": result_revision}
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
        room = self._room(connection)
        if room["state"] == "PLAYING":
            round_ = self._current_round(connection)
            self._maybe_begin_judging(connection, round_)

    def _start(self, connection: sqlite3.Connection, player: sqlite3.Row) -> None:
        room = self._room(connection)
        if room["state"] != "WAITING":
            raise PeerPressureError("illegal_transition", "The game has already started", status=409)
        first = connection.execute("SELECT id FROM players ORDER BY seat_order LIMIT 1").fetchone()
        if first is None or first[0] != player["id"]:
            raise PeerPressureError("not_responsible_adult", "Only the Responsible Adult may start the game", status=403)
        count = connection.execute("SELECT COUNT(*) FROM players WHERE connected=1").fetchone()[0]
        if count < self.minimum_players:
            raise PeerPressureError("not_enough_players", f"Peer Pressure requires at least {self.minimum_players} players", status=409)
        for row in connection.execute("SELECT id FROM players WHERE connected=1 ORDER BY seat_order"):
            self._fill_hand(connection, row[0])
        self._new_round(connection, first[0], 1)

    def _submit(self, connection: sqlite3.Connection, player: sqlite3.Row, cards: list[str]) -> None:
        room = self._room(connection)
        if room["state"] != "PLAYING":
            raise PeerPressureError("illegal_transition", "Responses are not being accepted right now", status=409)
        round_ = self._current_round(connection)
        if player["id"] == round_["responsible_adult_player_id"]:
            raise PeerPressureError("responsible_adult_cannot_submit", "The Responsible Adult cannot submit a decision", status=403)
        if connection.execute("SELECT 1 FROM submissions WHERE round_id=? AND player_id=?", (round_["id"], player["id"])).fetchone():
            raise PeerPressureError("already_submitted", "You already submitted a decision this round", status=409)
        if len(cards) != round_["slots"]:
            raise PeerPressureError("wrong_card_count", f"This question requires {round_['slots']} responses")
        if len(set(cards)) != len(cards):
            raise PeerPressureError("duplicate_card", "A response instance may only be used once")
        owned = connection.execute(
            f"SELECT * FROM hands WHERE player_id=? AND card_instance_id IN ({','.join('?' for _ in cards)})",
            (player["id"], *cards),
        ).fetchall() if cards else []
        by_id = {row["card_instance_id"]: row for row in owned}
        if len(by_id) != len(cards):
            raise PeerPressureError("card_not_in_hand", "Every submitted response must be in your hand", status=403)
        submission = f"sub_{uuid.uuid4().hex}"
        connection.execute("INSERT INTO submissions(id,round_id,player_id,submitted_at) VALUES(?,?,?,?)", (submission, round_["id"], player["id"], int(self.now())))
        for index, card_id in enumerate(cards):
            row = by_id[card_id]
            connection.execute(
                "INSERT INTO submission_cards VALUES(?,?,?,?,?,?)",
                (submission, index, card_id, row["response_id"], row["response_pack"], row["response_text"]),
            )
            connection.execute("DELETE FROM hands WHERE card_instance_id=?", (card_id,))
        self._maybe_begin_judging(connection, round_)

    def _maybe_begin_judging(self, connection: sqlite3.Connection, round_: sqlite3.Row) -> None:
        eligible = connection.execute("SELECT COUNT(*) FROM players WHERE connected=1 AND id<>?", (round_["responsible_adult_player_id"],)).fetchone()[0]
        submitted = connection.execute(
            "SELECT COUNT(*) FROM submissions s JOIN players p ON p.id=s.player_id WHERE s.round_id=? AND p.connected=1",
            (round_["id"],),
        ).fetchone()[0]
        if submitted >= eligible and submitted > 0:
            ids = [row[0] for row in connection.execute("SELECT id FROM submissions WHERE round_id=?", (round_["id"],))]
            self.rng.shuffle(ids)
            for order, submission_id in enumerate(ids):
                connection.execute("UPDATE submissions SET presentation_order=? WHERE id=?", (order, submission_id))
            connection.execute("UPDATE room SET state='JUDGING' WHERE id=1")
            connection.execute("UPDATE rounds SET state='JUDGING' WHERE id=?", (round_["id"],))

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
        for row in connection.execute("SELECT id FROM players WHERE connected=1"):
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
        first = connection.execute("SELECT id FROM players ORDER BY seat_order LIMIT 1").fetchone()
        if first is None or first[0] != player["id"]:
            raise PeerPressureError("not_room_owner", "Only the player who opened the room may end it", status=403)
        connection.execute("UPDATE room SET state='ENDED' WHERE id=1")

    def _fill_hand(self, connection: sqlite3.Connection, player_id: str) -> None:
        room = self._room(connection)
        count = connection.execute("SELECT COUNT(*) FROM hands WHERE player_id=?", (player_id,)).fetchone()[0]
        needed = room["hand_size"] - count
        rows = connection.execute("SELECT * FROM response_deck WHERE drawn=0 ORDER BY position LIMIT ?", (needed,)).fetchall()
        if len(rows) < needed:
            raise PeerPressureError("response_deck_exhausted", "There are not enough unused responses to replenish every hand", status=409)
        now = int(self.now())
        for row in rows:
            connection.execute("UPDATE response_deck SET drawn=1 WHERE position=?", (row["position"],))
            connection.execute(
                "INSERT INTO hands VALUES(?,?,?,?,?,?,?)",
                (player_id, f"card_{uuid.uuid4().hex}", row["card_key"], row["response_id"], row["pack_id"], row["response_text"], now),
            )

    def _new_round(self, connection: sqlite3.Connection, responsible_adult: str, number: int) -> None:
        question = connection.execute("SELECT * FROM question_deck WHERE drawn=0 ORDER BY position LIMIT 1").fetchone()
        if question is None:
            raise PeerPressureError("question_deck_exhausted", "There are no unused questions left", status=409)
        connection.execute("UPDATE question_deck SET drawn=1 WHERE position=?", (question["position"],))
        round_id = f"round_{uuid.uuid4().hex}"
        connection.execute(
            "INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (round_id, number, question["card_key"], question["card_id"], question["pack_id"], question["representation"], question["template"], question["slots"], responsible_adult, "PLAYING", None, int(self.now()), None),
        )
        connection.execute("UPDATE room SET state='PLAYING',round_number=? WHERE id=1", (number,))

    def project(self, room: str, player_id: str, token: str, *, touch: bool = False) -> dict[str, Any]:
        with self._connect(room) as connection:
            if touch:
                connection.execute("BEGIN IMMEDIATE")
            player = self._authenticate(connection, player_id, token)
            if touch:
                self._mark_stale(connection, exclude=player_id)
                connection.execute("UPDATE players SET connected=1,last_seen=? WHERE id=?", (int(self.now()), player_id))
                connection.commit()
            metadata = self._room(connection)
            self._ensure_live(room, metadata)
            players = [dict(row) for row in connection.execute("SELECT id,display_name,score,connected,seat_order FROM players ORDER BY seat_order")]
            value: dict[str, Any] = {
                "room": {"code": room, "state": metadata["state"], "revision": metadata["revision"], "round": metadata["round_number"]},
                "players": [{"id": row["id"], "name": row["display_name"], "score": row["score"], "connected": bool(row["connected"])} for row in players],
                "responsible_adult": None,
                "question": None,
                "you": {"id": player_id, "hand": [], "submitted": False, "room_owner": bool(players and players[0]["id"] == player_id)},
                "judging": None,
                "result": None,
            }
            round_ = connection.execute("SELECT * FROM rounds ORDER BY round_number DESC LIMIT 1").fetchone()
            if round_ is None:
                return value
            adult = next(row for row in players if row["id"] == round_["responsible_adult_player_id"])
            value["responsible_adult"] = {"id": adult["id"], "name": adult["display_name"]}
            value["question"] = {"id": round_["question_id"], "pack": round_["question_pack"], "text": round_["representation"], "slots": round_["slots"]}
            value["you"]["hand"] = [
                {"card_instance_id": row["card_instance_id"], "id": row["response_id"], "pack": row["response_pack"], "text": row["response_text"]}
                for row in connection.execute("SELECT * FROM hands WHERE player_id=? ORDER BY dealt_at,card_instance_id", (player_id,))
            ]
            value["you"]["submitted"] = connection.execute("SELECT 1 FROM submissions WHERE round_id=? AND player_id=?", (round_["id"], player_id)).fetchone() is not None
            if metadata["state"] == "JUDGING" and player_id == round_["responsible_adult_player_id"]:
                value["judging"] = {"decisions": [
                    {"submission_id": submission["id"], "responses": [card[0] for card in connection.execute("SELECT response_text FROM submission_cards WHERE submission_id=? ORDER BY position", (submission["id"],))]}
                    for submission in connection.execute("SELECT id FROM submissions WHERE round_id=? ORDER BY presentation_order", (round_["id"],))
                ]}
            if metadata["state"] == "ROUND_RESULT":
                winner = connection.execute("SELECT s.player_id,p.display_name FROM submissions s JOIN players p ON p.id=s.player_id WHERE s.id=?", (round_["winning_submission_id"],)).fetchone()
                cards = [row[0] for row in connection.execute("SELECT response_text FROM submission_cards WHERE submission_id=? ORDER BY position", (round_["winning_submission_id"],))]
                value["result"] = {"winning_player": {"id": winner[0], "name": winner[1]}, "responses": cards, "rendered": round_["template"].format(*cards)}
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
    def _authenticate(connection: sqlite3.Connection, player_id: str, token: str) -> sqlite3.Row:
        if not PLAYER_ID.fullmatch(player_id or "") or not token:
            raise PeerPressureError("invalid_session", "Valid room credentials are required", status=401)
        player = connection.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
        if player is None or not hmac.compare_digest(player["session_token_hash"], PeerPressureService._hash_token(token)):
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

    def _mark_stale(self, connection: sqlite3.Connection, *, exclude: str | None = None) -> None:
        cutoff = int(self.now()) - self.disconnect_timeout_seconds
        if exclude:
            cursor = connection.execute(
                "UPDATE players SET connected=0 WHERE connected=1 AND last_seen<? AND id<>?",
                (cutoff, exclude),
            )
        else:
            cursor = connection.execute("UPDATE players SET connected=0 WHERE connected=1 AND last_seen<?", (cutoff,))
        if cursor.rowcount:
            room = self._room(connection)
            if room["state"] == "PLAYING":
                self._maybe_begin_judging(connection, self._current_round(connection))
            self._bump(connection)
