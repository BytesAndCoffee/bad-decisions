from __future__ import annotations
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from bad_decisions.api import create_app
from bad_decisions import cli
from bad_decisions.consequences import ConsequencesStore, canonical_hash, round_identity

def configured(tmp_path, monkeypatch):
    database=tmp_path/"consequences.sqlite3"
    monkeypatch.setenv("BAD_DECISIONS_CONSEQUENCES_DB",str(database))
    return database

def issue(client):
    response=client.get("/v1/round",headers={"X-Regret-Client-ID":"not-a-uuid"})
    assert response.status_code == 200
    return response, response.json(), response.headers["X-Regret-Feedback-Token"]

def test_hash_identity_is_deterministic_and_ordered(sample_pack):
    from bad_decisions.engine import generate_from_resolved
    from bad_decisions.packs import Registry, resolve_pools
    registry=Registry({"maha":sample_pack})
    round_=generate_from_resolved(resolve_pools(registry,packs="maha"),registry)
    assert round_identity(round_) == round_identity(round_)
    assert canonical_hash({"b":2,"a":1}) == canonical_hash({"a":1,"b":2})

def test_feedback_transitions_and_no_token_leak(tmp_path, monkeypatch):
    database=configured(tmp_path,monkeypatch)
    with TestClient(create_app()) as client:
        response,body,token=issue(client)
        assert token not in response.text and body["feedback"]["available"]
        url=body["feedback"]["url"]; headers={"X-Regret-Feedback-Token":token}
        assert client.put(url,json={"enjoyed":True},headers=headers).status_code == 201
        assert client.put(url,json={"enjoyed":True},headers=headers).status_code == 200
        assert client.put(url,json={"enjoyed":False},headers=headers).json()["changed"] is True
        assert client.delete(url,headers=headers).status_code == 204
        assert client.delete(url,headers=headers).status_code == 204
        assert client.put(url,json={"enjoyed":"true"},headers=headers).status_code == 422
        assert client.put(url,json={"enjoyed":1},headers=headers).status_code == 422
        assert client.put(url,json={"enjoyed":True,"score":1},headers=headers).status_code == 422
    store=ConsequencesStore(database)
    stats=store.stats(body["combination_hash"])
    assert stats["vote_count"] == 0 and stats["score"] is None

def test_optional_storage_fails_open(tmp_path,monkeypatch):
    monkeypatch.setenv("BAD_DECISIONS_CONSEQUENCES_DB",str(tmp_path/"missing"/"c.sqlite"))
    with pytest.raises(ValueError):
        with TestClient(create_app()): pass
    monkeypatch.delenv("BAD_DECISIONS_CONSEQUENCES_DB")
    with TestClient(create_app()) as client:
        response=client.get("/v1/round")
    assert response.status_code == 200 and "round_id" not in response.json()

def test_request_metadata_errors_and_concurrent_votes(tmp_path,monkeypatch):
    database=configured(tmp_path,monkeypatch)
    with TestClient(create_app()) as client:
        client.get("/v1/nope")
        _,body,token=issue(client)
    store=ConsequencesStore(database)
    def vote(value): return store.feedback(body["round_id"],token,value)[0]
    with ThreadPoolExecutor(max_workers=8) as pool: assert set(pool.map(vote,[True,False,True,False,True,False,True,False])) == {"ok"}
    store.rebuild()
    stats=store.stats(body["combination_hash"])
    assert stats["vote_count"] == 1
    assert store.report()["requests"]["errors"] >= 1

def test_expired_capability(tmp_path,monkeypatch):
    database=configured(tmp_path,monkeypatch)
    monkeypatch.setenv("BAD_DECISIONS_CONSEQUENCES_FEEDBACK_TTL_SECONDS","1")
    with TestClient(create_app()) as client:
        _,body,token=issue(client)
    with sqlite3.connect(database) as connection: connection.execute("UPDATE rounds SET feedback_expires_at=0")
    with TestClient(create_app()) as client:
        assert client.put(body["feedback"]["url"],json={"enjoyed":True},headers={"X-Regret-Feedback-Token":token}).status_code == 410


def test_consequences_report_defaults_to_configured_database(tmp_path, monkeypatch):
    database = tmp_path / "configured.sqlite3"
    ConsequencesStore(database)
    monkeypatch.setenv("BAD_DECISIONS_CONSEQUENCES_DB", str(database))

    assert cli._default_consequences_database() == database
    assert cli.run(["consequences", "report"]) == 0


def test_consequences_report_discovers_installed_database(tmp_path, monkeypatch):
    database = tmp_path / "app" / "consequences" / "consequences.sqlite3"
    database.parent.mkdir(parents=True)
    ConsequencesStore(database)
    environment = tmp_path / "app" / "releases" / "release-id" / ".venv"
    monkeypatch.delenv("BAD_DECISIONS_CONSEQUENCES_DB", raising=False)
    monkeypatch.setattr(cli.sys, "prefix", str(environment))

    assert cli._default_consequences_database() == database
    assert cli.run(["consequences", "report"]) == 0


def test_mutating_consequences_commands_still_require_database_path():
    with pytest.raises(SystemExit):
        cli.run(["consequences", "rebuild"])
    with pytest.raises(SystemExit):
        cli.run(["consequences", "purge", "--retention-days", "90"])


def test_readonly_store_reports_without_write_access(tmp_path):
    database = tmp_path / "readonly.sqlite3"
    ConsequencesStore(database)
    database.chmod(0o440)

    report = ConsequencesStore(database, readonly=True).report()
    assert report["schema_version"] == 1
    assert report["draws"]["recorded"] == 0
