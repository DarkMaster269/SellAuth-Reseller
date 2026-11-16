"""Entrypoint for python -m resell."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from resell.cli import main as cli_main

        cli_main()
    except ImportError:
        print("resell CLI is not yet implemented (scheduled for Stage 5).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

