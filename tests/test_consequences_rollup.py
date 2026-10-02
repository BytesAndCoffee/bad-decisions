"""Consequences schema v3: content registry, enforced FKs, per-card rollups, v2 migration."""
from __future__ import annotations

import json
import sqlite3

import pytest

from bad_decisions.consequences import ConsequencesStore, round_identity
from bad_decisions.engine import generate_random_round
from bad_decisions.models import Selection

# The registry fixture (tests/conftest.py) has packs "a" and "b":
#   a:b1 "One _" (1 slot); a:w1 "α", a:w2 "same"
#   b:b1 "Two _ _" (2 slots); b:w1 "same", b:w2 "β", b:w3 "γ"
# so a:w2 and b:w1 are two cards that share one answer content hash.


class Scripted:
    """RandomLike that deals exactly the requested cards, in order."""

    def __init__(self, prompt, answers):
        self.prompt, self.answers = prompt, answers

    def choice(self, seq):
        return self.prompt

    def sample(self, population, k):
        assert k == len(self.answers)
        return list(self.answers)


def card(registry, ref):
    pack_id, card_id = ref.split(":")
    pack = registry.packs[pack_id]
    return next(c for c in (*pack.prompts, *pack.answers) if c.id == card_id)


def deal(registry, prompt_ref, *answer_refs):
    prompt = card(registry, prompt_ref)
    answers = [card(registry, ref) for ref in answer_refs]
    selection = Selection(prompt_packs=(prompt.pack,), answer_packs=tuple(sorted({a.pack for a in answers})))
    return generate_random_round([prompt], answers, registry=registry, selection=selection, rng=Scripted(prompt, answers))


def record(store, round_, *, feedback=True):
    return store.record_round(round_, request_id=None, client_id=None, session_id=None, feedback_enabled=feedback)


