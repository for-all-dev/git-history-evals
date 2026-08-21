#!/usr/bin/env python3
"""Runnable entry point — see `apply_ablate.quickstart` for the full docstring
(prerequisites, usage, taxonomy) and implementation.

    cd baselines
    uv run python quickstart.py --list-repos
    uv run python quickstart.py --repo <name> --src ../data/lean/<name> --dry-run
"""

from apply_ablate.quickstart import main

if __name__ == "__main__":
    main()
