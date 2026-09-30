"""Dark mode: every page colour is a theme token with a light and a dark value."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from bad_decisions.api import create_app

WEB = Path(__file__).resolve().parents[1] / "src/bad_decisions/web"
PAGES = {"index.html": "style.css", "together.html": "together.css"}
# Light marks on orange cards, the green status dot, and modal backdrops look right in both themes.
FIXED_COLOURS = {"#fff9ed", "#f2d8cb", "#ffffff35", "#4c9b59", "#4c9b5926", "#171511d1", "#171511b8"}


def _block(css: str, selector: str) -> dict[str, str]:
    start = css.index(selector)
    body = css[css.index("{", start) + 1:css.index("}", start)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


def _themes() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    css = (WEB / "theme.css").read_text(encoding="utf-8")
    return _block(css, ":root {"), _block(css, ':root:not([data-theme="light"])'), _block(css, ':root[data-theme="dark"]')


def test_every_light_token_has_the_same_dark_value_for_system_and_explicit_dark():
    light, system_dark, explicit_dark = _themes()
    assert set(light) == set(system_dark) == set(explicit_dark)
    assert system_dark == explicit_dark
    assert any(light[name] != system_dark[name] for name in ("--ink", "--paper", "--page"))


def test_page_stylesheets_use_only_theme_tokens_for_colour():
    light, _, _ = _themes()
    for stylesheet in PAGES.values():
        css = (WEB / stylesheet).read_text(encoding="utf-8")
        assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) <= FIXED_COLOURS, stylesheet
        used = set(re.findall(r"var\((--[\w-]+)\)", css))
        assert used - set(light) <= set(), (stylesheet, used - set(light))


def test_text_on_ink_inverts_with_the_theme_but_text_on_orange_stays_light():
    light, dark, _ = _themes()
    assert light["--on-ink"] != dark["--on-ink"]  # dark buttons become light buttons with dark text
    assert light["--on-accent"] == dark["--on-accent"] == "#fffbf2"


def test_pages_load_the_theme_before_first_paint_and_offer_a_toggle():
    for page, stylesheet in PAGES.items():
        html = (WEB / page).read_text(encoding="utf-8")
        head = html[:html.index("</head>")]
        assert head.index('href="theme.css?v=__ASSET_VERSION__"') < head.index(f'href="{stylesheet}?v=__ASSET_VERSION__"')
        assert '<script src="theme.js?v=__ASSET_VERSION__"></script>' in head  # not deferred: no flash of the wrong theme
        assert 'id="theme-toggle"' in html


def test_theme_assets_are_served_and_versioned(monkeypatch, tmp_path):
    monkeypatch.setenv("BAD_DECISIONS_PEER_PRESSURE_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        page = client.get("/")
        digest = re.search(r'theme\.css\?v=([0-9a-f]{16})', page.text).group(1)
        css = client.get(f"/assets/theme.css?v={digest}")
        assert css.headers["content-type"].startswith("text/css")
        assert "immutable" in css.headers["Cache-Control"]
        script = client.get(f"/assets/theme.js?v={digest}")
        assert script.headers["content-type"].startswith("application/javascript")
        assert "bad-decisions-theme" in script.text
        assert f"theme.js?v={digest}" in client.get("/peerpressure").text
