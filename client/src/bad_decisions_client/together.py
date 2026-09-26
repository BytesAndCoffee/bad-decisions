from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

SESSION_PATH = Path.home() / ".regret-peer-pressure.json"


def _session_key(api_url: str, room: str) -> str:
    return f"{api_url.rstrip('/')}|{room}"


def load_session(api_url: str, room: str, path: Path = SESSION_PATH) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("sessions", {}).get(_session_key(api_url, room))
        if isinstance(value, dict) and all(isinstance(value.get(key), str) for key in ("player_id", "session_token", "display_name")):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return None


def save_session(api_url: str, room: str, session: dict[str, str], atomic_json: Callable[[Path, dict[str, Any]], None], path: Path = SESSION_PATH) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except (OSError, ValueError, TypeError):
        payload = {}
    sessions = payload.setdefault("sessions", {})
    sessions[_session_key(api_url, room)] = session
    atomic_json(path, payload)


class TogetherClient:
    def __init__(self, api_url: str, room: str, timeout: float, request_json: Callable[..., tuple[Any, Any]]):
        self.api_url = api_url.rstrip("/")
        self.room = room
        self.timeout = timeout
        self.request_json = request_json
        self.player_id = ""
        self.session_token = ""
        self.revision = 0
        self.state: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        return f"{self.api_url}/v1/peer-pressure/rooms/{quote(self.room, safe='')}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.session_token}"}

    def join(self, display_name: str, saved: dict[str, str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"display_name": display_name, "create": True}
        if saved:
            payload.update({"player_id": saved["player_id"], "session_token": saved["session_token"]})
        response, _ = self.request_json(f"{self.endpoint}/join", timeout=self.timeout, method="POST", payload=payload)
        self.player_id = response["player_id"]
        self.session_token = response["session_token"]
        self._apply(response)
        return response

    def _apply(self, response: dict[str, Any]) -> None:
        with self._lock:
            incoming = int(response["revision"])
            if incoming < self.revision:
                return
            self.revision = incoming
            if "state" in response:
                self.state = response["state"]

    def sync(self) -> dict[str, Any]:
        response, _ = self.request_json(
            f"{self.endpoint}/sync", timeout=self.timeout, method="POST",
            payload={"player_id": self.player_id}, headers=self._headers(),
        )
        self._apply(response)
        return self.state

    def heartbeat(self) -> bool:
        with self._lock:
            revision = self.revision
        response, _ = self.request_json(
            f"{self.endpoint}/heartbeat", timeout=self.timeout, method="POST",
            payload={"player_id": self.player_id, "revision": revision}, headers=self._headers(),
        )
        if response.get("type") == "NACK" and response.get("resync"):
            self.sync()
            return True
        return False

    def mutate(self, action: str, **payload: Any) -> dict[str, Any]:
        request_id = f"req_{uuid.uuid4().hex}"
        with self._lock:
            revision = self.revision
        body = {"player_id": self.player_id, "request_id": request_id, "revision": revision, **payload}
        try:
            response, _ = self.request_json(f"{self.endpoint}/{action}", timeout=self.timeout, method="POST", payload=body, headers=self._headers())
        except RuntimeError as exc:
            if "stale_revision" in str(exc):
                self.sync()
                raise
            if "Cannot reach API" not in str(exc):
                raise
            response, _ = self.request_json(f"{self.endpoint}/{action}", timeout=self.timeout, method="POST", payload=body, headers=self._headers())
        with self._lock:
            self.revision = int(response["revision"])
        self.sync()
        return response


class Heartbeat:
    def __init__(self, client: TogetherClient, interval: float):
        self.client = client
        self.interval = interval
        self.stop_event = threading.Event()
        self.changed = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, name="regret-peer-pressure-heartbeat", daemon=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                if self.client.heartbeat():
                    self.changed.set()
            except Exception as exc:
                self.error = exc

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop_event.set()
        self.thread.join(timeout=min(self.interval, 1.0))


def render(state: dict[str, Any]) -> None:
    room = state["room"]
    print(f"\nRoom: {room['code']} · Round {room['round']} · {room['state'].replace('_', ' ').title()}")
    print("Table: " + ", ".join(f"{player['name']} ({player['score']})" + (" [away]" if not player["connected"] else "") for player in state["players"]))
    if state.get("responsible_adult"):
        print(f"Responsible Adult: {state['responsible_adult']['name']}")
    if state.get("question"):
        print(f"Question: {state['question']['text']}")
    if state.get("result"):
        print(f"Consequence: {state['result']['rendered']}")
        print(f"Peer pressure worked on {state['result']['winning_player']['name']}.")


def choose_responses(state: dict[str, Any], input_: Callable[[str], str]) -> list[str] | None:
    hand = state["you"]["hand"]
    for index, card in enumerate(hand, 1):
        print(f"  {index:2}. {card['text']}")
    needed = state["question"]["slots"]
    raw = input_(f"Choose {needed} response{'s' if needed != 1 else ''} (comma-separated, or q): ").strip()
    if raw.lower() == "q":
        return None
    try:
        indexes = [int(value.strip()) for value in raw.split(",")]
        if len(indexes) != needed or len(set(indexes)) != needed or any(index < 1 or index > len(hand) for index in indexes):
            raise ValueError
    except ValueError:
        print("That is not a usable decision.", file=sys.stderr)
        return []
    return [hand[index - 1]["card_instance_id"] for index in indexes]


def run_together(client: TogetherClient, input_: Callable[[str], str] = input, heartbeat_interval: float = 5.0) -> int:
    print("Giving in to Peer Pressure…")
    with Heartbeat(client, heartbeat_interval) as heartbeat:
        while True:
            state = client.sync()
            render(state)
            room_state = state["room"]["state"]
            you = state["you"]
            adult = state.get("responsible_adult", {}).get("id") == you["id"] if state.get("responsible_adult") else you["room_owner"]
            try:
                if room_state == "WAITING":
                    command = input_("[s] Start" + ("" if adult else " (Responsible Adult only)") + " · [Enter] Refresh · [q] Regret alone: ").strip().lower()
                    if command == "s" and adult:
                        client.mutate("start")
                    elif command == "q":
                        client.mutate("leave")
                        break
                elif room_state == "PLAYING" and adult:
                    if input_("Waiting for everyone else to decide. [Enter] Refresh · [q] Regret alone: ").strip().lower() == "q":
                        client.mutate("leave"); break
                elif room_state == "PLAYING" and not you["submitted"]:
                    cards = choose_responses(state, input_)
                    if cards is None:
                        client.mutate("leave"); break
                    if cards:
                        client.mutate("submit", card_instance_ids=cards)
                elif room_state == "PLAYING":
                    if input_("Decision submitted. [Enter] Refresh · [q] Regret alone: ").strip().lower() == "q":
                        client.mutate("leave"); break
                elif room_state == "JUDGING" and adult:
                    decisions = state["judging"]["decisions"]
                    for index, decision in enumerate(decisions, 1):
                        print(f"  {index:2}. " + " / ".join(decision["responses"]))
                    raw = input_("Choose the consequence (number): ").strip()
                    try:
                        choice = int(raw)
                        client.mutate("judge", submission_id=decisions[choice - 1]["submission_id"])
                    except (ValueError, IndexError):
                        print("The Responsible Adult must make a responsible selection.", file=sys.stderr)
                elif room_state == "JUDGING":
                    if input_("The Responsible Adult is deciding. [Enter] Refresh · [q] Regret alone: ").strip().lower() == "q":
                        client.mutate("leave"); break
                elif room_state == "ROUND_RESULT" and adult:
                    if input_("[Enter] Make another bad decision · [q] Regret alone: ").strip().lower() == "q":
                        client.mutate("leave"); break
                    client.mutate("advance")
                elif room_state == "ROUND_RESULT":
                    if input_("[Enter] Await further pressure · [q] Regret alone: ").strip().lower() == "q":
                        client.mutate("leave"); break
                else:
                    break
            except RuntimeError as exc:
                print(f"Peer Pressure faltered: {exc}", file=sys.stderr)
                time.sleep(min(heartbeat_interval, 1.0))
            if heartbeat.changed.is_set():
                heartbeat.changed.clear()
    print("Regret alone.")
    return 0
