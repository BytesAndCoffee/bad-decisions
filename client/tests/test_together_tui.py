from __future__ import annotations

import asyncio
import inspect
import threading

import pytest

pytest.importorskip("textual")

from textual.widgets import Button, DataTable, Static

from bad_decisions_client import together_tui
from bad_decisions_client.together_tui import PeerPressureApp, instruction_for, is_responsible_adult


def state(phase="PLAYING", *, adult="player_2", slots=2, submitted=False):
    return {
        "room": {"code": "ohno", "round": 3, "state": phase, "revision": 7},
        "players": [
            {"id": "player_1", "name": "Alice", "score": 2, "connected": True},
            {"id": "player_2", "name": "Bob", "score": 1, "connected": False},
        ],
        "responsible_adult": {"id": adult, "name": "Bob"} if adult else None,
        "prompt": {"id": "prompt_1", "text": "A [literal] prompt _ and _", "slots": slots},
        "you": {
            "id": "player_1",
            "room_owner": adult is None,
            "submitted": submitted,
            "hand": [
                {"card_instance_id": "card_1", "text": "First answer"},
                {"card_instance_id": "card_2", "text": "Second answer"},
                {"card_instance_id": "card_3", "text": "Third answer"},
            ],
        },
        "judging": {
            "decisions": [
                {"submission_id": "submission_1", "answers": ["One", "Two"]},
                {"submission_id": "submission_2", "answers": ["Three", "Four"]},
            ]
        },
        "result": None,
    }


class FakeClient:
    room = "ohno"
    revision = 7

    def __init__(self, current):
        self.state = current
        self.mutations = []
        self.syncs = 0

    def sync(self):
        self.syncs += 1
        return self.state

    def heartbeat(self):
        return False

    def mutate(self, action, **payload):
        self.mutations.append((action, payload))


async def settle(app, pilot):
    """Wait for network workers, then for the UI callbacks they posted."""
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_role_and_phase_instructions_are_projection_driven():
    playing = state()
    assert not is_responsible_adult(playing)
    assert instruction_for(playing) == "Select 2 answers, then submit."
    playing["you"]["submitted"] = True
    assert "submitted" in instruction_for(playing).lower()
    waiting = state("WAITING", adult=None)
    assert is_responsible_adult(waiting)
    assert instruction_for(waiting).startswith("Start")


