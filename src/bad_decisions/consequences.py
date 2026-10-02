"""Consequences: optional durable local analytics and feedback."""
from __future__ import annotations
import hashlib, hmac, json, secrets, sqlite3, time, uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .models import Round

HASH_SCHEMA = "consequences.v1"
SCHEMA_VERSION = 3

def canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

def round_identity(round_: Round) -> tuple[str, list[str], str]:
    prompt = canonical_hash({"schema":"consequences.prompt.v1","text":round_.prompt.text,"template":round_.prompt.template,"pick":round_.prompt.slots})
    answers = [canonical_hash({"schema":"consequences.answer.v1","text":card.text}) for card in round_.answers]
    combination = canonical_hash({"schema":"consequences.combination.v1","prompt":prompt,"answers":answers,"rendering":{"template":round_.prompt.template}})
    return prompt, answers, combination

def valid_uuid(value: str | None) -> str | None:
    if not value or len(value) > 64: return None
    try: return str(uuid.UUID(value))
    except ValueError: return None

@dataclass(frozen=True)
class IssuedRound:
    round_id: str
    combination_hash: str
    feedback_token: str | None
    expires_at: str | None

# --- schema -----------------------------------------------------------------
# contents is the registry every hash points at: prompt_hash, combination_answers
# and round_elements all reference it, so "the FK" is enforced by SQLite instead
# of relying on hash equality. Card text lives here once, not once per draw.

_ELEMENT_COLUMNS = "round_id,role,slot_index,content_hash,pack_id,pack_version,card_id,source_ref,license_id,attribution,modifications_json"

_REQUEST_EVENTS = [
    "CREATE TABLE IF NOT EXISTS request_events(request_id TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL, route TEXT NOT NULL, method TEXT NOT NULL, status INTEGER NOT NULL, duration_ms REAL NOT NULL, client_id TEXT, session_id TEXT, round_id TEXT, error_category TEXT)",
    "CREATE INDEX IF NOT EXISTS request_events_time ON request_events(occurred_at)",
]
_CONTENTS = "CREATE TABLE contents(content_hash TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('prompt','answer')), text TEXT, created_at INTEGER NOT NULL)"
_COMBINATION_ANSWERS = "CREATE TABLE combination_answers(combination_hash TEXT NOT NULL REFERENCES combinations(combination_hash) ON DELETE CASCADE, slot_index INTEGER NOT NULL CHECK(slot_index >= 0), content_hash TEXT NOT NULL REFERENCES contents(content_hash), PRIMARY KEY(combination_hash, slot_index))"
_ROUNDS = "CREATE TABLE rounds(round_id TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL, request_id TEXT, combination_hash TEXT NOT NULL REFERENCES combinations(combination_hash), client_id TEXT, session_id TEXT, feedback_expires_at INTEGER NOT NULL, token_verifier BLOB NOT NULL)"
_FEEDBACK = "CREATE TABLE feedback(round_id TEXT PRIMARY KEY REFERENCES rounds(round_id) ON DELETE CASCADE, enjoyed INTEGER NOT NULL CHECK(enjoyed IN (0,1)), created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
_COMBINATION_STATS = "CREATE TABLE combination_stats(combination_hash TEXT PRIMARY KEY REFERENCES combinations(combination_hash) ON DELETE CASCADE, draw_count INTEGER NOT NULL CHECK(draw_count >= 0), enjoy_count INTEGER NOT NULL DEFAULT 0 CHECK(enjoy_count >= 0), regret_count INTEGER NOT NULL DEFAULT 0 CHECK(regret_count >= 0), updated_at INTEGER NOT NULL)"
_CONTENT_STATS = "CREATE TABLE content_stats(content_hash TEXT PRIMARY KEY REFERENCES contents(content_hash) ON DELETE CASCADE, draw_count INTEGER NOT NULL CHECK(draw_count >= 0), enjoy_count INTEGER NOT NULL DEFAULT 0 CHECK(enjoy_count >= 0), regret_count INTEGER NOT NULL DEFAULT 0 CHECK(regret_count >= 0), updated_at INTEGER NOT NULL)"
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS rounds_time ON rounds(occurred_at)",
    "CREATE INDEX IF NOT EXISTS rounds_combination ON rounds(combination_hash)",
    "CREATE INDEX IF NOT EXISTS round_elements_content ON round_elements(content_hash)",
    "CREATE INDEX IF NOT EXISTS combinations_prompt ON combinations(prompt_hash)",
    "CREATE INDEX IF NOT EXISTS combination_answers_content ON combination_answers(content_hash)",
]

