from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

SESSION_PATH = Path(os.path.expanduser("~")) / ".regret-peer-pressure.json"
# Total sends of one action when the server keeps answering stale_revision.
STALE_RETRIES = 3
TERMINAL_STREAM_ERRORS = ("invalid_session:", "room_expired:", "room_not_found:")

StillValid = Callable[[dict[str, Any]], bool]
EventSource = Callable[[str, dict[str, str], threading.Event], Iterator[dict[str, Any]]]


def is_responsible_adult(state: dict[str, Any]) -> bool:
    """Return whether this projection gives the current player table control."""
    you = state.get("you") or {}
    adult = state.get("responsible_adult")
    return bool(
        (adult and adult.get("id") == you.get("id"))
        or (not adult and you.get("room_owner"))
    )


def _checked(check: Callable[[dict[str, Any]], bool]) -> StillValid:
    # A projection too malformed to check is treated as "no longer applies".
    def still_valid(state: dict[str, Any]) -> bool:
        try:
            return bool(check(state))
        except (KeyError, TypeError, AttributeError):
            return False
    return still_valid


def always(_state: dict[str, Any]) -> bool:
    return True


@_checked
def can_start(state: dict[str, Any]) -> bool:
    return state["room"]["state"] == "WAITING" and is_responsible_adult(state)


@_checked
def can_end(state: dict[str, Any]) -> bool:
    return state["room"]["state"] != "ENDED" and bool(state["you"]["room_owner"])


def can_submit(round_: Any, card_instance_ids: list[str]) -> StillValid:
    @_checked
    def check(state: dict[str, Any]) -> bool:
        hand = {card["card_instance_id"] for card in state["you"]["hand"]}
        return (
            state["room"]["state"] == "PLAYING" and state["room"]["round"] == round_
            and not is_responsible_adult(state) and not state["you"]["submitted"]
            and set(card_instance_ids) <= hand
        )
    return check


def can_judge(round_: Any, submission_id: str) -> StillValid:
    @_checked
    def check(state: dict[str, Any]) -> bool:
        return (
            state["room"]["state"] == "JUDGING" and state["room"]["round"] == round_
            and is_responsible_adult(state)
            and any(decision["submission_id"] == submission_id for decision in state["judging"]["decisions"])
        )
    return check


