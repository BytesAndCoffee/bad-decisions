from __future__ import annotations

import itertools
import os
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType

import pytest

from bad_decisions.api import create_app
from bad_decisions.models import Prompt, Answer
from bad_decisions.packs import Registry
from bad_decisions.peer_pressure import PeerPressureError, PeerPressureService
from bad_decisions.peer_pressure import service as service_module
from conftest import make_pack
from fastapi.testclient import TestClient


def multiplayer_registry() -> Registry:
    pack = make_pack(
        "party",
        prompts=tuple(
            Prompt(id=f"q{index}", text=f"Question {index}: _", template=f"Question {index}: {{}}", slots=1, pack="party")
            for index in range(4)
        ),
        answers=tuple(Answer(id=f"r{index}", text=f"Response {index}", pack="party") for index in range(40)),
    )
    return Registry(MappingProxyType({"party": pack}))


def selectable_registry() -> Registry:
    packs = {}
    for pack_id in ("regular", "custom"):
        packs[pack_id] = make_pack(
            pack_id,
            prompts=tuple(
                Prompt(id=f"q{index}", text=f"{pack_id} question {index}: _", template=f"{pack_id} question {index}: {{}}", slots=1, pack=pack_id)
                for index in range(4)
            ),
            answers=tuple(Answer(id=f"r{index}", text=f"{pack_id} response {index}", pack=pack_id) for index in range(40)),
        )
    return Registry(MappingProxyType(packs))


def join_table(service: PeerPressureService, room: str = "ohno"):
    alice = service.join(room, "Alice", create=True)
    bob = service.join(room, "Bob")
    carol = service.join(room, "Carol")
    return alice, bob, carol


def credentials(joined):
    return joined["player_id"], joined["session_token"]


def request(index: int) -> str:
    return f"req_{index:032x}"


def test_rooms_are_isolated_ephemeral_databases(tmp_path):
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3)
    first = service.join("first", "Alice", create=True)
    second = service.join("second", "Bob", create=True)
    assert (tmp_path / "first.sqlite3").is_file()
    assert (tmp_path / "second.sqlite3").is_file()
    assert service.sync("first", *credentials(first))["state"]["players"][0]["name"] == "Alice"
    assert service.sync("second", *credentials(second))["state"]["players"][0]["name"] == "Bob"
    assert "session_token" not in service.sync("first", *credentials(first))["state"]


def test_room_pack_selection_is_immutable_and_limits_every_draw(tmp_path):
    service = PeerPressureService(tmp_path, selectable_registry(), hand_size=3)
    alice = service.join("chosen", "Alice", create=True, packs=["custom"])
    bob = service.join("chosen", "Bob", create=True, packs=["regular"])
    carol = service.join("chosen", "Carol", packs=["regular"])
    assert alice["state"]["room"]["packs"] == ["custom"]
    assert bob["state"]["room"]["packs"] == ["custom"]
    started = service.start("chosen", *credentials(alice), request(900), carol["revision"])
    state = service.sync("chosen", *credentials(bob))["state"]
    assert started["revision"] == state["room"]["revision"]
    assert state["prompt"]["pack"] == "custom"
    assert {card["pack"] for card in state["you"]["hand"]} == {"custom"}


def test_selection_pool_cache_is_bounded(tmp_path, monkeypatch):
    from bad_decisions.peer_pressure import service as service_module

    monkeypatch.setattr(service_module, "SELECTION_POOL_CACHE_SIZE", 1)
    service = PeerPressureService(tmp_path, selectable_registry(), hand_size=3)
    for room, packs in (("first", ["regular"]), ("second", ["custom"])):
        players = [service.join(room, name, create=True, packs=packs) for name in ("Alice", "Bob", "Carol")]
        service.start(room, *credentials(players[0]), request(700 + len(room)), players[-1]["revision"])
        state = service.sync(room, *credentials(players[1]))["state"]
        assert {card["pack"] for card in state["you"]["hand"]} == set(packs)  # uncached selections still draw correctly
    assert len(service._selection_pools) == 1