def _combinations_ddl(name: str) -> str:
    return f"CREATE TABLE {name}(combination_hash TEXT PRIMARY KEY, hash_schema TEXT NOT NULL, prompt_hash TEXT NOT NULL REFERENCES contents(content_hash), answers_json TEXT NOT NULL, rendering_json TEXT NOT NULL, created_at INTEGER NOT NULL)"

def _round_elements_ddl(name: str) -> str:
    return f"CREATE TABLE {name}(round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('prompt','answer')), slot_index INTEGER NOT NULL, content_hash TEXT NOT NULL REFERENCES contents(content_hash), pack_id TEXT NOT NULL, pack_version TEXT NOT NULL, card_id TEXT NOT NULL, source_ref TEXT, license_id TEXT NOT NULL, attribution TEXT NOT NULL, modifications_json TEXT NOT NULL, PRIMARY KEY(round_id,role,slot_index))"


class ConsequencesStore:
    """SQLite is a single-host writer store: draws/votes are synchronous and atomic."""
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 250, feedback_ttl_seconds: int = 604800, readonly: bool = False) -> None:
        self.path = Path(path)
        if not self.path.is_absolute(): raise ValueError("consequences database path must be absolute")
        if not self.path.parent.is_dir(): raise ValueError(f"consequences database directory does not exist: {self.path.parent}")
        self.busy_timeout_ms, self.feedback_ttl_seconds, self.readonly = busy_timeout_ms, feedback_ttl_seconds, readonly
        if not readonly: self._initialize()

    def _connect(self) -> sqlite3.Connection:
        target = f"{self.path.as_uri()}?mode=ro" if self.readonly else self.path
        con = sqlite3.connect(target, timeout=self.busy_timeout_ms / 1000, isolation_level=None, uri=self.readonly)
        con.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        # sqlite3.Connection's own context manager never closes the connection.
        con = self._connect()
        try: yield con
        finally: con.close()

    # --- schema management ----------------------------------------------------

    def _initialize(self) -> None:
        with self._db() as c:
            # foreign_keys can't be toggled inside a transaction, and the v3 table
            # rebuild must not fire ON DELETE CASCADE when the old tables are dropped.
            c.execute("PRAGMA foreign_keys = OFF")
            try:
                c.execute("BEGIN IMMEDIATE")
                try:
                    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)")
                    fresh = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rounds'").fetchone() is None
                    current = c.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
                    if fresh: self._create_schema(c)
                    elif current < 3: self._migrate_v3(c)
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK"); raise
            finally:
                c.execute("PRAGMA foreign_keys = ON")

    def _create_schema(self, c: sqlite3.Connection) -> None:
        for stmt in [*_REQUEST_EVENTS, _CONTENTS, _combinations_ddl("combinations"), _COMBINATION_ANSWERS, _ROUNDS,
                     _round_elements_ddl("round_elements"), _FEEDBACK, _COMBINATION_STATS, _CONTENT_STATS, *_INDEXES]:
            c.execute(stmt)
        c.executemany("INSERT OR IGNORE INTO schema_migrations VALUES (?)", [(v,) for v in range(1, SCHEMA_VERSION + 1)])

    def _migrate_v3(self, c: sqlite3.Connection) -> None:
        """v1/v2 -> v3: add contents + combination_answers, enforce content FKs, add content_stats.

        Runs inside the caller's transaction with foreign_keys OFF (SQLite's
        recommended create/copy/drop/rename table rebuild), then verifies with
        foreign_key_check before the caller commits.
        """
        for stmt in _REQUEST_EVENTS: c.execute(stmt)
        cols = {row[1] for row in c.execute("PRAGMA table_info(round_elements)")}
        text_expr = "MAX(re.content_text)" if "content_text" in cols else "NULL"

        # 1. contents registry, backfilled from every place a hash appears.
        c.execute(_CONTENTS)
        # Prompt and answer hashes use different schema tags, so a hash has one role.
        # LEFT JOIN: an element whose round is missing still gets its content row;
        # the dangling round_id itself is then reported by foreign_key_check.
        c.execute(f"""INSERT INTO contents(content_hash,role,text,created_at)
            SELECT re.content_hash, MIN(re.role), {text_expr}, COALESCE(MIN(r.occurred_at), ?)
            FROM round_elements re LEFT JOIN rounds r ON r.round_id = re.round_id GROUP BY re.content_hash""", (self._now(),))
        c.execute("""INSERT OR IGNORE INTO contents(content_hash,role,text,created_at)
            SELECT prompt_hash, 'prompt', NULL, MIN(created_at) FROM combinations GROUP BY prompt_hash""")
        c.execute("""INSERT OR IGNORE INTO contents(content_hash,role,text,created_at)
            SELECT j.value, 'answer', NULL, MIN(co.created_at) FROM combinations co, json_each(co.answers_json) j GROUP BY j.value""")

        # 2. Rebuild round_elements with a real FK (and without the per-draw content_text copy).
        c.execute(_round_elements_ddl("round_elements_v3"))
        c.execute(f"INSERT INTO round_elements_v3({_ELEMENT_COLUMNS}) SELECT {_ELEMENT_COLUMNS} FROM round_elements")
        c.execute("DROP TABLE round_elements")
        c.execute("ALTER TABLE round_elements_v3 RENAME TO round_elements")

        # 3. Rebuild combinations so prompt_hash references contents.
        c.execute(_combinations_ddl("combinations_v3"))
        c.execute("INSERT INTO combinations_v3 SELECT combination_hash,hash_schema,prompt_hash,answers_json,rendering_json,created_at FROM combinations")
        c.execute("DROP TABLE combinations")
        c.execute("ALTER TABLE combinations_v3 RENAME TO combinations")

        # 4. Ordered answer slots as rows, so combination -> answer is a join, not json_each.
        c.execute(_COMBINATION_ANSWERS)
        c.execute("""INSERT INTO combination_answers(combination_hash,slot_index,content_hash)
            SELECT co.combination_hash, CAST(j.key AS INTEGER), j.value FROM combinations co, json_each(co.answers_json) j""")

        # 5. Per-card stats cache, then indexes, then derive both caches from the event rows.
        c.execute(_CONTENT_STATS)
        for stmt in _INDEXES: c.execute(stmt)
        self._rebuild(c)

        problems = c.execute("PRAGMA foreign_key_check").fetchall()
        if problems: raise RuntimeError(f"consequences v3 migration left dangling references: {problems[:5]}")
        c.executemany("INSERT OR IGNORE INTO schema_migrations VALUES (?)", [(v,) for v in range(1, SCHEMA_VERSION + 1)])

    @staticmethod
    def _now() -> int: return int(time.time())
    @staticmethod
    def _iso(value: int) -> str: return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")

    # --- writes -----------------------------------------------------------------

    def record_round(self, round_: Round, *, request_id: str | None, client_id: str | None, session_id: str | None, feedback_enabled: bool) -> IssuedRound:
        now, round_id = self._now(), str(uuid.uuid4())
        prompt, answers, combo = round_identity(round_)
        token = secrets.token_urlsafe(32) if feedback_enabled else None
        verifier = hashlib.sha256(token.encode()).digest() if token else b""
        packs = round_.provenance
        contents = [(prompt,"prompt",round_.prompt.text)] + [(d,"answer",card.text) for d,card in zip(answers,round_.answers)]
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.executemany("INSERT INTO contents(content_hash,role,text,created_at) VALUES(?,?,?,?) ON CONFLICT(content_hash) DO UPDATE SET text=COALESCE(contents.text, excluded.text)", [(h,role,text,now) for h,role,text in contents])
                c.execute("INSERT OR IGNORE INTO combinations VALUES(?,?,?,?,?,?)", (combo,HASH_SCHEMA,prompt,json.dumps(answers),json.dumps({"template":round_.prompt.template}),now))
                c.executemany("INSERT OR IGNORE INTO combination_answers(combination_hash,slot_index,content_hash) VALUES(?,?,?)", [(combo,i,d) for i,d in enumerate(answers)])
                c.execute("INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?)", (round_id,now,request_id,combo,client_id,session_id,now+self.feedback_ttl_seconds,verifier))
                cards = [("prompt",0,prompt,round_.prompt)] + [("answer",i,d,card) for i,(d,card) in enumerate(zip(answers,round_.answers))]
                c.executemany(f"INSERT INTO round_elements({_ELEMENT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(round_id,role,slot,digest,card.pack,packs[card.pack].version,card.id,card.source_ref,packs[card.pack].license_id,packs[card.pack].attribution,"[]") for role,slot,digest,card in cards])
                c.execute("INSERT INTO combination_stats(combination_hash,draw_count,updated_at) VALUES(?,1,?) ON CONFLICT(combination_hash) DO UPDATE SET draw_count=draw_count+1,updated_at=excluded.updated_at", (combo,now))
                # One draw per distinct card per round, matching how votes are attributed below.
                c.executemany("INSERT INTO content_stats(content_hash,draw_count,updated_at) VALUES(?,1,?) ON CONFLICT(content_hash) DO UPDATE SET draw_count=draw_count+1,updated_at=excluded.updated_at", [(h,now) for h in dict.fromkeys(h for h,_,_ in contents)])
                if request_id: c.execute("UPDATE request_events SET round_id=? WHERE request_id=?", (round_id,request_id))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK"); raise
        return IssuedRound(round_id,combo,token,self._iso(now+self.feedback_ttl_seconds) if token else None)

    def record_request(self, *, request_id: str, route: str, method: str, status: int, duration_ms: float, client_id: str | None, session_id: str | None) -> None:
        with self._db() as c:
            c.execute("INSERT OR IGNORE INTO request_events VALUES(?,?,?,?,?,?,?,?,NULL,?)", (request_id,self._now(),route[:160],method[:12],status,min(duration_ms,600000),client_id,session_id,None if status < 400 else f"http_{status//100}xx"))

    def link_request(self, request_id: str, round_id: str) -> None:
        with self._db() as c: c.execute("UPDATE request_events SET round_id=? WHERE request_id=?", (round_id, request_id))

    def feedback(self, round_id: str, token: str, enjoyed: bool | None) -> tuple[str, bool, bool | None, bool]:
        if not token or len(token) > 512: return "missing",False,None,False
        now = self._now()
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute("SELECT combination_hash,feedback_expires_at,token_verifier FROM rounds WHERE round_id=?",(round_id,)).fetchone()
                if row is None or not hmac.compare_digest(row[2],hashlib.sha256(token.encode()).digest()): c.execute("ROLLBACK"); return "missing",False,None,False
                if row[1] < now: c.execute("ROLLBACK"); return "expired",False,None,False
                oldrow = c.execute("SELECT enjoyed FROM feedback WHERE round_id=?",(round_id,)).fetchone()
                old = None if oldrow is None else bool(oldrow[0]); created = oldrow is None and enjoyed is not None; changed = old != enjoyed
                if enjoyed is None:
                    if oldrow is not None: c.execute("DELETE FROM feedback WHERE round_id=?",(round_id,))
                elif oldrow is None: c.execute("INSERT INTO feedback VALUES(?,?,?,?)",(round_id,int(enjoyed),now,now))
                else: c.execute("UPDATE feedback SET enjoyed=?,updated_at=? WHERE round_id=?",(int(enjoyed),now,round_id))
                if changed:
                    d_enjoy, d_regret = (enjoyed is True)-(old is True), (enjoyed is False)-(old is False)
                    c.execute("UPDATE combination_stats SET enjoy_count=enjoy_count+?,regret_count=regret_count+?,updated_at=? WHERE combination_hash=?", (d_enjoy,d_regret,now,row[0]))
                    # IN (...) credits each distinct card once, even if it fills two slots.
                    c.execute("UPDATE content_stats SET enjoy_count=enjoy_count+?,regret_count=regret_count+?,updated_at=? WHERE content_hash IN (SELECT content_hash FROM round_elements WHERE round_id=?)", (d_enjoy,d_regret,now,round_id))
                c.execute("COMMIT"); return "ok",changed,enjoyed,created
            except Exception:
                c.execute("ROLLBACK"); raise

    # --- reads ------------------------------------------------------------------

    @staticmethod
    def _score_dict(key: str, value: str, draws: int, enjoy: int, regret: int) -> dict[str, Any]:
        votes = enjoy + regret
        return {key:value,"draw_count":draws,"enjoy_count":enjoy,"regret_count":regret,"vote_count":votes,"score":None if not votes else (enjoy-regret)/votes}

    def stats(self, combo: str) -> dict[str,Any] | None:
        with self._db() as c: row=c.execute("SELECT draw_count,enjoy_count,regret_count FROM combination_stats WHERE combination_hash=?",(combo,)).fetchone()
        return None if row is None else self._score_dict("combination_hash", combo, *row)

    def content_stats(self, content_hash: str) -> dict[str,Any] | None:
        with self._db() as c:
            self._require_v3(c)
            row = c.execute("SELECT ct.role, s.draw_count, s.enjoy_count, s.regret_count FROM content_stats s JOIN contents ct ON ct.content_hash=s.content_hash WHERE s.content_hash=?", (content_hash,)).fetchone()
        if row is None: return None
        return {**self._score_dict("content_hash", content_hash, *row[1:]), "role": row[0]}

    def report(self) -> dict[str,Any]:
        with self._db() as c:
            requests=c.execute("SELECT COUNT(*),COALESCE(AVG(duration_ms),0),COALESCE(SUM(status>=400),0) FROM request_events").fetchone()
            draws=c.execute("SELECT COUNT(*),COUNT(DISTINCT combination_hash) FROM rounds").fetchone()
            votes=c.execute("SELECT COUNT(*),COALESCE(SUM(enjoyed=1),0),COALESCE(SUM(enjoyed=0),0) FROM feedback").fetchone()
        return {"schema_version":1,"requests":{"count":requests[0],"average_duration_ms":round(requests[1],2),"errors":requests[2]},"draws":{"recorded":draws[0],"combinations":draws[1]},"feedback":{"votes":votes[0],"enjoy":votes[1],"regret":votes[2]}}

    def _require_v3(self, c: sqlite3.Connection) -> None:
        # Write-mode opens migrate; a read-only open of a not-yet-migrated file cannot.
        if self.readonly and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_stats'").fetchone() is None:
            raise sqlite3.OperationalError("consequences database predates schema v3; open it once in write mode (for example `bad-decisions consequences rebuild PATH`) to upgrade it")

    @staticmethod
    def _card_rows(c: sqlite3.Connection, role: str, limit: int) -> list[dict[str, Any]]:
        # Reads the content_stats cache instead of re-aggregating round_elements.
        # The top-N subquery runs first (its LIMIT plus the outer join keep SQLite
        # from flattening it), so provenance and variants are looked up only for
        # the rows returned. Provenance shown is the most recent draw; `variants`
        # counts distinct (pack, card) pairs that have carried this exact content.
        rows = c.execute("""
            SELECT top.content_hash, top.text, re.pack_id, re.card_id, re.source_ref,
                   top.draw_count, top.enjoy_count, top.regret_count,
                   (SELECT COUNT(*) FROM (SELECT DISTINCT v.pack_id, v.card_id FROM round_elements v WHERE v.content_hash = top.content_hash))
            FROM (SELECT s.content_hash, ct.text, s.draw_count, s.enjoy_count, s.regret_count
                  FROM content_stats s JOIN contents ct ON ct.content_hash = s.content_hash
                  WHERE ct.role = ? ORDER BY s.draw_count DESC, s.content_hash LIMIT ?) top
            LEFT JOIN round_elements re ON re.rowid = (
                SELECT e.rowid FROM round_elements e JOIN rounds r ON r.round_id = e.round_id
                WHERE e.content_hash = top.content_hash ORDER BY r.occurred_at DESC, e.rowid DESC LIMIT 1)
            ORDER BY top.draw_count DESC, top.content_hash""", (role, limit)).fetchall()
        return [{"hash": r[0], "text": r[1], "pack": r[2], "card": r[3], "source": r[4], "draws": r[5], "enjoy": r[6], "regret": r[7], "variants": r[8]} for r in rows]

    def dashboard_rows(self, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        """Return read-only drill-down data for the operator UI."""
        if limit < 1 or limit > 500: raise ValueError("limit must be between 1 and 500")
        with self._db() as c:
            self._require_v3(c)
            combinations = c.execute("""SELECT combination_hash, draw_count, enjoy_count, regret_count,
                CASE WHEN enjoy_count + regret_count = 0 THEN NULL ELSE CAST(enjoy_count - regret_count AS REAL) / (enjoy_count + regret_count) END
                FROM combination_stats ORDER BY draw_count DESC, combination_hash LIMIT ?""", (limit,)).fetchall()
            prompts = self._card_rows(c, "prompt", limit)
            answers = self._card_rows(c, "answer", limit)
            recent = c.execute("""SELECT r.occurred_at, r.round_id, r.combination_hash,
                COALESCE(f.enjoyed, -1), r.client_id IS NOT NULL
                FROM rounds r LEFT JOIN feedback f ON f.round_id=r.round_id
                ORDER BY r.occurred_at DESC LIMIT ?""", (limit,)).fetchall()
        return {
            "combinations": [{"hash": row[0], "draws": row[1], "enjoy": row[2], "regret": row[3], "score": row[4]} for row in combinations],
            "prompts": prompts,
            "answers": answers,
            "recent": [{"occurred_at": row[0], "round_id": row[1], "combination": row[2], "vote": None if row[3] < 0 else bool(row[3]), "identified": bool(row[4])} for row in recent],
        }

    # --- maintenance ------------------------------------------------------------

    def rebuild(self) -> None:
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try: self._rebuild(c); c.execute("COMMIT")
            except Exception: c.execute("ROLLBACK"); raise

    def purge(self, retention_days: int) -> int:
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                cutoff = self._now() - retention_days*86400
                count=c.execute("DELETE FROM rounds WHERE occurred_at < ?",(cutoff,)).rowcount
                c.execute("DELETE FROM request_events WHERE occurred_at < ?",(cutoff,))
                # Retention reaches the registries too: drop combinations no surviving
                # round uses (cascades to combination_answers/stats), then any content
                # nothing references anymore (cascades to content_stats).
                c.execute("DELETE FROM combinations WHERE NOT EXISTS (SELECT 1 FROM rounds r WHERE r.combination_hash = combinations.combination_hash)")
                c.execute("""DELETE FROM contents WHERE
                    NOT EXISTS (SELECT 1 FROM round_elements e WHERE e.content_hash = contents.content_hash)
                    AND NOT EXISTS (SELECT 1 FROM combinations co WHERE co.prompt_hash = contents.content_hash)
                    AND NOT EXISTS (SELECT 1 FROM combination_answers ca WHERE ca.content_hash = contents.content_hash)""")
                self._rebuild(c); c.execute("COMMIT"); return count
            except Exception: c.execute("ROLLBACK"); raise

    def _rebuild(self,c: sqlite3.Connection) -> None:
        now = self._now()
        c.execute("DELETE FROM combination_stats")
        c.execute("INSERT INTO combination_stats(combination_hash,draw_count,enjoy_count,regret_count,updated_at) SELECT r.combination_hash,COUNT(r.round_id),COALESCE(SUM(f.enjoyed=1),0),COALESCE(SUM(f.enjoyed=0),0),? FROM rounds r LEFT JOIN feedback f ON f.round_id=r.round_id GROUP BY r.combination_hash",(now,))
        c.execute("DELETE FROM content_stats")
        c.execute("""INSERT INTO content_stats(content_hash,draw_count,enjoy_count,regret_count,updated_at)
            SELECT e.content_hash, COUNT(*), COALESCE(SUM(f.enjoyed=1),0), COALESCE(SUM(f.enjoyed=0),0), ?
            FROM (SELECT DISTINCT round_id, content_hash FROM round_elements) e
            LEFT JOIN feedback f ON f.round_id = e.round_id
            GROUP BY e.content_hash""", (now,))
