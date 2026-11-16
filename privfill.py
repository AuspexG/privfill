#!/usr/bin/env python3
"""privfill - let an AI assistant use your personal details without seeing them.

This file is the launcher: the program is the `privfill` package next to it.
Run it with no arguments for the usage.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from privfill.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