def test_room_pack_selection_defaults_to_all_and_rejects_invalid_values(tmp_path):
    service = PeerPressureService(tmp_path, selectable_registry(), hand_size=3)
    joined = service.join("default", "Alice", create=True)
    assert joined["state"]["room"]["packs"] == ["regular", "custom"]
    for room, packs, reason in (
        ("empty", [], "invalid_selector"),
        ("duplicate", ["regular", "regular"], "invalid_selector"),
        ("unknown", ["missing"], "unknown_pack"),
    ):
        with pytest.raises(PeerPressureError) as refused:
            service.create_room(room, packs)
        assert refused.value.reason == reason


def test_private_hands_anonymous_judging_and_idempotent_score(tmp_path):
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3)
    alice, bob, carol = join_table(service)
    alice_id, alice_token = credentials(alice)
    bob_id, bob_token = credentials(bob)
    carol_id, carol_token = credentials(carol)

    current = service.sync("ohno", alice_id, alice_token)["revision"]
    start = service.start("ohno", alice_id, alice_token, request(1), current)
    alice_state = service.sync("ohno", alice_id, alice_token)["state"]
    bob_state = service.sync("ohno", bob_id, bob_token)["state"]
    carol_state = service.sync("ohno", carol_id, carol_token)["state"]
    assert alice_state["responsible_adult"]["name"] == "Alice"
    assert alice_state["you"]["hand"] != bob_state["you"]["hand"]
    assert all("hand" not in player for player in alice_state["players"])

    bob_card = bob_state["you"]["hand"][0]["card_instance_id"]
    submitted = service.submit("ohno", bob_id, bob_token, request(2), start["revision"], [bob_card])
    carol_card = carol_state["you"]["hand"][0]["card_instance_id"]
    ready = service.submit("ohno", carol_id, carol_token, request(3), submitted["revision"], [carol_card])

    assert service.sync("ohno", bob_id, bob_token)["state"]["judging"] is None
    judging = service.sync("ohno", alice_id, alice_token)["state"]["judging"]
    assert len(judging["decisions"]) == 2
    assert all(set(decision) == {"submission_id", "answers"} for decision in judging["decisions"])
    winning = judging["decisions"][0]["submission_id"]
    judged = service.judge("ohno", alice_id, alice_token, request(4), ready["revision"], winning)
    retried = service.judge("ohno", alice_id, alice_token, request(4), ready["revision"], winning)
    assert retried == judged
    result = service.sync("ohno", alice_id, alice_token)["state"]
    assert result["result"]["winning_player"]["name"] in {"Bob", "Carol"}
    assert sum(player["score"] for player in result["players"]) == 1
    advanced = service.advance("ohno", alice_id, alice_token, request(5), judged["revision"])
    next_round = service.sync("ohno", bob_id, bob_token)["state"]
    assert advanced["revision"] == next_round["room"]["revision"]
    assert next_round["room"]["round"] == 2
    assert next_round["responsible_adult"]["name"] == "Bob"
    assert len(next_round["you"]["hand"]) == 3


def test_validation_revision_reconnect_and_cleanup(tmp_path):
    clock = [1_000]
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3, room_ttl_seconds=10, now=lambda: clock[0])
    alice, bob, carol = join_table(service)
    alice_id, alice_token = credentials(alice)
    bob_id, bob_token = credentials(bob)
    current = service.sync("ohno", alice_id, alice_token)["revision"]
    started = service.start("ohno", alice_id, alice_token, request(10), current)

    with pytest.raises(PeerPressureError, match="Responsible Adult") as denied:
        service.submit("ohno", alice_id, alice_token, request(11), started["revision"], [])
    assert denied.value.reason == "responsible_adult_cannot_submit"
    bob_state = service.sync("ohno", bob_id, bob_token)["state"]
    with pytest.raises(PeerPressureError) as count_error:
        service.submit("ohno", bob_id, bob_token, request(14), started["revision"], [])
    assert count_error.value.reason == "wrong_card_count"
    with pytest.raises(PeerPressureError) as foreign:
        service.submit("ohno", bob_id, bob_token, request(12), started["revision"], ["card_not_owned"])
    assert foreign.value.reason == "card_not_in_hand"
    with pytest.raises(PeerPressureError) as stale:
        service.leave("ohno", bob_id, bob_token, request(13), 0)
    assert stale.value.reason == "stale_revision" and stale.value.resync

    reconnected = service.join("ohno", "Bob", player_id=bob_id, session_token=bob_token)
    assert reconnected["player_id"] == bob_id
    assert reconnected["state"]["you"]["hand"] == bob_state["you"]["hand"]
    with pytest.raises(PeerPressureError) as hijack:
        service.join("ohno", "Bob", player_id=bob_id, session_token="wrong")
    assert hijack.value.reason == "invalid_session"

    clock[0] += 11
    assert service.cleanup_expired() == 1
    assert not (tmp_path / "ohno.sqlite3").exists()
    with pytest.raises(PeerPressureError) as expired:
        service.sync("ohno", alice_id, alice_token)
    assert expired.value.reason == "room_not_found"


