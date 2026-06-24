"""Enables `python -m mokata ...` (no install required)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
