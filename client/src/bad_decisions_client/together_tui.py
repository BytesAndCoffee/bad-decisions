"""Textual Peer Pressure client for Regret."""
from __future__ import annotations

import sys
from typing import Any, Callable

try:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, DataTable, Footer, Header, Static
except ImportError as exc:
    raise RuntimeError(
        "the Peer Pressure TUI requires Textual; install bad-decisions-client[tui]"
    ) from exc

from .together import (
    TogetherClient,
    always,
    can_advance,
    can_judge,
    can_start,
    can_submit,
    is_responsible_adult,
)


def instruction_for(state: dict[str, Any]) -> str:
    room_state = (state.get("room") or {}).get("state", "WAITING")
    you = state.get("you") or {}
    adult = is_responsible_adult(state)
    if room_state == "WAITING":
        return "Start when at least three people are present." if adult else "Waiting for the table host to start."
    if room_state == "PLAYING" and adult:
        return "Everyone else is choosing a terrible answer."
    if room_state == "PLAYING" and you.get("submitted"):
        return "Decision submitted. Peer pressure is processing."
    if room_state == "PLAYING":
        slots = int((state.get("prompt") or {}).get("slots", 1))
        return f"Select {slots} answer{'s' if slots != 1 else ''}, then submit."
    if room_state == "JUDGING" and adult:
        return "Choose the consequence. This is apparently your responsibility."
    if room_state == "JUDGING":
        return "The Responsible Adult is making a deeply responsible selection."
    if room_state == "ROUND_RESULT" and adult:
        return "Advance when the table has absorbed the consequences."
    if room_state == "ROUND_RESULT":
        return "Awaiting further peer pressure."
    return "This room has ended. Press q to go."


