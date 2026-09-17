"""Deprecated compatibility module for :mod:`bad_decisions_client.cli`."""

from bad_decisions_client.cli import *  # noqa: F403

if __name__ == "__main__":
    from bad_decisions_client.cli import legacy_main

    raise SystemExit(legacy_main())
