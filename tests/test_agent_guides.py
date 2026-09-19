from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

# Thin per-platform pointers. They must reference AGENTS.md and carry no rules.
ADAPTERS = [
    "CLAUDE.md",
    "GEMINI.md",
    ".cursor/rules/agents.mdc",
    ".github/copilot-instructions.md",
]
# Rules that must live in AGENTS.md (the single source of truth).
CORE_RULES = [
    "Keeping agent guides in sync",
    "Add a test with every fix",
    "deploy.sh",
    "twine upload",
    "git push",
    "The only remote is `origin`",
    "patchnotes.md",
    ".venv/bin/python",
    "AGENTS.local.md",
    "sudo",
]
# Words that signal a rule was pasted into an adapter instead of AGENTS.md.
RULE_WORDS = ["twine", "git push", "pytest", "sudo -v", "hostile-input", "os.link", "must not"]
# Machine-specific facts belong in the git-ignored AGENTS.local.md only.
FORBIDDEN = ["bytes.coffee", "8001", "/etc/nginx/sites-available", "/opt/bad-decisions"]
MAX_ADAPTER_LINES = 15


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_agents_md_holds_the_core_rules_and_no_machine_specific_details():
    for rule in CORE_RULES:
        assert rule in AGENTS, f"AGENTS.md is missing: {rule}"
    for item in FORBIDDEN:
        assert item not in AGENTS, f"AGENTS.md must not mention {item}"


def test_every_adapter_exists_and_points_at_agents_md():
    for name in ADAPTERS:
        text = read(name)
        assert "AGENTS.md" in text, f"{name} must reference AGENTS.md"
        assert "AGENTS.local.md" in text, f"{name} must mention AGENTS.local.md"
        assert "no rules" in text, f"{name} must say it contains no rules"


def test_adapters_import_agents_md_where_the_platform_supports_it():
    assert read("CLAUDE.md").splitlines()[0].strip() == "@AGENTS.md"
    assert read("GEMINI.md").splitlines()[0].strip() == "@./AGENTS.md"
    assert "@AGENTS.md" in read(".cursor/rules/agents.mdc")


def test_adapters_stay_thin_and_carry_no_rules_or_machine_details():
    for name in ADAPTERS:
        text = read(name)
        assert len([line for line in text.splitlines() if line.strip()]) <= MAX_ADAPTER_LINES, name
        for word in RULE_WORDS + FORBIDDEN:
            assert word not in text, f"{name} must not contain {word!r}"


def test_agents_md_lists_every_adapter():
    for name in ADAPTERS:
        assert f"`{name}`" in AGENTS, f"AGENTS.md project map must list {name}"


def test_local_notes_are_git_ignored():
    assert "AGENTS.local.md" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()
