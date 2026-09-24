"""Interactive Textual dashboard for local Consequences analytics."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from .consequences import ConsequencesStore

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, VerticalScroll
    from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane
except ImportError as exc:  # pragma: no cover - optional UI dependency
    raise RuntimeError("The Consequences TUI requires the 'tui' extra: pip install bad-decisions[tui]") from exc

class ConsequencesApp(App[None]):
    TITLE = "Consequences // local analytics"
    SUB_TITLE = "The stew remembers, pseudonymously."
    BINDINGS = [("d", "dashboard", "Dashboard"), ("c", "combinations", "Combinations"), ("p", "prompts", "Prompts"), ("r", "recent", "Recent"), ("q", "quit", "Quit")]
    CSS = """Screen { background: $surface; } #summary { height: auto; padding: 1 2; color: $text-muted; } DataTable { height: 1fr; } .detail { padding: 1 2; border: round $accent; height: auto; }"""

    def __init__(self, store: ConsequencesStore) -> None:
        super().__init__()
        self.store = store
        self.rows = store.dashboard_rows()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._summary(), id="summary")
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"): yield self._table("dashboard")
            with TabPane("Combinations", id="combinations"): yield self._table("combinations")
            with TabPane("Prompts", id="prompts"): yield self._table("prompts")
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
            "recent": ("When", "Round", "Combination", "Vote", "ID"),
        }[kind]))
        if kind == "dashboard":
            report = self.store.report(); values = [("draws", report["draws"]["recorded"]), ("unique combinations", report["draws"]["combinations"]), ("votes", report["feedback"]["votes"]), ("enjoy", report["feedback"]["enjoy"]), ("regret", report["feedback"]["regret"]), ("errors", report["requests"]["errors"])]
            for row in values: table.add_row(*map(str, row), key=row[0])
        elif kind == "combinations":
            for row in self.rows["combinations"]: table.add_row(row["hash"][:12], str(row["draws"]), str(row["enjoy"]), str(row["regret"]), "—" if row["score"] is None else f"{row['score']:+.2f}", key=row["hash"])
        elif kind == "prompts":
            for row in self.rows["prompts"]: table.add_row(row["hash"][:12], row["pack"], row["card"], str(row["draws"]), str(row["enjoy"]), str(row["regret"]), key=row["hash"])
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
    def action_recent(self) -> None: self._switch("recent")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.query_one("#detail", Static).update(f"Selected record: {event.row_key.value}")

def run_tui(database: Any) -> int:
    ConsequencesApp(ConsequencesStore(database, readonly=True)).run()
    return 0