def test_tui_selects_exact_hand_size_and_submits_privately():
    async def exercise():
        client = FakeClient(state())
        app = PeerPressureApp(client, heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            table = app.query_one("#choices", DataTable)
            assert table.row_count == 3
            assert "[literal]" in str(app.query_one("#prompt", Static).render())
            assert app.query_one("#submit", Button).disabled

            table.move_cursor(row=0)
            table.action_select_cursor()
            table.move_cursor(row=1)
            table.action_select_cursor()
            await pilot.pause()

            assert app.selected_cards == ["card_1", "card_2"]
            assert not app.query_one("#submit", Button).disabled
            app.on_button_pressed(Button.Pressed(app.query_one("#submit", Button)))
            await settle(app, pilot)
            assert client.mutations == [("submit", {"card_instance_ids": ["card_1", "card_2"]})]

    asyncio.run(exercise())


def test_tui_judging_is_anonymous_and_result_is_visible():
    async def exercise():
        judging = state("JUDGING", adult="player_1")
        client = FakeClient(judging)
        app = PeerPressureApp(client, heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            table = app.query_one("#choices", DataTable)
            assert table.row_count == 2
            table.move_cursor(row=1)
            table.action_select_cursor()
            await pilot.pause()
            assert app.selected_submission == "submission_2"
            app.on_button_pressed(Button.Pressed(app.query_one("#judge", Button)))
            await settle(app, pilot)
            assert client.mutations == [("judge", {"submission_id": "submission_2"})]

        result = state("ROUND_RESULT", adult="player_1")
        result["result"] = {
            "winning_player": {"id": "player_2", "name": "Bob"},
            "answers": ["An answer"],
            "rendered": "A prompt An answer",
        }
        result_app = PeerPressureApp(FakeClient(result), heartbeat_interval=3600)
        async with result_app.run_test(size=(120, 40)) as pilot:
            await settle(result_app, pilot)
            rendered = str(result_app.query_one("#result", Static).render())
            assert "A prompt An answer" in rendered and "Bob" in rendered
            assert not result_app.query_one("#advance", Button).disabled

    asyncio.run(exercise())


def test_tui_uses_project_language_not_third_party_trade_dress():
    source = inspect.getsource(__import__("bad_decisions_client.together_tui", fromlist=["*"])).lower()
    assert "responsible adult" in source
    assert "card czar" not in source


def test_selecting_a_choice_keeps_the_cursor_on_it():
    async def exercise():
        app = PeerPressureApp(FakeClient(state()), heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            table = app.query_one("#choices", DataTable)
            table.focus()
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert app.selected_cards == ["card_3"]
            assert table.cursor_row == 2
            await pilot.press("up", "enter")
            await pilot.pause()
            assert app.selected_cards == ["card_3", "card_2"]
            assert table.cursor_row == 1

    asyncio.run(exercise())


class SlowHeartbeatClient(FakeClient):
    def __init__(self, current):
        super().__init__(current)
        self.release = threading.Event()
        self.beating = threading.Event()
        self.mutated = threading.Event()

    def mutate(self, action, **payload):
        super().mutate(action, **payload)
        self.mutated.set()

    def heartbeat(self):
        self.beating.set()
        self.release.wait(5)
        return False


def test_a_heartbeat_in_flight_does_not_block_player_actions():
    async def exercise():
        client = SlowHeartbeatClient(state())
        app = PeerPressureApp(client, heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            app.selected_cards[:] = ["card_1", "card_2"]
            app._configure_buttons()
            app._heartbeat_tick()
            await asyncio.to_thread(client.beating.wait, 5)
            submit = app.query_one("#submit", Button)
            assert not submit.disabled and not app.query_one("#leave", Button).disabled
            app.on_button_pressed(Button.Pressed(submit))
            assert await asyncio.to_thread(client.mutated.wait, 5)
            assert client.mutations == [("submit", {"card_instance_ids": ["card_1", "card_2"]})]
            client.release.set()
            await settle(app, pilot)

    asyncio.run(exercise())


class StaleClient(FakeClient):
    def mutate(self, action, **payload):
        # TogetherClient resynchronizes before re-raising stale_revision.
        self.state = state("JUDGING", adult="player_2")
        raise RuntimeError("stale_revision: the table moved on")


def test_a_failed_action_shows_the_resynchronized_table():
    async def exercise():
        app = PeerPressureApp(StaleClient(state()), heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            app.selected_cards[:] = ["card_1", "card_2"]
            app._configure_buttons()
            app.on_button_pressed(Button.Pressed(app.query_one("#submit", Button)))
            await settle(app, pilot)
            assert app.state["room"]["state"] == "JUDGING"
            assert "stale_revision" in str(app.query_one("#instruction", Static).render())

    asyncio.run(exercise())


class FailingHeartbeatClient(FakeClient):
    def heartbeat(self):
        raise RuntimeError("Cannot reach API")


def test_heartbeats_do_not_overwrite_messages_until_they_recover():
    async def exercise():
        client = FakeClient(state())
        app = PeerPressureApp(client, heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            instruction = app.query_one("#instruction", Static)
            app._set_message("This prompt needs exactly 2 answers.")
            app._heartbeat_tick()
            await settle(app, pilot)
            assert "exactly 2" in str(instruction.render())
            client.heartbeat = FailingHeartbeatClient.heartbeat.__get__(client)
            app._heartbeat_tick()
            await settle(app, pilot)
            assert "Cannot reach API" in str(instruction.render())
            client.heartbeat = FakeClient.heartbeat.__get__(client)
            app._heartbeat_tick()
            await settle(app, pilot)
            assert str(instruction.render()) == instruction_for(app.state)

    asyncio.run(exercise())


class UnreachableClient(FakeClient):
    def mutate(self, action, **payload):
        raise RuntimeError("Cannot reach API")


def test_leaving_an_unreachable_table_still_exits(monkeypatch, capsys):
    async def exercise():
        app = PeerPressureApp(UnreachableClient(state()), heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await pilot.press("q")
            await settle(app, pilot)
        return app

    app = asyncio.run(exercise())
    assert app.leave_error and "Cannot reach API" in app.leave_error

    class Finished:
        leave_error = "Cannot reach API"

        def __init__(self, *_args):
            pass

        def run(self):
            pass

    monkeypatch.setattr(together_tui, "PeerPressureApp", Finished)
    assert together_tui.run_together_tui(UnreachableClient(state())) == 0
    assert "seat will expire" in capsys.readouterr().err


def test_an_ended_room_exits_without_another_request():
    async def exercise():
        client = FakeClient(state("ENDED"))
        app = PeerPressureApp(client, heartbeat_interval=3600)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            assert "q" in str(app.query_one("#instruction", Static).render())
            await pilot.press("q")
            await pilot.pause()
        assert client.mutations == []
        assert app.leave_error is None

    asyncio.run(exercise())
