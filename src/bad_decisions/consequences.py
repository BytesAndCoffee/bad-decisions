"""Consequences: optional durable local analytics and feedback."""
from __future__ import annotations
import hashlib, hmac, json, secrets, sqlite3, time, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .models import Round

HASH_SCHEMA = "consequences.v1"

def canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

def round_identity(round_: Round) -> tuple[str, list[str], str]:
    prompt = canonical_hash({"schema":"consequences.prompt.v1","text":round_.black.repr,"template":round_.black.template,"pick":round_.black.slots})
    answers = [canonical_hash({"schema":"consequences.answer.v1","text":card.text}) for card in round_.white]
    combination = canonical_hash({"schema":"consequences.combination.v1","prompt":prompt,"answers":answers,"rendering":{"template":round_.black.template}})
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

    def _initialize(self) -> None:
        with self._connect() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_migrations VALUES (1);
CREATE TABLE IF NOT EXISTS request_events(request_id TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL, route TEXT NOT NULL, method TEXT NOT NULL, status INTEGER NOT NULL, duration_ms REAL NOT NULL, client_id TEXT, session_id TEXT, round_id TEXT, error_category TEXT);
CREATE INDEX IF NOT EXISTS request_events_time ON request_events(occurred_at);
CREATE TABLE IF NOT EXISTS combinations(combination_hash TEXT PRIMARY KEY, hash_schema TEXT NOT NULL, prompt_hash TEXT NOT NULL, answers_json TEXT NOT NULL, rendering_json TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rounds(round_id TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL, request_id TEXT, combination_hash TEXT NOT NULL REFERENCES combinations(combination_hash), client_id TEXT, session_id TEXT, feedback_expires_at INTEGER NOT NULL, token_verifier BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS rounds_time ON rounds(occurred_at);
CREATE INDEX IF NOT EXISTS rounds_combination ON rounds(combination_hash);
CREATE TABLE IF NOT EXISTS round_elements(round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ("prompt","answer")), slot_index INTEGER NOT NULL, content_hash TEXT NOT NULL, content_text TEXT, pack_id TEXT NOT NULL, pack_version TEXT NOT NULL, card_id TEXT NOT NULL, source_ref TEXT, license_id TEXT NOT NULL, attribution TEXT NOT NULL, modifications_json TEXT NOT NULL, PRIMARY KEY(round_id,role,slot_index));
CREATE TABLE IF NOT EXISTS feedback(round_id TEXT PRIMARY KEY REFERENCES rounds(round_id) ON DELETE CASCADE, enjoyed INTEGER NOT NULL CHECK(enjoyed IN (0,1)), created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS combination_stats(combination_hash TEXT PRIMARY KEY REFERENCES combinations(combination_hash) ON DELETE CASCADE, draw_count INTEGER NOT NULL CHECK(draw_count >= 0), enjoy_count INTEGER NOT NULL DEFAULT 0 CHECK(enjoy_count >= 0), regret_count INTEGER NOT NULL DEFAULT 0 CHECK(regret_count >= 0), updated_at INTEGER NOT NULL);
""")
            columns = {row[1] for row in c.execute("PRAGMA table_info(round_elements)")}
            if "content_text" not in columns:
                c.execute("ALTER TABLE round_elements ADD COLUMN content_text TEXT")
            c.execute("INSERT OR IGNORE INTO schema_migrations VALUES (2)")

    @staticmethod
    def _now() -> int: return int(time.time())
    @staticmethod
    def _iso(value: int) -> str: return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")

    def record_round(self, round_: Round, *, request_id: str | None, client_id: str | None, session_id: str | None, feedback_enabled: bool) -> IssuedRound:
        now, round_id = self._now(), str(uuid.uuid4())
        prompt, answers, combo = round_identity(round_)
        token = secrets.token_urlsafe(32) if feedback_enabled else None
        verifier = hashlib.sha256(token.encode()).digest() if token else b""
        packs = round_.provenance
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("INSERT OR IGNORE INTO combinations VALUES(?,?,?,?,?,?)", (combo,HASH_SCHEMA,prompt,json.dumps(answers),json.dumps({"template":round_.black.template}),now))
                c.execute("INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?)", (round_id,now,request_id,combo,client_id,session_id,now+self.feedback_ttl_seconds,verifier))
                cards = [("prompt",0,prompt,round_.black.repr,round_.black)] + [("answer",i,d,card.text,card) for i,(d,card) in enumerate(zip(answers,round_.white))]
                c.executemany("INSERT INTO round_elements VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [(round_id,role,slot,digest,text,card.pack,packs[card.pack].version,card.id,card.source_ref,packs[card.pack].license_id,packs[card.pack].attribution,"[]") for role,slot,digest,text,card in cards])
                c.execute("INSERT INTO combination_stats(combination_hash,draw_count,updated_at) VALUES(?,1,?) ON CONFLICT(combination_hash) DO UPDATE SET draw_count=draw_count+1,updated_at=excluded.updated_at", (combo,now))
                if request_id: c.execute("UPDATE request_events SET round_id=? WHERE request_id=?", (round_id,request_id))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK"); raise
        return IssuedRound(round_id,combo,token,self._iso(now+self.feedback_ttl_seconds) if token else None)

    def record_request(self, *, request_id: str, route: str, method: str, status: int, duration_ms: float, client_id: str | None, session_id: str | None) -> None:
        with self._connect() as c:
            c.execute("INSERT OR IGNORE INTO request_events VALUES(?,?,?,?,?,?,?,?,NULL,?)", (request_id,self._now(),route[:160],method[:12],status,min(duration_ms,600000),client_id,session_id,None if status < 400 else f"http_{status//100}xx"))

    def link_request(self, request_id: str, round_id: str) -> None:
        with self._connect() as c: c.execute("UPDATE request_events SET round_id=? WHERE request_id=?", (round_id, request_id))

    def feedback(self, round_id: str, token: str, enjoyed: bool | None) -> tuple[str, bool, bool | None, bool]:
        if not token or len(token) > 512: return "missing",False,None,False
        now = self._now()
        with self._connect() as c:
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
                    c.execute("UPDATE combination_stats SET enjoy_count=enjoy_count+?,regret_count=regret_count+?,updated_at=? WHERE combination_hash=?", ((enjoyed is True)-(old is True),(enjoyed is False)-(old is False),now,row[0]))
                c.execute("COMMIT"); return "ok",changed,enjoyed,created
            except Exception:
                c.execute("ROLLBACK"); raise

    def stats(self, combo: str) -> dict[str,Any] | None:
        with self._connect() as c: row=c.execute("SELECT draw_count,enjoy_count,regret_count FROM combination_stats WHERE combination_hash=?",(combo,)).fetchone()
        if row is None: return None
        draws,enjoy,regret=row; votes=enjoy+regret
        return {"combination_hash":combo,"draw_count":draws,"enjoy_count":enjoy,"regret_count":regret,"vote_count":votes,"score":None if not votes else (enjoy-regret)/votes}

    def report(self) -> dict[str,Any]:
        with self._connect() as c:
            requests=c.execute("SELECT COUNT(*),COALESCE(AVG(duration_ms),0),COALESCE(SUM(status>=400),0) FROM request_events").fetchone()
            draws=c.execute("SELECT COUNT(*),COUNT(DISTINCT combination_hash) FROM rounds").fetchone()
            votes=c.execute("SELECT COUNT(*),COALESCE(SUM(enjoyed=1),0),COALESCE(SUM(enjoyed=0),0) FROM feedback").fetchone()
        return {"schema_version":1,"requests":{"count":requests[0],"average_duration_ms":round(requests[1],2),"errors":requests[2]},"draws":{"recorded":draws[0],"combinations":draws[1]},"feedback":{"votes":votes[0],"enjoy":votes[1],"regret":votes[2]}}

    def dashboard_rows(self, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        """Return read-only drill-down data for the operator UI."""
        if limit < 1 or limit > 500: raise ValueError("limit must be between 1 and 500")
        with self._connect() as c:
            combinations = c.execute("""SELECT combination_hash, draw_count, enjoy_count, regret_count,
                CASE WHEN enjoy_count + regret_count = 0 THEN NULL ELSE CAST(enjoy_count - regret_count AS REAL) / (enjoy_count + regret_count) END
                FROM combination_stats ORDER BY draw_count DESC, combination_hash LIMIT ?""", (limit,)).fetchall()
            prompts = c.execute("""SELECT re.content_hash, re.content_text, re.pack_id, re.card_id, re.source_ref,
                COUNT(r.round_id), COALESCE(SUM(f.enjoyed=1),0), COALESCE(SUM(f.enjoyed=0),0)
                FROM round_elements re JOIN rounds r ON r.round_id=re.round_id LEFT JOIN feedback f ON f.round_id=r.round_id
                WHERE re.role='prompt' GROUP BY re.content_hash, re.pack_id, re.card_id, re.source_ref
                ORDER BY COUNT(r.round_id) DESC LIMIT ?""", (limit,)).fetchall()
            answers = c.execute("""SELECT re.content_hash, re.content_text, re.pack_id, re.card_id, re.source_ref,
                COUNT(DISTINCT r.round_id), COALESCE(SUM(f.enjoyed=1),0), COALESCE(SUM(f.enjoyed=0),0)
                FROM round_elements re JOIN rounds r ON r.round_id=re.round_id LEFT JOIN feedback f ON f.round_id=r.round_id
                WHERE re.role='answer' GROUP BY re.content_hash, re.pack_id, re.card_id, re.source_ref
                ORDER BY COUNT(DISTINCT r.round_id) DESC LIMIT ?""", (limit,)).fetchall()
            recent = c.execute("""SELECT r.occurred_at, r.round_id, r.combination_hash,
                COALESCE(f.enjoyed, -1), r.client_id IS NOT NULL
                FROM rounds r LEFT JOIN feedback f ON f.round_id=r.round_id
                ORDER BY r.occurred_at DESC LIMIT ?""", (limit,)).fetchall()
        return {
            "combinations": [{"hash": row[0], "draws": row[1], "enjoy": row[2], "regret": row[3], "score": row[4]} for row in combinations],
            "prompts": [{"hash": row[0], "text": row[1], "pack": row[2], "card": row[3], "source": row[4], "draws": row[5], "enjoy": row[6], "regret": row[7]} for row in prompts],
            "answers": [{"hash": row[0], "text": row[1], "pack": row[2], "card": row[3], "source": row[4], "draws": row[5], "enjoy": row[6], "regret": row[7]} for row in answers],
            "recent": [{"occurred_at": row[0], "round_id": row[1], "combination": row[2], "vote": None if row[3] < 0 else bool(row[3]), "identified": bool(row[4])} for row in recent],
        }

    def rebuild(self) -> None:
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try: self._rebuild(c); c.execute("COMMIT")
            except Exception: c.execute("ROLLBACK"); raise

    def purge(self, retention_days: int) -> int:
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                count=c.execute("DELETE FROM rounds WHERE occurred_at < ?",(self._now()-retention_days*86400,)).rowcount
                c.execute("DELETE FROM request_events WHERE occurred_at < ?",(self._now()-retention_days*86400,))
                self._rebuild(c); c.execute("COMMIT"); return count
            except Exception: c.execute("ROLLBACK"); raise

    def _rebuild(self,c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM combination_stats")
        c.execute("INSERT INTO combination_stats(combination_hash,draw_count,enjoy_count,regret_count,updated_at) SELECT r.combination_hash,COUNT(r.round_id),COALESCE(SUM(f.enjoyed=1),0),COALESCE(SUM(f.enjoyed=0),0),? FROM rounds r LEFT JOIN feedback f ON f.round_id=r.round_id GROUP BY r.combination_hash",(self._now(),))
