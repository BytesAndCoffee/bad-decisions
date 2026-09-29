"""PyInstaller entry point for the native Windows Regret executable."""

from bad_decisions_client.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