class PeerPressureApp(App[None]):
    TITLE = "Regret // Peer Pressure"
    SUB_TITLE = "Poor judgment is better with friends."
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("s", "start", "Start"),
        ("enter", "primary", "Choose / act"),
        ("q", "leave", "Leave"),
    ]
    CSS = """
    Screen { background: #171511; color: #fffbf2; }
    Header, Footer { background: #271d17; color: #fffbf2; }
    #room-line { height: auto; padding: 1 2; color: #e8a47d; }
    #body { height: 1fr; }
    #sidebar { width: 34; padding: 0 1 1 2; border-right: solid #b85618; }
    #players-title, #choice-title { color: #e8a47d; text-style: bold; margin-top: 1; }
    #players { height: 1fr; }
    #main { width: 1fr; padding: 0 2 1 2; }
    #prompt { height: auto; min-height: 5; padding: 1 2; border: round #b85618; background: #271d17; text-style: bold; }
    #result { height: auto; padding: 1 2; margin-top: 1; border: round #706b62; }
    #choices { height: 1fr; margin-top: 1; }
    #instruction { height: auto; padding: 1 2; color: #d7cec0; }
    #actions { height: 3; align-horizontal: center; }
    Button { margin: 0 1; min-width: 12; }
    Button.-primary { background: #b85618; color: #fffbf2; }
    DataTable > .datatable--cursor { background: #78341f; color: #fffbf2; }
    """

    def __init__(self, client: TogetherClient, heartbeat_interval: float = 5.0) -> None:
        super().__init__()
        self.client = client
        self.heartbeat_interval = heartbeat_interval
        self.state: dict[str, Any] = client.state
        self.selected_cards: list[str] = []
        self.selected_submission: str | None = None
        self._row_keys: list[str] = []
        self._busy = False
        self._heartbeat_running = False
        self._heartbeat_failed = False
        self.leave_error: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Connecting to regrettable company…", id="room-line", markup=False)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("THE TABLE", id="players-title")
                yield Static("", id="players", markup=False)
            with Vertical(id="main"):
                yield Static("Waiting for a prompt.", id="prompt", markup=False)
                yield Static("", id="result", markup=False)
                yield Static("YOUR OPTIONS", id="choice-title")
                yield DataTable(id="choices", cursor_type="row", zebra_stripes=True)
                yield Static("Synchronizing…", id="instruction", markup=False)
                with Horizontal(id="actions"):
                    yield Button("Start", id="start", variant="primary")
                    yield Button("Submit", id="submit", variant="primary")
                    yield Button("Judge", id="judge", variant="primary")
                    yield Button("Advance", id="advance", variant="primary")
                    yield Button("Refresh", id="refresh")
                    yield Button("Leave", id="leave", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#choices", DataTable).add_columns("", "Decision")
        if self.state:
            self._render_state(self.state)
        self.set_interval(self.heartbeat_interval, self._heartbeat_tick)
        self._sync("Synchronizing with the table…")

    def _set_message(self, message: str) -> None:
        self.query_one("#instruction", Static).update(Text(message))

    def _sync(self, message: str = "Refreshing…") -> None:
        self._run_network(lambda: self.client.sync(), message)

    def _run_network(
        self,
        operation: Callable[[], dict[str, Any] | None],
        message: str,
        *,
        exit_after: bool = False,
    ) -> None:
        if self._busy:
            self.notify("Still waiting on the table; try again in a moment.", severity="warning")
            return
        self._busy = True
        self._set_message(message)
        self._configure_buttons()

        def task() -> None:
            try:
                state = operation()
            except Exception as exc:
                self.call_from_thread(self._network_finished, None, f"Peer Pressure faltered: {exc}", exit_after)
            else:
                self.call_from_thread(self._network_finished, state, None, exit_after)

        self.run_worker(task, thread=True, group="peer-pressure-network")

    def _network_finished(self, state: dict[str, Any] | None, error: str | None, exit_after: bool) -> None:
        self._busy = False
        if exit_after:
            # Leaving is local first: an unreachable table must not trap the player.
            self.leave_error = error
            self.exit()
            return
        self._render_newest(state)
        if error:
            self._set_message(error)
        self._configure_buttons()

    def _render_newest(self, state: dict[str, Any] | None) -> None:
        # A failed mutation may still have resynchronized the client (stale_revision).
        current = self.client.state
        if state is None and current and current is not self.state:
            state = current
        if state is not None:
            self._render_state(state)

    def _heartbeat_tick(self) -> None:
        # Heartbeats run beside player actions so they never block or relabel them.
        if self._heartbeat_running:
            return
        self._heartbeat_running = True

        def task() -> None:
            try:
                changed = self.client.heartbeat()
            except Exception as exc:
                self.call_from_thread(self._heartbeat_finished, False, f"Peer Pressure faltered: {exc}")
            else:
                self.call_from_thread(self._heartbeat_finished, changed, None)

        self.run_worker(task, thread=True, group="peer-pressure-heartbeat")

    def _heartbeat_finished(self, changed: bool, error: str | None) -> None:
        self._heartbeat_running = False
        if error:
            self._heartbeat_failed = True
            if not self._busy:
                self._set_message(error)
            return
        recovered, self._heartbeat_failed = self._heartbeat_failed, False
        if changed:
            self._render_newest(None)
        if recovered and not self._busy:
            self._set_message(instruction_for(self.state))

    def _render_state(self, state: dict[str, Any]) -> None:
        self.state = state
        busy_message = self._busy
        room = state.get("room") or {}
        phase = str(room.get("state", "WAITING")).replace("_", " ").title()
        self.query_one("#room-line", Static).update(
            Text(f"ROOM  {room.get('code', self.client.room)}    ROUND  {room.get('round', 0)}    {phase}    REV  {room.get('revision', self.client.revision)}")
        )
        adult_id = (state.get("responsible_adult") or {}).get("id")
        players = []
        for player in state.get("players") or []:
            marker = "★" if player.get("id") == adult_id else "•"
            away = "  away" if not player.get("connected") else ""
            players.append(f"{marker} {player.get('name', '?')}  {player.get('score', 0)}{away}")
        self.query_one("#players", Static).update(Text("\n".join(players) or "Nobody has admitted being here."))
        prompt = state.get("prompt")
        prompt_text = prompt.get("text", "Waiting for a prompt.") if prompt else "Waiting for a prompt."
        self.query_one("#prompt", Static).update(Text(prompt_text))
        result = state.get("result")
        result_text = ""
        if result:
            winner = (result.get("winning_player") or {}).get("name", "Someone")
            result_text = f"CONSEQUENCE\n{result.get('rendered', '')}\n\nPeer pressure worked on {winner}."
        result_widget = self.query_one("#result", Static)
        result_widget.update(Text(result_text))
        result_widget.display = bool(result_text)
        self._rebuild_choices()
        if not busy_message:
            self._set_message(instruction_for(state))
        self._configure_buttons()

    def _rebuild_choices(self) -> None:
        table = self.query_one("#choices", DataTable)
        # Keep the highlighted card or decision under the cursor across rebuilds.
        previous = self._row_keys[table.cursor_row] if 0 <= table.cursor_row < len(self._row_keys) else None
        table.clear(columns=False)
        self._row_keys = []
        room_state = (self.state.get("room") or {}).get("state")
        you = self.state.get("you") or {}
        if room_state == "PLAYING" and not is_responsible_adult(self.state) and not you.get("submitted"):
            available = {str(card.get("card_instance_id")) for card in you.get("hand") or []}
            self.selected_cards = [value for value in self.selected_cards if value in available]
            for card in you.get("hand") or []:
                key = str(card["card_instance_id"])
                self._row_keys.append(key)
                table.add_row("✓" if key in self.selected_cards else "", Text(str(card.get("text", ""))), key=key)
        elif room_state == "JUDGING" and is_responsible_adult(self.state):
            decisions = ((self.state.get("judging") or {}).get("decisions") or [])
            available = {str(decision.get("submission_id")) for decision in decisions}
            if self.selected_submission not in available:
                self.selected_submission = None
            for decision in decisions:
                key = str(decision["submission_id"])
                self._row_keys.append(key)
                table.add_row("✓" if key == self.selected_submission else "", Text(" / ".join(map(str, decision.get("answers") or []))), key=key)
        else:
            self.selected_cards.clear()
            self.selected_submission = None
        if previous in self._row_keys:
            table.move_cursor(row=self._row_keys.index(previous), animate=False)

    def _configure_buttons(self) -> None:
        if not self.is_mounted:
            return
        room_state = (self.state.get("room") or {}).get("state")
        you = self.state.get("you") or {}
        adult = is_responsible_adult(self.state)
        slots = int((self.state.get("prompt") or {}).get("slots", 1))
        enabled = {
            "start": room_state == "WAITING" and adult,
            "submit": room_state == "PLAYING" and not adult and not you.get("submitted") and len(self.selected_cards) == slots,
            "judge": room_state == "JUDGING" and adult and self.selected_submission is not None,
            "advance": room_state == "ROUND_RESULT" and adult,
            "refresh": True,
            "leave": True,
        }
        for button_id, allowed in enabled.items():
            self.query_one(f"#{button_id}", Button).disabled = self._busy or not allowed

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        if key not in self._row_keys:
            return
        room_state = (self.state.get("room") or {}).get("state")
        if room_state == "PLAYING":
            if key in self.selected_cards:
                self.selected_cards.remove(key)
            else:
                slots = int((self.state.get("prompt") or {}).get("slots", 1))
                if len(self.selected_cards) >= slots:
                    self._set_message(f"This prompt needs exactly {slots} answer{'s' if slots != 1 else ''}.")
                    return
                self.selected_cards.append(key)
        elif room_state == "JUDGING":
            self.selected_submission = key
        self._rebuild_choices()
        self._configure_buttons()

    def _round(self) -> Any:
        return (self.state.get("room") or {}).get("round")

    def _mutate(self, action: str, message: str, **payload: Any) -> None:
        def operation() -> dict[str, Any]:
            self.client.mutate(action, **payload)
            return self.client.state

        self._run_network(operation, message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "start":
            self._mutate("start", "Starting a regrettable round…", still_valid=can_start)
        elif button_id == "submit":
            self._mutate("submit", "Submitting your decision privately…", still_valid=can_submit(self._round(), list(self.selected_cards)), card_instance_ids=list(self.selected_cards))
        elif button_id == "judge" and self.selected_submission:
            self._mutate("judge", "Applying consequences…", still_valid=can_judge(self._round(), self.selected_submission), submission_id=self.selected_submission)
        elif button_id == "advance":
            self._mutate("advance", "Making another bad decision…", still_valid=can_advance(self._round()))
        elif button_id == "refresh":
            self._sync()
        elif button_id == "leave":
            self.action_leave()

    def action_refresh(self) -> None:
        self._sync()

    def action_start(self) -> None:
        button = self.query_one("#start", Button)
        if not button.disabled:
            self._mutate("start", "Starting a regrettable round…", still_valid=can_start)

    def action_primary(self) -> None:
        table = self.query_one("#choices", DataTable)
        if table.row_count:
            table.action_select_cursor()
            return
        for button_id in ("start", "submit", "judge", "advance"):
            button = self.query_one(f"#{button_id}", Button)
            if not button.disabled:
                button.press()
                return

    def action_leave(self) -> None:
        if (self.state.get("room") or {}).get("state") == "ENDED":
            self.exit()
            return

        def operation() -> None:
            self.client.mutate("leave", still_valid=always)
            return None

        self._run_network(operation, "Leaving this terrible influence…", exit_after=True)


def run_together_tui(client: TogetherClient, heartbeat_interval: float = 5.0) -> int:
    app = PeerPressureApp(client, heartbeat_interval)
    app.run()
    if app.leave_error:
        print(f"regret: left without telling the table ({app.leave_error}); your seat will expire", file=sys.stderr)
    return 0
