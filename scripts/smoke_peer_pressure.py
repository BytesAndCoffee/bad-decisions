#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(url: str, *, method: str = "GET", payload=None, token: str | None = None, player: str | None = None):
    headers = {"Accept": "application/json", "User-Agent": "bad-decisions-peer-pressure-smoke"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if player:
        headers["X-Peer-Pressure-Player"] = player
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, method=method, headers=headers), timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"API unavailable: {exc.reason}") from exc


def mutation(base: str, room: str, joined: dict, action: str, revision: int, **extra):
    payload = {
        "player_id": joined["player_id"],
        "request_id": f"req_{uuid.uuid4().hex}",
        "revision": revision,
        **extra,
    }
    return request(f"{base}/v1/peer-pressure/rooms/{room}/{action}", method="POST", payload=payload, token=joined["session_token"])


def sync(base: str, room: str, joined: dict):
    return request(
        f"{base}/v1/peer-pressure/rooms/{room}/state",
        token=joined["session_token"],
        player=joined["player_id"],
    )


def smoke(base: str) -> None:
    base = base.rstrip("/")
    room = f"smoke-{uuid.uuid4().hex[:12]}"
    joined: list[dict] = []
    try:
        for name in ("Smoke One", "Smoke Two", "Smoke Three"):
            joined.append(request(
                f"{base}/v1/peer-pressure/rooms/{room}/join",
                method="POST",
                payload={"display_name": name, "create": True},
            ))
        revision = joined[-1]["revision"]
        revision = mutation(base, room, joined[0], "start", revision)["revision"]
        states = [sync(base, room, player) for player in joined]
        assert states[0]["responsible_adult"]["name"] == "Smoke One"
        assert all("hand" not in player for player in states[0]["players"])
        slots = states[0]["question"]["slots"]
        for index in (1, 2):
            cards = [card["card_instance_id"] for card in states[index]["you"]["hand"][:slots]]
            revision = mutation(base, room, joined[index], "submit", revision, card_instance_ids=cards)["revision"]
        adult = sync(base, room, joined[0])
        decisions = adult["judging"]["decisions"]
        assert len(decisions) == 2
        assert all(set(decision) == {"submission_id", "responses"} for decision in decisions)
        revision = mutation(base, room, joined[0], "judge", revision, submission_id=decisions[0]["submission_id"])["revision"]
        result = sync(base, room, joined[1])
        assert result["room"]["state"] == "ROUND_RESULT"
        assert result["result"]["winning_player"]["name"] in {"Smoke Two", "Smoke Three"}
    finally:
        if joined:
            try:
                state = sync(base, room, joined[0])
                payload = {
                    "player_id": joined[0]["player_id"],
                    "request_id": f"req_{uuid.uuid4().hex}",
                    "revision": state["room"]["revision"],
                }
                request(f"{base}/v1/peer-pressure/rooms/{room}", method="DELETE", payload=payload, token=joined[0]["session_token"])
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise a disposable Peer Pressure room")
    parser.add_argument("base_url")
    args = parser.parse_args()
    try:
        smoke(args.base_url)
    except (AssertionError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        print(f"Peer Pressure smoke test failed: {exc}", file=sys.stderr)
        return 1
    print("Peer Pressure smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
