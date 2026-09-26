from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType

import pytest

from bad_decisions.api import create_app
from bad_decisions.models import BlackCard, WhiteCard
from bad_decisions.packs import Registry
from bad_decisions.peer_pressure import PeerPressureError, PeerPressureService
from conftest import make_pack
from fastapi.testclient import TestClient


def multiplayer_registry() -> Registry:
    pack = make_pack(
        "party",
        black=tuple(
            BlackCard(id=f"q{index}", repr=f"Question {index}: _", template=f"Question {index}: {{}}", slots=1, pack="party")
            for index in range(4)
        ),
        white=tuple(WhiteCard(id=f"r{index}", text=f"Response {index}", pack="party") for index in range(40)),
    )
    return Registry(MappingProxyType({"party": pack}))


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
    assert all(set(decision) == {"submission_id", "responses"} for decision in judging["decisions"])
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
    assert heartbeat == {"type": "NACK", "reason": "stale_revision", "revision": started["revision"] + 1, "resync": True}
    state = service.sync("ohno", *credentials(alice))["state"]
    assert next(player for player in state["players"] if player["name"] == "Bob")["connected"] is False
    reconnected = service.join("ohno", "Bob", player_id=bob["player_id"], session_token=bob["session_token"])
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
            outcomes.append(future.result()["type"])
        except PeerPressureError as exc:
            outcomes.append(exc.reason)
    assert sorted(outcomes) == ["ACK", "stale_revision"]


def test_http_protocol_uses_responsible_adult_and_protects_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        joined = [
            client.post("/v1/peer-pressure/rooms/tabletop/join", json={"display_name": name, "create": True}).json()
            for name in ("Alice", "Bob", "Carol")
        ]
        assert all(item["type"] == "SYNACK" for item in joined)
        alice = joined[0]
        headers = {"Authorization": f"Bearer {alice['session_token']}"}
        denied = client.get("/v1/peer-pressure/rooms/tabletop/state", headers={"X-Peer-Pressure-Player": alice["player_id"]})
        assert denied.status_code == 401 and denied.json()["reason"] == "invalid_session"
        started = client.post(
            "/v1/peer-pressure/rooms/tabletop/start",
            headers=headers,
            json={"player_id": alice["player_id"], "request_id": request(40), "revision": joined[-1]["revision"]},
        )
        assert started.status_code == 200 and started.json()["type"] == "ACK"
        state = client.get(
            "/v1/peer-pressure/rooms/tabletop/state",
            headers={**headers, "X-Peer-Pressure-Player": alice["player_id"]},
        ).json()
        assert state["responsible_adult"]["name"] == "Alice"
        assert all("hand" not in player for player in state["players"])
        schema = client.get("/openapi.json").text.lower()
        assert "card czar" not in schema and "choose_peer_consequence" in schema