def raw(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def query(path, sql, params=()):
    connection = raw(path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def snapshot(path, table, key):
    return query(path, f"SELECT {key},draw_count,enjoy_count,regret_count FROM {table} ORDER BY {key}")


@pytest.fixture
def database(tmp_path):
    return tmp_path / "consequences.sqlite3"


# --- fresh v3 databases ---------------------------------------------------------


def test_fresh_database_is_created_at_v3(database):
    ConsequencesStore(database)
    assert query(database, "SELECT version FROM schema_migrations ORDER BY version") == [(1,), (2,), (3,)]
    assert "content_text" not in {row[1] for row in query(database, "PRAGMA table_info(round_elements)")}
    indexes = {row[0] for row in query(database, "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"round_elements_content", "combinations_prompt", "combination_answers_content"} <= indexes


def test_draws_and_votes_roll_up_per_card(database, registry):
    store = ConsequencesStore(database)
    first = record(store, deal(registry, "a:b1", "a:w1"))
    record(store, deal(registry, "a:b1", "a:w2"))
    assert store.feedback(first.round_id, first.feedback_token, False)[0] == "ok"

    prompt_hash, (alpha_hash,), _ = round_identity(deal(registry, "a:b1", "a:w1"))
    prompt = store.content_stats(prompt_hash)
    assert (prompt["role"], prompt["draw_count"], prompt["enjoy_count"], prompt["regret_count"]) == ("prompt", 2, 0, 1)
    assert store.content_stats(alpha_hash)["score"] == -1.0
    assert store.content_stats("0" * 64) is None

    rows = store.dashboard_rows()
    assert [(row["text"], row["draws"], row["variants"]) for row in rows["prompts"]] == [("One _", 2, 1)]
    assert {(row["text"], row["regret"], row["variants"]) for row in rows["answers"]} == {("α", 1, 1), ("same", 0, 1)}


def test_dashboard_has_one_row_per_content_with_variants(database, registry):
    store = ConsequencesStore(database)
    record(store, deal(registry, "a:b1", "a:w2"))
    record(store, deal(registry, "a:b1", "b:w1"))  # same text, different pack and card

    (answer,) = store.dashboard_rows()["answers"]
    assert (answer["text"], answer["draws"], answer["variants"]) == ("same", 2, 2)
    # Provenance comes from the most recent draw.
    assert (answer["pack"], answer["card"]) == ("b", "w1")


def test_combination_answers_preserve_slot_order(database, registry):
    store = ConsequencesStore(database)
    forward = record(store, deal(registry, "b:b1", "b:w3", "b:w2"), feedback=False)
    backward = record(store, deal(registry, "b:b1", "b:w2", "b:w3"), feedback=False)
    assert forward.combination_hash != backward.combination_hash

    def slots(combo):
        return [text for (text,) in query(database, """SELECT ct.text FROM combination_answers ca
            JOIN contents ct ON ct.content_hash = ca.content_hash
            WHERE ca.combination_hash = ? ORDER BY ca.slot_index""", (combo,))]

    assert slots(forward.combination_hash) == ["γ", "β"]
    assert slots(backward.combination_hash) == ["β", "γ"]
    # answers_json is kept and agrees with the rows.
    (answers_json,) = query(database, "SELECT answers_json FROM combinations WHERE combination_hash=?", (forward.combination_hash,))[0]
    assert json.loads(answers_json) == round_identity(deal(registry, "b:b1", "b:w3", "b:w2"))[1]


def test_content_filling_two_slots_counts_once_per_round(database, registry):
    store = ConsequencesStore(database)
    round_ = deal(registry, "b:b1", "a:w2", "b:w1")  # both slots are "same"
    issued = record(store, round_)
    store.feedback(issued.round_id, issued.feedback_token, True)

    _, (same, same_again), _ = round_identity(round_)
    assert same == same_again
    stats = store.content_stats(same)
    assert (stats["draw_count"], stats["enjoy_count"], stats["regret_count"]) == (1, 1, 0)
    assert query(database, "SELECT COUNT(*) FROM combination_answers WHERE content_hash=?", (same,)) == [(2,)]


def test_incremental_stats_match_rebuild(database, registry):
    store = ConsequencesStore(database)
    rounds = [deal(registry, "a:b1", "a:w1"), deal(registry, "a:b1", "a:w2"), deal(registry, "a:b1", "a:w1"),
              deal(registry, "b:b1", "a:w2", "b:w1"), deal(registry, "b:b1", "b:w3", "b:w2")]
    issued = [record(store, round_) for round_ in rounds]
    votes = [(0, True), (1, False), (2, True), (2, False),  # changed vote
             (3, True), (4, False), (4, None)]              # withdrawn vote
    for index, value in votes:
        assert store.feedback(issued[index].round_id, issued[index].feedback_token, value)[0] == "ok"

    before = snapshot(database, "content_stats", "content_hash"), snapshot(database, "combination_stats", "combination_hash")
    store.rebuild()
    after = snapshot(database, "content_stats", "content_hash"), snapshot(database, "combination_stats", "combination_hash")
    assert before == after
    assert len(before[0]) == 6  # prompts "One _", "Two _ _"; answers α, same, β, γ
    assert sum(row[1] for row in before[1]) == len(rounds)


def test_content_foreign_keys_are_enforced(database, registry):
    store = ConsequencesStore(database)
    issued = record(store, deal(registry, "a:b1", "a:w1"), feedback=False)
    connection = raw(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("""INSERT INTO round_elements(round_id,role,slot_index,content_hash,pack_id,pack_version,card_id,source_ref,license_id,attribution,modifications_json)
                VALUES(?,'answer',9,'nope','a','1','w1',NULL,'l','a','[]')""", (issued.round_id,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO combinations VALUES('c','consequences.v1','nope','[]','{}',0)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO combination_answers VALUES(?,5,'nope')", (issued.combination_hash,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO content_stats VALUES('nope',1,0,0,0)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO contents VALUES('x','black',NULL,0)")
    finally:
        connection.close()


def test_purge_removes_orphaned_combinations_and_contents(database, registry):
    store = ConsequencesStore(database)
    record(store, deal(registry, "a:b1", "a:w1"))
    record(store, deal(registry, "b:b1", "b:w2", "b:w3"))
    connection = raw(database)
    with connection:
        connection.execute("UPDATE rounds SET occurred_at = occurred_at - 100*86400")
    connection.close()
    kept = record(store, deal(registry, "a:b1", "a:w2"))

    assert store.purge(30) == 2
    assert {text for (text,) in query(database, "SELECT text FROM contents")} == {"One _", "same"}
    assert query(database, "SELECT combination_hash FROM combinations") == [(kept.combination_hash,)]
    assert query(database, "SELECT COUNT(*) FROM combination_answers") == [(1,)]
    assert query(database, "SELECT COUNT(*) FROM content_stats") == [(2,)]
    assert query(database, "PRAGMA foreign_key_check") == []
    assert {row["text"] for row in store.dashboard_rows()["answers"]} == {"same"}


def test_store_closes_every_connection(database, registry, monkeypatch):
    store = ConsequencesStore(database)
    opened = []
    connect = store._connect
    monkeypatch.setattr(store, "_connect", lambda: opened.append(connect()) or opened[-1])

    issued = record(store, deal(registry, "a:b1", "a:w1"))
    store.feedback(issued.round_id, issued.feedback_token, True)
    store.stats(issued.combination_hash); store.report(); store.dashboard_rows(); store.rebuild(); store.purge(30)
    assert len(opened) == 7
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


# --- migration from the pre-v3 schema --------------------------------------------

# The v2 schema exactly as the 2.3.0 store created it.
V2_DDL = """
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
INSERT OR IGNORE INTO schema_migrations VALUES (2);
"""
# v1 had no per-draw text snapshot.
V1_DDL = V2_DDL.replace(" content_text TEXT,", "").replace("INSERT OR IGNORE INTO schema_migrations VALUES (2);", "")


def legacy_record(connection, round_, round_id, *, occurred_at=1_700_000_000, vote=None, with_text=True):
    """Write one round the way the 2.3.0 store's record_round and feedback did."""
    prompt, answers, combo = round_identity(round_)
    packs = round_.provenance
    connection.execute("INSERT OR IGNORE INTO combinations VALUES(?,?,?,?,?,?)",
                       (combo, "consequences.v1", prompt, json.dumps(answers), json.dumps({"template": round_.prompt.template}), occurred_at))
    connection.execute("INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?)", (round_id, occurred_at, None, combo, None, None, occurred_at + 604800, b""))
    cards = [("prompt", 0, prompt, round_.prompt.text, round_.prompt)] + [
        ("answer", i, digest, c.text, c) for i, (digest, c) in enumerate(zip(answers, round_.answers))]
    for role, slot, digest, text, c in cards:
        values = [round_id, role, slot, digest] + ([text] if with_text else []) + [
            c.pack, packs[c.pack].version, c.id, c.source_ref, packs[c.pack].license_id, packs[c.pack].attribution, "[]"]
        connection.execute(f"INSERT INTO round_elements VALUES({','.join('?' * len(values))})", values)
    connection.execute("""INSERT INTO combination_stats(combination_hash,draw_count,updated_at) VALUES(?,1,?)
        ON CONFLICT(combination_hash) DO UPDATE SET draw_count=draw_count+1""", (combo, occurred_at))
    if vote is not None:
        connection.execute("INSERT INTO feedback VALUES(?,?,?,?)", (round_id, int(vote), occurred_at, occurred_at))
        column = "enjoy_count" if vote else "regret_count"
        connection.execute(f"UPDATE combination_stats SET {column}={column}+1 WHERE combination_hash=?", (combo,))
    return combo


def legacy_database(path, ddl, rounds, *, with_text=True):
    connection = raw(path)
    try:
        connection.executescript(ddl)
        combos = [legacy_record(connection, round_, f"r{i}", vote=vote, with_text=with_text) for i, (round_, vote) in enumerate(rounds)]
        connection.commit()
    finally:
        connection.close()
    return combos


def test_v2_database_migrates_in_place(database, registry):
    reversed_pair = deal(registry, "b:b1", "b:w3", "b:w2")
    single = deal(registry, "a:b1", "a:w1")
    combos = legacy_database(database, V2_DDL, [(reversed_pair, False), (single, True), (single, None)])
    legacy_combination_stats = snapshot(database, "combination_stats", "combination_hash")

    store = ConsequencesStore(database)  # write-mode open runs the migration

    assert query(database, "SELECT MAX(version) FROM schema_migrations") == [(3,)]
    assert query(database, "PRAGMA foreign_key_check") == []
    assert "content_text" not in {row[1] for row in query(database, "PRAGMA table_info(round_elements)")}
    assert query(database, "SELECT COUNT(*) FROM round_elements") == [(7,)]
    assert ("contents",) in query(database, "SELECT \"table\" FROM pragma_foreign_key_list('combinations')")
    assert ("contents",) in query(database, "SELECT \"table\" FROM pragma_foreign_key_list('round_elements')")
    # Card text moved into contents, once per hash.
    assert sorted(text for (text,) in query(database, "SELECT text FROM contents")) == sorted(["Two _ _", "γ", "β", "One _", "α"])
    assert [text for (text,) in query(database, """SELECT ct.text FROM combination_answers ca
        JOIN contents ct ON ct.content_hash = ca.content_hash WHERE ca.combination_hash=? ORDER BY ca.slot_index""", (combos[0],))] == ["γ", "β"]
    assert snapshot(database, "combination_stats", "combination_hash") == legacy_combination_stats

    # Existing hashes stay valid: new draws and votes land on the migrated rows.
    issued = record(store, single)
    assert issued.combination_hash == combos[1]
    store.feedback(issued.round_id, issued.feedback_token, True)
    _, (alpha,), _ = round_identity(single)
    stats = store.content_stats(alpha)
    assert (stats["draw_count"], stats["enjoy_count"]) == (3, 2)
    assert store.stats(combos[1])["draw_count"] == 3

    before = snapshot(database, "content_stats", "content_hash")
    store.rebuild()
    assert snapshot(database, "content_stats", "content_hash") == before


def test_v1_database_migrates_and_backfills_text_on_next_draw(database, registry):
    round_ = deal(registry, "a:b1", "a:w1")
    legacy_database(database, V1_DDL, [(round_, None)], with_text=False)

    store = ConsequencesStore(database)
    assert query(database, "SELECT MAX(version) FROM schema_migrations") == [(3,)]
    assert query(database, "SELECT text FROM contents") == [(None,), (None,)]

    record(store, round_)
    assert sorted(text for (text,) in query(database, "SELECT text FROM contents")) == ["One _", "α"]


def test_migration_is_idempotent(database, registry):
    legacy_database(database, V2_DDL, [(deal(registry, "a:b1", "a:w1"), True)])
    ConsequencesStore(database)
    state = (query(database, "SELECT * FROM contents ORDER BY content_hash"),
             snapshot(database, "content_stats", "content_hash"),
             query(database, "SELECT sql FROM sqlite_master ORDER BY name"))
    ConsequencesStore(database)
    assert query(database, "SELECT version FROM schema_migrations ORDER BY version") == [(1,), (2,), (3,)]
    assert (query(database, "SELECT * FROM contents ORDER BY content_hash"),
            snapshot(database, "content_stats", "content_hash"),
            query(database, "SELECT sql FROM sqlite_master ORDER BY name")) == state


def test_migration_aborts_on_dangling_references(database, registry):
    legacy_database(database, V2_DDL, [(deal(registry, "a:b1", "a:w1"), None)])
    connection = sqlite3.connect(database)  # foreign_keys OFF, as a damaged file might have been written
    with connection:
        connection.execute("INSERT INTO rounds VALUES('ghost',0,NULL,'missing-combination',NULL,NULL,0,x'')")
    connection.close()

    with pytest.raises(RuntimeError, match="dangling"):
        ConsequencesStore(database)
    # Rolled back as one transaction: still a v2 file.
    assert query(database, "SELECT MAX(version) FROM schema_migrations") == [(2,)]
    assert "content_text" in {row[1] for row in query(database, "PRAGMA table_info(round_elements)")}
    assert query(database, "SELECT name FROM sqlite_master WHERE name IN ('contents','content_stats')") == []


def test_readonly_open_of_v2_database_reports_but_needs_upgrade_for_rollups(database, registry):
    legacy_database(database, V2_DDL, [(deal(registry, "a:b1", "a:w1"), None)])
    readonly = ConsequencesStore(database, readonly=True)
    assert readonly.report()["draws"]["recorded"] == 1
    with pytest.raises(sqlite3.OperationalError, match="schema v3"):
        readonly.dashboard_rows()

    ConsequencesStore(database)
    assert len(readonly.dashboard_rows()["answers"]) == 1