def test_only_room_owner_can_end_and_end_deletes_database(tmp_path):
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3)
    alice, bob, _ = join_table(service)
    current = service.sync("ohno", *credentials(bob))["revision"]
    with pytest.raises(PeerPressureError) as denied:
        service.end("ohno", *credentials(bob), request(20), current)
    assert denied.value.reason == "not_room_owner"
    current = service.sync("ohno", *credentials(alice))["revision"]
    service.end("ohno", *credentials(alice), request(21), current)
    assert not (tmp_path / "ohno.sqlite3").exists()


def test_missed_heartbeats_mark_away_without_losing_room_state(tmp_path):
    clock = [1_000]
    service = PeerPressureService(
        tmp_path, multiplayer_registry(), hand_size=3, disconnect_timeout_seconds=30, now=lambda: clock[0]
    )
    alice, bob, _ = join_table(service)
    current = service.sync("ohno", *credentials(alice))["revision"]
    started = service.start("ohno", *credentials(alice), request(25), current)
    bob_before = service.sync("ohno", *credentials(bob))["state"]["you"]["hand"]
    clock[0] += 31
    heartbeat = service.heartbeat("ohno", *credentials(alice), started["revision"])
    assert heartbeat == {"revision": started["revision"] + 1, "resync": True}
    state = service.sync("ohno", *credentials(alice))["state"]
    assert next(player for player in state["players"] if player["name"] == "Bob")["connected"] is False
    reconnected = service.join("ohno", "Bob", session_token=bob["session_token"])  # the token alone identifies Bob
    assert reconnected["state"]["you"]["hand"] == bob_before


def test_concurrent_mutations_cannot_commit_the_same_revision(tmp_path):
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3)
    alice, bob, carol = join_table(service)
    current = service.sync("ohno", *credentials(alice))["revision"]
    started = service.start("ohno", *credentials(alice), request(30), current)
    bob_state = service.sync("ohno", *credentials(bob))["state"]
    carol_state = service.sync("ohno", *credentials(carol))["state"]

    def submit(joined, card, request_id):
        return service.submit("ohno", *credentials(joined), request_id, started["revision"], [card])

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(submit, bob, bob_state["you"]["hand"][0]["card_instance_id"], request(31)),
            executor.submit(submit, carol, carol_state["you"]["hand"][0]["card_instance_id"], request(32)),
        ]
    outcomes = []
    for future in futures:
        try:
            outcomes.append("ok" if "revision" in future.result() else "?")
        except PeerPressureError as exc:
            outcomes.append(exc.reason)
    assert sorted(outcomes) == ["ok", "stale_revision"]