def can_advance(round_: Any) -> StillValid:
    @_checked
    def check(state: dict[str, Any]) -> bool:
        return state["room"]["state"] == "ROUND_RESULT" and state["room"]["round"] == round_ and is_responsible_adult(state)
    return check


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
    def __init__(self, api_url: str, room: str, timeout: float, request_json: Callable[..., tuple[Any, Any]], event_source: EventSource | None = None):
        self.api_url = api_url.rstrip("/")
        self.room = room
        self.timeout = timeout
        self.request_json = request_json
        self.event_source = event_source
        self.player_id = ""
        self.session_token = ""
        self.revision = 0
        self.state: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        return f"{self.api_url}/v2/peer-pressure/rooms/{quote(self.room, safe='')}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.session_token}"}

    def join(self, display_name: str, saved: dict[str, str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"display_name": display_name, "create": True}
        # A saved session token rejoins as the same player; it alone identifies them.
        headers = {"Authorization": f"Bearer {saved['session_token']}"} if saved else None
        response, _ = self.request_json(f"{self.endpoint}/join", timeout=self.timeout, method="POST", payload=payload, headers=headers)
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
            f"{self.endpoint}/sync", timeout=self.timeout, method="POST", headers=self._headers(),
        )
        self._apply(response)
        return self.state

    def heartbeat(self) -> bool:
        with self._lock:
            revision = self.revision
        response, _ = self.request_json(
            f"{self.endpoint}/heartbeat", timeout=self.timeout, method="POST",
            payload={"revision": revision}, headers=self._headers(),
        )
        return bool(response.get("resync"))

    def events(self, stop_event: threading.Event) -> Iterator[dict[str, Any]]:
        if self.event_source is None:
            raise RuntimeError("Peer Pressure live updates are unavailable")
        for payload in self.event_source(f"{self.endpoint}/events", self._headers(), stop_event):
            if stop_event.is_set():
                return
            self._apply(payload)
            yield self.state

    def mutate(self, action: str, *, still_valid: StillValid | None = None, **payload: Any) -> dict[str, Any]:
        """Send one action.

        A stale_revision refusal resynchronizes. The action is resent against the
        fresh revision (with a new request id) only while ``still_valid`` accepts
        the fresh state, and at most ``STALE_RETRIES`` sends in total.
        """
        for attempt in range(1, STALE_RETRIES + 1):
            try:
                response = self._send(action, payload)
                break
            except RuntimeError as exc:
                if "stale_revision" not in str(exc):
                    raise
                fresh = self.sync()
                if still_valid is None or attempt == STALE_RETRIES or not still_valid(fresh):
                    raise
        with self._lock:
            self.revision = int(response["revision"])
        if action != "end":  # an ended room is deleted; there is nothing left to synchronize
            self.sync()
        return response

    def _send(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = f"req_{uuid.uuid4().hex}"
        with self._lock:
            revision = self.revision
        body = {"request_id": request_id, "revision": revision, **payload}
        try:
            response, _ = self.request_json(f"{self.endpoint}/{action}", timeout=self.timeout, method="POST", payload=body, headers=self._headers())
        except RuntimeError as exc:
            if "Cannot reach API" not in str(exc):
                raise
            # A lost acknowledgement: resend the same request id so it applies once.
            try:
                response, _ = self.request_json(f"{self.endpoint}/{action}", timeout=self.timeout, method="POST", payload=body, headers=self._headers())
            except RuntimeError as retry_exc:
                # Ending deletes the room with its replay record, so the resend of an
                # end that already succeeded finds no room rather than the stored reply.
                if action == "end" and str(retry_exc).startswith("room_not_found"):
                    return {"request_id": request_id, "revision": revision}
                raise
        return response


class PeerConnection:
    def __init__(self, client: TogetherClient, interval: float, on_state: Callable[[dict[str, Any]], None] | None = None, *, heartbeats: bool = True):
        self.client = client
        self.interval = interval
        self.on_state = on_state
        self.send_heartbeats = heartbeats
        self.stop_event = threading.Event()
        self.changed = threading.Event()
        self.error: Exception | None = None
        self.heartbeat_thread = threading.Thread(target=self._heartbeats, name="regret-peer-pressure-heartbeat", daemon=True)
        self.events_thread = threading.Thread(target=self._events, name="regret-peer-pressure-events", daemon=True)

    def _heartbeats(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.client.heartbeat()
            except Exception as exc:
                self.error = exc
                if str(exc).startswith(TERMINAL_STREAM_ERRORS):
                    self.stop_event.set()
                    return

    def _events(self) -> None:
        delay = 0.5
        while not self.stop_event.is_set():
            try:
                for state in self.client.events(self.stop_event):
                    self.error = None
                    self.changed.set()
                    if self.on_state is not None:
                        self.on_state(state)
                delay = 0.5
            except Exception as exc:
                self.error = exc
                if str(exc).startswith(TERMINAL_STREAM_ERRORS):
                    self.stop_event.set()
                    return
            if self.stop_event.wait(delay):
                return
            delay = min(delay * 2, 5.0)

    def __enter__(self):
        if self.send_heartbeats:
            self.heartbeat_thread.start()
        self.events_thread.start()
        return self

    def __exit__(self, *_args):
        self.stop_event.set()
        if self.send_heartbeats:
            self.heartbeat_thread.join(timeout=min(self.interval, 1.0))
        self.events_thread.join(timeout=1.0)


def render(state: dict[str, Any]) -> None:
    room = state["room"]
    print(f"\nRoom: {room['code']} · Round {room['round']} · {room['state'].replace('_', ' ').title()}")
    print("Table: " + ", ".join(f"{player['name']} ({player['score']})" + (" [away]" if not player["connected"] else "") for player in state["players"]))
    if state.get("responsible_adult"):
        print(f"Responsible Adult: {state['responsible_adult']['name']}")
    if state.get("prompt"):
        print(f"Prompt: {state['prompt']['text']}")
    if state.get("result"):
        print(f"Consequence: {state['result']['rendered']}")
        print(f"Peer pressure worked on {state['result']['winning_player']['name']}.")


def choose_answers(state: dict[str, Any], input_: Callable[[str], str], extra: str = "") -> list[str] | None:
    hand = state["you"]["hand"]
    for index, card in enumerate(hand, 1):
        print(f"  {index:2}. {card['text']}")
    needed = state["prompt"]["slots"]
    raw = input_(f"Choose {needed} answer{'s' if needed != 1 else ''} (comma-separated, or q){extra}: ").strip()
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


class _EndRequested(Exception):
    """The room host typed e at a prompt."""


def run_together(client: TogetherClient, input_: Callable[[str], str] = input, heartbeat_interval: float = 5.0) -> int:
    print("Giving in to Peer Pressure…")
    with PeerConnection(client, heartbeat_interval) as connection:
        while True:
            if connection.error is not None and str(connection.error).startswith(TERMINAL_STREAM_ERRORS):
                print(f"Peer Pressure faltered: {connection.error}", file=sys.stderr)
                break
            state = client.state
            render(state)
            room_state = state["room"]["state"]
            you = state["you"]
            adult = is_responsible_adult(state)
            round_ = state["room"]["round"]
            host = can_end(state)
            end = " · [e] End room" if host else ""

            def ask(prompt: str) -> str:
                raw = input_(prompt).strip()
                if host and raw.lower() == "e":
                    raise _EndRequested
                return raw

            try:
                if room_state == "WAITING":
                    command = ask("[s] Start" + ("" if adult else " (host only)") + f" · [Enter] Refresh · [q] Regret alone{end}: ").strip().lower()
                    if command == "s" and adult:
                        client.mutate("start", still_valid=can_start)
                    elif command == "q":
                        client.mutate("leave", still_valid=always)
                        break
                elif room_state == "PLAYING" and adult:
                    if ask(f"Waiting for everyone else to decide. [Enter] Refresh · [q] Regret alone{end}: ").strip().lower() == "q":
                        client.mutate("leave", still_valid=always); break
                elif room_state == "PLAYING" and not you["submitted"]:
                    cards = choose_answers(state, ask, end)
                    if cards is None:
                        client.mutate("leave", still_valid=always); break
                    if cards:
                        client.mutate("submit", still_valid=can_submit(round_, cards), card_instance_ids=cards)
                elif room_state == "PLAYING":
                    if ask(f"Decision submitted. [Enter] Refresh · [q] Regret alone{end}: ").strip().lower() == "q":
                        client.mutate("leave", still_valid=always); break
                elif room_state == "JUDGING" and adult:
                    decisions = state["judging"]["decisions"]
                    for index, decision in enumerate(decisions, 1):
                        print(f"  {index:2}. " + " / ".join(decision["answers"]))
                    raw = ask(f"Choose the consequence (number, or q){end}: ").strip()
                    if raw.lower() == "q":
                        client.mutate("leave", still_valid=always); break
                    try:
                        choice = int(raw)
                    except ValueError:
                        choice = 0
                    if 1 <= choice <= len(decisions):
                        submission_id = decisions[choice - 1]["submission_id"]
                        client.mutate("judge", still_valid=can_judge(round_, submission_id), submission_id=submission_id)
                    else:
                        print("The Responsible Adult must make a responsible selection.", file=sys.stderr)
                elif room_state == "JUDGING":
                    if ask(f"The Responsible Adult is deciding. [Enter] Refresh · [q] Regret alone{end}: ").strip().lower() == "q":
                        client.mutate("leave", still_valid=always); break
                elif room_state == "ROUND_RESULT" and adult:
                    if ask(f"[Enter] Make another bad decision · [q] Regret alone{end}: ").strip().lower() == "q":
                        client.mutate("leave", still_valid=always); break
                    client.mutate("advance", still_valid=can_advance(round_))
                elif room_state == "ROUND_RESULT":
                    if ask(f"[Enter] Await further pressure · [q] Regret alone{end}: ").strip().lower() == "q":
                        client.mutate("leave", still_valid=always); break
                else:
                    break
            except _EndRequested:
                if input_("End this room for everyone? [y/N]: ").strip().lower() in ("y", "yes"):
                    try:
                        client.mutate("end", still_valid=can_end)
                    except RuntimeError as exc:
                        print(f"Peer Pressure faltered: {exc}", file=sys.stderr)
                    else:
                        print("The room has ended.")
                        return 0
            except RuntimeError as exc:
                print(f"Peer Pressure faltered: {exc}", file=sys.stderr)
                time.sleep(min(heartbeat_interval, 1.0))
            if connection.changed.is_set():
                connection.changed.clear()
    print("Regret alone.")
    return 0
