"""Interactive Textual dashboard for local Consequences analytics."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from .consequences import ConsequencesStore
from .packs import load_registry

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, VerticalScroll
    from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane
except ImportError as exc:  # pragma: no cover - optional UI dependency
    raise RuntimeError("The Consequences TUI requires the core Textual dependency; reinstall bad-decisions") from exc

class ConsequencesApp(App[None]):
    TITLE = "Consequences // local analytics"
    SUB_TITLE = "The stew remembers, pseudonymously."
    BINDINGS = [("d", "dashboard", "Dashboard"), ("c", "combinations", "Combinations"), ("p", "prompts", "Prompts"), ("a", "answers", "Answers"), ("r", "recent", "Recent"), ("s", "sort", "Sort"), ("h", "hashes", "Show values"), ("q", "quit", "Quit")]
    CSS = """Screen { background: $surface; } #summary { height: auto; padding: 1 2; color: $text-muted; } DataTable { height: 1fr; } .detail { padding: 1 2; border: round $accent; height: auto; }"""

    def __init__(self, store: ConsequencesStore) -> None:
        super().__init__()
        self.store = store
        self.rows = store.dashboard_rows()
        self.show_values = False
        self.sort_reverse = False
        self.registry = None
        try: self.registry = load_registry()
        except Exception: pass

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._summary(), id="summary")
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"): yield self._table("dashboard")
            with TabPane("Combinations", id="combinations"): yield self._table("combinations")
            with TabPane("Prompts", id="prompts"): yield self._table("prompts")
            with TabPane("Answers", id="answers"): yield self._table("answers")
            with TabPane("Recent draws", id="recent"): yield self._table("recent")
        yield Static("Select a row for detail. d dashboard · c combinations · p prompts · r recent · q quit", classes="detail", id="detail")
        yield Footer()

    def _summary(self) -> str:
        report = self.store.report()
        return (f"DRAWS  {report['draws']['recorded']}    COMBINATIONS  {report['draws']['combinations']}    "
                f"VOTES  {report['feedback']['votes']}  (enjoy {report['feedback']['enjoy']} / regret {report['feedback']['regret']})    "
                f"REQUESTS  {report['requests']['count']}    ERRORS  {report['requests']['errors']}")

    def _table(self, kind: str) -> DataTable:
        table = DataTable(id=f"table-{kind}", cursor_type="row")
        table.add_columns(*({
            "dashboard": ("View", "Value"),
            "combinations": ("Combination", "Draws", "Enjoy", "Regret", "Score"),
            "prompts": ("Prompt hash", "Pack", "Card", "Draws", "Enjoy", "Regret"),
            "answers": ("Answer hash", "Pack", "Card", "Draws", "Enjoy", "Regret"),
            "recent": ("When", "Round", "Combination", "Vote", "ID"),
        }[kind]))
        if kind == "dashboard":
            report = self.store.report(); values = [("draws", report["draws"]["recorded"]), ("unique combinations", report["draws"]["combinations"]), ("votes", report["feedback"]["votes"]), ("enjoy", report["feedback"]["enjoy"]), ("regret", report["feedback"]["regret"]), ("errors", report["requests"]["errors"])]
            for row in values: table.add_row(*map(str, row), key=row[0])
        elif kind == "combinations":
            for row in self.rows["combinations"]: table.add_row(row["hash"][:12], str(row["draws"]), str(row["enjoy"]), str(row["regret"]), "—" if row["score"] is None else f"{row['score']:+.2f}", key=row["hash"])
        elif kind == "prompts":
            for index, row in enumerate(self.rows["prompts"]):
                # Content hashes can repeat across pack/card provenance records.
                key = f"{row['hash']}:{row['pack']}:{row['card']}:{index}"
                table.add_row(row["hash"][:12], row["pack"], row["card"], str(row["draws"]), str(row["enjoy"]), str(row["regret"]), key=key)
        elif kind == "answers":
            for index, row in enumerate(self.rows["answers"]):
                key = f"{row['hash']}:{row['pack']}:{row['card']}:{index}"
                table.add_row(row["hash"][:12], row["pack"], row["card"], str(row["draws"]), str(row["enjoy"]), str(row["regret"]), key=key)
        else:
            for row in self.rows["recent"]:
                when = datetime.fromtimestamp(row["occurred_at"], timezone.utc).strftime("%Y-%m-%d %H:%M")
                vote = "enjoy" if row["vote"] is True else "regret" if row["vote"] is False else "—"
                table.add_row(when, row["round_id"][:8], row["combination"][:12], vote, "yes" if row["identified"] else "no", key=row["round_id"])
        return table

    def _switch(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_dashboard(self) -> None: self._switch("dashboard")
    def action_combinations(self) -> None: self._switch("combinations")
    def action_prompts(self) -> None: self._switch("prompts")
    def action_answers(self) -> None: self._switch("answers")
    def action_hashes(self) -> None:
        self.show_values = not self.show_values
        tab = self.query_one(TabbedContent).active
        if tab in {"prompts", "answers"} and self.registry:
            table = self.query_one(f"#table-{tab}", DataTable)
            first_column = list(table.columns.keys())[0]
            for row_key in table.rows:
                parts = str(row_key.value).split(":")
                if len(parts) >= 3:
                    value = self._card_value(parts[1], parts[2]) if self.show_values else parts[0][:12]
                    if value: table.update_cell(row_key, first_column, value)
        self.query_one("#detail", Static).update("Card values shown where the local registry can resolve them." if self.show_values else "Hashes shown. Press h to reveal local card values.")
    def action_sort(self) -> None:
        tab = self.query_one(TabbedContent).active
        table = self.query_one(f"#table-{tab}", DataTable)
        columns = list(table.columns.keys())
        self.sort_reverse = not self.sort_reverse
        table.sort(*columns, reverse=self.sort_reverse)
        self.query_one("#detail", Static).update(f"Sorted {tab} {'descending' if self.sort_reverse else 'ascending'}. Press s to reverse.")
    def action_recent(self) -> None: self._switch("recent")

    def _card_value(self, pack_id: str, card_id: str) -> str | None:
        if not self.registry: return None
        pack = self.registry.packs.get(pack_id)
        if not pack: return None
        card = next((card for card in (*pack.black, *pack.white) if card.id == card_id), None)
        if card is None: return None
        return card.text if hasattr(card, "text") else card.repr

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        value = f"Selected record: {event.row_key.value}"
        if self.show_values and self.registry:
            parts = str(event.row_key.value).split(":")
            if len(parts) >= 3:
                card_value = self._card_value(parts[1], parts[2])
                if card_value: value += f"\n{card_value}"
        self.query_one("#detail", Static).update(value)

def run_tui(database: Any) -> int:
    ConsequencesApp(ConsequencesStore(database, readonly=True)).run()
    return 0