def test_http_protocol_uses_responsible_adult_and_protects_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        joined = [
            client.post("/v2/peer-pressure/rooms/tabletop/join", json={"display_name": name, "create": True}).json()
            for name in ("Alice", "Bob", "Carol")
        ]
        assert all(set(item) >= {"player_id", "session_token", "revision", "state"} for item in joined)
        alice = joined[0]
        headers = {"Authorization": f"Bearer {alice['session_token']}"}
        denied = client.get("/v2/peer-pressure/rooms/tabletop/state")
        assert denied.status_code == 401 and denied.json()["error"]["code"] == "invalid_session"
        started = client.post(
            "/v2/peer-pressure/rooms/tabletop/start",
            headers=headers,
            json={"request_id": request(40), "revision": joined[-1]["revision"]},
        )
        assert started.status_code == 200 and started.json()["revision"] == joined[-1]["revision"] + 1
        state = client.get("/v2/peer-pressure/rooms/tabletop/state", headers=headers).json()
        assert state["responsible_adult"]["name"] == "Alice"
        assert all("hand" not in player for player in state["players"])
        schema = client.get("/openapi.json").text.lower()
        assert "card czar" not in schema and "choose_peer_consequence" in schema


def test_http_room_creation_accepts_and_exposes_pack_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        joined = [
            client.post(
                "/v2/peer-pressure/rooms/selected/join",
                json={"display_name": name, "create": True, "packs": ["maha"]},
            ).json()
            for name in ("Alice", "Bob", "Carol")
        ]
        assert all(item["state"]["room"]["packs"] == ["maha"] for item in joined)
        headers = {"Authorization": f"Bearer {joined[0]['session_token']}"}
        started = client.post(
            "/v2/peer-pressure/rooms/selected/start",
            headers=headers,
            json={"request_id": request(41), "revision": joined[-1]["revision"]},
        )
        assert started.status_code == 200
        state = client.post(
            "/v2/peer-pressure/rooms/selected/sync",
            headers={"Authorization": f"Bearer {joined[1]['session_token']}"},
        ).json()["state"]
        assert state["prompt"]["pack"] == "maha"
        assert {card["pack"] for card in state["you"]["hand"]} == {"maha"}

        invalid = client.post(
            "/v2/peer-pressure/rooms/nope/join",
            json={"display_name": "Alice", "create": True, "packs": ["missing"]},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "unknown_pack"


# --- departures, streaming draws, and room creation -------------------------


class Table:
    """Alice (seat 0, first Responsible Adult), Bob, and Carol at one room, on a fake clock."""

    def __init__(self, tmp_path, names=("Alice", "Bob", "Carol"), **options):
        self.clock = [1_000]
        self.service = PeerPressureService(
            tmp_path, multiplayer_registry(), hand_size=3, disconnect_timeout_seconds=30, now=lambda: self.clock[0], **options
        )
        self.players = {}
        for index, name in enumerate(names):
            self.players[name] = self.service.join("ohno", name, create=index == 0)
        self.requests = itertools.count(1000)

    def creds(self, name):
        return credentials(self.players[name])

    def state(self, name):
        return self.service.sync("ohno", *self.creds(name))["state"]

    def act(self, name, action, **values):
        revision = self.state(name)["room"]["revision"]
        return getattr(self.service, action)("ohno", *self.creds(name), request(next(self.requests)), revision, **values)

    def submit(self, name):
        hand = self.state(name)["you"]["hand"]
        return self.act(name, "submit", cards=[hand[0]["card_instance_id"]])

    def pass_time(self, seconds, *, active=()):
        """Advance the clock while only ``active`` players keep heartbeating."""
        self.clock[0] += seconds
        for name in active:
            self.service.heartbeat("ohno", *self.creds(name), 0)

    def adult(self, name="Bob"):
        return self.state(name)["responsible_adult"]["name"]


def test_vanished_responsible_adult_passes_the_role_and_the_round_continues(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    table.submit("Bob")
    table.submit("Carol")
    assert table.state("Bob")["room"]["state"] == "JUDGING"
    bob_hand = len(table.state("Bob")["you"]["hand"])

    table.pass_time(31, active=("Bob", "Carol"))  # Alice stops heartbeating

    bob = table.state("Bob")
    assert bob["responsible_adult"]["name"] == "Bob"
    assert bob["room"]["state"] == "JUDGING"
    # Bob now judges, so his own decision went back to his hand instead of being judged by him.
    assert len(bob["you"]["hand"]) == bob_hand + 1 and bob["you"]["submitted"] is False
    decisions = bob["judging"]["decisions"]
    assert len(decisions) == 1
    table.act("Bob", "judge", submission_id=decisions[0]["submission_id"])
    result = table.state("Carol")
    assert result["result"]["winning_player"]["name"] == "Carol"
    table.act("Bob", "advance")
    assert table.adult() == "Carol"  # rotation continues from the new Responsible Adult


def test_responsible_adult_leaving_before_anyone_submits_hands_over_the_round(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    table.act("Alice", "leave")
    assert table.adult() == "Bob"
    table.submit("Carol")
    bob = table.state("Bob")
    assert bob["room"]["state"] == "JUDGING"
    table.act("Bob", "judge", submission_id=bob["judging"]["decisions"][0]["submission_id"])
    assert table.state("Carol")["room"]["state"] == "ROUND_RESULT"


def test_successor_whose_decision_was_the_only_one_reopens_the_round(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    table.submit("Bob")
    table.act("Carol", "leave")
    assert table.state("Bob")["room"]["state"] == "JUDGING"  # Bob's is the only decision
    table.act("Alice", "leave")

    bob = table.state("Bob")
    assert bob["responsible_adult"]["name"] == "Bob"
    assert bob["room"]["state"] == "PLAYING" and bob["judging"] is None
    table.service.join("ohno", "Carol", player_id=table.players["Carol"]["player_id"], session_token=table.players["Carol"]["session_token"])
    table.submit("Carol")
    bob = table.state("Bob")
    assert bob["room"]["state"] == "JUDGING" and len(bob["judging"]["decisions"]) == 1


def test_responsible_adult_vanishing_at_the_result_lets_the_successor_advance(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    table.submit("Bob")
    table.submit("Carol")
    alice = table.state("Alice")
    table.act("Alice", "judge", submission_id=alice["judging"]["decisions"][0]["submission_id"])
    scores = {player["name"]: player["score"] for player in table.state("Bob")["players"]}
    table.pass_time(31, active=("Bob", "Carol"))
    assert table.adult() == "Bob"
    assert {player["name"]: player["score"] for player in table.state("Bob")["players"]} == scores
    table.act("Bob", "advance")
    assert table.state("Bob")["room"]["state"] == "PLAYING"
    assert table.adult() == "Carol"


def test_table_recovers_when_players_return_after_everyone_went_away(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    table.pass_time(31)  # nobody heartbeats
    table.pass_time(1, active=("Bob", "Carol"))  # Bob and Carol return; Alice does not
    assert table.adult() == "Bob"
    table.submit("Carol")
    assert table.state("Bob")["room"]["state"] == "JUDGING"


def test_returning_responsible_adult_does_not_take_the_role_back(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    table.pass_time(31, active=("Bob", "Carol"))
    table.pass_time(1, active=("Alice", "Bob", "Carol"))
    assert table.adult("Alice") == "Bob"
    table.submit("Alice")
    table.submit("Carol")
    assert table.state("Bob")["room"]["state"] == "JUDGING"


def test_host_role_passes_when_the_room_opener_leaves_the_lobby(tmp_path):
    table = Table(tmp_path, names=("Alice", "Bob", "Carol", "Dan"))
    table.act("Alice", "leave")
    bob = table.state("Bob")
    assert bob["you"]["room_owner"] is True
    assert table.state("Carol")["you"]["room_owner"] is False
    with pytest.raises(PeerPressureError) as refused:
        table.act("Carol", "start")
    assert refused.value.reason == "not_responsible_adult"
    table.act("Bob", "start")
    assert table.adult() == "Bob"


def test_rooms_stream_cards_instead_of_copying_decks(tmp_path):
    table = Table(tmp_path)
    table.act("Alice", "start")
    with sqlite3.connect(tmp_path / "ohno.sqlite3") as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        drawn = connection.execute("SELECT kind, COUNT(*) FROM drawn_cards GROUP BY kind").fetchall()
    assert "question_deck" not in tables and "response_deck" not in tables
    assert dict(drawn) == {"prompt": 1, "answer": 9}  # one prompt, three hands of three


def test_streamed_draws_never_repeat_and_report_exhaustion(tmp_path):
    service = PeerPressureService(tmp_path, multiplayer_registry(), rng=random.Random(7))
    service.create_room("deck")
    connection = service._connect("deck")
    try:
        keys = [key for _ in range(8) for key, _card in service._draw(connection, "answer", 5)]
        assert len(keys) == len(set(keys)) == 40
        with pytest.raises(PeerPressureError) as exhausted:
            service._draw(connection, "answer", 1)
        assert exhausted.value.reason == "answer_deck_exhausted"
        with pytest.raises(PeerPressureError) as oversized:
            service._draw(connection, "answer", 41)
        assert oversized.value.reason == "answer_deck_exhausted"
        prompts = [key for _ in range(4) for key, _card in service._draw(connection, "prompt", 1)]
        assert len(set(prompts)) == 4
        with pytest.raises(PeerPressureError) as no_prompts:
            service._draw(connection, "prompt", 1)
        assert no_prompts.value.reason == "prompt_deck_exhausted"
    finally:
        connection.close()


def test_room_creation_cost_does_not_grow_with_the_registry(tmp_path):
    big = make_pack(
        "big",
        prompts=tuple(Prompt(id=f"q{i}", text=f"Q{i} _", template=f"Q{i} {{}}", slots=1, pack="big") for i in range(2000)),
        answers=tuple(Answer(id=f"r{i}", text=f"R{i}", pack="big") for i in range(8000)),
    )
    service = PeerPressureService(tmp_path, Registry(MappingProxyType({"big": big})))
    service.create_room("huge")
    with sqlite3.connect(tmp_path / "huge.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM drawn_cards").fetchone()[0] == 0
    assert (tmp_path / "huge.sqlite3").stat().st_size < 128 * 1024


@pytest.mark.parametrize("version", [0, 2, service_module.SCHEMA_VERSION - 1, service_module.SCHEMA_VERSION + 1])
def test_rooms_from_other_server_versions_are_refused_not_migrated(tmp_path, version):
    """Rooms are disposable: a 1.x room gets a clear 410 and expires on schedule."""
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3)
    alice = service.join("legacy", "Alice", create=True)
    with sqlite3.connect(tmp_path / "legacy.sqlite3") as connection:
        connection.execute(f"PRAGMA user_version={version}")
    with pytest.raises(PeerPressureError) as refused:
        service.sync("legacy", *credentials(alice))
    assert (refused.value.reason, refused.value.status) == ("room_expired", 410)
    assert "start a new room" in refused.value.message


def test_idempotency_keys_are_scoped_to_the_authenticated_player(tmp_path):
    service = PeerPressureService(tmp_path, multiplayer_registry(), hand_size=3)
    alice, bob, _carol = join_table(service)
    current = service.sync("ohno", *credentials(alice))["revision"]
    shared = request(777)
    started = service.start("ohno", *credentials(alice), shared, current)
    bob_state = service.sync("ohno", *credentials(bob))["state"]
    card = bob_state["you"]["hand"][0]["card_instance_id"]
    submitted = service.submit("ohno", *credentials(bob), shared, started["revision"], [card])
    assert submitted["revision"] == started["revision"] + 1


def test_room_creation_is_atomic_and_cleans_up(tmp_path, monkeypatch):
    service = PeerPressureService(tmp_path, multiplayer_registry())

    def lost_race(_source, destination):
        destination.write_bytes(b"")  # someone else's room appeared first
        raise FileExistsError(destination)

    monkeypatch.setattr(service_module.os, "link", lost_race)
    with pytest.raises(PeerPressureError) as exists:
        service.create_room("racy")
    assert exists.value.reason == "room_exists"
    monkeypatch.undo()
    assert [path.name for path in tmp_path.iterdir()] == ["racy.sqlite3"]  # no half-built leftovers

    stale = tmp_path / f".old.{'0' * 16}{service_module.BUILDING_SUFFIX}"
    stale.write_bytes(b"")
    os.utime(stale, (0, 0))
    service.cleanup_expired()
    assert not stale.exists()
