"""Deprecated compatibility module for :mod:`bad_decisions.cli`."""

from bad_decisions.cli import *  # noqa: F403

if __name__ == "__main__":
    from bad_decisions.cli import legacy_main

    raise SystemExit(legacy_main())
